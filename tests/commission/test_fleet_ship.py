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

        def fleet_pull_ship(self, level):
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
    comm._commission_find_and_start = Mock(side_effect=lambda c, is_urgent=False: results.pop(0))
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
