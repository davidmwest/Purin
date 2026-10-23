"""One bounded SP availability observation, without entering a sortie.

The separate journal is an observation budget only. It neither reads nor changes
EventStars' completed SP goal or EventExtra's combat budget. A red notification
or an entrance alone is not treated as proof of a remaining daily attempt.
"""
import json
import os
import re
import tempfile
from datetime import datetime

from module.base.timer import Timer
from module.campaign.campaign_ui import ASIDE_SWITCH_20241219, MODE_SWITCH_20241219, CampaignUI
from module.campaign.event_completion_assets import TEMPESTA_CAMPAIGN
from module.combat.assets import BATTLE_PREPARATION
from module.eventstory.eventstory import (
    ensure_event_page, ensure_event_running, ensure_event_switch, save_event_diagnostic)
from module.exception import CampaignNameError, ScriptError
from module.logger import logger
from module.handler.assets import IN_MAP
from module.map.assets import (
    FLEET_PREPARATION, MAP_PREPARATION, MAP_PREPARATION_CANCEL, MAP_PREPARATION_HARD)
from module.ui.assets import BACK_ARROW, GOTO_MAIN
from module.ui.page import page_main, page_sp

SP_OBSERVATION_EVENT = 'event_20261022_cn'
SP_OBSERVATION_VERSION = 1


def sp_observation_path(account, folder='./config/stars'):
    if not re.fullmatch(r'[A-Za-z0-9_-]+', account):
        raise ScriptError('Invalid SP observation account')
    return os.path.join(folder, f'{account}-sp-observation.json')


def load_sp_observations(account, folder='./config/stars'):
    try:
        with open(sp_observation_path(account, folder), encoding='utf-8') as file:
            state = json.load(file)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise ScriptError(f'Cannot safely read SP observation budget: {exc}') from exc
    if not isinstance(state, dict) or any(not isinstance(record, dict) for record in state.values()):
        raise ScriptError('Invalid SP observation budget')
    return state


def save_sp_observations(account, state, folder='./config/stars'):
    os.makedirs(folder, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.sp-observation-', suffix='.json', dir=folder)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as file:
            json.dump(state, file, indent=2, sort_keys=True)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, sp_observation_path(account, folder))
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


