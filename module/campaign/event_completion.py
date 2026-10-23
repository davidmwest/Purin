"""Bounded story and ordinary mission rewards around an event campaign pass.

The Mission All tab includes event clear/star missions. Event hub PT ladders and shops
are separate UIs and are deliberately not claimed without supported recognition assets.
"""
import json
import os
import re
import tempfile
from datetime import datetime
from time import monotonic

from module.base.timer import Timer
from module.campaign.event_completion_assets import (
    TEMPESTA_BECKONING_CHOICE, TEMPESTA_BECKONING_LANDING, TEMPESTA_BECKONING_PREPARE,
    TEMPESTA_BECKONING_TAB, TEMPESTA_CAMPAIGN, TEMPESTA_FEATURED_ENTRY, TEMPESTA_FOGGY_SUPPLY_TAB,
    TEMPESTA_HUB, TEMPESTA_HUB_HOME,
    TEMPESTA_HUB_INACTIVE,
    TEMPESTA_HUB_LANDING, TEMPESTA_MAIN_ENTRY, TEMPESTA_REWARDS_TAB, TEMPESTA_SUPPLIES,
    TEMPESTA_SIDE_CHOICE_BACK, TEMPESTA_SIDE_CHOICE_PAGE)
from module.combat.assets import GET_ITEMS_1, GET_ITEMS_2, GET_SHIP
from module.eventstory.eventstory import EventStory, ensure_event_page, ensure_event_running, save_event_diagnostic
from module.exception import GamePageUnknownError, ScriptError
from module.logger import logger
from module.reward.assets import MISSION_EMPTY, MISSION_MULTI, MISSION_SINGLE, MISSION_UNFINISH
from module.reward.reward import Reward
from module.ui.assets import GOTO_MAIN
from module.ui.page import page_event_list, page_main, page_mission, page_sp

SUPPORTED_STORY_EVENTS = frozenset(('event_20251023_cn', 'event_20261022_cn'))
MISSION_CLAIMS_MAX = 24
MISSION_TIMEOUT = 120
REWARD_OBSERVATION_EVENTS = frozenset(('event_20261022_cn',))
REWARD_OBSERVATION_DIR = './config/stars'
SUPPLIES_CLICKS_MAX = 3


def reward_observation_path(account, folder=REWARD_OBSERVATION_DIR):
    if not re.fullmatch(r'[A-Za-z0-9_-]+', account):
        raise ScriptError('Invalid account name for event reward observation')
    return os.path.join(folder, f'{account}-event-rewards.json')


def load_reward_observations(account, folder=REWARD_OBSERVATION_DIR):
    try:
        with open(reward_observation_path(account, folder), encoding='utf-8') as file:
            state = json.load(file)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise ScriptError(f'Cannot safely read event reward observation budget: {exc}') from exc
    if not isinstance(state, dict) or any(not isinstance(record, dict) for record in state.values()):
        raise ScriptError('Invalid event reward observation budget')
    return state


def save_reward_observations(account, state, folder=REWARD_OBSERVATION_DIR):
    os.makedirs(folder, exist_ok=True)
    path = reward_observation_path(account, folder)
    fd, temporary = tempfile.mkstemp(prefix='.event-rewards-', suffix='.json', dir=folder)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as file:
            json.dump(state, file, indent=2, sort_keys=True)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


