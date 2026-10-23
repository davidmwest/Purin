"""Observe build-mission credit and the event pool before authorizing orders.

The first supported scheduler run only gathers evidence. A mission reader and
banner roster must be grounded in those captures before it can submit builds.
"""
import json
import os
import re
import tempfile
from datetime import datetime

from module.base.timer import Timer
from module.config.utils import server_time_offset
from module.eventstory.eventstory import ensure_event_page, save_event_diagnostic
from module.exception import ScriptError
from module.gacha.assets import BUILD_SUBMIT_ORDERS
from module.gacha.ui import GachaUI
from module.logger import logger
from module.reward.reward import Reward
from module.ui.page import page_build, page_main, page_mission

OBSERVATION_VERSION = 'missions-v1'
STATE_FOLDER = './config/gacha'


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
