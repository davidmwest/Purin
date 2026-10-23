from types import SimpleNamespace
from unittest.mock import Mock, call

import numpy as np
import pytest
from PIL import Image

from module.exception import GameNotRunningError, ScriptError
from module.gacha import mission_orders, banner
from module.gacha.mission_orders import (
    CURRENT_EVENT, ORDER_VERSION, GachaMissionOrders, load_order_state,
    order_state_path, parse_resource, read_order_resources,
    resources_cover_order, save_order_state, read_count_cost)
from module.gacha.mission_submit_assets import COUNT_WITNESSES, COUNT_PLUS, COUNT_CANCEL, COUNT_CONFIRM


PERIOD = '2026-10-23T00:00:00'
KEY = f'{PERIOD}:{CURRENT_EVENT}:{ORDER_VERSION}'


def controller(monkeypatch):
    helper = GachaMissionOrders.__new__(GachaMissionOrders)
    helper.config = SimpleNamespace(config_name='alas', Scheduler_ServerUpdate='00:00')
    helper.device = SimpleNamespace(click=Mock())
    helper.ui_get_current_page = Mock()
    helper._period = Mock(return_value=PERIOD)
    helper._capture_mission = Mock()
    helper._authorization = Mock(return_value=dict(current=0, earned=False))
    helper._submit_order = Mock()
    monkeypatch.setattr(mission_orders, 'ensure_event_page', Mock(return_value=True))
    return helper


@pytest.mark.parametrize('text,expected', [
    ('405', 405), (' 54 ', 54), ('2D873', 20873), ('I5DB', 1508),
    ('', None), (None, None), ('cubes54', None), ('54/6', None),
    ('-1', None), ('1.5', None), ('10000000', None)])
def test_resources_require_full_raw_numbers(text, expected):
    assert parse_resource(text) == expected


def test_actual_three_digit_cube_balance_is_not_truncated_by_legacy_crop():
    image = np.asarray(Image.open('tests/gacha/data/event_pool_20261023.png').convert('RGB'))
    assert read_order_resources(image) == dict(coins=20873, cubes=405)


@pytest.mark.parametrize('resources,count,pool,expected', [
    (dict(coins=4500, cubes=6), 3, 'event', True),
    (dict(coins=4499, cubes=6), 3, 'event', False),
    (dict(coins=4500, cubes=5), 3, 'event', False),
    (dict(coins=1800, cubes=3), 3, 'light', True),
    (dict(coins=1500, cubes=2), 1, 'special', True),
    (dict(coins=4500, cubes=True), 3, 'event', False),
    (dict(coins=4500, cubes=6), True, 'event', False),
    (dict(coins=4500, cubes=6), 4, 'event', False),
    (dict(coins=4500, cubes=6), 3, 'unknown', False)])
def test_insufficient_or_unknown_resources_cannot_authorize_partial_or_extra_orders(
        resources, count, pool, expected):
    assert resources_cover_order(resources, count, pool) is expected


def test_closed_game_leaves_order_budget_unspent(tmp_path, monkeypatch):
    helper = controller(monkeypatch)
    helper.ui_get_current_page.side_effect = GameNotRunningError
    with pytest.raises(GameNotRunningError):
        helper.run(folder=tmp_path)
    assert load_order_state('alas', tmp_path) == {}
    helper._authorization.assert_not_called()


@pytest.mark.parametrize('previous', [
    dict(submission_pending=True), dict(submit_clicks=1), dict(orders_submitted=3)])
def test_restart_never_repeats_any_possibly_submitted_order(tmp_path, monkeypatch, previous):
    helper = controller(monkeypatch)
    save_order_state('alas', {KEY: previous}, tmp_path)
    record = helper.run(folder=tmp_path)
    assert record['status'] == 'blocked'
    assert record['reason'] == 'reserved_order_credit_unverified'
    assert record['retry_required'] is True
    helper._submit_order.assert_not_called()
    helper.device.click.assert_not_called()


