"""Bounded EX observation and one verified auto battle, with separate durable budgets."""
import json
import os
import re
import tempfile
from datetime import datetime
from time import monotonic

import numpy as np

from module.base.timer import Timer
from module.campaign.campaign_ui import ASIDE_SWITCH_20241219, MODE_SWITCH_20241219, CampaignUI
from module.campaign.event_completion import EventCompletion
from module.campaign.event_completion_assets import TEMPESTA_CAMPAIGN, TEMPESTA_EX_LOCKED
from module.campaign.event_extra_assets import (EX_DISABLED_FLEET_2, EX_DISABLED_SUBMARINE,
                                               EX_MAP_ATTACK, EX_MAP_COLUMNS, EX_MAP_TIME_LIMIT,
                                               EX_MAP_NO_TIME_LIMIT)
from module.combat.assets import AUTOMATION_ON, BATTLE_PREPARATION, DEFEAT_TIPS
from module.combat.combat import Combat
from module.eventstory.eventstory import ensure_event_page, ensure_event_running, ensure_event_switch, save_event_diagnostic
from module.exception import CampaignNameError, ScriptError
from module.handler.assets import IN_MAP
from module.logger import logger
from module.map.assets import (FLEET_1_ADVICE, FLEET_1_BAR, FLEET_1_CHOOSE, FLEET_1_CLEAR,
                               FLEET_1_HARD_SATIESFIED, FLEET_1_IN_USE, FLEET_2_ADVICE,
                               FLEET_2_BAR, FLEET_2_CHOOSE, FLEET_2_CLEAR,
                               FLEET_2_HARD_SATIESFIED, FLEET_2_IN_USE, FLEET_PREPARATION,
                               MAP_PREPARATION, MAP_PREPARATION_HARD, SUBMARINE_ADVICE,
                               SUBMARINE_BAR, SUBMARINE_CHOOSE, SUBMARINE_CLEAR,
                               SUBMARINE_HARD_SATIESFIED, SUBMARINE_IN_USE, WITHDRAW)
from module.map.map_fleet_preparation import FleetOperator
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
        return EventCompletion(config=self.config, device=self.device).observe_event_supplies(event)

    def _extra_capture(self, record, tag):
        path = save_event_diagnostic(self.device, f'{self.config.config_name}_ex_{tag}')
        if path:
            record['diagnostics'].append(path)

    def _inspect_extra(self, record):
        if not ensure_event_page(self, page_sp):
            return 'blocked', 'event_page_unrecognized'
        if not ensure_event_switch(self, MODE_SWITCH_20241219, 'combat'):
            return 'blocked', 'combat_selector_unrecognized'
        # The selector reports its previous value when the EX tab is locked.
        # Distinguish a prerequisite from an unreadable entrance, without tapping it.
        if self.appear(TEMPESTA_CAMPAIGN, offset=(3, 3)) and self.appear(
                TEMPESTA_EX_LOCKED, offset=(3, 3)):
            return 'locked', 'required_stage_not_cleared'
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
            # Supplies has its own observation budget. An earlier locked scout
            # must not prevent the subsequently implemented reward observation.
            record['event_reward_observation'] = self._observe_rewards(event)
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


def valid_extra_fleet_options(options, choose):
    """The detector numbers contours; require the complete six-row list first."""
    if set(options) != set(range(1, 7)):
        return False
    rows = [options[index] for index in range(1, 7)]
    if sum(bool(row.selected) for row in rows) != 1:
        return False
    if not 0 <= rows[0].area[1] - choose.button[3] <= 33:
        return False
    return all(33 <= right.area[1] - left.area[1] <= 66
               for left, right in zip(rows, rows[1:]))


