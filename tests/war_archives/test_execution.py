from types import SimpleNamespace
from unittest.mock import Mock
from pathlib import Path

import numpy as np
from PIL import Image
import pytest

from module.campaign.campaign_stars import StageGoal
from module.campaign.campaign_status import OilReadError
from module.config.config import TaskEnd
from module.exception import OilExhausted, ScriptEnd
from module.war_archives import execution as ex
from module.war_archives.completion import ArchiveCoordinator
from module.war_archives.state import ArchiveState


EVENT = 'war_archives_20190321_en'


class Config(SimpleNamespace):
    def __getattr__(self, name):
        return 'previous'

    def override(self, **values):
        self.overridden.update(values)
        self.__dict__.update(values)


def controller(monkeypatch, *, progress=(False, True), one_clear=False, lost=False,
               boss_won=True, run_error=None):
    owner = ex.WarArchivesExecution.__new__(ex.WarArchivesExecution)
    owner.config = Config(config_name='alas', overridden={'kept': True},
                          check_task_switch=Mock(), task_switched=Mock(return_value=False), task_call=Mock())
    owner.device = SimpleNamespace(screenshot=Mock(), click=Mock())
    owner.get_oil = Mock(return_value=5000)
    owner.defer_for_oil = Mock()
    state = SimpleNamespace(in_map=False, in_stage=True, starts=0, progress=iter(progress))
    campaign = SimpleNamespace(
        device=owner.device, config=SimpleNamespace(MAP_HAS_CLEAR_PERCENTAGE=not one_clear,
                                                    MAP_IS_ONE_TIME_STAGE=one_clear),
        ENTRANCE=object(), combat_won=False, combat_lost=False, run_lost=False,
        _map_battle=5, emotion=SimpleNamespace(check_reduce=Mock()),
        is_in_map=lambda: state.in_map, is_in_stage=lambda: state.in_stage,
        handle_oil_purchase=Mock(return_value=False), appear=Mock(return_value=False),
        withdraw=Mock(), execute_a_battle=Mock(), fleet_preparation=Mock(),
        triggered_map_stop=lambda: next(state.progress))

    def enter(*args, **kwargs):
        campaign.handle_oil_purchase()
        campaign.triggered_map_stop()
        campaign.fleet_preparation()
        assert owner.reserved, 'Start before durable reservation'
        state.starts += 1
        state.in_map, state.in_stage = True, False
        return True

    def status(drop=None, expected_end=None):
        state.in_map, state.in_stage = False, True
        campaign.combat_won = boss_won
        campaign.run_lost = campaign.combat_lost = lost

    def run():
        campaign.enter_map(campaign.ENTRANCE, mode=owner.goal.mode)
        if run_error:
            raise run_error
        campaign.execute_a_battle()
        campaign.combat_status(expected_end='in_stage')

    campaign.enter_map, campaign.combat_status, campaign.run = enter, status, Mock(side_effect=run)
    owner.load_campaign = Mock(side_effect=lambda *args: setattr(owner, 'campaign', campaign))
    owner._cancel_preparation = Mock(return_value=True)
    owner.prepare = Mock()
    owner.name = 'a1'  # Regression: a cached same-name stage must be discarded.
    monkeypatch.setattr(ex, 'stage_names', Mock(return_value=['a1', 'd3']))
    proof = dict(account='alas', event=EVENT, stage='A1', mode='normal',
                 verified_for_navigation=True, verified_for_execution=True,
                 raw_keys=300, key_capacity=300)
    prepare = Mock(side_effect=lambda *args: dict(proof))
    reserve = Mock(return_value=True)
    return owner, campaign, state, proof, prepare, reserve


def invoke(owner, proof, prepare, reserve, **kwargs):
    goal = kwargs.pop('goal', StageGoal(EVENT, 'A1', goal='archive_clear'))
    options = dict(key_mode='per_entry', key_cost=5)
    options.update(kwargs)
    return owner.run_once(goal, proof, reserve, prepare, **options)


def test_one_sortie_reserved_before_start_then_fresh_100_percent_inspection(monkeypatch):
    owner, campaign, state, proof, prepare, reserve = controller(monkeypatch)
    before = dict(owner.config.overridden)
    result = invoke(owner, proof, prepare, reserve)
    assert result == dict(status='complete', reason='', reserved=True, boss_won=True, stage_complete=True)
    assert state.starts == 1 and owner.entry_calls == 2 and prepare.call_count == 2
    reserve.assert_called_once_with(owner.goal, raw_keys=5, key_mode='per_entry', attempt_kind='daily')
    owner.load_campaign.assert_called_once_with('a1', EVENT)
    assert owner.config.overridden == before and owner.config.Campaign_UseAutoSearch == 'previous'
    assert campaign.first_run is False


@pytest.mark.parametrize('field,value', [
    ('verified_for_navigation', False), ('verified_for_navigation', 1),
    ('account', 'f2p'), ('event', 'war_archives_other'), ('stage', 'D3'), ('mode', 'hard')])
def test_unknown_or_other_goal_navigation_never_loads_or_reserves(monkeypatch, field, value):
    owner, _, _, proof, prepare, reserve = controller(monkeypatch)
    proof[field] = value
    assert invoke(owner, proof, prepare, reserve)['status'] == 'blocked'
    owner.load_campaign.assert_not_called()
    prepare.assert_not_called()
    reserve.assert_not_called()
    owner.device.click.assert_not_called()


@pytest.mark.parametrize('goal', [StageGoal('campaign_main', 'A1', goal='archive_clear'),
    StageGoal('war_archives_unknown', 'A1', goal='archive_clear'),
    StageGoal(EVENT, 'D3', 'normal', 'archive_clear'),
    StageGoal(EVENT, 'A1', 'hard', 'archive_clear'),
    StageGoal(EVENT, 'A1', goal='three_stars'), StageGoal(EVENT, 'unknown', goal='archive_clear')])
def test_catalog_stage_and_mode_cannot_be_substituted(monkeypatch, goal):
    owner, _, _, proof, prepare, reserve = controller(monkeypatch)
    proof.update(event=goal.event, stage=goal.stage, mode=goal.mode)
    assert invoke(owner, proof, prepare, reserve, goal=goal)['status'] == 'blocked'
    owner.load_campaign.assert_not_called()


@pytest.mark.parametrize('updates,options', [
    ({'verified_for_execution': False}, {}), ({'verified_for_execution': 1}, {}),
    ({'raw_keys': 4}, {}), ({'raw_keys': 301}, {}), ({'raw_keys': True}, {}),
    ({'key_capacity': 60}, {}), ({'key_capacity': '300'}, {}),
    ({}, {'key_mode': 'timed'}), ({}, {'key_cost': 1}), ({}, {'key_cost': True})])
def test_only_current_fresh_per_entry_key_rules_authorize_combat(monkeypatch, updates, options):
    owner, _, _, proof, prepare, reserve = controller(monkeypatch)
    proof.update(updates)
    result = invoke(owner, proof, prepare, reserve, **options)
    assert result['reason'] == 'key_entitlement_unverified'
    owner.load_campaign.assert_not_called()
    reserve.assert_not_called()


@pytest.mark.parametrize('complete', [False, True])
def test_inspection_has_no_key_or_morale_requirement_and_never_reserves(monkeypatch, complete):
    owner, campaign, state, proof, prepare, reserve = controller(monkeypatch, progress=(complete,))
    proof.update(raw_keys=0, verified_for_execution=False)
    result = invoke(owner, proof, prepare, reserve, inspect_only=True)
    assert result['status'] == ('complete' if complete else 'incomplete')
    assert state.starts == 0 and result['reserved'] is False
    reserve.assert_not_called()
    owner.get_oil.assert_not_called()
    owner._cancel_preparation.assert_called_once_with(campaign)


def test_already_complete_skips_morale_oil_and_attempt(monkeypatch):
    owner, _, state, proof, prepare, reserve = controller(monkeypatch, progress=(True,))
    assert invoke(owner, proof, prepare, reserve)['status'] == 'complete'
    assert state.starts == 0
    reserve.assert_not_called()
    owner.get_oil.assert_not_called()


@pytest.mark.parametrize('kind,oil,expected', [('daily', 500, 'complete'), ('daily', 499, 'deferred'),
    ('extra', 5000, 'complete'), ('extra', 4999, 'deferred'), ('daily', True, 'deferred')])