def test_restart_with_verified_credit_three_only_resumes_claim_journal(tmp_path, monkeypatch):
    helper = controller(monkeypatch)
    save_order_state('alas', {KEY: dict(submit_clicks=1, orders_submitted=3,
                                      mission_credit=3, submission_pending=True)}, tmp_path)
    claim = Mock(return_value=dict(status='complete', mission_reward_claimed=True,
                                   returned_main=True, diagnostics=['receipt.png']))
    monkeypatch.setattr(mission_orders, 'GachaMissionClaim', Mock(return_value=SimpleNamespace(run=claim)))
    record = helper.run(folder=tmp_path)
    assert record['status'] == 'complete' and record['mission_reward_claimed']
    assert record['mission_credit'] == 3 and record['orders_submitted'] == 3
    assert 'receipt.png' in record['diagnostics']
    helper._authorization.assert_not_called()
    helper._submit_order.assert_not_called()
    claim.assert_called_once_with(folder=tmp_path,tabs=(4,))
    again = helper.run(folder=tmp_path)
    assert again['reused'] is True and claim.call_count == 1


def test_earned_complete_row_claims_without_any_order(tmp_path, monkeypatch):
    helper = controller(monkeypatch)
    helper._authorization.return_value = dict(current=3, earned=True)
    monkeypatch.setattr(mission_orders, 'GachaMissionClaim', Mock(return_value=SimpleNamespace(
        run=Mock(return_value=dict(status='complete', mission_reward_claimed=True, returned_main=True)))))
    record = helper.run(folder=tmp_path)
    assert record['remaining'] == 0 and record['mission_credit'] == 3
    assert record['status'] == 'complete' and record['orders_submitted'] == 0
    helper._submit_order.assert_not_called()


def test_pending_order_can_fresh_verify_credit_and_claim_but_cannot_submit(tmp_path, monkeypatch):
    helper = controller(monkeypatch)
    helper._authorization.return_value = dict(current=3, earned=True)
    save_order_state('alas', {KEY: dict(submit_clicks=1, submission_pending=True)}, tmp_path)
    claim = Mock(return_value=dict(status='blocked', mission_reward_claimed=False))
    monkeypatch.setattr(mission_orders, 'GachaMissionClaim', Mock(return_value=SimpleNamespace(run=claim)))
    record = helper.run(folder=tmp_path)
    assert record['reason'] == 'credited_mission_reward_unverified'
    assert record['mission_credit'] == 3 and record['submission_pending'] is False
    assert record['retry_required'] is True
    helper._submit_order.assert_not_called()


def test_unknown_row_after_restart_preserves_pending_and_never_builds(tmp_path, monkeypatch):
    helper = controller(monkeypatch)
    helper._authorization.return_value = None
    save_order_state('alas', {KEY: dict(submit_clicks=1, submission_pending=True)}, tmp_path)
    record = helper.run(folder=tmp_path)
    assert record['submission_pending'] and record['reason'] == 'build_mission_credit_unknown'
    helper._submit_order.assert_not_called()


def test_credit_regression_cannot_make_extra_orders(tmp_path, monkeypatch):
    helper = controller(monkeypatch)
    helper._authorization.return_value = dict(current=0, earned=False)
    save_order_state('alas', {KEY: dict(mission_credit=2)}, tmp_path)
    record = helper.run(folder=tmp_path)
    assert record['reason'] == 'build_mission_credit_regressed' and record['mission_credit'] == 2
    helper._submit_order.assert_not_called()


@pytest.mark.parametrize('previous', [
    dict(submit_clicks=True), dict(submit_clicks=2), dict(orders_submitted=4),
    dict(mission_credit=True), dict(mission_credit='3'), dict(submission_pending=1),
    dict(mission_reward_claimed=1), dict(diagnostics='file')])
def test_corrupt_proof_or_budget_fails_closed_before_navigation(tmp_path, monkeypatch, previous):
    helper = controller(monkeypatch)
    save_order_state('alas', {KEY: previous}, tmp_path)
    with pytest.raises(ScriptError):
        helper.run(folder=tmp_path)
    helper._authorization.assert_not_called()


def test_order_account_path_cannot_escape_state_folder(tmp_path):
    with pytest.raises(ScriptError):
        order_state_path('../alas', tmp_path)


class ImmediateTimer:
    def __init__(self, *args, **kwargs): pass
    def start(self): return self
    def reset(self): return self
    def reached(self): return True


def count(n):
    return dict(count=n, coins=n*1500, cubes=n*2)


