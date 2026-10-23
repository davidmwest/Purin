"""Observe one ordinary construction count dialog without placing orders.

The pool is established by Build's existing sidebar and bottom tabs. These
captures do not authorize construction: every pool still needs reviewed title
and cost evidence before the mission executor can use it.
"""
import json
import os
import re
import tempfile

from module.base.timer import Timer
from module.config.utils import get_server_last_update
from module.eventstory.eventstory import ensure_event_page, ensure_event_running, save_event_diagnostic
from module.exception import ScriptError
from module.gacha.banner_assets import EVENT_BUILD_ORDERS
from module.gacha.mission_submit_assets import (
    BATCH_BUILD_TITLE, COUNT_CANCEL, COUNT_CONFIRM, COUNT_HEADER, COUNT_MINUS, COUNT_PLUS)
from module.gacha.ui import GachaUI
from module.logger import logger
from module.ui.page import page_build, page_main

FALLBACK_OBSERVATION_VERSION = 'ordinary-count-observer-v1'
CURRENT_EVENT = 'event_20261022_cn'
POOL_RIGHT = {'light': 3, 'heavy': 2, 'special': 1}
# The observed Limited Build cost line is deliberately absent. The ordinary
# pool's title and costs are the evidence this observer is collecting.
SHARED_COUNT_WITNESSES = (
    COUNT_HEADER, BATCH_BUILD_TITLE, COUNT_MINUS, COUNT_PLUS, COUNT_CANCEL, COUNT_CONFIRM)