class EventSPObservation(CampaignUI):
    def _campaign_page(self):
        return (self.ui_page_appear(page_sp) and self.match_template_color(
            TEMPESTA_CAMPAIGN, offset=(3, 3), similarity=.85)
                and self._preparation()[0] is None
                and not self.appear(IN_MAP, offset=(5, 5)))

    def _sp_page(self):
        return (self._campaign_page()
                and MODE_SWITCH_20241219.get(self) == 'combat'
                and ASIDE_SWITCH_20241219.get(self) == 'sp')

    def _capture(self, record, tag):
        path = save_event_diagnostic(self.device, f'{self.config.config_name}_sp_observation_{tag}')
        if path:
            record['diagnostics'].append(path)
        return bool(path)

    def _wait_campaign(self, selected_sp=False):
        settled = Timer(1, count=2).start()
        for _ in self.loop(skip_first=False, timeout=8):
            if not (self._sp_page() if selected_sp else self._campaign_page()):
                settled.reset()
                continue
            if settled.reached():
                return True
        return False

    @staticmethod
    def _safe_entrance(entrance):
        try:
            left, top, right, bottom = entrance.button
            return 87 <= left < right <= 1151 and 117 <= top < bottom <= 636
        except (AttributeError, TypeError, ValueError):
            return False

    def _preparation(self):
        # These are recognition only. No Go, fleet choice or Start is an input.
        if self.appear(BATTLE_PREPARATION, offset=(20, 30)):
            return 'battle', BACK_ARROW
        for button in (FLEET_PREPARATION, MAP_PREPARATION_HARD, MAP_PREPARATION):
            if self.appear(button, offset=(20, 50)):
                return ('fleet' if button is FLEET_PREPARATION else 'map'), MAP_PREPARATION_CANCEL
        return None, None

    def _inspect_sp(self, record, persist):
        if not ensure_event_page(self, page_sp, timeout=20, max_clicks=6):
            return 'blocked', 'event_page_unrecognized'
        if not self._wait_campaign():
            return 'blocked', 'current_event_unrecognized'
        if not self._capture(record, 'campaign_before_selector'):
            return 'blocked', 'diagnostic_not_saved'
        record['phase'] = 'selecting_combat'
        persist()
        if not ensure_event_switch(self, MODE_SWITCH_20241219, 'combat', timeout=8, max_clicks=2):
            return 'blocked', 'combat_selector_unrecognized'
        if not self._wait_campaign():
            return 'blocked', 'current_event_disappeared'
        record['phase'] = 'selecting_sp'
        persist()
        if not ensure_event_switch(self, ASIDE_SWITCH_20241219, 'sp', timeout=8, max_clicks=2):
            return 'blocked', 'sp_selector_unrecognized'
        if not self._wait_campaign(selected_sp=True):
            return 'blocked', 'selected_sp_page_unrecognized'
        if not self._capture(record, 'selector'):
            return 'blocked', 'diagnostic_not_saved'
        record['selector_observed'] = True
        persist()

        previous = None
        settled = Timer(1, count=2).start()
        for _ in self.loop(skip_first=False, timeout=10):
            if not self._sp_page():
                previous = None
                settled.reset()
                continue
            try:
                self._get_stage_name(self.device.image)
            except (IndexError, CampaignNameError):
                previous = None
                settled.reset()
                continue
            candidates = self.stage_entrance
            entrance = candidates.get('sp')
            if (self.campaign_chapter != 'ex_sp' or set(candidates) != {'sp'}
                    or entrance is None or not self._safe_entrance(entrance)):
                previous = None
                settled.reset()
                continue
            geometry = tuple(entrance.button)
            if geometry != previous:
                previous = geometry
                settled.reset()
                continue
            if not settled.reached():
                continue
            record.update(phase='opening_entrance', entrance='sp', entrance_clicks=1)
            persist()  # A crash after this reservation must never open it again.
            self.device.click(entrance)
            break
        else:
            return 'blocked', 'sp_entrance_unrecognized'

        settled = Timer(1, count=2).start()
        previous = None
        for _ in self.loop(skip_first=False, timeout=15):
            preparation, _cancel = self._preparation()
            if not preparation:
                previous = None
                settled.reset()
                continue
            if previous != preparation:
                previous = preparation
                settled.reset()
                continue
            if not settled.reached():
                continue
            record['preparation'] = preparation
            if not self._capture(record, f'{preparation}_preparation'):
                return 'blocked', 'diagnostic_not_saved'
            return 'observed', 'sp_preparation_captured_availability_requires_review'
        self._capture(record, 'entrance_destination_unrecognized')
        return 'blocked', 'sp_entrance_destination_unrecognized'

    def _return_main(self, record, persist):
        settled = Timer(2, count=2).start()
        for _ in self.loop(skip_first=False, timeout=15):
            preparation, cancel = self._preparation()
            if preparation:
                # Cancel only the preparation opened by our one entrance tap.
                if (record['entrance_clicks'] != 1 or record['cancel_clicks'] >= 1
                        or not settled.reached()):
                    continue
                record['cancel_clicks'] = 1
                persist()
                self.device.click(cancel)
                settled.reset()
                continue
            if self.ui_page_appear(page_main):
                return True
            if not self._campaign_page():
                settled.reset()
                continue
            GOTO_MAIN.clear_offset()
            if (record['home_clicks'] < 2 and settled.reached()
                    and self.appear(GOTO_MAIN, offset=(5, 5))):
                record['home_clicks'] += 1
                persist()
                self.device.click(GOTO_MAIN)
                settled.reset()
        self._capture(record, 'main_return_unrecognized')
        return False

    def observe(self, event, folder='./config/stars'):
        account = self.config.config_name
        if event != SP_OBSERVATION_EVENT or account not in {'alas', 'f2p'}:
            return dict(status='unsupported', reason='unsupported_event_or_account', diagnostics=[],
                        returned_main=False, combat_attempted=False, entrance_clicks=0)
        ensure_event_running(self)  # Let the normal scheduler start a closed app before any budget.
        state = load_sp_observations(account, folder=folder)
        key = f'{event}:v{SP_OBSERVATION_VERSION}'
        if key in state:
            previous = state[key]
            if (previous.get('account') != account or previous.get('event') != event
                    or previous.get('version') != SP_OBSERVATION_VERSION):
                raise ScriptError('Invalid SP observation identity')
            return dict(previous, reused=True)
        record = dict(status='observing', phase='navigating', account=account, event=event,
                      version=SP_OBSERVATION_VERSION, started=datetime.now().isoformat(timespec='seconds'),
                      entrance_clicks=0, cancel_clicks=0, home_clicks=0,
                      combat_attempted=False, returned_main=False, diagnostics=[], availability='unverified')
        state[key] = record

        def persist():
            save_sp_observations(account, state, folder=folder)

        persist()  # Independent durable budget precedes every navigation control.
        old_entrance = self.config.STAGE_ENTRANCE
        try:
            self.config.override(STAGE_ENTRANCE=['half', '20240725'])
            record['status'], record['reason'] = self._inspect_sp(record, persist)
            record['returned_main'] = self._return_main(record, persist)
            if not record['returned_main']:
                record.update(status='blocked', reason='main_return_unrecognized')
        except Exception:
            record.update(status='blocked', reason='observation_exception')
            self._capture(record, 'exception')
            raise
        finally:
            self.config.override(STAGE_ENTRANCE=old_entrance)
            persist()
        logger.attr('SPObservation', record)
        return record
