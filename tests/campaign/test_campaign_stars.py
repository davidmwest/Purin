import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import module.campaign.campaign_stars as stars
from module.campaign.campaign_stars import (CampaignStars, EventExtra, EventStars, StageGoal, StageStarsRunner,
                                            StarProgress, campaign_plan, complete_stage, event_plan,
                                            stage_overrides)
from module.campaign.run import CampaignRun
from module.exception import ScriptEnd


def test_main_plan_is_ordered_and_has_an_explicit_upper_bound():
    plan = campaign_plan('campaign_1_3', '2-2')
    assert [goal.stage for goal in plan] == ['1-3', '1-4', '2-1', '2-2']
    assert all(goal.event == 'campaign_main' and goal.mode == 'normal' for goal in plan)
    assert len(campaign_plan()) == 64


@pytest.mark.parametrize('first,last', [('0-1', '1-4'), ('2-1', '1-4'), ('1-1', '17-1'), ('T1', '1-4')])
def test_main_plan_rejects_unsafe_bounds(first, last):
    with pytest.raises(ValueError):
        campaign_plan(first, last)


def test_missing_campaign_stage_does_not_silently_advance_to_harder_stage(monkeypatch):
    monkeypatch.setattr(stars, 'stage_names', lambda event: ['campaign_1_1', 'campaign_1_3'])
    with pytest.raises(ValueError, match='1-2'):
        campaign_plan('1-1', '1-3')


def test_current_event_uses_three_star_maps_and_one_clear_sp():
    plan = event_plan('event_20261022_cn')
    assert [(goal.stage, goal.mode, goal.goal) for goal in plan] == [
        (f'T{i}', 'normal', 'three_stars') for i in range(1, 7)] + [('SP', 'normal', 'clear')]


def test_event_planner_excludes_story_and_handles_hard_and_ex(monkeypatch):
    monkeypatch.setattr(stars, 'stage_names', lambda event: ['d2', 'a2', 'c1', 'ex', 'story', 'a1', 'sp'])
    monkeypatch.setattr(stars, 'load_stage', lambda event, stage: SimpleNamespace(Config=SimpleNamespace))
    assert [(goal.stage, goal.mode, goal.goal) for goal in event_plan('event_test')] == [
        ('A1', 'normal', 'three_stars'), ('A2', 'normal', 'three_stars'),
        ('C1', 'hard', 'three_stars'), ('D2', 'hard', 'three_stars'),
        ('SP', 'normal', 'clear'), ('EX', 'normal', 'clear')]


def test_account_job_event_and_goal_progress_are_separate(tmp_path):
    goal = StageGoal('event_20261022_cn', 'T1')
    progress = StarProgress('f2p', 'EventStars', tmp_path)
    progress.save(goal, attempts=2, status='complete')
    assert StarProgress('f2p', 'EventStars', tmp_path).record(goal)['status'] == 'complete'
    assert StarProgress('alas', 'EventStars', tmp_path).record(goal) == {}
    assert StarProgress('f2p', 'CampaignStars', tmp_path).record(goal) == {}
    assert progress.record(StageGoal('event_20261105_cn', 'T1')) == {}
    assert progress.record(StageGoal(goal.event, 'T1', goal='clear')) == {}


def test_corrupt_progress_fails_closed_instead_of_resetting_attempt_budget(tmp_path):
    path = tmp_path / 'f2p_EventStars.json'
    path.write_text('{bad json')
    with pytest.raises(ValueError):
        StarProgress('f2p', 'EventStars', tmp_path)


def test_completed_stage_is_skipped_without_navigation_or_combat(tmp_path):
    goal = StageGoal('campaign_main', '1-1')
    progress = StarProgress('f2p', 'CampaignStars', tmp_path)
    progress.save(goal, attempts=2, status='complete')
    attempt = Mock()
    assert complete_stage(goal, progress, attempt) == 'complete'
    attempt.assert_not_called()


