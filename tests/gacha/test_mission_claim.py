import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call

import numpy as np
import pytest
from PIL import Image

from module.base.button import Button
from module.combat.assets import GET_ITEMS_1
from module.exception import GameNotRunningError, ScriptError
from module.gacha import mission, mission_claim
from module.gacha.mission_claim import (
    CLAIM_VERSION, GachaMissionClaim, claim_state_path, earned_build_mission,
    load_claim_state, save_claim_state)
from module.ui.page import page_main, page_mission

DATA = Path(__file__).parent / 'data'


class ImmediateTimer:
    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        return self

    def reset(self):
        return self

    def reached(self):
        return True


def screenshot(name='build_three_main.png'):
    return np.array(Image.open(DATA / name).convert('RGB'))


def day_key():
    period = mission_claim.get_server_last_update('00:00').isoformat()
    return f'{period}:{mission.CURRENT_EVENT}:{CLAIM_VERSION}'


def controller():
    helper = GachaMissionClaim.__new__(GachaMissionClaim)
    helper.config = SimpleNamespace(config_name='alas', Scheduler_ServerUpdate='00:00')
    helper.device = SimpleNamespace(image=screenshot(), click=Mock(), stuck_record_clear=Mock())
    helper._capture = Mock()
    helper._resume_receipts = Mock()
    helper._reward_dialog = Mock(return_value=None)
    helper.reward_side_navbar_ensure = Mock(return_value=True)
    return helper


def record(**updates):
    value = dict(status='claiming', claim_clicks=0, reward_dialogs=0, receipt_seen=False,
                 pending_claim=False, mission_reward_claimed=False, diagnostics=[],
                 period=mission_claim.get_server_last_update('00:00').isoformat())
    value.update(updates)
    return value


@pytest.mark.parametrize('name', ['build_three_main.png', 'build_three_f2p.png'])
def test_actual_unfinished_build_target_never_claims_another_earned_row(name):
    assert mission.locate_build_mission(screenshot(name)) is not None
    assert earned_build_mission(screenshot(name)) is None


def earned_row_composite():
    # Composition test only: reuse the actual completed first Daily row and
    # replace its title with the actual Build 3 title. This is not live proof
    # that the Build mission has been earned.
    image = screenshot()
    title = image[435:460, 257:389].copy()
    image[128:153, 257:389] = title
    image[435:460, 257:389] = 0
    return image


def test_observed_complete_and_blue_claim_must_share_exact_build_title_row():
    image = earned_row_composite()
    button = earned_build_mission(image)
    assert button is not None
    assert 1110 <= button.button[0] <= 1130 and 135 <= button.button[1] <= 150
    image[164:182, 523:660] = 0
    assert earned_build_mission(image) is None


def test_gray_go_cannot_authorize_a_claim_even_with_complete_text():
    image = earned_row_composite()
    image[128:178, 1111:1230] = screenshot()[435:485, 1111:1230]
    assert earned_build_mission(image) is None


def test_missing_row_marker_rejects_otherwise_earned_title_and_claim():
    image = earned_row_composite()
    image[87:163, 175:197] = 0
    assert earned_build_mission(image) is None


def test_claim_is_durable_before_input_and_postclaim_requires_receipt_and_stable_list(tmp_path, monkeypatch):
    helper = controller()
    monkeypatch.setattr(mission_claim, 'Timer', ImmediateTimer)
    monkeypatch.setattr(mission_claim, 'ensure_event_running', Mock())
    monkeypatch.setattr(mission_claim, 'ensure_event_page', Mock(return_value=True))
    frame = {'receipt': False}
    def frames(**kwargs):
        for is_receipt in [False] * 3 + [True] + [False] * 3:
            frame['receipt'] = is_receipt
            yield
    helper._daily_list = Mock(side_effect=lambda: not frame['receipt'])
    helper.loop = frames
    helper._reward_get_state = Mock(return_value=object())
    helper._reward_dialog = Mock(side_effect=lambda: GET_ITEMS_1 if frame['receipt'] else None)
    button = Button((1118, 449, 1224, 479), (0, 0, 0), (1118, 449, 1224, 479), name='TARGET_CLAIM')
    monkeypatch.setattr(mission_claim, 'earned_build_mission', Mock(side_effect=[button] * 3 + [None] * 3))
    monkeypatch.setattr(mission, 'locate_build_mission', Mock(side_effect=[{}] * 3 + [None] * 3))

    def click(target):
        stored = load_claim_state('alas', tmp_path)[day_key()]
        assert stored['claim_clicks'] == 1
        if target is GET_ITEMS_1:
            assert stored['receipt_seen'] is True and stored['reward_dialogs'] == 1
        else:
            assert target is button and stored['pending_claim'] is True

    helper.device.click.side_effect = click
    result = helper.run(folder=tmp_path)
    assert result['status'] == 'complete' and result['mission_reward_claimed'] is True
    assert result['returned_main'] is True
    assert helper.device.click.call_count == 2
    helper.reward_side_navbar_ensure.assert_called_once_with(upper=4)
    mission_claim.ensure_event_page.assert_any_call(helper, page_mission)
    mission_claim.ensure_event_page.assert_any_call(helper, page_main)
    again = helper.run(folder=tmp_path)
    assert again['reused'] is True
    assert helper.device.click.call_count == 2


