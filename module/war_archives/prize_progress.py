"""Read catalog-bound guaranteed prizes and claim only a verified ready reward.

The ordinary Sortie action is never authorized. A separate durable journal owns
one Accept across scheduler requests; unknown results cannot grant another click.
"""
import json
import os
import re
from datetime import datetime
from uuid import uuid4

import numpy as np

from module.base.button import Button
from module.base.timer import Timer
from module.base.utils import save_image
from module.exception import ScriptError
from module.combat.assets import GET_ITEMS_1, GET_ITEMS_2, GET_ITEMS_3, GET_SHIP
from module.handler.assets import GET_ITEMS_SHIP_1
from filelock import FileLock, Timeout
from module.ocr.ocr import Ocr
from module.war_archives.catalog import get_archive
from module.war_archives.progress import _save_record
from module.war_archives.prize_assets import (
    PRIZE_PANEL_INFO, PRIZE_PANEL_CLOSE, PRIZE_PANEL_FRAME, PRIZE_PANEL_CAPTION,
    PRIZE_PANEL_NAME, PRIZE_PANEL_ACTION_TEXT, PRIZE_PANEL_ACCEPT)


VERSION = 1
VISITORS = 'war_archives_20190321_en'
FILE = './assets/en/war_archives/ARCHIVE_PRIZE_VISITORS_20261023.png'


def _button(area, name, button=()):
    return Button(area=area, color=(), button=button or area, file=FILE, name=name)


PRIZE_ARCHIVE_TITLE = _button((215, 74, 515, 98), 'PRIZE_ARCHIVE_VISITORS')
PRIZE_PROGRESS_FRAME = _button((1098, 670, 1180, 681), 'PRIZE_PROGRESS_FRAME')
PRIZE_ZUIKAKU = _button((1183, 623, 1237, 676), 'PRIZE_ZUIKAKU',
                       (1104, 625, 1235, 675))
PRIZE_CLAIMED_BACKGROUND = Button(
    area=(1100, 625, 1237, 680), color=(), button=(1100, 625, 1237, 680),
    file='./assets/en/war_archives/ARCHIVE_PRIZE_VISITORS_CLAIMED_20261023.png',
    name='PRIZE_VISITORS_CLAIMED_BACKGROUND')
PRIZE_CLAIMED_TITLE = Button(
    area=(215, 74, 515, 98), color=(), button=(215, 74, 515, 98),
    file='./assets/en/war_archives/ARCHIVE_PRIZE_VISITORS_CLAIMED_20261023.png',
    name='PRIZE_VISITORS_CLAIMED_TITLE')
PRIZE_COUNT = Ocr((1109, 634, 1171, 663), letter=(255, 255, 255),
                  threshold=96, alphabet='0123456789/', name='ARCHIVE_PRIZE_COUNT')


def parse_prize_count(text, total=60):
    """An unreadable/slashless result never becomes zero or a completed prize."""
    match = re.fullmatch(r'(0|[1-9]\d?)/(60)', str(text).strip())
    if match is None or total != 60 or int(match[1]) > total:
        return None
    return int(match[1])