def test_daily_four_and_surplus_have_distinct_oil_floors(monkeypatch, kind, oil, expected):
    owner, _, state, proof, prepare, reserve = controller(monkeypatch)
    owner.get_oil.return_value = oil
    result = invoke(owner, proof, prepare, reserve, attempt_kind=kind)
    assert result['status'] == expected
    assert owner.oil_floor == (500 if kind == 'daily' else 5000)
    assert state.starts == int(expected == 'complete')
    if expected == 'deferred':
        assert result['reason'] == 'oil_reserve'
        reserve.assert_not_called()
        owner.defer_for_oil.assert_called_once()


@pytest.mark.parametrize('kind,oil,spent_before', [('daily', 500, 0), ('extra', 5000, 4)])
@pytest.mark.parametrize('boundary', ['preflight', 'reservation', 'between_battles'])
def test_unreadable_oil_short_retry_preserves_shared_archive_budget(
        monkeypatch, tmp_path, kind, oil, spent_before, boundary):
    owner, campaign, state, proof, prepare, _ = controller(monkeypatch)
    owner.config.Scheduler_ServerUpdate = '00:00'
    owner.config.Archive_DailyTickets = 4
    owner.config.Archive_ExtraOilReserve = 5000
    owner.config.task_delay = Mock()
    owner.config.task_call = Mock()
    ledger = ArchiveState('alas', str(tmp_path))
    observer = SimpleNamespace(proof_for_goal=lambda goal: dict(proof), prepare_goal=prepare)
    coordinator = ArchiveCoordinator(owner.config, ledger, observer, lambda: owner)
    goal = StageGoal(EVENT, 'A1', goal='archive_clear')
    for _ in range(spent_before):
        assert ledger.reserve(coordinator.period, goal.key, oil=5000, raw_keys=300)
        ledger.finish(coordinator.period, goal.key, 'won')
    readings_before_failure = {'preflight': 0, 'reservation': 1, 'between_battles': 2}[boundary]
    owner.get_oil.side_effect = [oil] * readings_before_failure + [OilReadError('unreadable')]

    result = coordinator._attempt(goal)

    entered = boundary == 'between_battles'
    assert result == dict(status='deferred', reason='oil_unverified', reserved=entered,
                         boss_won=False, stage_complete=False)
    assert owner.oil_floor == (500 if kind == 'daily' else 5000)
    assert state.starts == int(entered)
    assert ledger.used(coordinator.period) == spent_before + int(entered)
    assert not ledger.has_loss(coordinator.period)
    assert not ledger.record(goal.key).get('complete')
    if entered:
        assert ledger.entries(coordinator.period)[-1]['status'] == 'uncertain'
        campaign.withdraw.assert_called_once_with(timeout=20)
    else:
        assert all(entry['status'] == 'won' for entry in ledger.entries(coordinator.period))
        campaign.withdraw.assert_not_called()
        owner._cancel_preparation.assert_called_once_with(campaign)
    owner.config.task_delay.assert_called_once_with(minute=1)
    owner.config.task_call.assert_not_called()
    owner.defer_for_oil.assert_not_called()
    assert owner.oil_deferred is False
    owner.device.click.assert_not_called()


def test_unreadable_oil_preserves_short_retry_if_preparation_cleanup_fails(monkeypatch):
    owner, campaign, state, proof, prepare, reserve = controller(monkeypatch)
    owner.config.task_delay = Mock()
    owner.get_oil.side_effect = OilReadError('unreadable')
    owner._cancel_preparation.side_effect = ex.ArchiveBlocked('preparation_return_unrecognized')
    result = invoke(owner, proof, prepare, reserve)
    assert result['status'] == 'deferred' and result['reason'] == 'oil_unverified'
    assert result['reserved'] is False and state.starts == 0
    reserve.assert_not_called()
    owner.config.task_delay.assert_called_once_with(minute=1)
    owner.defer_for_oil.assert_not_called()


def test_extra_reserve_is_cutoff_not_base_plus_reserve(monkeypatch):
    owner, _, _, proof, prepare, reserve = controller(monkeypatch)
    owner.get_oil.return_value = 500
    assert invoke(owner, proof, prepare, reserve, attempt_kind='extra', extra_oil_reserve=0)['status'] == 'complete'
    assert owner.oil_floor == 500


def test_new_navigation_proof_cannot_reuse_stale_execution_authorization(monkeypatch):
    owner, _, state, proof, prepare, reserve = controller(monkeypatch)
    prepare.side_effect = lambda *args: dict(proof, raw_keys=4)
    result = invoke(owner, proof, prepare, reserve)
    assert result['reason'] == 'execution_unverified' and state.starts == 0
    reserve.assert_not_called()


@pytest.mark.parametrize('accepted', [False, None, 1, 'yes'])
def test_reservation_requires_literal_true_before_start(monkeypatch, accepted):
    owner, _, state, proof, prepare, reserve = controller(monkeypatch)
    reserve.return_value = accepted
    assert invoke(owner, proof, prepare, reserve)['reason'] == 'reservation_rejected'
    assert state.starts == 0


def test_durable_write_failure_prevents_start_and_propagates(monkeypatch):
    owner, _, state, proof, prepare, reserve = controller(monkeypatch)
    reserve.side_effect = OSError('write failed')
    with pytest.raises(OSError) as error:
        invoke(owner, proof, prepare, reserve)
    assert error.value.archive_reserved is False and state.starts == 0
    assert owner.config.StopCondition_ThenTask == 'previous'


def test_morale_deferral_preserves_schedule_and_no_reservation(monkeypatch):
    owner, campaign, state, proof, prepare, reserve = controller(monkeypatch)
    def morale(_):
        owner.config.NextRun = 'morale time'
        raise ScriptEnd('morale')
    campaign.emotion.check_reduce.side_effect = morale
    assert invoke(owner, proof, prepare, reserve)['status'] == 'deferred'
    assert owner.config.NextRun == 'morale time' and state.starts == 0
    reserve.assert_not_called()


def test_scheduler_preemption_after_reservation_keeps_budget_spent(monkeypatch):
    owner, _, state, proof, prepare, reserve = controller(monkeypatch)
    def switched(_):
        if reserve.called:
            raise TaskEnd('new task')
    owner.config.check_task_switch.side_effect = switched
    with pytest.raises(TaskEnd) as error:
        invoke(owner, proof, prepare, reserve)
    assert error.value.archive_reserved is True and state.starts == 0
    reserve.assert_called_once()


def test_preparation_cleanup_failure_does_not_replace_scheduler_task_end(monkeypatch):
    owner, _, state, proof, prepare, reserve = controller(monkeypatch)
    preempted = TaskEnd('new task')

    def switched(_):
        if reserve.called:
            raise preempted

    owner.config.check_task_switch.side_effect = switched
    owner._cancel_preparation.side_effect = ex.ArchiveBlocked('preparation_return_unrecognized')
    with pytest.raises(TaskEnd) as error:
        invoke(owner, proof, prepare, reserve)
    assert error.value is preempted
    assert error.value.archive_reserved is True
    assert error.value.archive_cleanup_error == 'preparation_return_unrecognized'
    assert state.starts == 0
    reserve.assert_called_once()


def test_preparation_cleanup_failure_preserves_morale_deferral(monkeypatch):
    owner, campaign, state, proof, prepare, reserve = controller(monkeypatch)
    campaign.emotion.check_reduce.side_effect = ScriptEnd('morale')
    owner._cancel_preparation.side_effect = ex.ArchiveBlocked('preparation_return_unrecognized')
    result = invoke(owner, proof, prepare, reserve)
    assert result['status'] == 'deferred' and result['reason'] == 'morale'
    assert result['reserved'] is False and state.starts == 0
    reserve.assert_not_called()


def test_preparation_cleanup_failure_preserves_original_oil_reason(monkeypatch):
    owner, _, state, proof, prepare, reserve = controller(monkeypatch)
    owner.get_oil.return_value = 499
    owner._cancel_preparation.side_effect = ex.ArchiveBlocked('preparation_return_unrecognized')
    result = invoke(owner, proof, prepare, reserve)
    assert result['status'] == 'deferred' and result['reason'] == 'oil_reserve'
    assert result['reserved'] is False and state.starts == 0
    reserve.assert_not_called()


def test_between_battle_preemption_withdraws_only_known_map(monkeypatch):
    owner, campaign, state, proof, prepare, reserve = controller(monkeypatch)
    owner.config.task_switched.return_value = True
    with pytest.raises(TaskEnd) as error:
        invoke(owner, proof, prepare, reserve)
    campaign.withdraw.assert_called_once_with(timeout=20)
    assert error.value.archive_reserved is True and state.starts == 1


