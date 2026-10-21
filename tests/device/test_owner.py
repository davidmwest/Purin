"""
One controller per emulator, see module/device/owner.py.

Code review (Codex, 2026-10-21): the .running status files were the only guard, two schedulers on one emulator both
stayed alive, the second overwrote the first's file and the first removed it on exit.
"""
import os
import subprocess
import sys
import textwrap

import pytest

from module.device.owner import SHARED_ENV, acquire_device, device_key, lock_path, release_device
from module.exception import EmulatorBusy, RequestHumanTakeover

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def holder(lock_dir, serial, config='other'):
    """
    An other process holding a device until its stdin closes.
    """
    code = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {ROOT!r})
        from module.device.owner import acquire_device
        acquire_device({serial!r}, {config!r}, lock_dir={str(lock_dir)!r})
        print('held', flush=True)
        sys.stdin.read()
    """)
    env = dict(os.environ)
    env.pop(SHARED_ENV, None)
    process = subprocess.Popen([sys.executable, '-c', code], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, cwd=ROOT, env=env, text=True)
    # The logger of the child prints its banner first
    for _ in range(50):
        line = process.stdout.readline()
        if not line or line.strip() == 'held':
            break
    assert line.strip() == 'held'
    return process


def stop(process):
    process.stdin.close()
    process.wait(timeout=30)


@pytest.fixture(autouse=True)
def no_shared(monkeypatch):
    monkeypatch.delenv(SHARED_ENV, raising=False)


def test_a_second_process_on_the_same_emulator_stops(tmp_path):
    process = holder(tmp_path, '127.0.0.1:5765', config='f2p')
    try:
        with pytest.raises(EmulatorBusy) as error:
            acquire_device('127.0.0.1:5765', 'f2p', lock_dir=str(tmp_path))
        # Who holds it is told
        assert 'f2p' in str(error.value)
        # The scheduler stops on it as on any takeover request
        assert isinstance(error.value, RequestHumanTakeover)
    finally:
        stop(process)


def test_an_other_config_set_to_the_same_serial_stops(tmp_path):
    process = holder(tmp_path, 'emulator-5764', config='alas')
    try:
        # emulator-5764 is 127.0.0.1:5765
        with pytest.raises(EmulatorBusy):
            acquire_device('127.0.0.1:5765', 'f2p', lock_dir=str(tmp_path))
    finally:
        stop(process)


def test_the_emulator_is_free_once_its_owner_ends(tmp_path):
    process = holder(tmp_path, '127.0.0.1:5675')
    stop(process)
    acquire_device('127.0.0.1:5675', 'alas', lock_dir=str(tmp_path))
    release_device('127.0.0.1:5675')


def test_the_same_process_takes_it_again(tmp_path):
    acquire_device('127.0.0.1:5555', 'alas', lock_dir=str(tmp_path))
    acquire_device('127.0.0.1:5555', 'alas', lock_dir=str(tmp_path))
    release_device('127.0.0.1:5555')


def test_two_emulators_two_owners(tmp_path):
    process = holder(tmp_path, '127.0.0.1:5675', config='alas')
    try:
        acquire_device('127.0.0.1:5765', 'f2p', lock_dir=str(tmp_path))
        release_device('127.0.0.1:5765')
    finally:
        stop(process)


def test_a_tool_that_only_looks_shares_it(tmp_path, monkeypatch):
    process = holder(tmp_path, '127.0.0.1:5765')
    try:
        monkeypatch.setenv(SHARED_ENV, '1')
        acquire_device('127.0.0.1:5765', 'explore', lock_dir=str(tmp_path))
    finally:
        stop(process)


def test_device_key():
    assert device_key('emulator-5554') == device_key('127.0.0.1:5555') == '127.0.0.1_5555'
    assert lock_path('127.0.0.1:5765', 'locks') == os.path.join('locks', '127.0.0.1_5765.lock')


def test_a_scheduler_removes_only_its_own_status(tmp_path, monkeypatch):
    import json
    from module.config import scheduler_state
    file = tmp_path / 'f2p.running'
    monkeypatch.setattr(scheduler_state, 'filepath_running', lambda name: str(file))
    # The second scheduler wrote its file over the first one's
    file.write_text(json.dumps({'pid': 222, 'task': None, 'paused': False}))
    scheduler_state.remove_scheduler_state('f2p', owner=111)
    assert file.exists()
    scheduler_state.remove_scheduler_state('f2p', owner=222)
    assert not file.exists()
