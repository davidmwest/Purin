import collections
import time
from datetime import datetime

import numpy as np
from lxml import etree

from module.device.env import IS_WINDOWS
# Patch pkg_resources before importing adbutils and uiautomator2
from module.device.pkg_resources import get_distribution

# Just avoid being removed by import optimization
_ = get_distribution

from module.base.button import Button
from module.base.timer import Timer
from module.base.utils import load_image
from module.config.utils import get_server_next_update
from module.device.app_control import AppControl
from module.device.control import Control
from module.device.screenshot import Screenshot
from module.exception import (EmulatorNotRunningError, GameNotRunningError, GameStuckError, GameTooManyClickError,
                              RequestHumanTakeover)
from module.handler.assets import GET_MISSION
from module.handler.tutorial import TUTORIAL_CHECK_INTERVAL, TUTORIAL_DIALOG_TEMPLATE, TUTORIAL_GONE, \
    TUTORIAL_HAND_TEMPLATE, TUTORIAL_TAP_OFFSETS, is_same_step, tutorial_target
from module.logger import logger


def show_function_call():
    """
    INFO     21:07:31.554 │ Function calls:
                       <string>   L1 <module>
                   spawn.py L116 spawn_main()
                   spawn.py L129 _main()
                 process.py L314 _bootstrap()
                 process.py L108 run()
         process_manager.py L149 run_process()
                    alas.py L285 loop()
                    alas.py  L69 run()
                     src.py  L55 rogue()
                   rogue.py  L36 run()
                   rogue.py  L18 rogue_once()
                   entry.py L335 rogue_world_enter()
                    path.py L193 rogue_path_select()
    """
    import os
    import traceback
    stack = traceback.extract_stack()
    func_list = []
    for row in stack:
        filename, line_number, function_name, _ = row
        filename = os.path.basename(filename)
        # /tasks/character/switch.py:64 character_update()
        func_list.append([filename, str(line_number), function_name])
    max_filename = max([len(row[0]) for row in func_list])
    max_linenum = max([len(row[1]) for row in func_list]) + 1

    def format_(file, line, func):
        file = file.rjust(max_filename, " ")
        line = f'L{line}'.rjust(max_linenum, " ")
        if not func.startswith('<'):
            func = f'{func}()'
        return f'{file} {line} {func}'

    func_list = [f'\n{format_(*row)}' for row in func_list]
    logger.info('Function calls:' + ''.join(func_list))