def test_between_battle_oil_drop_withdraws_and_does_not_reenter(monkeypatch):
    owner, campaign, state, proof, prepare, reserve = controller(monkeypatch)
    owner.get_oil.side_effect = [500, 499]
    result = invoke(owner, proof, prepare, reserve)
    assert result['status'] == 'deferred' and result['reason'] == 'oil_reserve'
    assert result['reserved'] is True
    assert state.starts == 1
    campaign.withdraw.assert_called_once_with(timeout=20)


@pytest.mark.parametrize('lost,boss,expected', [(True, True, 'lost'), (False, False, 'blocked')])
def test_loss_or_ordinary_return_is_not_a_stage_clear(monkeypatch, lost, boss, expected):
    owner, _, state, proof, prepare, reserve = controller(monkeypatch, lost=lost, boss_won=boss)
    result = invoke(owner, proof, prepare, reserve)
    assert result['status'] == expected and result['stage_complete'] is False
    assert prepare.call_count == 1 and state.starts == 1


def test_boss_victory_not_100_percent_is_won_but_progress_incomplete(monkeypatch):
    owner, _, state, proof, prepare, reserve = controller(monkeypatch, progress=(False, False))
    result = invoke(owner, proof, prepare, reserve)
    assert result['status'] == 'won' and result['stage_complete'] is False
    assert state.starts == 1


def test_prize_farming_permits_one_run_of_already_100_percent_stage(monkeypatch):
    owner, _, state, proof, prepare, reserve = controller(monkeypatch, progress=(True,))
    result = invoke(owner, proof, prepare, reserve,
                    goal=StageGoal(EVENT, 'A1', goal='archive_prize'))
    assert result['status'] == 'won' and result['reason'] == 'prize_counter_requires_observation'
    assert state.starts == 1 and prepare.call_count == 1


def test_one_time_node_completes_only_on_verified_boss_win(monkeypatch):
    owner, _, state, proof, prepare, reserve = controller(monkeypatch, one_clear=True)
    result = invoke(owner, proof, prepare, reserve)
    assert result['status'] == 'complete' and result['reason'] == 'one_clear_boss_victory'
    assert prepare.call_count == 1 and state.starts == 1


def test_one_time_inspection_unknown_does_not_assume_complete(monkeypatch):
    owner, _, state, proof, prepare, reserve = controller(monkeypatch, one_clear=True)
    result = invoke(owner, proof, prepare, reserve, inspect_only=True)
    assert result['status'] == 'incomplete' and state.starts == 0


def test_existing_unowned_map_prevents_even_prepare_navigation(monkeypatch):
    owner, _, state, proof, prepare, reserve = controller(monkeypatch)
    state.in_map = True
    result = invoke(owner, proof, prepare, reserve)
    assert result['reason'] == 'unowned_map_in_progress'
    prepare.assert_not_called()
    reserve.assert_not_called()


@pytest.mark.parametrize('modal,reason', [(ex.USE_DATA_KEY, 'unverified_data_key_dialog'),
    (ex.POPUP_CONFIRM, 'unrecognized_entry_dialog'), (ex.GET_ITEMS_1, 'unowned_receipt')])
def test_entry_dialog_never_uses_generic_confirm_or_start(monkeypatch, modal, reason):
    owner, campaign, state, proof, prepare, reserve = controller(monkeypatch)
    campaign.appear.side_effect = lambda button, **kwargs: button is modal
    result = invoke(owner, proof, prepare, reserve)
    assert result['reason'] == reason and state.starts == 0
    owner.device.click.assert_not_called()
    reserve.assert_not_called()


@pytest.mark.parametrize('after_go', [False, True])
@pytest.mark.parametrize('modal,reason,tag', [
    (ex.USE_DATA_KEY, 'unverified_data_key_dialog', 'archive_data_key_dialog'),
    (ex.POPUP_CONFIRM_WHITE, 'unrecognized_entry_dialog', 'archive_entry_dialog')])
def test_entry_dialog_captures_current_frame_and_keeps_reservation(monkeypatch, after_go, modal, reason, tag):
    owner, campaign, state, proof, prepare, reserve = controller(monkeypatch)
    visible = [None if after_go else modal]
    campaign.appear.side_effect = lambda button, **kwargs: button is visible[0]
    capture = Mock(return_value='/tmp/archive_entry.png')
    monkeypatch.setattr(ex, 'save_event_diagnostic', capture)
    if after_go:
        def enter(*args, **kwargs):
            campaign.handle_oil_purchase()
            campaign.triggered_map_stop()
            campaign.fleet_preparation()
            campaign.device.click(ex.FLEET_PREPARATION)
            visible[0] = modal
            campaign.handle_oil_purchase()
        campaign.enter_map = enter
    result = invoke(owner, proof, prepare, reserve)
    assert result == dict(status='blocked', reason=reason, reserved=after_go,
                         boss_won=False, stage_complete=False, diagnostic='/tmp/archive_entry.png')
    capture.assert_called_once()
    captured_device, captured_tag = capture.call_args.args
    assert captured_device._device is campaign.device
    assert captured_tag == f'alas_{EVENT}_A1_{tag}'
    # The owned key handler verifies a fresh frame; diagnostics then capture
    # that rejected frame directly without another screenshot replacing it.
    assert owner.device.screenshot.call_count == (2 if after_go and modal is ex.USE_DATA_KEY else 1)
    assert reserve.call_count == int(after_go) and state.starts == 0
    assert owner.device.click.call_args_list == ([((ex.FLEET_PREPARATION,), {})] if after_go else [])
    owner.config.task_call.assert_called_once_with('Restart')


def test_entry_dialog_capture_failure_does_not_authorize_input(monkeypatch):
    owner, campaign, state, proof, prepare, reserve = controller(monkeypatch)
    campaign.appear.side_effect = lambda button, **kwargs: button is ex.USE_DATA_KEY
    capture = Mock(return_value=None)
    monkeypatch.setattr(ex, 'save_event_diagnostic', capture)
    result = invoke(owner, proof, prepare, reserve)
    assert result['reason'] == 'unverified_data_key_dialog' and 'diagnostic' not in result
    capture.assert_called_once()
    assert state.starts == 0
    owner.device.click.assert_not_called()
    reserve.assert_not_called()


def test_existing_exact_oil_guard_exception_defers_and_never_confirms(monkeypatch):
    owner, campaign, state, proof, prepare, reserve = controller(monkeypatch)
    campaign.handle_oil_purchase.side_effect = OilExhausted
    result = invoke(owner, proof, prepare, reserve)
    assert result['status'] == 'deferred' and result['reason'] == 'oil_exhausted'
    assert state.starts == 0
    owner.defer_for_oil.assert_called_once()
    reserve.assert_not_called()


@pytest.mark.parametrize('reserved', [False, True])
def test_oil_schedule_owned_deferral_preserves_reservation_and_recovery_time(monkeypatch, reserved):
    owner, campaign, state, proof, prepare, reserve = controller(monkeypatch)
    owner.get_oil.side_effect = [500, 499] if reserved else [499]
    owner.defer_for_oil.side_effect = lambda: setattr(owner.config, 'NextRun', 'oil recovery time')
    result = invoke(owner, proof, prepare, reserve)
    assert result['status'] == 'deferred' and result['reason'] == 'oil_reserve'
    assert owner.config.NextRun == 'oil recovery time'
    assert result['reserved'] is reserved and state.starts == int(reserved)
    assert reserve.call_count == int(reserved)
    owner.defer_for_oil.assert_called_once()
    if reserved:
        campaign.withdraw.assert_called_once_with(timeout=20)
    else:
        campaign.withdraw.assert_not_called()


def test_spent_oil_exhaustion_preserves_schedule_and_never_reenters(monkeypatch):
    owner, campaign, state, proof, prepare, reserve = controller(monkeypatch, run_error=OilExhausted())
    owner.defer_for_oil.side_effect = lambda: setattr(owner.config, 'NextRun', 'oil recovery time')
    result = invoke(owner, proof, prepare, reserve)
    assert result['status'] == 'deferred' and result['reason'] == 'oil_exhausted'
    assert owner.config.NextRun == 'oil recovery time'
    assert result['reserved'] is True and state.starts == 1
    assert result['boss_won'] is False and result['stage_complete'] is False
    reserve.assert_called_once()
    owner.defer_for_oil.assert_called_once()
    campaign.withdraw.assert_called_once_with(timeout=20)


