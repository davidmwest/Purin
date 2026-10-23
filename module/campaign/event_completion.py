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
from module.campaign.event_completion_assets import TEMPESTA_CAMPAIGN, TEMPESTA_SUPPLIES
from module.combat.assets import GET_ITEMS_1, GET_ITEMS_2, GET_SHIP
from module.eventstory.eventstory import EventStory, ensure_event_page, ensure_event_running, save_event_diagnostic
from module.exception import ScriptError
from module.logger import logger
from module.reward.assets import MISSION_EMPTY, MISSION_MULTI, MISSION_SINGLE, MISSION_UNFINISH
from module.reward.reward import Reward
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

    def run(self, event, collect_rewards=True, complete_story=False):
        result = dict(story='disabled', rewards='disabled', event_hub_rewards='disabled')
        # Story detection taps entrances, so gate before constructing its controller.
        if complete_story:
            if event in SUPPORTED_STORY_EVENTS:
                logger.info('CompleteStory enabled: bounded game restarts may skip story battles')
                story = EventStory(config=self.config, device=self.device)
                result['story'] = 'complete' if story.run(event=event, skip_battle=True) else 'failed'
            else:
                logger.warning(f'Automatic event story is not supported for {event}')
                result['story'] = 'unsupported'
        if collect_rewards:
            result['event_reward_observation'] = self.observe_event_rewards(event)
            result['event_supplies_observation'] = self.observe_event_supplies(event)
            result['rewards'] = 'complete' if self.collect_event_missions() else 'failed'
            if event.startswith('event_'):
                result['event_hub_rewards'] = 'unsupported'
                logger.warning('Event hub PT ladder rewards need supported assets; ordinary mission claims handled')
        logger.attr('EventCompletion', result)
        return result
