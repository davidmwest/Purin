"""Bounded free-ticket milestones and the six observed Beckoning stories."""
from module.base.timer import Timer
from module.campaign.event_beckoning_assets import (
    BECKONING_MONTHLY_COLLECT, BECKONING_VICTORY_COLLECT, OCR_BECKONING_VICTORIES)
from module.campaign.event_beckoning_story_assets import (
    MONTHLY_MISSION, OCR_MONTHLY_MILESTONE, STORY_CARDS, STORY_UNLOCKED_TITLES,
    STORY_VIEWER_LOG, STORY_VIEWER_SKIP, VICTORY_MISSION)
from module.campaign.event_rewards import EventRewards
from module.exception import ScriptError


class BeckoningStoryContinuation:
    """Uses only the enclosing job's already verified event/account/navigation."""
    def __init__(self, owner, record, state, event, persist):
        self.owner, self.record, self.persist = owner, record, persist
        self.ledger = state.setdefault(event + ':beckoning_stories_v1', dict(cards={}, milestones={}))
        if (not isinstance(self.ledger, dict) or not isinstance(self.ledger.get('cards'), dict)
                or not isinstance(self.ledger.get('milestones'), dict)):
            raise ScriptError('Malformed Beckoning story audit')
        record['stories'] = self.ledger
        self.cards = self.ledger['cards']
        for name, audit in self.cards.items():
            if (name not in {card[0] for card in STORY_CARDS} or not isinstance(audit, dict)
                    or type(audit.get('clicks')) is not int or not 1 <= audit['clicks'] <= 2
                    or type(audit.get('handler_calls', 0)) is not int
                    or not 0 <= audit.get('handler_calls', 0) <= 30):
                raise ScriptError('Malformed Beckoning card budget')
        if len(self.ledger['milestones']) > 6:
            raise ScriptError('Malformed Beckoning milestone budget')
        for audit in self.ledger['milestones'].values():
            if not isinstance(audit, dict) or audit.get('clicks') != 1:
                raise ScriptError('Malformed Beckoning milestone audit')
        # The old one-shot observation positively captured the first viewer.
        # Preserve that action and resume it; do not spend a second ticket.
        observed = state.get(event + ':beckoning_first_unlock', {})
        if (not self.cards and observed.get('card_clicks') == 1
                and observed.get('reason') == 'unlock_destination_captured'):
            self.cards['1'] = dict(clicks=1, pending=True, complete=False, story_seen=False,
                                   tickets_before=observed.get('tickets_before'), handler_calls=0)

    def visible(self, button):
        # Card labels brighten after unlocking; require the captured shape in
        # its exact local region, within the enclosing exact Choice page.
        return button.match_luma(self.owner.device.image, offset=(3, 3), similarity=.85)

    def on_story(self):
        return self.visible(STORY_VIEWER_LOG) and self.visible(STORY_VIEWER_SKIP)

    def card_title_visible(self, name):
        title = STORY_CARDS[int(name) - 1][1]
        unlocked = STORY_UNLOCKED_TITLES.get(name)
        return self.visible(title) or (unlocked is not None and self.visible(unlocked))

    def fail(self, reason):
        self.record.update(status='failed', reason=reason, story_complete=False)
        self.owner._capture(self.record, reason)
        self.persist()
        return False

    def finish_story(self, name):
        """Use the existing story handler, with a durable per-card call cap."""
        owner, audit = self.owner, self.cards[name]
        settled = Timer(1, count=2).start()
        action_wait = Timer(1, count=2)
        frames = 0
        backup = owner.config.temporary(STORY_ALLOW_SKIP=True)
        try:
            for _ in owner.loop(skip_first=False, timeout=60):
                if owner._on_choice():
                    tickets = owner._tickets()
                    card = STORY_CARDS[int(name) - 1]
                    unlocked = self.card_title_visible(name) and not self.visible(card[2])
                    paid = (type(tickets) is int and type(audit.get('tickets_before')) is int
                            and (tickets < audit['tickets_before'] or audit.get('replay')))
                    if audit.get('story_seen') and unlocked and paid:
                        frames += 1
                        if frames >= 3 and settled.reached():
                            audit.update(pending=False, complete=True, tickets_after=tickets)
                            owner._capture(self.record, f'card_{name}_story_finished')
                            self.persist()
                            return True
                    else:
                        frames = 0
                        settled.reset()
                    continue
                frames = 0
                settled.reset()
                if self.on_story():
                    owner.device.stuck_record_clear()
                    audit['story_seen'] = True
                # Existing story_skip handles its own recognized skip popup and
                # story choices. It is never invoked without our pending card.
                if not audit.get('story_seen') or not action_wait.reached():
                    continue
                # Each fresh frame authorizes at most one action. A recognized
                # receipt belongs to our pending story, never an unrelated UI.
                dialog = EventRewards._reward_dialog(owner)
                if dialog is not None:
                    count = audit.get('receipts', 0)
                    if count >= 8:
                        return self.fail('story_receipt_budget_exhausted')
                    audit['receipts'] = count + 1
                    self.persist()
                    owner.device.click(dialog)
                    action_wait.reset()
                    continue
                if audit.get('handler_calls', 0) >= 30:
                    return self.fail('story_handler_budget_exhausted')
                audit['handler_calls'] = audit.get('handler_calls', 0) + 1
                self.persist()
                owner.story_skip()
                action_wait.reset()
            return self.fail('story_return_unverified')
        finally:
            backup.recover()

    def resume_viewer(self):
        if not self.on_story():
            return True
        pending = [name for name, data in self.cards.items() if data.get('pending')]
        if len(pending) != 1:
            return self.fail('story_viewer_has_no_unique_pending_card')
        return self.finish_story(pending[0])

    def observe_unlock_payment(self):
        """Preserve spent-ticket proof before new earned tickets raise it."""
        pending = [(name, audit) for name, audit in self.cards.items()
                   if audit.get('pending') and audit.get('clicks') == 1 and not audit.get('unlock_paid')]
        if not pending:
            return True
        previous, frames = None, 0
        settled = Timer(1, count=2).start()
        for _ in self.owner.loop(skip_first=False, timeout=10):
            if not self.owner._on_choice():
                previous, frames = None, 0
                settled.reset()
                continue
            tickets = self.owner._tickets()
            proof = tuple(name for name, audit in pending
                          if type(tickets) is int and type(audit.get('tickets_before')) is int
                          and tickets < audit['tickets_before']
                          and self.card_title_visible(name)
                          and not self.visible(STORY_CARDS[int(name) - 1][2]))
            observation = tickets, proof
            if observation != previous or type(tickets) is not int:
                previous, frames = observation, 0
                settled.reset()
            frames += 1
            if type(tickets) is int and frames >= 3 and settled.reached():
                for name in proof:
                    audit = self.cards[name]
                    audit.update(unlock_paid=True, unlock_tickets=tickets)
                    # A prior invocation may have seen the viewer but failed to
                    # recognize its unlocked return. The same stable paid,
                    # unlocked evidence finishes that audit without a replay.
                    if audit.get('story_seen'):
                        audit.update(pending=False, complete=True, tickets_after=tickets)
                        self.owner._capture(self.record, f'card_{name}_story_finished')
                if proof:
                    self.persist()
                return True
        return self.fail('pending_unlock_payment_unreadable')

    def collect_milestones(self):
        """The actual postclaim screen proves missions advance to 2/2 and 30."""
        owner = self.owner
        settled = Timer(1, count=2).start()
        wait = Timer(1, count=2)
        previous, frames = None, 0
        rows = self.ledger['milestones']
        for _ in owner.loop(skip_first=False, timeout=75):
            pending = [row for row in rows.values() if row.get('pending')]
            if not owner._on_choice():
                previous, frames = None, 0
                settled.reset()
                dialog = EventRewards._reward_dialog(owner) if pending else None
                if dialog is not None and wait.reached():
                    audit = pending[0]
                    if audit.get('receipts', 0) >= 8:
                        return self.fail('milestone_receipt_limit')
                    audit['receipts'] = audit.get('receipts', 0) + 1
                    self.persist()
                    owner.device.click(dialog)
                    wait.reset()
                continue
            owner.device.stuck_record_clear()
            tickets = owner._tickets()
            if tickets is None:
                previous, frames = None, 0
                settled.reset()
                continue
            eligible = []
            for name, label, collect, counter in (
                    ('monthly', MONTHLY_MISSION, BECKONING_MONTHLY_COLLECT, OCR_MONTHLY_MILESTONE),
                    ('victories', VICTORY_MISSION, BECKONING_VICTORY_COLLECT, OCR_BECKONING_VICTORIES)):
                if not (self.visible(label) and owner._matches(collect)):
                    continue
                current, remaining, total = counter.ocr(owner.device.image)
                if type(total) is int and 0 < total <= 1000 and current == total and remaining == 0:
                    key = f'{name}:{total}'
                    # The original collector already owns these two claims.
                    if total == (1 if name == 'monthly' else 15) and name in self.record['rows']:
                        continue
                    if key not in rows:
                        eligible.append((key, collect))
            observation = tickets, tuple(key for key, _ in eligible)
            if observation != previous:
                previous, frames = observation, 0
                settled.reset()
            frames += 1
            if frames < 3 or not settled.reached():
                continue
            for audit in pending:
                if tickets > audit['tickets_before']:
                    audit.update(pending=False, verified=True, tickets_after=tickets)
                    self.persist()
            if any(row.get('pending') for row in rows.values()):
                continue
            if not eligible or tickets >= 6:
                self.record['tickets'] = tickets
                return True
            if len(rows) >= 6:
                return self.fail('milestone_claim_budget_exhausted')
            key, button = eligible[0]
            rows[key] = dict(clicks=1, pending=True, verified=False, tickets_before=tickets)
            self.persist()
            owner.device.click(button)
            previous, frames = None, 0
            settled.reset()
            wait.reset()
        return self.fail('milestone_claim_unverified')

    def run_cards(self):
        owner = self.owner
        # At most six unlocks plus one audited replay of an interrupted viewer.
        for _ in range(7):
            previous, frames = None, 0
            settled = Timer(1, count=2).start()
            candidate = None
            observed = False
            for _ in owner.loop(skip_first=False, timeout=10):
                if not owner._on_choice():
                    previous, frames = None, 0
                    settled.reset()
                    continue
                owner.device.stuck_record_clear()
                tickets = owner._tickets()
                if type(tickets) is not int:
                    previous, frames = None, 0
                    settled.reset()
                    continue
                available = []
                for name, title, lock in STORY_CARDS:
                    audit = self.cards.get(name, {})
                    if audit.get('complete') or not self.card_title_visible(name):
                        continue
                    locked = self.visible(lock)
                    if locked and type(tickets) is int and tickets > 0 and not audit:
                        available.append((name, lock, False))
                    elif (not locked and audit.get('pending') and audit.get('clicks') == 1
                          and type(audit.get('tickets_before')) is int
                          and (audit.get('unlock_paid') or tickets < audit['tickets_before'])):
                        available.append((name, title, True))
                observation = tickets, tuple((name, replay) for name, _, replay in available)
                if observation != previous:
                    previous, frames = observation, 0
                    settled.reset()
                frames += 1
                if frames >= 3 and settled.reached():
                    observed = True
                    candidate = available[0] if available else None
                    break
            if not observed:
                return self.fail('story_cards_not_stably_recognized')
            if candidate is None:
                complete = all(self.cards.get(name, {}).get('complete') for name, _, _ in STORY_CARDS)
                self.record.update(status='complete' if complete else 'observed', story_complete=complete,
                                   reason='all_six_stories_verified' if complete else 'remaining_stories_require_evidence_or_tickets')
                owner._capture(self.record, 'stories_finished' if complete else 'stories_pending')
                self.persist()
                return True
            name, button, replay = candidate
            audit = self.cards.setdefault(name, dict(clicks=0, pending=True, complete=False, handler_calls=0))
            audit.update(clicks=audit['clicks'] + 1, tickets_before=tickets, replay=replay)
            self.persist()
            owner.device.click(button)
            if not self.finish_story(name):
                return False
        return self.fail('story_card_budget_exhausted')
