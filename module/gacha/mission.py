"""Read exact build-mission credit and observe order dialogs without spending."""
import json
import os
import re
import tempfile
from datetime import datetime

import cv2
import numpy as np

from module.base.timer import Timer
from module.config.utils import get_server_last_update, server_time_offset
from module.eventstory.eventstory import ensure_event_page, save_event_diagnostic
from module.exception import ScriptError
from module.gacha.assets import BUILD_MINUS, BUILD_PLUS, BUILD_SUBMIT_ORDERS
from module.gacha.banner_assets import EVENT_BUILD_ORDERS
from module.gacha.mission_assets import (BUILD_THREE_TEXT, BUILD_THREE_TEXT_F2P,
                                        DAILY_MISSION_MARKER, EVENT_MISSION_MARKER)
from module.gacha.ui import GachaUI
from module.logger import logger
from module.ocr.ocr import Ocr
from module.reward.reward import Reward
from module.ui.page import page_build, page_main, page_mission

OBSERVATION_VERSION = 'missions-v1'
STATE_FOLDER = './config/gacha'
ORDER_OBSERVATION_VERSION = 'order-dialog-v1'
CURRENT_EVENT = 'event_20261022_cn'


def parse_build_counter(text):
    """Full denominator validation; permissive/clamped DigitCounter is unsafe."""
    if not isinstance(text, str):
        return None
    text = text.translate(str.maketrans({'I': '1', 'D': '0', 'S': '5', 'B': '8'}))
    matched = re.fullmatch(r'\s*([0-3])\s*/\s*3\s*', text)
    return int(matched[1]) if matched else None


def locate_build_mission(image):
    """Read one fully visible, exact EVENT Build 3 row; unknown is not absence.

    Search the text at its observed horizontal position, then match the EVENT
    label in the same row. Suppress nearby correlation peaks, not other rows.
    """
    if image.shape[:2] != (720, 1280):
        return None
    for asset in (BUILD_THREE_TEXT, BUILD_THREE_TEXT_F2P, DAILY_MISSION_MARKER, EVENT_MISSION_MARKER):
        asset.ensure_template()
    title = BUILD_THREE_TEXT.image
    region = image[87:692, 253:393]
    scores = np.maximum.reduce([cv2.matchTemplate(cv2.cvtColor(region, cv2.COLOR_RGB2GRAY),
                                cv2.cvtColor(asset.image, cv2.COLOR_RGB2GRAY), cv2.TM_CCOEFF_NORMED)
                                for asset in (BUILD_THREE_TEXT, BUILD_THREE_TEXT_F2P)])
    candidates = []
    while scores.size:
        _, score, _, (x, y) = cv2.minMaxLoc(scores)
        if score < .90:
            break
        candidates.append((x + 253, y + 87))
        scores[max(0, y - 35):y + 36, :] = -1
    if len(candidates) > 1:
        raise ScriptError('Ambiguous duplicate Build 3 mission rows')
    if not candidates:
        return None
    x, y = candidates[0]
    row_top = y - 38
    if row_top < 87 or row_top + 142 > 692:
        return None
    # The marker is 37 pixels above the observed text and remains bound to it.
    marker = image[y - 39:y + 35, 175:197]
    matches = [float(np.max(cv2.matchTemplate(
        cv2.cvtColor(marker, cv2.COLOR_RGB2GRAY),
        cv2.cvtColor(asset.image, cv2.COLOR_RGB2GRAY), cv2.TM_CCOEFF_NORMED)))
        for asset in (DAILY_MISSION_MARKER, EVENT_MISSION_MARKER)]
    if max(matches) < .85:
        return None
    # Keep only the text. Extra background above/below these short digits made
    # a fresh 0/3 read as /3 when the animated port background changed.
    counter_area = (616, y + 37, 660, y + 55)
    return dict(row_top=row_top,
                title_area=(x, y, x + title.shape[1], y + title.shape[0]),
                counter_area=counter_area)


def read_build_mission(image):
    row = locate_build_mission(image)
    if row is None:
        return None
    counter_area = row['counter_area']
    raw = Ocr(counter_area, alphabet='0123456789/IDSB', letter=(255, 255, 255),
              threshold=128, name='EVENT_BUILD_THREE_RAW').ocr(image)
    current = parse_build_counter(raw)
    if current is None:
        return None
    return dict(row, current=current, total=3)


def choose_mission_pool(roster, owned, fallback='light'):
    """Only positive ownership of a known, nonempty banner permits fallback."""
    if fallback not in ('light', 'heavy', 'special'):
        raise ScriptError('Unsupported completed-banner fallback pool')
    if roster and set(roster).issubset(set(owned or ())):
        return fallback
    return 'event'


def remaining_mission_builds(current, total):
    """Unreadable or different mission counters never authorize a build."""
    if type(current) is not int or type(total) is not int or total != 3 or not 0 <= current <= total:
        return None
    return total - current


def mission_state_path(account, folder=STATE_FOLDER):
    if not re.fullmatch(r'[A-Za-z0-9_-]+', account):
        raise ScriptError('Invalid account name for Gacha mission state')
    return os.path.join(folder, f'{account}-missions.json')