def test_legacy_campaign_navigation_reuses_provider_not_first_run_search(monkeypatch):
    owner, campaign, _, proof, prepare, reserve = controller(monkeypatch, progress=(True,))
    invoke(owner, proof, prepare, reserve)
    campaign.ensure_campaign_ui('A1', mode='normal')
    campaign.ui_goto_archives_campaign(mode='ex')
    campaign.ui_goto_event()
    campaign.ui_goto_sp()
    assert prepare.call_count == 5
    with pytest.raises(ex.ArchiveBlocked, match='navigation_goal_changed'):
        campaign.ensure_campaign_ui('D3', mode='hard')


def test_second_combat_entry_forbidden_even_with_spent_budget(monkeypatch):
    owner, campaign, state, proof, prepare, reserve = controller(monkeypatch)
    result = invoke(owner, proof, prepare, reserve,
                    goal=StageGoal(EVENT, 'A1', goal='archive_prize'))
    assert result['status'] == 'won'
    with pytest.raises(ex.ArchiveBlocked, match='second_sortie_forbidden'):
        campaign.enter_map(campaign.ENTRANCE)
    assert state.starts == 1


def test_post_win_inspection_cannot_allow_a_third_entry(monkeypatch):
    owner, campaign, state, proof, prepare, reserve = controller(monkeypatch)
    invoke(owner, proof, prepare, reserve)
    with pytest.raises(ex.ArchiveBlocked, match='second_sortie_forbidden'):
        campaign.enter_map(campaign.ENTRANCE)
    assert state.starts == 1


def test_legacy_fleet_dropdown_open_loop_is_frame_bounded_and_device_restored(monkeypatch):
    from module.map.map_fleet_preparation import FleetOperator

    owner, campaign, state, proof, prepare, reserve = controller(monkeypatch)
    original_device = campaign.device
    operator = FleetOperator.__new__(FleetOperator)
    operator.main, operator.options, operator._choose = campaign, {}, object()
    operator.update_options = Mock()  # The dropdown never appears.
    campaign.fleet_preparation = operator.open
    result = invoke(owner, proof, prepare, reserve)
    assert result['reason'] == 'fleet_preparation_frame_limit'
    assert result['reserved'] is True and state.starts == 0
    assert campaign.device is original_device
    assert original_device.screenshot.call_count == 91  # Provider + 90 nested frames.
    assert original_device.click.call_count <= 12
    assert not isinstance(owner.device, ex._FleetPreparationDevice)


def test_legacy_fleet_clear_loop_is_deadline_bounded(monkeypatch):
    from module.map.map_fleet_preparation import FleetOperator

    owner, campaign, state, proof, prepare, reserve = controller(monkeypatch)
    original_device = campaign.device
    now = [0]
    monkeypatch.setattr(ex, 'monotonic', lambda: now[0])
    operator = FleetOperator.__new__(FleetOperator)
    operator.main, operator._clear = campaign, object()
    operator.allow = Mock(return_value=False)
    campaign.handle_popup_confirm = Mock(return_value=False)

    def loop():
        while True:
            now[0] += 5
            campaign.device.screenshot()
            yield None

    campaign.loop = loop
    campaign.fleet_preparation = operator.clear
    result = invoke(owner, proof, prepare, reserve)
    assert result['reason'] == 'fleet_preparation_timeout'
    assert now[0] == 45 and state.starts == 0
    assert campaign.device is original_device
    original_device.click.assert_not_called()


def test_nested_fleet_clicks_share_one_budget_and_never_reach_final_go(monkeypatch):
    owner, campaign, state, proof, prepare, reserve = controller(monkeypatch)
    original_device = campaign.device

    def fleet():
        while True:
            campaign.device.click(object())

    campaign.fleet_preparation = fleet
    result = invoke(owner, proof, prepare, reserve)
    assert result['reason'] == 'fleet_preparation_click_limit'
    assert original_device.click.call_count == 12 and state.starts == 0
    assert campaign.device is original_device


@pytest.mark.parametrize('modal,reason', [
    (ex.GET_ITEMS_2, 'unowned_receipt'),
    (ex.USE_DATA_KEY, 'unverified_data_key_dialog'),
    (ex.POPUP_CONFIRM_WHITE, 'unrecognized_entry_dialog')])
def test_new_nested_modal_is_vetoed_after_fresh_frame_before_fleet_click(monkeypatch, modal, reason):
    owner, campaign, state, proof, prepare, reserve = controller(monkeypatch)
    original_device = campaign.device
    visible = [None]
    campaign.appear.side_effect = lambda button, **kwargs: button is visible[0]

    def fleet():
        visible[0] = modal
        campaign.device.screenshot()
        campaign.device.click(object())

    campaign.fleet_preparation = fleet
    result = invoke(owner, proof, prepare, reserve)
    assert result['reason'] == reason and state.starts == 0
    original_device.click.assert_not_called()
    assert campaign.device is original_device


@pytest.mark.parametrize('forbidden', [ex.POPUP_CONFIRM, ex.POPUP_CANCEL_WHITE, ex.FLEET_PREPARATION])
def test_nested_generic_dialog_or_go_control_is_never_forwarded(monkeypatch, forbidden):
    owner, campaign, state, proof, prepare, reserve = controller(monkeypatch)
    original_device = campaign.device
    campaign.fleet_preparation = lambda: campaign.device.click(forbidden)
    result = invoke(owner, proof, prepare, reserve)
    assert result['reason'] == 'unrecognized_fleet_control' and state.starts == 0
    original_device.click.assert_not_called()
    assert campaign.device is original_device


@pytest.mark.parametrize('error', [TaskEnd('preempted'), RuntimeError('fleet failed')])
def test_nested_fleet_exception_preserves_exception_and_original_device(monkeypatch, error):
    owner, campaign, state, proof, prepare, reserve = controller(monkeypatch)
    original_device = campaign.device
    campaign.fleet_preparation = Mock(side_effect=error)
    with pytest.raises(type(error)) as caught:
        invoke(owner, proof, prepare, reserve)
    assert caught.value is error and caught.value.archive_reserved is True
    assert campaign.device is original_device and state.starts == 0


def test_exact_oil_cancel_inside_nested_guard_does_not_recurse(monkeypatch):
    owner, campaign, state, proof, prepare, reserve = controller(monkeypatch)
    original_device = campaign.device
    oil_visible = [False]

    def oil():
        if oil_visible[0]:
            campaign.device.screenshot()
            campaign.device.click(object())  # Existing exact oil Cancel handler.
            raise OilExhausted()
        return False

    def fleet():
        oil_visible[0] = True
        campaign.device.screenshot()

    campaign.handle_oil_purchase = oil
    campaign.fleet_preparation = fleet
    result = invoke(owner, proof, prepare, reserve)
    assert result['status'] == 'deferred' and result['reason'] == 'oil_exhausted'
    assert original_device.click.call_count == 1 and state.starts == 0
    assert campaign.device is original_device


def fleet_probe_entry(owner, campaign, state):
    def enter(*args, **kwargs):
        campaign.handle_oil_purchase()
        campaign.triggered_map_stop()
        campaign.device.click(ex.MAP_PREPARATION)
        campaign.fleet_preparation()
        campaign.device.click(ex.FLEET_PREPARATION)
        state.starts += 1
    campaign.enter_map = enter


@pytest.mark.parametrize('complete', [False, True])
def test_fleet_probe_captures_completed_or_incomplete_without_reserve_or_go(monkeypatch, complete):
    owner, campaign, state, proof, prepare, reserve = controller(monkeypatch, progress=(complete,))
    fleet_probe_entry(owner, campaign, state)
    original_fleet = campaign.fleet_preparation
    morale = campaign.emotion.check_reduce
    proof.update(verified_for_execution=False, raw_keys=0)
    campaign.appear.side_effect = lambda button, **kwargs: button is ex.FLEET_PREPARATION
    capture = Mock(return_value='/tmp/archive_fleet.png')
    monkeypatch.setattr(ex, 'save_event_diagnostic', capture)
    result = invoke(owner, proof, prepare, reserve, inspect_fleet_only=True)
    assert result == dict(status='observed', reason='fleet_preparation', reserved=False,
                         boss_won=False, stage_complete=complete, diagnostic='/tmp/archive_fleet.png')
    assert state.starts == 0 and owner.fleet_probe_advances == 1
    owner.device.click.assert_called_once_with(ex.MAP_PREPARATION)
    reserve.assert_not_called()
    owner.get_oil.assert_not_called()
    morale.assert_not_called()
    original_fleet.assert_not_called()
    capture.assert_called_once()
    owner._cancel_preparation.assert_called_once_with(campaign)