class Device(Screenshot, Control, AppControl):
    _screen_size_checked = False
    detect_record = set()
    click_record = collections.deque(maxlen=15)
    stuck_timer = Timer(60, count=60).start()
    stuck_timer_long = Timer(180, count=180).start()
    stuck_long_wait_list = ['BATTLE_STATUS_S', 'PAUSE', 'LOGIN_CHECK', 'TEMPLATE_MANJUU']
    # A battle is given the long wait because it shows nothing to act on for minutes, but it never stands still.
    # A screen that has not changed by a single pixel for this many seconds during a battle is a hung game.
    stuck_frozen_wait_list = ['BATTLE_STATUS_S', 'PAUSE']
    stuck_frozen_seconds = 30
    _frozen_sample = None
    _frozen_since = 0.
    # Tutorials, see module/handler/tutorial.py
    _tutorial_templates = None
    _tutorial_check = None
    _tutorial_hand_seen = None
    _tutorial_dialog_interval = None
    # Where the hand pointed at the last tap, and taps at that step
    _tutorial_step = None
    _tutorial_taps = 0

    def __init__(self, *args, **kwargs):
        for trial in range(4):
            try:
                super().__init__(*args, **kwargs)
                break
            except EmulatorNotRunningError:
                if trial >= 3:
                    logger.critical('Failed to start emulator after 3 trial')
                    raise RequestHumanTakeover
                # Try to start emulator
                if self.emulator_instance is not None:
                    self.emulator_start()
                else:
                    logger.critical(
                        f'No emulator with serial "{self.config.Emulator_Serial}" found, '
                        f'please set a correct serial'
                    )
                    raise RequestHumanTakeover

        # Auto-fill emulator info
        if IS_WINDOWS and self.config.EmulatorInfo_Emulator == 'auto':
            _ = self.emulator_instance

        self.screenshot_interval_set()
        self.method_check()

        # Auto-select the fastest screenshot method
        if not self.config.is_template_config and self.config.Emulator_ScreenshotMethod == 'auto':
            self.run_simple_screenshot_benchmark()

        # Early init
        if self.config.is_actual_task:
            if self.config.Emulator_ControlMethod == 'MaaTouch':
                self.early_maatouch_init()
            if self.config.Emulator_ControlMethod == 'minitouch':
                self.early_minitouch_init()

    def run_simple_screenshot_benchmark(self):
        """
        Perform a screenshot method benchmark, test 3 times on each method.
        The fastest one will be set into config.
        """
        logger.info('run_simple_screenshot_benchmark')
        # Check resolution first
        self.resolution_check_uiautomator2()
        # Perform benchmark
        from module.daemon.benchmark import Benchmark
        disabled = self.is_stuck_detection_disabled
        bench = Benchmark(config=self.config, device=self)
        try:
            method = bench.run_simple_screenshot_benchmark()
        finally:
            # The benchmark is a daemon, which turns off stuck detection of the device it uses.
            # This device goes on to run tasks, where a stuck game has to be noticed.
            if not disabled:
                self.enable_stuck_detection()
        # Set
        with self.config.multi_set():
            self.config.Emulator_ScreenshotMethod = method
            # if method == 'nemu_ipc':
            #     self.config.Emulator_ControlMethod = 'nemu_ipc'

    def method_check(self):
        """
        Check combinations of screenshot method and control methods
        """
        # nemu_ipc should be together
        # if self.config.Emulator_ScreenshotMethod == 'nemu_ipc' and self.config.Emulator_ControlMethod != 'nemu_ipc':
        #     logger.warning('When using nemu_ipc, both screenshot and control should use nemu_ipc')
        #     self.config.Emulator_ControlMethod = 'nemu_ipc'
        # if self.config.Emulator_ScreenshotMethod != 'nemu_ipc' and self.config.Emulator_ControlMethod == 'nemu_ipc':
        #     logger.warning('When not using nemu_ipc, both screenshot and control should not use nemu_ipc')
        #     self.config.Emulator_ControlMethod = 'minitouch'
        # Allow Hermit on VMOS only
        if self.config.Emulator_ControlMethod == 'Hermit' and not self.is_vmos:
            logger.warning('ControlMethod Hermit is allowed on VMOS only')
            self.config.Emulator_ControlMethod = 'MaaTouch'
        if self.config.Emulator_ScreenshotMethod == 'ldopengl' \
                and self.config.Emulator_ControlMethod == 'minitouch':
            logger.warning('Use MaaTouch on ldplayer')
            self.config.Emulator_ControlMethod = 'MaaTouch'

        # Fallback to auto if nemu_ipc and ldopengl are selected on non-corresponding emulators
        if self.config.Emulator_ScreenshotMethod == 'nemu_ipc':
            if not (self.is_emulator and self.is_mumu_family):
                logger.warning('ScreenshotMethod nemu_ipc is available on MuMu Player 12 only, fallback to auto')
                self.config.Emulator_ScreenshotMethod = 'auto'
        if self.config.Emulator_ScreenshotMethod == 'ldopengl':
            if not (self.is_emulator and self.is_ldplayer_bluestacks_family):
                logger.warning('ScreenshotMethod ldopengl is available on LD Player only, fallback to auto')
                self.config.Emulator_ScreenshotMethod = 'auto'
        # DroidCast is available on SDK 23 (Android 6.0) to SDK 32 (Android 12), and on BlueStacks Air
        if self.config.Emulator_ScreenshotMethod in ['DroidCast', 'DroidCast_raw']:
            if not self.droidcast_available:
                logger.warning(f'ScreenshotMethod {self.config.Emulator_ScreenshotMethod} is available on '
                               f'Android 6.0 to 12 only (current sdk_ver={self.sdk_ver}), fallback to auto')
                self.config.Emulator_ScreenshotMethod = 'auto'
        if not IS_WINDOWS and self.config.Emulator_ScreenshotMethod in ['nemu_ipc', 'ldopengl']:
            logger.warning(f'ScreenshotMethod {self.config.Emulator_ScreenshotMethod} is available on Windows only, '
                           f'fallback to auto')
            self.config.Emulator_ScreenshotMethod = 'auto'

    def handle_night_commission(self, daily_trigger='21:00', threshold=30):
        """
        Args:
            daily_trigger (int): Time for commission refresh.
            threshold (int): Seconds around refresh time.

        Returns:
            bool: If handled.
        """
        update = get_server_next_update(daily_trigger=daily_trigger)
        now = datetime.now()
        diff = (update.timestamp() - now.timestamp()) % 86400
        if threshold < diff < 86400 - threshold:
            return False

        if GET_MISSION.match(self.image, offset=True):
            logger.info('Night commission appear.')
            self.click(GET_MISSION)
            return True

        return False

    def screenshot(self):
        """
        Returns:
            np.ndarray:
        """
        self.stuck_record_check()

        try:
            super().screenshot()
        except RequestHumanTakeover:
            if not self.ascreencap_available:
                logger.error('aScreenCap unavailable on current device, fallback to auto')
                self.run_simple_screenshot_benchmark()
                super().screenshot()
            else:
                raise

        if self.handle_night_commission():
            super().screenshot()
        # A tutorial takes no other tap, the task gets the screen back once it is followed through
        self.handle_tutorial()

        self.frozen_record_update()
        return self.image

    @staticmethod
    def _tutorial_template(file):
        image = load_image(file)
        return image if image.ndim == 2 else image[:, :, 0]

    def tutorial_follow(self):
        """
        Tap where the hand of a tutorial points, once it has been there a moment, or the dialog of a step
        without a hand.

        Returns:
            bool: If a tutorial is on screen
        """
        if self._tutorial_templates is None:
            Device._tutorial_templates = (
                self._tutorial_template(TUTORIAL_HAND_TEMPLATE), self._tutorial_template(TUTORIAL_DIALOG_TEMPLATE))
        if self._tutorial_hand_seen is None:
            self._tutorial_hand_seen = Timer(0.5, count=1)
            self._tutorial_dialog_interval = Timer(1.5, count=2)
        hand_template, dialog_template = self._tutorial_templates

        target = tutorial_target(self.image, hand_template, dialog_template)
        if target is None:
            self._tutorial_hand_seen.clear()
            return False
        kind, point = target
        if kind == 'hand':
            # Seen once, it may be a frame of something else
            if not self._tutorial_hand_seen.started():
                self._tutorial_hand_seen.start()
                return True
            if not self._tutorial_hand_seen.reached():
                return True
            if is_same_step(point, self._tutorial_step):
                # The tap before did not take
                self._tutorial_taps += 1
            else:
                self._tutorial_step = point
                self._tutorial_taps = 0
            dx, dy = TUTORIAL_TAP_OFFSETS[self._tutorial_taps % len(TUTORIAL_TAP_OFFSETS)]
            x, y = point[0] + dx, point[1] + dy
            area = (x - 5, y - 5, x + 5, y + 5)
            # Named by the step, a step tapped all around many times without the tutorial going on is stuck
            sx, sy = self._tutorial_step
            button = Button(area=area, color=(), button=area, name=f'TUTORIAL_HAND_{sx}_{sy}')
            logger.info(f'Tutorial points at {point}, tap {(x, y)}')
            self.click(button)
            self._tutorial_hand_seen.clear()
            return True

        self._tutorial_hand_seen.clear()
        if self._tutorial_dialog_interval.reached():
            x, y = point
            area = (x - 5, y - 5, x + 5, y + 5)
            logger.info('Tutorial dialog, tap to go on')
            self.click(Button(area=area, color=(), button=area, name='TUTORIAL_DIALOG'))
            self._tutorial_dialog_interval.reset()
        return True

    def handle_tutorial(self):
        """
        Follow a tutorial through, if one is on screen. Whatever a task is waiting for, a tutorial would hide it
        and take no other tap.

        Returns:
            bool: If a tutorial was followed
        """
        if self._tutorial_check is None:
            self._tutorial_check = Timer(TUTORIAL_CHECK_INTERVAL)
        if not self._tutorial_check.reached():
            return False
        self._tutorial_check.reset()
        if not self.tutorial_follow():
            return False

        logger.info('Tutorial on screen, following it')
        gone = Timer(TUTORIAL_GONE, count=3).start()
        while 1:
            self.stuck_record_check()
            super().screenshot()
            if self.tutorial_follow():
                gone.reset()
            elif gone.reached():
                break
        logger.info('Tutorial gone')
        return True

    def frozen_record_update(self):
        """
        Remember since when the screen has been exactly the same.
        """
        # Every 8th pixel is plenty to tell a frame that has not changed from one that has
        sample = self.image[::8, ::8]
        if self._frozen_sample is not None and sample.shape == self._frozen_sample.shape \
                and np.array_equal(sample, self._frozen_sample):
            return
        self._frozen_sample = sample.copy()
        self._frozen_since = time.time()

    def is_screen_frozen(self):
        """
        Returns:
            bool: If the screen has been exactly the same for stuck_frozen_seconds.
        """
        return self._frozen_since > 0 and time.time() - self._frozen_since > self.stuck_frozen_seconds

    def dump_hierarchy(self) -> etree._Element:
        self.stuck_record_check()
        return super().dump_hierarchy()

    def release_during_wait(self):
        # Scrcpy server is still sending video stream,
        # stop it during wait
        if self.config.Emulator_ScreenshotMethod == 'scrcpy':
            self._scrcpy_server_stop()
        if self.config.Emulator_ScreenshotMethod == 'nemu_ipc':
            self.nemu_ipc_release()

    def get_orientation(self):
        """
        Callbacks when orientation changed.
        """
        o = super().get_orientation()

        self.on_orientation_change_maatouch()

        return o

    def stuck_record_add(self, button):
        self.detect_record.add(str(button))

    def stuck_record_clear(self):
        self.detect_record = set()
        self.stuck_timer.reset()
        self.stuck_timer_long.reset()

    def stuck_record_check(self):
        """
        Raises:
            GameStuckError:
        """
        reached = self.stuck_timer.reached()
        reached_long = self.stuck_timer_long.reached()

        if not reached:
            return False
        if not reached_long:
            for button in self.stuck_long_wait_list:
                if button in self.detect_record:
                    if button in self.stuck_frozen_wait_list and self.is_screen_frozen():
                        logger.warning(f'Screen has not changed for {self.stuck_frozen_seconds} seconds')
                        break
                    return False

        show_function_call()
        logger.warning('Wait too long')
        logger.warning(f'Waiting for {self.detect_record}')
        self.stuck_record_clear()

        if self.app_is_running():
            raise GameStuckError(f'Wait too long')
        else:
            raise GameNotRunningError('Game died')

    def handle_control_check(self, button):
        self.stuck_record_clear()
        self.click_record_add(button)
        self.click_record_check()

    def click_record_add(self, button):
        self.click_record.append(str(button))

    def click_record_clear(self):
        self.click_record.clear()

    def click_record_remove(self, button):
        """
        Remove a button from `click_record`

        Args:
            button (Button):

        Returns:
            int: Number of button removed
        """
        removed = 0
        for _ in range(self.click_record.maxlen):
            try:
                self.click_record.remove(str(button))
                removed += 1
            except ValueError:
                # Value not in queue
                break

        return removed

    def click_record_check(self):
        """
        Raises:
            GameTooManyClickError:
        """
        count = collections.Counter(self.click_record).most_common(2)
        if count[0][1] >= 12:
            show_function_call()
            logger.warning(f'Too many click for a button: {count[0][0]}')
            logger.warning(f'History click: {[str(prev) for prev in self.click_record]}')
            self.click_record_clear()
            raise GameTooManyClickError(f'Too many click for a button: {count[0][0]}')
        if len(count) >= 2 and count[0][1] >= 6 and count[1][1] >= 6:
            show_function_call()
            logger.warning(f'Too many click between 2 buttons: {count[0][0]}, {count[1][0]}')
            logger.warning(f'History click: {[str(prev) for prev in self.click_record]}')
            self.click_record_clear()
            raise GameTooManyClickError(f'Too many click between 2 buttons: {count[0][0]}, {count[1][0]}')

    def disable_stuck_detection(self):
        """
        Disable stuck detection and its handler. Usually uses in semi auto and debugging.
        """
        logger.info('Disable stuck detection')

        def empty_function(*arg, **kwargs):
            return False

        self.click_record_check = empty_function
        self.stuck_record_check = empty_function

    def enable_stuck_detection(self):
        """
        Undo disable_stuck_detection().
        """
        logger.info('Enable stuck detection')
        # The empty functions of this instance cover the checks of the class
        self.__dict__.pop('click_record_check', None)
        self.__dict__.pop('stuck_record_check', None)
        self.stuck_record_clear()
        self.click_record_clear()

    @property
    def is_stuck_detection_disabled(self):
        return 'stuck_record_check' in self.__dict__

    # If the game was seen logged in since Alas started or stopped it. A login page after that
    # means the account was logged out, like by a log in on another device.
    logged_in = False

    def app_start(self):
        if not self.config.Error_HandleError:
            logger.critical('No app stop/start, because HandleError disabled')
            logger.critical('Please enable Alas.Error.HandleError or manually login to AzurLane')
            raise RequestHumanTakeover
        super().app_start()
        self.logged_in = False
        self.stuck_record_clear()
        self.click_record_clear()

    def app_stop(self):
        if not self.config.Error_HandleError:
            logger.critical('No app stop/start, because HandleError disabled')
            logger.critical('Please enable Alas.Error.HandleError or manually login to AzurLane')
            raise RequestHumanTakeover
        super().app_stop()
        self.logged_in = False
        self.stuck_record_clear()
        self.click_record_clear()
