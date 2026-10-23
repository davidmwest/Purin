"""Bounded War Archives list and stage navigation, without combat or claims.

Fresh title, Clear/% stage and key witnesses can authorize the separate execution
adapter. List completion never substitutes for a visible stage-clear witness.
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
from module.base.utils import save_image, rgb2gray, crop
from module.campaign.assets import SWITCH_1_HARD, SWITCH_1_NORMAL, CHAPTER_PREV
from module.campaign.campaign_ui import CampaignUI
from module.combat.assets import BATTLE_PREPARATION, GET_ITEMS_1, GET_ITEMS_2, GET_ITEMS_3, GET_SHIP
from module.eventstory.eventstory import ensure_event_page, ensure_event_running
from module.exception import ScriptError, CampaignNameError
from module.freebies.assets import OCR_DATA_KEY
from module.handler.assets import IN_MAP, POPUP_CANCEL, POPUP_CONFIRM
from module.map.assets import FLEET_PREPARATION, MAP_PREPARATION, MAP_PREPARATION_HARD
from module.ocr.ocr import Ocr
from module.ui.assets import WAR_ARCHIVES_CHECK
from module.ui.page import page_archives, page_main
from module.ui_white.assets import POPUP_CANCEL_WHITE, POPUP_CONFIRM_WHITE
from module.ui.scroll import Scroll
from module.war_archives.assets import (WAR_ARCHIVES_EX_ON, WAR_ARCHIVES_SCROLL, WAR_ARCHIVES_SP_ON,
                                      WAR_ARCHIVES_CAMPAIGN_CHECK, TEMPLATE_MOONLIT_OVERTURE)
from module.war_archives.dictionary import dic_archives_template
from module.war_archives.stage_assets import VISITORS_TITLE, ARCHIVE_NEXT, STAGE_CLEAR, STAGE_PERCENT

ARCHIVE_KEYS = Ocr(OCR_DATA_KEY, letter=(255, 247, 247), threshold=64, alphabet='0123456789/')
ARCHIVE_SCROLL = Scroll(WAR_ARCHIVES_SCROLL, color=(247, 211, 66), name='ARCHIVE_OBSERVE_SCROLL')
CATEGORY_BUTTONS = {'ex': WAR_ARCHIVES_EX_ON, 'sp': WAR_ARCHIVES_SP_ON}
OBSERVATION_VERSION = 1
FILES_HEADER = Template('./assets/en/war_archives/ARCHIVE_FILES_HEADER_20261023.png')
# The old Prelude portrait crop predates the current Moonlit Overture card.
# Use its existing current-art crop without relaxing the identity threshold.
CARD_IDENTITY_OVERRIDES = {'war_archives_20190314_en': TEMPLATE_MOONLIT_OVERTURE}
STAGE_KEYS = Ocr((1188, 107, 1272, 131), lang='cnocr', letter=(255, 255, 255),
                 threshold=96, alphabet='0123456789/', name='ARCHIVE_STAGE_KEYS')
STAGE_TITLE = Ocr((210, 70, 1100, 100), lang='cnocr', letter=(255, 255, 255),
                  threshold=96, name='ARCHIVE_STAGE_TITLE')


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
                    BATTLE_PREPARATION, GET_ITEMS_1, GET_ITEMS_2, GET_ITEMS_3, GET_SHIP,
                    POPUP_CANCEL, POPUP_CONFIRM, POPUP_CANCEL_WHITE, POPUP_CONFIRM_WHITE)))

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
            template = CARD_IDENTITY_OVERRIDES.get(archive, template)
            similarity, anchor = template.match_result(self.device.image)
            left, top, right, bottom = (int(value) for value in anchor.area)
            if similarity < .85 or not (75 <= left < right <= 1245 and 110 <= top < bottom <= 700):
                continue
            boxes = []
            for header in headers:
                x, y = (int(value) for value in header.area[:2])
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
                x, y = (int(value) for value in header.area[:2])
                completion = parse_completion(Ocr((x + 80, y + 204, x + 143, y + 234),
                    lang='cnocr', letter=(158, 255, 82), threshold=96,
                    alphabet='0123456789%', name='ARCHIVE_COMPLETION').ocr(self.device.image))
            cards.append(dict(archive=archive, identity_area=[left, top, right, bottom],
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

    def _list_geometry(self):
        """Cheap settling witnesses; these never authorize an archive entrance."""
        return tuple(sorted(tuple(int(value) for value in header.area)
                            for header in FILES_HEADER.match_multi(self.device.image, similarity=.90)
                            if (75 <= header.area[0] and header.area[0] + 470 <= 1245
                                and 110 <= header.area[1] and header.area[1] + 249 <= 700)))

    @staticmethod
    def _scroll_at_top(info):
        return info['top'] <= 1

    @staticmethod
    def _scroll_at_bottom(info):
        return info['bottom'] >= ARCHIVE_SCROLL.area[3] - ARCHIVE_SCROLL.area[1] - 1

    def _settled_list(self, category=None, timeout=8):
        previous = None
        settled = Timer(1, count=2).start()
        deadline = monotonic() + timeout
        for _ in self.loop(skip_first=False, timeout=timeout):
            if monotonic() >= deadline:
                break
            if not self._list_page():
                previous = None
                settled.reset()
                continue
            current_category = self._category()
            if category is not None and current_category != category:
                previous = None
                settled.reset()
                continue
            headers = self._list_geometry()
            scroll = self._scroll_info()
            signature = (headers, None if scroll is None else (scroll['top'], scroll['bottom']))
            if signature != previous or not headers:
                previous = signature
                settled.reset()
                continue
            if settled.reached():
                # Matching all identities is expensive. Do it once, on the final
                # fresh settled frame; actions still reread their exact identity.
                cards = self.read_cards()
                # Recent unsupported archives can fill a whole page. Exact list,
                # selected category, full Files cards and a known scrollbar still
                # permit bounded scrolling; they never prove any card identity.
                if not cards and (scroll is None or current_category is None):
                    self._archive_scroll_pending = None
                    return None
                self._confirm_scroll_progress(current_category, scroll)
                if cards:
                    # Unknown identities alone do not refresh the idle watchdog.
                    self.device.stuck_record_clear()
                return cards, scroll
        self._archive_scroll_pending = None
        return None

    def _confirm_scroll_progress(self, category, scroll):
        """Forget only our swipe history after a fresh settled directional move."""
        pending = getattr(self, '_archive_scroll_pending', None)
        self._archive_scroll_pending = None
        if pending is None or scroll is None or category != pending[0]:
            return
        _, before_top, before_bottom, to_top = pending
        delta = scroll['top'] - before_top
        same_thumb = abs((scroll['bottom'] - scroll['top']) - (before_bottom - before_top)) <= 2
        if same_thumb and (delta <= -2 if to_top else delta >= 2):
            # The global detector otherwise counts a long, progressing census
            # as repeated clicks. Other buttons' history remains intact.
            self.device.click_record_remove('ARCHIVE_OBSERVE_SCROLL')

    def _select_category(self, category, record, persist):
        settled = Timer(2, count=2).start()
        clicks = 0
        deadline = monotonic() + 10
        for _ in self.loop(skip_first=False, timeout=10):
            if monotonic() >= deadline:
                break
            if not self._list_page():
                continue
            current = self._category()
            # The loop's Timer also requires a minimum frame count. Slow
            # recognition must not keep that count alive past the wall limit.
            if monotonic() >= deadline:
                break
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

    def _drag_scroll(self, info, to_top, record, persist, expected_category=None, deadline=None):
        self._archive_scroll_pending = None
        if not self._list_page() or self._scroll_info() != info:
            return False
        category = self._category()
        if ((expected_category is not None and category != expected_category)
                or (deadline is not None and monotonic() >= deadline)):
            return False
        left, top, right, bottom = ARCHIVE_SCROLL.area
        middle = (info['top'] + info['bottom']) / 2
        half = (info['bottom'] - info['top']) / 2
        # Half a viewport overlaps by >=282px on the captured 565px list,
        # enough for each 249px card to be fully visible on at least one page.
        target = half if to_top else min(bottom - top - half, middle + half)
        if abs(target - middle) < 2:
            return False
        start = (int((left + right) / 2), int(top + middle))
        end = (start[0], int(top + target))
        record['scroll_drags'] += 1
        persist()  # One bounded drag, never Scroll.set's retry loop.
        self.device.swipe(start, end, duration=(.3, .5), name='ARCHIVE_OBSERVE_SCROLL', distance_check=False)
        if category is not None:
            self._archive_scroll_pending = (category, info['top'], info['bottom'], to_top)
        return True

    def _capture(self, directory, name, record):
        path = os.path.abspath(os.path.join(directory, f'{name}.png'))
        save_image(self.device.image, path)
        record['diagnostics'].append(path)
        return path

    def _reset_scroll_top(self, category, result, record, persist, deadline):
        """Finish a shortened drag only after fresh, same-list upward progress."""
        for _ in range(3):
            _, before = result
            if self._scroll_at_top(before):
                return result, None
            if monotonic() >= deadline:
                return None, 'observation_deadline'
            if self._category() != category:
                return None, 'scroll_top_unverified'
            if monotonic() >= deadline:
                return None, 'observation_deadline'
            if not self._drag_scroll(before, True, record, persist,
                                     expected_category=category, deadline=deadline):
                return None, ('observation_deadline' if monotonic() >= deadline
                              else 'scroll_top_unverified')
            remaining = deadline - monotonic()
            if remaining <= 0:
                self._archive_scroll_pending = None
                return None, 'observation_deadline'
            result = self._settled_list(category, timeout=min(8, remaining))
            if monotonic() >= deadline:
                self._archive_scroll_pending = None
                return None, 'observation_deadline'
            if result is None or result[1] is None:
                return None, 'scroll_top_unrecognized'
            _, after = result
            same_thumb = abs((after['bottom'] - after['top']) - (before['bottom'] - before['top'])) <= 2
            if not same_thumb or after['top'] >= before['top']:
                return None, 'scroll_top_not_reached'
        if self._scroll_at_top(result[1]):
            return result, None
        return None, 'scroll_top_not_reached'

    def _find_archive(self, archive, category, record, persist, deadline):
        """Search known list pages; never use legacy Scroll.set/search loops."""
        if monotonic() >= deadline:
            return None
        if not self._select_category(category, record, persist):
            return None
        result = self._settled_list(category)
        if result is None:
            return None
        cards, scroll = result
        if scroll and not self._scroll_at_top(scroll):
            result, reason = self._reset_scroll_top(category, result, record, persist, deadline)
            if reason is not None:
                return None
            cards, scroll = result
        seen = set()
        for index in range(20):
            if monotonic() >= deadline:
                break
            matches = [card for card in cards if card['archive'] == archive
                       and card.get('card_area') is not None and card.get('completion') is not None]
            if len(matches) == 1:
                return matches[0]
            signature = (tuple((card['archive'], tuple(card['identity_area'])) for card in cards),
                         None if scroll is None else (scroll['top'], scroll['bottom']))
            if signature in seen or scroll is None or self._scroll_at_bottom(scroll):
                break
            seen.add(signature)
            if index == 19 or monotonic() >= deadline:
                break
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
                    BATTLE_PREPARATION, GET_ITEMS_1, GET_ITEMS_2, GET_ITEMS_3, GET_SHIP,
                    POPUP_CANCEL, POPUP_CONFIRM, POPUP_CANCEL_WHITE, POPUP_CONFIRM_WHITE)))

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
        """Navigate known stage controls, then prove the visible entrance freshly.

        This does not click a stage or spend keys. Persistent input reservations
        stop a repeated request from replaying mode/page clicks after a crash.
        """
        from module.war_archives.catalog import get_archive
        definition = get_archive(goal.event)
        if (self.config.config_name not in {'alas', 'f2p'} or definition is None
                or goal.mode not in {'normal', 'hard'}
                or goal.stage.lower() not in definition.stages(goal.mode)):
            raise ScriptError('Unsupported archive stage goal')
        request_id = request_id or f'{datetime.now():%Y%m%dT%H%M%S%f}-{uuid4().hex[:12]}'
        if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', request_id):
            raise ScriptError('Invalid archive stage request')
        directory = os.path.join(folder, self.config.config_name, request_id)
        path = os.path.join(directory, 'goal.json')
        record = dict(version=OBSERVATION_VERSION, account=self.config.config_name,
                      request_id=request_id, event=goal.event, stage=goal.stage.lower(), mode=goal.mode,
                      status='observing', diagnostics=[], navigation_inputs=[],
                      verified_for_navigation=False, verified_for_execution=False,
                      unlocked=None, stage_complete=None, raw_keys=None, key_capacity=None,
                      key_cost=5, key_mode='per_entry', combat_attempted=False,
                      journal=os.path.abspath(path))
        if os.path.exists(path):
            try:
                with open(path, encoding='utf-8') as file:
                    previous = json.load(file)
            except (OSError, ValueError) as exc:
                raise ScriptError('Cannot safely reuse archive stage request') from exc
            if (not isinstance(previous, dict)
                    or any(previous.get(key) != record[key] for key in
                           ('version', 'account', 'request_id', 'event', 'stage', 'mode'))
                    or not isinstance(previous.get('navigation_inputs'), list)
                    or len(previous['navigation_inputs']) > 6
                    or any(not isinstance(item, str) for item in previous['navigation_inputs'])):
                raise ScriptError('Invalid archive stage request identity/budget')
            record['navigation_inputs'] = previous['navigation_inputs']
            record['diagnostics'] = previous.get('diagnostics', [])
        persist = lambda: _save_record(path, record)
        persist()
        ensure_event_running(self)
        try:
            self.device.screenshot()
            if self._read_stage_screen(goal.event) is None:
                entered = self.observe_archive(goal.event, folder=folder, request_id=request_id)
                record['diagnostics'].extend(entered.get('diagnostics', []))
                if entered.get('status') != 'observed':
                    record.update(status='blocked', reason=entered.get('reason', 'archive_entry_failed'))
                    return record
            self._navigate_goal(goal, record, persist)
            self._capture(directory, 'goal_stage' if record['verified_for_navigation'] else 'goal_blocked', record)
            return record
        finally:
            record['finished'] = datetime.now().isoformat(timespec='seconds')
            persist()

    def prepare_goal(self, campaign, goal):
        # A failed fresh read must not leave a previous campaign's entrance usable.
        campaign.ENTRANCE = Button(area=(), color=(), button=(), name='archive_unverified')
        proof = self.proof_for_goal(goal)
        if proof.get('verified_for_navigation'):
            area = tuple(proof['entrance_area'])
            campaign.ENTRANCE = Button(area=area, color=(), button=area, name=goal.stage.lower())
            # enter_map uses appear_then_click without a template offset, just
            # like normal campaign OCR entrances: retain this fresh icon color.
            campaign.ENTRANCE.load_color(self.device.image)
        return proof

    def _archive_identity(self, archive):
        """Require the exact known title, or an exact full catalog title OCR."""
        from module.war_archives.catalog import get_archive
        definition = get_archive(archive)
        if definition is None or not self._archive_stage_page():
            return False
        if archive == 'war_archives_20190321_en':
            if not VISITORS_TITLE.match(self.device.image, offset=(3, 3), similarity=.90):
                return False
            # A popup can leave a correlated but dimmed title beneath it.
            actual = crop(self.device.image, VISITORS_TITLE.area)
            return bool(abs(actual.mean(axis=(0, 1)) - VISITORS_TITLE.image.mean(axis=(0, 1))).max() <= 15)
        normalize = lambda value: re.sub(r'[^a-z0-9]', '', str(value).lower())
        actual = normalize(STAGE_TITLE.ocr(self.device.image))
        expected = {normalize(name) + suffix for name in (definition.name, *definition.aliases)
                    for suffix in ('', 'prequel', 'sequel')}
        return actual in expected

    def _read_stage_screen(self, archive):
        """Read only unlocked Clear/% stage icons on the exact archive page."""
        from module.war_archives.catalog import get_archive
        definition = get_archive(archive)
        if not self._archive_identity(archive):
            return None
        image = self.device.image
        gray = rgb2gray(crop(image, self._stage_detect_area, copy=False))
        # Use the existing icon/name geometry, with explicit EN templates. A
        # locked node lacks either witness and cannot become an entrance.
        clear = self.campaign_match_multi(STAGE_CLEAR, image, gray,
                                          name_offset=(70, 12), name_size=(60, 14))
        partial = self.campaign_match_multi(STAGE_PERCENT, image, gray,
                                            name_offset=(45, 3), name_size=(60, 14))
        buttons = clear + partial
        if not buttons:
            return None
        names = Ocr(buttons, letter=(255, 255, 255), threshold=128,
                    alphabet='0123456789ABCDEFGHIJKLMNPQRSTUVWXYZ-', name='ARCHIVE_STAGES').ocr(image)
        if not isinstance(names, list):
            names = [names]
        stages = {}
        for index, (name, button) in enumerate(zip(names, buttons)):
            name = self._campaign_ocr_result_process(name)
            area = [int(value) for value in button.button]
            if (not name or name in stages or name not in definition.normal_stages + definition.hard_stages
                    or not (87 <= area[0] < area[2] <= 1151 and 117 <= area[1] < area[3] <= 636)):
                return None
            stages[name] = dict(entrance_area=area, complete=index < len(clear))
        modes = [mode for mode in ('normal', 'hard')
                 if stages and set(stages).issubset(definition.stages(mode))]
        if len(modes) != 1:
            return None
        try:
            pages = {self._campaign_get_chapter_index(self._campaign_separate_name(name)[0]) for name in stages}
        except (CampaignNameError, ValueError, IndexError):
            return None
        if len(pages) != 1:
            return None
        counter = parse_key_counter(STAGE_KEYS.ocr(image))
        return dict(event=archive, mode=modes[0], page=pages.pop(), stages=stages,
                    raw_keys=None if counter is None else counter['raw_keys'],
                    key_capacity=None if counter is None else counter['key_capacity'])

    def _navigate_goal(self, goal, record, persist):
        target = goal.stage.lower()
        desired_page = self._campaign_get_chapter_index(self._campaign_separate_name(target)[0])
        previous = None
        settled = Timer(1, count=2).start()
        for _ in self.loop(skip_first=False, timeout=45):
            screen = self._read_stage_screen(goal.event)
            if screen is None:
                previous = None
                settled.reset()
                continue
            signature = json.dumps(screen, sort_keys=True)
            if signature != previous:
                previous = signature
                settled.reset()
                continue
            if not settled.reached():
                continue
            self.device.stuck_record_clear()
            if screen['mode'] == goal.mode and target in screen['stages']:
                stage = screen['stages'][target]
                record.update(status='observed', reason='fresh_stage_verified',
                              verified_for_navigation=True, unlocked=True,
                              stage_complete=stage['complete'], entrance_area=stage['entrance_area'],
                              raw_keys=screen['raw_keys'], key_capacity=screen['key_capacity'],
                              verified_for_execution=(screen['key_capacity'] == 300
                                  and type(screen['raw_keys']) is int and screen['raw_keys'] >= 5))
                return
            if screen['mode'] != goal.mode:
                # Labels indicate the destination, unlike legacy Switch state names.
                action = SWITCH_1_HARD if goal.mode == 'hard' else SWITCH_1_NORMAL
                transition = f'mode:{screen["mode"]}:{goal.mode}'
            elif desired_page != screen['page']:
                action = ARCHIVE_NEXT if desired_page > screen['page'] else CHAPTER_PREV
                transition = f'page:{goal.mode}:{screen["page"]}:{desired_page}'
            else:
                record.update(status='blocked', reason='target_locked_or_unreadable', unlocked=None,
                              safe_target_return=dict(
                                  verified=True, proof_kind='fresh_exact_archive_stage',
                                  account=self.config.config_name, event=goal.event,
                                  mode=goal.mode, stage=target, page=screen['page']))
                return
            # A fresh known state may authorize one input, never a repeated click
            # on a stale animation or recovery of an uncertain reserved input.
            if (transition in record['navigation_inputs'] or len(record['navigation_inputs']) >= 6
                    or not self.appear(action, offset=(3, 3))):
                continue
            record['navigation_inputs'].append(transition)
            persist()
            self.device.click(action)
            previous = None
            settled.reset()
        record.update(status='blocked', reason='stage_navigation_unverified')

    def observe_prize(self, archive, prize, folder='./log/war_archives', request_id=None):
        from module.war_archives.catalog import Archive
        from module.war_archives.prize_progress import ArchivePrizeProgress
        archive_id = archive.id if isinstance(archive, Archive) else archive
        return ArchivePrizeProgress(self).observe(archive_id, prize, folder=folder, request_id=request_id)

    def claim_prize(self, archive, prize, folder='./log/war_archives', request_id=None):
        from module.war_archives.catalog import Archive
        from module.war_archives.prize_progress import ArchivePrizeProgress
        archive_id = archive.id if isinstance(archive, Archive) else archive
        return ArchivePrizeProgress(self).claim(archive_id, prize, folder=folder, request_id=request_id)

    def _observe_category(self, category, directory, record, persist, max_pages, deadline):
        if monotonic() >= deadline:
            return 'observation_deadline'
        if not self._select_category(category, record, persist):
            return 'selector_unrecognized'
        result = self._settled_list(category)
        if result is None:
            return 'list_unrecognized'
        cards, scroll = result
        if scroll and not self._scroll_at_top(scroll):
            result, reason = self._reset_scroll_top(category, result, record, persist, deadline)
            if reason is not None:
                return reason
            cards, scroll = result
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
            if self._scroll_at_bottom(scroll):
                return 'bottom_observed'
            if index + 1 >= max_pages:
                return 'page_limit'
            if monotonic() >= deadline:
                return 'observation_deadline'
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