def test_restart_pending_claim_never_repeats_claim_or_submits_builds(tmp_path, monkeypatch):
    save_claim_state('alas', {day_key(): record(claim_clicks=1, pending_claim=True)}, tmp_path)
    helper = controller()
    monkeypatch.setattr(mission_claim, 'ensure_event_running', Mock())
    monkeypatch.setattr(mission_claim, 'ensure_event_page', Mock(return_value=True))
    monkeypatch.setattr(mission_claim, 'Timer', ImmediateTimer)
    helper._daily_list = Mock(return_value=True)
    helper.loop = Mock(return_value=iter(range(5)))
    button = Button((1118, 449, 1224, 479), (0, 0, 0), (1118, 449, 1224, 479), name='TARGET_CLAIM')
    monkeypatch.setattr(mission_claim, 'earned_build_mission', Mock(return_value=button))
    monkeypatch.setattr(mission, 'locate_build_mission', Mock(return_value={}))
    result = helper.run(folder=tmp_path)
    assert result['status'] == 'blocked'
    assert result['claim_clicks'] == 1 and result['mission_reward_claimed'] is False
    helper._resume_receipts.assert_called_once()
    helper.device.click.assert_not_called()
    helper.loop.assert_called_once_with(skip_first=False, timeout=40)


def test_absence_without_receipt_does_not_claim_success(monkeypatch):
    helper = controller()
    monkeypatch.setattr(mission_claim, 'Timer', ImmediateTimer)
    monkeypatch.setattr(mission_claim, 'earned_build_mission', Mock(return_value=None))
    monkeypatch.setattr(mission, 'locate_build_mission', Mock(return_value=None))
    helper._daily_list = Mock(return_value=True)
    helper._reward_get_state = Mock(return_value=object())
    helper.loop = Mock(return_value=iter(range(5)))
    audit = record(claim_clicks=1, pending_claim=True)
    helper._collect(audit, Mock())
    assert audit['status'] == 'blocked' and audit['mission_reward_claimed'] is False
    helper.device.click.assert_not_called()


def test_unrelated_reward_dialog_is_never_dismissed_without_our_claim():
    helper = controller()
    helper._reward_dialog = Mock(return_value=GET_ITEMS_1)
    assert helper._dismiss_receipt(record(), Mock()) is False
    helper._reward_dialog.assert_not_called()
    helper.device.click.assert_not_called()


def test_receipt_dismissals_have_durable_limit():
    helper = controller()
    helper._reward_dialog = Mock(return_value=GET_ITEMS_1)
    assert helper._dismiss_receipt(record(claim_clicks=1, reward_dialogs=8), Mock()) is False
    helper.device.click.assert_not_called()


def test_bad_mission_page_returns_main_without_claim(tmp_path, monkeypatch):
    helper = controller()
    monkeypatch.setattr(mission_claim, 'ensure_event_running', Mock())
    monkeypatch.setattr(mission_claim, 'ensure_event_page', Mock(side_effect=[False, True]))
    helper._collect = Mock()
    result = helper.run(folder=tmp_path)
    assert result['reason'] == 'daily_mission_page_unrecognized'
    helper._collect.assert_not_called()
    helper.device.click.assert_not_called()


