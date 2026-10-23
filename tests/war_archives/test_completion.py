from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from module.campaign.campaign_stars import StageGoal
from module.exception import HardNotSatisfied, RequestHumanTakeover
from module.war_archives.catalog import ARCHIVES, get_archive
from module.war_archives.completion import ArchiveCoordinator, verified_prize
from module.war_archives.planner import ArchivePlan, prize_key, story_key
from module.war_archives.state import ArchiveState


@pytest.fixture
def setup(tmp_path):
    config = SimpleNamespace(config_name='f2p', Scheduler_ServerUpdate='00:00', Archive_DailyTickets=4,
                             Archive_ExtraOilReserve=5000, Archive_StagesPerRun=4,
                             task_switched=lambda: False)
    state = ArchiveState('f2p', str(tmp_path))
    archive = get_archive('war_archives_20181026_en')
    goal = StageGoal(archive.id, archive.normal_stages[0], 'normal', 'archive_clear')
    plan = ArchivePlan(archive, 'normal', (goal,), ())
    observer = SimpleNamespace(config=config, device=Mock(),
                               proof_for_goal=lambda selected: dict(raw_keys=80),
                               prepare_goal=lambda campaign, selected: {})
    return config, state, archive, goal, plan, observer


def factory(outcomes, calls, oil=6000):
    def create():
        runner = SimpleNamespace(proof=dict(raw_keys=80), get_oil=lambda **kwargs: oil)

        def run(goal, proof, reserve, prepare, **kwargs):
            calls.append((goal, kwargs))
            result = dict(outcomes.pop(0))
            if result.get('reserved'):
                if not reserve(goal, raw_keys=5, key_mode='per_entry',
                               attempt_kind=kwargs['attempt_kind']):
                    return dict(status='blocked', reason='reservation_rejected', reserved=False)
            if result.pop('raise_error', False):
                raise RuntimeError('after reservation')
            return result
        runner.run_once = run
        return runner
    return create


def test_completed_inspection_never_uses_budget(setup):
    config, state, archive, goal, plan, observer = setup
    calls = []
    coordinator = ArchiveCoordinator(config, state, observer, factory([
        dict(status='complete', reserved=False, stage_complete=True)], calls))
    assert coordinator.run((plan,))['status'] == 'complete'
    assert state.record(goal.key)['complete'] is True
    assert state.record(story_key(archive))['complete'] is True
    assert state.used(coordinator.period) == 0


def test_four_daily_sorties_then_surplus_policy(setup):
    config, state, _, _, plan, observer = setup
    calls = []
    results = [dict(status='won', reserved=True, boss_won=True)] * 4
    coordinator = ArchiveCoordinator(config, state, observer, factory(results, calls))
    assert coordinator.run((plan,)) == dict(status='pending', sorties=4)
    assert state.used(coordinator.period) == 4
    assert all(item[1]['attempt_kind'] == 'daily' for item in calls)
    second = ArchiveCoordinator(config, state, observer, factory([
        dict(status='won', reserved=True, boss_won=True)], calls, oil=4999))
    result = second.run((plan,))
    assert result['status'] == 'blocked'
    assert calls[-1][1]['attempt_kind'] == 'extra'
    assert state.used(second.period) == 4


def test_crash_preserves_spent_reservation(setup):
    config, state, _, goal, plan, observer = setup
    coordinator = ArchiveCoordinator(config, state, observer, factory([
        dict(status='won', reserved=True, raise_error=True)], []))
    with pytest.raises(RuntimeError):
        coordinator.run((plan,))
    assert state.entries(coordinator.period)[0]['status'] == 'uncertain'
    assert not state.record(goal.key).get('complete')


def test_observe_only_cannot_reserve(setup):
    config, state, _, _, plan, observer = setup
    calls = []
    coordinator = ArchiveCoordinator(config, state, observer, factory([
        dict(status='incomplete', reserved=False)], calls))
    assert coordinator.run((plan,), observe_only=True)['status'] == 'observed'
    assert calls[0][1]['inspect_only'] is True
    assert state.used(coordinator.period) == 0


