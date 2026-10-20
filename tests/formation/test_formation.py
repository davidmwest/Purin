import os
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from module.formation.formation import ADD_TEMPLATE, Formation, find_adds, row_of, ship_score
from module.retire.scanner import Ship

DATA = os.path.join(os.path.dirname(__file__), 'data')


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
