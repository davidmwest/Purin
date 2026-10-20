import os
from types import SimpleNamespace
from unittest.mock import Mock

import cv2
import numpy as np
import pytest

from module.handler.info_handler import InfoHandler
from module.handler.tutorial import TUTORIAL_DIALOG_TEMPLATE, TUTORIAL_HAND_TEMPLATE, tutorial_dialog, tutorial_hand

DATA = os.path.join(os.path.dirname(__file__), 'data')
ROOT = os.path.join(os.path.dirname(__file__), '..')


def template():
    return cv2.imread(TUTORIAL_HAND_TEMPLATE, cv2.IMREAD_GRAYSCALE)


def dialog_template():
    return cv2.imread(TUTORIAL_DIALOG_TEMPLATE, cv2.IMREAD_GRAYSCALE)


def screen(file, left, top):
    """
    A screenshot of the EN client at 1280x720, black but for a part of it.
    """
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    part = cv2.cvtColor(cv2.imread(file), cv2.COLOR_BGR2RGB)
    height, width = part.shape[:2]
    image[top:top + height, left:left + width] = part
    return image


@pytest.mark.parametrize('file', ['tutorial_hand_1.png', 'tutorial_hand_2.png'])
def test_hand_points_at_the_button_to_tap(file):
    # "Let's go to HQ", the hand on the HQ button of page_main, its ring at 2 sizes
    point = tutorial_hand(screen(os.path.join(DATA, file), 520, 590), template())

    assert point is not None
    x, y = point
    # On the HQ button
    assert 590 < x < 630 and 660 < y < 695


def test_hand_right_above_its_button():
    # "Let's get some supplies~!" in the dorm, the hand right above the button "Supplies"
    point = tutorial_hand(screen(os.path.join(DATA, 'tutorial_hand_dorm.png'), 330, 580), template())

    assert point is not None
    x, y = point
    assert 395 < x < 415 and 605 < y < 630


@pytest.mark.parametrize('file, left, top', [
    ('formation/data/fleet_two_empty.png', 700, 170),
    ('combat/data/defeat_tips.png', 385, 128),
    ('combat/data/exp_info_d.png', 300, 30),
])
def test_no_hand_without_a_tutorial(file, left, top):
    image = screen(os.path.join(ROOT, file), left, top)

    assert tutorial_hand(image, template()) is None
    assert tutorial_dialog(image, dialog_template()) is None


def test_dialog_without_a_hand():
    # "You can choose from a wide variety of high level snacks with EXP buff effects."
    image = screen(os.path.join(DATA, 'tutorial_dialog.png'), 480, 515)

    assert tutorial_hand(image, template()) is None
    point = tutorial_dialog(image, dialog_template())
    assert point is not None
    x, y = point
    # In the dialog
    assert 480 < x < 790 and 515 < y < 650


class FakeTimer:
    def __init__(self):
        self.state = 'clear'

    def started(self):
        return self.state != 'clear'

    def start(self):
        self.state = 'started'

    def reached(self):
        return self.state == 'reached'

    def clear(self):
        self.state = 'clear'

    def reset(self):
        self.state = 'started'


def make_handler(image):
    handler = object.__new__(InfoHandler)
    handler.device = SimpleNamespace(image=image, click=Mock())
    handler._tutorial_hand_seen = FakeTimer()
    handler._tutorial_dialog_interval = FakeTimer()
    handler._tutorial_dialog_interval.state = 'reached'
    return handler


def test_hand_is_followed_once_it_stays():
    handler = make_handler(screen(os.path.join(DATA, 'tutorial_hand_1.png'), 520, 590))
    timer = handler._tutorial_hand_seen

    # Seen once, it may be a frame of something else
    assert handler.handle_tutorial() is False
    handler.device.click.assert_not_called()
    timer.state = 'reached'
    assert handler.handle_tutorial() is True

    button = handler.device.click.call_args[0][0]
    assert button.name.startswith('TUTORIAL_HAND_')
    # Seen again before the next tap
    assert timer.state == 'clear'


def test_tap_that_does_not_take_is_tried_around():
    handler = make_handler(screen(os.path.join(DATA, 'tutorial_hand_dorm.png'), 330, 580))
    taps = []
    for _ in range(3):
        handler._tutorial_hand_seen.state = 'reached'
        assert handler.handle_tutorial() is True
        x1, y1, x2, y2 = handler.device.click.call_args[0][0].button
        taps.append(((x1 + x2) // 2, (y1 + y2) // 2))

    # At the tip, then below it, where the button "Supplies" takes the tap
    (x0, y0), (x1, y1), (x2, y2) = taps
    assert (x1, y1) == (x0, y0 + 35)
    assert y2 == y0 + 35 and x2 < x1
    # A step tapped all around without going on ends in GameTooManyClickError, under one name
    names = {call[0][0].name for call in handler.device.click.call_args_list}
    assert len(names) == 1


def test_dialog_alone_is_tapped():
    handler = make_handler(screen(os.path.join(DATA, 'tutorial_dialog.png'), 480, 515))

    assert handler.handle_tutorial() is True

    assert handler.device.click.call_args[0][0].name == 'TUTORIAL_DIALOG'