def fallback_state_path(account, folder='./config/gacha'):
    if not isinstance(account, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', account):
        raise ScriptError('Invalid account name for fallback observation')
    return os.path.join(folder, f'{account}-fallback-observation.json')


def load_fallback_state(account, folder='./config/gacha'):
    try:
        with open(fallback_state_path(account, folder), encoding='utf-8') as stream:
            state = json.load(stream)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise ScriptError(f'Cannot safely read fallback observation: {exc}') from exc
    if not isinstance(state, dict) or any(not isinstance(item, dict) for item in state.values()):
        raise ScriptError('Invalid fallback observation state')
    return state


def save_fallback_state(account, state, folder='./config/gacha'):
    os.makedirs(folder, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.gacha-fallback-', suffix='.json', dir=folder)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(state, stream, indent=2, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, fallback_state_path(account, folder))
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


class GachaFallbackObserver(GachaUI):
    def _period(self):
        return get_server_last_update(self.config.Scheduler_ServerUpdate).isoformat()

    def _exact(self, button):
        return self.match_template_color(button, offset=(3, 3), similarity=.85)

    def _capture(self, record, tag):
        path = save_event_diagnostic(
            self.device, f'{self.config.config_name}_gacha_{record["pool"]}_{tag}')
        if path:
            record['diagnostics'].append(path)
        return path

    def _build_sidebar(self):
        active, minimum, maximum = self._gacha_side_navbar.get_info(self)
        return self.ui_page_appear(page_build) and active == 0 and minimum == 0 and maximum in (3, 4)

    def _pool_info(self):
        info = self._construct_bottom_navbar.get_info(self)
        active, minimum, maximum = info
        if (type(active) is not int or minimum != 0 or maximum not in (2, 3)
                or not minimum <= active <= maximum):
            return None
        return info

    def _on_pool(self, pool):
        info = self._pool_info()
        return (self._build_sidebar() and info is not None
                and info[0] == info[2] - POOL_RIGHT[pool] + 1
                and self._exact(EVENT_BUILD_ORDERS))

    def _count_dialog(self):
        return all(self._exact(button) for button in SHARED_COUNT_WITNESSES)

    def _wait_pool(self, pool):
        settled, frames = Timer(1, count=2).start(), 0
        for _ in self.loop(skip_first=False, timeout=8):
            if not self._on_pool(pool):
                frames = 0
                settled.reset()
                continue
            frames += 1
            if frames >= 3 and settled.reached():
                return True
        return False

    def _enter_pool(self, pool):
        if not ensure_event_page(self, page_build):
            return False
        active, minimum, maximum = self._gacha_side_navbar.get_info(self)
        if (type(active) is not int or minimum != 0 or maximum not in (3, 4)
                or not minimum <= active <= maximum):
            return False
        # Upper 1 is Build; the Orders queue is never visited.
        if not self._gacha_side_navbar.set(self, upper=1, skip_first_screenshot=False):
            return False
        self.device.screenshot()
        if not self._build_sidebar() or self._pool_info() is None:
            return False
        if not self._construct_bottom_navbar.set(
                self, right=POOL_RIGHT[pool], skip_first_screenshot=False):
            return False
        return self._wait_pool(pool)

    def _recover_dialog(self, record, persist):
        """Capture the dialog and cancel it once; never press its confirmation."""
        settled = Timer(1, count=2).start()
        previous, frames = None, 0
        for _ in self.loop(skip_first=False, timeout=15):
            kind = ('count' if self._count_dialog()
                    else ('pool' if self._on_pool(record['pool']) else None))
            if kind is None or kind != previous:
                previous, frames = kind, 1 if kind else 0
                settled.reset()
                continue
            frames += 1
            if frames < 3 or not settled.reached():
                continue
            if kind == 'pool':
                record['dialog_open'] = False
                if record.get('count_captured'):
                    record.update(status='observed', reason='fallback_dialog_requires_review')
                else:
                    record.update(status='blocked', reason='count_dialog_not_observed')
                return True
            if not record.get('count_captured'):
                if not self._capture(record, 'count_dialog'):
                    record.update(status='blocked', reason='count_capture_failed')
                    return False
                record.update(count_captured=True, status='observed', reason='fallback_dialog_requires_review')
                persist()
            if record['cancel_clicks']:
                continue
            # This exact Cancel is shared with the captured count dialog. No
            # generic popup confirmation or receipt handler is used here.
            record['cancel_clicks'] = 1
            persist()
            self.device.click(COUNT_CANCEL)
            previous, frames = None, 0
            settled.reset()
        record.update(status='blocked', reason='fallback_dialog_return_unverified')
        self._capture(record, 'dialog_blocked')
        return False

    def run(self, pool='light', folder='./config/gacha'):
        if not isinstance(pool, str) or pool not in POOL_RIGHT:
            raise ScriptError('Unsupported ordinary fallback pool')
        ensure_event_running(self)
        account = self.config.config_name
        # One evidence budget per pool/event/version, including across reset.
        # A crash after opening must recover or stop, never open again tomorrow.
        key = f'{CURRENT_EVENT}:{pool}:{FALLBACK_OBSERVATION_VERSION}'
        state = load_fallback_state(account, folder)
        previous = state.get(key, {})
        for name in ('open_clicks', 'cancel_clicks'):
            if name in previous and (type(previous[name]) is not int or not 0 <= previous[name] <= 1):
                raise ScriptError('Invalid fallback observation budget')
        for name in ('dialog_open', 'count_captured', 'returned_main'):
            if name in previous and type(previous[name]) is not bool:
                raise ScriptError('Invalid fallback observation proof')
        if not isinstance(previous.get('diagnostics', []), list):
            raise ScriptError('Invalid fallback observation diagnostics')
        if (previous.get('pool', pool) != pool or previous.get('event', CURRENT_EVENT) != CURRENT_EVENT
                or type(previous.get('orders_submitted', 0)) is not int
                or previous.get('orders_submitted', 0) != 0
                or previous.get('cancel_clicks', 0) > previous.get('open_clicks', 0)):
            raise ScriptError('Invalid fallback observation scope')
        if previous.get('count_captured') and previous.get('returned_main'):
            return dict(previous, reused=True)
        record = dict(status='observing', reason='fallback_pool_requires_capture', pool=pool,
                      period=self._period(), event=CURRENT_EVENT, open_clicks=0, cancel_clicks=0,
                      orders_submitted=0, dialog_open=False, count_captured=False,
                      returned_main=False, diagnostics=[])
        record.update(previous)
        state[key] = record

        def persist():
            save_fallback_state(account, state, folder)

        persist()
        try:
            if record['open_clicks']:
                self._recover_dialog(record, persist)
            elif not self._enter_pool(pool):
                record.update(status='blocked', reason='fallback_pool_navigation_unverified')
                self._capture(record, 'pool_blocked')
            else:
                self._capture(record, 'pool')
                self.device.screenshot()
                if not self._on_pool(pool) or self._period() != record['period']:
                    record.update(status='blocked', reason='fallback_pool_or_period_changed')
                else:
                    record.update(open_clicks=1, dialog_open=True)
                    persist()
                    if self._period() == record['period']:
                        self.device.click(EVENT_BUILD_ORDERS)
                        self._recover_dialog(record, persist)
                    else:
                        record.update(status='blocked', reason='period_changed_before_dialog')
            self._capture(record, record['status'])
        except Exception:
            record.update(status='blocked', reason='fallback_observation_exception')
            self._capture(record, 'exception')
            raise
        finally:
            # A still-open or unknown dialog is evidence for review, never an
            # invitation to navigate behind it or flush the construction queue.
            if not record['dialog_open']:
                record['returned_main'] = ensure_event_page(self, page_main)
            if not record['returned_main']:
                record.update(status='blocked', reason='fallback_main_return_unverified')
            persist()
        logger.attr('GachaFallbackObservation', record)
        return record
