import os
from unittest.mock import Mock

import cv2
import numpy as np
import pytest

from module.base import timer as timer_module
from module.device import device as device_module
from module.device.device import Device
from module.device.screenshot import Screenshot
from module.handler.tutorial import TUTORIAL_DIALOG_TEMPLATE, TUTORIAL_HAND_TEMPLATE, tutorial_dialog, tutorial_hand, \
    tutorial_target

DATA = os.path.join(os.path.dirname(__file__), 'data')
ROOT = os.path.join(os.path.dirname(__file__), '..')


def template():
    return cv2.imread(TUTORIAL_HAND_TEMPLATE, cv2.IMREAD_GRAYSCALE)


def dialog_template():
    return cv2.imread(TUTORIAL_DIALOG_TEMPLATE, cv2.IMREAD_GRAYSCALE)


def screen(*parts):
    """
    A screenshot of the EN client at 1280x720, black but for some parts of it.

    Args:
        *parts: (file, left, top)
    """
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    for file, left, top in parts:
        part = cv2.cvtColor(cv2.imread(file), cv2.COLOR_BGR2RGB)
        height, width = part.shape[:2]
        image[top:top + height, left:left + width] = part
    return image


def data(file, left, top):
    return os.path.join(DATA, file), left, top


# "Check Commissions here.", once the commander level unlocks commissions, on the main page after a battle
COMMISSION_DIALOG = data('tutorial_commission_dialog.png', 620, 238)
COMMISSION_HAND = data('tutorial_commission_hand.png', 1110, 420)


@pytest.mark.parametrize('file', ['tutorial_hand_1.png', 'tutorial_hand_2.png'])
def test_hand_points_at_the_button_to_tap(file):
    # "Let's go to HQ", the hand on the HQ button of page_main, its ring at 2 sizes
    point = tutorial_hand(screen(data(file, 520, 590)), template())

    assert point is not None
    x, y = point
    # On the HQ button
    assert 590 < x < 630 and 660 < y < 695


def test_hand_right_above_its_button():
    # "Let's get some supplies~!" in the dorm, the hand right above the button "Supplies"
    point = tutorial_hand(screen(data('tutorial_hand_dorm.png', 330, 580)), template())

    assert point is not None
    x, y = point
    assert 395 < x < 415 and 605 < y < 630


@pytest.mark.parametrize('file, left, top', [
    ('formation/data/fleet_two_empty.png', 700, 170),
    ('combat/data/defeat_tips.png', 385, 128),
    ('combat/data/exp_info_d.png', 300, 30),
])
def test_no_hand_without_a_tutorial(file, left, top):
    image = screen((os.path.join(ROOT, file), left, top))

    assert tutorial_hand(image, template()) is None
    assert tutorial_dialog(image, dialog_template()) is None


@pytest.mark.parametrize('part, box', [
    # "You can choose from a wide variety of high level snacks with EXP buff effects."
    (data('tutorial_dialog.png', 480, 515), (480, 515, 790, 650)),
    # The bars of the voice wave at 2 moments
    (COMMISSION_DIALOG, (620, 238, 925, 368)),
    (data('tutorial_commission_dialog_2.png', 620, 238), (620, 238, 925, 368)),
    # "HQ is extremely impressed with your performance, Commander.", its wave at the lower left
    (data('tutorial_second_fleet.png', 478, 265), (478, 265, 800, 455)),
])
def test_dialog_whatever_its_voice_wave_shows(part, box):
    image = screen(part)

    assert tutorial_hand(image, template()) is None
    point = tutorial_dialog(image, dialog_template())
    assert point is not None
    x, y = point
    x1, y1, x2, y2 = box
    assert x1 < x < x2 and y1 < y < y2


def test_hand_over_a_blue_icon_with_its_dialog():
    # "How about some Oxy-cola?", the hand stands up over the blue icon of the snack, its blue is not the template's
    image = screen(data('tutorial_hand_snack.png', 380, 290))

    assert tutorial_hand(image, template()) is None
    kind, (x, y) = tutorial_target(image, template(), dialog_template())

    assert kind == 'hand'
    # The tip of the finger, on the icon of Snacks1000
    assert 455 < x < 490 and 430 < y < 460


def test_hand_over_a_blue_button_with_its_dialog():
    image = screen(COMMISSION_DIALOG, COMMISSION_HAND)

    assert tutorial_hand(image, template()) is None
    kind, (x, y) = tutorial_target(image, template(), dialog_template())

    assert kind == 'hand'
    # On the button BATTLE
    assert 1120 < x < 1265 and 435 < y < 585