def test_prize_requires_live_increment_and_claim(setup):
    config, state, archive, _, _, observer = setup
    prize = archive.prizes[0]
    plan = ArchivePlan(archive, prize.mode, (), (prize,))
    counters = iter([dict(verified=True, returned_stage=True, count=59, total=60, claimed=False),
                     dict(verified=True, returned_stage=True, count=60, total=60, claimed=False),
                     dict(verified=True, returned_stage=True, count=60, total=60, claimed=False)])
    observer.observe_prize = lambda archive, prize: next(counters)
    observer.claim_prize = lambda archive, prize: dict(verified=True, returned_stage=True, count=60, total=60, claimed=True)
    coordinator = ArchiveCoordinator(config, state, observer, factory([
        dict(status='won', reserved=True, boss_won=True)], []))
    assert coordinator.run((plan,))['status'] == 'complete'
    assert state.record(prize_key(archive, prize))['claimed'] is True
    assert state.used(coordinator.period) == 1


def test_unknown_counter_never_farms_or_claims(setup):
    config, state, archive, _, _, observer = setup
    prize = archive.prizes[0]
    observer.observe_prize = lambda archive, prize: dict(verified=False, returned_stage=True, count=None)
    coordinator = ArchiveCoordinator(config, state, observer, lambda: pytest.fail('must not enter'))
    result = coordinator.run((ArchivePlan(archive, prize.mode, (), (prize,)),))
    assert result['reason'] == 'prize_counter_unverified'


def test_only_identity_bound_100_percent_skips_route(setup):
    config, state, archive, _, plan, observer = setup
    coordinator = ArchiveCoordinator(config, state, observer, lambda: None)
    for card in [dict(archive=archive.id, completion=100),
                 dict(archive=archive.id, completion=99, completion_verified=True),
                 dict(archive='unknown', completion=100, completion_verified=True)]:
        coordinator.sync_list(dict(pages=[dict(cards=[card])]), (plan,))
        assert state.record(story_key(archive)).get('complete') is not True
    card = dict(archive=archive.id, completion=100, completion_verified=True)
    coordinator.sync_list(dict(pages=[dict(cards=[card])]), (plan,))
    assert state.record(story_key(archive))['complete'] is True


def test_stale_prize_counter_stops_farming(setup):
    config, state, archive, _, _, observer = setup
    prize = archive.prizes[0]
    observer.observe_prize = lambda archive, prize: dict(verified=True, returned_stage=True, count=10, total=60, claimed=False)
    calls = []
    coordinator = ArchiveCoordinator(config, state, observer, factory([
        dict(status='won', reserved=True, boss_won=True)], calls))
    result = coordinator.run((ArchivePlan(archive, prize.mode, (), (prize,)),))
    assert result['reason'] == 'prize_increment_unverified'
    assert len(calls) == 1
    assert state.used(coordinator.period) == 1


def test_loss_is_spent_and_stops_chunk(setup):
    config, state, _, goal, plan, observer = setup
    calls = []
    coordinator = ArchiveCoordinator(config, state, observer, factory([
        dict(status='lost', reserved=True, boss_won=False)], calls))
    assert coordinator.run((plan,))['status'] == 'lost'
    assert state.entries(coordinator.period)[0]['status'] == 'lost'
    assert not state.record(goal.key).get('complete')
    assert len(calls) == 1


@pytest.mark.parametrize('value', [None, {}, dict(verified=True, returned_stage=True, count=True, total=60, claimed=False),
                                  dict(verified=True, returned_stage=True, count=61, total=60, claimed=False),
                                  dict(verified=True, returned_stage=True, count=12, total=60, claimed=True),
                                  dict(verified=True, returned_stage=True, count=5, total=60, claimed=None)])
def test_prize_proof_rejects_unknown(value, setup):
    assert not verified_prize(value, setup[2].prizes[0])


@pytest.mark.parametrize('returned', [None, False, 1, 'true'])
def test_numeric_prize_proof_requires_literal_verified_stage_return(setup, returned):
    value = dict(verified=True, count=12, total=60, claimed=False)
    if returned is not None:
        value['returned_stage'] = returned
    assert not verified_prize(value, setup[2].prizes[0])


