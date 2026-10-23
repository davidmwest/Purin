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
from module.base.button import Button
from module.base.template import Template
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
from module.war_archives.assets import (WAR_ARCHIVES_EX_ON, WAR_ARCHIVES_SCROLL, WAR_ARCHIVES_SP_ON,
                                      WAR_ARCHIVES_CAMPAIGN_CHECK)
from module.war_archives.dictionary import dic_archives_template

ARCHIVE_KEYS = Ocr(OCR_DATA_KEY, letter=(255, 247, 247), threshold=64, alphabet='0123456789/')
ARCHIVE_SCROLL = Scroll(WAR_ARCHIVES_SCROLL, color=(247, 211, 66), name='ARCHIVE_OBSERVE_SCROLL')
CATEGORY_BUTTONS = {'ex': WAR_ARCHIVES_EX_ON, 'sp': WAR_ARCHIVES_SP_ON}
OBSERVATION_VERSION = 1
FILES_HEADER = Template('./assets/en/war_archives/ARCHIVE_FILES_HEADER_20261023.png')


def parse_completion(text):
    match = re.fullmatch(r'(0|[1-9]\d?|100)%', str(text).strip())
    return None if match is None else int(match[1])


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
        # Selected and inactive tabs have identical lettering. Shape alone
        # matches both on the current EN screen; the selected blue is required.
        states = [name for name, button in CATEGORY_BUTTONS.items()
                  if self.match_template_color(button, offset=(3, 3))]
        return states[0] if len(states) == 1 else None

    def read_cards(self):
        """Associate each known identity with one fully visible Files card."""
        if not self._list_page():
            return []
        cards = []
        headers = FILES_HEADER.match_multi(self.device.image, similarity=.90)
        for archive, template in dic_archives_template.items():
            similarity, anchor = template.match_result(self.device.image)
            left, top, right, bottom = anchor.area
            if similarity < .85 or not (75 <= left < right <= 1245 and 110 <= top < bottom <= 700):
                continue
            boxes = []
            for header in headers:
                x, y = header.area[:2]
                # Geometry is relative to the exact repeated Files header in
                # both captured account fixtures. A clipped card is unreadable.
                box = (x, y + 20, x + 470, y + 249)
                if (75 <= x and x + 470 <= 1245 and 110 <= y and y + 249 <= 700
                        and box[0] <= left < right <= box[2]
                        and box[1] <= top < bottom <= box[3]):
                    boxes.append((header, box))
            completion, card_area = None, None
            if len(boxes) == 1:
                header, card_area = boxes[0]
                x, y = header.area[:2]
                completion = parse_completion(Ocr((x + 80, y + 204, x + 143, y + 234),
                    lang='cnocr', letter=(158, 255, 82), threshold=96,
                    alphabet='0123456789%', name='ARCHIVE_COMPLETION').ocr(self.device.image))
            cards.append(dict(archive=archive, identity_area=list(anchor.area),
                              identity_similarity=round(float(similarity), 4),
                              card_area=None if card_area is None else list(card_area),
                              completion=completion, completion_verified=completion is not None,
                              guaranteed_prize_counter=None,
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

    def _find_archive(self, archive, category, record, persist, deadline):
        """Search known list pages; never use legacy Scroll.set/search loops."""
        if not self._select_category(category, record, persist):
            return None
        result = self._settled_list(category)
        if result is None:
            return None
        cards, scroll = result
        if scroll and scroll['position'] > .05:
            if not self._drag_scroll(scroll, True, record, persist):
                return None
            result = self._settled_list(category)
            if result is None:
                return None
            cards, scroll = result
            if scroll and scroll['position'] > .05:
                return None
        seen = set()
        for _ in range(20):
            if monotonic() >= deadline:
                break
            matches = [card for card in cards if card['archive'] == archive
                       and card.get('card_area') is not None and card.get('completion') is not None]
            if len(matches) == 1:
                return matches[0]
            signature = tuple((card['archive'], tuple(card['identity_area'])) for card in cards)
            if signature in seen or scroll is None or scroll['position'] >= .95:
                break
            seen.add(signature)
            if not self._drag_scroll(scroll, False, record, persist):
                break
            result = self._settled_list(category)
            if result is None:
                break
            cards, scroll = result
        return None

    def _archive_stage_page(self):
        return (not self._list_page()
                and self.appear(WAR_ARCHIVES_CAMPAIGN_CHECK, offset=(20, 20))
                and not any(self.appear(button, offset=(20, 30)) for button in (
                    IN_MAP, MAP_PREPARATION, MAP_PREPARATION_HARD, FLEET_PREPARATION,
                    BATTLE_PREPARATION, GET_ITEMS_1, GET_ITEMS_2, GET_ITEMS_3, GET_SHIP)))

    def observe_archive(self, archive, folder='./log/war_archives', request_id=None):
        """Enter one positively identified archive and capture its stage/prize UI.

        One durable card input is the entire action budget. No stage, key,
        battle or claim control is used. A repeated request never re-enters.
        """
        from module.war_archives.catalog import get_archive
        definition = get_archive(archive)
        account = self.config.config_name
        if account not in {'alas', 'f2p'} or definition is None or definition.id not in dic_archives_template:
            raise ScriptError('Unsupported War Archives card observation')
        request_id = request_id or f'{datetime.now():%Y%m%dT%H%M%S%f}-{uuid4().hex[:12]}'
        if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', request_id):
            raise ScriptError('Invalid War Archives card observation request')
        directory = os.path.join(folder, account, request_id)
        path = os.path.join(directory, 'archive.json')
        if os.path.exists(path):
            try:
                with open(path, encoding='utf-8') as file:
                    previous = json.load(file)
            except (OSError, ValueError) as exc:
                raise ScriptError('Cannot safely reuse archive card observation') from exc
            if (not isinstance(previous, dict) or previous.get('account') != account
                    or previous.get('event') != definition.id or previous.get('request_id') != request_id
                    or previous.get('version') != OBSERVATION_VERSION):
                raise ScriptError('Invalid archive card observation identity')
            return dict(previous, reused=True)
        ensure_event_running(self)
        record = dict(version=OBSERVATION_VERSION, account=account, request_id=request_id,
                      event=definition.id, category=definition.category, status='observing',
                      selector_clicks=0, scroll_drags=0, entrance_clicks=0, diagnostics=[],
                      verified_for_navigation=False, verified_for_execution=False,
                      raw_keys=None, key_capacity=None, key_cost=5, key_mode='per_entry',
                      returned_main=False, combat_attempted=False, journal=os.path.abspath(path))
        persist = lambda: _save_record(path, record)
        persist()
        try:
            if not ensure_event_page(self, page_archives, timeout=25, max_clicks=8):
                record.update(status='blocked', reason='archive_navigation_unrecognized')
                return record
            card = self._find_archive(definition.id, definition.category, record, persist, monotonic() + 150)
            if card is None:
                record.update(status='blocked', reason='archive_card_unrecognized')
                return record
            # Read both account's raw key regime freshly before leaving the list.
            counter = parse_key_counter(ARCHIVE_KEYS.ocr(self.device.image))
            if counter:
                record.update(counter)
            record['card'] = card
            self._capture(directory, 'archive_card', record)
            # Revalidate identity after capture and before the sole entrance input.
            fresh = [item for item in self.read_cards() if item['archive'] == definition.id]
            if len(fresh) != 1 or fresh[0] != card or not self._list_page():
                record.update(status='blocked', reason='archive_card_changed')
                return record
            record['entrance_clicks'] = 1
            persist()
            area = tuple(card['identity_area'])
            self.device.click(Button(area=area, color=(), button=area, name='ARCHIVE_CARD_ENTRANCE'))
            settled = Timer(1, count=2).start()
            for _ in self.loop(skip_first=False, timeout=15):
                if not self._archive_stage_page():
                    settled.reset()
                    continue
                if settled.reached():
                    self.device.stuck_record_clear()
                    self._capture(directory, 'archive_stage', record)
                    record.update(status='observed', reason='stage_and_prize_readers_unverified')
                    return record
            record.update(status='blocked', reason='archive_entrance_unrecognized')
            return record
        finally:
            if record['status'] != 'observed':
                self._capture(directory, 'archive_blocked', record)
            record['finished'] = datetime.now().isoformat(timespec='seconds')
            persist()

    def proof_for_goal(self, goal, folder='./log/war_archives', request_id=None):
        """Fresh target observation; execution remains closed until UI is proved."""
        from module.war_archives.catalog import get_archive
        definition = get_archive(goal.event)
        if definition is None or goal.stage.lower() not in definition.stages(goal.mode):
            raise ScriptError('Unsupported archive stage goal')
        result = self.observe_archive(goal.event, folder=folder, request_id=request_id)
        return dict(result, stage=goal.stage, mode=goal.mode)

    def prepare_goal(self, campaign, goal):
        # Never set an entrance from an invented coordinate or an old screenshot.
        # The initial capture establishes the next fixture-backed reader.
        return self.proof_for_goal(goal)

    def observe_prize(self, archive, prize):
        return dict(verified=False, count=None, total=prize.clears, claimed=None,
                    reason='prize_reader_unverified')

    def claim_prize(self, archive, prize):
        # No claim inputs are supported by the list-screen fixture.
        return self.observe_prize(archive, prize)

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
