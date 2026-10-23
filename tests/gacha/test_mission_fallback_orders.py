from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest
from PIL import Image

from module.exception import ScriptError
from module.gacha import mission_fallback_orders as ordinary
from module.gacha.mission_fallback_orders import OrdinaryMissionOrders, read_ordinary_count_cost
from module.gacha.mission_fallback_assets import ORDINARY_COST_TITLES
from module.gacha.mission_fallback import SHARED_COUNT_WITNESSES
from module.gacha.banner_assets import EVENT_BUILD_ORDERS
from module.gacha.mission_order_assets import UR_CAP_CONFIRM
from module.gacha.mission_submit_assets import COUNT_CANCEL, COUNT_CONFIRM, COUNT_PLUS

PERIOD = '2026-10-23T00:00:00'


class ImmediateTimer:
    def __init__(self, *args, **kwargs): pass
    def start(self): return self
    def reset(self): return self
    def reached(self): return True


def fixture(pool):
    return np.asarray(Image.open(f'tests/gacha/data/{pool}_build_count.png').convert('RGB')).copy()


def record(remaining=3):
    return dict(remaining=remaining, period=PERIOD, diagnostics=[], submit_clicks=0,
                orders_reserved=0, orders_submitted=0, submission_pending=False,
                dialog_opens=0, adjust_clicks=0, cancel_clicks=0, warning_ack_clicks=0)


def controller(monkeypatch):
    helper = OrdinaryMissionOrders.__new__(OrdinaryMissionOrders)
    helper.config = SimpleNamespace(config_name='alas', Gacha_FallbackPool='light')
    helper.device = SimpleNamespace(click=Mock(), screenshot=Mock())
    helper._period = Mock(return_value=PERIOD)
    helper._enter_pool = Mock(return_value=True)
    helper._on_pool = Mock(return_value=True)
    helper._stable_resources = Mock(return_value=dict(coins=9000,cubes=20))
    helper._open_count = Mock(side_effect=lambda rec,*args: rec.update(
        dialog_opens=1, build_open_clicked=True, order_dialog_open=True) or True)
    helper._stable_count = Mock(return_value=dict(count=3, coins=1800,cubes=3))
    helper._count_witness = Mock(return_value=True)
    helper._wait_pool = Mock(return_value=True)
    helper._return_after_submit = Mock(return_value=True)
    helper._ur_cap_warning = Mock(return_value=False)
    helper._capture = Mock(return_value='diagnostic.png')
    helper.loop = Mock(side_effect=lambda **kw: iter(range(20)))
    monkeypatch.setattr(ordinary, 'Timer', ImmediateTimer)
    monkeypatch.setattr(ordinary, 'resolve_mission_pool', Mock(return_value='light'))
    monkeypatch.setattr(ordinary, 'ensure_event_running', Mock())
    return helper


@pytest.mark.parametrize('pool', ['light','heavy','special'])
def test_actual_pool_title_and_costs_are_read_without_submitting(pool, monkeypatch):
    helper = controller(monkeypatch)
    del helper._count_witness
    helper.device.image = fixture(pool)
    helper._exact = lambda asset: asset.match_template_color(helper.device.image, offset=(3,3), similarity=.85)
    assert helper._count_witness(pool)
    assert read_ordinary_count_cost(helper.device.image, pool) == dict(
        count=1, coins=600 if pool=='light' else 1500,cubes=1 if pool=='light' else 2)
    for asset in (*SHARED_COUNT_WITNESSES,ORDINARY_COST_TITLES[pool]):
        helper.device.image = fixture(pool)
        x1,y1,x2,y2=asset.area
        helper.device.image[y1-4:y2+4,x1-4:x2+4]=0
        assert not helper._count_witness(pool), asset.name
    helper.device.click.assert_not_called()


@pytest.mark.parametrize('source', ['light','heavy','special','event'])
def test_wrong_pool_and_event_cost_titles_cannot_authorize_ordinary_order(source,monkeypatch):
    helper=controller(monkeypatch); del helper._count_witness
    path='tests/gacha/data/event_build_count.png' if source=='event' else f'tests/gacha/data/{source}_build_count.png'
    helper.device.image=np.asarray(Image.open(path).convert('RGB'))
    helper._exact=lambda asset:asset.match_template_color(helper.device.image,offset=(3,3),similarity=.85)
    for pool in ORDINARY_COST_TITLES:
        if pool!=source: assert not helper._count_witness(pool), (source,pool)


