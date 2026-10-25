import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import module.campaign.campaign_stars as stars
from module.campaign.campaign_stars import (CampaignStars, EventExtra, EventStars, StageGoal, StageStarsRunner,
                                            StarProgress, campaign_plan, complete_stage, event_plan,
                                            stage_overrides)
from module.campaign.run import CampaignRun
from module.config.config import TaskEnd
from module.exception import CampaignEnd, GameNotRunningError, GamePageUnknownError, ScriptEnd
from module.handler.fast_forward import FastForwardHandler


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


@pytest.mark.parametrize('result', ['stopped', 'unavailable', 'deferred', 'already_complete'])
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
                               ensure_campaign_ui=Mock(), enter_map_cancel=Mock(), combat_status=Mock())
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
                               ensure_campaign_ui=Mock(), enter_map_cancel=Mock(), combat_status=Mock())
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
                               handle_chapter_additional=additional, enter_map_cancel=Mock(), combat_status=Mock())
    campaign.ensure_campaign_ui = lambda: campaign.handle_chapter_additional()
    monkeypatch.setattr(CampaignRun, 'load_campaign', lambda self, name, folder: setattr(self, 'campaign', campaign) or True)
    runner.load_campaign('campaign_1_1')
    with pytest.raises(stars.StageUnavailable):
        campaign.ensure_campaign_ui()
    additional.assert_not_called()


@pytest.mark.parametrize('kind,target', [('mode', 'combat'), ('aside', 'sp')])
def test_event_selectors_use_bounded_known_state_helper_and_stop_if_unavailable(monkeypatch, kind, target):
    from module.eventstory import eventstory
    from module.campaign.campaign_ui import ASIDE_SWITCH_20241219, MODE_SWITCH_20241219
    runner = StageStarsRunner.__new__(StageStarsRunner)
    campaign = SimpleNamespace(triggered_map_stop=Mock(), handle_chapter_additional=Mock(),
                               ensure_campaign_ui=Mock(), combat_status=Mock())
    monkeypatch.setattr(CampaignRun, 'load_campaign', lambda self, name, folder: setattr(self, 'campaign', campaign) or True)
    ensure = Mock(return_value=False)
    monkeypatch.setattr(eventstory, 'ensure_event_switch', ensure)
    runner.load_campaign('sp', 'event_20261022_cn')
    method = (campaign.campaign_ensure_mode_20241219 if kind == 'mode'
              else campaign.campaign_ensure_aside_20241219)
    with pytest.raises(stars.StageUnavailable):
        method(target)
    ensure.assert_called_once_with(campaign, MODE_SWITCH_20241219 if kind == 'mode' else ASIDE_SWITCH_20241219, target)
    ensure.return_value = True
    method(target)


@pytest.mark.parametrize('started', [False, True])
def test_explicit_scheduler_deferral_refunds_only_before_campaign_run(monkeypatch, tmp_path, started):
    runner = StageStarsRunner.__new__(StageStarsRunner)
    runner.config = config()
    campaign = SimpleNamespace(triggered_map_stop=Mock(), handle_chapter_additional=Mock(),
                               ensure_campaign_ui=Mock(), combat_status=Mock(),
                               run=Mock(side_effect=TaskEnd('Commission notice found')))
    monkeypatch.setattr(CampaignRun, 'load_campaign', lambda self, name, folder: setattr(self, 'campaign', campaign) or True)
    def run(*args, **kwargs):
        runner.load_campaign('sp', 'event_20261022_cn')
        if started:
            campaign.run()
        raise TaskEnd('Commission notice found')
    monkeypatch.setattr(runner, 'run', run)
    goal = StageGoal('event_20261022_cn', 'SP', goal='clear')
    progress = StarProgress('alas', 'EventStars', tmp_path)
    with pytest.raises(TaskEnd, match='Commission notice found'):
        complete_stage(goal, progress, runner.run_goal)
    record = StarProgress('alas', 'EventStars', tmp_path).record(goal)
    assert record == {'attempts': int(started), 'status': 'attempting' if started else 'deferred'}
    # Both cases restore task overrides while preserving the actual scheduler stop.
    assert runner.config.StopCondition_ThenTask == 'Event'
    resume = Mock(return_value='capped' if started else 'complete')
    complete_stage(goal, progress, resume)
    resume.assert_called_once_with(goal, inspect_only=started)