class EventCompletion(Reward):
    def _inspect_event_hub(self, record, entry=TEMPESTA_MAIN_ENTRY):
        if not ensure_event_page(self, page_main):
            record.update(status='blocked', reason='hub_main_unrecognized')
            return
        settled = Timer(1, count=2).start()
        clicks = 0
        for _ in self.loop(skip_first=False, timeout=20):
            on_main = self.ui_page_appear(page_main)
            known = on_main and self.match_template_color(
                entry, offset=(3, 3), similarity=self.text_similarity)
            if not known and not clicks:
                settled.reset()
                continue
            if not settled.reached():
                continue
            if not on_main and clicks:
                record.update(status='observed', reason='event_hub_assets_required')
                return
            if not known:
                continue
            if clicks >= 2:
                record.update(status='blocked', reason='event_hub_did_not_open')
                return
            self.device.click(entry)
            clicks += 1
            record.update(hub_clicked=True, hub_clicks=clicks)
            settled = Timer(3, count=4).start()
        record.update(status='blocked', reason='event_hub_timeout' if clicks else 'event_logo_unrecognized')

    def _return_event_hub_main(self):
        """Exit only through the existing home template, never unknown hub controls."""
        from module.campaign.event_rewards_assets import FOGGY_SUPPLY_HOME, FOGGY_SUPPLY_PAGE

        settled = Timer(2, count=2)
        clicks = 0
        for _ in self.loop(skip_first=False, timeout=10):
            if self.ui_page_appear(page_main):
                return True
            if clicks >= 2 or not settled.reached():
                continue
            GOTO_MAIN.clear_offset()
            if self.match_template_color(GOTO_MAIN, offset=(30, 30)):
                self.device.click(GOTO_MAIN)
                clicks += 1
                settled.reset()
            elif ((self.match_template_color(TEMPESTA_HUB, offset=(3, 3), similarity=self.text_similarity)
                   or self.match_template_color(TEMPESTA_HUB_INACTIVE, offset=(3, 3),
                                                similarity=self.text_similarity))
                  and self.match_template_color(TEMPESTA_HUB_HOME, offset=(3, 3),
                                                similarity=self.text_similarity)):
                self.device.click(TEMPESTA_HUB_HOME)
                clicks += 1
                settled.reset()
            elif (self.match_template_color(FOGGY_SUPPLY_PAGE, offset=(3, 3), similarity=self.text_similarity)
                  and self.match_template_color(FOGGY_SUPPLY_HOME, offset=(3, 3), similarity=self.text_similarity)):
                self.device.click(FOGGY_SUPPLY_HOME)
                clicks += 1
                settled.reset()
            elif (self.match_template_color(TEMPESTA_SIDE_CHOICE_PAGE, offset=(3, 3), similarity=self.text_similarity)
                  and self.match_template_color(TEMPESTA_SIDE_CHOICE_BACK, offset=(3, 3), similarity=self.text_similarity)):
                self.device.click(TEMPESTA_SIDE_CHOICE_BACK)
                clicks += 1
                settled.reset()
        return False

    def observe_event_hub(self, event, folder=REWARD_OBSERVATION_DIR):
        """Open only the observed current-event logo and capture its hub once."""
        if event not in REWARD_OBSERVATION_EVENTS:
            return dict(status='unsupported', diagnostics=[])
        ensure_event_running(self)
        account = self.config.config_name
        state = load_reward_observations(account, folder=folder)
        key = event + ':hub'
        if key in state:
            record = dict(state[key])
            if record.get('status') == 'observing':
                record.update(status='blocked', reason='previous_hub_observation_interrupted')
            record['reused'] = True
            logger.attr('EventHubObservation', record)
            return record
        record = dict(status='observing', diagnostics=[], hub_clicked=False, returned_main=False,
                      started=datetime.now().isoformat(timespec='seconds'))
        state[key] = record
        save_reward_observations(account, state, folder=folder)
        try:
            self._inspect_event_hub(record)
            path = save_event_diagnostic(self.device, f'{account}_event_hub_{record["status"]}')
            if path:
                record['diagnostics'].append(path)
            else:
                record.update(status='blocked', reason='diagnostic_not_saved')
        except Exception:
            record.update(status='blocked', reason='hub_observation_exception')
            raise
        finally:
            try:
                record['returned_main'] = self._return_event_hub_main()
                if not record['returned_main']:
                    record['return_reason'] = 'main_return_unrecognized'
            finally:
                save_reward_observations(account, state, folder=folder)
        logger.attr('EventHubObservation', record)
        return record

    def observe_event_featured(self, event, folder=REWARD_OBSERVATION_DIR):
        """Inspect the observed middle EVENT portrait without acting in its destination.

        The event logo opens the campaign, and the right EVENT portrait opens
        ordinary activities. This separately budgeted route identifies the
        remaining featured-event UI before any reward control is supported.
        """
        if event not in REWARD_OBSERVATION_EVENTS:
            return dict(status='unsupported', diagnostics=[])
        ensure_event_running(self)
        account = self.config.config_name
        state = load_reward_observations(account, folder=folder)
        key = event + ':featured'
        if key in state:
            record = dict(state[key])
            if record.get('status') == 'observing':
                record.update(status='blocked', reason='previous_featured_observation_interrupted')
            record['reused'] = True
            logger.attr('EventFeaturedObservation', record)
            return record
        record = dict(status='observing', diagnostics=[], hub_clicked=False, returned_main=False,
                      entry='middle_event_portrait', started=datetime.now().isoformat(timespec='seconds'))
        state[key] = record
        save_reward_observations(account, state, folder=folder)
        try:
            self._inspect_event_hub(record, entry=TEMPESTA_FEATURED_ENTRY)
            path = save_event_diagnostic(self.device, f'{account}_event_featured_{record["status"]}')
            if path:
                record['diagnostics'].append(path)
            else:
                record.update(status='blocked', reason='diagnostic_not_saved')
        except Exception:
            record.update(status='blocked', reason='featured_observation_exception')
            raise
        finally:
            try:
                record['returned_main'] = self._return_event_hub_main()
                if not record['returned_main']:
                    record['return_reason'] = 'main_return_unrecognized'
            finally:
                save_reward_observations(account, state, folder=folder)
        logger.attr('EventFeaturedObservation', record)
        return record

    def _inspect_event_rewards_panel(self, record, tab_button=TEMPESTA_REWARDS_TAB, panel='rewards'):
        self._inspect_event_hub(record, entry=TEMPESTA_FEATURED_ENTRY)
        if record.get('status') != 'observed':
            return
        settled = Timer(1, count=2).start()
        clicks = 0
        for _ in self.loop(skip_first=False, timeout=20):
            hub = self.match_template_color(TEMPESTA_HUB, offset=(3, 3), similarity=self.text_similarity)
            tab = hub and self.match_template_color(tab_button, offset=(3, 3),
                                                    similarity=self.text_similarity)
            landing = hub and self.match_template_color(TEMPESTA_HUB_LANDING, offset=(3, 3),
                                                        similarity=self.text_similarity)
            if not clicks and not (hub and tab and landing):
                settled.reset()
                continue
            if not settled.reached():
                continue
            if clicks and not landing:
                record.update(status='observed', reason=f'{panel}_panel_assets_required')
                return
            if not (hub and tab and landing):
                continue
            if clicks >= 2:
                record.update(status='blocked', reason=f'{panel}_panel_did_not_open')
                return
            self.device.click(tab_button)
            clicks += 1
            record[f'{panel}_tab_clicks'] = clicks
            settled = Timer(3, count=4).start()
        record.update(status='blocked', reason=f'{panel}_panel_timeout' if clicks else f'{panel}_tab_unrecognized')

    def observe_event_rewards_panel(self, event, folder=REWARD_OBSERVATION_DIR):
        """Open the observed Rewards catalog and capture its controls once."""
        if event not in REWARD_OBSERVATION_EVENTS:
            return dict(status='unsupported', diagnostics=[])
        ensure_event_running(self)
        account = self.config.config_name
        state = load_reward_observations(account, folder=folder)
        key = event + ':pt_panel'
        if key in state:
            record = dict(state[key])
            if record.get('status') == 'observing':
                record.update(status='blocked', reason='previous_rewards_panel_observation_interrupted')
            record['reused'] = True
            logger.attr('EventRewardsPanelObservation', record)
            return record
        record = dict(status='observing', diagnostics=[], returned_main=False,
                      started=datetime.now().isoformat(timespec='seconds'))
        state[key] = record
        save_reward_observations(account, state, folder=folder)
        try:
            self._inspect_event_rewards_panel(record)
            path = save_event_diagnostic(self.device, f'{account}_event_rewards_panel_{record["status"]}')
            if path:
                record['diagnostics'].append(path)
            else:
                record.update(status='blocked', reason='diagnostic_not_saved')
        except Exception:
            record.update(status='blocked', reason='rewards_panel_observation_exception')
            raise
        finally:
            try:
                record['returned_main'] = self._return_event_hub_main()
                if not record['returned_main']:
                    record['return_reason'] = 'main_return_unrecognized'
            finally:
                save_reward_observations(account, state, folder=folder)
        logger.attr('EventRewardsPanelObservation', record)
        return record

    def observe_event_beckoning(self, event, folder=REWARD_OBSERVATION_DIR):
        """Inspect the observed Beckoning tab once, without acting in its panel."""
        return self._observe_event_tab(event, TEMPESTA_BECKONING_TAB, 'beckoning', folder=folder)

    def observe_event_foggy_supply(self, event, folder=REWARD_OBSERVATION_DIR):
        """Inspect the known Foggy Supply Station tab without buying or claiming."""
        return self._observe_event_tab(event, TEMPESTA_FOGGY_SUPPLY_TAB, 'foggy_supply', folder=folder)

    def observe_event_side_prepare(self, event, folder=REWARD_OBSERVATION_DIR):
        """Capture Beckoning's mission entrance without claiming or spending."""
        return self._observe_event_tab(event, TEMPESTA_BECKONING_TAB, 'beckoning_prepare', folder=folder,
                                       side_entry=TEMPESTA_BECKONING_PREPARE)

    def observe_event_side_choice(self, event, folder=REWARD_OBSERVATION_DIR):
        """Capture Beckoning's story entrance without selecting or unlocking."""
        return self._observe_event_tab(event, TEMPESTA_BECKONING_TAB, 'beckoning_choice', folder=folder,
                                       side_entry=TEMPESTA_BECKONING_CHOICE)

    def _inspect_event_side_story(self, record, entry, panel):
        self._inspect_event_rewards_panel(record, tab_button=TEMPESTA_BECKONING_TAB, panel='beckoning')
        if record.get('status') != 'observed':
            return
        settled = Timer(1, count=2).start()
        clicks = 0
        for _ in self.loop(skip_first=False, timeout=20):
            landing = self.match_template_color(TEMPESTA_BECKONING_LANDING, offset=(3, 3),
                                                similarity=self.text_similarity)
            known = landing and all(self.match_template_color(button, offset=(3, 3),
                                                              similarity=self.text_similarity)
                                    for button in (TEMPESTA_HUB_INACTIVE, TEMPESTA_HUB_HOME, entry))
            if not clicks and not known:
                settled.reset()
                continue
            if not settled.reached():
                continue
            if clicks and not landing:
                record.update(status='observed', reason=f'{panel}_assets_required')
                return
            if not known:
                continue
            if clicks >= 2:
                record.update(status='blocked', reason=f'{panel}_did_not_open')
                return
            self.device.click(entry)
            clicks += 1
            record[f'{panel}_clicks'] = clicks
            settled = Timer(3, count=4).start()
        record.update(status='blocked', reason=f'{panel}_timeout' if clicks else f'{panel}_unrecognized')

    def _observe_event_tab(self, event, tab, panel, folder=REWARD_OBSERVATION_DIR, side_entry=None):
        if event not in REWARD_OBSERVATION_EVENTS:
            return dict(status='unsupported', diagnostics=[])
        ensure_event_running(self)
        account = self.config.config_name
        state = load_reward_observations(account, folder=folder)
        key = event + ':' + panel
        if key in state:
            record = dict(state[key])
            if record.get('status') == 'observing':
                record.update(status='blocked', reason=f'previous_{panel}_observation_interrupted')
            record['reused'] = True
            logger.attr(f'EventTabObservation.{panel}', record)
            return record
        record = dict(status='observing', diagnostics=[], returned_main=False,
                      started=datetime.now().isoformat(timespec='seconds'))
        state[key] = record
        save_reward_observations(account, state, folder=folder)
        try:
            if side_entry is None:
                self._inspect_event_rewards_panel(record, tab_button=tab, panel=panel)
            else:
                self._inspect_event_side_story(record, entry=side_entry, panel=panel)
            path = save_event_diagnostic(self.device, f'{account}_event_{panel}_{record["status"]}')
            if path:
                record['diagnostics'].append(path)
            else:
                record.update(status='blocked', reason='diagnostic_not_saved')
        except Exception:
            record.update(status='blocked', reason=f'{panel}_observation_exception')
            raise
        finally:
            try:
                record['returned_main'] = self._return_event_hub_main()
                if not record['returned_main']:
                    record['return_reason'] = 'main_return_unrecognized'
            finally:
                save_reward_observations(account, state, folder=folder)
        logger.attr(f'EventTabObservation.{panel}', record)
        return record

    def observe_event_main(self, event, folder=REWARD_OBSERVATION_DIR):
        """Capture one fresh recognized main screen to identify the event hub entry."""
        if event not in REWARD_OBSERVATION_EVENTS:
            return dict(status='unsupported', diagnostics=[])
        ensure_event_running(self)
        account = self.config.config_name
        state = load_reward_observations(account, folder=folder)
        key = event + ':main'
        if key in state:
            record = dict(state[key])
            if record.get('status') == 'observing':
                record.update(status='blocked', reason='previous_main_observation_interrupted')
            record['reused'] = True
            logger.attr('EventMainObservation', record)
            return record
        record = dict(status='observing', diagnostics=[],
                      started=datetime.now().isoformat(timespec='seconds'))
        state[key] = record
        save_reward_observations(account, state, folder=folder)
        try:
            record.update(status='blocked', reason='main_page_unrecognized')
            if ensure_event_page(self, page_main):
                for _ in self.loop(skip_first=False, timeout=5):
                    if not self.ui_page_appear(page_main):
                        continue
                    path = save_event_diagnostic(self.device, f'{account}_event_main_observe')
                    if path:
                        record['diagnostics'].append(path)
                        record.update(status='observed', reason='event_hub_entry_assets_required')
                    else:
                        record.update(reason='diagnostic_not_saved')
                    break
        except Exception:
            record.update(status='blocked', reason='main_observation_exception')
            raise
        finally:
            save_reward_observations(account, state, folder=folder)
        logger.attr('EventMainObservation', record)
        return record

    def _inspect_event_supplies(self, record):
        if not ensure_event_page(self, page_sp):
            record.update(status='blocked', reason='campaign_page_unrecognized')
            return
        # Fresh frames and a short stable-page interval avoid acting on the
        # cached pre-navigation frame. Every retry rechecks both known labels.
        settled = Timer(1, count=2).start()
        clicked = 0
        for _ in self.loop(skip_first=False, timeout=20):
            known = (self.ui_page_appear(page_sp)
                     and self.match_template_color(TEMPESTA_CAMPAIGN, offset=(3, 3),
                                                   similarity=self.text_similarity)
                     and self.match_template_color(TEMPESTA_SUPPLIES, offset=(3, 3),
                                                   similarity=self.text_similarity))
            if not known and not clicked:
                settled.reset()
                continue
            if not settled.reached():
                continue
            if not known:
                if clicked:
                    record.update(status='observed', reason='supplies_panel_assets_required')
                    return
                continue
            if clicked >= SUPPLIES_CLICKS_MAX:
                record.update(status='blocked', reason='supplies_did_not_open')
                return
            self.device.click(TEMPESTA_SUPPLIES)
            clicked += 1
            record.update(supplies_clicked=True, supplies_clicks=clicked)
            # Wait for a slow panel transition before deciding another known
            # entry click is necessary. Never click an unknown destination.
            settled = Timer(3, count=4).start()
        reason = 'supplies_panel_timeout' if clicked else 'supplies_label_unrecognized'
        record.update(status='blocked', reason=reason)

    def observe_event_supplies(self, event, folder=REWARD_OBSERVATION_DIR):
        """Open only the observed event Supplies control, once per account/event."""
        if event not in REWARD_OBSERVATION_EVENTS:
            return dict(status='unsupported', diagnostics=[])
        ensure_event_running(self)
        account = self.config.config_name
        state = load_reward_observations(account, folder=folder)
        # Separate from the earlier activity-list observation: that list did not
        # contain the current event hub, and spending it must not block Supplies.
        key = event + ':supplies'
        previous = state.get(key, {})
        # The first observed text-label tap left the known campaign unchanged.
        # Permit one new bounded emblem attempt after that specific failure.
        # Successful, interrupted, or otherwise blocked observations cannot retry.
        if (previous.get('status') == 'blocked' and previous.get('reason') == 'supplies_did_not_open'
                and previous.get('entry') != 'emblem'):
            key += ':emblem_retry'
        if key in state:
            record = dict(state[key])
            if record.get('status') == 'observing':
                record.update(status='blocked', reason='previous_supplies_observation_interrupted')
            record['reused'] = True
            logger.attr('EventSuppliesObservation', record)
            return record
        record = dict(status='observing', diagnostics=[], supplies_clicked=False, entry='emblem',
                      returned_main=False, started=datetime.now().isoformat(timespec='seconds'))
        state[key] = record
        save_reward_observations(account, state, folder=folder)
        try:
            self._inspect_event_supplies(record)
            path = save_event_diagnostic(self.device, f'{account}_supplies_{record["status"]}')
            if path:
                record['diagnostics'].append(path)
            else:
                record.update(status='blocked', reason='diagnostic_not_saved')
        except Exception:
            record.update(status='blocked', reason='supplies_observation_exception')
            raise
        finally:
            try:
                # Unknown panel controls are never guessed. Only known page
                # graph links can return to main; keep this fallback brief.
                record['returned_main'] = ensure_event_page(self, page_main, timeout=8, max_clicks=3)
                if not record['returned_main']:
                    record['return_reason'] = 'main_return_unrecognized'
            finally:
                save_reward_observations(account, state, folder=folder)
        logger.attr('EventSuppliesObservation', record)
        return record

    def observe_event_rewards(self, event, folder=REWARD_OBSERVATION_DIR):
        """Capture the recognized activity list once; never open an unknown event banner."""
        if event not in REWARD_OBSERVATION_EVENTS:
            return dict(status='unsupported', diagnostics=[])
        ensure_event_running(self)
        account = self.config.config_name
        state = load_reward_observations(account, folder=folder)
        if event in state:
            record = dict(state[event])
            if record.get('status') == 'observing':
                record.update(status='blocked', reason='previous_observation_interrupted')
            record['reused'] = True
            logger.attr('EventRewardObservation', record)
            return record

        record = dict(status='observing', diagnostics=[], returned_main=False,
                      started=datetime.now().isoformat(timespec='seconds'))
        state[event] = record
        save_reward_observations(account, state, folder=folder)
        try:
            if ensure_event_page(self, page_event_list):
                path = save_event_diagnostic(self.device, f'{account}_event_list_observe')
                if path:
                    record['diagnostics'].append(path)
                    record.update(status='observed', reason='event_banner_assets_required')
                else:
                    record.update(status='blocked', reason='diagnostic_not_saved')
            else:
                record.update(status='blocked', reason='event_list_unrecognized')
                path = save_event_diagnostic(self.device, f'{account}_event_list_blocked')
                if path:
                    record['diagnostics'].append(path)
        except Exception:
            record.update(status='blocked', reason='observation_exception')
            raise
        finally:
            try:
                record['returned_main'] = ensure_event_page(self, page_main)
                if not record['returned_main']:
                    record.update(status='blocked', reason='main_return_unrecognized')
                    path = save_event_diagnostic(self.device, f'{account}_event_list_return_blocked')
                    if path:
                        record['diagnostics'].append(path)
            finally:
                save_reward_observations(account, state, folder=folder)
        logger.attr('EventRewardObservation', record)
        return record

    def _event_mission_click(self):
        clicked = False
        clicks = 0
        click_interval = Timer(1, count=2)
        for _ in self.loop(timeout=15):
            on_mission = self.ui_page_appear(page_mission)
            if not on_mission:
                if clicked:
                    return True
                continue
            if self._reward_get_state() in (MISSION_EMPTY, MISSION_UNFINISH):
                return clicked
            if click_interval.reached():
                if clicks >= 3:
                    break
                if self.appear_then_click(MISSION_MULTI, offset=(20, 20)):
                    clicked = True
                    clicks += 1
                    click_interval.reset()
                    continue
                if self.match_template_color(MISSION_SINGLE, offset=(50, 200)):
                    self.device.click(MISSION_SINGLE)
                    clicked = True
                    clicks += 1
                    click_interval.reset()
        return False

    def _event_mission_receive(self):
        for _ in self.loop(timeout=20):
            if self.ui_page_appear(page_mission):
                state = self._reward_get_state()
                if state:
                    return state
            if self.appear_then_click(GET_ITEMS_1, offset=(30, 30), interval=1):
                continue
            if self.appear_then_click(GET_ITEMS_2, offset=(30, 30), interval=1):
                continue
            if self.appear_then_click(GET_SHIP, offset=(20, 20), interval=1):
                continue
            if self.handle_mission_popup_ack():
                continue
            if self.handle_vote_popup():
                continue
            if self.handle_story_skip():
                continue
            if self.handle_popup_confirm('MISSION_REWARD'):
                continue
        return 'timeout'

    def collect_event_missions(self):
        """Collect recognized ordinary mission claims; return false on an unknown screen."""
        if not ensure_event_page(self, page_main):
            save_event_diagnostic(self.device, 'mission_main_blocked')
            return False
        if not self.reward_mission_notice():
            logger.info('No pending mission rewards')
            return True
        if not ensure_event_page(self, page_mission):
            save_event_diagnostic(self.device, 'mission_page_blocked')
            return False
        if not self.reward_side_navbar_ensure(upper=1):
            save_event_diagnostic(self.device, 'mission_navbar_blocked')
            return False
        state = self._reward_wait_mission_list()
        deadline = monotonic() + MISSION_TIMEOUT
        for _ in range(MISSION_CLAIMS_MAX):
            if state in (MISSION_EMPTY, MISSION_UNFINISH):
                logger.info('Event mission reward collection verified')
                return True
            if state not in (MISSION_MULTI, MISSION_SINGLE) or monotonic() >= deadline:
                break
            self.device.stuck_record_clear()
            self.device.click_record_clear()
            self.interval_clear([GET_ITEMS_1, GET_ITEMS_2, MISSION_MULTI, MISSION_SINGLE, GET_SHIP])
            if not self._event_mission_click():
                break
            state = self._event_mission_receive()

        logger.warning(f'Event mission reward collection stopped at state: {state}')
        save_event_diagnostic(self.device, 'mission_claim_stopped')
        return False

    def collect_current_event_rewards(self, event):
        # The supported collector reuses the bounded event entry helpers.
        # Import lazily so ordinary event navigation has no import cycle.
        from module.campaign.event_rewards import EventRewards

        return EventRewards(config=self.config, device=self.device).run(event)

    def observe_event_strict_maid(self, event):
        from module.campaign.event_strict_maid import StrictMaidObserver

        return StrictMaidObserver(config=self.config, device=self.device).run(event)

    def ensure_completion_return(self, result):
        """Do not let a new unknown event destination reach campaign reservation."""
        if not any(isinstance(record, dict) and record.get('returned_main') is False
                   and not record.get('reused') for record in result.values()):
            return
        if not ensure_event_page(self, page_main, timeout=10, max_clicks=3):
            save_event_diagnostic(self.device, 'event_completion_return_unknown')
            raise GamePageUnknownError('Event completion could not return through recognized controls')

    def run(self, event, collect_rewards=True, complete_story=False):
        result = dict(story='disabled', rewards='disabled', event_hub_rewards='disabled')
        if collect_rewards:
            result['event_main_observation'] = self.observe_event_main(event)
            result['event_hub_observation'] = self.observe_event_hub(event)
            result['event_featured_observation'] = self.observe_event_featured(event)
            result['event_pt_observation'] = self.observe_event_rewards_panel(event)
            result['event_beckoning_observation'] = self.observe_event_beckoning(event)
            result['event_foggy_supply_observation'] = self.observe_event_foggy_supply(event)
            self.ensure_completion_return(result)
        # Story detection taps entrances, so gate before constructing its controller.
        if complete_story:
            if event in SUPPORTED_STORY_EVENTS:
                logger.info('CompleteStory enabled: bounded game restarts may skip story battles')
                story = EventStory(config=self.config, device=self.device)
                result['story'] = 'complete' if story.run(event=event, skip_battle=True) else 'failed'
                if event in REWARD_OBSERVATION_EVENTS:
                    result['event_side_prepare_observation'] = self.observe_event_side_prepare(event)
                    self.ensure_completion_return(result)
                    result['event_side_choice_observation'] = self.observe_event_side_choice(event)
                    self.ensure_completion_return(result)
                    result['event_strict_maid_observation'] = self.observe_event_strict_maid(event)
                    self.ensure_completion_return(result)
                    result['event_side_story'] = 'unsupported'
                    logger.warning('Side stories need supported destination assets; campaign story handled separately')
            else:
                logger.warning(f'Automatic event story is not supported for {event}')
                result['story'] = 'unsupported'
        if collect_rewards:
            result['event_reward_observation'] = self.observe_event_rewards(event)
            result['event_supplies_observation'] = self.observe_event_supplies(event)
            result['rewards'] = 'complete' if self.collect_event_missions() else 'failed'
            if event.startswith('event_'):
                collection = self.collect_current_event_rewards(event)
                result['event_hub_reward_collection'] = collection
                status = collection.get('status', 'failed')
                result['event_hub_rewards'] = status if status in ('complete', 'failed', 'unsupported') else 'failed'
            self.ensure_completion_return(result)
        logger.attr('EventCompletion', result)
        return result