def test_blank_or_wrong_unit_cost_does_not_read_as_zero_or_valid_order():
    image=fixture('light')
    assert read_ordinary_count_cost(image,'heavy') is None
    assert read_ordinary_count_cost(np.zeros_like(image),'light') is None
    assert read_ordinary_count_cost(image,'event') is None


@pytest.mark.parametrize('remaining', [1,2,3])
def test_only_remaining_orders_share_durable_daily_submission_budget(monkeypatch,remaining):
    helper=controller(monkeypatch); rec=record(remaining)
    def cost(count): return dict(count=count,coins=600*count,cubes=count)
    helper._stable_count.side_effect=[cost(n) for n in range(1,remaining+1)]+[cost(remaining)]
    saved=[]
    def persist():saved.append(deepcopy(rec))
    def click(button):
        if button==COUNT_CONFIRM:
            assert saved[-1]['submit_clicks']==1
            assert saved[-1]['orders_reserved']==remaining
            assert saved[-1]['submission_pending']
            assert 'submit_click_performed' not in saved[-1]
        else:
            assert button==COUNT_PLUS
            assert saved[-1]['adjust_clicks']>=1
    helper.device.click.side_effect=click
    assert helper.submit(rec,persist,'light')
    assert rec['submit_click_performed'] and not rec['order_dialog_open']
    assert rec['orders_submitted']==0  # Mission credit is the caller's proof.
    assert rec['adjust_clicks']==remaining-1
    assert [c.args[0] for c in helper.device.click.call_args_list]==[COUNT_PLUS]*(remaining-1)+[COUNT_CONFIRM]
    before=helper.device.click.call_count
    assert not helper.submit(rec,persist,'light')
    assert helper.device.click.call_count==before


@pytest.mark.parametrize('pool', ['heavy','special'])
def test_configured_nonlight_pool_uses_its_higher_resource_cost(monkeypatch,pool):
    helper=controller(monkeypatch);rec=record()
    ordinary.resolve_mission_pool.return_value=pool
    helper.config.Gacha_FallbackPool=pool
    helper._stable_count.return_value=dict(count=3,coins=4500,cubes=6)
    assert helper.submit(rec,Mock(),pool)
    assert rec['pool']==pool and rec['orders_reserved']==3
    ordinary.resolve_mission_pool.assert_called_with('alas',fallback=pool,folder='./config/gacha')


@pytest.mark.parametrize('proof', ['event','heavy'])
def test_missing_or_changed_banner_ownership_never_navigates_to_fallback(monkeypatch,proof):
    helper=controller(monkeypatch);rec=record()
    ordinary.resolve_mission_pool.return_value=proof
    assert not helper.submit(rec,Mock(),'light')
    helper._enter_pool.assert_not_called();helper.device.click.assert_not_called()


@pytest.mark.parametrize('resources', [None,dict(coins=1799,cubes=3),dict(coins=1800,cubes=2)])
def test_unreadable_or_insufficient_resources_never_open_count(monkeypatch,resources):
    helper=controller(monkeypatch);rec=record()
    helper._stable_resources.return_value=resources
    assert not helper.submit(rec,Mock(),'light')
    helper._open_count.assert_not_called();helper.device.click.assert_not_called()


def test_adjustment_must_reach_exact_next_count_or_only_cancel(monkeypatch):
    helper=controller(monkeypatch);rec=record()
    helper._stable_count.side_effect=[dict(count=1,coins=600,cubes=1),dict(count=1,coins=600,cubes=1)]
    assert not helper.submit(rec,Mock(),'light')
    assert [c.args[0] for c in helper.device.click.call_args_list]==[COUNT_PLUS,COUNT_CANCEL]
    assert not rec['submit_clicks'] and rec['cancel_clicks']==1


def test_ownership_expires_before_final_confirmation_only_exact_cancel(monkeypatch):
    helper=controller(monkeypatch);rec=record()
    ordinary.resolve_mission_pool.side_effect=['light','event']
    assert not helper.submit(rec,Mock(),'light')
    helper.device.click.assert_called_once_with(COUNT_CANCEL)
    assert not rec['submit_clicks']