@pytest.mark.parametrize('started', [False, True])
@pytest.mark.parametrize('stage,goal_type,prior_attempts', [('SP', 'clear', 0), ('T5', 'three_stars', 1)])
def test_unknown_page_refunds_only_proven_pre_run_navigation_and_preserves_recovery(
        monkeypatch, tmp_path, started, stage, goal_type, prior_attempts):
    runner = StageStarsRunner.__new__(StageStarsRunner)
    runner.config = config()
    error = GamePageUnknownError('unknown event hub')
    campaign = SimpleNamespace(
        triggered_map_stop=Mock(), handle_chapter_additional=Mock(), combat_status=Mock(),
        ensure_campaign_ui=Mock(side_effect=error), run=Mock(side_effect=error))
    monkeypatch.setattr(CampaignRun, 'load_campaign',
                        lambda self, name, folder: setattr(self, 'campaign', campaign) or True)

    def run(*args, **kwargs):
        runner.load_campaign(stage.lower(), 'event_20261022_cn')
        if started:
            campaign.run()
        campaign.ensure_campaign_ui()

    monkeypatch.setattr(runner, 'run', run)
    goal = StageGoal('event_20261022_cn', stage, goal=goal_type)
    progress = StarProgress('alas', 'EventStars', tmp_path)
    progress.save(goal, attempts=prior_attempts, status='ran')
    with pytest.raises(GamePageUnknownError) as raised:
        complete_stage(goal, progress, runner.run_goal)
    # Keep the original exception so the scheduler still performs its Restart.
    assert raised.value is error
    assert StarProgress('alas', 'EventStars', tmp_path).record(goal) == {
        'attempts': prior_attempts + int(started),
        'status': 'attempting' if started else 'deferred'}
    assert runner.config.StopCondition_ThenTask == 'Event'
    resume = Mock(return_value='capped' if started else 'complete')
    complete_stage(goal, StarProgress('alas', 'EventStars', tmp_path), resume,
                   max_runs=prior_attempts + 1)
    resume.assert_called_once_with(goal, inspect_only=started)


def test_unclassified_pre_run_crash_keeps_reserved_attempt(monkeypatch, tmp_path):
    runner = StageStarsRunner.__new__(StageStarsRunner)
    runner.config = config()
    monkeypatch.setattr(runner, 'run', Mock(side_effect=RuntimeError('unexpected interruption')))
    goal = StageGoal('event_20261022_cn', 'SP', goal='clear')
    progress = StarProgress('alas', 'EventStars', tmp_path)
    with pytest.raises(RuntimeError, match='unexpected interruption'):
        complete_stage(goal, progress, runner.run_goal)
    assert StarProgress('alas', 'EventStars', tmp_path).record(goal) == {
        'attempts': 1, 'status': 'attempting'}
    resume = Mock(return_value='capped')
    complete_stage(goal, StarProgress('alas', 'EventStars', tmp_path), resume)
    resume.assert_called_once_with(goal, inspect_only=True)


@pytest.mark.parametrize('complete,inspect_only,expected', [
    (True, False, 'already_complete'), (False, True, 'capped'), (False, False, 'ran')])
def test_star_inspection_precedes_morale_check_but_new_sortie_still_checks_it(
        monkeypatch, complete, inspect_only, expected):
    runner = StageStarsRunner.__new__(StageStarsRunner)
    runner.config = config()
    order = []
    check = Mock(side_effect=lambda count: order.append('morale'))
    campaign = SimpleNamespace(
        triggered_map_stop=lambda: order.append('stars') or complete,
        handle_chapter_additional=Mock(), ensure_campaign_ui=Mock(), combat_status=Mock(),
        enter_map_cancel=Mock(), emotion=SimpleNamespace(check_reduce=check), _map_battle=6, run_lost=False)

    def run_campaign():
        campaign.emotion.check_reduce(campaign._map_battle)
        assert not order  # CampaignBase's preflight is deferred to the preparation read.
        if campaign.triggered_map_stop():
            raise ScriptEnd('Reach condition')
        order.append('start')

    campaign.run = run_campaign
    monkeypatch.setattr(CampaignRun, 'load_campaign',
                        lambda self, name, folder: setattr(self, 'campaign', campaign) or True)

    def run(*args, **kwargs):
        runner.load_campaign('t5', 'event_20261022_cn')
        runner.run_count = 0
        try:
            campaign.run()
        except ScriptEnd:
            pass
        else:
            runner.run_count = 1

    monkeypatch.setattr(runner, 'run', run)
    assert runner.run_goal(StageGoal('event_20261022_cn', 'T5'), inspect_only=inspect_only) == expected
    if complete or inspect_only:
        check.assert_not_called()
        assert order == ['stars']
    else:
        check.assert_called_once_with(6)
        assert order == ['stars', 'morale', 'start']


