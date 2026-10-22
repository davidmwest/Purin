from typing import Union

import numpy as np
from scipy.signal import find_peaks
from uiautomator2 import UiObject
from uiautomator2.exceptions import XPathElementNotFoundError
from uiautomator2.xpath import XPath, XPathSelector

import module.config.server as server
from module.base.button import Button
from module.base.timer import Timer
from module.base.utils import color_similarity_2d, crop
from module.handler.assets import *
from module.logger import logger
from module.map.assets import *
from module.ui.assets import *
from module.ui.page import page_campaign_menu
from module.ui.ui import UI


# English 2026-10-08 Capsule Rerun login landing page. Require both title and Home.
_CAPSULE_TITLE = Button(area=(126, 20, 342, 47), color=(0, 0, 0),
                        button=(1206, 18, 1250, 64),
                        file='./assets/en/ui/CAPSULE_RERUN_HOME.png', name='CAPSULE_RERUN_HOME')
_CAPSULE_HOME = Button(area=(1213, 26, 1245, 55), color=(0, 0, 0),
                       button=(1206, 18, 1250, 64),
                       file='./assets/en/ui/CAPSULE_RERUN_HOME.png', name='CAPSULE_RERUN_HOME_CHECK')


# A game update downloads before the login, 570 MB on 2026-10-22: the screen holds a progress bar for minutes, and
# a restart after 3 minutes of nothing known on it starts the download over. Stuck and click checks wait this long.
GAME_UPDATE_WAIT = 20 * 60


