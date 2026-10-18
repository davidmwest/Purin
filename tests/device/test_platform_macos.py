from types import SimpleNamespace
from unittest.mock import Mock

import psutil
import pytest

from module.device import connection as connection_module
from module.device.connection import Connection
from module.device.platform import platform_macos
from module.device.platform.platform_macos import (EmulatorInstanceMacOS, PlatformMacOS, is_instance_cmdline,
                                                   iter_bluestacks_instances)
from module.exception import EmulatorNotRunningError
from module.map.map_grids import SelectedGrids

BLUESTACKS = '/Applications/BlueStacks.app/Contents/MacOS/BlueStacks'
CONF = '''
bst.instance.Tiramisu64.adb_port="5555"
bst.instance.Tiramisu64.display_name="BlueStacks Air"
bst.instance.Tiramisu64.status.adb_port="5555"
bst.instance.Tiramisu64_12.adb_port="5675"
bst.instance.Tiramisu64_12.display_name="BlueStacks Azur Lane"
bst.instance.Tiramisu64_12.status.adb_port="5675"
bst.instance.Tiramisu64_13.adb_port="5685"
bst.instance.Tiramisu64_13.display_name="BlueStacks Nikke"
bst.instance.Tiramisu64_13.status.adb_port="5686"
bst.instance.Tiramisu64_20.adb_port="5755"
'''


def azur_lane():
    return EmulatorInstanceMacOS(serial='127.0.0.1:5675', name='Tiramisu64_12', path='/Applications/BlueStacks.app',
                                 display_name='BlueStacks Azur Lane')


def make_platform(serial='127.0.0.1:5675'):
    platform = object.__new__(PlatformMacOS)
    platform.serial = serial
    platform.adb_client = Mock()
    platform.__dict__['all_emulator_instances'] = list(iter_bluestacks_instances(CONF))
    return platform


class Process:
    def __init__(self, pid, *cmdline):
        self.pid = pid
        self.info = {'cmdline': list(cmdline)}
        self.terminate = Mock()
        self.kill = Mock()


def test_instances_are_read_from_bluestacks_conf():
    instances = {instance.name: instance for instance in iter_bluestacks_instances(CONF)}

    assert instances['Tiramisu64_12'].serial == '127.0.0.1:5675'
    assert instances['Tiramisu64_12'].display_name == 'BlueStacks Azur Lane'
    # The port in use wins over the one it was given
    assert instances['Tiramisu64_13'].serial == '127.0.0.1:5686'
    # An instance never started has no status yet
    assert instances['Tiramisu64_20'].serial == '127.0.0.1:5755'
    assert instances['Tiramisu64_20'].display_name == ''
    assert instances['Tiramisu64'].type == 'BlueStacksAir'


def test_instances_compare_and_hash_as_emulator_instances():
    assert azur_lane() == azur_lane()
    assert azur_lane() == 'BlueStacksAir'
    assert len({azur_lane(), azur_lane()}) == 1


def test_only_the_process_of_the_instance_is_matched():
    assert is_instance_cmdline([BLUESTACKS, '--instance', 'Tiramisu64_12'], 'Tiramisu64_12')
    # Other instances, NIKKE among them
    assert not is_instance_cmdline([BLUESTACKS, '--instance', 'Tiramisu64_13'], 'Tiramisu64_12')
    assert not is_instance_cmdline([BLUESTACKS, '--instance', 'Tiramisu64_12'], 'Tiramisu64_1')
    assert not is_instance_cmdline([BLUESTACKS, '--instance', 'Tiramisu64_1'], 'Tiramisu64_12')
    # The multi-instance manager, and anything that is not BlueStacks
    manager = '/Applications/BlueStacks Air multi-instance manager.app/Contents/MacOS/BlueStacks Air multi-instance manager'
    assert not is_instance_cmdline([manager], 'Tiramisu64_12')
    assert not is_instance_cmdline(['grep', '--instance', 'Tiramisu64_12'], 'Tiramisu64_12')
    assert not is_instance_cmdline([BLUESTACKS, 'Tiramisu64_12', '--instance'], 'Tiramisu64_12')
    # Processes of other users show no command line
    assert not is_instance_cmdline(None, 'Tiramisu64_12')
    assert not is_instance_cmdline([], 'Tiramisu64_12')


def test_instance_is_found_by_serial():
    assert make_platform('127.0.0.1:5675').emulator_instance.name == 'Tiramisu64_12'
    assert make_platform('127.0.0.1:5686').emulator_instance.name == 'Tiramisu64_13'
    assert make_platform('127.0.0.1:7555').emulator_instance is None


