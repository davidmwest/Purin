"""Capture the current event's Rate Info without submitting a build.

The destination is observation only until its actual controls are known.
Unknown ownership always retains Event as the preferred pool.
"""
import json
import os
import re
import tempfile
from datetime import datetime

from module.base.timer import Timer
from module.eventstory.eventstory import ensure_event_page, ensure_event_running, save_event_diagnostic
from module.exception import ScriptError
from module.gacha.banner_assets import (EVENT_BANNER_DURATION, EVENT_BANNER_TEXT, EVENT_RATE_INFO,
                                      EVENT_BUILD_ORDERS)
from module.gacha.ui import GachaUI
from module.logger import logger
from module.ui.page import page_build, page_main

CURRENT_EVENT = 'event_20261022_cn'
BANNER_VERSION = 'rate-info-v1'


def banner_state_path(account, folder):
    if not re.fullmatch(r'[A-Za-z0-9_-]+', account):
        raise ScriptError('Invalid account name for Gacha banner state')
    return os.path.join(folder, f'{account}-banner.json')


def load_banner_state(account, folder):
    try:
        with open(banner_state_path(account, folder), encoding='utf-8') as stream:
            state = json.load(stream)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise ScriptError(f'Cannot safely read Gacha banner state: {exc}') from exc
    if not isinstance(state, dict) or any(not isinstance(item, dict) for item in state.values()):
        raise ScriptError('Invalid Gacha banner state')
    return state


def save_banner_state(account, state, folder):
    os.makedirs(folder, exist_ok=True)
    path = banner_state_path(account, folder)
    fd, temporary = tempfile.mkstemp(prefix='.gacha-banner-', suffix='.json', dir=folder)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(state, stream, indent=2, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


class EventBannerObserver(GachaUI):
    def _exact(self, button):
        return self.match_template_color(button, offset=(3, 3), similarity=.85)

    def on_event_pool(self):
        return (self.ui_page_appear(page_build)
                and self._gacha_side_navbar.get_active(self) == 0
                and self._construct_bottom_navbar.get_info(self) == (0, 0, 3)
                and all(self._exact(button) for button in (
                    EVENT_BUILD_ORDERS, EVENT_BANNER_DURATION, EVENT_BANNER_TEXT, EVENT_RATE_INFO)))

    def _wait(self, predicate, timeout=8):
        settled = Timer(1, count=2).start()
        frames = 0
        for _ in self.loop(skip_first=False, timeout=timeout):
            if not predicate():
                frames = 0
                settled.reset()
                continue
            frames += 1
            if frames >= 2 and settled.reached():
                return True
        return False

    def enter_event_pool(self):
        """Known navigation only; a missing Event tab never becomes Light."""
        if not ensure_event_page(self, page_build):
            return False
        # The legacy load helper does not recognize this event's observed
        # Build Orders artwork. Bound the known sidebar navigation normally,
        # then verify the actual current button on fresh settled frames below.
        if not self._gacha_side_navbar.set(self, upper=1):
            return False
        if not self._wait(lambda: self.ui_page_appear(page_build)
                          and self._gacha_side_navbar.get_active(self) == 0
                          and self._exact(EVENT_BUILD_ORDERS)):
            return False
        if self._construct_bottom_navbar.get_info(self)[1:] != (0, 3):
            return False
        if not self._construct_bottom_navbar.set(self, left=1):
            return False
        return self._wait(self.on_event_pool)

    def _capture(self, record, tag):
        path = save_event_diagnostic(self.device, f'{self.config.config_name}_gacha_banner_{tag}')
        if path:
            record['diagnostics'].append(path)
        return path

    def _return_main(self):
        if self.ui_page_appear(page_main):
            return True
        # An unobserved info panel may overlay a valid background page. Its
        # unknown Close/Confirm controls are never guessed. Only the fully
        # recognized, unobscured current pool permits its normal Home route.
        if self.on_event_pool():
            return ensure_event_page(self, page_main)
        return False

    def run(self, folder='./config/gacha'):
        ensure_event_running(self)
        account = self.config.config_name
        key = f'{CURRENT_EVENT}:{BANNER_VERSION}'
        state = load_banner_state(account, folder)
        if key in state:
            record = dict(state[key], reused=True)
            if record.get('status') == 'observing':
                record.update(status='blocked', reason='previous_banner_observation_interrupted')
            logger.attr('GachaBanner', record)
            return record
        record = dict(status='observing', reason='rate_info_requires_observation',
                      diagnostics=[], returned_main=False, reused=False,
                      info_clicks=0, orders_submitted=0, preferred_pool='event',
                      banner_ownership_verified=False,
                      started=datetime.now().isoformat(timespec='seconds'))
        state[key] = record
        save_banner_state(account, state, folder)
        try:
            if not self.enter_event_pool():
                record.update(status='blocked', reason='current_event_pool_unrecognized')
                self._capture(record, 'pool_blocked')
            else:
                self._capture(record, 'pool_before_info')
                # One durable reservation before the exact small info click.
                # Restarting cannot repeat it or open a count/Exchange dialog.
                record['info_clicks'] = 1
                save_banner_state(account, state, folder)
                self.device.click(EVENT_RATE_INFO)
                if self._wait(lambda: not self.on_event_pool(), timeout=12):
                    captured = self._capture(record, 'rate_info_destination')
                    record.update(status='observed' if captured else 'blocked',
                                  reason='rate_info_destination_requires_assets' if captured
                                  else 'rate_info_capture_failed')
                else:
                    record.update(status='blocked', reason='rate_info_destination_not_observed')
                    self._capture(record, 'info_click_blocked')
        except Exception:
            record.update(status='blocked', reason='banner_observation_exception')
            self._capture(record, 'exception')
            raise
        finally:
            try:
                record['returned_main'] = self._return_main()
            finally:
                save_banner_state(account, state, folder)
        logger.attr('GachaBanner', record)
        return record
