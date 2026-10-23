"""Resumable star collection using the ordinary campaign map reader and battle plan.

The account and scheduler job each own their progress. A completed map is inspected
before any battle; missing escort/clear-all stars use MAP_CLEAR_ALL_THIS_TIME from
FastForwardHandler rather than another battle implementation.
"""
import json
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime

from module.base.timer import Timer
from module.campaign.run import CampaignRun
from module.campaign.stage import load_stage, stage_names
from module.config.config import TaskEnd
from module.exception import RequestHumanTakeover, ScriptEnd
from module.handler.fast_forward import to_map_file_name, to_map_input_name
from module.logger import logger


PROGRESS_DIR = './config/star_progress'


@dataclass(frozen=True)
class StageGoal:
    event: str
    stage: str
    mode: str = 'normal'
    goal: str = 'three_stars'

    @property
    def key(self):
        return f'{self.event}/{self.mode}/{self.stage.upper()}/{self.goal}'


def campaign_plan(first='1-1', last='16-4'):
    """An explicit upper bound; locked chapters stop the pass instead of being guessed."""
    def index(value):
        value = to_map_input_name(value)
        if not re.fullmatch(r'\d+-[1-4]', value):
            raise ValueError(f'Invalid normal campaign stage: {value}')
        chapter, stage = map(int, value.split('-'))
        return (chapter - 1) * 4 + stage - 1

    start, stop = index(first), index(last)
    if start < 0 or stop < start or stop >= 16 * 4:
        raise ValueError(f'Invalid campaign stage range: {first} to {last}')
    available = set(stage_names('campaign_main'))
    plan = [StageGoal('campaign_main', f'{i // 4 + 1}-{i % 4 + 1}') for i in range(start, stop + 1)]
    missing = [goal.stage for goal in plan if to_map_file_name(goal.stage) not in available]
    if missing:
        raise ValueError(f'Unsupported campaign stages: {missing}')
    return plan


def event_plan(event):
    """Only implemented combat stages; story nodes and unsupported EX are never tapped."""
    names = stage_names(event)
    order = {'a': 0, 'b': 1, 't': 2, 'c': 3, 'd': 4, 'ht': 5, 'sp': 6, 'ex': 7, 'extra': 7}
    def sort_key(name):
        match = re.fullmatch(r'(ht|[abcdt]|sp|ex|extra)(\d*)', name)
        return (order[match[1]], int(match[2] or 0))

    combat = [name for name in names if re.fullmatch(r'(ht|[abcdt]|sp|ex|extra)\d*', name)]
    plan = []
    for name in sorted(combat, key=sort_key):
        config = load_stage(event, name).Config()
        one_clear = (name in ('sp', 'ex', 'extra') or
                     getattr(config, 'MAP_IS_ONE_TIME_STAGE', False) or
                     all(getattr(config, f'STAR_REQUIRE_{n}', n) == 0 for n in (1, 2, 3)))
        mode = 'hard' if name.startswith(('c', 'd', 'ht')) else 'normal'
        plan.append(StageGoal(event, name.upper(), mode, 'clear' if one_clear else 'three_stars'))
    return plan


class StarProgress:
    def __init__(self, account, job, folder=PROGRESS_DIR):
        self.path = os.path.join(folder, f'{account}_{job}.json')
        try:
            with open(self.path, encoding='utf-8') as stream:
                self.data = json.load(stream)
            if not isinstance(self.data, dict):
                raise ValueError('Star progress must be an object')
        except FileNotFoundError:
            self.data = {}
        # Corrupt progress must not reset an exhausted one-clear/EX attempt.

    def record(self, goal):
        record = self.data.get(goal.key, {})
        if not isinstance(record, dict) or not isinstance(record.get('attempts', 0), int):
            raise ValueError(f'Invalid star progress for {goal.key}')
        return record

    def save(self, goal, **values):
        self.data[goal.key] = dict(self.record(goal), **values)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        temporary = self.path + '.tmp'
        with open(temporary, 'w', encoding='utf-8') as stream:
            json.dump(self.data, stream, indent=2, sort_keys=True)
        os.replace(temporary, self.path)


