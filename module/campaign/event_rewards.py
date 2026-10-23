"""Receive earned cumulative PT rewards, without entering the event exchange."""
from datetime import datetime

from module.base.timer import Timer
from module.campaign.event_completion import (
    EventCompletion, load_reward_observations, save_reward_observations)
from module.campaign.event_completion_assets import TEMPESTA_FOGGY_SUPPLY_TAB
from module.campaign.event_rewards_assets import (
    FOGGY_CLAIM_BADGE, FOGGY_COLLECT_ALL, FOGGY_COLLECT_ALL_DISABLED,
    FOGGY_SUPPLY_HOME, FOGGY_SUPPLY_PAGE)
from module.combat.assets import GET_ITEMS_1, GET_ITEMS_2, GET_ITEMS_3, GET_SHIP
from module.eventstory.eventstory import ensure_event_running, save_event_diagnostic
from module.handler.assets import GET_ITEMS_SHIP_1, GET_SKIN
from module.logger import logger
from module.reward.reward import Reward

SUPPORTED_EVENTS = frozenset(('event_20261022_cn',))
CLAIMS_MAX = 2
REWARD_DIALOGS_MAX = 24


class EventRewards(Reward):
    def _foggy_page_appear(self):
        return all(self.match_template_color(button, offset=(3, 3), similarity=.85)
                   for button in (FOGGY_SUPPLY_PAGE, FOGGY_SUPPLY_HOME))

    def _foggy_state(self):
        """A missing marker never becomes permission to claim or success."""
        if not self._foggy_page_appear():
            return 'unknown'
        if self.match_template_color(FOGGY_CLAIM_BADGE, offset=(3, 3), similarity=.85):
            if self.match_template_color(FOGGY_COLLECT_ALL, offset=(3, 3), similarity=.85):
                return 'claimable'
            return 'unknown'
        # A partly animated/unmatched badge must not be mistaken for no rewards.
        if self.image_color_count(FOGGY_CLAIM_BADGE, color=(255, 66, 85), threshold=80, count=12):
            return 'unknown'
        if self.match_template_color(FOGGY_COLLECT_ALL_DISABLED, offset=(3, 3), similarity=.85):
            return 'empty'
        return 'unknown'

    def _reward_dialog(self):
        """Only known reward-dismissal screens; no generic popup confirmation."""
        for button in (GET_ITEMS_1, GET_ITEMS_2, GET_ITEMS_3, GET_ITEMS_SHIP_1, GET_SHIP, GET_SKIN):
            offset = (20, 20) if button in (GET_SHIP, GET_SKIN) else 5
            if self.appear(button, offset=offset):
                # GET_ITEMS_2/3 have the same dismissal location as GET_ITEMS_1.
                return GET_ITEMS_1 if button in (GET_ITEMS_2, GET_ITEMS_3) else button
        return None

    def _collect(self, record, persist):
        settled = Timer(2, count=3).start()
        click_wait = Timer(2, count=3)
        empty_frames = 0
        dialogs = 0
        for _ in self.loop(skip_first=False, timeout=90):
            state = self._foggy_state()
            # The local timeout bounds a known ladder inspection. Its disabled
            # button is a legitimate idle state, so keep the global watchdog
            # alive only while the exact page title and Home remain recognized.
            if state != 'unknown' or self._foggy_page_appear():
                self.device.stuck_record_clear()
            if state == 'empty':
                empty_frames += 1
                if empty_frames >= 3 and settled.reached():
                    record.update(status='complete', reason='free_pt_rewards_verified', pending_claim=False)
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
                # Durable audit precedes each free claim, including a rejected click.
                record['claims'] += 1
                record['pending_claim'] = True
                persist()
                self.device.click(FOGGY_COLLECT_ALL)
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
        record.update(status='failed', reason='free_pt_collection_timeout')

    def run(self, event, folder='./config/stars'):
        if event not in SUPPORTED_EVENTS:
            return dict(status='unsupported', diagnostics=[], returned_main=False, claims=0)
        ensure_event_running(self)
        account = self.config.config_name
        state = load_reward_observations(account, folder=folder)
        key = event + ':free_pt_collection'
        previous = state.get(key, {})
        record = dict(status='collecting', diagnostics=[], returned_main=False, claims=0,
                      pending_claim=False, invocations=previous.get('invocations', 0) + 1,
                      started=datetime.now().isoformat(timespec='seconds'))
        state[key] = record

        def persist():
            save_reward_observations(account, state, folder=folder)

        # Each invocation may recheck newly earned PT. A prior complete record
        # never prevents later claims; every click still needs fresh recognition.
        persist()
        helper = EventCompletion(config=self.config, device=self.device)
        try:
            helper._inspect_event_rewards_panel(record, tab_button=TEMPESTA_FOGGY_SUPPLY_TAB,
                                                 panel='foggy_supply')
            if record.get('status') != 'observed':
                record.update(status='failed', reason='free_pt_page_unavailable')
            else:
                record['status'] = 'collecting'
                self._collect(record, persist)
            path = save_event_diagnostic(self.device, f'{account}_event_free_pt_{record["status"]}')
            if path:
                record['diagnostics'].append(path)
        except Exception:
            record.update(status='failed', reason='free_pt_collection_exception')
            # Home recovery replaces the current image and screenshot history.
            # Preserve the actual failed collector screen before that recovery.
            path = save_event_diagnostic(self.device, f'{account}_event_free_pt_exception')
            if path:
                record['diagnostics'].append(path)
            raise
        finally:
            try:
                record['returned_main'] = helper._return_event_hub_main()
                if not record['returned_main']:
                    record.update(status='failed', reason='free_pt_main_return_unrecognized')
            finally:
                persist()
        logger.attr('EventFreePTRewards', record)
        return record