def test_claim_key_uses_configured_server_reset_and_current_event(tmp_path, monkeypatch):
    helper = controller()
    helper.config.Scheduler_ServerUpdate = '04:00'
    reset = datetime(2026, 10, 22, 4)
    get_reset = Mock(return_value=reset)
    monkeypatch.setattr(mission_claim, 'get_server_last_update', get_reset)
    monkeypatch.setattr(mission_claim, 'ensure_event_running', Mock())
    monkeypatch.setattr(mission_claim, 'ensure_event_page', Mock(side_effect=[False, True]))
    result = helper.run(folder=tmp_path)
    assert result['period'] == reset.isoformat()
    assert result['event'] == mission.CURRENT_EVENT
    assert f'{reset.isoformat()}:{mission.CURRENT_EVENT}:{CLAIM_VERSION}' in load_claim_state('alas', tmp_path)
    assert get_reset.call_args_list == [call('04:00'), call('04:00')]


@pytest.mark.parametrize('after_reservation', [False, True])
def test_server_reset_during_navigation_or_persistence_vetoes_claim(monkeypatch, after_reservation):
    helper = controller()
    monkeypatch.setattr(mission_claim, 'Timer', ImmediateTimer)
    button = Button((1118, 449, 1224, 479), (0, 0, 0), (1118, 449, 1224, 479), name='TARGET_CLAIM')
    monkeypatch.setattr(mission_claim, 'earned_build_mission', Mock(return_value=button))
    monkeypatch.setattr(mission, 'locate_build_mission', Mock(return_value={}))
    helper._daily_list = Mock(return_value=True)
    helper.loop = Mock(return_value=iter(range(3)))
    audit = record(period='before-reset')
    helper._period = Mock(side_effect=['before-reset', 'after-reset'] if after_reservation else ['after-reset'])
    persist = Mock()
    helper._collect(audit, persist)
    assert audit['status'] == 'blocked' and audit['reason'] == 'mission_period_changed'
    assert audit['claim_clicks'] == int(after_reservation)
    assert audit['pending_claim'] is False
    assert persist.call_count == int(after_reservation)
    helper.device.click.assert_not_called()


def test_closed_game_never_reserves_claim_budget(tmp_path, monkeypatch):
    helper = controller()
    monkeypatch.setattr(mission_claim, 'ensure_event_running', Mock(side_effect=GameNotRunningError))
    with pytest.raises(GameNotRunningError):
        helper.run(folder=tmp_path)
    assert load_claim_state('alas', tmp_path) == {}


@pytest.mark.parametrize('invalid', ['garbage', '[]', '{"day": 2}'])
def test_invalid_ledger_fails_closed(tmp_path, invalid):
    Path(claim_state_path('alas', tmp_path)).write_text(invalid)
    with pytest.raises(ScriptError):
        load_claim_state('alas', tmp_path)


@pytest.mark.parametrize('value', [-1, 2, True, '1'])
def test_invalid_claim_count_cannot_authorize_input(tmp_path, monkeypatch, value):
    save_claim_state('alas', {day_key(): record(claim_clicks=value)}, tmp_path)
    helper = controller()
    monkeypatch.setattr(mission_claim, 'ensure_event_running', Mock())
    with pytest.raises(ScriptError):
        helper.run(folder=tmp_path)
    helper.device.click.assert_not_called()


def test_account_validation_keeps_claim_audit_in_its_folder(tmp_path):
    with pytest.raises(ScriptError):
        claim_state_path('../alas', tmp_path)


def test_event_claim_has_separate_durable_budget_from_daily(tmp_path,monkeypatch):
    helper=controller()
    monkeypatch.setattr(mission_claim,'ensure_event_running',Mock())
    monkeypatch.setattr(mission_claim,'ensure_event_page',Mock(return_value=True))
    def collect(audit,persist):
        audit.update(claim_clicks=1,claim_tab=helper._claim_upper,pending_claim=True,status='blocked')
        persist()
    helper._collect=Mock(side_effect=collect)
    result=helper.run(folder=tmp_path,tabs=(4,6))
    assert result['claim_clicks']==2 and not result['mission_reward_claimed']
    saved=load_claim_state('alas',tmp_path)
    assert saved[day_key()]['claim_clicks']==1
    assert saved[day_key()+':tab6']['claim_clicks']==1
    assert [call.kwargs['upper'] for call in helper.reward_side_navbar_ensure.call_args_list]==[4,6]
    helper._collect=Mock()
    helper.run(folder=tmp_path,tabs=(4,6))
    assert helper._resume_receipts.call_count==2
    helper.device.click.assert_not_called()


