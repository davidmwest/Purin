"""Capture the current event's Rate Info without submitting a build.

The destination is observation only until its actual controls are known.
Unknown ownership always retains Event as the preferred pool.
"""
import json
import os
import re
import tempfile
from datetime import datetime, timedelta

import numpy as np

from module.base.timer import Timer
from module.base.utils import random_rectangle_point
from module.eventstory.eventstory import ensure_event_page, ensure_event_running, save_event_diagnostic
from module.exception import ScriptError
from module.gacha.banner_assets import (EVENT_BANNER_DURATION, EVENT_BANNER_DURATION_F2P,
                                      EVENT_BANNER_TEXT, EVENT_RATE_INFO, EVENT_BUILD_ORDERS,
                                      EVENT_RATE_INFO_HEADER, EVENT_BUILD_RATES_TITLE,
                                      EVENT_RATE_INFO_CLOSE, EVENT_RATE_INFO_CONFIRM)
from module.gacha.ui import GachaUI
from module.logger import logger
from module.ui.page import page_build, page_main
from module.ui.scroll import Scroll

CURRENT_EVENT = 'event_20261022_cn'
BANNER_VERSION = 'rate-info-v2'
# Reviewed against Purin's complete two-page Build Rates capture at 01:25 on
# 2026-10-23. This is the event's advertised limited roster, not a dock census
# or a claim that every ordinary ship in the construction pool is owned.
ROSTER_VERSION = 'tempesta-20261022-en-v1'
CURRENT_BANNER_NAMES = ('Merkuriy', 'Selimiye', 'Járnbarðinn', 'Ormrinn Langi')
OWNERSHIP_MAX_AGE = timedelta(hours=24)
CURRENT_EVENT_START = datetime(2026, 10, 22)


def current_banner_roster():
    return dict(event=CURRENT_EVENT, version=ROSTER_VERSION,
                names=list(CURRENT_BANNER_NAMES), complete=True,
                source='reviewed_complete_build_rates_20261023_0125')


