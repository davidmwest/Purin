"""Bounded War Archives list observations, without entering or claiming anything.

The old identity templates establish which archive is visible. They do not prove
completion, unlocked stages or a guaranteed reward. Those fields deliberately
remain unknown until captured UI supplies a tested reader.
"""
import json
import os
import re
import tempfile
from datetime import datetime
from time import monotonic
from uuid import uuid4

from module.base.timer import Timer
from module.base.utils import save_image
from module.campaign.campaign_ui import CampaignUI
from module.combat.assets import BATTLE_PREPARATION, GET_ITEMS_1, GET_ITEMS_2, GET_ITEMS_3, GET_SHIP
from module.eventstory.eventstory import ensure_event_page, ensure_event_running
from module.exception import ScriptError
from module.freebies.assets import OCR_DATA_KEY
from module.handler.assets import IN_MAP
from module.map.assets import FLEET_PREPARATION, MAP_PREPARATION, MAP_PREPARATION_HARD
from module.ocr.ocr import Ocr
from module.ui.assets import WAR_ARCHIVES_CHECK
from module.ui.page import page_archives, page_main
from module.ui.scroll import Scroll
from module.war_archives.assets import WAR_ARCHIVES_EX_ON, WAR_ARCHIVES_SCROLL, WAR_ARCHIVES_SP_ON
from module.war_archives.dictionary import dic_archives_template

ARCHIVE_KEYS = Ocr(OCR_DATA_KEY, letter=(255, 247, 247), threshold=64, alphabet='0123456789/')
ARCHIVE_SCROLL = Scroll(WAR_ARCHIVES_SCROLL, color=(247, 211, 66), name='ARCHIVE_OBSERVE_SCROLL')
CATEGORY_BUTTONS = {'ex': WAR_ARCHIVES_EX_ON, 'sp': WAR_ARCHIVES_SP_ON}
OBSERVATION_VERSION = 1


def parse_raw_keys(text):
    """Keep unreadable OCR distinct from a real zero; never infer missing digits."""
    counter = parse_key_counter(text)
    return None if counter is None else counter['raw_keys']


def parse_key_counter(text):
    # Older UI used 60 units, newer UI may show 300. The denominator is
    # observation evidence, never permission to infer a per-sortie conversion.
    match = re.fullmatch(r'(0|[1-9]\d{0,2})/(60|300)', str(text).strip())
    if not match or int(match[1]) > int(match[2]):
        return None
    return dict(raw_keys=int(match[1]), key_capacity=int(match[2]))


def _save_record(path, record):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.observation-', suffix='.json', dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as file:
            json.dump(record, file, indent=2, sort_keys=True)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


