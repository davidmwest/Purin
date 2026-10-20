import math
import os

import cv2
import numpy as np
import pytest

import module.config.server as server
from module.base.utils import crop
from module.inventory.info import InfoReader, NAME_MARGIN, NAME_STILL_TIME, NAME_WINDOW, NameScroll, parse_bonus, \
    parse_exp, parse_hull, parse_stat_label, parse_trailing_number, tidy_name

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


@pytest.mark.parametrize('text, expected', [('22', 'DD'), ('3C', 'BC'), ('CL', 'CL'), ('1A', 'IX'), ('SSV', 'SSV'),
                                            ('4R', 'AR'), ('BIV', 'BM'), ('D', 'DD'), ('BBV', 'BBV')])
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
    ('Conte di Cavour', 'Conte di Cavour'),
    ('Friedrich der Gro e', 'Friedrich der Groe'),
    ('1-404', 'I-404'),
    ('l-58', 'I-58'),
    ('U-81', 'U-81'),
])
def test_tidy_name(text, expected):
    assert tidy_name(text) == expected


@pytest.mark.parametrize('text, expected', [
    ('HP', 'hp'), ('Cost', 'cost'), ('Light', 'armor:light'), ('Medium', 'armor:medium'), ('lASW', 'asw'), ('', ''),
])
def test_parse_stat_label(text, expected):
    assert parse_stat_label(text) == expected


# Seconds between frames, of screenshots by ADB
FRAME_TIME = 0.37


def name_frames(fixture):
    """
    Frames of a name, from a strip of the area (236, 84, 420, 120) of frames one after the other.

    Returns:
        list[np.ndarray]: NAME_WINDOW of each frame
    """
    strip = load(fixture)
    windows = []
    for top in range(0, strip.shape[0], 36):
        image = np.zeros((720, 1280, 3), dtype=np.uint8)
        image[84:120, 236:420] = strip[top:top + 36]
        windows.append(crop(image, NAME_WINDOW))
    return windows


def follow(windows):
    """
    Returns:
        tuple[NameScroll, int]: The name, and the frames it took, 0 if not done
    """
    scroll = NameScroll()
    for index, window in enumerate(windows):
        scroll.add(window, now=index * FRAME_TIME)
        if scroll.done:
            return scroll, index + 1
    return scroll, 0


def test_long_name_put_together_from_frames(reader):
    # The page arriving with the dock behind, then "Nana Astar Deviluke" from its start to its end, and again
    windows = name_frames('name_nana.png')
    # A frame alone cuts the name
    assert reader.ocr_name.ocr([windows[1]], direct_ocr=True) != 'Nana Astar Deviluke'

    scroll, taken = follow(windows)

    assert scroll.whole is True
    # Done at its end, it starts again in the 14th frame
    assert 0 < taken <= 14
    assert tidy_name(reader.ocr_name.ocr([scroll.image], direct_ocr=True)) == 'Nana Astar Deviluke'


def test_start_of_a_long_name_missed(reader):
    # Frames from the middle of its scrolling, the name is whole once it has started again
    windows = name_frames('name_nana.png')[6:]

    scroll, taken = follow(windows)

    assert scroll.whole is True
    assert taken > 14 - 6
    assert tidy_name(reader.ocr_name.ocr([scroll.image], direct_ocr=True)) == 'Nana Astar Deviluke'


def test_retrofit_name_scrolling(reader):
    # Frames of "Fortune (Retrofit)" from its start until its end comes in, still scrolling
    scroll, taken = follow(name_frames('name_fortune.png'))

    assert taken == 0
    assert tidy_name(reader.ocr_name.ocr([scroll.image], direct_ocr=True)) == 'Fortune (Retrofit)'


def test_name_not_done_before_its_end():
    # Frames of "Hammann (Retrofit)" before its end comes in, the last one has a gap between 2 letters
    # at the right of the window
    scroll, taken = follow(name_frames('name_hammann.png'))

    assert taken == 0
    assert scroll.whole is False


def synthetic_name(width):
    """
    Returns:
        np.ndarray: White bars of letters on a dark background, as high as NAME_WINDOW, to its end
    """
    rng = np.random.default_rng(width)
    height = NAME_WINDOW[3] - NAME_WINDOW[1]
    image = np.full((height, width, 3), 40, dtype=np.uint8)
    x = 0
    while x < width - 8:
        letter = int(rng.integers(3, 9))
        image[6:height - 6, x:x + letter] = 250
        image[6 + int(rng.integers(0, 8)):10 + int(rng.integers(0, 8)), x:x + letter] = 40
        x += letter + int(rng.integers(2, 4))
    image[6:height - 6, width - 4:] = 250
    return image


def scroll_frames(name, step=5, pause=2, cycles=2):
    """
    Frames of a long name in its window: at its start a moment, scrolling to the left until it is 5 pixels from
    the right of the window, at its end a moment, and again.
    """
    width = NAME_WINDOW[2] - NAME_WINDOW[0]
    background = np.full((name.shape[0], width, 3), 40, dtype=np.uint8)
    track = np.concatenate([background[:, :4], name, background], axis=1)
    last = 4 + name.shape[1] + 5 - width
    offsets = [0] * pause + list(range(0, last, step))[1:] + [last] * pause
    return [track[:, offset:offset + width] for offset in offsets] * cycles


@pytest.mark.parametrize('pause', [1, 2])
def test_long_name_scrolled_to_its_end(pause):
    name = synthetic_name(260)

    scroll, taken = follow(scroll_frames(name, pause=pause))

    assert scroll.whole is True
    image = scroll.image[:, NAME_MARGIN:-NAME_MARGIN]
    assert np.array_equal(image[:, 4:4 + name.shape[1]], name)


def test_frame_of_the_page_arriving_is_skipped():
    name = synthetic_name(240)
    frames = scroll_frames(name)
    arriving = synthetic_name(frames[0].shape[1])[:, ::-1]

    scroll, taken = follow([arriving] + frames)

    assert scroll.whole is True
    image = scroll.image[:, NAME_MARGIN:-NAME_MARGIN]
    assert np.array_equal(image[:, 4:4 + name.shape[1]], name)


def test_name_that_fits_in_its_window():
    width = NAME_WINDOW[2] - NAME_WINDOW[0]
    window = np.full((NAME_WINDOW[3] - NAME_WINDOW[1], width, 3), 40, dtype=np.uint8)
    # In the middle, 6 pixels from each side
    window[:, 6:width - 6] = synthetic_name(width - 12)

    scroll, taken = follow([window.copy() for _ in range(10)])

    assert scroll.whole is True
    # Still long enough not to be a long name at its start
    assert taken == 1 + math.ceil(NAME_STILL_TIME / FRAME_TIME)
    assert scroll.image.shape[1] == width + 2 * NAME_MARGIN
