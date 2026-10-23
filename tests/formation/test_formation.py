import os
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

import module.config.server as server
import module.formation.formation as formation_module
from module.formation.formation import (ADD_TEMPLATE, PICKER_LIST_BOTTOM, Formation, find_adds, row_of,
                                       ship_score)
from module.exception import ScriptError
from module.inventory.inventory import CARD_HEIGHT
from module.retire.dock_walk import DockWalker
from module.retire.scanner import Ship, ShipScanner

DATA = os.path.join(os.path.dirname(__file__), 'data')


@pytest.fixture(autouse=True)
def en():
    previous = server.server
    server.set_server('en')
    yield
    server.set_server(previous)


def dock_image(name, dock=False):
    path = os.path.join(DATA if not dock else os.path.join(DATA, '..', '..', 'dock', 'data'), name)
    return cv2.cvtColor(cv2.imread(path), cv2.COLOR_BGR2RGB)


def candidate_formation(image):
    form = object.__new__(Formation)
    form.device = SimpleNamespace(image=image)
    form.dock_favourite_set = lambda *args, **kwargs: None
    form.dock_sort_method_dsc_set = lambda *args, **kwargs: None
    form.dock_filter_set = lambda *args, **kwargs: None
    return form


def test_commissioned_laffey_is_rejected_even_when_status_template_reads_free():
    # The exact F2P failure screenshot from 2026-10-22 19:19: template-only scanning picks commissioned Laffey.
    image = dock_image('commission_dock_f2p.png')
    scanner = ShipScanner(fleet=0, status='free')
    scanner.disable('emotion')
    assert max(scanner.scan(image, output=False), key=ship_score).button.name == 'CARD_2_0'
    form = candidate_formation(image)
    below_first_page = [Ship(level=11)]
    form.formation_more_candidates = lambda: below_first_page

    # Every first-page ship is commissioned or already in a fleet, so look further down rather than tap Laffey.
    assert form.formation_candidates('vanguard') is below_first_page


def test_failure_screen_selected_counter_is_readable_before_selection():
    image = dock_image('commission_dock_f2p.png')

    assert formation_module.OCR_DOCK_SELECTED.ocr(image) == (0, 1, 1)


def picker_formation(monkeypatch, states):
    """Each state is (selected, total, on_fleet); advance on the next screenshot."""
    form = object.__new__(Formation)
    clicks, failures = [], []
    form.device = SimpleNamespace(image=None, click=clicks.append)

    def screenshots(**kwargs):
        assert kwargs == {'timeout': formation_module.PICKER_WAIT}
        for state in states:
            form.device.image = state
            yield

    form.loop = screenshots
    form.appear = lambda *args, **kwargs: True
    form.ui_process_check_button = lambda *args: form.device.image[2]
    form.handle_popup_cancel = lambda *args: False
    form.formation_save_failure = failures.append
    monkeypatch.setattr(formation_module.OCR_DOCK_SELECTED, 'ocr', lambda image: (image[0], 0, image[1]))
    monkeypatch.setattr(formation_module, 'Timer', lambda *args: SimpleNamespace(reached=lambda: True,
                                                                              reset=lambda: None))
    return form, clicks, failures


def test_picker_rejected_selection_stops_tapping_and_saves_failure(monkeypatch):
    form, clicks, failures = picker_formation(monkeypatch, [(0, 1, False)] * 10)

    assert not form.formation_picker_click('candidate', selecting=True)

    assert clicks == ['candidate'] * formation_module.PICKER_MAX_CLICKS
    assert failures == ['selection_timeout']


def test_picker_unreadable_counter_never_taps_a_ship(monkeypatch):
    form, clicks, failures = picker_formation(monkeypatch, [(0, 0, False)] * 10)

    assert not form.formation_picker_click('candidate', selecting=True)

    assert clicks == []
    assert failures == ['selection_timeout']