@pytest.mark.parametrize('full_clear,fleets,battles,order', [
    (False, (6, 0), 7, None),
    (True, (6, 0), 12, 'fleet1_all_fleet2_standby'),
    (True, (6, 2), 12, None),
    (True, (0, 6), 12, 'fleet1_standby_fleet2_all'),
])
def test_full_clear_preflight_counts_actual_t6_escorts_sirens_and_boss(
        monkeypatch, full_clear, fleets, battles, order):
    runner = StageStarsRunner.__new__(StageStarsRunner)
    stage_config = SimpleNamespace(MAP_CLEAR_ALL_THIS_TIME=full_clear,
                                   Fleet_Fleet1=fleets[0], Fleet_Fleet2=fleets[1],
                                   Fleet_FleetOrder='fleet1_mob_fleet2_boss')
    check = Mock()
    campaign = SimpleNamespace(
        config=stage_config, MAP=stars.load_stage('event_20261022_cn', 't6').MAP, _map_battle=7,
        triggered_map_stop=Mock(return_value=False), handle_chapter_additional=Mock(),
        ensure_campaign_ui=Mock(), combat_status=Mock(), emotion=SimpleNamespace(check_reduce=check))
    monkeypatch.setattr(CampaignRun, 'load_campaign',
                        lambda self, name, folder: setattr(self, 'campaign', campaign) or True)
    runner.load_campaign('t6', 'event_20261022_cn')
    assert campaign.triggered_map_stop() is False
    if order is None:
        check.assert_called_once_with(battles)
    else:
        check.assert_called_once_with(battles, fleet_order=order)
    assert stage_config.Fleet_FleetOrder == 'fleet1_mob_fleet2_boss'


def test_full_clear_preflight_does_not_reuse_the_previous_clearing_mode_spawn_list(monkeypatch):
    stage_map = stars.load_stage('campaign_main', 'campaign_14_4').MAP
    monkeypatch.setattr(stage_map, '_spawn_data_use_loop', True)
    assert sum(row.get(kind, 0) for row in stage_map.spawn_data
               for kind in ('enemy', 'siren', 'boss')) == 12
    campaign = SimpleNamespace(
        config=SimpleNamespace(MAP_CLEAR_ALL_THIS_TIME=True, Fleet_Fleet1=6, Fleet_Fleet2=0),
        MAP=stage_map, _map_battle=7)
    check = Mock()

    StageStarsRunner.__new__(StageStarsRunner).check_stage_morale(campaign, check)

    check.assert_called_once_with(13, fleet_order='fleet1_all_fleet2_standby')


