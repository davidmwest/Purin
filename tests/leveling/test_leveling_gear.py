import os

import cv2
import numpy as np
import pytest

import module.config.server as server
from module.leveling.gear import LevelingGear, best_card, depot_card_levels, depot_has_item, \
    empty_gear_slots, gear_levels

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