def test_max_attempts_survive_restart_and_final_check_cannot_start_combat(tmp_path):
    goal = StageGoal('campaign_main', '1-1')
    progress = StarProgress('f2p', 'CampaignStars', tmp_path)
    progress.save(goal, attempts=4, status='ran')
    calls = []
    def attempt(goal, inspect_only):
        calls.append(inspect_only)
        # The attempt is already on disk before any combat is permitted.
        expected = 6 if inspect_only else 4 + len(calls)
        assert StarProgress('f2p', 'CampaignStars', tmp_path).record(goal)['attempts'] == expected
        return 'capped' if inspect_only else 'ran'
    assert complete_stage(goal, progress, attempt, max_runs=6) == 'capped'
    assert calls == [False, False, True]
    assert progress.record(goal)['attempts'] == 6
    attempt = Mock()
    assert complete_stage(goal, StarProgress('f2p', 'CampaignStars', tmp_path), attempt) == 'capped'
    attempt.assert_not_called()


def test_last_allowed_run_can_be_verified_complete(tmp_path):
    goal = StageGoal('campaign_main', '1-1')
    progress = StarProgress('f2p', 'CampaignStars', tmp_path)
    attempt = Mock(side_effect=['ran', 'complete'])
    assert complete_stage(goal, progress, attempt, max_runs=1) == 'complete'
    assert attempt.call_args_list[1].kwargs == {'inspect_only': True}


@pytest.mark.parametrize('goal_name', ['SP', 'EX'])
def test_one_clear_stages_have_only_one_persisted_battle_attempt(tmp_path, goal_name):
    goal = StageGoal('event_test', goal_name, goal='clear')
    progress = StarProgress('alas', 'EventStars', tmp_path)
    attempt = Mock(side_effect=['ran', 'capped'])
    assert complete_stage(goal, progress, attempt, max_runs=12) == 'capped'
    assert [call.kwargs['inspect_only'] for call in attempt.call_args_list] == [False, True]


def test_crash_cannot_reset_reserved_ex_attempt(tmp_path):
    goal = StageGoal('event_test', 'EX', goal='clear')
    progress = StarProgress('alas', 'EventStars', tmp_path)
    with pytest.raises(RuntimeError):
        complete_stage(goal, progress, Mock(side_effect=RuntimeError('process interrupted')))
    resumed = Mock(return_value='capped')
    assert complete_stage(goal, StarProgress('alas', 'EventStars', tmp_path), resumed) == 'capped'
    resumed.assert_called_once_with(goal, inspect_only=True)


@pytest.mark.parametrize('result', ['stopped', 'unavailable', 'already_complete'])
def test_oil_or_locked_stage_does_not_consume_battle_budget(tmp_path, result):
    goal = StageGoal('campaign_main', '5-1')
    progress = StarProgress('f2p', 'CampaignStars', tmp_path)
    attempt = Mock(return_value=result)
    assert complete_stage(goal, progress, attempt) == ('complete' if result == 'already_complete' else result)
    assert progress.record(goal)['attempts'] == 0
    assert attempt.call_count == 1


def test_loss_holds_at_same_stage_across_scheduler_runs(tmp_path):
    goal = StageGoal('campaign_main', '5-1')
    progress = StarProgress('f2p', 'CampaignStars', tmp_path)
    assert complete_stage(goal, progress, Mock(return_value='lost')) == 'lost'
    attempt = Mock()
    assert complete_stage(goal, StarProgress('f2p', 'CampaignStars', tmp_path), attempt) == 'lost'
    attempt.assert_not_called()


class Config(SimpleNamespace):
    def override(self, **values):
        self.overridden.update(values)
        for key, value in values.items():
            setattr(self, key, value)


