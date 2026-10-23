from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest
from PIL import Image

from module.exception import GameNotRunningError, ScriptError
from module.gacha import mission_fallback as fallback
from module.gacha.banner_assets import EVENT_BUILD_ORDERS
from module.gacha.mission_fallback import (
    CURRENT_EVENT, FALLBACK_OBSERVATION_VERSION, GachaFallbackObserver,
    SHARED_COUNT_WITNESSES, fallback_state_path, load_fallback_state, save_fallback_state)
from module.gacha.mission_submit_assets import COUNT_CANCEL, COUNT_CONFIRM
from module.gacha.mission_order_assets import UR_CAP_CONFIRM
from module.ui.page import page_build, page_main

PERIOD = '2026-10-23T00:00:00'


def key(pool='light'):
    return f'{CURRENT_EVENT}:{pool}:{FALLBACK_OBSERVATION_VERSION}'


class ImmediateTimer:
    def __init__(self, *args, **kwargs): pass
    def start(self): return self
    def reset(self): return self
    def reached(self): return True


def controller(monkeypatch, frames=20):
    helper = GachaFallbackObserver.__new__(GachaFallbackObserver)
    helper.config = SimpleNamespace(config_name='alas', Scheduler_ServerUpdate='00:00')
    helper.device = SimpleNamespace(click=Mock(), screenshot=Mock())
    helper._period = Mock(return_value=PERIOD)
    helper._enter_pool = Mock(return_value=True)
    helper._on_pool = Mock(return_value=True)
    helper._count_dialog = Mock(return_value=False)
    helper._ur_cap_warning = Mock(return_value=False)
    helper.loop = Mock(side_effect=lambda **kw: iter(range(frames)))
    def capture(record, tag):
        path = tag + '.png'
        record['diagnostics'].append(path)
        return path
    helper._capture = Mock(side_effect=capture)
    monkeypatch.setattr(fallback, 'Timer', ImmediateTimer)
    monkeypatch.setattr(fallback, 'ensure_event_running', Mock())
    monkeypatch.setattr(fallback, 'ensure_event_page', Mock(return_value=True))
    return helper


def navigation_controller(monkeypatch, bottom=(0, 0, 3), side=(0, 0, 4)):
    helper = controller(monkeypatch)
    del helper._enter_pool
    del helper._on_pool
    helper.ui_page_appear = Mock(return_value=True)
    helper._gacha_side_navbar = SimpleNamespace(
        get_info=Mock(return_value=side), set=Mock(return_value=True))
    helper._construct_bottom_navbar = SimpleNamespace(
        get_info=Mock(return_value=bottom), set=Mock(return_value=True))
    helper._exact = Mock(return_value=True)
    helper._wait_pool = Mock(return_value=True)
    return helper


def test_shared_count_cancel_requires_every_observed_dialog_witness(monkeypatch):
    helper = controller(monkeypatch)
    del helper._count_dialog
    image = np.asarray(Image.open('tests/gacha/data/event_build_count.png').convert('RGB')).copy()
    helper.device.image = image
    helper._exact = lambda button: button.match_template_color(
        helper.device.image, offset=(3, 3), similarity=.85)
    assert helper._count_dialog()
    assert COUNT_CONFIRM in SHARED_COUNT_WITNESSES
    for asset in SHARED_COUNT_WITNESSES:
        helper.device.image = image.copy()
        x1, y1, x2, y2 = asset.area
        helper.device.image[y1-4:y2+4, x1-4:x2+4] = 0
        assert not helper._count_dialog(), asset.name
    helper.device.click.assert_not_called()


@pytest.mark.parametrize('pool,right,target', [('light', 3, 1), ('heavy', 2, 2), ('special', 1, 3)])
def test_observer_navigation_uses_build_and_exact_known_pool_index(monkeypatch, pool, right, target):
    helper = navigation_controller(monkeypatch, bottom=(target, 0, 3))
    assert helper._enter_pool(pool)
    helper._gacha_side_navbar.set.assert_called_once_with(
        helper, upper=1, skip_first_screenshot=False)
    helper._construct_bottom_navbar.set.assert_called_once_with(
        helper, right=right, skip_first_screenshot=False)
    assert helper._on_pool(pool)
    helper._construct_bottom_navbar.get_info.return_value = ((target + 1) % 4, 0, 3)
    assert not helper._on_pool(pool)
    helper.device.click.assert_not_called()


@pytest.mark.parametrize('pool,target', [('light', 0), ('heavy', 1), ('special', 2)])
def test_ordinary_three_pool_navbar_uses_same_right_index(monkeypatch, pool, target):
    helper = navigation_controller(monkeypatch, bottom=(target, 0, 2), side=(0, 0, 3))
    assert helper._on_pool(pool)


