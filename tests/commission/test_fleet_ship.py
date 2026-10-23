import os

import cv2
import pytest

import module.config.server as server
from module.commission.commission import requirement_level
from module.leveling.fleet import choose_pull
from module.leveling.plan import Slot

DATA = os.path.join(os.path.dirname(__file__), 'data')
# Top of COMMISSION_ADVICE, the Recommend of a commission unfolded
ADVICE_TOP = 328


@pytest.fixture(autouse=True)
def en():
    previous = server.server
    server.set_server('en')
    yield
    server.set_server(previous)


@pytest.mark.parametrize('name, level', [
    # "Have at least 1 lv. 30 ship." of Daily Resource Extraction V, the F2P account 2026-10-22
    ('requirement_lv30', 30),
    # "Have at least 1 lv. 25 ship." of Merchant Rescue II
    ('requirement_lv25', 25),
    # Scrolled the other way in its box
    ('requirement_lv25_scrolled', 25),
    # "Have at least 1 lv. 12 ship" of Destroy Enemy Scouts: not a round level
    ('requirement_lv12', 12),
    # The commission still unfolding, its requirements not drawn: read as 2 and 4 by the first reader
    ('requirement_opening', 0),
])
def test_level_a_commission_asks_for(name, level):
    image = cv2.cvtColor(cv2.imread(os.path.join(DATA, f'{name}.png')), cv2.COLOR_BGR2RGB)

    assert requirement_level(image, ADVICE_TOP) == level


def test_the_requirement_is_found_when_recommend_matches_lower():
    # Research Mission II of the F2P account, 2026-10-22, its team filled with Lv.1 ships: Recommend matched at 339,
    # the box at a fixed offset cut the letters in half and read "vea`'e>v.3VS'P"
    image = cv2.cvtColor(cv2.imread(os.path.join(DATA, 'requirement_lv30_advice_low.png')), cv2.COLOR_BGR2RGB)

    assert requirement_level(image, ADVICE_TOP + 11) == 30


def fleet(*ships):
    rows = ['main'] * 3 + ['vanguard'] * 3
    return [Slot(fleet=1, row=row, index=i % 3, name=name, level=level)
            for i, (row, (name, level)) in enumerate(zip(rows, ships))]


F2P = fleet(('Long Island', 77), ('Nevada', 57), ('Oklahoma', 57),
            ('Laffey', 69), ('Hammann', 60), ('Indianapolis', 59))


def test_the_highest_ship_leaves_but_never_the_flagship():
    # Long Island leads the fleet: Laffey goes
    assert choose_pull(F2P, 30).name == 'Laffey'


def test_a_ship_under_the_level_does_not_leave():
    assert choose_pull(F2P, 70) is None


def test_the_last_ship_of_the_vanguard_stays():
    slots = fleet(('Long Island', 77), ('Nevada', 57), ('Oklahoma', 57), ('Laffey', 69), ('', 0), ('', 0))

    assert choose_pull(slots, 30).name == 'Nevada'


def test_a_fleet_of_a_flagship_and_one_escort_keeps_both():
    slots = fleet(('Long Island', 77), ('', 0), ('', 0), ('Laffey', 69), ('', 0), ('', 0))

    assert choose_pull(slots, 10) is None


class Comm:
    def __init__(self, name):
        self.name = name
        self.running = False

    def convert_to_running(self):
        self.running = True

    def __repr__(self):
        return self.name


def commission(monkeypatch, pull_on, pulled, starts):
    """
    A Commission whose fleet pull frees the given slots one by one, and whose starts return the given results.
    """
    from types import SimpleNamespace
    from unittest.mock import Mock
    from module.commission.commission import RewardCommission
    import module.leveling.fleet as fleet_module

    pulls = list(pulled)
    taken = []

    class Fleet:
        def __init__(self, *args, **kwargs):
            pass

        def fleet_choose_ship(self, level):
            taken.append(level)
            return pulls.pop(0) if pulls else None

    monkeypatch.setattr(fleet_module, 'LevelingFleet', Fleet)
    comm = RewardCommission.__new__(RewardCommission)
    comm.config = SimpleNamespace(Commission_PullFleetShip=pull_on)
    comm.device = None
    comm.ui_ensure = Mock()
    comm._commission_ensure_mode = Mock()
    comm._commission_swipe_to_top = Mock()
    comm._commission_mode_reset = Mock()
    comm.handle_info_bar = Mock()
    results = list(starts)
    comm._commission_find_and_start = Mock(side_effect=lambda c, is_urgent=False, pick=None: results.pop(0))
    comm.pulls = taken
    return comm


def test_a_commission_with_no_free_ship_takes_one_from_the_fleet(monkeypatch):
    laffey = choose_pull(F2P, 30)
    comm = commission(monkeypatch, pull_on=True, pulled=[laffey], starts=[True])
    dre = Comm('DAILY RESOURCE EXTRACTION V')

    assert comm.commission_start_with_fleet_ships([(dre, False, 30)], free=1) == 1
    assert dre.running


def test_no_ship_taken_with_the_option_off(monkeypatch):
    comm = commission(monkeypatch, pull_on=False, pulled=[], starts=[])

    assert comm.commission_start_with_fleet_ships([(Comm('DRE V'), False, 30)], free=1) == 0
    comm._commission_find_and_start.assert_not_called()