def executor(monkeypatch):
    helper = controller(monkeypatch)
    del helper._submit_order
    pool = SimpleNamespace(enter_event_pool=Mock(return_value=True), on_event_pool=Mock(return_value=True))
    monkeypatch.setattr(banner, 'EventBannerObserver', Mock(return_value=pool))
    monkeypatch.setattr(banner, 'resolve_mission_pool', Mock(return_value='event'), raising=False)
    helper._stable_resources = Mock(return_value=dict(coins=4500, cubes=6))
    helper._stable_count = Mock(side_effect=[count(1),count(2),count(3),count(3)])
    helper._pool_after_dialog = Mock(return_value=True)
    helper._count_witness = Mock(return_value=True)
    def opened(record, persist):
        record.update(build_open_clicked=True, reason='order_count_dialog_requires_review')
    helper._open_order_dialog = Mock(side_effect=opened)
    return helper


def audit(**kw):
    value=dict(period=PERIOD,remaining=3,diagnostics=[],submit_clicks=0,orders_submitted=0,
               adjust_clicks=0,cancel_clicks=0,dialog_opens=0,order_dialog_open=False)
    value.update(kw)
    return value


def test_actual_count_cost_and_each_exact_witness_are_required(monkeypatch):
    image=np.asarray(Image.open('tests/gacha/data/event_build_count.png').convert('RGB'))
    assert read_count_cost(image)==count(1)
    helper=GachaMissionOrders.__new__(GachaMissionOrders)
    helper.match_template_color=lambda asset,**kw: asset.match_template_color(image,**kw)
    assert helper._count_witness()
    for asset in COUNT_WITNESSES:
        changed=image.copy()
        x1,y1,x2,y2=asset.area
        changed[y1:y2,x1:x2]=0
        helper.match_template_color=lambda asset,**kw: asset.match_template_color(changed,**kw)
        assert not helper._count_witness(),asset.name


@pytest.mark.parametrize('raw',[(0,1500,2),(4,6000,8),(3,1500,6),(3,4500,2),(3,None,6)])
def test_count_cost_must_match_exact_event_batch(monkeypatch,raw):
    monkeypatch.setattr(mission_orders,'Ocr',Mock(side_effect=[SimpleNamespace(ocr=Mock(return_value=None if n is None else str(n))) for n in raw]))
    assert read_count_cost(object()) is None


def test_submit_is_once_and_durable_before_confirm(monkeypatch):
    helper=executor(monkeypatch)
    record=audit()
    saved=[]
    persist=lambda:saved.append(dict(record))
    def click(button):
        if button is COUNT_CONFIRM:
            assert saved[-1]['submission_pending'] and saved[-1]['submit_clicks']==1
            assert saved[-1]['orders_reserved']==3
    helper.device.click.side_effect=click
    helper._submit_order(record,persist)
    assert helper.device.click.call_args_list==[call(COUNT_PLUS),call(COUNT_PLUS),call(COUNT_CONFIRM)]
    assert record['submit_click_performed'] and record['submission_pending']
    assert record['orders_submitted']==0 # returning to Build never proves credit
    assert record['final_count_cost']==count(3)
    assert not record['order_dialog_open']


def test_rejected_adjustment_is_not_retried_and_exact_cancel_is_once(monkeypatch):
    helper=executor(monkeypatch)
    helper._stable_count.side_effect=[count(1),count(1)]
    record=audit()
    helper._submit_order(record,Mock())
    assert [c.args[0] for c in helper.device.click.call_args_list]==[COUNT_PLUS,COUNT_CANCEL]
    assert record['adjust_clicks']==1 and record['cancel_clicks']==1
    assert record['submit_clicks']==0 and not record['order_dialog_open']


def test_unknown_dialog_cannot_receive_even_cancel(monkeypatch):
    helper=executor(monkeypatch)
    helper._stable_count.side_effect=[None]
    helper._count_witness.return_value=False
    record=audit()
    helper._submit_order(record,Mock())
    helper.device.click.assert_not_called()
    assert record['order_dialog_open'] and not record['submit_clicks']


def test_resource_shortage_never_opens_or_buys_anything(monkeypatch):
    helper=executor(monkeypatch)
    helper._stable_resources.return_value=dict(coins=4499,cubes=6)
    record=audit()
    helper._submit_order(record,Mock())
    helper._open_order_dialog.assert_not_called()
    helper.device.click.assert_not_called()
    assert record['reason']=='insufficient_or_unreadable_build_resources'


def test_positive_owned_banner_cannot_silently_place_more_event_orders(monkeypatch):
    helper=executor(monkeypatch)
    banner.resolve_mission_pool.return_value='light'
    record=audit()
    helper._submit_order(record,Mock())
    assert record['reason']=='fallback_pool_controls_unobserved'
    helper._open_order_dialog.assert_not_called()
    helper.device.click.assert_not_called()