def test_fleet_probe_rejects_unknown_fleet_page_without_setup_or_go(monkeypatch):
    owner, campaign, state, proof, prepare, reserve = controller(monkeypatch, progress=(False,))
    fleet_probe_entry(owner, campaign, state)
    original_fleet = campaign.fleet_preparation
    capture = Mock()
    monkeypatch.setattr(ex, 'save_event_diagnostic', capture)
    result = invoke(owner, proof, prepare, reserve, inspect_fleet_only=True)
    assert result['reason'] == 'fleet_preparation_unrecognized' and state.starts == 0
    owner.device.click.assert_called_once_with(ex.MAP_PREPARATION)
    original_fleet.assert_not_called()
    reserve.assert_not_called()
    capture.assert_not_called()


def test_fleet_probe_never_repeats_map_preparation_advance(monkeypatch):
    owner, campaign, state, proof, prepare, reserve = controller(monkeypatch, progress=(True, True))

    def enter(*args, **kwargs):
        for _ in range(2):
            campaign.triggered_map_stop()
            campaign.device.click(ex.MAP_PREPARATION)

    campaign.enter_map = enter
    result = invoke(owner, proof, prepare, reserve, inspect_fleet_only=True)
    assert result['reason'] == 'fleet_preparation_not_reached' and state.starts == 0
    owner.device.click.assert_called_once_with(ex.MAP_PREPARATION)
    reserve.assert_not_called()


def test_conflicting_inspection_modes_never_navigate(monkeypatch):
    owner, _, _, proof, prepare, reserve = controller(monkeypatch)
    result = invoke(owner, proof, prepare, reserve, inspect_only=True, inspect_fleet_only=True)
    assert result['reason'] == 'conflicting_inspection_modes'
    owner.load_campaign.assert_not_called()
    prepare.assert_not_called()
    reserve.assert_not_called()


def test_fleet_probe_failed_capture_cancels_without_reserve_or_go(monkeypatch):
    owner, campaign, state, proof, prepare, reserve = controller(monkeypatch)
    fleet_probe_entry(owner, campaign, state)
    campaign.appear.side_effect = lambda button, **kwargs: button is ex.FLEET_PREPARATION
    monkeypatch.setattr(ex, 'save_event_diagnostic', Mock(return_value=None))
    result = invoke(owner, proof, prepare, reserve, inspect_fleet_only=True)
    assert result['reason'] == 'fleet_preparation_capture_failed' and state.starts == 0
    owner._cancel_preparation.assert_called_once_with(campaign)
    reserve.assert_not_called()
    owner.device.click.assert_called_once_with(ex.MAP_PREPARATION)


class ReadyTimer:
    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        return self

    def reset(self):
        return self

    def reached(self):
        return True


def test_cancel_requires_preparation_disappearance_before_background_stage_success(monkeypatch):
    owner, campaign, state, _, _, _ = controller(monkeypatch)
    monkeypatch.setattr(ex, 'Timer', ReadyTimer)
    visible = [ex.FLEET_PREPARATION]

    def loop(**kwargs):
        yield None  # Stage background is already recognizable below the overlay.
        assert campaign.device.click.call_count == 1
        visible[0] = None
        yield None

    campaign.loop = loop
    campaign.appear.side_effect = lambda button, **kwargs: button is visible[0]
    assert ex.WarArchivesExecution._cancel_preparation(owner, campaign) is True
    campaign.device.click.assert_called_once_with(ex.MAP_PREPARATION_CANCEL)
    assert state.in_stage is True


@pytest.mark.parametrize('overlay', [ex.GET_ITEMS_2, ex.POPUP_CONFIRM_WHITE, ex.USE_DATA_KEY])
def test_cancel_rejects_receipt_or_unknown_dialog_above_recognizable_stage(monkeypatch, overlay):
    owner, campaign, _, _, _, _ = controller(monkeypatch)
    monkeypatch.setattr(ex, 'Timer', ReadyTimer)
    campaign.loop = lambda **kwargs: iter([None])
    campaign.appear.side_effect = lambda button, **kwargs: button is overlay
    with pytest.raises(ex.ArchiveBlocked, match='preparation_return_unrecognized'):
        ex.WarArchivesExecution._cancel_preparation(owner, campaign)
    campaign.device.click.assert_not_called()


@pytest.mark.parametrize('lines,expected', [
    (['Average Level>70(124）TotalTRP>700（2477)', 'TotalEVA>400(1293)'],
     (('AverageLevel', 70, 124), ('TotalTRP', 700, 2477), ('TotalEVA', 400, 1293))),
    (['Total AVI > 1200 (2200)', 'AverageLevel>90(110)'],
     (('TotalAVI', 1200, 2200), ('AverageLevel', 90, 110))),
    (['AverageLevel>70(70)'], None), (['TotalTRP>700(699)'], None),
    (['UnknownStat>1(2)'], None), (['TotalTRP>0(0)'], None),
    (['TotalTRP>700(2477) unreadable'], None),
    (['TotalTRP>700(2477)', 'TotalTRP>100(2477)'], None),
    (['TotalTRP>700'], None), ([''], None), ([], None)])
def test_hard_requirement_grammar_requires_every_known_comparison_satisfied(lines, expected):
    assert ex.parse_hard_requirements(lines) == expected


def real_hard_fleet():
    from module.config.server import set_server
    set_server('en')
    image = np.array(Image.open(Path(__file__).parent / 'data' / 'archive_hard_fleet.png').convert('RGB'))
    device = SimpleNamespace(image=image, screenshot=Mock(), click=Mock())
    campaign = SimpleNamespace(device=device, config=SimpleNamespace())
    campaign.match_template_color = lambda button, **kwargs: button.match_template_color(device.image, **kwargs)
    return campaign


def test_real_hard_fleet_fixture_has_satisfied_generic_requirements_unmocked_ocr():
    owner = ex.WarArchivesExecution.__new__(ex.WarArchivesExecution)
    campaign = real_hard_fleet()
    assert owner._hard_fleet_witness(campaign) == (
        ('AverageLevel', 70, 124), ('TotalTRP', 700, 2477), ('TotalEVA', 400, 1293))
    campaign.device.click.assert_not_called()


@pytest.mark.parametrize('area', [
    (108, 108, 319, 144), (115, 170, 168, 384), (431, 435, 986, 516),
    (989, 559, 1144, 605), (995, 186, 1074, 257), (1080, 190, 1158, 252),
    (1000, 305, 1080, 362), (1080, 305, 1158, 365),
    (370, 235, 665, 265), (690, 235, 983, 265),
    (370, 350, 665, 380), (690, 350, 983, 380), (106, 559, 976, 607)])
def test_hard_fleet_rejects_missing_layout_controls_rows_or_requirements(area):
    owner = ex.WarArchivesExecution.__new__(ex.WarArchivesExecution)
    campaign = real_hard_fleet()
    x1, y1, x2, y2 = area
    campaign.device.image[y1:y2, x1:x2] = 0
    assert owner._hard_fleet_witness(campaign) is None
    campaign.device.click.assert_not_called()


@pytest.mark.parametrize('color', [(255, 40, 40), (255, 255, 255), (40, 255, 40)])
def test_hard_fleet_rejects_bright_nongold_requirement_before_filtered_ocr(monkeypatch, color):
    owner = ex.WarArchivesExecution.__new__(ex.WarArchivesExecution)
    campaign = real_hard_fleet()
    campaign.device.image[589:601, 500:550] = color
    reader = Mock()
    monkeypatch.setattr(ex, 'Ocr', reader)
    assert owner._hard_fleet_witness(campaign) is None
    reader.assert_not_called()