@pytest.mark.parametrize('bottom,side', [
    ((None, 0, 3), (0, 0, 4)), ((0, 1, 3), (0, 0, 4)),
    ((0, 0, 1), (0, 0, 4)), ((0, 0, 3), (None, 0, 4)),
    ((0, 0, 3), (0, 0, 2)), ((0, 0, 3), (0, 1, 4))])
def test_unknown_navigation_fails_before_order_open(monkeypatch, bottom, side):
    helper = navigation_controller(monkeypatch, bottom=bottom, side=side)
    assert not helper._enter_pool('light')
    helper._construct_bottom_navbar.set.assert_not_called()
    helper.device.click.assert_not_called()


def test_opener_needs_fresh_page_build_sidebar_target_and_exact_button(monkeypatch):
    helper = navigation_controller(monkeypatch, bottom=(1, 0, 3))
    helper._exact.return_value = False
    assert not helper._on_pool('light')
    helper._exact.return_value = True
    helper.ui_page_appear.return_value = False
    assert not helper._on_pool('light')
    helper.ui_page_appear.return_value = True
    helper._gacha_side_navbar.get_info.return_value = (2, 0, 4)
    assert not helper._on_pool('light')  # Orders queue is not Build.


def test_one_open_and_cancel_are_persisted_before_each_input(tmp_path, monkeypatch):
    helper = controller(monkeypatch)
    phase = {'count': False}
    helper._count_dialog.side_effect = lambda: phase['count']
    helper._on_pool.side_effect = lambda pool: not phase['count']
    def click(button):
        audit = load_fallback_state('alas', tmp_path)[key()]
        assert audit['orders_submitted'] == 0
        if button == EVENT_BUILD_ORDERS:
            assert audit['open_clicks'] == 1 and audit['dialog_open']
            phase['count'] = True
        else:
            assert button == COUNT_CANCEL
            assert audit['cancel_clicks'] == 1 and audit['count_captured']
            phase['count'] = False
    helper.device.click.side_effect = click
    result = helper.run(folder=tmp_path)
    assert result['status'] == 'observed' and result['returned_main']
    assert result['open_clicks'] == result['cancel_clicks'] == 1
    assert result['count_captured'] and not result['dialog_open']
    assert [call.args[0] for call in helper.device.click.call_args_list] == [EVENT_BUILD_ORDERS, COUNT_CANCEL]
    assert COUNT_CONFIRM not in [call.args[0] for call in helper.device.click.call_args_list]
    assert result['orders_submitted'] == 0
    fallback.ensure_event_page.assert_called_once_with(helper, page_main)


def test_restart_after_reserved_open_only_recovers_exact_cancel(tmp_path, monkeypatch):
    helper = controller(monkeypatch)
    save_fallback_state('alas', {key(): dict(open_clicks=1, dialog_open=True)}, tmp_path)
    phase = {'count': True}
    helper._count_dialog.side_effect = lambda: phase['count']
    helper._on_pool.side_effect = lambda pool: not phase['count']
    helper.device.click.side_effect = lambda button: phase.update(count=False)
    result = helper.run(folder=tmp_path)
    assert result['status'] == 'observed' and result['returned_main']
    helper._enter_pool.assert_not_called()
    helper.device.click.assert_called_once_with(COUNT_CANCEL)


def test_cancel_budget_cannot_repeat_or_navigate_behind_dialog(tmp_path, monkeypatch):
    helper = controller(monkeypatch)
    save_fallback_state('alas', {key(): dict(open_clicks=1, cancel_clicks=1,
                                              count_captured=True, dialog_open=True)}, tmp_path)
    helper._count_dialog.return_value = True
    result = helper.run(folder=tmp_path)
    assert result['status'] == 'blocked' and not result['returned_main']
    helper.device.click.assert_not_called()
    helper._enter_pool.assert_not_called()
    fallback.ensure_event_page.assert_not_called()