def inventory_banner_ownership(account, folder='./config/gacha', now=None):
    """Positive retained copies read in this account's recent inventory only.

    A partial scan can prove an individually read ship exists; neither its
    completeness nor an old carried row can prove ownership. Re-read the
    source on every decision so a cached positive never outlives its proof.
    """
    if not isinstance(account, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', account):
        raise ScriptError('Invalid account name for Gacha ownership proof')
    now = now or datetime.now()
    root = os.path.join(os.path.dirname(os.path.normpath(os.fspath(folder))), 'inventory')
    names, sources = set(), []
    for suffix in ('.json', '.partial.json'):
        path = os.path.join(root, f'{account}{suffix}')
        try:
            with open(path, encoding='utf-8') as stream:
                snapshot = json.load(stream)
            if (not isinstance(snapshot, dict)
                    or snapshot.get('account', account) != account
                    or snapshot.get('config_name', account) != account
                    or not isinstance(snapshot.get('ships'), list)):
                continue
            stamp = datetime.fromisoformat(snapshot['time'])
            if stamp.tzinfo is not None:
                stamp = stamp.astimezone().replace(tzinfo=None)
            if not CURRENT_EVENT_START <= stamp <= now or now - stamp > OWNERSHIP_MAX_AGE:
                continue
        except (OSError, ValueError, TypeError, KeyError):
            continue
        positive = set()
        for ship in snapshot['ships']:
            if (not isinstance(ship, dict) or ship.get('name_whole') is not True
                    or ship.get('locked') is not True
                    or type(ship.get('level')) is not int or ship['level'] < 1
                    or type(ship.get('missed', 0)) is not int or ship.get('missed', 0) != 0
                    or ship.get('carried', False)):
                continue
            name = ship.get('name')
            if isinstance(name, str) and name in CURRENT_BANNER_NAMES:
                positive.add(name)
        if positive:
            names.update(positive)
            sources.append(dict(path=path, time=stamp.isoformat(timespec='seconds'),
                                positive_exact_names=sorted(positive)))
    return dict(account=account, event=CURRENT_EVENT, roster_version=ROSTER_VERSION,
                positive_exact_names=sorted(names), sources=sources)


def resolve_mission_pool(account, fallback='light', folder='./config/gacha'):
    """Runtime pool choice; unknown or expired ownership keeps Event."""
    return verified_banner_pool(current_banner_roster(),
                                inventory_banner_ownership(account, folder=folder),
                                account, fallback=fallback)


def verified_banner_pool(roster, owned, account, event=CURRENT_EVENT, fallback='light'):
    """A nonempty complete roster and exact account/event proof are required.

    Callers must pass explicit positive ownership evidence, never inferred
    absence, ambiguous OCR names, or the completeness of a cached dock census.
    """
    if fallback not in ('light', 'heavy', 'special'):
        raise ScriptError('Unsupported completed-banner fallback pool')
    if not isinstance(roster, dict) or not isinstance(owned, dict):
        return 'event'
    names = roster.get('names')
    version = roster.get('version')
    if (roster.get('complete') is not True or roster.get('event') != event
            or not isinstance(version, str) or not version
            or not isinstance(names, list) or not names
            or any(not isinstance(name, str) or not name for name in names)
            or owned.get('account') != account or owned.get('event') != event
            or owned.get('roster_version') != version):
        return 'event'
    evidence = owned.get('positive_exact_names')
    if not isinstance(evidence, list) or any(not isinstance(name, str) for name in evidence):
        return 'event'
    return fallback if set(names).issubset(evidence) else 'event'


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
    def _refresh_ownership(self, record, folder):
        roster = current_banner_roster()
        owned = inventory_banner_ownership(self.config.config_name, folder=folder)
        pool = verified_banner_pool(roster, owned, self.config.config_name,
                                    fallback=getattr(self.config, 'Gacha_FallbackPool', 'light'))
        record.update(roster=roster, ownership=owned, preferred_pool=pool,
                      banner_ownership_verified=pool != 'event')

    def _exact(self, button):
        return self.match_template_color(button, offset=(3, 3), similarity=.85)

    def on_event_pool(self):
        return (self.ui_page_appear(page_build)
                and self._gacha_side_navbar.get_active(self) == 0
                and self._construct_bottom_navbar.get_info(self) == (0, 0, 3)
                and (self._exact(EVENT_BANNER_DURATION) or self._exact(EVENT_BANNER_DURATION_F2P))
                and all(self._exact(button) for button in (
                    EVENT_BUILD_ORDERS, EVENT_BANNER_TEXT, EVENT_RATE_INFO)))

    def on_rate_info(self):
        return all(self._exact(button) for button in (
            EVENT_RATE_INFO_HEADER, EVENT_BUILD_RATES_TITLE,
            EVENT_RATE_INFO_CLOSE, EVENT_RATE_INFO_CONFIRM))

    def _rate_scroll(self):
        return Scroll(area=(930, 276, 936, 442), color=(245, 217, 90), name='EVENT_RATE_SCROLL')

    def _rate_position(self, scroll):
        mask = scroll.match_color(self)
        if not np.any(mask) or not 0 < scroll.length < scroll.total:
            return None
        position = scroll.cal_position(self)
        return float(position) if np.isfinite(position) else None

    def _observe_rates(self, record, persist):
        """Capture overlapping pages with one bounded scrollbar drag per page."""
        scroll = self._rate_scroll()
        for index in range(12):
            if not self.on_rate_info():
                record.update(status='blocked', reason='rate_info_page_unrecognized')
                self._capture(record, 'rates_unrecognized')
                return
            position = self._rate_position(scroll)
            if position is None or (index == 0 and position > .03):
                record.update(status='blocked', reason='rate_scroll_top_unverified')
                self._capture(record, 'rates_scroll_unverified')
                return
            captured = self._capture(record, f'rates_page_{index + 1:02}')
            if not captured:
                record.update(status='blocked', reason='rate_info_capture_failed')
                return
            record['rate_pages'].append(dict(path=captured, position=position))
            persist()
            if position >= .97:
                record.update(status='observed', reason='rate_list_requires_roster_validation',
                              rate_list_scrolled_complete=True)
                return
            target = min(1., position + .75 * scroll.length / (scroll.total - scroll.length))
            p1 = random_rectangle_point(scroll.position_to_screen(position, random_range=(-.005, .005)))
            p2 = random_rectangle_point(scroll.position_to_screen(target, random_range=(-.005, .005)))
            # Preserve each scroll reservation before input. No unbounded
            # Scroll.set()/next_page() loop can strand an unknown modal.
            record['rate_scrolls'] += 1
            persist()
            self.device.swipe(p1, p2, duration=(.35, .45), name='EVENT_RATE_SCROLL', distance_check=False)
            if not self._wait(lambda: self.on_rate_info()
                              and self._rate_position(scroll) is not None
                              and self._rate_position(scroll) > position + .005):
                record.update(status='blocked', reason='rate_scroll_did_not_advance')
                self._capture(record, 'rates_scroll_blocked')
                return
        record.update(status='blocked', reason='rate_page_budget_exhausted')

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
        # Close only the observed informational modal. Never use generic
        # Confirm/Cancel, which could refer to an order or Exchange dialog.
        for _ in range(2):
            if not self.on_rate_info():
                break
            self.device.click(EVENT_RATE_INFO_CLOSE)
            if self._wait(self.on_event_pool):
                break
        if self.ui_page_appear(page_main):
            return True
        # Unknown panels are untouched. Only the fully recognized current
        # pool permits its normal bounded Home route.
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
            self._refresh_ownership(record, folder)
            record['returned_main'] = self._return_main()
            state[key] = record
            save_banner_state(account, state, folder)
            logger.attr('GachaBanner', record)
            return record
        record = dict(status='observing', reason='rate_info_requires_observation',
                      diagnostics=[], returned_main=False, reused=False,
                      info_clicks=0, orders_submitted=0, preferred_pool='event',
                      banner_ownership_verified=False,
                      rate_pages=[], rate_scrolls=0, rate_list_scrolled_complete=False,
                      started=datetime.now().isoformat(timespec='seconds'))
        self._refresh_ownership(record, folder)
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
                if self._wait(self.on_rate_info, timeout=12):
                    self._observe_rates(record, lambda: save_banner_state(account, state, folder))
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
