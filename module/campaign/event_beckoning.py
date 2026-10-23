"""Receive two observed free tickets, then inspect one locked character story.

This is not a side-story completion runner. Unknown unlock confirmations are
captured without action, and each mission/card action has a durable budget.
"""
from datetime import datetime

from module.base.timer import Timer
from module.campaign.event_beckoning_assets import (
    BECKONING_FIRST_LOCK, BECKONING_FIRST_STORY, BECKONING_MONTHLY_COLLECT,
    BECKONING_MONTHLY_EARNED, BECKONING_VICTORY_COLLECT, BECKONING_VICTORY_MISSION,
    OCR_BECKONING_TICKETS, OCR_BECKONING_VICTORIES)
from module.campaign.event_completion import (
    EventCompletion, load_reward_observations, save_reward_observations)
from module.campaign.event_completion_assets import (
    TEMPESTA_BECKONING_CHOICE, TEMPESTA_SIDE_CHOICE_BACK, TEMPESTA_SIDE_CHOICE_PAGE)
from module.campaign.event_rewards import EventRewards
from module.eventstory.eventstory import ensure_event_running, save_event_diagnostic
from module.logger import logger
from module.reward.reward import Reward

EVENT = 'event_20261022_cn'
ROWS = (('monthly', BECKONING_MONTHLY_EARNED, BECKONING_MONTHLY_COLLECT),
        ('victories', BECKONING_VICTORY_MISSION, BECKONING_VICTORY_COLLECT))
RECEIPTS_MAX = 8


