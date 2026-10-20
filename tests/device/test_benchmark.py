import contextlib
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from module.base.timer import Timer
from module.daemon import benchmark as benchmark_module
from module.daemon.benchmark import Benchmark
from module.device.device import Device
from module.exception import GameStuckError, GameTooManyClickError


class FakeBenchmark:
    def __init__(self, config, device):
        # Like DaemonBase
        device.disable_stuck_detection()

    def run_simple_screenshot_benchmark(self):
        return 'DroidCast_raw'


def make_device(screenshot='auto', control='ADB', sdk_ver=33, bluestacks_air=True):
    device = object.__new__(Device)
    device.config = SimpleNamespace(
        Emulator_ScreenshotMethod=screenshot,
        Emulator_ControlMethod=control,
        multi_set=contextlib.nullcontext,
    )
    device.resolution_check_uiautomator2 = Mock()
    device.app_is_running = Mock(return_value=True)
    # Cached properties, that would ask the emulator
    device.__dict__['sdk_ver'] = sdk_ver
    device.__dict__['is_bluestacks_air'] = bluestacks_air
    return device


def wait_too_long(device):
    """
    Nothing handled for longer than the long wait.
    """
    device.detect_record = {'IN_MAP'}
    device.stuck_timer = Timer(-1).start()
    device.stuck_timer_long = Timer(-1).start()


def test_benchmark_at_start_leaves_stuck_detection_on(monkeypatch):
    # A start with ScreenshotMethod auto runs the benchmark on the device that goes on to run tasks
    monkeypatch.setattr(benchmark_module, 'Benchmark', FakeBenchmark)
    device = make_device()

    device.run_simple_screenshot_benchmark()

    assert device.config.Emulator_ScreenshotMethod == 'DroidCast_raw'
    assert device.is_stuck_detection_disabled is False
    wait_too_long(device)
    with pytest.raises(GameStuckError):
        device.stuck_record_check()
    device.click_record.extend(['TUTORIAL_DIALOG'] * 12)
    with pytest.raises(GameTooManyClickError):
        device.click_record_check()


def test_benchmark_of_a_daemon_keeps_stuck_detection_off(monkeypatch):
    monkeypatch.setattr(benchmark_module, 'Benchmark', FakeBenchmark)
    device = make_device()
    device.disable_stuck_detection()

    device.run_simple_screenshot_benchmark()

    assert device.is_stuck_detection_disabled is True
    wait_too_long(device)
    assert device.stuck_record_check() is False


@pytest.mark.parametrize('sdk_ver, bluestacks_air, kept', [
    # BlueStacks Air is Android 13
    (33, True, True),
    (33, False, False),
    (32, False, True),
    (22, False, False),
])
def test_droidcast_where_it_is_available(sdk_ver, bluestacks_air, kept):
    device = make_device(screenshot='DroidCast_raw', sdk_ver=sdk_ver, bluestacks_air=bluestacks_air)

    device.method_check()

    expected = 'DroidCast_raw' if kept else 'auto'
    assert device.config.Emulator_ScreenshotMethod == expected


@pytest.mark.parametrize('bluestacks_air, tested', [(True, True), (False, False)])
def test_simple_benchmark_tests_what_method_check_keeps(bluestacks_air, tested):
    bench = object.__new__(Benchmark)
    bench.device = make_device(sdk_ver=33, bluestacks_air=bluestacks_air)
    bench.device.is_chinac_phone_cloud = False
    bench.device.nemu_ipc_available = lambda: False
    bench.device.ldopengl_available = lambda: False
    bench.benchmark = Mock(return_value=('ADB_nc', None))

    bench.run_simple_screenshot_benchmark()

    screenshot, _ = bench.benchmark.call_args[0]
    assert ('DroidCast_raw' in screenshot) is tested
    assert ('DroidCast' in screenshot) is tested
    assert 'ADB_nc' in screenshot