class EventExtraRunner(EventExtraScout, Combat):
    """One direct EX sortie, using a verified common or stage-owned lineup.

    The observed hard selector permits only its own recommended Fleet 1.
    The observed EX board permits one settled Attack click, with no grid moves.
    """

    def _fleet_operators(self):
        return (
            FleetOperator(FLEET_1_CHOOSE, FLEET_1_ADVICE, FLEET_1_BAR, FLEET_1_CLEAR,
                          FLEET_1_IN_USE, FLEET_1_HARD_SATIESFIED, self),
            FleetOperator(FLEET_2_CHOOSE, FLEET_2_ADVICE, FLEET_2_BAR, FLEET_2_CLEAR,
                          FLEET_2_IN_USE, FLEET_2_HARD_SATIESFIED, self),
            FleetOperator(SUBMARINE_CHOOSE, SUBMARINE_ADVICE, SUBMARINE_BAR, SUBMARINE_CLEAR,
                          SUBMARINE_IN_USE, SUBMARINE_HARD_SATIESFIED, self),
        )

    def _clear_extra_fleet(self, fleet):
        if not fleet.allow():
            return True
        if fleet.is_hard():
            return False
        if not fleet.in_use():
            return True
        self.device.click(fleet._clear)
        for _ in self.loop(skip_first=False, timeout=5):
            if fleet.allow() and not fleet.in_use():
                return True
        return False

    def _extra_dropdown(self, fleet):
        fleet.update_options()
        if not fleet.options:
            self.device.click(fleet._choose)
        for _ in self.loop(skip_first=False, timeout=6):
            fleet.update_options()
            if fleet.options:
                return valid_extra_fleet_options(fleet.options, fleet._choose)
        return False

    def _close_extra_dropdown(self, fleet):
        self.device.click(fleet._choose)
        for _ in self.loop(skip_first=False, timeout=5):
            fleet.update_options()
            if not fleet.options:
                return fleet.in_use()
        return False

    def _extra_hard_secondary_disabled(self, secondary, submarine):
        # Absence of a button alone cannot prove a disabled slot. Require the
        # actual disabled-X rows, and reject any active secondary controls.
        return (self.match_template_color(EX_DISABLED_FLEET_2, offset=(3, 3))
                and self.match_template_color(EX_DISABLED_SUBMARINE, offset=(3, 3))
                and not secondary.allow() and not secondary.is_hard()
                and not submarine.allow() and not submarine.is_hard())

    def _prepare_extra_hard_fleet(self, record, fleet, secondary, submarine):
        if not fleet.allow() or not self._extra_hard_secondary_disabled(secondary, submarine):
            return False, 'hard_lineup_layout_unrecognized'
        self._extra_capture(record, 'hard_lineup_before_recommendation')
        # FleetOperator.advise has a 20-second/three-click cap and never clears
        # fleets. It also leaves an already-satisfied lineup unchanged.
        if fleet.advise() is not True:
            return False, 'hard_lineup_recommendation_failed'
        self.device.screenshot()
        if (not self.appear(FLEET_PREPARATION, offset=(20, 50))
                or not fleet.allow() or not fleet.is_hard()
                or fleet.is_hard_satisfied() is not True or not fleet.in_use()
                or not self._extra_hard_secondary_disabled(secondary, submarine)):
            self._extra_capture(record, 'hard_lineup_unverified')
            return False, 'hard_lineup_not_verified'
        record['fleet'] = 'stage_owned_recommended'
        self._extra_capture(record, 'hard_lineup_verified')
        return True, 'hard_lineup_verified'

    def _prepare_extra_fleet(self, record):
        # This job's fleet settings are isolated from ordinary Event farming.
        if (self.config.Fleet_Fleet1 != 6 or self.config.Fleet_Fleet2
                or self.config.Submarine_Fleet):
            return False, 'requires_fleet_6_only'
        # A missing oil read returns zero; never authorize a sortie from it.
        if self.get_oil() < max(100, self.config.StopCondition_OilLimit):
            return False, 'oil_floor_reached'
        fleet, secondary, submarine = self._fleet_operators()
        if fleet.is_hard():
            return self._prepare_extra_hard_fleet(record, fleet, secondary, submarine)
        if not fleet.allow():
            return False, 'common_fleet_selector_unrecognized'
        if not self._clear_extra_fleet(secondary) or not self._clear_extra_fleet(submarine):
            return False, 'secondary_fleet_not_cleared'
        if not self._extra_dropdown(fleet):
            return False, 'six_fleet_options_unrecognized'
        if not fleet.options[6].selected:
            self.device.click(fleet.options[6])
            for _ in self.loop(skip_first=False, timeout=5):
                fleet.update_options()
                if not fleet.options:
                    break
            else:
                return False, 'fleet_selection_not_closed'
            if not self._extra_dropdown(fleet):
                return False, 'fleet_selection_not_verifiable'
        # Re-open after a selection to prove the orange row is really number 6.
        if not fleet.options[6].selected:
            return False, 'fleet_6_not_selected'
        record['fleet'] = 6
        self._extra_capture(record, 'fleet_6_verified')
        if not self._close_extra_dropdown(fleet):
            return False, 'fleet_dropdown_not_closed'
        return True, 'fleet_6_verified'

    def _extra_map_appear(self):
        return (self.appear(IN_MAP, offset=(5, 5))
                and self.match_template_color(EX_MAP_COLUMNS, offset=(3, 3), similarity=.85)
                and any(self.match_template_color(button, offset=(3, 3), similarity=.85)
                        for button in (EX_MAP_TIME_LIMIT, EX_MAP_NO_TIME_LIMIT)))

    def _launch_extra_map(self, record, reserve):
        # The first map screenshot can precede drawing the fleets/enemies. Wait
        # for fresh, stable board images; never select a guessed grid coordinate.
        settled = Timer(2, count=3).start()
        previous = None
        stable_frames = 0
        for _ in self.loop(skip_first=False, timeout=12):
            if not (self._extra_map_appear()
                    and self.match_template_color(EX_MAP_ATTACK, offset=(3, 3), similarity=.85)):
                previous = None
                stable_frames = 0
                settled.reset()
                continue
            # The bottom frame settles with the camera, while ships and water
            # can keep animating indefinitely inside the board.
            board = self.device.image[625:646:2, 450:1120:2].astype(np.int16)
            if previous is None or np.mean(np.abs(board - previous)) > 2:
                stable_frames = 0
                settled.reset()
            else:
                stable_frames += 1
            previous = board.copy()
            if stable_frames >= 3 and settled.reached():
                self._extra_capture(record, 'settled_map_attack')
                record['map_observed'] = True
                record['map_attack_clicked'] = True
                reserve()  # Durable evidence precedes the only map launch click.
                self.device.click(EX_MAP_ATTACK)
                return True
        self._extra_capture(record, 'map_launch_unverified')
        return False

    def _return_extra_event(self, record):
        if not self.appear(IN_MAP, offset=(5, 5)):
            return ensure_event_page(self, page_sp)
        # A blocked or finished EX board is outside the page graph. Withdraw
        # only from this exact map, and confirm only our own recognized retreat.
        retreat_clicked = confirmed = False
        settled = Timer(2, count=3).start()
        for _ in self.loop(skip_first=False, timeout=15):
            if self.ui_page_appear(page_sp):
                return True
            if retreat_clicked and not confirmed and self.handle_popup_confirm('EX_WITHDRAW'):
                confirmed = True
                settled.reset()
                continue
            if self._extra_map_appear():
                if (not retreat_clicked and settled.reached()
                        and self.match_template_color(WITHDRAW, offset=(3, 3), similarity=.85)):
                    self._extra_capture(record, 'map_withdraw')
                    self.device.click(WITHDRAW)
                    retreat_clicked = True
                continue
            if (confirmed and settled.reached()
                    and any(self.appear(button, offset=(20, 50)) for button in (
                        MAP_PREPARATION, MAP_PREPARATION_HARD, FLEET_PREPARATION, BATTLE_PREPARATION))):
                return ensure_event_page(self, page_sp)
        self._extra_capture(record, 'map_return_unverified')
        return False

    def _direct_extra_battle(self, record, reserve):
        if not self.appear(FLEET_PREPARATION, offset=(20, 50)):
            return 'blocked', 'verified_fleet_preparation_disappeared'
        self.combat_won = self.combat_lost = False
        self.battle_status_click_interval = 0
        self.combat_auto_reset()
        # On a direct EX, this may be the final start. Persist before either
        # possible start button, including crashes between the click and loading.
        record['fleet_start_clicked'] = True
        reserve()
        self.device.click(FLEET_PREPARATION)
        battle_start_clicked = False
        result_seen = False
        combat_seen = False
        map_attack_clicked = False
        start_deadline = monotonic() + 30
        for _ in self.loop(skip_first=False, timeout=360):
            if self.ui_page_appear(page_sp):
                # Closing the fleet overlay exposes the entrance page before
                # the tactical board loads. That frame is not an ended sortie.
                if not combat_seen and not result_seen and monotonic() < start_deadline:
                    continue
                if self.combat_lost:
                    return 'lost', 'battle_lost'
                if self.combat_won:
                    return 'complete', 'battle_won'
                return 'blocked', 'returned_without_verified_result'
            if self.appear(IN_MAP, offset=(5, 5)):
                if combat_seen or result_seen:
                    break  # One battle only, including unreadable results.
                if not map_attack_clicked:
                    if not self._launch_extra_map(record, reserve):
                        return 'blocked', 'map_launch_not_verified'
                    map_attack_clicked = True
                    start_deadline = monotonic() + 30
                elif monotonic() >= start_deadline:
                    break
                continue
            if self.appear(BATTLE_PREPARATION, offset=(30, 20)):
                if combat_seen or result_seen:
                    # Loading already proves a possible sortie. A fast defeat
                    # may return here without a readable result; never retry it.
                    break
                if monotonic() >= start_deadline:
                    break
                if battle_start_clicked:
                    continue
                if self.handle_combat_automation_set(auto=True):
                    continue
                if not self.appear(AUTOMATION_ON):
                    continue
                self._extra_capture(record, 'auto_battle_start')
                record['battle_start_clicked'] = True
                reserve()
                self.device.click(BATTLE_PREPARATION)
                battle_start_clicked = True
                continue
            if self.is_combat_executing():
                if not combat_seen:
                    record['combat_seen'] = True
                    reserve()
                combat_seen = True
                self.handle_combat_auto('combat_auto')
                continue
            if self.is_combat_loading():
                if not combat_seen:
                    record['combat_seen'] = True
                    reserve()
                combat_seen = True
                continue
            if self.handle_combat_automation_confirm():
                continue
            if self.handle_battle_status():
                result_seen = True
                self._extra_capture(record, 'battle_result')
                continue
            if self.handle_get_items() or self.handle_exp_info():
                continue
            if self.appear_then_click(DEFEAT_TIPS, offset=(20, 20), interval=2):
                continue
            if result_seen and self.appear(FLEET_PREPARATION, offset=(20, 50)):
                break
            if not combat_seen and not result_seen and monotonic() >= start_deadline:
                break
        if self.combat_lost:
            return 'lost', 'battle_lost'
        if self.combat_won:
            return 'complete', 'battle_won'
        return 'blocked', 'battle_end_not_verified'

    def run(self, event, folder=EX_SCOUT_DIR):
        if event not in EX_SCOUT_EVENTS:
            return dict(status='unsupported', combat_attempted=False, diagnostics=[])
        ensure_event_running(self)
        account = self.config.config_name
        state = load_extra_state(account, folder=folder)
        # An earlier scout, including a locked one, cannot consume this budget.
        key = f'{event}/combat'
        saved = state.get(key)
        if saved and (saved.get('combat_attempted') or saved.get('status') != 'locked'):
            result = dict(saved, reused=True)
            if result.get('status') == 'preparing':
                result.update(status='blocked', reason='previous_preparation_interrupted')
            logger.attr('EXCombat', result)
            return result
        record = dict(status='preparing', combat_attempted=False, diagnostics=[],
                      started=datetime.now().isoformat(timespec='seconds'))
        state[key] = record
        save_extra_state(account, state, folder=folder)
        previous_entrances = self.config.STAGE_ENTRANCE

        def reserve():
            record.update(status='attempting', combat_attempted=True)
            save_extra_state(account, state, folder=folder)

        try:
            self.config.override(STAGE_ENTRANCE=['half', '20240725'])
            status, reason = self._inspect_extra(record)
            if status == 'observed' and reason == 'fleet_preparation_requires_supported_runner':
                prepared, reason = self._prepare_extra_fleet(record)
                if prepared:
                    status, reason = self._direct_extra_battle(record, reserve)
                else:
                    status = 'blocked'
            elif status == 'observed':
                status, reason = 'blocked', 'fleet_6_not_verified_before_battle_preparation'
            record.update(status=status, reason=reason)
            self._extra_capture(record, status)
            record['returned_event'] = self._return_extra_event(record)
            if not record['returned_event'] and status == 'complete':
                record.update(status='blocked', reason='battle_won_but_event_return_unverified')
        except Exception:
            record.update(status='blocked', reason='combat_exception')
            self._extra_capture(record, 'exception')
            raise
        finally:
            self.config.override(STAGE_ENTRANCE=previous_entrances)
            save_extra_state(account, state, folder=folder)
        logger.attr('EXCombat', record)
        return record