@pytest.mark.parametrize('count', [12, 60])
def test_failed_prize_close_cannot_farm_claim_update_or_visit_next_archive(setup, count):
    config, state, archive, _, _, observer = setup
    prize = archive.prizes[0]
    plan = ArchivePlan(archive, prize.mode, (), (prize,))
    observer.observe_prize = Mock(return_value=dict(
        verified=True, returned_stage=False, count=count, total=60, claimed=False,
        reason='prize_panel_close_unrecognized'))
    observer.claim_prize = Mock(side_effect=AssertionError('must not claim'))
    observer.proof_for_goal = Mock(side_effect=AssertionError('must not navigate'))
    coordinator = ArchiveCoordinator(config, state, observer, lambda: pytest.fail('must not farm'))
    result = coordinator.run((plan, setup[4]))
    assert result == dict(status='blocked', reason='prize_panel_close_unrecognized',
                          reserved=False, sorties=0)
    assert state.record(prize_key(archive, prize)) == {}
    observer.observe_prize.assert_called_once_with(archive, prize)
    observer.claim_prize.assert_not_called()
    observer.proof_for_goal.assert_not_called()


def test_failed_claim_return_preserves_prior_counter_and_does_not_accept_claim(setup):
    config, state, archive, _, _, observer = setup
    prize = archive.prizes[0]
    observer.observe_prize = Mock(return_value=dict(
        verified=True, returned_stage=True, count=60, total=60, claimed=False))
    observer.claim_prize = Mock(return_value=dict(
        verified=True, returned_stage=False, count=60, total=60, claimed=True,
        reason='claim_receipt_return_unrecognized'))
    coordinator = ArchiveCoordinator(config, state, observer, lambda: pytest.fail('must not enter'))
    result = coordinator.run((ArchivePlan(archive, prize.mode, (), (prize,)),))
    assert result['reason'] == 'claim_receipt_return_unrecognized'
    assert result['sorties'] == 0
    assert state.record(prize_key(archive, prize))['claimed'] is False
    observer.claim_prize.assert_called_once_with(archive, prize)


def test_post_win_prize_return_failure_preserves_spend_and_old_counter(setup):
    config, state, archive, _, _, observer = setup
    prize = archive.prizes[0]
    observer.observe_prize = Mock(side_effect=[
        dict(verified=True, returned_stage=True, count=12, total=60, claimed=False),
        dict(verified=True, returned_stage=False, count=13, total=60, claimed=False,
             reason='prize_panel_close_unrecognized')])
    calls = []
    coordinator = ArchiveCoordinator(config, state, observer, factory([
        dict(status='won', reserved=True, boss_won=True)], calls))
    result = coordinator.run((ArchivePlan(archive, prize.mode, (), (prize,)), setup[4]))
    assert result['status'] == 'blocked' and result['reason'] == 'prize_panel_close_unrecognized'
    assert result['reserved'] is True and result['sorties'] == 1
    assert state.record(prize_key(archive, prize))['count'] == 12
    assert state.entries(coordinator.period)[0]['status'] == 'won'
    assert len(calls) == 1 and observer.observe_prize.call_count == 2


def second_story_plan(archive):
    second = next(item for item in ARCHIVES if item.supported and item.id != archive.id)
    goal = StageGoal(second.id, second.normal_stages[0], 'normal', 'archive_clear')
    return ArchivePlan(second, 'normal', (goal,), ())


def safe_target_failure(config, goal, reason='target_locked_or_unreadable'):
    return dict(status='blocked', reason=reason, reserved=False,
                safe_target_return=dict(
                    verified=True, proof_kind='fresh_exact_archive_stage',
                    account=config.config_name, event=goal.event, mode=goal.mode,
                    stage=goal.stage.lower(), page=1))


