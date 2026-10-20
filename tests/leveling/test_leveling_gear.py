import os

import cv2
import numpy as np
import pytest

import module.config.server as server
from module.leveling.gear import LevelingGear, best_card, depot_card_levels, depot_has_item, \
    empty_gear_slots, gear_levels, tag_level

DATA = os.path.join(os.path.dirname(__file__), 'data')


@pytest.fixture(autouse=True)
def en():
    previous = server.server
    server.set_server('en')
    yield
    server.set_server(previous)


def load(name):
    return cv2.cvtColor(cv2.imread(os.path.join(DATA, name)), cv2.COLOR_BGR2RGB)


def frame(*parts):
    """
    A screenshot of the EN client at 1280x720 of the main account, black but for the parts kept, (file, x, y).
    """
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    for name, x, y in parts:
        part = load(name)
        height, width = part.shape[:2]
        image[y:y + height, x:x + width] = part
    return image


def depot(state):
    """
    The depot of the Anti-Air slot of Isokaze: its title, the EQUIPPED switch and the first item.
    """
    return frame((f'depot_equipped_{state}_title.png', 90, 0), (f'depot_equipped_{state}_switch.png', 0, 480),
                 (f'depot_equipped_{state}_first.png', 143, 90))


class FakeDevice:
    def __init__(self, image):
        self.image = image


def gear_page(image):
    gear = LevelingGear.__new__(LevelingGear)
    gear.device = FakeDevice(image)
    return gear


def test_empty_gear_slots_of_the_info_tab():
    # Isokaze: a gun and torpedoes at +0, no anti-air nor auxiliary
    image = frame(('gear_info_isokaze.png', 690, 160))

    assert empty_gear_slots(image) == [2, 3, 4]


@pytest.mark.parametrize('state, equipped', [('on', True), ('off', False)])
def test_depot_and_its_switch(state, equipped):
    gear = gear_page(depot(state))

    assert gear.is_in_depot()
    assert gear.depot_lists_equipped() is equipped
    assert depot_has_item(gear.device.image)


def test_depot_without_items():
    image = depot('off')
    # Storage has nothing for the slot, the list is empty
    image[90:210, 143:261] = (40, 45, 60)

    assert not depot_has_item(image)


def test_not_in_depot_on_the_detail_page():
    gear = gear_page(frame(('gear_info_isokaze.png', 690, 160)))

    assert not gear.is_in_depot()


def test_enhancement_of_the_cards_of_the_depot():
    # Anti-air guns in storage: +0, +10, +10, +0, +10, +10, +0 on the first row
    image = frame(('depot_equipped_off_row.png', 137, 84))

    levels = depot_card_levels(image)

    assert levels[:7] == [0, 10, 10, 0, 10, 10, 0]
    # No cards below, black
    assert levels[7:] == [-1] * 14
    assert best_card(levels) == 1


@pytest.mark.parametrize('levels, best', [
    ([0, 10, 11, 10], 2),
    ([0, 0, 0], 0),
    ([-1, 0, 10], 2),
    # A dimmed first card, no card at all
    ([-1, 6], 1),
    ([-1, -1], -1),
])
def test_best_card(levels, best):
    assert best_card(levels) == best


def test_enhancement_of_her_gear():
    # Isokaze: a gun and torpedoes at +0, nothing else
    image = frame(('gear_info_isokaze.png', 690, 160))

    assert gear_levels(image) == [0, 0, 0, 0, 0]


@pytest.mark.parametrize('digit, level', [
    (10, 10), (13, 13), (2, 2), (0, 0),
    # The plus of the tag read as a 4, or its point
    (411, 11), (410, 10), (413, 13), (104, 10), (101, 10), (42, 2),
])
def test_level_of_a_tag(digit, level):
    assert tag_level(digit) == level


@pytest.mark.parametrize('name, popup, confirm', [
    ('gear_popup_buttons.png', True, False),
    ('gear_confirm_change_buttons.png', False, True),
    ('gear_confirm_empty_buttons.png', False, True),
    ('gear_tab_bottom.png', False, False),
])
def test_popups_of_gear(name, popup, confirm):
    # Unequip, Change, Enhance on gear in a slot; Cancel, Confirm on gear picked in the depot, for a slot with gear
    # or an empty one
    gear = gear_page(frame((name, 300, 600)))

    assert gear.is_gear_popup() is popup
    assert gear.is_gear_confirm() is confirm


def test_depot_of_a_change_has_a_remove_card_first():
    # Guns for Maury in storage: REMOVE, Twin 127mm +11, +10, +0, Twin 100mm +10, +0, Prototype +10
    image = frame(('depot_change_title.png', 90, 0), ('depot_change_off_row.png', 137, 84))

    assert gear_page(image).is_in_depot()
    levels = depot_card_levels(image)
    assert levels[:7] == [-1, 11, 10, 0, 10, 0, 10]
    assert best_card(levels) == 1


