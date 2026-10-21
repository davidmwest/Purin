import os
from types import SimpleNamespace
from unittest.mock import Mock

import cv2
import numpy as np
import pytest

import module.config.server as server
from module.leveling.boost import LevelingBoost, parse_gain, parse_level

DATA = os.path.join(os.path.dirname(__file__), 'data')


@pytest.fixture(autouse=True)
def en():
    previous = server.server
    server.set_server('en')
    yield
    server.set_server(previous)


def frame(name, x, y):
    """
    A screenshot of the EN client at 1280x720 of the main account, black but for a part kept at (x, y)
    """
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    part = cv2.cvtColor(cv2.imread(os.path.join(DATA, name)), cv2.COLOR_BGR2RGB)
    height, width = part.shape[:2]
    image[y:y + height, x:x + width] = part
    return image


def boost_on(image):
    boost = LevelingBoost.__new__(LevelingBoost)
    boost.device = SimpleNamespace(image=image, stuck_record_add=Mock())
    boost.interval_timer = {}
    return boost


def info_tab():
    # Isokaze at 70, her Boost EXP button beside her level
    return frame('boost_info_level.png', 680, 270)


def dialog(name):
    return frame(name, 280, 100)


@pytest.mark.parametrize('text, gain', [
    # OCR takes the plus for "4-"
    ('4-3', 3), ('4-30', 30), ('+3', 3), ('', 0), ('43', 3), ('430', 30), ('4-3]', 3),
])
def test_levels_the_packs_add(text, gain):
    assert parse_gain(text) == gain


@pytest.mark.parametrize('text, level', [('LEVEL:70', 70), ('LCVBI:70', 70), ('LEVEL7', 7), ('', 0)])
def test_level_read(text, level):
    assert parse_level(text) == level


def test_info_tab_and_dialog():
    info = boost_on(info_tab())
    assert info.is_boost_enter() and not info.is_boost_dialog()
    assert info.info_read_level() == 70

    for name in ('boost_dialog_none.png', 'boost_dialog_gain3.png', 'boost_dialog_gain30.png'):
        shown = boost_on(dialog(name))
        assert shown.is_boost_dialog() and not shown.is_boost_enter()


@pytest.mark.parametrize('name, gain', [
    ('boost_dialog_none.png', 0), ('boost_dialog_gain3.png', 3), ('boost_dialog_gain30.png', 30)])
def test_dialog_reads(name, gain):
    # No packs, 10 packs: 70+3, and Auto, 274 packs: 70+30 to the level cap
    boost = boost_on(dialog(name))

    assert boost.boost_read_level() == 70
    assert boost.boost_read_gain() == gain


