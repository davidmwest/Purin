"""Inspect the separate Strict Maid milestone panel before supporting claims."""
from datetime import datetime

from module.base.button import Button
from module.base.timer import Timer
from module.campaign.event_completion import (
    EventCompletion, load_reward_observations, save_reward_observations)
from module.campaign.event_strict_maid import STRICT_MAID_TAB
from module.campaign.event_strict_maid_rewards import StrictMaidRewards
from module.eventstory.eventstory import ensure_event_running, save_event_diagnostic
from module.logger import logger


STRICT_MAID_MILESTONES = Button(
    area=(1019, 465, 1121, 488), color=(232, 214, 193), button=(1032, 470, 1115, 486),
    file='./assets/en/event_completion/STRICT_MAID_MILESTONES_20261022.png',
    name='STRICT_MAID_MILESTONES')


class StrictMaidMilestones(StrictMaidRewards):
    def observe(self, event, folder='./config/stars'):
        if event != 'event_20261022_cn':
            return dict(status='unsupported', diagnostics=[], returned_main=True)
        ensure_event_running(self)
        account = self.config.config_name
        state = load_reward_observations(account, folder=folder)
        key = event + ':strict_maid_milestone_observation'
        if key in state:
            return dict(state[key], reused=True)
        record = dict(status='observing', diagnostics=[], clicks=0, returned_main=False,
                      started=datetime.now().isoformat(timespec='seconds'))
        state[key] = record

        def persist():
            save_reward_observations(account, state, folder=folder)

        persist()
        helper = EventCompletion(config=self.config, device=self.device)
        helper._inspect_event_rewards_panel(record, tab_button=STRICT_MAID_TAB, panel='strict_maid')
        if record.get('status') == 'observed':
            settled = Timer(2, count=3).start()
            after_click = Timer(3, count=4)
            for _ in self.loop(skip_first=False, timeout=15):
                if record['clicks']:
                    if after_click.reached():
                        record.update(status='observed', reason='milestone_destination_assets_required')
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
        # A new popup is not a known exit. The audit caller must stop here;
        # no reward, purchase, or generic close is authorized by this capture.
        persist()
        logger.attr('StrictMaidMilestoneObservation', record)
        return record
