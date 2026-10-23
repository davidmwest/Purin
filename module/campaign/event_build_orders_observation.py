"""Inspect existing construction orders without submitting or expediting any."""
from datetime import datetime

from module.base.timer import Timer
from module.campaign.event_completion import load_reward_observations, save_reward_observations
from module.eventstory.eventstory import ensure_event_page, ensure_event_running, save_event_diagnostic
from module.gacha.mission_result import GachaSubmissionResult
from module.logger import logger
from module.ui.page import page_build


class EventBuildOrdersObservation(GachaSubmissionResult):
    def observe(self, event, folder='./config/stars'):
        account = self.config.config_name
        if account != 'alas' or event != 'event_20261022_cn':
            return dict(status='unsupported', diagnostics=[], returned_main=True)
        ensure_event_running(self)
        state = load_reward_observations(account, folder=folder)
        key = event + ':build_orders_observation'
        if key in state:
            return dict(state[key], reused=True)
        record = dict(status='observing', diagnostics=[], returned_main=False,
                      orders_clicks=0, started=datetime.now().isoformat(timespec='seconds'))
        state[key] = record

        def persist():
            save_reward_observations(account, state, folder=folder)

        persist()
        try:
            if not ensure_event_page(self, page_build, timeout=20, max_clicks=6):
                record.update(status='blocked', reason='build_page_unrecognized')
            else:
                settled = Timer(1, count=2).start()
                for _ in self.loop(skip_first=False, timeout=12):
                    if self.orders_page_present():
                        if settled.reached():
                            record.update(status='observed', reason='orders_capture_only')
                            break
                        continue
                    settled.reset()
                    if record['orders_clicks'] or self._receipt_present() or not self.ui_page_appear(page_build):
                        continue
                    active, first, last = self._gacha_side_navbar.get_info(self)
                    # Orders is third from the bottom in both observed sidebar layouts.
                    if first != 0 or last not in (3, 4) or active is None:
                        continue
                    record['orders_clicks'] = 1
                    persist()
                    self.device.click(self._gacha_side_navbar.grids.buttons[last - 2])
                else:
                    record.update(status='blocked', reason='orders_page_unrecognized')
            path = save_event_diagnostic(self.device, f'{account}_build_orders_{record["status"]}')
            if path:
                record['diagnostics'].append(path)
            else:
                record.update(status='blocked', reason='diagnostic_not_saved')
            if record['status'] == 'observed':
                record['returned_main'] = self.return_to_main()
        except Exception as exc:
            record.update(status='failed', reason=type(exc).__name__)
            raise
        finally:
            persist()
            logger.attr('EventBuildOrdersObservation', record)
        return record