class BeckoningTickets(Reward):
    def _matches(self, button):
        return self.match_template_color(button, offset=(3, 3), similarity=.85)

    def _on_choice(self):
        return self._matches(TEMPESTA_SIDE_CHOICE_PAGE) and self._matches(TEMPESTA_SIDE_CHOICE_BACK)

    def _tickets(self):
        value = OCR_BECKONING_TICKETS.ocr(self.device.image)
        return value if isinstance(value, int) and 0 <= value <= 99 else None

    def _earned(self, row):
        if row == 'monthly':
            # The exact observed mission crop includes its completed (1/1).
            return True
        current, remaining, total = OCR_BECKONING_VICTORIES.ocr(self.device.image)
        return total == 15 and current == 15 and remaining == 0

    def _capture(self, record, suffix):
        path = save_event_diagnostic(self.device, f'{self.config.config_name}_beckoning_{suffix}')
        if path:
            record['diagnostics'].append(path)
        return path

    def _collect(self, record, persist):
        """Never repeat a row click, even after interruption or rejected input."""
        settled = Timer(2, count=3).start()
        click_wait = Timer(2, count=3)
        ticket_wait = Timer(1, count=2).start()
        ticket_value, ticket_reads = None, 0
        receipts = 0
        for _ in self.loop(skip_first=False, timeout=45):
            pending = [(name, audit) for name, audit in record['rows'].items() if audit.get('pending')]
            if not click_wait.reached():
                continue
            if not self._on_choice():
                ticket_reads = 0
                ticket_wait.reset()
                # Receipt dismissal is authorized only after one of our claims.
                dialog = EventRewards._reward_dialog(self) if pending else None
                if dialog is not None:
                    if receipts >= RECEIPTS_MAX:
                        record.update(status='failed', reason='ticket_receipt_limit')
                        return False
                    self.device.click(dialog)
                    receipts += 1
                    click_wait.reset()
                continue
            tickets = self._tickets()
            if tickets is None or tickets != ticket_value:
                ticket_value, ticket_reads = tickets, 1
                ticket_wait.reset()
                tickets_stable = False
            else:
                ticket_reads += 1
                tickets_stable = ticket_reads >= 2 and ticket_wait.reached()
            for name, audit in pending:
                before = audit.get('tickets_before')
                increased = tickets is not None and before is not None and tickets > before
                # A missing COLLECT may be an animation or a missed template.
                # Only stable ticket growth proves this row actually paid out.
                if tickets_stable and increased:
                    audit.update(pending=False, verified=True, tickets_after=tickets)
                    self._capture(record, f'{name}_ticket_received')
                    persist()
            if any(audit.get('pending') for audit in record['rows'].values()):
                settled.reset()
                continue
            if not settled.reached():
                continue
            if not tickets_stable:
                continue
            for name, mission, collect in ROWS:
                if name in record['rows'] or not (self._matches(mission) and self._matches(collect)):
                    continue
                if not self._earned(name):
                    record.update(status='failed', reason=f'{name}_mission_not_verified_earned')
                    return False
                # Persist before input; an interrupted/rejected click is never replayed.
                record['rows'][name] = dict(clicks=1, pending=True, verified=False, tickets_before=tickets)
                persist()
                self.device.click(collect)
                click_wait.reset()
                settled.reset()
                break
            else:
                record.update(status='observed', reason='available_ticket_claims_processed', tickets=tickets)
                self._capture(record, 'postclaims')
                return True
        record.update(status='failed', reason='ticket_collection_timeout')
        return False

    def _inspect_first_unlock(self, record, state, key, persist):
        if key in state:
            record['unlock'] = dict(state[key], reused=True)
            return
        previous = None
        settled = Timer(1, count=2).start()
        for _ in self.loop(skip_first=False, timeout=10):
            if not self._on_choice():
                continue
            tickets = self._tickets()
            if tickets != previous:
                previous = tickets
                settled.reset()
                continue
            if not settled.reached():
                continue
            record['tickets'] = tickets
            if tickets is None or tickets <= 0:
                record['unlock'] = dict(status='deferred', reason='no_verified_ticket')
                return
            if not (self._matches(BECKONING_FIRST_STORY) and self._matches(BECKONING_FIRST_LOCK)):
                record['unlock'] = dict(status='unsupported', reason='first_locked_card_unrecognized')
                return
            audit = dict(status='inspecting', card_clicks=1, tickets_before=tickets,
                         started=datetime.now().isoformat(timespec='seconds'))
            state[key] = audit
            record['unlock'] = audit
            persist()
            self.device.click(BECKONING_FIRST_LOCK)
            wait = Timer(3, count=4).start()
            for _ in self.loop(skip_first=False, timeout=10):
                if not wait.reached():
                    continue
                # No destination controls, including generic confirms, are touched.
                changed = not self._on_choice() or not self._matches(BECKONING_FIRST_LOCK)
                path = self._capture(record, 'first_unlock_observed')
                audit.update(status='observed' if changed and path else 'blocked',
                             reason='unlock_destination_captured' if changed else 'first_card_did_not_open',
                             diagnostic=path)
                persist()
                return
            audit.update(status='blocked', reason='first_unlock_timeout')
            persist()
            return
        record['unlock'] = dict(status='deferred', reason='ticket_count_unstable')

    def run(self, event, folder='./config/stars'):
        if event != EVENT or self.config.config_name != 'f2p':
            return dict(status='unsupported', diagnostics=[], story_complete=False)
        ensure_event_running(self)
        state = load_reward_observations(self.config.config_name, folder=folder)
        key = event + ':beckoning_ticket_collection'
        previous = state.get(key, {})
        record = dict(status='collecting', rows=previous.get('rows', {}), diagnostics=[],
                      invocations=previous.get('invocations', 0) + 1, returned_main=False, story_complete=False)
        state[key] = record

        def persist():
            save_reward_observations(self.config.config_name, state, folder=folder)

        persist()
        helper = EventCompletion(config=self.config, device=self.device)
        try:
            helper._inspect_event_side_story(record, entry=TEMPESTA_BECKONING_CHOICE, panel='beckoning_choice')
            if record.get('status') != 'observed':
                record.update(status='failed', reason='choice_page_unavailable')
            elif self._collect(record, persist):
                self._inspect_first_unlock(record, state, event + ':beckoning_first_unlock', persist)
            self._capture(record, 'ticket_step_finished')
        except Exception:
            record.update(status='failed', reason='beckoning_ticket_exception')
            raise
        finally:
            try:
                record['returned_main'] = helper._return_event_hub_main()
                if not record['returned_main']:
                    record['return_reason'] = 'beckoning_main_return_unrecognized'
            finally:
                persist()
        logger.attr('BeckoningTickets', record)
        return record