def test_fresh_target_absence_defers_only_that_archive_without_creating_runner(setup):
    config, state, archive, goal, plan, observer = setup
    second = second_story_plan(archive)
    failure = safe_target_failure(config, goal)
    observer.proof_for_goal = Mock(side_effect=[failure, dict(raw_keys=80)])
    calls = []
    create = Mock(side_effect=factory([
        dict(status='complete', reserved=False, stage_complete=True)], calls))
    coordinator = ArchiveCoordinator(config, state, observer, create)
    result = coordinator.run((plan, second))
    assert result['status'] == 'blocked' and result['reason'] == 'target_unavailable'
    assert result['sorties'] == 0
    assert result['deferred_archives'] == [dict(
        archive=archive.id, reason=failure['reason'], safe_target_return=failure['safe_target_return'])]
    assert [item[0] for item in calls] == [second.stages[0]]
    create.assert_called_once()
    assert not state.record(story_key(archive)).get('complete')
    assert state.record(story_key(second.archive))['complete'] is True
    assert state.used(coordinator.period) == 0


@pytest.mark.parametrize('field,value', [
    ('verified', False), ('verified', 1), ('proof_kind', 'stage_background'),
    ('account', 'alas'), ('event', 'other_archive'), ('mode', 'hard'),
    ('stage', 'unknown'), ('page', -1), ('page', True), ('page', None)])
def test_invalid_safe_return_never_advances_to_next_target(setup, field, value):
    config, state, archive, goal, plan, observer = setup
    failure = safe_target_failure(config, goal)
    failure['safe_target_return'][field] = value
    calls = []
    coordinator = ArchiveCoordinator(config, state, observer, factory([failure], calls))
    result = coordinator.run((plan, second_story_plan(archive)))
    assert result['status'] == 'blocked' and result['reason'] == failure['reason']
    assert 'deferred_archives' not in result
    assert [item[0] for item in calls] == [goal]


@pytest.mark.parametrize('status,reason,reserved', [
    ('blocked', 'unknown_ui', False), ('blocked', 'stage_navigation_unverified', False),
    ('blocked', 'oil_reserve', False), ('deferred', 'morale', False),
    ('lost', 'lost', True), ('blocked', 'hard_lineup', True)])
def test_safe_page_cannot_swallow_global_stops_or_spent_attempts(setup, status, reason, reserved):
    config, state, archive, goal, plan, observer = setup
    failure = safe_target_failure(config, goal)
    failure.update(status=status, reason=reason, reserved=reserved)
    calls = []
    coordinator = ArchiveCoordinator(config, state, observer, factory([failure], calls))
    result = coordinator.run((plan, second_story_plan(archive)))
    assert result['status'] == status and result['reason'] == reason
    assert 'deferred_archives' not in result
    assert [item[0] for item in calls] == [goal]
    assert state.used(coordinator.period) == int(reserved)


def test_explicit_safe_unspent_hard_return_can_defer_to_another_archive(setup):
    config, state, archive, goal, plan, observer = setup
    second = second_story_plan(archive)
    calls = []
    coordinator = ArchiveCoordinator(config, state, observer, factory([
        safe_target_failure(config, goal, reason='hard_lineup'),
        dict(status='complete', reserved=False, stage_complete=True)], calls))
    result = coordinator.run((plan, second))
    assert result['status'] == 'blocked' and result['reason'] == 'hard_lineup'
    assert [item[0] for item in calls] == [goal, second.stages[0]]
    assert not state.record(story_key(archive)).get('complete')
    assert state.record(story_key(second.archive))['complete'] is True
    assert state.used(coordinator.period) == 0


def received_proof():
    return dict(verified=True, claimed=True, count=None, total=60,
                proof_kind='source_received_hidden_button', reason='prize_already_claimed',
                archive='war_archives_20190321_en', prize_id=307061,
                returned_stage=True, diagnostics=['/capture/prize_already_claimed.png'])


def test_exact_received_button_proof_needs_no_fabricated_counter():
    prize = get_archive('war_archives_20190321_en').prizes[0]
    assert verified_prize(received_proof(), prize)


@pytest.mark.parametrize('field,value', [
    ('verified', False), ('claimed', False), ('proof_kind', None),
    ('reason', 'unverified'), ('archive', 'war_archives_20181026_en'),
    ('prize_id', 105121), ('returned_stage', False), ('diagnostics', [])])