def test_both_rewards_require_each_tab_verified(monkeypatch):
    helper=controller()
    helper._run_tab=Mock(side_effect=[dict(status='complete',mission_reward_claimed=True,returned_main=True),
                                     dict(status='blocked',mission_reward_claimed=False,returned_main=True)])
    result=helper.run(tabs=(4,6))
    assert result['status']=='blocked' and not result['mission_reward_claimed']
    assert list(result['tab_results'])==[4,6]


def test_selected_event_list_requires_exact_active_event_tab():
    helper=controller()
    helper._claim_upper=6
    helper.ui_page_appear=Mock(return_value=True)
    nav=SimpleNamespace(get_info=Mock(return_value=(5,0,5)))
    helper.__dict__['_reward_side_navbar']=nav
    assert helper._daily_list()
    nav.get_info.return_value=(3,0,5)
    assert not helper._daily_list()


def test_pending_event_receipt_is_resumed_before_unclaimed_daily_tab(tmp_path):
    helper = controller()
    save_claim_state('alas', {day_key()+':tab6': record(claim_clicks=1, pending_claim=True, claim_tab=6)}, tmp_path)
    helper._run_tab = Mock(return_value=dict(status='complete', mission_reward_claimed=True, returned_main=True))
    result = helper.run(folder=tmp_path, tabs=(4, 6))
    assert [call.kwargs['upper'] for call in helper._run_tab.call_args_list] == [6, 4]
    assert list(result['tab_results']) == [6, 4]
    assert result['mission_reward_claimed']


def test_owned_receipt_takes_priority_over_matching_background_page(monkeypatch):
    helper = controller()
    del helper._resume_receipts
    monkeypatch.setattr(mission_claim, 'Timer', ImmediateTimer)
    visible = {'receipt': True}
    helper._reward_dialog = Mock(side_effect=lambda: GET_ITEMS_1 if visible['receipt'] else None)
    helper.ui_page_appear = Mock(return_value=True)
    helper.loop = Mock(return_value=iter(range(2)))
    audit = record(claim_clicks=1, pending_claim=True)
    persist = Mock()
    def click(button):
        assert audit['receipt_seen'] and audit['reward_dialogs'] == 1
        assert persist.call_count == 1
        visible['receipt'] = False
    helper.device.click.side_effect = click
    assert helper._resume_receipts(audit, persist)
    helper.device.click.assert_called_once_with(GET_ITEMS_1)
    assert audit['receipt_seen']


def test_exhausted_owned_receipt_never_falls_through_to_generic_navigation(tmp_path, monkeypatch):
    helper = controller()
    del helper._resume_receipts
    monkeypatch.setattr(mission_claim, 'Timer', ImmediateTimer)
    monkeypatch.setattr(mission_claim, 'ensure_event_running', Mock())
    navigate = Mock()
    monkeypatch.setattr(mission_claim, 'ensure_event_page', navigate)
    save_claim_state('alas', {day_key(): record(claim_clicks=1, reward_dialogs=8, pending_claim=True)}, tmp_path)
    helper._reward_dialog = Mock(return_value=GET_ITEMS_1)
    helper.ui_page_appear = Mock(return_value=True)
    helper.loop = Mock(return_value=iter(range(3)))
    result = helper.run(folder=tmp_path)
    assert result['status'] == 'blocked' and not result['returned_main']
    assert not result['mission_reward_claimed']
    navigate.assert_not_called()
    helper.device.click.assert_not_called()


def test_verified_claim_can_recover_failed_home_without_reclaim(tmp_path, monkeypatch):
    helper = controller()
    save_claim_state('alas', {day_key(): record(claim_clicks=1, receipt_seen=True,
        mission_reward_claimed=True, returned_main=False)}, tmp_path)
    monkeypatch.setattr(mission_claim, 'ensure_event_running', Mock())
    navigate = Mock(return_value=True)
    monkeypatch.setattr(mission_claim, 'ensure_event_page', navigate)
    helper._collect = Mock()
    result = helper.run(folder=tmp_path)
    assert result['mission_reward_claimed'] and result['returned_main'] and result['reused']
    navigate.assert_called_once_with(helper, page_main)
    helper._collect.assert_not_called()
    helper.device.click.assert_not_called()