class ArchiveProgress(CampaignUI):
    def _list_page(self):
        # A receipt/preparation can leave the old archive header visible beneath it.
        return (self.appear(WAR_ARCHIVES_CHECK, offset=(5, 5))
                and not any(self.appear(button, offset=(20, 30)) for button in (
                    IN_MAP, MAP_PREPARATION, MAP_PREPARATION_HARD, FLEET_PREPARATION,
                    BATTLE_PREPARATION, GET_ITEMS_1, GET_ITEMS_2, GET_ITEMS_3, GET_SHIP)))

    def _category(self):
        states = [name for name, button in CATEGORY_BUTTONS.items() if self.appear(button, offset=(3, 3))]
        return states[0] if len(states) == 1 else None

    def read_cards(self):
        """Read only fully visible known identity anchors, with no guessed card box."""
        if not self._list_page():
            return []
        cards = []
        for archive, template in dic_archives_template.items():
            similarity, anchor = template.match_result(self.device.image)
            left, top, right, bottom = anchor.area
            if similarity < .85 or not (75 <= left < right <= 1245 and 110 <= top < bottom <= 700):
                continue
            cards.append(dict(archive=archive, identity_area=list(anchor.area),
                              identity_similarity=round(float(similarity), 4),
                              completion=None, guaranteed_prize_counter=None,
                              guaranteed_prize_claimed=None, unlocked=None,
                              fully_observed=False))
        return sorted(cards, key=lambda card: (card['identity_area'][1], card['identity_area'][0]))

    def _scroll_info(self):
        mask = ARCHIVE_SCROLL.match_color(self)
        indices = mask.nonzero()[0]
        if len(indices) < 3 or len(indices) >= len(mask) - 2:
            return None
        top, bottom = int(indices[0]), int(indices[-1]) + 1
        # A fragmented/animated color match is not permission to drag.
        if len(indices) < (bottom - top) * .9:
            return None
        travel = len(mask) - (bottom - top)
        return dict(top=top, bottom=bottom, position=round(top / travel, 3))

    def _settled_list(self, category=None, timeout=8):
        previous = None
        settled = Timer(1, count=2).start()
        for _ in self.loop(skip_first=False, timeout=timeout):
            if not self._list_page() or (category is not None and self._category() != category):
                previous = None
                settled.reset()
                continue
            cards = self.read_cards()
            scroll = self._scroll_info()
            signature = (tuple((card['archive'], tuple(card['identity_area'])) for card in cards),
                         None if scroll is None else (scroll['top'], scroll['bottom']))
            if signature != previous or not cards:
                previous = signature
                settled.reset()
                continue
            if settled.reached():
                # Only an exact, recognized list refreshes the global idle watchdog.
                self.device.stuck_record_clear()
                return cards, scroll
        return None

    def _select_category(self, category, record, persist):
        settled = Timer(2, count=2).start()
        clicks = 0
        for _ in self.loop(skip_first=False, timeout=10):
            if not self._list_page():
                continue
            current = self._category()
            if current == category:
                return True
            if current is None or not settled.reached() or clicks >= 2:
                continue
            record['selector_clicks'] += 1
            persist()
            self.device.click(CATEGORY_BUTTONS[category])
            clicks += 1
            settled.reset()
        return False

    def _drag_scroll(self, info, to_top, record, persist):
        if not self._list_page() or self._scroll_info() != info:
            return False
        left, top, right, bottom = ARCHIVE_SCROLL.area
        middle = (info['top'] + info['bottom']) / 2
        half = (info['bottom'] - info['top']) / 2
        target = half if to_top else min(bottom - top - half, middle + half * 1.32)
        if abs(target - middle) < 2:
            return False
        start = (int((left + right) / 2), int(top + middle))
        end = (start[0], int(top + target))
        record['scroll_drags'] += 1
        persist()  # One bounded drag, never Scroll.set's retry loop.
        self.device.swipe(start, end, duration=(.3, .5), name='ARCHIVE_OBSERVE_SCROLL', distance_check=False)
        return True

    def _capture(self, directory, name, record):
        path = os.path.abspath(os.path.join(directory, f'{name}.png'))
        save_image(self.device.image, path)
        record['diagnostics'].append(path)
        return path

    def _observe_category(self, category, directory, record, persist, max_pages, deadline):
        if not self._select_category(category, record, persist):
            return 'selector_unrecognized'
        result = self._settled_list(category)
        if result is None:
            return 'list_unrecognized'
        cards, scroll = result
        if scroll and scroll['position'] > .05:
            if not self._drag_scroll(scroll, True, record, persist):
                return 'scroll_top_unverified'
            result = self._settled_list(category)
            if result is None:
                return 'scroll_top_unrecognized'
            cards, scroll = result
            if scroll and scroll['position'] > .05:
                return 'scroll_top_not_reached'
        seen = set()
        for index in range(max_pages):
            if monotonic() >= deadline:
                return 'observation_deadline'
            signature = (tuple((card['archive'], tuple(card['identity_area'])) for card in cards),
                         None if scroll is None else (scroll['top'], scroll['bottom']))
            if signature in seen:
                return 'repeated_page'
            seen.add(signature)
            screenshot = self._capture(directory, f'{category}_{index:02d}', record)
            raw_text = ARCHIVE_KEYS.ocr(self.device.image)
            counter = parse_key_counter(raw_text)
            record['pages'].append(dict(category=category, index=index, cards=cards,
                                        raw_keys=None if counter is None else counter['raw_keys'],
                                        key_capacity=None if counter is None else counter['key_capacity'],
                                        keys_ocr=str(raw_text),
                                        scroll=scroll, screenshot=screenshot))
            persist()
            if scroll is None:
                return 'scroll_not_visible'
            if scroll['position'] >= .95:
                return 'bottom_observed'
            if not self._drag_scroll(scroll, False, record, persist):
                return 'scroll_not_moved'
            result = self._settled_list(category)
            if result is None:
                return 'scrolled_list_unrecognized'
            cards, scroll = result
        return 'page_limit'

    def run_observe(self, category='all', folder='./log/war_archives', request_id=None, max_pages=20):
        """Capture list pages only. A supplied request ID is never replayed after a crash.

        No archive entrance, key collection, stage entrance, prize or battle control
        is an allowed input. The result cannot authorize the execution adapter.
        """
        account = self.config.config_name
        if account not in {'alas', 'f2p'} or category not in {'all', 'ex', 'sp'}:
            raise ScriptError('Unsupported War Archives observation account/category')
        if type(max_pages) is not int or not 1 <= max_pages <= 20:
            raise ScriptError('Invalid War Archives observation page limit')
        request_id = request_id or f'{datetime.now():%Y%m%dT%H%M%S%f}-{uuid4().hex[:12]}'
        if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', request_id):
            raise ScriptError('Invalid War Archives observation request')
        directory = os.path.join(folder, account, request_id)
        path = os.path.join(directory, 'observation.json')
        if os.path.exists(path):
            try:
                with open(path, encoding='utf-8') as file:
                    previous = json.load(file)
            except (OSError, ValueError) as exc:
                raise ScriptError('Cannot safely reuse War Archives observation') from exc
            if (not isinstance(previous, dict) or previous.get('account') != account
                    or previous.get('request_id') != request_id or previous.get('version') != OBSERVATION_VERSION):
                raise ScriptError('Invalid War Archives observation identity')
            return dict(previous, reused=True)
        ensure_event_running(self)
        record = dict(version=OBSERVATION_VERSION, request_id=request_id, account=account,
                      status='observing', category=category, started=datetime.now().isoformat(timespec='seconds'),
                      pages=[], categories={}, diagnostics=[], selector_clicks=0, scroll_drags=0,
                      returned_main=False, combat_attempted=False, verified_for_execution=False,
                      journal=os.path.abspath(path))
        persist = lambda: _save_record(path, record)
        persist()
        deadline = monotonic() + 180
        try:
            # ui_ensure and legacy archive search have nested unbounded loops.
            if not ensure_event_page(self, page_archives, timeout=25, max_clicks=8):
                record.update(status='blocked', reason='archive_navigation_unrecognized')
                self._capture(directory, 'navigation_blocked', record)
                return record
            for selected in (('ex', 'sp') if category == 'all' else (category,)):
                reason = self._observe_category(selected, directory, record, persist, max_pages, deadline)
                record['categories'][selected] = reason
                persist()
                if reason not in {'bottom_observed', 'scroll_not_visible'}:
                    record.update(status='blocked', reason=reason)
                    self._capture(directory, f'{selected}_blocked', record)
                    break
            else:
                record.update(status='observed', reason='list_captured_progress_readers_unverified')
            # Only leave a positively recognized list. Unknown destinations stay captured.
            if self._list_page():
                record['returned_main'] = ensure_event_page(self, page_main, timeout=20, max_clicks=5)
            return record
        except Exception:
            self._capture(directory, 'exception', record)
            record.update(status='blocked', reason='exception')
            raise
        finally:
            record['finished'] = datetime.now().isoformat(timespec='seconds')
            persist()
