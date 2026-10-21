import os
from unittest.mock import Mock

import cv2
import numpy as np
import pytest

import module.config.server as server
from module.leveling.fleet import LevelingFleet, card_level, is_input_shown, is_search_open, name_fits, \
    search_term
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

    def sleep(self, second):
        pass


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
    # The only Cooper, the dock read had her lower: she went up since
    assert fleet.dock_find(target('Cooper', 72)) is not None
    # The dock read had her higher: the card is an other copy, ships never go down in level
    assert fleet.dock_find(target('Cooper', 74)) is None
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
    # Its dots read as a letter, one, or a colon, 2026-10-21
    ('Vittorio Cu.x', 'Vittorio Cuniberti', True),
    ('London (Ret.', 'London (Retrofit)', True),
    ('London (Ret:', 'London (Retrofit)', True),
    ('Vampire MET.x', 'Vampire', False),
    ('St. Louis', 'St. Louis', True),
    ('Hasty', 'Cooper', False),
    ('Vampire MET..', 'Vampire', False),
    ('Vampire MET..', 'Vampire META', True),
    ('Co', 'Cooper', False),
    ('', 'Cooper', False),
])
def test_name_on_a_card(read, name, fits):
    assert name_fits(read, name) is fits


def test_a_name_cut_short_by_its_scroll_is_read_again():
    first = info_view('info_fleet_1')
    # Names scrolled: Ning Hai and Ping Hai lose their first letters, as close to one as to the other
    scrolled = first.copy()
    for left in (873, 1059):
        scrolled[456:492, left:left + 156] = np.roll(first[456:492, left:left + 156], -14, axis=1)
    frames = iter([scrolled, scrolled, first, first, first])

    class Scrolling(FakeDevice):
        def screenshot(self):
            self.image = next(frames)
            return self.image

    fleet = LevelingFleet.__new__(LevelingFleet)
    fleet.device = Scrolling(scrolled)
    fleet.fleet_info_ensure = Mock()

    slots = fleet.fleet_read_slots(1)

    matcher = ShipMatcher(load_ship_data())
    assert [matcher.match(slot.name).name for slot in slots[4:]] == ['Ning Hai (Retrofit)', 'Ping Hai (Retrofit)']


def test_two_ships_as_close_are_no_match():
    matcher = ShipMatcher(load_ship_data())

    assert matcher.match('ing Hai Retrofit') is None
    assert matcher.match_score('Ning Hai(Retrofit') == (matcher.ships['Ning Hai (Retrofit)'], 1.)
    ship, score = matcher.match_score('na Astar Deviluk')
    assert ship.name == 'Nana Astar Deviluke' and 0.8 < score < 1


@pytest.mark.parametrize('read, level', [
    (73, 73), (125, 125), (1, 1),
    # Dmitri Donskoi at 101, 2026-10-21: a stray digit
    (1011, 101), (1250, 125), (9999, 99),
    (0, 0), (None, 0),
])
def test_level_on_a_card(read, level):
    assert card_level(read) == level


class Card:
    def __init__(self, level):
        self.level = level
        self.button = CARD_GRIDS[(1, 0)]


def dock_with(monkeypatch, cards):
    """
    The dock after a search, its cards as (name read, level read). ShipScanner and the OCR of names give them.
    """
    limits = {}

    class Scanner:
        def __init__(self, *args, **kwargs):
            pass

        def disable(self, *args):
            pass

        def set_limitation(self, **kwargs):
            limits.update(kwargs)

        def scan(self, image, output=False):
            # The scanner drops cards out of its limits of level
            ships = [Card(level) for _, level in cards]
            if limits.get('level', ()) is not None:
                ships = [ship for ship in ships if 1 <= ship.level <= 125]
            return ships

    class Names:
        def __init__(self, buttons, **kwargs):
            self.buttons = buttons

        def ocr(self, image):
            names = [name for name, level in cards if limits.get('level', ()) is None or 1 <= level <= 125]
            return names[0] if len(names) == 1 else names

    monkeypatch.setattr('module.leveling.fleet.ShipScanner', Scanner)
    monkeypatch.setattr('module.leveling.fleet.Ocr', Names)
    return fleet_page(np.zeros((720, 1280, 3), dtype=np.uint8))


def test_a_level_read_with_a_stray_digit_is_her_level(monkeypatch):
    # Dmitri Donskoi at 101, read 1011, the dock read had 100
    fleet = dock_with(monkeypatch, [('Dmitri Donsk..', 1011)])

    ship = fleet.dock_find(target('Dmitri Donskoi', 100))
    assert ship is not None and ship.level == 101
    # Still below the dock read, an other copy
    assert dock_with(monkeypatch, [('Dmitri Donsk..', 1011)]).dock_find(target('Dmitri Donskoi', 110)) is None


def test_a_name_cut_and_read_with_a_letter_is_found(monkeypatch):
    # Vittorio Cuniberti at 108, the dock read had 105
    fleet = dock_with(monkeypatch, [('Vittorio Cu.x', 108)])

    ship = fleet.dock_find(target('Vittorio Cuniberti', 105))
    assert ship is not None and ship.level == 108


def test_a_card_whose_level_can_not_be_read_is_not_taken(monkeypatch):
    # It could be an other copy of her
    fleet = dock_with(monkeypatch, [('Cooper', 0)])

    assert fleet.dock_find(target('Cooper', 73)) is None
