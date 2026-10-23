"""Inspect Strict Maid milestones and safely close the verified completed panel."""
from datetime import datetime

from module.base.button import Button
from module.base.timer import Timer
from module.campaign.event_completion import (
    EventCompletion, load_reward_observations, save_reward_observations)
from module.campaign.event_strict_maid import STRICT_MAID_TAB
from module.campaign.event_strict_maid_milestone_assets import (
    MILESTONE_CLOSE, MILESTONE_COMPLETED, MILESTONE_FINAL_PHASES,
    MILESTONE_GRAY_COLLECT, MILESTONE_HEADER, MILESTONE_POSSESSION)
from module.campaign.event_strict_maid_rewards import StrictMaidRewards
from module.campaign.event_strict_maid_rewards_assets import STRICT_MAID_HOME
from module.eventstory.eventstory import ensure_event_running, save_event_diagnostic
from module.logger import logger
from module.ui.page import page_main


STRICT_MAID_MILESTONES = Button(
    area=(1019, 465, 1121, 488), color=(232, 214, 193), button=(1032, 470, 1115, 486),
    file='./assets/en/event_completion/STRICT_MAID_MILESTONES_20261022.png',
    name='STRICT_MAID_MILESTONES')


class StrictMaidMilestones(StrictMaidRewards):
    def _milestone_modal(self):
        return all(self.match_template_color(button, offset=(3, 3), similarity=.9)
                   for button in (MILESTONE_HEADER, MILESTONE_POSSESSION, MILESTONE_CLOSE))

    def _milestone_completed(self):
        return self._milestone_modal() and all(
            self.match_template_color(button, offset=(3, 3), similarity=.9)
            for button in (*MILESTONE_FINAL_PHASES, *MILESTONE_COMPLETED, MILESTONE_GRAY_COLLECT))

    def _milestone_diagnostic(self, record, suffix):
        path = save_event_diagnostic(self.device, f'{self.config.config_name}_strict_maid_milestones_{suffix}')
        if path:
            record['diagnostics'].append(path)
        return path

    def _close_completed_modal(self, record, persist):
        """Only close the captured final-completed layout; never click Collect."""
        settled = Timer(1, count=2).start()
        click_wait = Timer(2, count=3)
        modal_frames = main_frames = 0
        for _ in self.loop(skip_first=False, timeout=25):
            if self._milestone_modal():
                self.device.stuck_record_clear()
                main_frames = 0
                if not self._milestone_completed():
                    modal_frames = 0
                    settled.reset()
                    continue
                modal_frames += 1
                if modal_frames < 3 or not settled.reached() or not click_wait.reached():
                    continue
                if record.get('close_clicks', 0) >= 2:
                    continue
                if not record.get('completed_verified'):
                    record.update(completed_verified=True, reward_state='final_completed', claims=0)
                    self._milestone_diagnostic(record, 'completed')
                record['close_clicks'] = record.get('close_clicks', 0) + 1
                persist()
                self.device.click(MILESTONE_CLOSE)
                modal_frames = 0
                click_wait.reset()
                settled.reset()
                continue
            modal_frames = 0
            # A previously persisted close may have completed before interruption.
            if not record.get('completed_verified'):
                settled.reset()
                continue
            if self.ui_page_appear(page_main):
                main_frames += 1
                if main_frames >= 3 and settled.reached():
                    record.update(status='complete', reason='final_milestones_already_completed',
                                  returned_main=True)
                    persist()
                    return True
                continue
            main_frames = 0
            settled.reset()
            if not self._strict_page():
                continue
            self.device.stuck_record_clear()
            if record.get('home_clicks', 0) >= 2 or not click_wait.reached():
                continue
            record['home_clicks'] = record.get('home_clicks', 0) + 1
            persist()
            self.device.click(STRICT_MAID_HOME)
            click_wait.reset()
        record.update(status='blocked', reason='completed_milestone_return_unrecognized', returned_main=False)
        self._milestone_diagnostic(record, 'blocked')
        persist()
        return False

    def resume(self, event, folder='./config/stars'):
        """Recover an already open exact milestone modal, without reopening it.

        No exact modal is a successful no-op. An observed milestone modal with
        unknown/earned controls is left untouched and reports failure.
        """
        if event != 'event_20261022_cn':
            return True
        ensure_event_running(self)
        self.device.screenshot()
        if not self._milestone_modal():
            return True
        account = self.config.config_name
        state = load_reward_observations(account, folder=folder)
        key = event + ':strict_maid_milestone_recovery'
        previous = state.get(key, {})
        record = previous if previous and not previous.get('returned_main') else dict(
            status='recovering', diagnostics=[], returned_main=False, claims=0,
            close_clicks=0, home_clicks=0,
            started=datetime.now().isoformat(timespec='seconds'))
        state[key] = record

        def persist():
            save_reward_observations(account, state, folder=folder)

        persist()
        return self._close_completed_modal(record, persist)

    def observe(self, event, folder='./config/stars'):
        if event != 'event_20261022_cn':
            return dict(status='unsupported', diagnostics=[], returned_main=True)
        ensure_event_running(self)
        account = self.config.config_name
        state = load_reward_observations(account, folder=folder)
        key = event + ':strict_maid_milestone_observation'
        previous = state.get(key)
        record = previous or dict(status='observing', diagnostics=[], clicks=0, returned_main=False,
                                  claims=0, close_clicks=0, home_clicks=0,
                                  started=datetime.now().isoformat(timespec='seconds'))
        state[key] = record

        def persist():
            save_reward_observations(account, state, folder=folder)

        persist()
        self.device.screenshot()
        if self._milestone_modal():
            self._close_completed_modal(record, persist)
            return dict(record, reused=bool(previous))
        if previous:
            return dict(record, reused=True)
        helper = EventCompletion(config=self.config, device=self.device)
        helper._inspect_event_rewards_panel(record, tab_button=STRICT_MAID_TAB, panel='strict_maid')
        if record.get('status') == 'observed':
            settled = Timer(2, count=3).start()
            after_click = Timer(3, count=4)
            for _ in self.loop(skip_first=False, timeout=15):
                if record['clicks']:
                    if after_click.reached():
                        self._close_completed_modal(record, persist)
                        break
                    continue
                if not (self._strict_page() and self.match_template_color(
                        STRICT_MAID_MILESTONES, offset=(3, 3), similarity=.9)):
                    settled.reset()
                    continue
                if not settled.reached():
                    continue
                record['clicks'] = 1
                persist()
                self.device.click(STRICT_MAID_MILESTONES)
                after_click.reset()
            else:
                record.update(status='blocked', reason='milestone_entry_unrecognized')
        path = save_event_diagnostic(self.device, f'{account}_strict_maid_milestones_{record["status"]}')
        if path:
            record['diagnostics'].append(path)
        else:
            record.update(status='blocked', reason='diagnostic_not_saved')
        # Unknown destinations retain a diagnostic and are never guessed closed.
        persist()
        logger.attr('StrictMaidMilestoneObservation', record)
        return record