class LoginHandler(UI):
    def _handle_app_login(self):
        """
        Pages:
            in: Any page
            out: page_main

        Raises:
            GameStuckError:
            GameTooManyClickError:
            GameNotRunningError:
        """
        logger.hr('App login')

        confirm_timer = Timer(1.5, count=4).start()
        orientation_timer = Timer(5)
        login_success = False
        download_timer = Timer(GAME_UPDATE_WAIT)
        self.device.stuck_record_clear()
        self.device.click_record_clear()

        while 1:
            # A game update downloading
            if download_timer.started() and not download_timer.reached():
                self.device.stuck_record_clear()
                self.device.click_record_clear()
            # Watch device rotation
            if not login_success and orientation_timer.reached():
                # Screen may rotate after starting an app
                self.device.get_orientation()
                orientation_timer.reset()

            self.device.screenshot()

            # End
            if self.is_in_main():
                if confirm_timer.reached():
                    logger.info('Login to main confirm')
                    self.device.logged_in = True
                    break
            else:
                confirm_timer.reset()

            # Login
            if self.match_template_color(LOGIN_CHECK, offset=(30, 30), interval=5):
                self.device.click(LOGIN_CHECK)
                if not login_success:
                    logger.info('Login success')
                    login_success = True
            if self.appear(ANDROID_NO_RESPOND, offset=(30, 30), interval=5):
                logger.warning('Emulator no respond')
                self.device.click_record_add(ANDROID_NO_RESPOND)
                self.device.click_record_check()
                self.device.click(ANDROID_NO_RESPOND, control_check=False)
                continue
            if self.appear_then_click(LOGIN_ANNOUNCE, offset=(30, 30), interval=5):
                continue
            if self.appear_then_click(LOGIN_ANNOUNCE_2, offset=(30, 30), interval=5):
                continue
            if self.appear(EVENT_LIST_CHECK, offset=(30, 30), interval=5):
                self.device.click(BACK_ARROW)
                continue
            # Updates and maintenance
            if self.appear_then_click(MAINTENANCE_ANNOUNCE, offset=(30, 30), interval=5):
                continue
            if self.appear_then_click(LOGIN_GAME_UPDATE, offset=(30, 30), interval=5):
                self.game_update_wait(download_timer)
                continue
            if server.server == 'cn' and not login_success:
                if self.handle_cn_user_agreement():
                    continue
            # Player return
            if self.appear_then_click(LOGIN_RETURN_SIGN, offset=(30, 30), interval=5):
                continue
            if self.appear_then_click(LOGIN_RETURN_INFO, offset=(30, 30), interval=5):
                continue
            if self.appear_then_click(AVATAR_EXPIRED, offset=(30, 30), interval=5):
                continue
            # Popups
            if self.handle_popup_confirm('LOGIN'):
                if not login_success:
                    # The update asks in a popup like this one, which takes it first at 1440p (F2P, 2026-10-22)
                    self.game_update_wait(download_timer)
                continue
            if self.handle_urgent_commission():
                continue
            # Popups appear at page_main
            if self.ui_page_main_popups(get_ship=login_success):
                self.device.logged_in = True
                return True
            # New rerun landing page has a different Home icon than GOTO_MAIN.
            if server.server == 'en' and self.appear(_CAPSULE_TITLE, offset=(5, 5), interval=5) \
                    and self.appear(_CAPSULE_HOME, offset=(5, 5)):
                self.device.click(_CAPSULE_TITLE)
                continue
            # Always goto page_main
            if self.appear_then_click(GOTO_MAIN, offset=(30, 30), interval=5):
                continue

        return True

    @staticmethod
    def game_update_wait(download_timer):
        """
        Args:
            download_timer (Timer): Started at the first confirm of a game update, until GAME_UPDATE_WAIT
        """
        if not download_timer.started():
            logger.info(f'A game update may be downloading, wait for it up to {GAME_UPDATE_WAIT // 60} minutes')
            download_timer.start()

    _user_agreement_timer = Timer(1, count=2)

    def handle_cn_user_agreement(self):
        if not self._user_agreement_timer.reached():
            return False

        right = self.image_color_button(
            area=(640, 360, 1280, 720), color=(78, 189, 234),
            threshold=10, encourage=25, name='AGREEMENT_CONFIRM')
        if right is None:
            return False
        # 2026.04.17 No scroll anymore, just bare swipe before clicking confirm
        # if having blue button at right half of screen, but missing in left, it's a confirm button
        # if having both, it's a blue button at middle confirming login
        left = self.image_color_button(
            area=(0, 360, 640, 720), color=(78, 189, 234),
            threshold=10, encourage=25, name='AGREEMENT_CONFIRM')
        if left is None:
            # User agreement
            # just somewhere at the middle
            box = (350, 230, 920, 430)
            self.device.swipe_vector((0, -150), box, name='AGREEMENT_SCROLL')
            self.device.swipe_vector((0, -150), box, name='AGREEMENT_SCROLL')
            self.device.click(right)
            self._user_agreement_timer.reset()
            return True
        else:
            # User login
            self.device.click(right)
            self._user_agreement_timer.reset()
            return True

    def handle_app_login(self):
        """
        Returns:
            bool: If login success

        Raises:
            GameStuckError:
            GameTooManyClickError:
            GameNotRunningError:
        """
        logger.info('handle_app_login')
        self.device.screenshot_interval_set(1.0)
        try:
            self._handle_app_login()
        finally:
            self.device.screenshot_interval_set()

    def app_stop(self):
        logger.hr('App stop')
        self.device.app_stop()

    def app_start(self):
        logger.hr('App start')
        self.device.app_start()
        self.handle_app_login()
        # self.ensure_no_unfinished_campaign()

    def app_restart(self):
        logger.hr('App restart')
        self.device.app_stop()
        self.device.app_start()
        self.handle_app_login()
        # self.ensure_no_unfinished_campaign()
        self.config.task_delay(server_update=True)

    # The network diagnosis SDK of the game may log "send_packets error" in a loop. logd then takes a core of the
    # emulator and the game loses frames, until it is started again. A healthy start logs single digits of it.
    LOG_FLOOD_TAG = 'AliNetworkDiagnosis'
    LOG_FLOOD_LINES = 2000
    LOG_FLOOD_THRESHOLD = 1000
    # Once an hour at most, if the flood comes back at once
    _log_flood_restart = Timer(3600)

    def log_flood_count(self):
        """
        Returns:
            int: Lines of the network diagnosis among the last ones of logcat
        """
        output = self.device.adb_shell(['logcat', '-d', '-t', str(self.LOG_FLOOD_LINES)])
        return output.count(self.LOG_FLOOD_TAG)

    def app_restart_on_log_flood(self):
        """
        Returns:
            bool: If the game was started again
        """
        timer = LoginHandler._log_flood_restart
        if timer.started() and not timer.reached():
            return False
        count = self.log_flood_count()
        if count < self.LOG_FLOOD_THRESHOLD:
            return False

        logger.warning(f'{self.LOG_FLOOD_TAG} floods logcat, {count} of the last {self.LOG_FLOOD_LINES} lines, '
                       f'start the game again')
        # Lines of this flood do not count the next time
        self.device.adb_shell(['logcat', '-c'])
        timer.reset()
        self.app_stop()
        self.app_start()
        return True

    def ensure_no_unfinished_campaign(self, confirm_wait=3):
        """
        Pages:
            in: page_main
            out: page_main
        """

        def ensure_campaign_retreat():
            if self.appear_then_click(WITHDRAW, offset=(30, 30), interval=5):
                return True
            if self.handle_popup_confirm('WITHDRAW'):
                return True

        def in_campaign():
            return self.appear(CAMPAIGN_CHECK, offset=(30, 30)) \
                   or self.appear(CAMPAIGN_MENU_CHECK, offset=(30, 30)) \
                   or self.appear(EVENT_CHECK, offset=(30, 30)) \
                   or self.appear(SP_CHECK, offset=(30, 30))

        skip_first_screenshot = True
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # End
            if in_campaign():
                break

            # Click
            if self.ui_main_appear_then_click(page_campaign_menu, interval=3):
                continue
            if ensure_campaign_retreat():
                continue

        self.ui_goto_main()

    def handle_user_agreement(self, xp, hierarchy):
        """
        For CN only.
        CN client is bugged. User Agreement and Privacy Policy may popup again even you have agreed with it.
        This method scrolls to the bottom and click AGREE.

        Returns:
            bool: If handled.
        """

        if server.server == 'cn':
            area_wait_results = self.get_for_any_ele([
                XPS('//*[@text="sdk协议"]', xp, hierarchy),
                XPS('//*[@content-desc="sdk协议"]', xp, hierarchy)])
            if area_wait_results is False:
                return False
            agree_wait_results = self.get_for_any_ele([
                XPS('//*[@text="同意"]', xp, hierarchy),
                XPS('//*[@content-desc="同意"]', xp, hierarchy)])
            start_padding_results = self.get_for_any_ele([
                XPS('//*[@text="隐私政策"]', xp, hierarchy), XPS('//*[@content-desc="隐私政策"]', xp, hierarchy),
                XPS('//*[@text="用户协议"]', xp, hierarchy), XPS('//*[@content-desc="用户协议"]', xp, hierarchy)])
            start_margin_results = self.get_for_any_ele([
                XPS('//*[@text="请滑动阅读协议内容"]', xp, hierarchy),
                XPS('//*[@content-desc="请滑动阅读协议内容"]', xp, hierarchy)])

            test_image_original = self.device.image
            image_handle_crop = crop(
                test_image_original, (start_padding_results[2], 0, start_margin_results[2], 720), copy=False)
            # Image.fromarray(image_handle_crop).show()
            sims = color_similarity_2d(image_handle_crop, color=(182, 189, 202))
            points = np.sum(sims >= 255)
            if points == 0:
                return False
            sims_height = np.mean(sims, axis=1)
            # pyplot.plot(sims_height, color='r')
            # pyplot.show()
            peaks, __ = find_peaks(sims_height, height=225)
            if len(peaks) == 2:
                peaks = (peaks[0] + peaks[1]) / 2
            start_pos = [(start_padding_results[2] + start_margin_results[2]) / 2, float(peaks)]
            end_pos = [(start_padding_results[2] + start_margin_results[2]) / 2, area_wait_results[3]]
            logger.info("user agreement position find result: " + ', '.join('%.2f' % _ for _ in start_pos))
            logger.info("user agreement area expect:          " + 'x:963-973, y:259-279')

            self.device.drag(start_pos, end_pos, segments=2, shake=(0, 25), point_random=(0, 0, 0, 0),
                             shake_random=(0, -5, 0, 5))
            AGREE = Button(area=agree_wait_results, color=(), button=agree_wait_results, name='AGREE')
            self.device.click(AGREE)
            return True

    def handle_user_login(self, xp, hierarchy) -> bool:
        login_wait_results = self.get_for_any_ele([
            XPS('//*[@text="登录"]', xp, hierarchy),
            XPS('//*[@content-desc="登录"]', xp, hierarchy)])
        if login_wait_results is False:
            return False
        else:
            USER_LOGIN_BTN = Button(area=login_wait_results, color=(), button=login_wait_results, name='USER_LOGIN_BTN')
            self.device.click(USER_LOGIN_BTN)
            return True

    @staticmethod
    def get_for_any_ele(list_u2_path: list) -> Union[bool, tuple]:
        """
        Args:
            list_u2_path (list): [UiObject or XPathSelector]  In this case, len(list_u2_path) >= 1
        Returns:
            bool: False if wait failed
            tuple: (bounds): if wait success
        """
        for path in list_u2_path:
            try:
                if isinstance(path, UiObject):
                    if path.exists():
                        return path.bounds()
                    elif not path.exists():
                        continue
                elif isinstance(path, XPathSelector):
                    if path.exists:
                        return path.bounds
                    elif not path.exists:
                        continue
            except XPathElementNotFoundError:
                continue
        return False

    def get_cn_xp_hierarchy(self) -> tuple:
        d = self.device.u2
        xp = XPath(d)
        hierarchy = d.dump_hierarchy()
        return xp, hierarchy


class XPS(XPathSelector):
    def __init__(self, xpath, parent, source):
        super().__init__(parent, xpath, source)
