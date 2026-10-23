from module.base.decorator import cached_property
from module.base.timer import Timer
from module.exception import ScriptError
from module.logger import logger
from module.retire.assets import *
from module.ui.setting import Setting
from module.ui.ui import UI

SETTING_WAIT = 12
SETTING_MAX_CLICKS = 3


def priority_button(button, en_area):
    """The generated EN priority assets point at None; target the intended R/E/N diamonds instead."""
    area = dict(button.raw_area, en=en_area)
    click = dict(button.raw_button, en=en_area)
    return Button(area=area, color=button.raw_color, button=click, name=button.name)


class QuickRetireSetting(Setting):
    def is_option_active(self, option: Button) -> bool:
        # Read the inner check, excluding the diamond outline and adjacent text. The F2P client downsamples 1440p.
        x1, y1, x2, y2 = option.area
        x, y = (x1 + x2) // 2, (y1 + y2) // 2
        return self.main.image_color_count((x - 6, y - 6, x + 6, y + 6),
                                           color=(255, 255, 255), threshold=30, count=20)

    def set(self, **kwargs):
        """Verify convergence; never assume the settings are correct after a timeout."""
        status = self._product_setting_status(**kwargs)
        retry = Timer(1, count=2)
        clicks = {}
        for _ in self.main.loop(timeout=SETTING_WAIT):
            self.show_active_buttons()
            pending = self.get_buttons_to_click(status)
            if not pending:
                return True
            if retry.reached():
                for button in pending:
                    if clicks.get(button, 0) < SETTING_MAX_CLICKS:
                        self.main.device.click(button)
                        clicks[button] = clicks.get(button, 0) + 1
                retry.reset()
        logger.warning('Quick retire settings did not converge; retirement must not continue')
        return False


class QuickRetireSettingHandler(UI):
    def _retire_setting_transition(self, click_button, check_button):
        retry, clicks = Timer(3), 0
        for _ in self.loop(timeout=SETTING_WAIT):
            # ENTER remains detectable through the dimmed popup background. Closing also requires QUIT to vanish.
            arrived = self.appear(check_button, offset=(30, 100))
            if click_button is RETIRE_SETTING_QUIT and self.appear(RETIRE_SETTING_QUIT, offset=(30, 100)):
                arrived = False
            if arrived:
                return
            if clicks < SETTING_MAX_CLICKS and retry.reached() and self.appear(click_button, offset=(30, 100)):
                self.device.click(click_button)
                clicks += 1
                retry.reset()
        raise ScriptError(f'Could not reach {check_button} while changing quick retire settings')

    def _retire_setting_enter(self):
        """
        Pages:
            in: IN_RETIREMENT_CHECK, RETIRE_SETTING_ENTER
            out: RETIRE_SETTING_QUIT
        """
        self._retire_setting_transition(RETIRE_SETTING_ENTER, RETIRE_SETTING_QUIT)

    def _retire_setting_quit(self):
        """
        Pages:
            in: RETIRE_SETTING_QUIT
            out: IN_RETIREMENT_CHECK, RETIRE_SETTING_ENTER
        """
        self._retire_setting_transition(RETIRE_SETTING_QUIT, RETIRE_SETTING_ENTER)

    @cached_property
    def retire_setting(self) -> QuickRetireSetting:
        setting = QuickRetireSetting(name='RETIRE', main=self)
        setting.reset_first = False
        setting.add_setting(
            setting='filter_1',
            option_buttons=[priority_button(RETIRE_SETTING_1, (818, 260, 847, 289))],
            option_names=['R'],
            option_default='R'
        )
        setting.add_setting(
            setting='filter_2',
            option_buttons=[priority_button(RETIRE_SETTING_2, (747, 317, 775, 345))],
            option_names=['E'],
            option_default='E'
        )
        setting.add_setting(
            setting='filter_3',
            option_buttons=[priority_button(RETIRE_SETTING_3, (895, 374, 923, 402))],
            option_names=['N'],
            option_default='N'
        )
        setting.add_setting(
            setting='filter_4',
            option_buttons=[RETIRE_SETTING_4],
            option_names=['all'],
            option_default='all'
        )
        setting.add_setting(
            setting='filter_5',
            option_buttons=[RETIRE_SETTING_5_PRESERVE, RETIRE_SETTING_5_ALL],
            option_names=['keep_limit_break', 'all'],
            option_default='all'
        )
        return setting

    def quick_retire_setting_set(self, filter_5='all'):
        """
        Set options of quick retire options.
        The first 4 options are forced to set to:
        - Prioritize Rarity 1: R (Rare)
        - Prioritize Rarity 1: E (Elite)
        - Prioritize Rarity 1: N (Normal)
        - If you own a ship that has been fully Limit Broken, this option
          determines what you want to do with the corresponding duplicate ships.
              Don't Keep

        Args:
            filter_5 (str, None): The fifth option in quick retire options.
                "If you own multiple copies of a ship that has not been fully Limit
                Broken, this option determines what you want to do with those copies."
                'keep_limit_break' for "Keep Enough to Max LB",
                'all' for "Don't Keep"
                None for don't change

        Pages:
            in: IN_RETIREMENT_CHECK, RETIRE_SETTING_ENTER
            out: IN_RETIREMENT_CHECK, RETIRE_SETTING_ENTER
        """
        self._retire_setting_enter()
        try:
            success = self.retire_setting.set(filter_5=filter_5)
        finally:
            self._retire_setting_quit()
        if not success:
            raise ScriptError('Quick retire settings could not be verified')

    def server_support_quick_retire_setting_fallback(self):
        """
        Fallback to the correct quick retire settings if user has wrong set.
        """
        return self.config.SERVER in ['cn', 'en', 'jp']