def test_restart_recovers_known_ur_warning_then_captures_and_cancels(tmp_path, monkeypatch):
    helper = controller(monkeypatch)
    save_fallback_state('alas', {key(): dict(open_clicks=1, dialog_open=True)}, tmp_path)
    phase = {'value': 'ur_cap'}
    helper._ur_cap_warning.side_effect = lambda: phase['value'] == 'ur_cap'
    helper._count_dialog.side_effect = lambda: phase['value'] == 'count'
    helper._on_pool.side_effect = lambda pool: phase['value'] == 'pool'
    def click(button):
        audit = load_fallback_state('alas', tmp_path)[key()]
        if button == UR_CAP_CONFIRM:
            assert audit['warning_ack_clicks'] == 1 and audit['orders_submitted'] == 0
            phase['value'] = 'count'
        else:
            assert button == COUNT_CANCEL and audit['count_captured'] and audit['cancel_clicks'] == 1
            phase['value'] = 'pool'
    helper.device.click.side_effect = click
    result = helper.run(folder=tmp_path)
    assert result['returned_main'] and result['count_captured']
    assert result['orders_submitted'] == 0 and result['warning_ack_clicks'] == 1
    helper._enter_pool.assert_not_called()
    assert [call.args[0] for call in helper.device.click.call_args_list] == [UR_CAP_CONFIRM, COUNT_CANCEL]


def test_ur_warning_acknowledgement_is_not_repeated(tmp_path, monkeypatch):
    helper = controller(monkeypatch)
    save_fallback_state('alas', {key(): dict(open_clicks=1, warning_ack_clicks=1, dialog_open=True)}, tmp_path)
    helper._ur_cap_warning.return_value = True
    result = helper.run(folder=tmp_path)
    assert not result['returned_main'] and result['dialog_open']
    helper.device.click.assert_not_called()
    fallback.ensure_event_page.assert_not_called()


def test_unrecognized_dialog_is_captured_and_left_for_review(tmp_path, monkeypatch):
    helper = controller(monkeypatch)
    helper._on_pool.side_effect = [True] + [False] * 20
    result = helper.run(folder=tmp_path)
    assert result['status'] == 'blocked' and result['dialog_open']
    assert not result['returned_main'] and 'dialog_blocked.png' in result['diagnostics']
    helper.device.click.assert_called_once_with(EVENT_BUILD_ORDERS)
    fallback.ensure_event_page.assert_not_called()


def test_count_capture_failure_does_not_authorize_cancel(tmp_path, monkeypatch):
    helper = controller(monkeypatch)
    save_fallback_state('alas', {key(): dict(open_clicks=1, dialog_open=True)}, tmp_path)
    helper._count_dialog.return_value = True
    helper._capture.return_value = None
    helper._capture.side_effect = None
    result = helper.run(folder=tmp_path)
    assert not result['count_captured'] and not result['returned_main']
    helper.device.click.assert_not_called()


def test_successful_observation_never_reopens_after_server_reset(tmp_path, monkeypatch):
    helper = controller(monkeypatch)
    save_fallback_state('alas', {key(): dict(open_clicks=1, cancel_clicks=1,
                                              count_captured=True, dialog_open=False,
                                              returned_main=True)}, tmp_path)
    helper._period.return_value = '2026-10-24T00:00:00'
    result = helper.run(folder=tmp_path)
    assert result['reused']
    helper._enter_pool.assert_not_called()
    helper.device.click.assert_not_called()
    fallback.ensure_event_page.assert_not_called()


def test_server_reset_during_reservation_never_opens_old_authorization(tmp_path, monkeypatch):
    helper = controller(monkeypatch)
    helper._period.side_effect = [PERIOD, PERIOD, '2026-10-24T00:00:00']
    result = helper.run(folder=tmp_path)
    assert result['open_clicks'] == 1 and not result['returned_main']
    helper.device.click.assert_not_called()
    assert load_fallback_state('alas', tmp_path)[key()]['open_clicks'] == 1


def test_closed_game_spends_no_observation_budget(tmp_path, monkeypatch):
    helper = controller(monkeypatch)
    fallback.ensure_event_running.side_effect = GameNotRunningError
    with pytest.raises(GameNotRunningError):
        helper.run(folder=tmp_path)
    assert load_fallback_state('alas', tmp_path) == {}


@pytest.mark.parametrize('previous', [dict(open_clicks=True), dict(cancel_clicks=2),
                                     dict(dialog_open=1), dict(diagnostics='x'),
                                     dict(warning_ack_clicks=True), dict(warning_ack_clicks=1)])
def test_invalid_observation_journal_fails_before_navigation(tmp_path, monkeypatch, previous):
    helper = controller(monkeypatch)
    save_fallback_state('alas', {key(): previous}, tmp_path)
    with pytest.raises(ScriptError):
        helper.run(folder=tmp_path)
    helper._enter_pool.assert_not_called()
    helper.device.click.assert_not_called()


def test_invalid_pool_and_account_cannot_create_a_journal(tmp_path, monkeypatch):
    helper = controller(monkeypatch)
    with pytest.raises(ScriptError):
        helper.run(pool='event', folder=tmp_path)
    with pytest.raises(ScriptError):
        fallback_state_path('../alas', tmp_path)