def config():
    return Config(
        overridden={'Keep': 7}, Campaign_Name='12-4', Campaign_Event='campaign_main', Campaign_Mode='normal',
        Campaign_UseClearMode=True, Campaign_UseAutoSearch=True, Campaign_Use2xBook=True,
        ShipRotation_Enable=True, StopCondition_MapAchievement='non_stop', StopCondition_RunCount=10,
        StopCondition_StageIncrease=True, StopCondition_StageRetreat=3, StopCondition_RetreatRemain=2,
        StopCondition_EndOnLoss=True, StopCondition_GetNewShip=True, StopCondition_ReachLevel=120,
        StopCondition_ThenTask='Event', EventGeneral_PtLimit=10000, Fleet_Fleet1=3, Fleet_Fleet2=4)


def test_stage_overrides_restore_all_task_settings_after_failure():
    value = config()
    previous = vars(value).copy()
    previous['overridden'] = value.overridden.copy()
    with pytest.raises(RuntimeError):
        with stage_overrides(value, StageGoal('event_test', 'D2', 'hard')):
            assert value.Campaign_Event == 'event_test'
            assert value.Campaign_Mode == 'hard'
            assert value.StopCondition_MapAchievement == 'map_3_stars'
            assert not value.Campaign_UseClearMode and not value.Campaign_UseAutoSearch
            assert not value.ShipRotation_Enable
            assert value.StopCondition_ThenTask == ''
            assert value.EventGeneral_PtLimit == 0
            assert (value.Fleet_Fleet1, value.Fleet_Fleet2) == (3, 4)
            value.override(Campaign_Name='temporary')
            raise RuntimeError('task switched')
    assert vars(value) == previous


def test_stage_runner_reuses_map_stop_without_disabling_job(monkeypatch):
    runner = StageStarsRunner.__new__(StageStarsRunner)
    campaign = SimpleNamespace(triggered_map_stop=Mock(return_value=True),
                               handle_map_stop=Mock(), handle_chapter_additional=Mock(),
                               ensure_campaign_ui=Mock(), enter_map_cancel=Mock(), combat=Mock())
    def load(self, name, folder):
        self.campaign = campaign
        return True
    monkeypatch.setattr(CampaignRun, 'load_campaign', load)
    assert runner.load_campaign('campaign_1_1')
    assert campaign.triggered_map_stop() is True
    campaign.handle_map_stop()
    assert runner.goal_complete is True


def test_final_inspection_cancels_incomplete_map_before_fleet_or_battle(monkeypatch):
    runner = StageStarsRunner.__new__(StageStarsRunner)
    runner.inspect_only = True
    campaign = SimpleNamespace(triggered_map_stop=Mock(return_value=False),
                               handle_map_stop=Mock(), handle_chapter_additional=Mock(),
                               ensure_campaign_ui=Mock(), enter_map_cancel=Mock(), combat=Mock())
    monkeypatch.setattr(CampaignRun, 'load_campaign', lambda self, name, folder: setattr(self, 'campaign', campaign) or True)
    runner.load_campaign('campaign_1_1')
    with pytest.raises(ScriptEnd, match='still incomplete'):
        campaign.triggered_map_stop()
    campaign.enter_map_cancel.assert_called_once_with()
    assert not runner.goal_complete


def test_chapter_navigation_timeout_terminates_unlocked_stage_search(monkeypatch):
    runner = StageStarsRunner.__new__(StageStarsRunner)
    timer = Mock()
    timer.start.return_value = timer
    timer.reached.return_value = True
    monkeypatch.setattr(stars, 'Timer', Mock(return_value=timer))
    additional = Mock()
    campaign = SimpleNamespace(triggered_map_stop=Mock(return_value=False), handle_map_stop=Mock(),
                               handle_chapter_additional=additional, enter_map_cancel=Mock(), combat=Mock())
    campaign.ensure_campaign_ui = lambda: campaign.handle_chapter_additional()
    monkeypatch.setattr(CampaignRun, 'load_campaign', lambda self, name, folder: setattr(self, 'campaign', campaign) or True)
    runner.load_campaign('campaign_1_1')
    with pytest.raises(stars.StageUnavailable):
        campaign.ensure_campaign_ui()
    additional.assert_not_called()


