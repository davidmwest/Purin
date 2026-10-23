"""Observe the guaranteed-prize panel; never use its Sortie/Accept action.

The first captured EN stage fixtures support Visitors/Zuikaku only. A received
prize requires the exact settled, unobscured button-free fixture. The client uses the same
single-item panel for both the under-60 Sortie and ready Accept actions, so no
generic confirmation handler is allowed here.
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
from module.handler.assets import POPUP_CANCEL
from module.ocr.ocr import Ocr
from module.war_archives.catalog import get_archive
from module.war_archives.progress import _save_record


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

    def _exact(self, button):
        if not button.match(self.device.image, offset=(2, 2), similarity=.90):
            return False
        # Correlation alone also matches a dimmed stage beneath a popup.
        left, top, right, bottom = button.area
        actual = self.device.image[top:bottom, left:right]
        return bool(np.max(np.abs(actual.mean(axis=(0, 1))
                                  - button.image.mean(axis=(0, 1)))) <= 15)

    def _source(self):
        return (self.owner._archive_stage_page()
                and all(self._exact(button) for button in
                        (PRIZE_ARCHIVE_TITLE, PRIZE_PROGRESS_FRAME, PRIZE_ZUIKAKU)))

    def _count(self):
        return parse_prize_count(PRIZE_COUNT.ocr(self.device.image)) if self._source() else None

    def _claimed_source(self):
        # ChapterProxy.register initializes every catalog drop_gain as unreceived.
        # ChapterAwardInfo hides this sole reward only after receive=true. Exact
        # background + title + stage/no-overlay proof excludes loading/other maps.
        return (self.owner._archive_stage_page()
                and self._exact(PRIZE_CLAIMED_TITLE)
                and self._exact(PRIZE_CLAIMED_BACKGROUND)
                and not self._exact(PRIZE_ZUIKAKU)
                and not self._exact(PRIZE_PROGRESS_FRAME))

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
        if definition.id != VISITORS or prize.id != 307061:
            record['reason'] = 'prize_fixture_unsupported'
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

            # A changed panel must settle before capture. Nothing in it is an
            # authorized claim; Sortie and Accept are both observation-only.
            changed = Timer(1, count=2).start()
            for _ in self.owner.loop(skip_first=False, timeout=8):
                if self._source():
                    changed.reset()
                    continue
                if changed.reached():
                    record.update(status='observed', reason='prize_panel_captured_unverified')
                    capture('prize_panel')
                    break
            else:
                record['reason'] = 'prize_panel_not_observed'
                capture('prize_open_blocked')
                return record

            # Only the established exact Cancel control is safe before a real
            # panel fixture supplies its X. No generic handler or guessed back.
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
                        and POPUP_CANCEL.match(self.device.image, offset=(2, 2), similarity=.95)):
                    record['cancel_clicks'] += 1
                    persist()
                    self.device.click(POPUP_CANCEL)
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