def test_tags_read_with_their_point():
    # Auxiliaries in storage: +0, +10, +0, +10, +10, an Advanced Sonar dimmed as she has one, +10
    image = frame(('depot_aux_off_row_2.png', 137, 440))

    assert depot_card_levels(image)[14:] == [0, 10, 0, 10, 10, -1, 10]


class ScreenDevice:
    """
    Shows a screen, a click on a button goes to the screen of moves[screen, button name] if any.
    """

    def __init__(self, moves, screen='tab'):
        self.screens = {
            'tab': frame(('gear_tab_bottom.png', 300, 600)),
            'popup': frame(('gear_popup_buttons.png', 300, 600)),
            'depot_on': frame(('depot_change_title.png', 90, 0), ('depot_equipped_on_switch.png', 0, 480),
                              ('depot_change_row.png', 137, 84)),
            'depot_off': frame(('depot_change_title.png', 90, 0), ('depot_equipped_off_switch.png', 0, 480),
                               ('depot_change_off_row.png', 137, 84)),
            'confirm': frame(('gear_confirm_change_buttons.png', 300, 600)),
        }
        self.moves = moves
        self.screen = screen
        self.clicks = []

    @property
    def image(self):
        return self.screens[self.screen]

    def screenshot(self):
        return self.image

    def click(self, button):
        self.clicks.append(button.name)
        self.screen = self.moves.get((self.screen, button.name), self.screen)

    def stuck_record_add(self, button):
        pass

    def sleep(self, second):
        pass


# Changing the gun of Maury, and the ways back
CHANGE = {
    ('tab', 'EQUIP_TAKE_ON_0'): 'popup',
    ('popup', 'GEAR_CHANGE'): 'depot_on',
    ('popup', 'GEAR_POPUP_CLOSE'): 'tab',
    ('depot_on', 'EQUIPPED_SWITCH'): 'depot_off',
    ('depot_on', 'BACK_ARROW'): 'tab',
    ('depot_off', 'DEPOT_CARD_0_1'): 'confirm',
    ('depot_off', 'BACK_ARROW'): 'tab',
    ('confirm', 'GEAR_CONFIRM'): 'tab',
    ('confirm', 'GEAR_CANCEL'): 'depot_off',
}


def gear_on(device):
    gear = LevelingGear.__new__(LevelingGear)
    gear.device = device
    gear.interval_timer = {}
    return gear


@pytest.fixture
def short_steps(monkeypatch):
    monkeypatch.setattr('module.leveling.gear.GEAR_TIMEOUT', 1)


def test_gear_in_a_slot_is_changed_for_better_gear_from_storage():
    device = ScreenDevice(CHANGE)

    # Her gun at +0, storage has one at +11
    assert gear_on(device).gear_slot_fill(0, current=0) == 11
    assert device.clicks == ['EQUIP_TAKE_ON_0', 'GEAR_CHANGE', 'EQUIPPED_SWITCH', 'DEPOT_CARD_0_1', 'GEAR_CONFIRM']
    assert device.screen == 'tab'


def test_gear_stays_when_storage_has_nothing_better():
    device = ScreenDevice(CHANGE)

    assert gear_on(device).gear_slot_fill(0, current=11) is None
    assert device.clicks == ['EQUIP_TAKE_ON_0', 'GEAR_CHANGE', 'EQUIPPED_SWITCH', 'BACK_ARROW']
    assert device.screen == 'tab'


def test_a_depot_that_does_not_open_closes_the_popup(short_steps):
    moves = dict(CHANGE)
    del moves['popup', 'GEAR_CHANGE']
    device = ScreenDevice(moves)

    assert gear_on(device).gear_slot_fill(0, current=0) is None
    assert device.clicks[:2] == ['EQUIP_TAKE_ON_0', 'GEAR_CHANGE']
    assert device.clicks[-1] == 'GEAR_POPUP_CLOSE'
    assert device.screen == 'tab'


def test_a_confirm_that_does_not_go_through_is_cancelled(short_steps):
    moves = dict(CHANGE)
    del moves['confirm', 'GEAR_CONFIRM']
    device = ScreenDevice(moves)

    assert gear_on(device).gear_slot_fill(0, current=0) is None
    assert device.clicks[-2:] == ['GEAR_CANCEL', 'BACK_ARROW']
    assert device.screen == 'tab'


def test_the_gear_tab_from_where_a_step_stopped():
    for screen in ('tab', 'popup', 'depot_on', 'depot_off', 'confirm'):
        device = ScreenDevice(CHANGE, screen=screen)

        assert gear_on(device).gear_tab_ensure()
        assert device.screen == 'tab'
