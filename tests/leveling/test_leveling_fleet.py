import os
from unittest.mock import Mock

import cv2
import numpy as np
import pytest

import module.config.server as server
from module.leveling.fleet import LevelingFleet, is_input_shown, is_search_open, name_fits, search_term
from module.leveling.plan import ShipMatcher, Target, load_ship_data
from module.retire.dock import CARD_GRIDS

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


def info_view(name):
    """
    The Info view of the fleet page: the levels on top of the cards and the names below their portraits.
    """
    return frame((f'{name}_levels.png', 0, 150), (f'{name}_names.png', 0, 456))


def dock(name):
    """
    The dock of a slot: the magnifier of the search bar, and the bottom where the input bar of Android comes up.
    """
    return frame((f'{name}_top.png', 600, 0), (f'{name}_bottom.png', 300, 600))


class FakeDevice:
    def __init__(self, image):
        self.image = image

    def screenshot(self):
        return self.image


def fleet_page(image):
    fleet = LevelingFleet.__new__(LevelingFleet)
    fleet.device = FakeDevice(image)
    fleet.fleet_info_ensure = Mock()
    return fleet


def target(ship, level):
    return Target(name=ship, ship=ship, hull='DD', row='vanguard', rarity='Elite', level=level, goal=120,
                  chips=1320, arrays=0, gain=0, tech=0, score=0.3, priority=1, geared=True, index=0)


@pytest.mark.parametrize('name, levels, ships', [
    ('info_fleet_1', [78, 97, 85, 100, 91, 91],
     ['Hakuhou', 'Gangut', 'Kansas', 'Dmitri Donskoi', 'Ning Hai (Retrofit)', 'Ping Hai (Retrofit)']),
    # Haruna at 102, a name scrolling in its banner
    ('info_fleet_2', [110, 108, 102, 118, 110, 105],
     ['Victorious META', 'Hiei', 'Haruna', 'London (Retrofit)', 'Nana Astar Deviluke', 'Vittorio Cuniberti']),
])
def test_slots_of_the_info_view(name, levels, ships):
    slots = fleet_page(info_view(name)).fleet_read_slots(1)

    assert [(slot.row, slot.index) for slot in slots] == \
           [('main', 0), ('main', 1), ('main', 2), ('vanguard', 0), ('vanguard', 1), ('vanguard', 2)]
    assert [slot.level for slot in slots] == levels
    matcher = ShipMatcher(load_ship_data())
    assert [matcher.match(slot.name).name for slot in slots] == ships


def test_empty_slots_of_the_info_view():
    # The submarine fleet, its 3 ships where the main fleet is and nothing after
    slots = fleet_page(info_view('info_submarine')).fleet_read_slots(1)

    assert [slot.level for slot in slots] == [116, 78, 71, 0, 0, 0]
    assert [slot.name for slot in slots[3:]] == ['', '', '']
    matcher = ShipMatcher(load_ship_data())
    # "1-404"
    assert [matcher.match(slot.name).name for slot in slots[:3]] == ['U-557', 'Whydah', 'I-404']


@pytest.mark.parametrize('name, opened, typing', [
    ('dock', False, False),
    ('dock_search_open', True, False),
    ('dock_search_typing', True, True),
    ('dock_search_found', True, False),
])
def test_search_bar_and_input_bar(name, opened, typing):
    image = dock(name)

    assert is_search_open(image) is opened
    assert is_input_shown(image) is typing


def test_ship_found_by_a_search():
    fleet = fleet_page(frame(('dock_search_found_cards.png', 80, 60)))

    ship = fleet.dock_find(target('Cooper', 73))
    # The first card is to remove the ship of the slot, the ship found comes after it
    assert ship.level == 73
    assert ship.button.area == CARD_GRIDS[(1, 0)].area
    # The dock read had an other level, she is the only Cooper
    assert fleet.dock_find(target('Cooper', 74)) is not None
    assert fleet.dock_find(target('Hasty', 72)) is None


@pytest.mark.parametrize('name, term', [
    ('Cooper', 'Cooper'),
    ('Halsey Powell', 'Halsey%sPowell'),
    ('London (Retrofit)', 'London'),
    ('Königsberg', 'nigsberg'),
    ("Queen Anne's Revenge", 'Queen%sAnne'),
    ('U-557', 'U-557'),
    ('Le Téméraire µ', 'raire'),
])
def test_search_term(name, term):
    assert search_term(name) == term


@pytest.mark.parametrize('read, name, fits', [
    ('Cooper', 'Cooper', True),
    ('London (Ret..', 'London (Retrofit)', True),
    ('Vittorio Cu..', 'Vittorio Cuniberti', True),
    ('Hasty', 'Cooper', False),
    ('Co', 'Cooper', False),
    ('', 'Cooper', False),
])
def test_name_on_a_card(read, name, fits):
    assert name_fits(read, name) is fits