def hard_preparation_controller(monkeypatch, witnesses):
    owner = ex.WarArchivesExecution.__new__(ex.WarArchivesExecution)
    owner.config = SimpleNamespace(config_name='alas', check_task_switch=Mock())
    owner.goal = StageGoal(EVENT, 'C1', 'hard', 'archive_clear')
    campaign = SimpleNamespace(device=SimpleNamespace(screenshot=Mock(), click=Mock()),
                               config=SimpleNamespace())
    campaign.loop = lambda **kwargs: iter([None] * len(witnesses))
    owner._hard_fleet_witness = Mock(side_effect=witnesses)
    monkeypatch.setattr(ex, 'Timer', ReadyTimer)
    from module.mood import lineup
    record = Mock()
    monkeypatch.setattr(lineup, 'record_lineup', record)
    monkeypatch.setattr(ex, 'save_event_diagnostic', Mock(return_value='/tmp/hard.png'))
    return owner, campaign, record


def test_hard_preparation_requires_three_equal_fresh_frames_and_final_recheck(monkeypatch):
    proof = (('AverageLevel', 70, 124),)
    owner, campaign, record = hard_preparation_controller(monkeypatch, [proof] * 4)
    assert owner._prepare_hard_fleet(campaign) is False
    assert owner._hard_fleet_witness.call_count == 4
    assert campaign.map_is_hard_mode is True and campaign.config.SUBMARINE == 0
    record.assert_called_once_with(campaign)
    campaign.device.screenshot.assert_called_once()
    campaign.device.click.assert_not_called()


@pytest.mark.parametrize('witnesses', [[None] * 4, [(('TotalTRP', 10, 20),)] * 3 + [None],
    [(('TotalTRP', 10, 20),), None, (('TotalTRP', 10, 20),)]])
def test_hard_unknown_unstable_or_changed_final_frame_never_permits_go(monkeypatch, witnesses):
    owner, campaign, _ = hard_preparation_controller(monkeypatch, witnesses)
    with pytest.raises(ex.ArchiveBlocked, match='hard_lineup_or_requirements_unverified'):
        owner._prepare_hard_fleet(campaign)
    campaign.device.click.assert_not_called()
    assert owner.diagnostic == '/tmp/hard.png'


def test_hard_stage_bypasses_common_fleet_number_selection(monkeypatch):
    owner, campaign, state, proof, prepare, reserve = controller(monkeypatch)
    monkeypatch.setattr(ex, 'stage_names', Mock(return_value=['c1']))
    proof.update(stage='C1', mode='hard')
    original = campaign.fleet_preparation
    owner._prepare_hard_fleet = Mock(return_value=False)
    result = invoke(owner, proof, prepare, reserve, goal=StageGoal(EVENT, 'C1', 'hard', 'archive_clear'))
    assert result['status'] == 'complete' and state.starts == 1
    owner._prepare_hard_fleet.assert_called_once_with(campaign)
    original.assert_not_called()
    campaign.device.click.assert_not_called()



def key_dialog_image():
    return np.asarray(Image.open(Path(__file__).parent / 'data/archive_data_key_dialog.png').convert('RGB'))


def test_actual_five_data_key_reminder_requires_exact_complete_text():
    owner = ex.WarArchivesExecution.__new__(ex.WarArchivesExecution)
    assert owner._data_key_dialog_witness(key_dialog_image()) is True


def test_actual_scaled_f2p_five_key_dialog_is_recognized_with_complete_text():
    owner = ex.WarArchivesExecution.__new__(ex.WarArchivesExecution)
    image = np.asarray(Image.open(Path(__file__).parent / 'data/archive_data_key_dialog_scaled.png').convert('RGB'))
    assert owner._data_key_dialog_candidate(image) is True
    assert owner._data_key_dialog_witness(image) is True


def key_toast_image():
    return np.asarray(Image.open(Path(__file__).parent / 'data/archive_data_key_toast.png').convert('RGB'))


def test_actual_game_toast_is_only_a_wait_candidate_without_complete_key_message():
    owner = ex.WarArchivesExecution.__new__(ex.WarArchivesExecution)
    assert owner._data_key_dialog_candidate(key_toast_image()) is True
    assert owner._data_key_dialog_witness(key_toast_image()) is False


@pytest.mark.parametrize('filename', ['archive_data_key_dialog.png', 'archive_data_key_dialog_scaled.png'])
@pytest.mark.parametrize('changed', ['five', 'message', 'header', 'cancel', 'confirm', 'dim', 'wrong_cost'])
def test_actual_key_dialog_rejects_missing_price_controls_and_dim_overlay(changed, filename):
    from PIL import ImageDraw
    owner = ex.WarArchivesExecution.__new__(ex.WarArchivesExecution)
    image = np.asarray(Image.open(Path(__file__).parent / 'data' / filename).convert('RGB')).copy()
    areas = {'five': (737,319,757,347), 'message': (410,345,871,373),
             'header': (353,175,430,215), 'cancel': (401,481,578,542),
             'confirm': (702,481,878,542), 'wrong_cost': (737,319,757,347)}
    if changed == 'dim':
        image = (image * .45).astype(np.uint8)
    else:
        x1,y1,x2,y2 = areas[changed]
        image[y1:y2,x1:x2] = (58,65,82)
        if changed == 'wrong_cost':
            altered = Image.fromarray(image)
            ImageDraw.Draw(altered).text((740,325), '6', fill=(255,220,50))
            image = np.asarray(altered)
    assert owner._data_key_dialog_witness(image) is False


def test_key_dialog_does_not_accept_extra_or_incomplete_message_by_template_alone(monkeypatch):
    owner = ex.WarArchivesExecution.__new__(ex.WarArchivesExecution)
    monkeypatch.setattr(ex, 'Ocr', Mock(side_effect=[
        SimpleNamespace(ocr=Mock(return_value=['Unlocking this stage requires 15',
                                              'Would you like to unlock this stage?'])),
        SimpleNamespace(ocr=Mock(return_value=['5 Data Key(s)', '5']))]))
    assert owner._data_key_dialog_witness(key_dialog_image()) is False


def key_dialog_entry(monkeypatch, *, keep_dialog=False, unknown_after=False, fail_confirm=False):
    owner, campaign, state, proof, prepare, reserve = controller(monkeypatch)
    monkeypatch.setattr(ex, 'Timer', ReadyTimer)
    monkeypatch.setattr(ex, 'save_event_diagnostic', Mock(return_value='/tmp/key.png'))
    image = key_dialog_image()
    owner.device.image = np.zeros_like(image)
    state.dialog = False
    state.confirmed = False
    state.legacy_after_handler = 0
    state.events = []
    reserve.side_effect = lambda *args, **kwargs: state.events.append('reserve') or True
    campaign.appear.side_effect = lambda button, **kwargs: state.dialog and button is ex.USE_DATA_KEY

    def screenshot():
        owner.device.image = image if state.dialog else np.zeros_like(image)
    owner.device.screenshot.side_effect = screenshot

    def click(button, *args, **kwargs):
        if button is ex.FLEET_PREPARATION:
            assert owner.fleet_go_clicked and owner.reserved
            state.events.append('go')
            state.dialog = True
            owner.device.image = image
        elif button is ex.ARCHIVE_KEY_CONFIRM:
            assert owner.key_confirm_clicked and owner.reserved and owner.fleet_go_clicked
            state.events.append('confirm')
            if fail_confirm:
                raise OSError('lost confirmation transport')
            state.confirmed = True
            state.dialog = keep_dialog
            state.in_map = not keep_dialog and not unknown_after
            state.in_stage = False
        else:
            pytest.fail(f'unexpected control: {button}')
    owner.device.click.side_effect = click

    def enter(*args, **kwargs):
        campaign.handle_oil_purchase()
        campaign.triggered_map_stop()
        campaign.fleet_preparation()
        campaign.device.click(ex.FLEET_PREPARATION)
        # The real enter_map ignores the handler return, so our helper must
        # retain control until recognized entry instead of yielding the modal.
        campaign.handle_oil_purchase()
        assert not state.dialog and state.in_map
        state.legacy_after_handler += 1
        state.starts += 1
        return True
    campaign.enter_map = enter
    return owner, campaign, state, proof, prepare, reserve


def test_reserved_go_exact_key_dialog_is_settled_then_confirmed_once_before_verified_map(monkeypatch):
    owner, campaign, state, proof, prepare, reserve = key_dialog_entry(monkeypatch)
    original_device = campaign.device
    # Prize goal avoids a later inspect-only entry; this is one actual sortie.
    result = invoke(owner, proof, prepare, reserve, goal=StageGoal(EVENT, 'A1', goal='archive_prize'))
    assert result['status'] == 'won' and result['reserved'] is True
    assert state.events == ['reserve', 'go', 'confirm']
    assert state.starts == state.legacy_after_handler == 1
    assert owner.device.screenshot.call_count >= 5  # initial + 2 modal + 2 map
    assert campaign.device is original_device
    assert owner.device.click.call_args_list == [((ex.FLEET_PREPARATION,), {}), ((ex.ARCHIVE_KEY_CONFIRM,), {})]


