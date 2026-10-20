import os
from types import SimpleNamespace
from unittest.mock import Mock

import cv2
import numpy as np
import pytest

from module.handler.info_handler import InfoHandler
from module.handler.tutorial import TUTORIAL_HAND_TEMPLATE, tutorial_hand

DATA = os.path.join(os.path.dirname(__file__), 'data')
ROOT = os.path.join(os.path.dirname(__file__), '..')


def template():
    return cv2.imread(TUTORIAL_HAND_TEMPLATE, cv2.IMREAD_GRAYSCALE)


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


@pytest.mark.parametrize('file, left, top', [
    ('formation/data/fleet_two_empty.png', 700, 170),
    ('combat/data/defeat_tips.png', 385, 128),
    ('combat/data/exp_info_d.png', 300, 30),
])
def test_no_hand_without_a_tutorial(file, left, top):
    assert tutorial_hand(screen(os.path.join(ROOT, file), left, top), template()) is None


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


def test_hand_is_followed_once_it_stays():
    handler = object.__new__(InfoHandler)
    handler.device = SimpleNamespace(image=screen(os.path.join(DATA, 'tutorial_hand_1.png'), 520, 590), click=Mock())
    timer = FakeTimer()
    handler._tutorial_hand_seen = timer

    # Seen once, it may be a frame of something else
    assert handler.handle_tutorial() is False
    handler.device.click.assert_not_called()
    timer.state = 'reached'
    assert handler.handle_tutorial() is True

    button = handler.device.click.call_args[0][0]
    assert button.name.startswith('TUTORIAL_HAND_')
    # Seen again before the next tap
    assert timer.state == 'clear'