def test_received_proof_rejects_unbound_or_incomplete_evidence(field, value):
    proof = received_proof()
    proof[field] = value
    assert not verified_prize(proof, get_archive('war_archives_20190321_en').prizes[0])


def test_other_difficulty_prizes_remain_explicitly_deferred(setup):
    config, state, archive, goal, plan, observer = setup
    state.update(story_key(archive), complete=True)
    plan = ArchivePlan(archive, 'normal', (goal,), (), archive.prizes)
    result = ArchiveCoordinator(config, state, observer, lambda: pytest.fail('no sortie')).run((plan,))
    assert result == dict(status='deferred_prizes', reason='prizes_require_other_difficulty',
                          sorties=0, deferred_prizes=1)


@pytest.mark.parametrize('reserved', [False, True])
def test_hard_lineup_without_exact_return_stops_before_next_archive(setup, reserved):
    config, state, archive, goal, plan, observer = setup
    second_archive = next(item for item in ARCHIVES if item.supported and item.id != archive.id)
    second_goal = StageGoal(second_archive.id, second_archive.normal_stages[0], 'normal', 'archive_clear')
    second_plan = ArchivePlan(second_archive, 'normal', (second_goal,), ())
    calls = []

    def create():
        runner = SimpleNamespace(proof=dict(raw_keys=80), get_oil=lambda **kwargs: 6000)

        def run(selected, proof, reserve, prepare, **kwargs):
            calls.append(selected)
            if selected == goal:
                if reserved:
                    assert reserve(selected, raw_keys=5, key_mode='per_entry', attempt_kind='daily')
                raise HardNotSatisfied('missing DD ' + 'x' * 600)
            return dict(status='complete', reserved=False, stage_complete=True)
        runner.run_once = run
        return runner

    coordinator = ArchiveCoordinator(config, state, observer, create)
    result = coordinator.run((plan, second_plan))
    assert result['status'] == 'blocked' and result['reason'] == 'hard_lineup'
    assert calls == [goal]
    assert len(result['diagnostic']) == 500
    assert not state.record(goal.key).get('complete')
    assert not state.record(story_key(archive)).get('complete')
    assert not state.record(story_key(second_archive)).get('complete')
    assert state.used(coordinator.period) == int(reserved)
    if reserved:
        assert state.entries(coordinator.period)[0]['status'] == 'uncertain'
        assert state.entries(coordinator.period)[0]['evidence']['reason'] == 'hard_lineup'


def test_all_hard_lineups_blocked_never_report_complete_or_retry_same_archive(setup):
    config, state, archive, goal, plan, observer = setup
    calls = []

    def proof(selected):
        calls.append(selected)
        raise HardNotSatisfied('missing fleet')
    observer.proof_for_goal = proof
    result = ArchiveCoordinator(config, state, observer, lambda: pytest.fail('no runner')).run((plan,))
    assert result['status'] == 'blocked' and result['reason'] == 'hard_lineup'
    assert result['sorties'] == 0 and calls == [goal]
    assert not state.record(story_key(archive)).get('complete')


def test_other_takeover_errors_are_not_mistaken_for_hard_lineup(setup):
    config, state, _, _, plan, observer = setup
    observer.proof_for_goal = Mock(side_effect=RequestHumanTakeover('unknown UI'))
    with pytest.raises(RequestHumanTakeover, match='unknown UI'):
        ArchiveCoordinator(config, state, observer, lambda: None).run((plan,))


