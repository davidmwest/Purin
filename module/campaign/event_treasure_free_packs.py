"""Claim the two observed FREE Marks of Treasure packs; never paid cards."""
from datetime import datetime

from module.base.timer import Timer
from module.campaign.event_completion import load_reward_observations, save_reward_observations
from module.campaign.event_main_notifications import DAY7
from module.campaign.event_rewards import EventRewards
from module.campaign.event_treasure_free_pack_assets import (
    OCR_TREASURE_CONFIRM_PRICE, TREASURE_CARDS, TREASURE_CONFIRM_BUY, TREASURE_CONFIRM_WITNESSES,
    TREASURE_DATES, TREASURE_HEADER, TREASURE_HOME)
from module.combat.assets import GET_ITEMS_1, GET_ITEMS_2, GET_ITEMS_3
from module.eventstory.eventstory import ensure_event_page, ensure_event_running, save_event_diagnostic
from module.exception import ScriptError
from module.logger import logger
from module.ui.page import page_main

EVENT = 'event_20261022_cn'
KEY = EVENT + ':treasure_free_packs_v1'


class EventTreasureFreePacks(EventRewards):
    @staticmethod
    def _validate(record):
        if 'externally_completed' in record:
            external = record['externally_completed']
            if (not isinstance(external, dict) or set(external) != {'reason', 'timestamp'}
                    or external['reason'] != 'user_reported_manual_collection'
                    or type(external['timestamp']) is not str):
                raise ScriptError('Invalid externally completed free treasure metadata')
            try:
                timestamp = datetime.fromisoformat(external['timestamp'])
            except ValueError:
                raise ScriptError('Invalid externally completed free treasure timestamp') from None
            if timestamp.isoformat(timespec='seconds') != external['timestamp']:
                raise ScriptError('Invalid externally completed free treasure timestamp')
        cards = record.get('cards')
        if not isinstance(cards, dict) or set(cards) != set(TREASURE_CARDS):
            raise ScriptError('Invalid free treasure claim journal')
        for row in cards.values():
            if (not isinstance(row, dict) or type(row.get('claim_clicks')) is not int
                    or row['claim_clicks'] not in (0, 1)
                    or type(row.get('confirm_clicks', 0)) is not int or row.get('confirm_clicks', 0) not in (0, 1)
                    or any(type(row.get(field)) is not bool for field in ('pending', 'receipt_seen', 'verified'))):
                raise ScriptError('Invalid free treasure claim reservation')
            if (row['pending'] and (row['claim_clicks'] != 1 or row['verified'])
                    or row['verified'] and (row['claim_clicks'] != 1 or not row['receipt_seen'])
                    or row['receipt_seen'] and row['claim_clicks'] != 1
                    or row.get('confirm_clicks', 0) and row['claim_clicks'] != 1
                    or row['claim_clicks'] == 1 and not (row['pending'] or row['verified'])):
                raise ScriptError('Inconsistent free treasure claim evidence')
        for field, maximum in (('entry_clicks', 2), ('reward_dialogs', 12), ('invocations', None)):
            value = record.get(field)
            if type(value) is not int or value < 0 or (maximum is not None and value > maximum):
                raise ScriptError('Invalid free treasure navigation journal')

    def _reward_dialog(self):
        # These packs contain items/oil. No generic confirmation, ship/skin
        # acquisition, shop control or unknown modal belongs to this collector.
        for button in (GET_ITEMS_1, GET_ITEMS_2, GET_ITEMS_3):
            if self.appear(button, offset=5):
                return GET_ITEMS_1
        return None

    def _exact(self, button):
        button.clear_offset()
        return self.match_template_color(button, offset=(3, 3), similarity=.9)

    def _treasure_page(self):
        return all(self._exact(button) for button in (TREASURE_HEADER, TREASURE_DATES, TREASURE_HOME))

    def _card_base(self, day):
        free, name, _ = TREASURE_CARDS[day]
        return self._treasure_page() and self._exact(free) and self._exact(name)

    def _claimable(self, day):
        return self._card_base(day) and self._exact(TREASURE_CARDS[day][2])

    def _free_confirmation(self):
        # This is the observed FREE blue crate with the captured books/oil,
        # exact name, zero-price strip and Buy button. Generic shop confirmation
        # handlers must never authorize it or any paid pack on our behalf.
        return (all(self._exact(button) for button in TREASURE_CONFIRM_WITNESSES)
                and str(OCR_TREASURE_CONFIRM_PRICE.ocr(self.device.image)).strip() == '0')

    def _capture(self, record, suffix):
        path = save_event_diagnostic(self.device, f'alas_treasure_free_{suffix}')
        if path:
            record['diagnostics'].append(path)
        return path

    def _enter(self, record, persist):
        # Already-open panels and owned pending receipts bypass ordinary routing.
        if self._treasure_page() or any(row.get('pending') for row in record['cards'].values()):
            return True
        if all(row.get('verified') for row in record['cards'].values()):
            return True
        if not ensure_event_page(self, page_main, timeout=20, max_clicks=6):
            return False
        settled = Timer(1, count=2).start()
        clicked = False
        for _ in self.loop(skip_first=False, timeout=15):
            if self._treasure_page():
                return True
            if clicked:
                continue
            if not (self.ui_page_appear(page_main) and self._exact(DAY7)):
                settled.reset()
                continue
            if not settled.reached():
                continue
            if record['entry_clicks'] >= 2:
                return False
            record['entry_clicks'] += 1
            persist()
            self.device.click(DAY7)
            clicked = True
        return False

    def _collect_free(self, record, persist):
        settled = Timer(1, count=2).start()
        confirm_settled = Timer(1, count=2).start()
        click_wait = Timer(2, count=3)
        stable_key, stable_frames = None, 0
        confirm_day, confirm_frames = None, 0
        for _ in self.loop(skip_first=False, timeout=45):
            pending = next((day for day, row in record['cards'].items() if row.get('pending')), None)
            # Receipts take priority over a panel visible behind their overlay.
            dialog = self._reward_dialog() if pending else None
            if dialog is not None:
                confirm_day, confirm_frames = None, 0
                settled.reset()
                stable_frames = 0
                if not click_wait.reached():
                    continue
                if record['reward_dialogs'] >= 12:
                    record.update(status='blocked', reason='free_pack_receipt_limit')
                    return
                row = record['cards'][pending]
                row['receipt_seen'] = True
                record['reward_dialogs'] += 1
                persist()
                self.device.click(dialog)
                click_wait.reset()
                continue
            if pending and self._free_confirmation():
                row = record['cards'][pending]
                if row.get('confirm_clicks', 0):
                    # A previously sent Buy cannot be repeated, even when its
                    # receipt has not arrived or the modal remains visible.
                    stable_key, stable_frames = None, 0
                    settled.reset()
                    continue
                # The observed name marquee exposes the exact full witness for
                # less than a second per cycle. Count fresh, independently
                # matching frames across animation gaps, with elapsed settling
                # time, and still require *all* witnesses/raw zero right now.
                # These local observations never authorize a nonmatching frame
                # and never survive a receipt, different card or invocation.
                if pending != confirm_day:
                    confirm_day, confirm_frames = pending, 0
                    confirm_settled.reset()
                confirm_frames += 1
                if confirm_frames >= 3 and confirm_settled.reached() and click_wait.reached():
                    row['confirm_clicks'] = 1
                    persist()
                    self.device.click(TREASURE_CONFIRM_BUY)
                    confirm_day, confirm_frames = None, 0
                    click_wait.reset()
                    stable_key, stable_frames = None, 0
                    settled.reset()
                continue
            if not self._treasure_page():
                stable_key, stable_frames = None, 0
                settled.reset()
                continue
            self.device.stuck_record_clear()
            if pending:
                row = record['cards'][pending]
                # An absent button alone cannot prove a free pack was received.
                key = (pending, 'received') if (row.get('receipt_seen') and self._card_base(pending)
                                               and not self._exact(TREASURE_CARDS[pending][2])) else None
            else:
                if all(row.get('verified') for row in record['cards'].values()):
                    record.update(status='complete', reason='both_observed_free_packs_verified')
                    return
                day = next((day for day, row in record['cards'].items()
                            if not row.get('verified') and not row.get('claim_clicks') and self._claimable(day)), None)
                key = (day, 'claimable') if day else None
            if key is None or key != stable_key:
                stable_key, stable_frames = key, 1
                settled.reset()
                continue
            stable_frames += 1
            if stable_frames < 3 or not settled.reached() or not click_wait.reached():
                continue
            day, action = key
            row = record['cards'][day]
            if action == 'received':
                row.update(pending=False, verified=True)
                persist()
            else:
                # Each observed day has one irreversible attempt for this event.
                row.update(claim_clicks=1, pending=True, receipt_seen=False)
                persist()
                self.device.click(TREASURE_CARDS[day][2])
                click_wait.reset()
            stable_key, stable_frames = None, 0
            settled.reset()
        record.update(status='blocked', reason='free_pack_result_unverified')

    def _return_main(self, record, persist):
        if any(row.get('pending') for row in record['cards'].values()):
            return False
        settled = Timer(1, count=2).start()
        main_frames = 0
        for _ in self.loop(skip_first=False, timeout=12):
            if self.ui_page_appear(page_main):
                main_frames += 1
                if main_frames >= 3 and settled.reached():
                    return True
                continue
            main_frames = 0
            if not self._treasure_page():
                # Unknown confirmation/receipt: preserve it for observation.
                return False
            self.device.stuck_record_clear()
            if record['home_clicks'] or not settled.reached():
                continue
            record['home_clicks'] = 1
            persist()
            self.device.click(TREASURE_HOME)
            settled.reset()
        return False

    def run(self, event, folder='./config/stars'):
        if event != EVENT or self.config.config_name != 'alas':
            return dict(status='unsupported', diagnostics=[], returned_main=True)
        state = load_reward_observations('alas', folder=folder)
        previous = state.get(KEY)
        record = previous if previous is not None else dict(cards={day: dict(claim_clicks=0, confirm_clicks=0, pending=False, receipt_seen=False,
                                                   verified=False) for day in TREASURE_CARDS},
                                  entry_clicks=0, reward_dialogs=0, invocations=0)
        self._validate(record)
        if 'externally_completed' in record:
            # This explicit human report ends this collector's responsibility.
            # It is not evidence of our receipt or an authorization to resume a
            # pending click. Preserve every original card/counter fact as-is.
            record.update(status='complete', reason='user_reported_manual_collection',
                          inactive=True, returned_main=True)
            state[KEY] = record
            save_reward_observations('alas', state, folder=folder)
            return record
        ensure_event_running(self)
        for row in record['cards'].values():
            row.setdefault('confirm_clicks', 0)
        # Persisted claim reservations survive restart; only read-only settlement
        # or owned receipt dismissal can resume a pending day. Never reset it.
        record.update(status='collecting', diagnostics=[], returned_main=False, home_clicks=0, inactive=False,
                      invocations=record['invocations'] + 1,
                      started=datetime.now().isoformat(timespec='seconds'))
        state[KEY] = record

        def persist():
            save_reward_observations('alas', state, folder=folder)

        persist()
        try:
            self.device.screenshot()
            if (all(row.get('verified') for row in record['cards'].values())
                    and not self._treasure_page()):
                # Finished collectors must not intercept another task's receipt
                # before the existing ordinary mission receipt recovery runs.
                record.update(status='complete', reason='previous_free_pack_receipts_verified',
                              inactive=True, returned_main=True)
                persist()
                return record
            elif self._enter(record, persist):
                self._collect_free(record, persist)
            else:
                record.update(status='blocked', reason='free_pack_page_unrecognized')
            self._capture(record, record['status'])
        except Exception:
            record.update(status='blocked', reason='free_pack_collection_exception')
            self._capture(record, 'exception')
            raise
        finally:
            try:
                if not record.get('inactive'):
                    record['returned_main'] = self._return_main(record, persist)
            finally:
                persist()
        logger.attr('EventTreasureFreePacks', record)
        return record

    def resume(self, event, folder='./config/stars'):
        """Recover only this open panel or an owned pending item receipt.

        None leaves unrelated UI to existing startup receipt recovery. This does
        not create a first journal or navigate from another task's receipt.
        """
        if event != EVENT or self.config.config_name != 'alas':
            return None
        previous = load_reward_observations('alas', folder=folder).get(KEY)
        if previous is not None:
            self._validate(previous)
            if 'externally_completed' in previous:
                # Do not intercept another collector's receipt or navigate on
                # a report of manual completion. The ordinary run is inactive.
                return None
        ensure_event_running(self)
        self.device.screenshot()
        pending = previous is not None and any(row['pending'] for row in previous['cards'].values())
        if self._treasure_page() or pending:
            return self.run(event, folder=folder)
        return None