def test_start_opens_the_instance_in_the_background(monkeypatch):
    run = Mock()
    monkeypatch.setattr(platform_macos.subprocess, 'run', run)

    make_platform()._emulator_start(azur_lane())

    command = run.call_args[0][0]
    assert command == ['open', '-g', '-n', '-a', '/Applications/BlueStacks.app',
                       '--args', '--instance', 'Tiramisu64_12']


def stop_with(monkeypatch, processes, still_alive):
    """
    Args:
        processes (list[Process]): All running processes
        still_alive (list[list[Process]]): Processes alive after each wait
    """
    monkeypatch.setattr(platform_macos.psutil, 'process_iter', lambda attrs: iter(processes))
    waits = []

    def wait_procs(procs, timeout):
        waits.append(list(procs))
        return [], still_alive[len(waits) - 1]

    monkeypatch.setattr(platform_macos.psutil, 'wait_procs', wait_procs)
    platform = make_platform()
    platform._emulator_stop(azur_lane())
    return platform, waits


def test_stop_terminates_the_instance_alone(monkeypatch):
    azur = Process(15211, BLUESTACKS, '--instance', 'Tiramisu64_12')
    nikke = Process(42755, BLUESTACKS, '--instance', 'Tiramisu64_13')
    manager = Process(3655, '/Applications/BlueStacks Air multi-instance manager.app/Contents/MacOS/'
                            'BlueStacks Air multi-instance manager')

    platform, waits = stop_with(monkeypatch, [manager, azur, nikke], still_alive=[[], []])

    azur.terminate.assert_called_once_with()
    azur.kill.assert_not_called()
    for proc in [nikke, manager]:
        proc.terminate.assert_not_called()
        proc.kill.assert_not_called()
    assert waits[0] == [azur]
    platform.adb_client.disconnect.assert_called_once_with('127.0.0.1:5675')


def test_stop_kills_an_instance_that_hangs(monkeypatch):
    azur = Process(15211, BLUESTACKS, '--instance', 'Tiramisu64_12')

    stop_with(monkeypatch, [azur], still_alive=[[azur], []])

    azur.terminate.assert_called_once_with()
    azur.kill.assert_called_once_with()


def test_stop_fails_if_the_instance_survives(monkeypatch):
    azur = Process(15211, BLUESTACKS, '--instance', 'Tiramisu64_12')

    with pytest.raises(OSError):
        stop_with(monkeypatch, [azur], still_alive=[[azur], [azur]])


def test_stop_ignores_a_process_already_gone(monkeypatch):
    azur = Process(15211, BLUESTACKS, '--instance', 'Tiramisu64_12')
    azur.terminate.side_effect = psutil.NoSuchProcess(15211)

    stop_with(monkeypatch, [azur], still_alive=[[], []])


def test_stopping_an_instance_not_running_is_fine(monkeypatch):
    platform, waits = stop_with(monkeypatch, [], still_alive=[[], []])

    assert waits == [[], []]
    platform.adb_client.disconnect.assert_called_once_with('127.0.0.1:5675')


def test_emulator_start_stops_a_hung_instance_first(monkeypatch):
    platform = make_platform()
    calls = []
    platform._emulator_stop = lambda instance: calls.append(('stop', instance.name))
    platform._emulator_start = lambda instance: calls.append(('start', instance.name))
    platform.emulator_start_watch = lambda: True

    assert platform.emulator_start() is True
    assert calls == [('stop', 'Tiramisu64_12'), ('start', 'Tiramisu64_12')]


def test_emulator_functions_fail_without_an_instance():
    platform = make_platform('127.0.0.1:7555')

    assert platform.emulator_start() is False
    assert platform.emulator_stop() is False


def make_connection(message):
    conn = object.__new__(Connection)
    conn.config = SimpleNamespace(DEVICE_OVER_HTTP=False)
    conn.serial = '127.0.0.1:5675'
    conn.adb_client = Mock()
    conn.adb_client.connect.return_value = message
    conn.list_device = lambda: SelectedGrids([])
    return conn


def test_refused_connection_on_macos_means_the_emulator_is_not_running(monkeypatch):
    monkeypatch.setattr(connection_module, 'IS_MACINTOSH', True)
    conn = make_connection("failed to connect to '127.0.0.1:5675': Connection refused")

    with pytest.raises(EmulatorNotRunningError):
        conn.adb_connect()


def test_refused_connection_elsewhere_is_left_as_it_was(monkeypatch):
    monkeypatch.setattr(connection_module, 'IS_MACINTOSH', False)
    conn = make_connection("failed to connect to '127.0.0.1:5675': Connection refused")
    conn.detect_device = Mock()

    assert conn.adb_connect() is False
    assert conn.adb_client.connect.call_count == 3