@pytest.mark.parametrize('goal_name,goal_type,prior_attempts', [('T5', 'three_stars', 1), ('SP', 'clear', 0)])
@pytest.mark.parametrize('full_clear', [False, True])
def test_morale_deferral_preserves_copied_config_delay_dormrest_and_battle_budget(
        monkeypatch, tmp_path, goal_name, goal_type, prior_attempts, full_clear):
    runner = StageStarsRunner.__new__(StageStarsRunner)
    runner.config = config()
    original_time = datetime.now() - timedelta(minutes=1)
    recovered = datetime.now() + timedelta(minutes=3)
    runner.config.Scheduler_NextRun = original_time
    # CampaignRun deep-copies task config; its persisted delay does not refresh
    # the coordinator's cached Scheduler_NextRun value.
    stage_config = SimpleNamespace(Scheduler_NextRun=original_time, task_call=Mock(),
                                   MAP_CLEAR_ALL_THIS_TIME=full_clear,
                                   Fleet_Fleet1=6, Fleet_Fleet2=0,
                                   Fleet_FleetOrder='fleet1_mob_fleet2_boss')

    def delay_for_morale(count, fleet_order=None):
        assert count == (12 if full_clear else 6)
        assert fleet_order == ('fleet1_all_fleet2_standby' if full_clear else None)
        stage_config.Scheduler_NextRun = recovered
        stage_config.task_call('DormRest', force_call=False)
        raise ScriptEnd('Emotion control')

    campaign = SimpleNamespace(
        config=stage_config, triggered_map_stop=Mock(return_value=False),
        handle_chapter_additional=Mock(), ensure_campaign_ui=Mock(), combat_status=Mock(),
        MAP=stars.load_stage('event_20261022_cn', 't6').MAP,
        emotion=SimpleNamespace(check_reduce=delay_for_morale), _map_battle=6,
        enter_map_cancel=Mock(), run_lost=False)
    start = Mock()

    def run_campaign():
        campaign.emotion.check_reduce(6)
        campaign.triggered_map_stop()
        start()

    campaign.run = run_campaign
    monkeypatch.setattr(CampaignRun, 'load_campaign',
                        lambda self, name, folder: setattr(self, 'campaign', campaign) or True)

    def run(*args, **kwargs):
        runner.load_campaign(goal_name, 'event_20261022_cn')
        runner.run_count = 0
        try:
            campaign.run()
        except ScriptEnd:
            pass

    monkeypatch.setattr(runner, 'run', run)
    goal = StageGoal('event_20261022_cn', goal_name, goal=goal_type)
    progress = StarProgress('alas', 'EventStars', tmp_path)
    progress.save(goal, attempts=prior_attempts, status='ran')
    assert complete_stage(goal, progress, runner.run_goal) == 'deferred'
    assert progress.record(goal) == dict(attempts=prior_attempts, status='deferred')
    assert stage_config.Scheduler_NextRun == recovered
    assert runner.config.Scheduler_NextRun == original_time
    assert stage_config.Fleet_FleetOrder == 'fleet1_mob_fleet2_boss'
    stage_config.task_call.assert_called_once_with('DormRest', force_call=False)
    campaign.enter_map_cancel.assert_called_once_with()
    start.assert_not_called()


def test_oil_delay_before_campaign_run_is_a_deferred_result(monkeypatch, tmp_path):
    runner = StageStarsRunner.__new__(StageStarsRunner)
    runner.config = config()
    runner.config.Scheduler_NextRun = datetime.now() - timedelta(minutes=1)
    runner.campaign = SimpleNamespace(run_lost=False)
    next_run = datetime.now() + timedelta(hours=3)

    def run(*args, **kwargs):
        runner.run_count = 0
        runner.config.Scheduler_NextRun = next_run

    monkeypatch.setattr(runner, 'run', run)
    goal = StageGoal('campaign_main', '2-3')
    progress = StarProgress('f2p', 'CampaignStars', tmp_path)
    assert complete_stage(goal, progress, runner.run_goal) == 'deferred'
    assert progress.record(goal) == dict(attempts=0, status='deferred')
    assert runner.config.Scheduler_NextRun == next_run


def test_reused_campaign_checks_morale_again_before_its_second_sortie(monkeypatch):
    runner = StageStarsRunner.__new__(StageStarsRunner)
    runner.config = config()
    check = Mock(side_effect=[None, ScriptEnd('Emotion control')])
    campaign = SimpleNamespace(
        triggered_map_stop=Mock(return_value=False), handle_chapter_additional=Mock(),
        ensure_campaign_ui=Mock(), combat_status=Mock(), enter_map_cancel=Mock(),
        emotion=SimpleNamespace(check_reduce=check), _map_battle=6, run_lost=False)
    start = Mock()

    def run_campaign():
        campaign.emotion.check_reduce(6)
        campaign.triggered_map_stop()
        start()

    campaign.run = run_campaign
    monkeypatch.setattr(CampaignRun, 'load_campaign',
                        lambda self, name, folder: setattr(self, 'campaign', campaign) or True)
    runner.load_campaign('t5', 'event_20261022_cn')

    def run(*args, **kwargs):
        # CampaignRun reuses the same loaded module for another attempt.
        runner.run_count = 0
        try:
            campaign.run()
        except ScriptEnd:
            pass
        else:
            runner.run_count = 1

    monkeypatch.setattr(runner, 'run', run)
    goal = StageGoal('event_20261022_cn', 'T5')
    assert runner.run_goal(goal) == 'ran'
    assert runner.run_goal(goal) == 'deferred'
    assert check.call_count == 2
    start.assert_called_once_with()
    campaign.enter_map_cancel.assert_called_once_with()