@pytest.mark.parametrize('keep_dialog,unknown_after', [(True, False), (False, True)])
def test_confirmed_but_unverified_entry_remains_bounded_without_legacy_fallthrough(monkeypatch, keep_dialog, unknown_after):
    owner, campaign, state, proof, prepare, reserve = key_dialog_entry(
        monkeypatch, keep_dialog=keep_dialog, unknown_after=unknown_after)
    original_device = campaign.device
    result = invoke(owner, proof, prepare, reserve)
    assert result['reason'] == 'data_key_entry_not_verified' and result['reserved'] is True
    assert state.events == ['reserve', 'go', 'confirm']
    assert state.starts == state.legacy_after_handler == 0
    assert owner.device.screenshot.call_count <= 31
    assert campaign.device is original_device


@pytest.mark.parametrize('field,value', [
    ('reserved', False), ('fleet_go_clicked', False), ('key_confirm_clicked', True),
    ('inspection', True), ('fleet_inspection', True)])
def test_exact_dialog_without_own_fresh_reservation_go_or_once_budget_never_clicks(monkeypatch, field, value):
    owner, campaign, state, proof, prepare, reserve = controller(monkeypatch)
    owner.goal = StageGoal(EVENT, 'A1', goal='archive_prize')
    owner.proof, owner.key_mode, owner.key_cost = proof, 'per_entry', 5
    owner.reserved = owner.fleet_go_clicked = True
    owner.key_confirm_clicked = owner.inspection = owner.fleet_inspection = False
    setattr(owner, field, value)
    owner.device.image = key_dialog_image()
    with pytest.raises(ex.ArchiveBlocked, match='unverified_data_key_dialog'):
        owner._confirm_data_key(campaign, Mock(), float('inf'))
    owner.device.click.assert_not_called()
    owner.device.screenshot.assert_not_called()


def test_dialog_change_before_stability_does_not_confirm(monkeypatch):
    owner, campaign, state, proof, prepare, reserve = key_dialog_entry(monkeypatch)
    owner._data_key_dialog_witness = Mock(side_effect=[True, False])
    result = invoke(owner, proof, prepare, reserve)
    assert result['reason'] == 'unverified_data_key_dialog'
    assert state.events == ['reserve', 'go'] and owner.key_confirm_clicked is False


@pytest.mark.parametrize('persistent', [False, True])
def test_owned_go_waits_for_real_toast_to_clear_but_never_confirms_covered_message(monkeypatch, persistent):
    owner, campaign, state, proof, prepare, reserve = key_dialog_entry(monkeypatch)
    toast, clean = key_toast_image(), key_dialog_image()
    frames = []
    original_click = owner.device.click.side_effect

    def click(button, *args, **kwargs):
        if button is ex.ARCHIVE_KEY_CONFIRM:
            assert len(frames) >= 5 and all(value == 'clean' for value in frames[-2:])
        original_click(button, *args, **kwargs)
        if button is ex.FLEET_PREPARATION:
            owner.device.image = toast

    def screenshot():
        if state.dialog:
            covered = persistent or len(frames) < 3
            frames.append('toast' if covered else 'clean')
            owner.device.image = toast if covered else clean
        else:
            owner.device.image = np.zeros_like(clean)

    owner.device.click.side_effect = click
    owner.device.screenshot.side_effect = screenshot
    # Live failure: the obscured message misses USE_DATA_KEY while the generic
    # popup marker still matches. Candidate controls must retain ownership.
    campaign.appear.side_effect = lambda button, **kwargs: state.dialog and button is ex.POPUP_CONFIRM
    result = invoke(owner, proof, prepare, reserve, goal=StageGoal(EVENT, 'A1', goal='archive_prize'))
    assert result['reserved'] is True
    if persistent:
        assert result['reason'] == 'data_key_entry_not_verified'
        assert state.events == ['reserve', 'go'] and not owner.key_confirm_clicked
        assert len(frames) == 30 and state.legacy_after_handler == 0
    else:
        assert result['status'] == 'won'
        assert frames == ['toast'] * 3 + ['clean'] * 2
        assert state.events == ['reserve', 'go', 'confirm']
        assert state.legacy_after_handler == 1


def test_failed_confirmation_transport_consumes_once_flag_and_restores_entry_device(monkeypatch):
    owner, campaign, state, proof, prepare, reserve = key_dialog_entry(monkeypatch, fail_confirm=True)
    original_device = campaign.device
    with pytest.raises(OSError, match='lost confirmation transport') as error:
        invoke(owner, proof, prepare, reserve)
    assert error.value.archive_reserved is True
    assert owner.key_confirm_clicked is True and state.events == ['reserve', 'go', 'confirm']
    assert campaign.device is original_device


def test_second_fleet_go_never_reaches_device_even_after_ignored_return(monkeypatch):
    owner, campaign, state, proof, prepare, reserve = key_dialog_entry(monkeypatch)
    original = campaign.enter_map
    def twice(*args, **kwargs):
        original(*args, **kwargs)
        campaign.device.click(ex.FLEET_PREPARATION)
    campaign.enter_map = twice
    result = invoke(owner, proof, prepare, reserve)
    assert result['reason'] == 'second_fleet_go_forbidden'
    assert state.events == ['reserve', 'go', 'confirm']
    assert owner.device.click.call_count == 2


def test_five_key_settling_timer_is_one_second_and_needs_fresh_positive_frames(monkeypatch):
    owner, campaign, state, proof, prepare, reserve = key_dialog_entry(monkeypatch)
    timer = ReadyTimer()
    timer.reached = Mock(side_effect=[False, True])
    timer.reset = Mock(return_value=timer)
    timer_factory = Mock(return_value=timer)
    monkeypatch.setattr(ex, 'Timer', timer_factory)
    result = invoke(owner, proof, prepare, reserve, goal=StageGoal(EVENT, 'A1', goal='archive_prize'))
    assert result['status'] == 'won'
    timer_factory.assert_called_once_with(1, count=2)
    assert timer.reached.call_count == 2
    timer.reset.assert_called_once_with()
    assert owner.device.screenshot.call_count >= 6



def test_key_confirmation_accepts_two_recognized_loading_frames_without_battle_inputs(monkeypatch):
    owner, campaign, state, proof, prepare, reserve = key_dialog_entry(monkeypatch, unknown_after=True)
    owner.goal = StageGoal(EVENT, 'A1', goal='archive_prize')
    owner.proof, owner.key_mode, owner.key_cost = proof, 'per_entry', 5
    owner.reserved = owner.fleet_go_clicked = state.dialog = True
    owner.key_confirm_clicked = owner.inspection = owner.fleet_inspection = False
    campaign.is_combat_loading = lambda: state.confirmed
    owner._confirm_data_key(campaign, Mock(), float('inf'))
    assert state.events == ['confirm']
    owner.device.click.assert_called_once_with(ex.ARCHIVE_KEY_CONFIRM)
    assert owner.device.screenshot.call_count == 4


def test_entry_proxy_consumes_go_before_transport_failure_and_restores_original_device(monkeypatch):
    owner, campaign, state, proof, prepare, reserve = key_dialog_entry(monkeypatch)
    original_device = campaign.device
    owner.device.click.side_effect = OSError('lost Go transport')
    with pytest.raises(OSError, match='lost Go transport') as error:
        invoke(owner, proof, prepare, reserve)
    assert error.value.archive_reserved is True
    assert owner.fleet_go_clicked is True and owner.key_confirm_clicked is False
    owner.device.click.assert_called_once_with(ex.FLEET_PREPARATION)
    assert campaign.device is original_device


@pytest.mark.parametrize('button', [ex.POPUP_CONFIRM, ex.POPUP_CONFIRM_WHITE])
def test_entry_proxy_never_forwards_generic_confirmation(button):
    device = SimpleNamespace(click=Mock())
    proxy = ex._ArchiveEntryDevice(device, Mock())
    with pytest.raises(ex.ArchiveBlocked, match='unrecognized_entry_confirm'):
        proxy.click(button)
    device.click.assert_not_called()


ENIGMA = 'war_archives_20200806_cn'