class ArchivePrizeProgress:
    def __init__(self, observer):
        self.owner = observer
        self.config = observer.config
        self.device = observer.device
        self.definition = None
        self.prize = None

    def _exact(self, button):
        if not button.match(self.device.image, offset=(2, 2), similarity=.90):
            return False
        # Correlation alone also matches a dimmed stage beneath a popup.
        left, top, right, bottom = button.area
        actual = self.device.image[top:bottom, left:right]
        return bool(np.max(np.abs(actual.mean(axis=(0, 1))
                                  - button.image.mean(axis=(0, 1)))) <= 15)

    def _source(self):
        if self.definition is not None and self.definition.id != VISITORS:
            return self._generic_source()
        return (self.owner._archive_stage_page()
                and all(self._exact(button) for button in
                        (PRIZE_ARCHIVE_TITLE, PRIZE_PROGRESS_FRAME, PRIZE_ZUIKAKU)))

    def _count(self):
        return parse_prize_count(PRIZE_COUNT.ocr(self.device.image)) if self._source() else None

    def _claimed_source(self):
        if self.definition is not None and self.definition.id != VISITORS:
            return False
        # ChapterProxy.register initializes every catalog drop_gain as unreceived.
        # ChapterAwardInfo hides this sole reward only after receive=true. Exact
        # background + title + stage/no-overlay proof excludes loading/other maps.
        return (self.owner._archive_stage_page()
                and self._exact(PRIZE_CLAIMED_TITLE)
                and self._exact(PRIZE_CLAIMED_BACKGROUND)
                and not self._exact(PRIZE_ZUIKAKU)
                and not self._exact(PRIZE_PROGRESS_FRAME))

    def _generic_source(self):
        # The EN client displays the first unreceived drop_gain. Only a sole
        # catalog prize can be bound without identifying a moving portrait.
        definition = self.definition
        return (definition is not None and definition.prizes == (self.prize,)
                and self._stage_identity() and self._exact(PRIZE_PROGRESS_FRAME))

    def _stage_identity(self):
        definition = self.definition
        if definition is None or not self.owner._archive_identity(definition.id):
            return False
        screen = self.owner._read_stage_screen(definition.id)
        return (isinstance(screen, dict) and screen.get('event') == definition.id
                and screen.get('mode') in ('normal', 'hard')
                and bool(screen.get('stages'))
                and type(screen.get('raw_keys')) is int
                and 0 <= screen['raw_keys'] <= 300)

    def _panel_frame(self):
        return all(self._exact(button) for button in
                   (PRIZE_PANEL_INFO, PRIZE_PANEL_CLOSE, PRIZE_PANEL_FRAME))

    @staticmethod
    def _name_key(text):
        return ''.join(character for character in str(text).casefold() if character.isalnum())

    def _panel(self, prize):
        """Independent raw name, chapter-code and complete N/60 proof."""
        if not self._panel_frame():
            return None
        caption = PRIZE_PANEL_CAPTION
        caption.clear_offset()
        if not caption.match(self.device.image, offset=(40, 2), similarity=.90):
            return None
        delta = caption.button[0] - caption.area[0]
        if not -8 <= delta <= 40:
            return None
        stage = Ocr((355, 492, 390 + delta, 532), threshold=96,
                    name='ARCHIVE_PRIZE_STAGE').ocr(self.device.image)
        count = Ocr((578 + delta, 492, 733, 532), threshold=96,
                    alphabet='0123456789/', name='ARCHIVE_PRIZE_PANEL_COUNT').ocr(self.device.image)
        name = PRIZE_PANEL_NAME.ocr(self.device.image)
        if (self._name_key(stage) != self._name_key(prize.stage)
                or self._name_key(name) != self._name_key(prize.name)):
            return None
        value = parse_prize_count(count, prize.clears)
        if value is None:
            return None
        action = self._name_key(PRIZE_PANEL_ACTION_TEXT.ocr(self.device.image))
        expected = 'accept' if value == prize.clears else 'sortie'
        if action != expected:
            return None
        return dict(count=value, action=action, name=name, stage=stage,
                    proof_kind='catalog_bound_reward_panel')

    def _receipt(self):
        for button in (GET_ITEMS_1, GET_ITEMS_2, GET_ITEMS_3, GET_ITEMS_SHIP_1, GET_SHIP):
            if button.match(self.device.image, offset=(5, 5), similarity=.90):
                return GET_ITEMS_1 if button in (GET_ITEMS_2, GET_ITEMS_3) else button
        return None

    @staticmethod
    def _valid_record(record, account, archive, prize, request_id):
        return (isinstance(record, dict) and record.get('version') == VERSION
                and record.get('account') == account and record.get('archive') == archive
                and record.get('prize_id') == prize.id and record.get('request_id') == request_id
                and type(record.get('open_clicks')) is int and record['open_clicks'] in (0, 1)
                and type(record.get('cancel_clicks')) is int and 0 <= record['cancel_clicks'] <= 2
                and type(record.get('claim_clicks')) is int and record['claim_clicks'] == 0
                and record.get('combat_attempted') is False
                and isinstance(record.get('diagnostics'), list))

    def observe(self, archive, prize, folder='./log/war_archives', request_id=None):
        """Read a stable count, open once, capture, and try exact Cancel only.

        An unknown panel deliberately remains open for a fixture-backed followup.
        A repeated request returns its durable record without any input, including
        after a crash between the open reservation and the actual click.
        """
        definition = get_archive(archive)
        self.definition, self.prize = definition, prize
        account = self.config.config_name
        if (account not in {'alas', 'f2p'} or definition is None
                or prize not in definition.prizes):
            raise ScriptError('Invalid archive prize observation identity')
        request_id = request_id or f'{datetime.now():%Y%m%dT%H%M%S%f}-{uuid4().hex[:12]}'
        if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', request_id):
            raise ScriptError('Invalid archive prize observation request')
        directory = os.path.join(folder, account, request_id)
        path = os.path.join(directory, f'prize-{prize.id}.json')
        if os.path.exists(path):
            try:
                with open(path, encoding='utf-8') as file:
                    old = json.load(file)
            except (OSError, ValueError) as exc:
                raise ScriptError('Unreadable archive prize observation') from exc
            if not self._valid_record(old, account, definition.id, prize, request_id):
                raise ScriptError('Invalid archive prize observation journal')
            return dict(old, reused=True)
        record = dict(version=VERSION, account=account, archive=definition.id,
                      prize_id=prize.id, prize_name=prize.name, stage=prize.stage,
                      mode=prize.mode, request_id=request_id, status='blocked',
                      reason='prize_source_unrecognized', verified=False,
                      count=None, total=prize.clears, claimed=None, open_clicks=0,
                      cancel_clicks=0, claim_clicks=0, combat_attempted=False,
                      returned_stage=False, diagnostics=[], journal=os.path.abspath(path))
        persist = lambda: _save_record(path, record)

        def capture(name):
            output = os.path.abspath(os.path.join(directory, name + '.png'))
            save_image(self.device.image, output)
            record['diagnostics'].append(output)
            persist()

        persist()
        if not definition.supported or definition.prizes != (prize,):
            record['reason'] = 'prize_source_unsupported'
            persist()
            return record
        try:
            previous = None
            settled = Timer(1, count=2).start()
            absent = Timer(1, count=2).start()
            absent_frames = 0
            for _ in self.owner.loop(skip_first=False, timeout=8):
                if self._claimed_source():
                    absent_frames += 1
                    if absent_frames >= 3 and absent.reached():
                        record.update(status='observed', reason='prize_already_claimed',
                                      proof_kind='source_received_hidden_button',
                                      verified=True, claimed=True, returned_stage=True)
                        capture('prize_already_claimed')
                        return record
                else:
                    absent_frames = 0
                    absent.reset()
                count = self._count()
                if count is None or count != previous:
                    previous = count
                    settled.reset()
                    continue
                if not settled.reached():
                    continue
                record.update(verified=True, count=count, claimed=False)
                capture('prize_source')
                # Fresh source/count equality immediately precedes the only open.
                if self._count() != count:
                    record['reason'] = 'prize_source_changed'
                    return record
                record['open_clicks'] = 1
                persist()
                self.device.click(PRIZE_ZUIKAKU)
                break
            else:
                capture('prize_source_unrecognized')
                return record

            # Observe never invokes either action, even when the panel is ready.
            changed = Timer(1, count=2).start()
            for _ in self.owner.loop(skip_first=False, timeout=8):
                if self._source():
                    changed.reset()
                    continue
                if changed.reached():
                    panel = self._panel(prize)
                    if panel is not None and panel['count'] == record['count']:
                        record.update(panel=panel, proof_kind=panel['proof_kind'])
                    record.update(status='observed', reason='prize_panel_captured_unverified')
                    capture('prize_panel')
                    break
            else:
                record['reason'] = 'prize_panel_not_observed'
                capture('prize_open_blocked')
                return record

            # The captured single-item panel has its own red X. Never use a
            # generic confirmation/cancel matcher over its Sortie/Accept control.
            cancel_settle = Timer(2, count=2).start()
            returned = Timer(1, count=2).start()
            for _ in self.owner.loop(skip_first=False, timeout=8):
                if self._source():
                    if returned.reached():
                        record.update(returned_stage=True, reason='prize_panel_captured')
                        capture('prize_returned')
                        return record
                    continue
                returned.reset()
                if (record['cancel_clicks'] < 2 and cancel_settle.reached()
                        and self._panel_frame()):
                    record['cancel_clicks'] += 1
                    persist()
                    self.device.click(PRIZE_PANEL_CLOSE)
                    cancel_settle.reset()
            record['reason'] = 'prize_panel_close_unrecognized'
            capture('prize_close_blocked')
            return record
        except Exception:
            record.update(status='blocked', reason='exception')
            capture('prize_exception')
            raise
        finally:
            record['finished'] = datetime.now().isoformat(timespec='seconds')
            persist()

    @staticmethod
    def _valid_claim(record, account, definition, prize):
        return (isinstance(record, dict) and record.get('version') == VERSION
                and record.get('account') == account and record.get('archive') == definition.id
                and record.get('prize_id') == prize.id
                and record.get('prize_name') == prize.name
                and record.get('stage') == prize.stage and record.get('mode') == prize.mode
                and type(record.get('total')) is int and record['total'] == prize.clears
                and (record.get('count') is None or
                     (type(record.get('count')) is int and 0 <= record['count'] <= prize.clears))
                and all(type(record.get(key)) is int and record[key] in (0, 1)
                        for key in ('open_clicks', 'claim_clicks'))
                and type(record.get('cancel_clicks')) is int and 0 <= record['cancel_clicks'] <= 2
                and type(record.get('reward_dialogs')) is int and 0 <= record['reward_dialogs'] <= 24
                and type(record.get('pending')) is bool and type(record.get('receipt_seen')) is bool
                and type(record.get('claimed')) is bool
                and (not record['claim_clicks'] or record['open_clicks'] == 1)
                and (not record['claim_clicks'] or record.get('count') == prize.clears)
                and (not record['claim_clicks'] or record['pending'] or record['claimed'])
                and (not record['pending'] or record['claim_clicks'] == 1)
                and (not record['receipt_seen'] or record['claim_clicks'] == 1)
                and (not record['claimed'] or (record['receipt_seen'] and not record['pending']))
                and record.get('combat_attempted') is False
                and isinstance(record.get('diagnostics'), list))

    def claim(self, archive, prize, folder='./log/war_archives', request_id=None):
        """One durable Accept, independently read from the ready reward panel.

        A pending/uncertain claim never clicks Accept again, even under another
        scheduler request. Only its known reward receipt and fresh returned
        archive stage can finish that reservation. No generic popup is handled.
        """
        definition = get_archive(archive)
        account = self.config.config_name
        if (account not in {'alas', 'f2p'} or definition is None or not definition.supported
                or definition.prizes != (prize,)):
            raise ScriptError('Invalid archive prize claim identity')
        self.definition, self.prize = definition, prize
        directory = os.path.join(folder, account, 'prize-claims')
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, f'{definition.id}-{prize.id}.json')
        try:
            with FileLock(path + '.lock', timeout=0):
                return self._claim_locked(definition, prize, path)
        except Timeout as exc:
            raise ScriptError('Archive prize claim is already running') from exc

    def resume(self, archive, prize, folder='./log/war_archives'):
        """Settle an existing Accept reservation without ever starting a claim."""
        definition = get_archive(archive)
        account = self.config.config_name
        if (account not in {'alas', 'f2p'} or definition is None or not definition.supported
                or definition.prizes != (prize,)):
            raise ScriptError('Invalid archive prize claim identity')
        path = os.path.join(folder, account, 'prize-claims',
                            f'{definition.id}-{prize.id}.json')
        if not os.path.isfile(path):
            return None
        self.definition, self.prize = definition, prize
        try:
            with FileLock(path + '.lock', timeout=0):
                return self._claim_locked(definition, prize, path, resume_only=True)
        except Timeout as exc:
            raise ScriptError('Archive prize claim is already running') from exc

    def _claim_locked(self, definition, prize, path, resume_only=False):
        account = self.config.config_name
        try:
            with open(path, encoding='utf-8') as stream:
                record = json.load(stream)
        except FileNotFoundError:
            if resume_only:
                return None
            record = dict(version=VERSION, account=account, archive=definition.id,
                          prize_id=prize.id, prize_name=prize.name, stage=prize.stage,
                          mode=prize.mode, status='blocked', reason='prize_claim_unverified',
                          count=None, total=prize.clears, claimed=False, verified=False,
                          returned_stage=False, open_clicks=0, claim_clicks=0, cancel_clicks=0,
                          pending=False, receipt_seen=False, reward_dialogs=0,
                          combat_attempted=False, diagnostics=[], journal=os.path.abspath(path))
        except (OSError, ValueError) as exc:
            raise ScriptError('Unreadable archive prize claim journal') from exc
        if not self._valid_claim(record, account, definition, prize):
            raise ScriptError('Invalid archive prize claim journal')
        if resume_only and not record['claim_clicks']:
            return None
        persist = lambda: _save_record(path, record)

        def capture(name):
            output = os.path.abspath(os.path.join(os.path.dirname(path),
                                    f'{definition.id}-{prize.id}-{name}.png'))
            save_image(self.device.image, output)
            record['diagnostics'].append(output)
            persist()

        # Previous success does not authorize input or substitute for a fresh
        # stage return. Keep the receipt/counters, but verify the current screen.
        record.update(verified=False, returned_stage=False)
        persist()
        stable = Timer(1, count=2).start()
        wait = Timer(2, count=2).start()
        previous = None
        frames = 0
        try:
            for _ in self.owner.loop(skip_first=False, timeout=35):
                receipt = self._receipt() if record['pending'] else None
                if receipt is not None:
                    if record['reward_dialogs'] >= 24:
                        record['reason'] = 'prize_reward_dialog_limit'
                        break
                    if wait.reached():
                        record['receipt_seen'] = True
                        record['reward_dialogs'] += 1
                        persist()
                        self.device.click(receipt)
                        wait.reset()
                    stable.reset()
                    frames = 0
                    continue

                # An owned receipt + disappearance of the source's only reward
                # is stronger than possession of the reward ship/item. The 60
                # count here was actually read and saved before Accept.
                received = (record['receipt_seen'] and record.get('count') == prize.clears
                            and ((definition.id == VISITORS and self._claimed_source())
                                 or (definition.id != VISITORS and self._stage_identity()
                                     and not self._exact(PRIZE_PROGRESS_FRAME)
                                     and PRIZE_COUNT.ocr(self.device.image).strip() == '')))
                if received:
                    frames += 1
                    if frames >= 3 and stable.reached():
                        record.update(status='complete', reason='prize_claim_verified',
                                      proof_kind='owned_claim_receipt', verified=True,
                                      claimed=True, pending=False, returned_stage=True)
                        capture('claimed')
                        return record
                    continue

                panel = self._panel(prize) if record['open_clicks'] else None
                state = ('panel', panel['count'], panel['action']) if panel else None
                if (panel and panel['count'] == prize.clears
                        and panel['action'] == 'accept' and not record['claim_clicks']):
                    if previous != state:
                        previous = state
                        stable.reset()
                        continue
                    if stable.reached() and self._panel(prize) == panel:
                        record.update(claim_clicks=1, pending=True, count=prize.clears,
                                      reason='prize_claim_pending')
                        persist()  # Crash after this point can never replay Accept.
                        self.device.click(PRIZE_PANEL_ACCEPT)
                        wait.reset()
                        stable.reset()
                        frames = 0
                        continue
                elif not record['open_clicks']:
                    count = self._count()
                    state = ('source', count)
                    if count != prize.clears:
                        record['reason'] = 'prize_not_ready'
                        break
                    if previous != state:
                        previous = state
                        stable.reset()
                        continue
                    if stable.reached() and self._count() == count:
                        record.update(open_clicks=1, count=count)
                        persist()
                        self.device.click(PRIZE_ZUIKAKU)
                        wait.reset()
                        stable.reset()
                        frames = 0
                        continue
                else:
                    stable.reset()
                    frames = 0
                # A known popup may be closed after a failed/pending attempt;
                # closing is never treated as proof that the reward was claimed.
                if record['claim_clicks'] and self._panel_frame() and wait.reached():
                    if record['cancel_clicks'] < 2:
                        record['cancel_clicks'] += 1
                        persist()
                        self.device.click(PRIZE_PANEL_CLOSE)
                        wait.reset()
                if self._source() and record['claim_clicks']:
                    record['returned_stage'] = True
            record['reason'] = ('prize_claim_pending_unverified' if record['claim_clicks']
                                else record.get('reason', 'prize_claim_unverified'))
            capture('blocked')
            # Even an unreadable name/counter may leave our positively recognized
            # Info panel open. Its own red X is safe; Sortie/Accept stay untouched.
            for _ in self.owner.loop(skip_first=False, timeout=8):
                if self._source():
                    if stable.reached():
                        record['returned_stage'] = True
                        break
                    continue
                stable.reset()
                if (record['open_clicks'] and record['cancel_clicks'] < 2
                        and wait.reached() and self._panel_frame()):
                    record['cancel_clicks'] += 1
                    persist()
                    self.device.click(PRIZE_PANEL_CLOSE)
                    wait.reset()
            return record
        except Exception:
            record.update(status='blocked', reason='prize_claim_exception')
            capture('exception')
            raise
        finally:
            record['finished'] = datetime.now().isoformat(timespec='seconds')
            persist()