def test_entered_campaign_interruption_keeps_its_durable_attempt(monkeypatch, tmp_path):
    runner = StageStarsRunner.__new__(StageStarsRunner)
    runner.config = config()
    runner.campaign = SimpleNamespace(run_lost=False)

    def run(*args, **kwargs):
        runner.attempt_started = True
        runner.run_count = 0

    monkeypatch.setattr(runner, 'run', run)
    goal = StageGoal('event_20261022_cn', 'SP', goal='clear')
    progress = StarProgress('alas', 'EventStars', tmp_path)
    assert complete_stage(goal, progress, runner.run_goal) == 'interrupted'
    assert progress.record(goal) == dict(attempts=1, status='interrupted')
    resume = Mock(return_value='capped')
    assert complete_stage(goal, progress, resume) == 'capped'
    resume.assert_called_once_with(goal, inspect_only=True)


@pytest.mark.parametrize('boss_cleared,result', [(False, 'blocked'), (True, 'complete')])
def test_one_clear_goal_rejects_early_emotion_withdrawal(monkeypatch, boss_cleared, result):
    runner = StageStarsRunner.__new__(StageStarsRunner)
    runner.config = config()
    runner.campaign = SimpleNamespace(run_lost=False, battle_count=7, _map_battle=4)
    runner.run_count = 1
    monkeypatch.setattr(runner, 'run', lambda *args, **kwargs: setattr(runner, 'boss_cleared', boss_cleared))
    assert runner.run_goal(StageGoal('event_test', 'SP', goal='clear')) == result


@pytest.mark.parametrize('boss_cleared,result', [(False, 'blocked'), (True, 'complete')])
def test_sp_full_preparation_bar_without_stars_never_substitutes_for_boss_victory(
        monkeypatch, boss_cleared, result):
    runner = StageStarsRunner.__new__(StageStarsRunner)
    runner.config = config()
    campaign = SimpleNamespace(
        config=runner.config, map_is_100_percent_clear=True, map_is_3_stars=False,
        handle_chapter_additional=Mock(), ensure_campaign_ui=Mock(), combat_status=Mock(), run_lost=False)
    campaign.triggered_map_stop = lambda: FastForwardHandler.triggered_map_stop(campaign)
    monkeypatch.setattr(CampaignRun, 'load_campaign',
                        lambda self, name, folder: setattr(self, 'campaign', campaign) or True)

    def run(*args, **kwargs):
        runner.load_campaign('sp', 'event_20261022_cn')
        # SP exposes the preparation button before its percentage animation
        # settles. Even this full bar must allow the one authorized boss sortie.
        assert runner.config.StopCondition_MapAchievement == 'non_stop'
        assert not campaign.triggered_map_stop()
        runner.run_count = 1
        runner.boss_cleared = boss_cleared

    monkeypatch.setattr(runner, 'run', run)
    goal = event_plan('event_20261022_cn')[-1]
    assert goal.stage == 'SP' and goal.goal == 'clear'
    assert runner.run_goal(goal) == result
    assert not runner.goal_complete


@pytest.mark.parametrize('expected_end,won,lost,complete', [
    ('with_searching', True, False, False), ('in_stage', False, False, False),
    ('in_stage', True, True, False), ('in_stage', True, False, True)])
def test_only_won_boss_combat_returning_to_campaign_proves_one_clear(
        monkeypatch, expected_end, won, lost, complete):
    runner = StageStarsRunner.__new__(StageStarsRunner)
    runner.boss_cleared = False
    campaign = SimpleNamespace(triggered_map_stop=Mock(return_value=False), handle_chapter_additional=Mock(),
                               ensure_campaign_ui=Mock(), combat_status=Mock(return_value=True),
                               combat_won=won, combat_lost=lost, run_lost=False,
                               is_in_stage=Mock(return_value=True))
    monkeypatch.setattr(CampaignRun, 'load_campaign', lambda self, name, folder: setattr(self, 'campaign', campaign) or True)
    runner.load_campaign('sp', 'event_test')
    assert campaign.combat_status(expected_end=expected_end) is True
    assert runner.boss_cleared is complete


