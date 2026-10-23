"""
Dock housekeeping on a schedule, not only when the dock fills up during a sortie.

EnhanceShips: ships of the dock that can still be enhanced, highest level first, take the materials
    the game recommends, see Enhancement.enhance_ships() and the options of Enhance.
RetireShips: the retire page of the build menu, one-click retire with the quick retire options set in the game,
    or old retire with the options of OldRetire, as Retirement.RetireMode says.
    Preview mode verifies conservative quick retire options and cancels the selection; nothing to retire is fine.
"""
from module.base.timer import Timer
from module.exception import GameStuckError
from module.gacha.ui import GachaUI
from module.logger import logger
from module.retire.assets import IN_RETIREMENT_CHECK, RETIRE_SETTING_QUIT
from module.retire.common import CommonDuplicates
from module.retire.retirement import Retirement
from module.ui.page import page_build, page_dock


class EnhanceShips(Retirement):
    def run(self):
        """
        Pages:
            in: Any page
            out: page_dock
        """
        self.ui_ensure(page_dock)
        self.handle_dock_cards_loading()
        total = self.enhance_ships()
        logger.info(f'Enhanced {total // 10} times')
        self.config.task_delay(server_update=True)


class RetireShips(CommonDuplicates, Retirement, GachaUI):
    def retire_page_enter(self):
        """
        The last option of the side menu of the build page.

        Pages:
            in: page_build
            out: IN_RETIREMENT_CHECK
        """
        logger.info('Retire page enter')
        navbar = self._gacha_side_navbar
        click_timer = Timer(3)
        timeout = Timer(30, count=60).start()
        for _ in self.loop():
            if self.appear(IN_RETIREMENT_CHECK, offset=(20, 20)):
                break
            if timeout.reached():
                raise GameStuckError('Can not enter the retire page of the build menu')
            # The side menu is gone once the retire page shows, do not wait for its last item to light up
            if click_timer.reached():
                _, _, last = navbar.get_info(main=self)
                if last is not None:
                    self.device.click(navbar.grids.buttons[last])
                    click_timer.reset()

    def run(self):
        """
        Pages:
            in: Any page
            out: page_build
        """
        mode = self.config.Retirement_RetireMode
        if mode == 'do_not_retire':
            logger.info('Retirement.RetireMode is do_not_retire, nothing is retired')
            self.config.task_delay(server_update=True)
            return

        if getattr(self.config, 'Retirement_CommonDuplicates', False):
            self._common_retire_prepare()
        dry_run = getattr(self.config, 'Retirement_DryRun', False)
        # A full dock may have interrupted a sortie with the settings popup still open.
        if dry_run and self.appear(RETIRE_SETTING_QUIT, offset=(30, 100)):
            self._retire_setting_quit()
        if not (dry_run and self.appear(IN_RETIREMENT_CHECK, offset=(20, 20))):
            self.ui_ensure(page_build)
            self.retire_page_enter()
        self.handle_dock_cards_loading()
        if dry_run:
            # Preview the conservative fallback proposed for a full dock. Actual retirement is guarded globally.
            self.quick_retire_setting_set(filter_5='keep_limit_break')
            self.retire_ships_one_click()
            self._retirement_quit()
            if getattr(self.config, 'Retirement_CommonDuplicates', False):
                self.retire_common_duplicates()
            self.config.task_delay(server_update=True)
            return
        if mode == 'old_retire':
            total = self.retire_ships_old()
        else:
            # The game asks to confirm when the quick retire options include rare ships, as in one_click_retire
            self.config.override(Retirement_RetireMode='one_click_retire')
            total = self.retire_ships_one_click()
        if total:
            logger.info(f'Retired, {total // 10} rounds')
        else:
            logger.info('Nothing to retire')
        self._retirement_quit()
        if getattr(self.config, 'Retirement_CommonDuplicates', False):
            self.retire_common_duplicates()
        self.config.task_delay(server_update=True)
