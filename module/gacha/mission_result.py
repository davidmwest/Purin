"""Leave a recognized construction Orders page without collecting its queue."""
from module.base.timer import Timer
from module.combat.assets import GET_ITEMS_1, GET_ITEMS_2, GET_ITEMS_3, GET_SHIP
from module.eventstory.eventstory import ensure_event_running, save_event_diagnostic
from module.gacha.mission_result_assets import ORDERS_HOME, ORDERS_WITNESSES
from module.gacha.ui import GachaUI
from module.ui.page import page_main


class GachaSubmissionResult(GachaUI):
    def _receipt_present(self):
        # A claim journal owns receipt dismissal. Never navigate behind one,
        # even when its darkened background still matches page templates.
        return any(self.match_template_color(button, offset=(20, 20), similarity=.85)
                   for button in (GET_ITEMS_1, GET_ITEMS_2, GET_ITEMS_3, GET_SHIP))

    def orders_page_present(self):
        return (not self._receipt_present()
                and all(self.match_template_color(button, offset=(3, 3), similarity=.85)
                        for button in ORDERS_WITNESSES))

    def return_to_main(self):
        """Use only the captured Orders Home, with fresh settled confirmation.

        The Orders page can contain completed ships and expeditable builds.
        No sidebar, Finish, Launch, or generic popup handler is used here.
        """
        ensure_event_running(self)
        settled = Timer(1, count=2).start()
        clicked = Timer(2, count=2)
        previous, frames, clicks = None, 0, 0
        for _ in self.loop(skip_first=False, timeout=12):
            if self._receipt_present():
                # Stop immediately so the caller can resume its own journal.
                break
            state = ('main' if self.ui_page_appear(page_main)
                     else ('orders' if self.orders_page_present() else None))
            if state is None or state != previous:
                previous, frames = state, 1 if state else 0
                settled.reset()
                continue
            frames += 1
            if frames < 3 or not settled.reached():
                continue
            if state == 'main':
                self.ui_current = page_main
                return True
            if clicks >= 2 or not clicked.reached():
                continue
            self.device.click(ORDERS_HOME)
            clicks += 1
            clicked.reset()
            previous, frames = None, 0
            settled.reset()
        save_event_diagnostic(self.device, f'{self.config.config_name}_gacha_orders_main_blocked')
        return False