def test_only_as_many_ships_as_free_fleets(monkeypatch):
    slots = [choose_pull(F2P, 30), choose_pull(F2P, 30)]
    comm = commission(monkeypatch, pull_on=True, pulled=slots, starts=[True, True])
    waiting = [(Comm('MERCHANT RESCUE II'), True, 25), (Comm('DRE V'), False, 30)]

    assert comm.commission_start_with_fleet_ships(waiting, free=1) == 1
    assert comm._commission_find_and_start.call_count == 1


def test_no_ship_of_the_level_in_the_fleets_stops(monkeypatch):
    comm = commission(monkeypatch, pull_on=True, pulled=[], starts=[])

    assert comm.commission_start_with_fleet_ships([(Comm('PATROL ESCORT'), True, 90)], free=2) == 0
    comm._commission_find_and_start.assert_not_called()


def test_a_ship_taken_out_for_nothing_is_tried_by_the_next_commission(monkeypatch):
    # Merchant Rescue L asked for types Laffey is not, Daily Resource Extraction V takes her
    laffey = choose_pull(F2P, 30)
    comm = commission(monkeypatch, pull_on=True, pulled=[laffey, choose_pull(F2P, 30)], starts=[False, True])
    rescue, dre = Comm('MERCHANT RESCUE L'), Comm('DAILY RESOURCE EXTRACTION V')

    assert comm.commission_start_with_fleet_ships([(rescue, True, 25), (dre, False, 30)], free=1) == 1
    assert dre.running and not rescue.running
    # One ship left the fleet, not two
    assert comm.pulls == [25]


def test_the_ship_chosen_is_put_in_the_team_of_the_commission(monkeypatch):
    laffey = choose_pull(F2P, 30)
    comm = commission(monkeypatch, pull_on=True, pulled=[laffey], starts=[True])
    dre = Comm('DAILY RESOURCE EXTRACTION I')

    comm.commission_start_with_fleet_ships([(dre, False, 30)], free=1)

    assert comm._commission_find_and_start.call_args.kwargs['pick'] is laffey


def load(name):
    return cv2.cvtColor(cv2.imread(os.path.join(DATA, f'{name}.png')), cv2.COLOR_BGR2RGB)


def make_picker(frames, state):
    """
    A RewardCommission on the F2P account whose screen is frames[state['now']], each tap moving it on.
    """
    from types import SimpleNamespace
    from unittest.mock import Mock
    from module.commission.commission import RewardCommission

    comm = RewardCommission.__new__(RewardCommission)
    comm.config = SimpleNamespace()
    comm.interval_timer = {}
    comm.device = SimpleNamespace(image=frames[state['now']], click=Mock(), stuck_record_add=Mock(),
                                  display_scale=2.0, sleep=Mock())

    def screenshot():
        comm.device.image = frames[state['now']]
        return comm.device.image

    comm.device.screenshot = screenshot

    def loop(timeout=None):
        import time
        for _ in range(1000):
            time.sleep(0.01)
            yield screenshot()

    comm.loop = loop
    return comm


def test_the_card_of_the_fleet_ship_is_found_in_the_team_picker():
    from module.commission.commission import RewardCommission
    from module.leveling.plan import Slot
    comm = make_picker({'search': load('pick_search_laffey')}, {'now': 'search'})

    card = RewardCommission.commission_pick_card(
        comm, Slot(fleet=1, row='vanguard', index=1, name='L affey', level=75), 'Laffey')

    x1, y1, x2, y2 = card.area
    # The card on the top left, (90, 75) to (235, 280)
    assert 90 <= (x1 + x2) / 2 <= 235 and 75 <= (y1 + y2) / 2 <= 280


def test_a_fleet_ship_is_picked_for_the_team(monkeypatch):
    # Frames of the F2P account, 2026-10-22: Research Mission II with an empty team, the picker, the search for
    # Laffey, "This ship is assigned to another fleet. Remove ship from that fleet?", a ship taken, the team with her
    import module.leveling.fleet as fleet_module
    from module.commission.assets import COMMISSION_ADVICE
    from module.commission.commission import RewardCommission
    from module.leveling.plan import Slot
    from module.retire.assets import SHIP_CONFIRM
    frames = {
        'panel': load('pick_r2'), 'picker': load('pick_slot'), 'search': load('pick_search_laffey'),
        'popup': load('pick_picked'), 'selected': load('pick_foote_sel'), 'team': load('pick_foote_team'),
    }
    state = {'now': 'panel'}
    comm = make_picker(frames, state)
    clicks = []

    def click(button):
        clicks.append(button.name)
        now = state['now']
        if now == 'panel' and button.name == 'COMMISSION_TEAM_SLOT':
            state['now'] = 'picker'
        elif now == 'search' and button.name.startswith('CARD') or now == 'search' and 'SHIP' in button.name:
            state['now'] = 'popup'
        elif now == 'popup' and 'CONFIRM' in button.name:
            state['now'] = 'selected'
        elif now == 'selected' and button is SHIP_CONFIRM:
            state['now'] = 'team'

    comm.device.click = click

    class Fleet:
        def __init__(self, *args, **kwargs):
            pass

        def dock_search(self, name):
            assert name == 'Laffey'
            state['now'] = 'search'
            return True

    monkeypatch.setattr(fleet_module, 'LevelingFleet', Fleet)
    assert comm.appear(COMMISSION_ADVICE, offset=(5, 20))

    # Her name as the fleet page read it, 2026-10-22
    assert RewardCommission.commission_pick(comm, Slot(fleet=1, row='vanguard', index=1, name='L affey', level=75))

    assert state['now'] == 'team'
    assert clicks.count('COMMISSION_TEAM_SLOT') == 1
    assert not any('CANCEL' in name for name in clicks)