@contextmanager
def stage_overrides(config, goal):
    """Restore task values and override bindings even on scheduler interruption."""
    values = dict(Campaign_Name=goal.stage, Campaign_Event=goal.event, Campaign_Mode=goal.mode,
                  Campaign_UseClearMode=False, Campaign_UseAutoSearch=False, Campaign_Use2xBook=False,
                  ShipRotation_Enable=False, StopCondition_MapAchievement=(
                      # One-time SP preparation skips percentage stabilization;
                      # its initial full bar cannot prove a previous boss clear.
                      # Clear goals are certified by boss-victory evidence below.
                      'map_3_stars' if goal.goal == 'three_stars' else 'non_stop'),
                  StopCondition_RunCount=0, StopCondition_StageIncrease=False,
                  StopCondition_StageRetreat=0, StopCondition_RetreatRemain=0,
                  StopCondition_EndOnLoss=False, StopCondition_GetNewShip=False,
                  StopCondition_ReachLevel=0, StopCondition_ThenTask='', EventGeneral_PtLimit=0)
    previous = {key: getattr(config, key) for key in values}
    overridden = dict(config.overridden)
    try:
        config.override(**values)
        yield
    finally:
        config.overridden.clear()
        config.overridden.update(overridden)
        for key, value in previous.items():
            object.__setattr__(config, key, value)


class StageUnavailable(Exception):
    pass


class StageStarsRunner(CampaignRun):
    """One bounded attempt, shared by campaign and event jobs."""
    inspect_only = False
    goal_complete = False
    boss_cleared = False

    def check_stage_morale(self, campaign, check_reduce):
        battle = campaign._map_battle
        config = getattr(campaign, 'config', None)
        if not getattr(config, 'MAP_CLEAR_ALL_THIS_TIME', False):
            return check_reduce(battle)
        # _map_battle is when the boss spawns, not the number of fights needed
        # to clear every escort and siren before it. Spawn rows are increments.
        # Before map_init, spawn_data can still select a previous clearing-mode
        # loop; full clears require the original, unlooped stage spawns.
        battle = max(battle, sum(row.get(kind, 0) for row in campaign.MAP._spawn_data
                                 for kind in ('enemy', 'siren', 'boss')))
        fleet_1, fleet_2 = config.Fleet_Fleet1, config.Fleet_Fleet2
        if fleet_1 and not fleet_2:
            return check_reduce(battle, fleet_order='fleet1_all_fleet2_standby')
        if fleet_2 and not fleet_1:
            return check_reduce(battle, fleet_order='fleet1_standby_fleet2_all')
        return check_reduce(battle)

    def load_campaign(self, name, folder='campaign_main'):
        loaded = super().load_campaign(name, folder)
        if not loaded:
            return loaded
        campaign = self.campaign
        original_stop = campaign.triggered_map_stop
        original_additional = campaign.handle_chapter_additional
        original_ensure = campaign.ensure_campaign_ui
        original_combat_status = campaign.combat_status
        original_run = getattr(campaign, 'run', None)
        emotion = getattr(campaign, 'emotion', None)
        original_check_reduce = getattr(emotion, 'check_reduce', None)
        emotion_checked = False
        deadline = None

        def chapter_additional():
            if deadline is not None and deadline.reached():
                raise StageUnavailable('Chapter did not become available within 20 seconds')
            return original_additional()

        def ensure_ui(*args, **kwargs):
            nonlocal deadline
            deadline = Timer(20, count=60).start()
            return original_ensure(*args, **kwargs)

        def ensure_event_mode(mode='combat'):
            from module.campaign.campaign_ui import MODE_SWITCH_20241219
            from module.eventstory.eventstory import ensure_event_switch
            state = 'story' if mode == 'story' else 'combat'
            if mode not in ('normal', 'hard', 'ex', 'combat', 'story') or not ensure_event_switch(
                    campaign, MODE_SWITCH_20241219, state):
                raise StageUnavailable(f'Event mode selector did not reach {mode}')

        def ensure_event_aside(chapter):
            from module.campaign.campaign_ui import ASIDE_SWITCH_20241219
            from module.eventstory.eventstory import ensure_event_switch
            states = dict(part1='part1', a='part1', c='part1', t='part1',
                          part2='part2', b='part2', d='part2',
                          sp='sp', ex_sp='sp', ex='ex', ex_ex='ex')
            state = states.get(chapter)
            if state is None or not ensure_event_switch(campaign, ASIDE_SWITCH_20241219, state):
                raise StageUnavailable(f'Event chapter selector did not reach {chapter}')

        def run_campaign(*args, **kwargs):
            nonlocal emotion_checked
            # Commission notices and event expiry stop CampaignRun before this
            # call. Once map execution begins, any interruption keeps the budget.
            self.attempt_started = True
            emotion_checked = False
            return original_run(*args, **kwargs)

        def map_stop():
            nonlocal emotion_checked
            self.goal_complete = original_stop()
            if not self.goal_complete and self.inspect_only:
                campaign.enter_map_cancel()
                raise ScriptEnd('Final star inspection: stage still incomplete')
            if not self.goal_complete and original_check_reduce is not None and not emotion_checked:
                # Inspect the preparation stars before requiring another sortie's
                # morale. The same preflight check still gates its Start click.
                try:
                    self.check_stage_morale(campaign, original_check_reduce)
                except ScriptEnd:
                    self.scheduler_deferred = True
                    campaign.enter_map_cancel()
                    raise
                emotion_checked = True
            return self.goal_complete

        def combat_status(drop=None, expected_end=None):
            # combat_execute may consume the victory screen before combat_status
            # resets these flags. Keep that evidence through cleanup, which normally
            # raises CampaignEnd once the stage page has been recognized.
            won_before = campaign.combat_won
            lost_before = campaign.combat_lost
            try:
                return original_combat_status(drop=drop, expected_end=expected_end)
            finally:
                if (expected_end == 'in_stage' and
                        (won_before or campaign.combat_won) and
                        not (lost_before or campaign.combat_lost or campaign.run_lost) and
                        campaign.is_in_stage()):
                    self.boss_cleared = True

        campaign.handle_chapter_additional = chapter_additional
        campaign.ensure_campaign_ui = ensure_ui
        campaign.campaign_ensure_mode_20241219 = ensure_event_mode
        campaign.campaign_ensure_aside_20241219 = ensure_event_aside
        if original_run is not None:
            campaign.run = run_campaign
        campaign.triggered_map_stop = map_stop
        campaign.combat_status = combat_status
        if original_check_reduce is not None:
            emotion.check_reduce = lambda battle: None
        # CampaignRun catches ScriptEnd after this call. The job, not the map,
        # owns progression, scheduler state and the next task.
        campaign.handle_map_stop = lambda: None
        return loaded

    def run_goal(self, goal, inspect_only=False):
        self.inspect_only, self.goal_complete, self.boss_cleared = inspect_only, False, False
        self.attempt_started = False
        self.scheduler_deferred = False
        next_run_before = getattr(self.config, 'Scheduler_NextRun', None)
        with stage_overrides(self.config, goal):
            try:
                self.run(goal.stage, folder=goal.event, mode=goal.mode, total=1)
            except TaskEnd as error:
                # Preserve the scheduler's Commission/expiry dispatch. Only an
                # explicit stop before campaign.run is proven safe to refund.
                error.star_pass_precombat = not self.attempt_started
                raise
            except (StageUnavailable, ScriptEnd) as error:
                logger.warning(f'Star pass stopped at unavailable {goal.stage}: {error}')
                return 'unavailable'
            except RequestHumanTakeover:
                # The ordinary map entry guard also rejects fleet stat/level restrictions.
                return 'blocked'
        if self.goal_complete:
            return 'already_complete'
        next_run = getattr(self.config, 'Scheduler_NextRun', None)
        if self.scheduler_deferred or (not self.attempt_started and
                isinstance(next_run, datetime) and next_run != next_run_before and next_run > datetime.now()):
            return 'deferred'
        if getattr(self.campaign, 'run_lost', False):
            return 'lost'
        if inspect_only:
            return 'capped'
        if not self.run_count:
            return 'interrupted' if self.attempt_started else 'stopped'
        if goal.goal == 'clear':
            # An emotion/fleet withdrawal can end a run without defeating the boss.
            return 'complete' if self.boss_cleared else 'blocked'
        return 'ran'