def test_picker_accepts_selection_then_confirms_only_after_fleet_returns(monkeypatch):
    form, clicks, failures = picker_formation(monkeypatch, [(0, 1, False), (1, 1, False)])

    assert form.formation_picker_click('candidate', selecting=True)
    assert clicks == ['candidate']

    form, clicks, failures = picker_formation(monkeypatch, [(1, 1, False), (0, 0, True)])

    assert form.formation_picker_click('confirm', selecting=False)
    assert clicks == ['confirm']
    assert failures == []


def test_picker_confirmation_timeout_is_bounded(monkeypatch):
    form, clicks, failures = picker_formation(monkeypatch, [(1, 1, False)] * 10)

    assert not form.formation_picker_click('confirm', selecting=False)

    assert clicks == ['confirm'] * formation_module.PICKER_MAX_CLICKS
    assert failures == ['confirmation_timeout']


@pytest.mark.parametrize('outcomes, added, expected', [
    ([False], False, ['select', 'cancel']),
    ([True, False], False, ['select', 'reset', 'confirm', 'cancel']),
    ([True, True], True, ['select', 'reset', 'confirm']),
])
def test_add_reports_success_only_after_selection_and_confirmation(outcomes, added, expected):
    form = object.__new__(Formation)
    calls = []
    outcomes = iter(outcomes)
    form.ship_info_enter = lambda **kwargs: None
    form.formation_candidates = lambda row: [Ship(rarity='rare', level=10, button='candidate')]
    form.dock_reset = lambda: calls.append('reset')
    form.formation_cancel_picker = lambda: calls.append('cancel')

    def select(button, selecting):
        calls.append('select' if selecting else 'confirm')
        return next(outcomes)

    form.formation_picker_click = select

    assert form.formation_add((970, 341)) is added
    assert calls == expected


def test_picker_cancel_failure_raises_for_scheduler_recovery(monkeypatch):
    form, clicks, failures = picker_formation(monkeypatch, [(0, 1, False)] * 10)

    with pytest.raises(ScriptError, match='could not leave the ship picker safely'):
        form.formation_cancel_picker()

    assert clicks == [formation_module.BACK_ARROW] * formation_module.PICKER_MAX_CLICKS
    assert failures == ['cancel_timeout']


def test_walk_finds_free_ship_after_busy_first_page_and_stops_before_next_drag(monkeypatch):
    form = candidate_formation(dock_image('commission_dock_f2p.png'))
    walker = DockWalker(form, list_bottom=PICKER_LIST_BOTTOM, card_read_bottom=CARD_HEIGHT)
    pages = [form.device.image, dock_image('dock_engaged_f2p.png', dock=True)]

    def cards():
        for image in pages:
            form.device.image = image
            for row in walker.reader.rows(image):
                if row.top + CARD_HEIGHT <= PICKER_LIST_BOTTOM:
                    for card in row.cards:
                        yield card, walker.fleet_of(image, card)
        raise AssertionError('The free card must be selected before the walk moves again')

    walker.cards = cards

    def make_walker(main, **kwargs):
        assert main is form
        assert kwargs == {'list_bottom': 630, 'card_read_bottom': 204}
        return walker

    monkeypatch.setattr(formation_module, 'DockWalker', make_walker)

    candidates = form.formation_more_candidates()

    # Crescent has no busy banner. The red/pink portrait must not be mistaken for "Engaged".
    assert len(candidates) == 1
    assert candidates[0].level == 15
    assert candidates[0].rarity == 'common'
    assert candidates[0].button.name == 'INVENTORY_CARD_1'


@pytest.mark.parametrize('status', ['in_hard_mode', 'in_event_fleet'])
def test_walk_keeps_special_lineup_exclusions(monkeypatch, status):
    form = candidate_formation(dock_image('dock_engaged_f2p.png', dock=True))
    walker = DockWalker(form, list_bottom=PICKER_LIST_BOTTOM, card_read_bottom=CARD_HEIGHT)
    cards = [(card, walker.fleet_of(form.device.image, card))
             for row in walker.reader.rows(form.device.image)
             if row.top + CARD_HEIGHT <= PICKER_LIST_BOTTOM for card in row.cards]
    walker.cards = lambda: iter(cards)
    monkeypatch.setattr(formation_module, 'DockWalker', lambda *args, **kwargs: walker)
    monkeypatch.setattr(formation_module.StatusScanner, '_match', lambda *args: status)

    assert form.formation_more_candidates() == []


