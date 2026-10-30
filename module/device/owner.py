"""
One controller per emulator.

A process that takes screenshots of a device and taps on it holds an exclusive lock on that device for its whole
life, taken once its serial is known, before any screenshot. A second process on the same device, from the GUI, a
terminal, a one-off task or an other config set to the same serial, stops with EmulatorBusy instead of tapping
over the first. The lock is the operating system's: it goes with its process, killed or not, no file is left to
clean up.

The ./config/<config>.running files of module/config/scheduler_state.py only tell the GUI what a scheduler does,
they are kept by config and are not this lock (code review, 2026-10-21: two status-only processes both stayed
alive, the second overwrote the first's file and the first removed it on exit).

Tools that only look at a device a worker holds, with the worker paused, set PURIN_SHARED_DEVICE=1.
"""
import json
import os
import re

from module.exception import EmulatorBusy
from module.logger import logger

LOCK_DIR = './config/lock'
SHARED_ENV = 'PURIN_SHARED_DEVICE'

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None
    import msvcrt

# Serial to the open lock file of this process
_held = {}


def device_key(serial):
    """
    Args:
        serial (str): Serial of a device, as adb names it

    Returns:
        str: The same device under any of its names, safe in a file name.
            "emulator-5554" is "127.0.0.1:5555".
    """
    serial = str(serial).strip().lower()
    match = re.fullmatch(r'emulator-(\d+)', serial)
    if match:
        serial = f'127.0.0.1:{int(match.group(1)) + 1}'
    return re.sub(r'[^a-z0-9.\-]+', '_', serial)


def lock_path(serial, lock_dir=LOCK_DIR):
    return os.path.join(lock_dir, f'{device_key(serial)}.lock')


def _try_lock(file):
    """
    Returns:
        bool: If the lock was taken, without waiting
    """
    try:
        if fcntl is not None:
            fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:
            file.seek(0)
            msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
        return True
    except OSError:
        return False


def acquire_device(serial, config_name, lock_dir=LOCK_DIR):
    """
    Take the device for this process, again and again in it if asked.

    Args:
        serial (str):
        config_name (str): Written in the lock file, to tell who holds it
        lock_dir (str):

    Raises:
        EmulatorBusy: If an other process holds it
    """
    if os.environ.get(SHARED_ENV) == '1':
        logger.warning(f'{SHARED_ENV}=1, {serial} is not taken: an other process may be tapping on it')
        return
    key = device_key(serial)
    if key in _held:
        return
    os.makedirs(lock_dir, exist_ok=True)
    path = lock_path(serial, lock_dir)
    file = open(path, 'a+')
    if not _try_lock(file):
        try:
            file.seek(0)
            owner = file.read().strip()
        except OSError:
            owner = ''
        file.close()
        raise EmulatorBusy(f'{serial} is held by an other process: {owner or "unknown"}')
    file.seek(0)
    file.truncate()
    file.write(json.dumps({'pid': os.getpid(), 'config': config_name}))
    file.flush()
    _held[key] = file
    logger.info(f'Device {serial} taken by config {config_name}, pid {os.getpid()}')


def release_device(serial):
    """
    Give the device back before this process ends, for tests and tools. A process that ends gives it back anyway.
    """
    file = _held.pop(device_key(serial), None)
    if file is None:
        return
    try:
        if fcntl is not None:
            fcntl.flock(file.fileno(), fcntl.LOCK_UN)
        else:
            file.seek(0)
            msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
    except OSError:
        pass
    file.close()