def complete_stage(goal, progress, attempt, max_runs=6):
    """Persist before combat so a crash never grants an extra oil/EX attempt."""
    record = progress.record(goal)
    if record.get('status') == 'complete':
        return 'complete'
    if record.get('status') in ('lost', 'blocked', 'capped'):
        return record['status']
    limit = 1 if goal.goal == 'clear' else max(1, min(int(max_runs), 12))
    runs = max(0, record.get('attempts', 0))
    while runs <= limit:
        inspecting = runs == limit
        if not inspecting:
            runs += 1
            progress.save(goal, attempts=runs, status='attempting')
        try:
            result = attempt(goal, inspect_only=inspecting)
        except TaskEnd as error:
            if getattr(error, 'star_pass_precombat', False) and not inspecting:
                progress.save(goal, attempts=runs - 1, status='deferred')
            raise
        # Navigation/oil deferral did not run combat. Return its reservation.
        if result in ('unavailable', 'stopped', 'deferred', 'already_complete') and not inspecting:
            runs -= 1
        if result == 'already_complete':
            result = 'complete'
        progress.save(goal, attempts=runs, status=result)
        if result != 'ran':
            return result
    return 'capped'


class CampaignStars(CampaignRun):
    def make_plan(self):
        return campaign_plan(self.config.Campaign_Name, self.config.StarPass_EndStage)

    def collect(self, before=False):
        from module.campaign.event_completion import EventCompletion
        return EventCompletion(config=self.config, device=self.device).run(
            event=self.config.Campaign_Event,
            collect_rewards=self.config.StarPass_CollectRewards,
            complete_story=bool(before and self.config.StarPass_CompleteStory))

    def call_then_task(self):
        task = self.config.StopCondition_ThenTask
        if task:
            logger.info(f'Star pass returns to task: {task}')
            self.config.task_call(task, force_call=True)

    def run(self):
        # Let dispatch restart a closed/logged-out game before consuming any
        # durable stage/inspection budget or navigating event-only screens.
        self.ui_get_current_page()
        plan = self.make_plan()
        if not plan:
            raise ValueError('No implemented stages in star pass')
        completion = self.collect(before=True)
        if completion and completion.get('story') in ('blocked', 'failed'):
            self.config.task_delay(success=False)
            self.call_then_task()
            return
        progress = StarProgress(self.config.config_name, self.config.task.command)
        pending = [goal for goal in plan if progress.record(goal).get('status') != 'complete']
        limit = max(1, min(int(self.config.StarPass_StagesPerRun), 64))
        result = 'complete'
        for goal in pending[:limit]:
            runner = StageStarsRunner(config=self.config, device=self.device)
            result = complete_stage(goal, progress, runner.run_goal, self.config.StarPass_MaxRunsPerStage)
            logger.attr('Star_pass_stage', f'{goal.key}: {result}')
            if result == 'deferred':
                # The stage's own oil/morale handler already scheduled its retry
                # and any DormRest work. Do not replace that time or dispatch farm.
                return
            if result != 'complete':
                break
            if self.config.task_switched():
                # The scheduler owns the next action. Progress is already durable;
                # do not start reward navigation or alter scheduling after a switch.
                return
        completion = self.collect()
        if all(progress.record(goal).get('status') == 'complete' for goal in plan):
            if completion and any(completion.get(key) == 'failed' for key in ('rewards', 'event_hub_rewards')):
                logger.warning('Star pass stages complete; retry pending reward collection')
                self.config.task_delay(success=False)
                self.call_then_task()
                return
            logger.info('Star pass completed all implemented stages')
            extra_dispatched = self.extra_objectives()
            self.config.Scheduler_Enable = False
            if extra_dispatched is not True:
                self.call_then_task()
        elif result in ('lost', 'blocked', 'capped'):
            logger.warning('Star pass held for fleet/progress review; saved attempts will not reset')
            self.config.task_delay(minute=360)
            self.call_then_task()
        elif result in ('stopped', 'unavailable', 'interrupted'):
            self.config.task_delay(minute=120)
            self.call_then_task()
        else:
            self.config.task_delay(minute=1)

    def extra_objectives(self):
        pass


