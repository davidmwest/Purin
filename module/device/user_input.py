"""
Touches of the emulator's screen by a person, so that Purin does not tap over someone playing.

Purin taps with `adb shell input`, events injected into Android that skip the kernel's input devices. A click on the
emulator's window comes through its virtual touch device, "BlueStacks Virtual Touch" on BlueStacks, and a key
through its keyboard. `getevent` reads those devices: on BlueStacks Air, a minute and a half of Purin's taps
showed nothing there.

A touch, a mouse button or a key pressed counts, DOWN or held. Nothing else does: as the game starts, BlueStacks adds a
mouse device for a moment that sends a BTN_MOUSE UP with no DOWN before it, and one start in three a BTN_MOUSE of
value 00000020 before that UP (2026-10-22, both emulators: each restart of the game was taken for someone playing,
and restarted again 30 s later, up to 8 times in a row). A finger or the mouse moving does not count, nor a sensor.
"""
import subprocess
import threading
import time

from module.logger import logger

# Control methods that tap through Android's InputManager, unseen by `getevent`. minitouch writes to the touch
# device itself, Purin would take its own taps for someone's.
CONTROL_METHODS = ('ADB', 'uiautomator2', 'MaaTouch')
# Seconds between two tries to follow the input devices, like while the emulator starts again
RETRY_SECONDS = 5
# A touch this long after the one before is logged, not every one of a click
LOG_AFTER_SECONDS = 10


def is_user_event(line):
    """
    Args:
        line (str): A line of `getevent -l`, like '/dev/input/event2: EV_KEY       BTN_TOUCH            DOWN'

    Returns:
        bool: If someone touched the screen, clicked or pressed a key
    """
    parts = str(line).split()
    # `getevent -lt` puts a timestamp first, '[   22292.586877]'
    while parts and not parts[0].startswith('/dev/input/'):
        if not (parts[0].startswith('[') or parts[0].endswith(']') or parts[0].replace('.', '').isdigit()):
            return False
        parts = parts[1:]
    if len(parts) < 4:
        return False
    kind, code, value = parts[1], parts[2], parts[3]
    if kind == 'EV_KEY':
        return value in ('DOWN', 'REPEAT')
    if code == 'ABS_MT_TRACKING_ID':
        # A contact starts with its id, ends with ffffffff
        return value.lower() != 'ffffffff'
    return False


class UserInputWatcher:
    """
    Follows the input devices of an emulator in a thread of its own, and tells how long ago someone used them.
    """

    def __init__(self, adb_binary, serial):
        """
        Args:
            adb_binary (str):
            serial (str): Serial of the emulator, like 127.0.0.1:5765
        """
        self.adb_binary = adb_binary
        self.serial = serial
        # time.time() of the last touch, 0 for none yet
        self.last = 0.
        self._stop = threading.Event()
        self._proc = None
        self._thread = threading.Thread(target=self._run, name=f'UserInput {serial}', daemon=True)

    def start(self):
        logger.info(f'Following touches on {self.serial}, Purin waits for someone playing')
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        proc = self._proc
        if proc is not None:
            try:
                proc.kill()
            except OSError:
                pass

    @property
    def is_alive(self):
        return self._thread.is_alive()

    def seconds_since(self):
        """
        Returns:
            float: Seconds since the last touch, infinite if there was none
        """
        if not self.last:
            return float('inf')
        return time.time() - self.last

    def on_line(self, line):
        """
        Args:
            line (str): A line of `getevent -l`
        """
        if not is_user_event(line):
            return
        if self.seconds_since() > LOG_AFTER_SECONDS:
            # The event, should an emulator send one more that is nobody's
            logger.info(f'Someone touched {self.serial}: {" ".join(str(line).split())}')
        self.last = time.time()

    def _follow(self):
        self._proc = subprocess.Popen(
            [self.adb_binary, '-s', self.serial, 'shell', 'getevent', '-l'],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
            text=True, errors='replace', bufsize=1)
        try:
            for line in self._proc.stdout:
                if self._stop.is_set():
                    break
                self.on_line(line)
        finally:
            try:
                self._proc.kill()
                self._proc.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                pass

    def _run(self):
        while not self._stop.is_set():
            try:
                self._follow()
            except Exception as e:
                # adb gone or the emulator restarting, try again soon
                logger.info(f'Touches on {self.serial} not followed for now: {e}')
            if self._stop.wait(RETRY_SECONDS):
                break