@pytest.mark.parametrize('observed,observe_only', [(False, False), (False, True), (True, False)])
def test_list_scan_cache_and_fresh_target_probe(setup, monkeypatch, observed, observe_only):
    from module.war_archives import completion
    config, state, _, goal, plan, observer = setup
    config.config_name = 'f2p'
    config.Archive_Target = 'all'
    config.Archive_Difficulty = 'hard'
    config.Archive_Goal = 'stories'
    config.Archive_Prize = 'auto'
    config.Archive_ObserveOnly = observe_only
    config.task_delay = Mock()
    observer.run_observe = Mock(return_value=dict(
        status='observed' if observed else 'blocked', reason='partial_list', pages=[], journal='capture.json'))
    observer.proof_for_goal = Mock(return_value=dict(raw_keys=80))
    monkeypatch.setattr(completion, 'build_plan', lambda *args: ((plan,), ()))
    monkeypatch.setattr(completion, 'ArchiveProgress', lambda **kwargs: observer)
    monkeypatch.setattr(completion, 'ArchiveState', lambda *args: state)
    calls = []
    runner = factory([dict(status='complete', reserved=False, stage_complete=True)], calls)
    monkeypatch.setattr(completion, 'WarArchivesExecution', lambda **kwargs: runner())
    job = object.__new__(completion.WarArchivesComplete)
    job.config, job.device = config, object()
    if observe_only:
        # The diagnostic first-goal probe must run even when the route is already complete.
        state.update(goal.key, complete=True)
    job.run()
    observer.proof_for_goal.assert_called_once_with(goal)
    assert observer.run_observe.call_count == (0 if observe_only else 1)
    assert bool(state.record('_list_scan').get('period')) is observed
    assert len(calls) == (0 if observe_only else 1)
    assert state.used(ArchiveCoordinator(config, state, observer, runner).period) == 0


def test_unsupported_only_selection_never_reports_complete(setup, monkeypatch):
    from module.war_archives import completion
    config = setup[0]
    config.Archive_Target = 'Frostfall'
    config.Archive_Difficulty = 'hard'
    config.Archive_Goal = 'stories'
    config.Archive_Prize = 'auto'
    config.task_delay = Mock()
    monkeypatch.setattr(completion, 'build_plan', lambda *args: ((), ('Frostfall',)))
    monkeypatch.setattr(completion, 'ArchiveProgress', lambda **kwargs: pytest.fail('no supported route'))
    attribute = Mock()
    monkeypatch.setattr(completion.logger, 'attr', attribute)
    job = object.__new__(completion.WarArchivesComplete)
    job.config, job.device = config, object()
    job.run()
    attribute.assert_called_once_with('WarArchivesResult', dict(
        status='unsupported', sorties=0, unsupported_archives=('Frostfall',)))
    config.task_delay.assert_called_once_with(server_update=True)


@pytest.mark.parametrize('mode', ['normal', 'hard'])
def test_diagnostic_preparation_uses_nonspending_inspection(setup, monkeypatch, mode):
    from module.war_archives import completion
    config, state, archive, goal, plan, observer = setup
    goal = StageGoal(goal.event, goal.stage, mode, goal.goal)
    plan = ArchivePlan(archive, mode, (goal,), ())
    config.config_name, config.Archive_Target = 'f2p', 'all'
    config.Archive_Difficulty, config.Archive_Goal = mode, 'items_and_stories'
    config.Archive_Prize, config.Archive_ObserveOnly = 'auto', True
    config.task_delay = Mock()
    proof = dict(verified_for_navigation=True)
    observer.proof_for_goal = Mock(return_value=proof)
    observer.observe_prize = Mock(return_value=dict(verified=False))
    runner = Mock()
    runner.run_once.return_value = dict(status='observed', reserved=False)
    monkeypatch.setattr(completion, 'build_plan', lambda *args: ((plan,), ()))
    monkeypatch.setattr(completion, 'ArchiveProgress', lambda **kwargs: observer)
    monkeypatch.setattr(completion, 'ArchiveState', lambda *args: state)
    monkeypatch.setattr(completion, 'WarArchivesExecution', lambda **kwargs: runner)
    job = object.__new__(completion.WarArchivesComplete)
    job.config, job.device = config, object()
    job.run()
    args, kwargs = runner.run_once.call_args
    assert args[0] == goal and args[1] is proof
    assert args[2](goal) is False
    assert kwargs['inspect_only'] is (mode == 'normal')
    assert kwargs['inspect_fleet_only'] is (mode == 'hard')
    observer.observe_prize.assert_called_once_with(archive, archive.prizes[0])
    assert state.used(ArchiveCoordinator(config, state, observer, lambda: None).period) == 0