def test_hand_at_the_bottom_of_the_screen():
    # "Now we need to find someone that needs some rest", the hand on "Train" goes down to the bottom of the screen
    image = screen(data('tutorial_hand_bottom.png', 40, 360))

    kind, (x, y) = tutorial_target(image, template(), dialog_template())

    assert kind == 'hand'
    # On "Train 0/2"
    assert 20 < x < 205 and 655 < y < 700


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


def make_device(image):
    device = object.__new__(Device)
    device.image = image
    device.click = Mock()
    device._tutorial_hand_seen = FakeTimer()
    device._tutorial_dialog_interval = FakeTimer()
    device._tutorial_dialog_interval.state = 'reached'
    return device


def test_hand_is_followed_once_it_stays():
    device = make_device(screen(data('tutorial_hand_1.png', 520, 590)))
    timer = device._tutorial_hand_seen

    # Seen once, it may be a frame of something else
    assert device.tutorial_follow() is True
    device.click.assert_not_called()
    timer.state = 'reached'
    assert device.tutorial_follow() is True

    button = device.click.call_args[0][0]
    assert button.name.startswith('TUTORIAL_HAND_')
    # Seen again before the next tap
    assert timer.state == 'clear'


def test_tap_that_does_not_take_is_tried_around():
    device = make_device(screen(data('tutorial_hand_dorm.png', 330, 580)))
    taps = []
    for _ in range(3):
        device._tutorial_hand_seen.state = 'reached'
        assert device.tutorial_follow() is True
        x1, y1, x2, y2 = device.click.call_args[0][0].button
        taps.append(((x1 + x2) // 2, (y1 + y2) // 2))

    # At the tip, then below it, where the button "Supplies" takes the tap
    (x0, y0), (x1, y1), (x2, y2) = taps
    assert (x1, y1) == (x0, y0 + 35)
    assert y2 == y0 + 35 and x2 < x1
    # A step tapped all around without going on ends in GameTooManyClickError, under one name
    names = {call[0][0].name for call in device.click.call_args_list}
    assert len(names) == 1


def test_dialog_alone_is_tapped():
    device = make_device(screen(data('tutorial_dialog.png', 480, 515)))

    assert device.tutorial_follow() is True

    assert device.click.call_args[0][0].name == 'TUTORIAL_DIALOG'


class Clock:
    def __init__(self):
        self.now = 1000.

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(timer_module, 'time', clock)
    return clock


def run_device(monkeypatch, clock, frames):
    """
    A device that sees the given frames, then the last one, a screenshot every 0.3s.
    """
    device = object.__new__(Device)
    device.image = frames[0]
    device.click = Mock()
    device.stuck_record_check = Mock()
    remaining = list(frames[1:])

    def screenshot(self):
        clock.now += 0.3
        if remaining:
            self.image = remaining.pop(0)
        return self.image

    monkeypatch.setattr(Screenshot, 'screenshot', screenshot)
    return device


def test_tutorial_is_followed_through_before_the_task_gets_the_screen(monkeypatch, clock):
    # The commissions unlocked, the task waits for the end of a battle on the main page it does not know
    tutorial = screen(COMMISSION_DIALOG, COMMISSION_HAND)
    after = screen()
    device = run_device(monkeypatch, clock, [tutorial] * 4 + [after])

    assert device.handle_tutorial() is True

    buttons = [call[0][0] for call in device.click.call_args_list]
    assert buttons
    assert {button.name for button in buttons} == {'TUTORIAL_HAND_1187_477'}
    x1, y1, x2, y2 = buttons[0].button
    assert 1120 < x1 and x2 < 1265 and 435 < y1 and y2 < 585
    # Back to the task once the tutorial is gone, with the screen after it
    assert device.image is after
    assert device.stuck_record_check.called


def test_dialog_steps_are_tapped_at_an_interval(monkeypatch, clock):
    dialog = screen(data('tutorial_second_fleet.png', 478, 265))
    device = run_device(monkeypatch, clock, [dialog] * 8 + [screen()])

    assert device.handle_tutorial() is True

    names = [call[0][0].name for call in device.click.call_args_list]
    # 1.5s apart at least, while the dialog stays 2.4s
    assert names == ['TUTORIAL_DIALOG'] * 2


def test_without_a_tutorial_it_is_looked_for_once_a_second(monkeypatch, clock):
    device = run_device(monkeypatch, clock, [screen()])
    looked = []
    monkeypatch.setattr(device_module, 'tutorial_target', lambda *args: looked.append(clock.now))

    for _ in range(10):
        assert device.handle_tutorial() is False
        # Screenshots of the task
        clock.now += 0.3

    assert 3 <= len(looked) <= 4
    device.click.assert_not_called()