def template():
    return cv2.imread(ADD_TEMPLATE, cv2.IMREAD_GRAYSCALE)


def fleet_page():
    """
    Fleet 1 of a new account, the EN client at 1280x720: the main fleet has its flagship only,
    the vanguard 2 ships. The part with both "Add" is kept, the rest is black.
    """
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    image[170:390, 700:1060] = cv2.cvtColor(cv2.imread(os.path.join(DATA, 'fleet_two_empty.png')), cv2.COLOR_BGR2RGB)
    return image


def test_empty_slots_are_found_main_fleet_first():
    adds = find_adds(fleet_page(), template())

    assert [row_of(point) for point in adds] == ['main', 'vanguard']
    (x1, y1), (x2, y2) = adds
    assert abs(x1 - 799) < 6 and abs(y1 - 212) < 6
    assert abs(x2 - 970) < 6 and abs(y2 - 341) < 6


def test_no_empty_slot_on_a_full_fleet():
    assert find_adds(np.zeros((720, 1280, 3), dtype=np.uint8), template()) == []


@pytest.mark.parametrize('point, row', [
    # Where the next empty slot of each row shows its "Add", along the rows drawn in perspective
    ((300, 310), 'main'), ((799, 212), 'main'),
    ((505, 450), 'vanguard'), ((750, 400), 'vanguard'), ((970, 341), 'vanguard'),
])
def test_row_of_a_slot(point, row):
    assert row_of(point) == row


def test_a_ship_of_higher_rarity_is_worth_a_few_levels():
    common = Ship(rarity='common', level=1)
    elite = Ship(rarity='elite', level=1)
    levelled = Ship(rarity='common', level=30)

    assert max([common, elite], key=ship_score) is elite
    assert max([elite, levelled], key=ship_score) is levelled


class FakeFleet:
    """
    Empty slots of a fleet page as rows, the dock with the ships free for each row.
    """

    def __init__(self, empty, free):
        self.empty = dict(empty)
        self.free = dict(free)
        self.added = []

    def adds(self, full):
        points = {'main': (799, 212), 'vanguard': (970, 341)}
        return [points[row] for row in ['main', 'vanguard'] if self.empty.get(row) and row not in full]

    def add(self, point):
        row = row_of(point)
        if not self.free.get(row):
            return False
        self.free[row] -= 1
        self.empty[row] -= 1
        self.added.append(row)
        return True


def make_formation(fleet):
    form = object.__new__(Formation)
    form.formation_adds = fleet.adds
    form.formation_add = fleet.add
    return form


def test_fill_both_rows():
    fleet = FakeFleet(empty={'main': 2, 'vanguard': 1}, free={'main': 5, 'vanguard': 5})

    assert make_formation(fleet).formation_fill() == 3

    assert fleet.added == ['main', 'main', 'vanguard']


def test_a_row_without_free_ships_is_left_as_it_is():
    # A new account has no free main fleet ship, its destroyers go to the vanguard
    fleet = FakeFleet(empty={'main': 2, 'vanguard': 1}, free={'main': 0, 'vanguard': 4})

    assert make_formation(fleet).formation_fill() == 1

    assert fleet.added == ['vanguard']
    assert fleet.empty == {'main': 2, 'vanguard': 0}


def test_fleets_of_the_campaign():
    form = object.__new__(Formation)
    data = {'Main': {'Fleet': {'Fleet1': 1, 'Fleet2': 0}}}
    form.config = SimpleNamespace(cross_get=lambda key, default=None: {
        'Main.Fleet.Fleet1': data['Main']['Fleet']['Fleet1'],
        'Main.Fleet.Fleet2': data['Main']['Fleet']['Fleet2'],
    }.get(key, default))

    assert form.formation_fleets() == [1]

    data['Main']['Fleet']['Fleet2'] = 2
    assert form.formation_fleets() == [1, 2]