def test_period_crossover_during_submit_persistence_vetoes_final_input(monkeypatch):
    helper=executor(monkeypatch)
    helper._stable_count.side_effect=[count(3),count(3)]
    record=audit()
    def persist():
        if record.get('submit_clicks'):
            helper._period.return_value='new day'
    helper._submit_order(record,persist)
    assert record['submit_clicks']==1 and record['submission_pending']
    helper.device.click.assert_not_called()


def test_submit_then_fresh_credited_mission_and_claim_complete_same_job(tmp_path,monkeypatch):
    helper=executor(monkeypatch)
    helper._authorization.side_effect=[dict(current=0,earned=False),dict(current=3,earned=True)]
    claim=Mock(return_value=dict(status='complete',mission_reward_claimed=True,returned_main=True))
    monkeypatch.setattr(mission_orders,'GachaMissionClaim',Mock(return_value=SimpleNamespace(run=claim)))
    result=helper.run(folder=tmp_path)
    assert result['mission_credit']==3 and result['orders_submitted']==3
    assert result['mission_reward_claimed'] and result['status']=='complete'
    assert result['submit_clicks']==1 and not result['submission_pending']
    assert claim.call_count==1
    saved=load_order_state('alas',tmp_path)[KEY]
    assert saved['mission_reward_claimed']
    helper.run(folder=tmp_path)
    assert helper._open_order_dialog.call_count==1 and claim.call_count==1


def test_uncertain_result_preserves_pending_and_requests_recognized_recovery(tmp_path,monkeypatch):
    helper=executor(monkeypatch)
    helper._pool_after_dialog.return_value=False
    result=helper.run(folder=tmp_path)
    assert result['submission_pending'] and result['submit_clicks']==1
    assert result['order_dialog_open'] and not result['returned_main']
    helper._authorization.assert_called_once()
    mission_orders.ensure_event_page.assert_not_called()


def test_restarted_open_dialog_cannot_be_opened_again(tmp_path,monkeypatch):
    helper=executor(monkeypatch)
    save_order_state('alas',{KEY:dict(dialog_opens=1)},tmp_path)
    result=helper.run(folder=tmp_path)
    assert result['reason']=='dialog_budget_or_period_changed'
    helper._open_order_dialog.assert_not_called()
    helper.device.click.assert_not_called()


@pytest.mark.parametrize('credited,counts,expected_plus',[(1,[1,2,2],1),(2,[1,1],0)])
def test_only_remaining_builds_are_submitted(tmp_path,monkeypatch,credited,counts,expected_plus):
    helper=executor(monkeypatch)
    helper._stable_count.side_effect=[count(n) for n in counts]
    helper._authorization.side_effect=[dict(current=credited,earned=False),dict(current=3,earned=True)]
    monkeypatch.setattr(mission_orders,'GachaMissionClaim',Mock(return_value=SimpleNamespace(
        run=Mock(return_value=dict(status='complete',mission_reward_claimed=True,returned_main=True)))))
    result=helper.run(folder=tmp_path)
    assert result['orders_submitted']==3-credited and result['orders_reserved']==3-credited
    assert [c.args[0] for c in helper.device.click.call_args_list].count(COUNT_PLUS)==expected_plus
    assert [c.args[0] for c in helper.device.click.call_args_list].count(COUNT_CONFIRM)==1


def test_earned_event_only_row_authorizes_claim_not_builds(monkeypatch):
    helper=controller(monkeypatch)
    helper._read_daily_authorization=Mock(return_value=None)
    helper._read_authorization_tab=Mock(return_value=dict(current=3,total=3,earned=True,tab='event'))
    del helper._authorization
    record=dict(diagnostics=[])
    result=helper._authorization(record)
    assert result==dict(current=3,earned=True)
    helper._read_authorization_tab.assert_called_once_with(record,'event',6)


def test_changed_owned_banner_proof_before_final_submit_cancels_event_orders(monkeypatch):
    helper=executor(monkeypatch)
    banner.resolve_mission_pool.side_effect=['event','light']
    record=audit()
    helper._submit_order(record,Mock())
    assert COUNT_CONFIRM not in [c.args[0] for c in helper.device.click.call_args_list]
    assert helper.device.click.call_args.args[0] is COUNT_CANCEL
    assert not record['submit_clicks']