@pytest.mark.parametrize('won_before,won_cleanup,in_stage,run_lost,complete', [
    (True, False, True, False, True), (False, True, True, False, True),
    (True, False, False, False, False), (False, False, True, False, False),
    (True, False, True, True, False)])
def test_boss_victory_survives_cleanup_flag_reset_and_campaign_end(
        monkeypatch, won_before, won_cleanup, in_stage, run_lost, complete):
    runner = StageStarsRunner.__new__(StageStarsRunner)
    runner.boss_cleared = False
    campaign = SimpleNamespace(triggered_map_stop=Mock(return_value=False), handle_chapter_additional=Mock(),
                               ensure_campaign_ui=Mock(), combat_won=won_before, combat_lost=False,
                               run_lost=run_lost, is_in_stage=Mock(return_value=in_stage))

    def cleanup(drop=None, expected_end=None):
        campaign.combat_won = won_cleanup
        campaign.combat_lost = False
        raise CampaignEnd('In stage.' if in_stage else 'Main page')

    campaign.combat_status = cleanup
    monkeypatch.setattr(CampaignRun, 'load_campaign', lambda self, name, folder: setattr(self, 'campaign', campaign) or True)
    runner.load_campaign('sp', 'event_test')
    with pytest.raises(CampaignEnd):
        campaign.combat_status(expected_end='in_stage')
    assert runner.boss_cleared is complete


def make_job(monkeypatch, tmp_path, goals, result='already_complete'):
    progress = StarProgress('f2p', 'CampaignStars', tmp_path)
    monkeypatch.setattr(stars, 'StarProgress', lambda *args: progress)
    runner = SimpleNamespace(run_goal=Mock(return_value=result))
    factory = Mock(return_value=runner)
    monkeypatch.setattr(stars, 'StageStarsRunner', factory)
    job = CampaignStars.__new__(CampaignStars)
    job.device = Mock()
    job.ui_get_current_page = Mock()
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


def test_deferred_job_preserves_handler_delay_and_does_not_dispatch_or_collect(monkeypatch, tmp_path):
    goal = StageGoal('event_20261022_cn', 'T5')
    job, progress, runner, _ = make_job(monkeypatch, tmp_path, [goal], 'deferred')
    recovered = datetime.now() + timedelta(minutes=3)
    job.config.Scheduler_NextRun = recovered
    progress.save(goal, attempts=1, status='ran')
    job.run()
    assert progress.record(goal) == dict(attempts=1, status='deferred')
    assert job.config.Scheduler_NextRun == recovered
    assert job.config.Scheduler_Enable
    job.config.task_delay.assert_not_called()
    job.config.task_call.assert_not_called()
    job.collect.assert_called_once_with(before=True)
    job.extra_objectives.assert_not_called()


@pytest.mark.parametrize('reward_key', ['rewards', 'event_hub_rewards'])
def test_final_reward_failure_retries_without_replaying_completed_stages(monkeypatch, tmp_path, reward_key):
    goal = StageGoal('campaign_main', '1-1')
    job, progress, runner, factory = make_job(monkeypatch, tmp_path, [goal])
    job.collect.side_effect = [dict(story='disabled', rewards='complete'),
                               dict(story='disabled', **{reward_key: 'failed'}),
                               dict(story='disabled', rewards='complete'),
                               dict(story='disabled', rewards='complete')]

    job.run()

    assert progress.record(goal) == dict(attempts=0, status='complete')
    assert job.config.Scheduler_Enable
    job.config.task_delay.assert_called_once_with(success=False)
    job.config.task_call.assert_called_once_with('Event', force_call=True)
    job.extra_objectives.assert_not_called()
    assert factory.call_count == 1
    job.config.task_delay.reset_mock()
    job.config.task_call.reset_mock()

    job.run()

    assert progress.record(goal) == dict(attempts=0, status='complete')
    assert factory.call_count == 1
    runner.run_goal.assert_called_once_with(goal, inspect_only=False)
    assert not job.config.Scheduler_Enable
    job.extra_objectives.assert_called_once_with()
    job.config.task_delay.assert_not_called()
    job.config.task_call.assert_called_once_with('Event', force_call=True)


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