def load_mission_state(account, folder=STATE_FOLDER):
    try:
        with open(mission_state_path(account, folder), encoding='utf-8') as stream:
            state = json.load(stream)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise ScriptError(f'Cannot safely read Gacha mission state: {exc}') from exc
    if not isinstance(state, dict) or any(not isinstance(item, dict) for item in state.values()):
        raise ScriptError('Invalid Gacha mission state')
    return state


def save_mission_state(account, state, folder=STATE_FOLDER):
    os.makedirs(folder, exist_ok=True)
    path = mission_state_path(account, folder)
    fd, temporary = tempfile.mkstemp(prefix='.gacha-missions-', suffix='.json', dir=folder)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(state, stream, indent=2, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


class GachaMissionObserver(GachaUI, Reward):
    def _capture_mission(self, record, tag):
        path = save_event_diagnostic(self.device, f'{self.config.config_name}_gacha_{tag}')
        if path:
            record['diagnostics'].append(path)

    def _wait_mission_page(self, page, predicate):
        settled = Timer(1, count=2).start()
        frames = 0
        for _ in self.loop(skip_first=False, timeout=8):
            if not self.ui_page_appear(page) or not predicate():
                frames = 0
                settled.reset()
                continue
            frames += 1
            if frames >= 2 and settled.reached():
                return True
        return False

    def _observe_mission_tab(self, record, name, upper):
        if not ensure_event_page(self, page_mission):
            return False
        if not self.reward_side_navbar_ensure(upper=upper):
            return False
        if not self._wait_mission_page(
                page_mission, lambda: self._reward_side_navbar.get_info(self) == (upper - 1, 0, 5)):
            return False
        self._capture_mission(record, f'missions_{name}')
        record['mission_tabs'].append(name)
        return True

    def _observe_event_pool(self, record):
        if not ensure_event_page(self, page_build):
            return False
        if not self.gacha_side_navbar_ensure(upper=1):
            return False
        if not self._wait_mission_page(
                page_build, lambda: self._gacha_side_navbar.get_active(self) == 0
                and self.match_template_color(BUILD_SUBMIT_ORDERS, offset=(3, 3), similarity=.85)):
            return False
        # Do not call gacha_goto_pool: its missing-event fallback is intended for
        # legacy fixed orders. Mission mode keeps Event until ownership is proven.
        self._capture_mission(record, 'build_pool_before_selection')
        navbar = self._construct_bottom_navbar
        if navbar.get_total(self) != 4:
            record['event_pool_available'] = False
            return False
        if not navbar.set(self, left=1):
            return False
        if not self._wait_mission_page(
                page_build, lambda: navbar.get_info(self) == (0, 0, 3)
                and self.match_template_color(BUILD_SUBMIT_ORDERS, offset=(3, 3), similarity=.85)):
            return False
        record['event_pool_available'] = True
        self._capture_mission(record, 'event_build_pool')
        return True

    def run(self, folder=STATE_FOLDER):
        # The normal scheduler restarts a closed game before any durable budget
        # is spent. No build queue collection or order confirmation is used here.
        self.ui_get_current_page()
        account = self.config.config_name
        date = (datetime.now() - server_time_offset()).date().isoformat()
        key = f'{date}:{OBSERVATION_VERSION}'
        state = load_mission_state(account, folder)
        if key in state:
            record = dict(state[key], reused=True)
            if record.get('status') == 'observing':
                record.update(status='blocked', reason='previous_observation_interrupted')
            logger.attr('GachaMission', record)
            return record
        record = dict(status='observing', reason='mission_reader_requires_evidence',
                      diagnostics=[], mission_tabs=[], mission_credit=None,
                      daily_target=3, preferred_pool='event',
                      fallback_pool=self.config.Gacha_FallbackPool,
                      banner_ownership_verified=False, orders_submitted=0,
                      returned_main=False, started=datetime.now().isoformat(timespec='seconds'))
        state[key] = record
        # Reserve before the first navigation; interruption cannot authorize more
        # observation clicks, and never changes a mission counter into a guess.
        save_mission_state(account, state, folder)
        try:
            for name, upper in (('daily', 4), ('event', 6)):
                if not self._observe_mission_tab(record, name, upper):
                    record.update(status='blocked', reason=f'{name}_mission_tab_unrecognized')
                    self._capture_mission(record, f'{name}_mission_blocked')
                    break
            else:
                if self._observe_event_pool(record):
                    record.update(status='observed', reason='mission_and_banner_evidence_required')
                else:
                    record.update(status='blocked', reason='event_build_pool_unrecognized')
                    self._capture_mission(record, 'event_build_pool_blocked')
        except Exception:
            record.update(status='blocked', reason='observation_exception')
            self._capture_mission(record, 'mission_observation_exception')
            raise
        finally:
            try:
                record['returned_main'] = ensure_event_page(self, page_main)
                if not record['returned_main']:
                    record.update(status='blocked', reason='main_return_unrecognized')
            finally:
                save_mission_state(account, state, folder)
        logger.attr('GachaMission', record)
        return record


class GachaMissionOrderObserver(GachaMissionObserver):
    """Read credited builds, then capture one count dialog without confirming.

    This is a separate evidence budget from mission/RateInfo observation. An
    interrupted opener cannot be repeated on restart. Actual submission remains
    disabled until its dialog and any UR-capacity warning have been observed.
    """
    def _period(self):
        return get_server_last_update(self.config.Scheduler_ServerUpdate).isoformat()

    def _read_mission_tab(self, record, name, upper):
        if not self._observe_mission_tab(record, name, upper):
            return None
        previous = None
        frames = 0
        settled = Timer(1, count=2).start()
        for _ in self.loop(skip_first=False, timeout=8):
            if (not self.ui_page_appear(page_mission)
                    or self._reward_side_navbar.get_info(self) != (upper - 1, 0, 5)):
                value = None
            else:
                value = read_build_mission(self.device.image)
            current = None if value is None else value['current']
            if current is None or current != previous:
                previous = current
                frames = 1 if current is not None else 0
                settled.reset()
                continue
            frames += 1
            if frames >= 2 and settled.reached():
                self._capture_mission(record, f'mission_credit_{name}')
                return dict(value, tab=name)
        return None

    def _open_order_dialog(self, record, reserve):
        from module.gacha.banner import EventBannerObserver
        banner = EventBannerObserver(config=self.config, device=self.device)
        if not banner.enter_event_pool():
            record.update(status='blocked', reason='current_event_pool_unrecognized')
            return
        # The counter was read in this invocation. A reset during navigation
        # invalidates it; no count or pool preference can override that.
        self.device.screenshot()
        if self._period() != record['period'] or not banner.on_event_pool():
            record.update(status='blocked', reason='mission_period_or_pool_changed')
            return
        record.update(status='opening', build_open_clicked=True)
        reserve()
        self.device.click(EVENT_BUILD_ORDERS)
        # No generic popup handler is called: even an apparent Confirm could be
        # a cost authorization or a different dialog. Capture unknown warnings.
        settled = Timer(1, count=2).start()
        previous = None
        frames = 0
        for _ in self.loop(skip_first=False, timeout=8):
            count_dialog = (self.match_template_color(BUILD_PLUS, offset=(60, 20), similarity=.85)
                            and self.match_template_color(BUILD_MINUS, offset=(60, 20), similarity=.85))
            kind = 'count' if count_dialog else ('warning' if not banner.on_event_pool() else None)
            if kind is None or kind != previous:
                previous = kind
                frames = 1 if kind else 0
                settled.reset()
                continue
            frames += 1
            if frames < 2 or not settled.reached():
                continue
            if kind == 'count':
                self._capture_mission(record, 'order_count_dialog')
                record.update(status='observed', reason='order_count_dialog_requires_review')
                return
            if kind == 'warning':
                self._capture_mission(record, 'order_warning_dialog')
                record.update(status='observed', reason='order_warning_requires_review')
                return
        self._capture_mission(record, 'order_opener_blocked')
        record.update(status='blocked', reason='order_dialog_not_observed')

    def run(self, folder=STATE_FOLDER):
        self.ui_get_current_page()
        account = self.config.config_name
        period = self._period()
        key = f'{period}:{CURRENT_EVENT}:{ORDER_OBSERVATION_VERSION}'
        state = load_mission_state(account, folder)
        if key in state:
            return dict(state[key], reused=True)
        record = dict(status='reading', reason='mission_credit_unknown', period=period,
                      event=CURRENT_EVENT, diagnostics=[], mission_tabs=[],
                      mission_credit=None, remaining=None, orders_submitted=0,
                      mission_reward_claimed=False, build_open_clicked=False,
                      returned_main=False)
        state[key] = record
        def reserve():
            save_mission_state(account, state, folder)
        reserve()
        try:
            credits = []
            for name, upper in (('daily', 4), ('event', 6)):
                value = self._read_mission_tab(record, name, upper)
                if value is not None:
                    credits.append(value)
            if not credits:
                record.update(status='blocked', reason='build_mission_not_positively_identified')
            elif len({item['current'] for item in credits}) != 1:
                record.update(status='blocked', reason='conflicting_build_mission_credit')
            else:
                record['mission_credit'] = credits[0]['current']
                record['remaining'] = remaining_mission_builds(record['mission_credit'], 3)
                record['credit_evidence'] = credits
                reserve()
                if record['remaining'] == 0:
                    record.update(status='complete', reason='three_builds_already_credited')
                else:
                    self._open_order_dialog(record, reserve)
        except Exception:
            record.update(status='blocked', reason='order_observation_exception')
            self._capture_mission(record, 'order_observation_exception')
            raise
        finally:
            # A newly opened, unreviewed dialog has no authorized cancel asset.
            # Leave it for scheduler game-only recovery, never tap behind it.
            if not record['build_open_clicked']:
                record['returned_main'] = ensure_event_page(self, page_main)
            reserve()
        logger.attr('GachaMissionOrder', record)
        return record
