"""Collect only already-earned Strict Maid mission rewards."""
from datetime import datetime

import numpy as np

from module.base.timer import Timer
from module.campaign.event_completion import (
    EventCompletion, load_reward_observations, save_reward_observations)
from module.campaign.event_rewards import EventRewards, SUPPORTED_EVENTS
from module.campaign.event_strict_maid import STRICT_MAID_TAB
from module.campaign.event_strict_maid_rewards_assets import (
    STRICT_MAID_COLLECT_ALL, STRICT_MAID_EARNED, STRICT_MAID_HOME,
    STRICT_MAID_PAGE, STRICT_MAID_PROCEED)
from module.eventstory.eventstory import ensure_event_running, save_event_diagnostic
from module.logger import logger
from module.ui.page import page_main

CLAIMS_MAX = 2
REWARD_DIALOGS_MAX = 24


class StrictMaidRewards(EventRewards):
    def _strict_page(self):
        return all(self.match_template_color(button, offset=(3, 3), similarity=.85)
                   for button in (STRICT_MAID_PAGE, STRICT_MAID_HOME))

    def _strict_state(self):
        if not self._strict_page():
            return 'unknown'
        # The local collection deadline owns this verified page, including a
        # changed/disabled action. Unknown screens retain the device watchdog.
        self.device.stuck_record_clear()
        if not self.match_template_color(STRICT_MAID_COLLECT_ALL, offset=(3, 3), similarity=.85):
            return 'unknown'
        # Search only the four captured mission-control locations, using the
        # distinctive pink earned Collect heart. The orange Proceed cannot match.
        if self.match_template_color(STRICT_MAID_EARNED, offset=(0, 0, 480, 80), similarity=.9):
            return 'claimable'
        # An animated or partly unreadable pink heart is not an empty state.
        for left, right in ((657, 735), (1131, 1208)):
            control = self.device.image[511:654, left:right].astype(np.int16)
            red, green, blue = control[:, :, 0], control[:, :, 1], control[:, :, 2]
            pink = (red > 200) & (blue > 160) & (green < 235) & (red > green + 10) & (blue > green + 6)
            if np.count_nonzero(pink) >= 80:
                return 'unknown'
        # Keep a known, fully rendered unfinished-row control as positive proof
        # of the mission list. A new empty/grey layout needs its own observation.
        if self.match_template_color(STRICT_MAID_PROCEED, offset=(-480, -80, 0, 0), similarity=.9):
            return 'empty'
        return 'unknown'

    def _collect_strict(self, record, persist):
        settled = Timer(2, count=3).start()
        click_wait = Timer(2, count=3)
        empty_frames = dialogs = 0
        for _ in self.loop(skip_first=False, timeout=90):
            state = self._strict_state()
            if state == 'empty':
                empty_frames += 1
                if empty_frames >= 3 and settled.reached():
                    record.update(status='complete', reason='earned_missions_verified', pending_claim=False)
                    return
                continue
            empty_frames = 0
            settled.reset()
            if not click_wait.reached():
                continue
            if state == 'claimable':
                if record['claims'] >= CLAIMS_MAX:
                    record.update(status='failed', reason='collect_all_did_not_clear')
                    return
                record['claims'] += 1
                record['pending_claim'] = True
                persist()
                self.device.click(STRICT_MAID_COLLECT_ALL)
                click_wait.reset()
                continue
            dialog = self._reward_dialog() if record['pending_claim'] else None
            if dialog is not None:
                if dialogs >= REWARD_DIALOGS_MAX:
                    record.update(status='failed', reason='reward_dialog_limit')
                    return
                self.device.click(dialog)
                dialogs += 1
                record['reward_dialogs'] = dialogs
                click_wait.reset()
        record.update(status='failed', reason='earned_mission_collection_timeout')

    def _return_strict_main(self, helper):
        settled = Timer(2, count=3)
        clicks = 0
        for _ in self.loop(skip_first=False, timeout=10):
            if self.ui_page_appear(page_main):
                return True
            if not self._strict_page():
                if not clicks:
                    return helper._return_event_hub_main()
                continue
            if clicks >= 2 or not settled.reached():
                continue
            self.device.click(STRICT_MAID_HOME)
            clicks += 1
            settled.reset()
        return False

    def run(self, event, folder='./config/stars'):
        if event not in SUPPORTED_EVENTS:
            return dict(status='unsupported', diagnostics=[], returned_main=False, claims=0)
        ensure_event_running(self)
        account = self.config.config_name
        state = load_reward_observations(account, folder=folder)
        key = event + ':strict_maid_collection'
        previous = state.get(key, {})
        record = dict(status='collecting', diagnostics=[], returned_main=False, claims=0,
                      pending_claim=False, invocations=previous.get('invocations', 0) + 1,
                      started=datetime.now().isoformat(timespec='seconds'))
        state[key] = record

        def persist():
            save_reward_observations(account, state, folder=folder)

        # Daily missions can become earned after a previous successful check.
        # Every invocation is bounded; every free claim is audited before click.
        persist()
        helper = EventCompletion(config=self.config, device=self.device)
        try:
            helper._inspect_event_rewards_panel(record, tab_button=STRICT_MAID_TAB, panel='strict_maid')
            if record.get('status') != 'observed':
                record.update(status='failed', reason='strict_maid_page_unavailable')
            else:
                record['status'] = 'collecting'
                self._collect_strict(record, persist)
            path = save_event_diagnostic(self.device, f'{account}_strict_maid_rewards_{record["status"]}')
            if path:
                record['diagnostics'].append(path)
        except Exception:
            record.update(status='failed', reason='strict_maid_collection_exception')
            path = save_event_diagnostic(self.device, f'{account}_strict_maid_rewards_exception')
            if path:
                record['diagnostics'].append(path)
            raise
        finally:
            try:
                record['returned_main'] = self._return_strict_main(helper)
                if not record['returned_main']:
                    record.update(status='failed', reason='strict_maid_main_return_unrecognized')
            finally:
                persist()
        logger.attr('StrictMaidMissionRewards', record)
        return record