class FakeDialog:
    """
    The Boost EXP dialog of a ship at 70 with 9,511 EXP: levels come every 12,100 EXP up to 100, packs give 3,000.
    With level_exp, the EXP of each level from hers on.
    """

    def __init__(self, packs_owned=2811, closes_on_confirm=True, level_exp=None):
        self.packs = 0
        self.owned = packs_owned
        self.shown = False
        self.level = 70
        self.used = 0
        self.closes_on_confirm = closes_on_confirm
        self.level_exp = level_exp

    def gain(self):
        exp = 9511 + self.packs * 3000
        if self.level_exp is None:
            return min(exp // 12100, 100 - self.level)
        gain = 0
        for need in self.level_exp:
            if exp < need or self.level + gain >= 100:
                break
            exp -= need
            gain += 1
        return gain

    def click(self, button):
        name = button.name
        if name == 'BOOST_ENTER':
            self.shown = True
        elif name == 'BOOST_T1_PLUS10':
            self.packs = min(self.packs + 10, self.owned)
        elif name == 'BOOST_T1_PLUS':
            self.packs = min(self.packs + 1, self.owned)
        elif name == 'BOOST_T1_MINUS':
            self.packs = max(self.packs - 1, 0)
        elif name == 'BOOST_CLEAR':
            self.packs = 0
        elif name == 'BOOST_AUTO':
            # The packs to her level cap
            self.packs = 0
            while self.gain() < 100 - self.level and self.packs < self.owned:
                self.packs += 1
        elif name == 'BOOST_CLOSE':
            self.shown = False
        elif name == 'BOOST_CONFIRM':
            self.level += self.gain()
            self.used += self.packs
            self.packs = 0
            if self.closes_on_confirm:
                self.shown = False


def fake_boost(fake):
    boost = LevelingBoost.__new__(LevelingBoost)
    boost.device = SimpleNamespace(image=None, click=fake.click, sleep=Mock(), screenshot=Mock(),
                                   stuck_record_add=Mock(), click_record_clear=Mock())
    boost.interval_timer = {}
    boost.is_boost_dialog = lambda: fake.shown
    boost.is_boost_enter = lambda: not fake.shown
    boost.boost_read_level = lambda: fake.level
    boost.boost_read_gain = fake.gain
    boost.boost_read_stock = lambda: fake.owned
    boost.boost_read_count = lambda: fake.packs
    boost.info_read_level = lambda: fake.level
    boost.handle_popup_confirm = Mock(return_value=False)
    boost.handle_popup_cancel = Mock(return_value=False)
    return boost


@pytest.mark.parametrize('closes', [True, False])
def test_packs_raise_her_to_the_level_wanted_with_as_few_as_it_takes(closes):
    fake = FakeDialog(closes_on_confirm=closes)

    assert fake_boost(fake).boost_to(85) == 85
    # 15 levels are 171,989 EXP more, 58 packs
    assert fake.used == 58
    assert not fake.shown


def test_packs_go_to_100_at_most():
    fake = FakeDialog()

    assert fake_boost(fake).boost_to(107) == 100
    assert fake.level == 100


def test_too_few_packs_are_not_used():
    fake = FakeDialog(packs_owned=20)

    assert fake_boost(fake).boost_to(85) is None
    assert fake.used == 0 and fake.level == 70
    assert not fake.shown


def test_spare_packs_take_her_to_100():
    # 118 packs from 70 to 100, 2811 in storage
    fake = FakeDialog()

    assert fake_boost(fake).boost_to(85, spare=True) == 100
    assert fake.used == 118


def test_packs_storage_can_not_spare_go_to_the_level_needed():
    # 300 kept in storage: 400 can not spare 118
    fake = FakeDialog(packs_owned=400)

    assert fake_boost(fake).boost_to(85, spare=True) == 85
    assert fake.used == 58


def test_spare_packs_only_when_storage_spares_them():
    fake = FakeDialog(packs_owned=400)

    assert fake_boost(fake).boost_to(None, spare=True) is None
    assert fake.used == 0 and not fake.shown


def test_many_taps_do_not_trip_the_record_of_clicks():
    # Tirpitz at 72 to 97 took more than 12 taps of +10, Alas stopped it as a button tapped too often
    fake = FakeDialog()
    boost = fake_boost(fake)
    clicks = []

    def click(button):
        clicks.append(button.name)
        # The record of Alas: 12 taps of a button in the last 15 stop it
        if clicks[-15:].count(button.name) >= 12:
            raise AssertionError('Too many click')
        fake.click(button)

    def clear():
        clicks.clear()

    boost.device.click = click
    boost.device.click_record_clear = clear
    fake.level = 72

    assert boost.boost_to(97) == 97


def test_auto_takes_her_to_100_in_one_tap():
    fake = FakeDialog()
    boost = fake_boost(fake)
    taps = []
    boost.device.click = lambda button: taps.append(button.name) or fake.click(button)

    assert boost.boost_to(100) == 100
    assert taps.count('BOOST_T1_PLUS10') == 0 and 'BOOST_AUTO' in taps


def test_levels_near_100_take_more_than_a_tap():
    # Tirpitz at 72 to 97 stopped at +23: a tap of 10 packs, 30,000 EXP, did not make a level near 95
    fake = FakeDialog(level_exp=[20000 + 2000 * index for index in range(28)])
    fake.level = 72

    assert fake_boost(fake).boost_to(97) == 97
