from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from module.campaign.campaign_stars import StageGoal
from module.config.config import TaskEnd
from module.exception import OilExhausted, ScriptEnd
from module.war_archives import execution as ex


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
                          check_task_switch=Mock(), task_switched=Mock(return_value=False))
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
