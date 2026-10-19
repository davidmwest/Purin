import os

import cv2
import pytest

import module.config.server as server
from module.inventory.info import InfoReader, parse_bonus, parse_exp, parse_hull, parse_stat_label, \
    parse_trailing_number, tidy_name

DATA = os.path.join(os.path.dirname(__file__), 'data')


@pytest.fixture(autouse=True)
def en():
    previous = server.server
    server.set_server('en')
    yield
    server.set_server(previous)


@pytest.fixture(scope='module')
def reader():
    return InfoReader()


def load(name):
    """
    Info tabs of the EN client at 1280x720, black except the parts read.
    """
    return cv2.cvtColor(cv2.imread(os.path.join(DATA, name)), cv2.COLOR_BGR2RGB)


def test_oathed_ship_at_the_maximum_level(reader):
    ship = reader.read(load('info_tiger.png'))

    assert ship['name'] == 'Tiger'
    assert ship['hull'] == 'CL'
    assert ship['stars'] == 6
    assert ship['affinity'] == 'oath'
    assert ship['level'] == 125
    # EXP of a ship at the maximum level does not matter
    assert (ship['exp'], ship['exp_next']) == (None, 0)
    assert ship['power'] == 5276
    assert ship['armor'] == 'light'
    assert ship['stats'] == {
        'hp': [5415, 560], 'fp': [209, 90], 'aa': [447, 65], 'asw': [175, 0], 'lck': [65, 0],
        'trp': [0, 0], 'avi': [0, 0], 'spd': [31, 0], 'cost': [14, 0],
        'rld': [227, 12], 'eva': [112, 40], 'acc': [190, 25],
    }
    assert ship['gear_levels'] == [13, 13, 10, 10, 10]
    assert ship['augment_level'] == 10
    assert ship['skill_levels'] == [10, 10, 10]
    assert ship['locked'] is True
    assert ship['favorite'] is False


def test_submarine_has_oxygen_and_ammo(reader):
    ship = reader.read(load('info_u557.png'))

    assert (ship['name'], ship['hull'], ship['stars'], ship['affinity']) == ('U-557', 'SS', 5, 'love')
    assert (ship['level'], ship['exp'], ship['exp_next']) == (115, 139714, 145000)
    assert ship['power'] == 3410
    assert ship['stats']['oxy'] == [188, 0]
    assert ship['stats']['amo'] == [2, 0]
    assert ship['stats']['spd'] == [14, 0]
    assert ship['stats']['trp'] == [493, 160]
    assert 'asw' not in ship['stats']
    assert ship['skill_levels'] == [1, 1, 1]


def test_retrofit_with_a_long_name(reader):
    ship = reader.read(load('info_london.png'))

    # The start of a long name is cut by the name box
    assert 'ondon (Retrofit' in ship['name']
    assert (ship['hull'], ship['affinity'], ship['level']) == ('CA', 'love', 118)
    assert (ship['exp'], ship['exp_next']) == (159091, 190000)
    assert ship['skill_levels'] == [6, 7, 1]


def test_research_ship(reader):
    ship = reader.read(load('info_vittorio.png'))

    assert (ship['hull'], ship['stars'], ship['affinity'], ship['level']) == ('DD', 6, 'crush', 105)
    assert ship['power'] == 4543
    assert ship['skill_levels'] == [10, 1, 1]


def test_carrier_power_keeps_every_digit(reader):
    ship = reader.read(load('info_hakuhou.png'))

    assert (ship['name'], ship['hull'], ship['affinity']) == ('Hakuhou', 'CV', 'stranger')
    assert ship['power'] == 5070
    assert ship['armor'] == 'heavy'
    assert ship['stats']['avi'] == [321, 425]
    assert (ship['level'], ship['exp'], ship['exp_next']) == (77, 538, 16560)


def test_ship_not_limit_broken_with_locked_skills(reader):
    ship = reader.read(load('info_phoenix.png'))

    assert (ship['name'], ship['stars'], ship['affinity'], ship['level']) == ('Phoenix', 2, 'oath', 70)
    # A skill not unlocked yet and an empty slot
    assert ship['skill_levels'] == [10, 0, 0]


@pytest.mark.parametrize('text, expected', [
    ('139714/145000', (139714, 145000)),
    # Ships at the maximum level, "/" may read as "7"
    ('236428/M8X', (None, 0)),
    ('Z26089367/M8X', (None, 0)),
    ('I880/19550', (880, 19550)),
    ('', (None, None)),
    ('999/10', (None, None)),
])
def test_parse_exp(text, expected):
    assert parse_exp(text) == expected


@pytest.mark.parametrize('text, expected', [('4-560', 560), ('4-5', 5), ('+90', 90), ('', 0), ('4-1D', 10)])
def test_parse_bonus(text, expected):
    # The OCR model of digits reads "+" as "4-"
    assert parse_bonus(text) == expected


@pytest.mark.parametrize('text, expected', [('-13', 13), ('L13', 13), ('-1D', 10), ('413', 13), ('', 0)])
def test_gear_level(text, expected):
    assert parse_trailing_number(text, maximum=13) == expected


@pytest.mark.parametrize('text, expected', [('22', 'DD'), ('3C', 'BC'), ('CL', 'CL'), ('1A', 'IX'), ('SSV', 'SSV')])
def test_parse_hull(text, expected):
    assert parse_hull(text) == expected


@pytest.mark.parametrize('text, expected', [
    ('T ige r', 'Tiger'),
    ('Shim ak aze', 'Shimakaze'),
    ('U-55 7', 'U-557'),
    ('Dmitri Donskoi', 'Dmitri Donskoi'),
    ('Ning Hai (Retrofi', 'Ning Hai (Retrofi'),
    ('Yorktown lI', 'Yorktown II'),
    ('.ondon (Retrofit', 'ondon (Retrofit'),
])
def test_tidy_name(text, expected):
    assert tidy_name(text) == expected


@pytest.mark.parametrize('text, expected', [
    ('HP', 'hp'), ('Cost', 'cost'), ('Light', 'armor:light'), ('Medium', 'armor:medium'), ('lASW', 'asw'), ('', ''),
])
def test_parse_stat_label(text, expected):
    assert parse_stat_label(text) == expected