@pytest.mark.parametrize('boss_cleared,result', [(False, 'blocked'), (True, 'complete')])
def test_one_clear_goal_rejects_early_emotion_withdrawal(monkeypatch, boss_cleared, result):
    runner = StageStarsRunner.__new__(StageStarsRunner)
    runner.config = config()
    runner.campaign = SimpleNamespace(run_lost=False, battle_count=7, _map_battle=4)
    runner.run_count = 1
    monkeypatch.setattr(runner, 'run', lambda *args, **kwargs: setattr(runner, 'boss_cleared', boss_cleared))
    assert runner.run_goal(StageGoal('event_test', 'SP', goal='clear')) == result


@pytest.mark.parametrize('expected_end,won,lost,complete', [
    ('with_searching', True, False, False), ('in_stage', False, False, False),
    ('in_stage', True, True, False), ('in_stage', True, False, True)])
def test_only_won_boss_combat_returning_to_campaign_proves_one_clear(
        monkeypatch, expected_end, won, lost, complete):
    runner = StageStarsRunner.__new__(StageStarsRunner)
    runner.boss_cleared = False
    campaign = SimpleNamespace(triggered_map_stop=Mock(return_value=False), handle_chapter_additional=Mock(),
                               ensure_campaign_ui=Mock(), combat=Mock(return_value=True),
                               combat_won=won, combat_lost=lost)
    monkeypatch.setattr(CampaignRun, 'load_campaign', lambda self, name, folder: setattr(self, 'campaign', campaign) or True)
    runner.load_campaign('sp', 'event_test')
    assert campaign.combat(expected_end=expected_end) is True
    assert runner.boss_cleared is complete


def make_job(monkeypatch, tmp_path, goals, result='already_complete'):
    progress = StarProgress('f2p', 'CampaignStars', tmp_path)
    monkeypatch.setattr(stars, 'StarProgress', lambda *args: progress)
    runner = SimpleNamespace(run_goal=Mock(return_value=result))
    factory = Mock(return_value=runner)
    monkeypatch.setattr(stars, 'StageStarsRunner', factory)
    job = CampaignStars.__new__(CampaignStars)
    job.device = Mock()
    job.config = SimpleNamespace(
        config_name='f2p', task=SimpleNamespace(command='CampaignStars'),
        StarPass_StagesPerRun=4, StarPass_MaxRunsPerStage=6, StopCondition_ThenTask='Event',
        Scheduler_Enable=True, task_delay=Mock(), task_call=Mock(), task_switched=Mock(return_value=False))
    job.make_plan = Mock(return_value=goals)
    job.collect = Mock(return_value=dict(story='disabled', rewards='complete'))
    job.extra_objectives = Mock()
    return job, progress, runner, factory


@pytest.mark.parametrize('result', ['already_complete', 'lost', 'blocked', 'capped', 'unavailable', 'stopped'])
def test_completed_or_held_job_dispatches_farming_task(monkeypatch, tmp_path, result):
    goal = StageGoal('campaign_main', '1-1')
    job, progress, runner, _ = make_job(monkeypatch, tmp_path, [goal], result)
    job.run()
    job.config.task_call.assert_called_once_with('Event', force_call=True)
    if result == 'already_complete':
        assert not job.config.Scheduler_Enable
        assert progress.record(goal) == dict(attempts=0, status='complete')
        job.extra_objectives.assert_called_once_with()
    else:
        assert job.config.Scheduler_Enable
        job.config.task_delay.assert_called_once()


