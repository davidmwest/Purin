"""Inspect a supported event's EX entrance once, without claiming a combat attempt.

EX currently has no CampaignMap in this event. This scout records the recognized
entrance and preparation UI so a battle runner can be implemented from evidence.
It never clicks a fleet/battle start button or retries a lost battle.
"""
import json
import os
import re
import tempfile
from datetime import datetime

from module.campaign.campaign_ui import ASIDE_SWITCH_20241219, MODE_SWITCH_20241219, CampaignUI
from module.campaign.event_completion import EventCompletion
from module.combat.assets import BATTLE_PREPARATION
from module.eventstory.eventstory import ensure_event_page, ensure_event_running, ensure_event_switch, save_event_diagnostic
from module.exception import CampaignNameError, ScriptError
from module.logger import logger
from module.map.assets import FLEET_PREPARATION, MAP_PREPARATION, MAP_PREPARATION_HARD
from module.ui.page import page_sp

EX_SCOUT_EVENTS = frozenset(('event_20261022_cn',))
EX_SCOUT_DIR = './config/stars'


def extra_state_path(config_name, folder=EX_SCOUT_DIR):
    if not re.fullmatch(r'[A-Za-z0-9_-]+', config_name):
        raise ScriptError('Invalid account name for EX state')
    return os.path.join(folder, f'{config_name}-ex.json')


def load_extra_state(config_name, folder=EX_SCOUT_DIR):
    try:
        with open(extra_state_path(config_name, folder), encoding='utf-8') as file:
            data = json.load(file)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        # An unreadable budget must not reset to zero and authorize another attempt.
        raise ScriptError(f'Cannot safely read EX inspection budget: {exc}') from exc
    if not isinstance(data, dict) or any(not isinstance(value, dict) for value in data.values()):
        raise ScriptError('Invalid EX inspection budget')
    return data


def save_extra_state(config_name, data, folder=EX_SCOUT_DIR):
    os.makedirs(folder, exist_ok=True)
    path = extra_state_path(config_name, folder)
    fd, temporary = tempfile.mkstemp(prefix='.ex-', suffix='.json', dir=folder)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as file:
            json.dump(data, file, indent=2, sort_keys=True)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


class EventExtraScout(CampaignUI):
    def _observe_rewards(self, event):
        return EventCompletion(config=self.config, device=self.device).observe_event_rewards(event)

    def _extra_capture(self, record, tag):
        path = save_event_diagnostic(self.device, f'{self.config.config_name}_ex_{tag}')
        if path:
            record['diagnostics'].append(path)

    def _inspect_extra(self, record):
        if not ensure_event_page(self, page_sp):
            return 'blocked', 'event_page_unrecognized'
        if not ensure_event_switch(self, MODE_SWITCH_20241219, 'combat'):
            return 'blocked', 'combat_selector_unrecognized'
        if not ensure_event_switch(self, ASIDE_SWITCH_20241219, 'ex'):
            return 'blocked', 'ex_selector_unrecognized'
        self.device.screenshot()
        self._extra_capture(record, 'entrance')

        entrance = None
        for _ in self.loop(timeout=8):
            try:
                self._get_stage_name(self.device.image)
            except (IndexError, CampaignNameError):
                continue
            candidates = [button for name, button in self.stage_entrance.items()
                          if name == 'ex' or name.startswith('extra')]
            if self.campaign_chapter == 'ex_ex' and len(candidates) == 1:
                entrance = candidates[0]
                break
        if entrance is None:
            return 'blocked', 'ex_entrance_unrecognized'

        # Exactly one OCR-recognized EX entrance click; no retries on an unknown screen.
        record['entrance'] = entrance.name
        self.device.click(entrance)
        map_preparation_clicked = False
        for _ in self.loop(skip_first=False, timeout=15):
            if self.appear(BATTLE_PREPARATION, offset=(30, 20)):
                self._extra_capture(record, 'battle_preparation')
                return 'observed', 'battle_preparation_requires_supported_runner'
            if self.appear(FLEET_PREPARATION, offset=(20, 50)):
                self._extra_capture(record, 'fleet_preparation')
                return 'observed', 'fleet_preparation_requires_supported_runner'
            for button in (MAP_PREPARATION, MAP_PREPARATION_HARD):
                if self.appear(button, offset=(20, 20)):
                    if not map_preparation_clicked:
                        self._extra_capture(record, 'map_preparation')
                        self.device.click(button)
                        map_preparation_clicked = True
                    break
        return 'blocked', 'ex_preparation_unrecognized'

    def run(self, event, folder=EX_SCOUT_DIR):
        if event not in EX_SCOUT_EVENTS:
            return dict(status='unsupported', combat_attempted=False, diagnostics=[])
        ensure_event_running(self)
        account = self.config.config_name
        state = load_extra_state(account, folder=folder)
        if event in state:
            # An interrupted inspection stays spent. Only an explicit reviewed reset may retry.
            record = dict(state[event])
            if record.get('status') == 'inspecting':
                record.update(status='blocked', reason='previous_inspection_interrupted')
            record['reused'] = True
            logger.attr('EXInspection', record)
            return record

        record = dict(status='inspecting', combat_attempted=False, diagnostics=[],
                      started=datetime.now().isoformat(timespec='seconds'))
        state[event] = record
        # Persist before any navigation, so crashes cannot silently reset the budget.
        save_extra_state(account, state, folder=folder)
        previous_entrances = self.config.STAGE_ENTRANCE
        try:
            record['event_reward_observation'] = self._observe_rewards(event)
            record['diagnostics'].extend(record['event_reward_observation'].get('diagnostics', []))
            self.config.override(STAGE_ENTRANCE=['half', '20240725'])
            record['status'], record['reason'] = self._inspect_extra(record)
            self._extra_capture(record, record['status'])
        except Exception:
            record.update(status='blocked', reason='inspection_exception')
            self._extra_capture(record, 'exception')
            raise
        finally:
            self.config.override(STAGE_ENTRANCE=previous_entrances)
            save_extra_state(account, state, folder=folder)
        logger.attr('EXInspection', record)
        return record