def test_extra_attempt_runs_once_then_disables_without_stage_runner(monkeypatch):
    from module.campaign import event_extra
    factory = Mock()
    factory.return_value.run.return_value = dict(status='observed', combat_attempted=False)
    monkeypatch.setattr(event_extra, 'EventExtraRunner', factory)
    job = EventExtra.__new__(EventExtra)
    job.device = Mock()
    job.ui_get_current_page = Mock()
    job.config = SimpleNamespace(Campaign_Event='event_20261022_cn', Scheduler_Enable=True,
                                 StopCondition_ThenTask='EventStars', task_call=Mock())
    job.run()
    factory.return_value.run.assert_called_once_with('event_20261022_cn')
    assert not job.config.Scheduler_Enable
    job.config.task_call.assert_called_once_with('EventStars', force_call=True)


@pytest.mark.parametrize('job_class', [CampaignStars, EventStars, EventExtra])
def test_new_jobs_detect_closed_game_before_navigation_or_progress_budget(monkeypatch, job_class):
    from module.campaign import event_extra
    job = job_class.__new__(job_class)
    job.ui_get_current_page = Mock(side_effect=GameNotRunningError('Game not running'))
    job.make_plan = Mock()
    job.collect = Mock()
    progress = Mock()
    scout = Mock()
    monkeypatch.setattr(stars, 'StarProgress', progress)
    monkeypatch.setattr(event_extra, 'EventExtraRunner', scout)
    with pytest.raises(GameNotRunningError):
        job.run()
    job.make_plan.assert_not_called()
    job.collect.assert_not_called()
    progress.assert_not_called()
    scout.assert_not_called()


def test_event_pass_dispatches_extra_only_after_all_supported_stages_complete(monkeypatch, tmp_path):
    goals = [StageGoal('event_20261022_cn', stage) for stage in ('T6', 'SP')]
    job, progress, runner, _ = make_job(monkeypatch, tmp_path, goals)
    job.config.StarPass_TryEx = True
    job.config.StarPass_StagesPerRun = 1
    job.extra_objectives = lambda: EventStars.extra_objectives(job)
    job.run()
    job.config.task_call.assert_not_called()
    assert job.config.Scheduler_Enable
    job.run()
    job.config.task_call.assert_called_once_with('EventExtra', force_call=True)
    assert not job.config.Scheduler_Enable


def test_event_pass_does_not_dispatch_extra_after_loss(monkeypatch, tmp_path):
    job, _, _, _ = make_job(monkeypatch, tmp_path, [StageGoal('event_20261022_cn', 'SP')], result='lost')
    job.config.StarPass_TryEx = True
    job.extra_objectives = lambda: EventStars.extra_objectives(job)
    job.run()
    job.config.task_call.assert_called_once_with('Event', force_call=True)


def test_optional_story_failure_does_not_block_stage_goals(monkeypatch, tmp_path):
    goal = StageGoal('event_test', 'T1')
    job, progress, runner, factory = make_job(monkeypatch, tmp_path, [goal])
    job.collect.return_value = dict(story='failed')
    job.run()
    factory.assert_called_once_with(config=job.config, device=job.device)
    runner.run_goal.assert_called_once_with(goal, inspect_only=False)
    assert progress.record(goal)['status'] == 'complete'
    job.config.task_delay.assert_not_called()
    job.config.task_call.assert_called_once_with('Event', force_call=True)


def test_unsafe_story_return_cannot_reserve_a_star_pass_attempt(monkeypatch, tmp_path):
    goal = StageGoal('event_test', 'T1')
    job, progress, _, factory = make_job(monkeypatch, tmp_path, [goal])
    job.collect.side_effect = GamePageUnknownError('Unsafe story return')
    with pytest.raises(GamePageUnknownError, match='Unsafe story return'):
        job.run()
    factory.assert_not_called()
    assert progress.record(goal) == {}
    job.config.task_call.assert_not_called()


def test_event_try_ex_false_excludes_supported_extra_stage(monkeypatch):
    job = EventStars.__new__(EventStars)
    job.config = SimpleNamespace(Campaign_Event='event_test', StarPass_TryEx=False)
    monkeypatch.setattr(stars, 'event_plan', lambda event: [StageGoal(event, 'T1'), StageGoal(event, 'EX', goal='clear')])
    assert [goal.stage for goal in job.make_plan()] == ['T1']