class EventStars(CampaignStars):
    def extra_objectives(self):
        if not self.config.StarPass_TryEx:
            logger.info('Event EX objective disabled')
            return
        if any(goal.stage in ('EX', 'EXTRA') for goal in self.make_plan()):
            return
        logger.info('Supported event stages complete; dispatching the isolated EX attempt')
        self.config.task_call('EventExtra', force_call=True)
        return True

    def make_plan(self):
        event = self.config.Campaign_Event
        if not event.startswith('event_'):
            raise ValueError('EventStars needs an implemented current event')
        plan = event_plan(event)
        if not self.config.StarPass_TryEx:
            plan = [goal for goal in plan if goal.stage not in ('EX', 'EXTRA')]
        if not any(goal.stage in ('EX', 'EXTRA') for goal in plan):
            logger.info(f'{event} EX uses the separate verified direct-battle job')
        return plan


class EventExtra(CampaignStars):
    """One scheduler-driven EX attempt, separate from the regular stage pass."""
    def run(self):
        self.ui_get_current_page()
        from module.campaign.event_extra import EventExtraRunner
        event = self.config.Campaign_Event
        if not event.startswith('event_'):
            raise ValueError('EventExtra needs a current event')
        result = EventExtraRunner(config=self.config, device=self.device).run(event)
        logger.attr('Event_EX_result', result)
        if result.get('status') != 'complete':
            logger.warning('Event EX objective is not complete; no automatic battle retry')
        self.config.Scheduler_Enable = False
        self.call_then_task()
