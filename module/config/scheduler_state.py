"""
While the scheduler of a config runs, it tells what it is doing in ./config/<config_name>.running.
The GUI reads it, so a scheduler started from a terminal or a script shows up there as running,
and the GUI does not start a second one on the same emulator.
"""
import json
import os

from deploy.atomic import atomic_read_bytes, atomic_write
from module.config.utils import filepath_running
from module.logger import logger


def write_scheduler_state(config_name, task=None, paused=False):
    """
    Args:
        config_name (str):
        task (str): Task running now, None between tasks.
        paused (bool): If the scheduler holds because of the pause file.
    """
    state = {'pid': os.getpid(), 'task': task, 'paused': paused}
    try:
        atomic_write(filepath_running(config_name), json.dumps(state))
    except Exception as e:
        # Only the GUI needs it, never stop the scheduler for it
        logger.warning(f'Failed to write scheduler state: {e}')


def remove_scheduler_state(config_name, owner=None):
    """
    Args:
        config_name (str):
        owner (int): Pid the file must have to be removed, None for any. A scheduler that ends removes only its
            own file: one started after it wrote its own, and lost it to the first one's exit.
    """
    file = filepath_running(config_name)
    if owner is not None:
        try:
            if int(json.loads(atomic_read_bytes(file))['pid']) != owner:
                return
        except FileNotFoundError:
            return
        except Exception:
            # Unreadable, nobody's
            pass
    try:
        os.remove(file)
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.warning(f'Failed to remove scheduler state: {e}')


def read_scheduler_state(config_name):
    """
    Returns:
        dict: {'pid': int, 'task': str or None, 'paused': bool} of the scheduler running this config,
            None if there is none. The file of a scheduler that was killed is ignored.
    """
    file = filepath_running(config_name)
    try:
        state = json.loads(atomic_read_bytes(file))
        state['pid'] = int(state['pid'])
        written = os.path.getmtime(file)
    except Exception:
        # No file, an empty file, invalid content
        return None
    if not is_process_alive(state['pid'], started_before=written):
        return None
    return state


def is_process_alive(pid, started_before):
    """
    Args:
        pid (int):
        started_before (float): Timestamp. A process with this pid that started later is another one,
            which got the pid after the scheduler was gone.

    Returns:
        bool:
    """
    import psutil
    try:
        process = psutil.Process(pid)
        return process.is_running() and process.create_time() <= started_before + 1
    except psutil.Error:
        return False
