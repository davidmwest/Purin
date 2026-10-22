import os
import stat
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from module.device.device import Device
from module.device.user_input import UserInputWatcher, is_user_event
from module.exception import UserPlaying


@pytest.mark.parametrize('line, user', [
    # A click on the window of BlueStacks Air
    ('/dev/input/event2: EV_KEY       BTN_TOUCH            DOWN', True),
    ('/dev/input/event2: EV_KEY       BTN_MOUSE            DOWN', True),
    ('/dev/input/event2: EV_ABS       ABS_MT_TRACKING_ID   00000001', True),
    # Its end, after the press that counted
    ('/dev/input/event2: EV_KEY       BTN_TOUCH            UP', False),
    ('/dev/input/event2: EV_ABS       ABS_MT_TRACKING_ID   ffffffff', False),
    # The mouse device BlueStacks adds for a moment as the game starts, 2026-10-22: a release with no press
    ('/dev/input/event3: EV_KEY       BTN_MOUSE            UP', False),
    # A key held down
    ('/dev/input/event1: EV_KEY       KEY_A                REPEAT', True),
    # A key of its keyboard
    ('/dev/input/event1: EV_KEY       KEY_A                DOWN', True),
    # The mouse moving, the end of an event
    ('/dev/input/event2: EV_ABS       ABS_MT_POSITION_X    00003fff', False),
    ('/dev/input/event2: EV_SYN       SYN_REPORT           00000000', False),
    # What getevent tells as it starts
    ('add device 1: /dev/input/event2', False),
    ('  name:     "BlueStacks Virtual Touch"', False),
    ('', False),
])
def test_touches_of_a_person(line, user):
    assert is_user_event(line) is user


def test_watcher_tells_how_long_ago_someone_touched():
    watcher = UserInputWatcher('adb', '127.0.0.1:5765')

    assert watcher.seconds_since() == float('inf')
    watcher.on_line('/dev/input/event2: EV_ABS       ABS_MT_POSITION_X    00003fff')
    assert watcher.seconds_since() == float('inf')
    watcher.on_line('/dev/input/event2: EV_KEY       BTN_TOUCH            DOWN')
    assert watcher.seconds_since() < 1


def test_watcher_follows_getevent(tmp_path):
    # An adb that prints what getevent prints for a click, then stays open
    adb = tmp_path / 'adb'
    adb.write_text('#!/bin/sh\n'
                   'echo "add device 1: /dev/input/event2"\n'
                   'echo "/dev/input/event2: EV_KEY       BTN_TOUCH            DOWN"\n'
                   'sleep 5\n')
    adb.chmod(adb.stat().st_mode | stat.S_IEXEC)
    watcher = UserInputWatcher(str(adb), '127.0.0.1:5765').start()
    try:
        for _ in range(50):
            if watcher.last:
                break
            time.sleep(0.1)
        assert watcher.seconds_since() < 5
    finally:
        watcher.stop()


class Watcher:
    def __init__(self, seconds):
        self.seconds = seconds
        self.is_alive = True

    def seconds_since(self):
        return self.seconds


def device_with(seconds, wait=30, control='ADB', actual=True):
    device = Device.__new__(Device)
    device.config = SimpleNamespace(Optimization_UserInputWait=wait, is_actual_task=actual,
                                    Emulator_ControlMethod=control)
    device._user_input = Watcher(seconds)
    device.click_record_add = Mock()
    device.click_record_check = Mock()
    device.stuck_record_clear = Mock()
    return device


def test_a_tap_waits_for_someone_playing():
    device = device_with(seconds=3)

    with pytest.raises(UserPlaying):
        device.handle_control_check('BUTTON')
    device.click_record_add.assert_not_called()


@pytest.mark.parametrize('seconds, wait, control, actual', [
    # Nobody for 30s
    (31, 30, 'ADB', True),
    # Turned off
    (3, 0, 'ADB', True),
    # minitouch taps through the touch device, its taps would be taken for someone's
    (3, 30, 'minitouch', True),
    # Not a task, like the GUI
    (3, 30, 'ADB', False),
])
def test_taps_go_on(seconds, wait, control, actual):
    device = device_with(seconds, wait=wait, control=control, actual=actual)

    device.handle_control_check('BUTTON')
    device.click_record_add.assert_called_once_with('BUTTON')


def test_semi_auto_plays_along():
    # The daemons turn off stuck detection, they tap while someone plays
    device = device_with(seconds=3)
    device.disable_stuck_detection()

    device.handle_control_check('BUTTON')
    assert device.user_playing() is False
