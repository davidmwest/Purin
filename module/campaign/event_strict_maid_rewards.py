"""Collect only already-earned Strict Maid mission rewards."""
from datetime import datetime

from module.base.timer import Timer
from module.campaign.event_completion import (
    EventCompletion, load_reward_observations, save_reward_observations)
from module.campaign.event_rewards import EventRewards, SUPPORTED_EVENTS
from module.campaign.event_strict_maid import STRICT_MAID_TAB
from module.campaign.event_strict_maid_rewards_assets import (
    OCR_STRICT_MAID_TOTAL, STRICT_MAID_HOME,
    STRICT_MAID_PAGE, STRICT_MAID_PROCEED, STRICT_MAID_ROW_COLLECT,
    STRICT_MAID_ROW_COLLECTED, STRICT_MAID_ROW_OFFSETS)
from module.eventstory.eventstory import ensure_event_running, save_event_diagnostic
from module.logger import logger
from module.ui.page import page_main

CLAIMS_MAX = 16
REWARD_DIALOGS_MAX = 24


class StrictMaidRewards(EventRewards):
    def _strict_page(self):
        return all(self.match_template_color(button, offset=(3, 3), similarity=.85)
                   for button in (STRICT_MAID_PAGE, STRICT_MAID_HOME))

    def _strict_rows(self):
        if not self._strict_page():
            return None
        # The local collection deadline owns this verified page, including a
        # changed/disabled action. Unknown screens retain the device watchdog.
        self.device.stuck_record_clear()
        # Collect All changes appearance when any row is earned. The exact
        # title/Home and each individual row own this collector's recognition.
        rows = []
        for index, (dx, dy) in enumerate(STRICT_MAID_ROW_OFFSETS):
            offset = (dx - 3, dy - 3, dx + 3, dy + 3)
            if self.match_template_color(STRICT_MAID_ROW_COLLECT[index], offset=offset, similarity=.9):
                rows.append('claimable')
            elif self.match_template_color(STRICT_MAID_ROW_COLLECTED[index], offset=offset, similarity=.9):
                rows.append('collected')
            elif self.match_template_color(
                    STRICT_MAID_PROCEED,
                    offset=(dx - 476, dy - 78, dx - 470, dy - 72), similarity=.9):
                rows.append('proceed')
            else:
                rows.append('unknown')
        return rows

    def _strict_state(self, rows=None):
        rows = self._strict_rows() if rows is None else rows
        if rows is None:
            return 'unknown'
        if 'claimable' in rows:
            return 'claimable'
        # Every visible row must positively be Collected or unfinished Proceed.
        # A missing/animated control never proves that rewards were received.
        if all(row in ('collected', 'proceed') for row in rows):
            return 'empty'
        return 'unknown'

    def _strict_total(self):
        value = OCR_STRICT_MAID_TOTAL.ocr(self.device.image)
        if isinstance(value, str) and value.isdigit():
            value = int(value)
            if 0 <= value <= 100000 and value % 100 == 0:
                return value
        return None

    def _collect_strict(self, record, persist):
        settled = Timer(2, count=3).start()
        click_wait = Timer(2, count=3)
        total_wait = Timer(1, count=2).start()
        total_value, total_reads = None, 0
        empty_frames = dialogs = 0
        for _ in self.loop(skip_first=False, timeout=90):
            rows = self._strict_rows()
            state = self._strict_state(rows) if rows is not None else 'unknown'
            total = self._strict_total() if rows is not None else None
            if total is None or total != total_value:
                total_value, total_reads = total, 1
                total_wait.reset()
                total_stable = False
            else:
                total_reads += 1
                total_stable = total_reads >= 2 and total_wait.reached()
            if record['pending_claim'] and total_stable:
                pending = record['claim_audit'][-1]
                if total > pending['total_before']:
                    pending.update(verified=True, total_after=total)
                    record.update(pending_claim=False,
                                  verified_claims=record.get('verified_claims', 0) + 1)
                    persist()
            # Never repeat a claim until stable total growth proves receipt.
            # Exact Collected and a disappeared Collect alone are insufficient.
            if record['pending_claim']:
                settled.reset()
                empty_frames = 0
                if not click_wait.reached():
                    continue
                dialog = self._reward_dialog()
                if dialog is not None:
                    if dialogs >= REWARD_DIALOGS_MAX:
                        record.update(status='failed', reason='reward_dialog_limit')
                        return
                    self.device.click(dialog)
                    dialogs += 1
                    record['reward_dialogs'] = dialogs
                    click_wait.reset()
                continue
            if state == 'empty':
                empty_frames += 1
                if empty_frames >= 3 and settled.reached() and total_stable:
                    record.update(status='complete', reason='earned_missions_verified', total=total)
                    return
                continue
            empty_frames = 0
            settled.reset()
            if not click_wait.reached():
                continue
            if state == 'claimable':
                if not total_stable:
                    continue
                if record['claims'] >= CLAIMS_MAX:
                    record.update(status='failed', reason='earned_mission_claim_limit')
                    return
                index = rows.index('claimable')
                record['claims'] += 1
                record['pending_claim'] = True
                record.setdefault('claim_audit', []).append(
                    dict(row=index, total_before=total, verified=False))
                persist()
                self.device.click(STRICT_MAID_ROW_COLLECT[index])
                click_wait.reset()
                total_reads = 0
                total_wait.reset()
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
                      verified_claims=0, claim_audit=[],
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