def test_pending_claim_recovery_precedes_other_selected_archive(setup, monkeypatch):
    from module.war_archives import completion
    config, state, _, _, plan, observer = setup
    archive = get_archive('war_archives_20190321_en')
    prize = archive.prizes[0]
    calls = []

    def resume(selected, reward, folder):
        calls.append((selected, reward.id, folder))
        if selected == archive.id:
            return dict(verified=True, returned_stage=True, count=60, total=60,
                        claimed=True, proof_kind='owned_claim_receipt')
        return None

    monkeypatch.setattr(completion, 'ArchivePrizeProgress', lambda owner: SimpleNamespace(resume=resume))
    observer.proof_for_goal = Mock(side_effect=lambda goal: (
        dict(raw_keys=80) if state.record(prize_key(archive, prize)).get('claimed') is True
        else pytest.fail('navigation before pending receipt recovery')))
    coordinator = ArchiveCoordinator(config, state, observer, factory([
        dict(status='complete', reserved=False, stage_complete=True)], []), prize_folder='owned-journals')
    assert coordinator.run((plan,))['status'] == 'complete'
    assert (archive.id, prize.id, 'owned-journals') in calls
    assert state.record(prize_key(archive, prize))['claimed'] is True
    assert state.used(coordinator.period) == 0


def test_unknown_pending_claim_stops_before_counter_farming_or_navigation(setup, monkeypatch):
    from module.war_archives import completion
    config, state, _, _, plan, observer = setup
    observer.proof_for_goal = Mock(side_effect=AssertionError('no navigation'))
    observer.observe_prize = Mock(side_effect=AssertionError('no fresh counter first'))
    resume = Mock(return_value=dict(verified=False, returned_stage=False,
                                   reason='prize_claim_pending_unverified'))
    monkeypatch.setattr(completion, 'ArchivePrizeProgress', lambda owner: SimpleNamespace(resume=resume))
    coordinator = ArchiveCoordinator(config, state, observer,
                                     lambda: pytest.fail('no farm while claim uncertain'))
    result = coordinator.run((plan,))
    assert result == dict(status='blocked', reason='prize_claim_pending_unverified',
                          reserved=False, sorties=0)
    assert not state.data['goals']
    observer.proof_for_goal.assert_not_called()
    observer.observe_prize.assert_not_called()
    resume.assert_called_once()


def test_job_recovers_pending_claim_before_list_census(setup, monkeypatch):
    from module.war_archives import completion
    config, state, _, _, plan, observer = setup
    config.Archive_Target, config.Archive_Difficulty = 'all', 'normal'
    config.Archive_Goal, config.Archive_Prize, config.Archive_ObserveOnly = 'stories', 'auto', False
    config.task_delay = Mock()
    observer.run_observe = Mock(side_effect=AssertionError('no census through owned receipt'))
    monkeypatch.setattr(completion, 'build_plan', lambda *args: ((plan,), ()))
    monkeypatch.setattr(completion, 'ArchiveProgress', lambda **kwargs: observer)
    monkeypatch.setattr(completion, 'ArchiveState', lambda *args: state)
    monkeypatch.setattr(completion, 'ArchivePrizeProgress', lambda owner: SimpleNamespace(
        resume=lambda *args, **kwargs: dict(verified=False, returned_stage=False,
                                          reason='prize_claim_pending_unverified')))
    job = object.__new__(completion.WarArchivesComplete)
    job.config, job.device = config, observer.device
    job.run()
    observer.run_observe.assert_not_called()
    config.task_delay.assert_called_once_with(success=False)


def test_observe_only_never_resumes_or_dismisses_pending_claim(setup, monkeypatch):
    from module.war_archives import completion
    config, state, _, _, plan, observer = setup
    monkeypatch.setattr(completion, 'ArchivePrizeProgress',
                        lambda owner: pytest.fail('diagnostic cannot resume a claim'))
    coordinator = ArchiveCoordinator(config, state, observer, factory([
        dict(status='incomplete', reserved=False)], []))
    assert coordinator.run((plan,), observe_only=True)['status'] == 'observed'