def test_ordinary_event_optional_story_failure_still_loads_campaign(monkeypatch):
    run = CampaignRun.__new__(CampaignRun)
    run.config = SimpleNamespace(task=SimpleNamespace(command='Event'), task_delay=Mock(), override=Mock())
    run.event_completion = Mock(return_value=dict(story='failed', rewards='complete'))
    run.handle_stage_name = Mock(return_value=('t1', 'event_test'))
    run.load_campaign = Mock(side_effect=ScriptEnd('Campaign reached'))
    with pytest.raises(ScriptEnd, match='Campaign reached'):
        run.run('T1', folder='event_test')
    run.event_completion.assert_called_once_with('event_test', before=True)
    run.load_campaign.assert_called_once_with('t1', folder='event_test')
    run.config.task_delay.assert_not_called()


def test_ordinary_event_unsafe_story_return_stops_before_loading_campaign():
    run = CampaignRun.__new__(CampaignRun)
    run.config = SimpleNamespace(task=SimpleNamespace(command='Event'), task_delay=Mock())
    run.event_completion = Mock(side_effect=GamePageUnknownError('Unsafe story return'))
    run.load_campaign = Mock()
    with pytest.raises(GamePageUnknownError, match='Unsafe story return'):
        run.run('T1', folder='event_test')
    run.event_completion.assert_called_once_with('event_test', before=True)
    run.load_campaign.assert_not_called()
    run.config.task_delay.assert_not_called()


def test_ordinary_event_lifecycle_only_runs_story_before_campaign(monkeypatch):
    from module.campaign import event_completion
    factory = Mock()
    result = dict(story='failed', rewards='complete')
    factory.return_value.run.return_value = result
    monkeypatch.setattr(event_completion, 'EventCompletion', factory)
    run = CampaignRun.__new__(CampaignRun)
    run.device = Mock()
    run.config = SimpleNamespace(EventCompletion_CollectRewards=True, EventCompletion_CompleteStory=True)
    assert run.event_completion('event_test', before=True) is result
    assert run.event_completion('event_test') is result
    assert [call.kwargs for call in factory.return_value.run.call_args_list] == [
        dict(event='event_test', collect_rewards=True, complete_story=True),
        dict(event='event_test', collect_rewards=True, complete_story=False)]


def test_both_job_entrypoints_use_shared_runner():
    assert issubclass(EventStars, CampaignStars)
    assert EventStars.run is CampaignStars.run


@pytest.mark.parametrize('entered,attempts,status', [(False, 0, 'deferred'), (True, 1, 'oil_exhausted')])
def test_oil_stop_preserves_delay_and_only_refunds_pre_sortie_reservation(
        monkeypatch, tmp_path, entered, attempts, status):
    runner = StageStarsRunner.__new__(StageStarsRunner)
    runner.config = config()
    runner.campaign = SimpleNamespace(run_lost=False)
    recovered = datetime.now() + timedelta(hours=3)

    def oil_stop(*args, **kwargs):
        runner.run_count = 0
        runner.attempt_started = entered
        runner.oil_deferred = True
        runner.config.Scheduler_NextRun = recovered

    monkeypatch.setattr(runner, 'run', oil_stop)
    goal = StageGoal('event_20261022_cn', 'SP', goal='clear')
    progress = StarProgress('alas', 'EventStars', tmp_path)
    assert complete_stage(goal, progress, runner.run_goal) == status
    assert progress.record(goal) == dict(attempts=attempts, status=status)
    assert runner.config.Scheduler_NextRun == recovered


def test_mid_sortie_oil_stop_does_not_retry_or_overwrite_rest_delay(monkeypatch, tmp_path):
    goal = StageGoal('event_20261022_cn', 'T1')
    job, progress, runner, _ = make_job(monkeypatch, tmp_path, [goal], 'oil_exhausted')
    recovered = datetime.now() + timedelta(hours=3)
    job.config.Scheduler_NextRun = recovered
    job.run()
    assert progress.record(goal) == dict(attempts=1, status='oil_exhausted')
    assert job.config.Scheduler_NextRun == recovered
    runner.run_goal.assert_called_once_with(goal, inspect_only=False)
    job.config.task_delay.assert_not_called()
    job.config.task_call.assert_not_called()
    job.collect.assert_called_once_with(before=True)
    job.extra_objectives.assert_not_called()