def test_both_positive_tabs_are_deduplicated_for_builds_but_both_rewards_claimed(monkeypatch):
    helper=controller(monkeypatch)
    helper._read_daily_authorization=Mock(return_value=dict(current=3,earned=True,tab='daily'))
    helper._read_authorization_tab=Mock(return_value=dict(current=3,earned=True,tab='event'))
    del helper._authorization
    audit=dict(diagnostics=[])
    assert helper._authorization(audit)['current']==3
    assert audit['claim_tabs']==[4,6]
    claim=Mock(return_value=dict(status='complete',mission_reward_claimed=True,returned_main=True))
    monkeypatch.setattr(mission_orders,'GachaMissionClaim',Mock(return_value=SimpleNamespace(run=claim)))
    helper._claim(audit,'state-folder')
    claim.assert_called_once_with(folder='state-folder',tabs=(4,6))
    helper._submit_order.assert_not_called()


def test_claim_restart_precedes_generic_page_discovery(tmp_path, monkeypatch):
    helper = controller(monkeypatch)
    helper.ui_get_current_page.side_effect = AssertionError('receipt must not reach generic UI')
    save_order_state('alas', {KEY: dict(mission_credit=3, claim_tabs=[4, 6])}, tmp_path)
    claim = Mock(return_value=dict(status='complete', mission_reward_claimed=True, returned_main=True))
    monkeypatch.setattr(mission_orders, 'GachaMissionClaim', Mock(return_value=SimpleNamespace(run=claim)))
    result = helper.run(folder=tmp_path)
    assert result['mission_reward_claimed']
    helper.ui_get_current_page.assert_not_called()
    helper._authorization.assert_not_called()
    helper._submit_order.assert_not_called()
    claim.assert_called_once_with(folder=tmp_path, tabs=(4, 6))


def test_recovery_probe_is_current_account_event_and_configured_reset_only(tmp_path, monkeypatch):
    from datetime import datetime
    cfg = SimpleNamespace(config_name='alas', Scheduler_ServerUpdate='04:00')
    reset = datetime(2026, 10, 23, 4)
    get_reset = Mock(return_value=reset)
    monkeypatch.setattr(mission_orders, 'get_server_last_update', get_reset)
    key = f'{reset.isoformat()}:{CURRENT_EVENT}:{ORDER_VERSION}'
    save_order_state('alas', {KEY: dict(mission_credit=3)}, tmp_path)
    assert not mission_orders.mission_claim_recovery_due(cfg, tmp_path)
    save_order_state('alas', {key: dict(mission_credit=3)}, tmp_path)
    assert mission_orders.mission_claim_recovery_due(cfg, tmp_path)
    cfg.config_name = 'f2p'
    assert not mission_orders.mission_claim_recovery_due(cfg, tmp_path)
    get_reset.assert_called_with('04:00')


def test_claim_unrecognized_receipt_cannot_fall_through_to_coordinator_navigation(tmp_path, monkeypatch):
    helper = controller(monkeypatch)
    save_order_state('alas', {KEY: dict(mission_credit=3)}, tmp_path)
    monkeypatch.setattr(mission_orders, 'GachaMissionClaim', Mock(return_value=SimpleNamespace(
        run=Mock(return_value=dict(status='blocked', mission_reward_claimed=False, returned_main=False)))))
    result = helper.run(folder=tmp_path)
    assert not result['returned_main'] and result['retry_required']
    mission_orders.ensure_event_page.assert_not_called()
    helper.ui_get_current_page.assert_not_called()


def test_verified_reward_with_failed_home_resumes_only_claim_recovery(tmp_path, monkeypatch):
    helper = controller(monkeypatch)
    save_order_state('alas', {KEY: dict(mission_credit=3, mission_reward_claimed=True,
                                      returned_main=False)}, tmp_path)
    claim = Mock(return_value=dict(status='complete', mission_reward_claimed=True, returned_main=True))
    monkeypatch.setattr(mission_orders, 'GachaMissionClaim', Mock(return_value=SimpleNamespace(run=claim)))
    result = helper.run(folder=tmp_path)
    assert result['returned_main'] and result['mission_reward_claimed'] and result['reused']
    helper.ui_get_current_page.assert_not_called()
    helper._submit_order.assert_not_called()
    claim.assert_called_once_with(folder=tmp_path, tabs=(4,))