def test_reset_during_durable_submit_reservation_vetoes_confirm(monkeypatch):
    helper=controller(monkeypatch);rec=record()
    def persist():
        if rec.get('submit_clicks'):helper._period.return_value='2026-10-24T00:00:00'
    assert not helper.submit(rec,persist,'light')
    assert rec['submit_clicks']==1 and rec['orders_reserved']==3
    helper.device.click.assert_not_called()


def test_expired_ownership_during_submit_reservation_vetoes_confirm(monkeypatch):
    helper=controller(monkeypatch);rec=record()
    def persist():
        if rec.get('submit_clicks'):ordinary.resolve_mission_pool.return_value='event'
    assert not helper.submit(rec,persist,'light')
    assert rec['submit_clicks']==1
    helper.device.click.assert_not_called()


def test_unverified_post_submit_never_cancels_or_reopens(monkeypatch):
    helper=controller(monkeypatch);rec=record()
    helper._return_after_submit.return_value=False
    assert not helper.submit(rec,Mock(),'light')
    assert rec['order_dialog_open'] and rec['submit_clicks']==1
    helper.device.click.assert_called_once_with(COUNT_CONFIRM)
    assert not helper.submit(rec,Mock(),'light')
    helper.device.click.assert_called_once_with(COUNT_CONFIRM)


def test_actual_open_and_warning_inputs_are_reserved_before_click(monkeypatch):
    helper=controller(monkeypatch);del helper._open_count
    rec=record();rec['pool']='light'
    phase={'value':'pool'};saved=[]
    helper._count_witness.side_effect=lambda pool:phase['value']=='count'
    helper._ur_cap_warning.side_effect=lambda:phase['value']=='warning'
    def click(button):
        if button==EVENT_BUILD_ORDERS:
            assert saved[-1]['dialog_opens']==1 and saved[-1]['order_dialog_open']
            phase['value']='warning'
        else:
            assert button==UR_CAP_CONFIRM and saved[-1]['warning_ack_clicks']==1
            phase['value']='count'
    helper.device.click.side_effect=click
    assert helper._open_count(rec,lambda:saved.append(deepcopy(rec)),'light','./config/gacha')
    assert [c.args[0] for c in helper.device.click.call_args_list]==[EVENT_BUILD_ORDERS,UR_CAP_CONFIRM]
    assert rec['submit_clicks']==0


def test_same_warning_cannot_be_acknowledged_twice(monkeypatch):
    helper=controller(monkeypatch);del helper._open_count
    rec=record();rec['pool']='light'
    helper._ur_cap_warning.return_value=True
    assert not helper._open_count(rec,Mock(),'light','./config/gacha')
    assert [c.args[0] for c in helper.device.click.call_args_list]==[EVENT_BUILD_ORDERS,UR_CAP_CONFIRM]


def test_reset_between_opener_reservation_and_click_spends_budget_without_input(monkeypatch):
    helper=controller(monkeypatch);del helper._open_count
    rec=record()
    def persist():helper._period.return_value='2026-10-24T00:00:00'
    assert not helper._open_count(rec,persist,'light','./config/gacha')
    assert rec['dialog_opens']==1 and rec['order_dialog_open']
    helper.device.click.assert_not_called()


@pytest.mark.parametrize('budget',['submit_clicks','orders_reserved','orders_submitted','submission_pending',
                                 'dialog_opens','build_open_clicked','adjust_clicks','cancel_clicks','warning_ack_clicks'])
def test_any_prior_budget_prevents_a_second_ordinary_attempt(monkeypatch,budget):
    helper=controller(monkeypatch);rec=record();rec[budget]=1
    assert not helper.submit(rec,Mock(),'light')
    helper._enter_pool.assert_not_called();helper.device.click.assert_not_called()


@pytest.mark.parametrize('remaining',[None,0,4,True,'3'])
def test_invalid_mission_remaining_never_navigates(monkeypatch,remaining):
    helper=controller(monkeypatch);rec=record(remaining)
    with pytest.raises(ScriptError):helper.submit(rec,Mock(),'light')
    helper._enter_pool.assert_not_called();helper.device.click.assert_not_called()