def story_unlock_image():
    return np.asarray(Image.open(Path(__file__).parent / 'data/archive_story_unlock_enigma.png').convert('RGB'))


def test_real_story_unlock_requires_complete_current_archive_message_unmocked_ocr():
    owner = ex.WarArchivesExecution.__new__(ex.WarArchivesExecution)
    owner.goal = StageGoal(ENIGMA, 'SP3', goal='archive_clear')
    assert owner._story_unlock_witness(story_unlock_image()) is True
    owner.goal = StageGoal(EVENT, 'A1', goal='archive_clear')
    assert owner._story_unlock_witness(story_unlock_image()) is False
    # Identical Info/Cancel layout with an unrelated full message is insufficient.
    assert owner._story_unlock_candidate(key_dialog_image()) is True
    assert owner._story_unlock_witness(key_dialog_image()) is False


@pytest.mark.parametrize('area', [(353,175,430,215), (401,481,578,542),
                                  (350,290,930,400), (705,485,876,540)])
def test_real_story_unlock_missing_header_cancel_message_or_go_never_authorizes(area):
    owner = ex.WarArchivesExecution.__new__(ex.WarArchivesExecution)
    owner.goal = StageGoal(ENIGMA, 'SP3', goal='archive_clear')
    image = story_unlock_image().copy()
    x1, y1, x2, y2 = area
    image[y1:y2, x1:x2] = (58,65,82)
    assert owner._story_unlock_witness(image) is False


def story_unlock_controller(monkeypatch, *, keep_dialog=False, unknown_after=False):
    owner, campaign, state, proof, _, _ = controller(monkeypatch)
    monkeypatch.setattr(ex, 'Timer', ReadyTimer)
    owner.goal = StageGoal(ENIGMA, 'SP3', goal='archive_clear')
    proof.update(event=ENIGMA, stage='SP3')
    owner.proof, owner.key_mode, owner.key_cost = proof, 'per_entry', 5
    owner.reserved = owner.fleet_go_clicked = True
    owner.story_unlock_cancelled = owner.inspection = owner.fleet_inspection = False
    campaign.combat_won = True
    state.in_stage, state.dialog = False, True
    image = story_unlock_image()
    owner.device.image = image

    def screenshot():
        owner.device.image = image if state.dialog else np.zeros_like(image)
    owner.device.screenshot.side_effect = screenshot

    def click(button):
        assert button is ex.ARCHIVE_KEY_CANCEL
        assert owner.story_unlock_cancelled and owner.device.screenshot.call_count >= 2
        state.dialog = keep_dialog
        state.in_stage = not keep_dialog and not unknown_after
    owner.device.click.side_effect = click
    # Keep real OCR in fixture tests; mocked OCR here isolates ownership/loop behavior.
    owner._story_unlock_witness = Mock(return_value=True)
    return owner, campaign, state


def test_owned_story_unlock_cancels_once_then_requires_two_fresh_stage_frames(monkeypatch):
    owner, campaign, _ = story_unlock_controller(monkeypatch)
    owner._acknowledge_story_unlock(campaign, False)
    assert owner._story_unlock_witness.call_count == 2
    assert owner.device.screenshot.call_count == 4
    owner.device.click.assert_called_once_with(ex.ARCHIVE_KEY_CANCEL)
    assert owner.story_unlock_cancelled is True


@pytest.mark.parametrize('keep_dialog,unknown_after', [(True, False), (False, True)])
def test_story_unlock_return_is_bounded_and_never_repeats_cancel(monkeypatch, keep_dialog, unknown_after):
    owner, campaign, _ = story_unlock_controller(monkeypatch, keep_dialog=keep_dialog,
                                                unknown_after=unknown_after)
    with pytest.raises(ex.ArchiveBlocked, match='archive_story_unlock_return_unverified'):
        owner._acknowledge_story_unlock(campaign, True)
    assert owner.device.screenshot.call_count == 20
    owner.device.click.assert_called_once_with(ex.ARCHIVE_KEY_CANCEL)


@pytest.mark.parametrize('target,field,value', [
    ('owner', 'reserved', False), ('owner', 'fleet_go_clicked', False),
    ('owner', 'inspection', True), ('owner', 'fleet_inspection', True),
    ('owner', 'story_unlock_cancelled', True), ('campaign', 'combat_won', False),
    ('campaign', 'combat_lost', True), ('campaign', 'run_lost', True)])
def test_story_unlock_without_owned_victory_never_reads_or_clicks(monkeypatch, target, field, value):
    owner, campaign, _ = story_unlock_controller(monkeypatch)
    setattr(owner if target == 'owner' else campaign, field, value)
    with pytest.raises(ex.ArchiveBlocked, match='unowned_archive_story_unlock'):
        owner._acknowledge_story_unlock(campaign, False)
    owner.device.screenshot.assert_not_called()
    owner.device.click.assert_not_called()


@pytest.mark.parametrize('proof_update', [{'event': EVENT}, {'account': 'f2p'},
                                         {'verified_for_execution': False}, {'raw_keys': 0}])
def test_story_unlock_wrong_proof_never_clicks(monkeypatch, proof_update):
    owner, campaign, _ = story_unlock_controller(monkeypatch)
    owner.proof.update(proof_update)
    with pytest.raises(ex.ArchiveBlocked, match='unowned_archive_story_unlock'):
        owner._acknowledge_story_unlock(campaign, True)
    owner.device.click.assert_not_called()


def test_story_unlock_prior_loss_survives_cleanup_flag_reset(monkeypatch):
    owner, campaign, _ = story_unlock_controller(monkeypatch)
    with pytest.raises(ex.ArchiveBlocked, match='unowned_archive_story_unlock'):
        owner._acknowledge_story_unlock(campaign, True, lost_before=True)
    owner.device.click.assert_not_called()


@pytest.mark.parametrize('real_unrelated', [False, True])
def test_unrecognized_or_changed_story_notice_never_clicks(monkeypatch, real_unrelated):
    owner, campaign, _ = story_unlock_controller(monkeypatch)
    if real_unrelated:
        owner.goal = StageGoal(EVENT, 'A1', goal='archive_clear')
        owner.proof.update(event=EVENT, stage='A1')
        del owner._story_unlock_witness
    else:
        owner._story_unlock_witness.side_effect = [True, False]
    with pytest.raises(ex.ArchiveBlocked, match='archive_story_unlock_unverified'):
        owner._acknowledge_story_unlock(campaign, True)
    owner.device.click.assert_not_called()


def test_failed_story_cancel_transport_consumes_once_flag(monkeypatch):
    owner, campaign, _ = story_unlock_controller(monkeypatch)
    owner.device.click.side_effect = OSError('lost Cancel transport')
    with pytest.raises(OSError, match='lost Cancel transport'):
        owner._acknowledge_story_unlock(campaign, True)
    assert owner.story_unlock_cancelled is True
    with pytest.raises(ex.ArchiveBlocked, match='unowned_archive_story_unlock'):
        owner._acknowledge_story_unlock(campaign, True)
    owner.device.click.assert_called_once_with(ex.ARCHIVE_KEY_CANCEL)


def test_post_boss_cleanup_hook_restores_stage_handler_and_preserves_victory(monkeypatch):
    owner, campaign, state, proof, prepare, reserve = controller(monkeypatch)
    original_in_stage = campaign.handle_in_stage = Mock(return_value=True)
    image = story_unlock_image()
    owner.device.image = image
    owner._story_unlock_candidate = Mock(return_value=True)

    def acknowledge(current, won_before, lost_before):
        assert current is campaign and expected['active']
        assert won_before is True and lost_before is False
        state.in_map, state.in_stage = False, True
    owner._acknowledge_story_unlock = Mock(side_effect=acknowledge)
    expected = {'active': False}

    def cleanup(drop=None, expected_end=None):
        campaign.combat_won = False  # Real Combat cleanup resets the prior result.
        expected['active'] = True
        campaign.handle_in_stage()
        expected['active'] = False
    campaign.combat_status = cleanup
    original_battle = campaign.execute_a_battle
    campaign.execute_a_battle = lambda: (original_battle(), setattr(campaign, 'combat_won', True))
    result = invoke(owner, proof, prepare, reserve,
                    goal=StageGoal(EVENT, 'A1', goal='archive_prize'))
    assert result['status'] == 'won' and result['boss_won'] is True
    assert campaign.handle_in_stage is original_in_stage
    original_in_stage.assert_called_once_with()
    owner._acknowledge_story_unlock.assert_called_once()