def test_task_switch_resumes_after_completed_stage_without_repeating_it(monkeypatch, tmp_path):
    goals = [StageGoal('campaign_main', stage) for stage in ('1-1', '1-2')]
    job, progress, runner, factory = make_job(monkeypatch, tmp_path, goals)
    job.config.task_switched.return_value = True
    job.run()
    runner.run_goal.assert_called_once_with(goals[0], inspect_only=False)
    assert progress.record(goals[0])['status'] == 'complete'
    assert progress.record(goals[1]) == {}
    job.config.task_call.assert_not_called()
    job.config.task_delay.assert_not_called()
    job.collect.assert_called_once_with(before=True)
    runner.run_goal.reset_mock()
    job.config.task_switched.return_value = False
    job.run()
    runner.run_goal.assert_called_once_with(goals[1], inspect_only=False)
    assert factory.call_count == 2
    job.config.task_call.assert_called_once_with('Event', force_call=True)


def test_extra_inspection_runs_once_then_disables_without_stage_runner(monkeypatch):
    from module.campaign import event_extra
    factory = Mock()
    factory.return_value.run.return_value = dict(status='observed', combat_attempted=False)
    monkeypatch.setattr(event_extra, 'EventExtraScout', factory)
    job = EventExtra.__new__(EventExtra)
    job.device = Mock()
    job.config = SimpleNamespace(Campaign_Event='event_20261022_cn', Scheduler_Enable=True,
                                 StopCondition_ThenTask='EventStars', task_call=Mock())
    job.run()
    factory.return_value.run.assert_called_once_with('event_20261022_cn')
    assert not job.config.Scheduler_Enable
    job.config.task_call.assert_called_once_with('EventStars', force_call=True)


def test_story_failure_dispatches_farming_without_stage_navigation(monkeypatch, tmp_path):
    job, _, _, factory = make_job(monkeypatch, tmp_path, [StageGoal('event_test', 'T1')])
    job.collect.return_value = dict(story='failed')
    job.run()
    factory.assert_not_called()
    job.config.task_delay.assert_called_once_with(success=False)
    job.config.task_call.assert_called_once_with('Event', force_call=True)


def test_event_try_ex_false_excludes_supported_extra_stage(monkeypatch):
    job = EventStars.__new__(EventStars)
    job.config = SimpleNamespace(Campaign_Event='event_test', StarPass_TryEx=False)
    monkeypatch.setattr(stars, 'event_plan', lambda event: [StageGoal(event, 'T1'), StageGoal(event, 'EX', goal='clear')])
    assert [goal.stage for goal in job.make_plan()] == ['T1']


def test_ordinary_event_story_failure_stops_before_loading_campaign(monkeypatch):
    run = CampaignRun.__new__(CampaignRun)
    run.config = SimpleNamespace(task=SimpleNamespace(command='Event'), task_delay=Mock())
    run.event_completion = Mock(return_value=False)
    run.load_campaign = Mock()
    run.run('T1', folder='event_test')
    run.event_completion.assert_called_once_with('event_test', before=True)
    run.load_campaign.assert_not_called()
    run.config.task_delay.assert_called_once_with(success=False)


def test_ordinary_event_lifecycle_only_runs_story_before_campaign(monkeypatch):
    from module.campaign import event_completion
    factory = Mock()
    factory.return_value.run.return_value = dict(story='disabled')
    monkeypatch.setattr(event_completion, 'EventCompletion', factory)
    run = CampaignRun.__new__(CampaignRun)
    run.device = Mock()
    run.config = SimpleNamespace(EventCompletion_CollectRewards=True, EventCompletion_CompleteStory=True)
    assert run.event_completion('event_test', before=True)
    assert run.event_completion('event_test')
    assert [call.kwargs for call in factory.return_value.run.call_args_list] == [
        dict(event='event_test', collect_rewards=True, complete_story=True),
        dict(event='event_test', collect_rewards=True, complete_story=False)]


def test_both_job_entrypoints_use_shared_runner():
    assert issubclass(EventStars, CampaignStars)
    assert EventStars.run is CampaignStars.run
