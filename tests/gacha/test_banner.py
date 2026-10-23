from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest
from PIL import Image

from module.exception import GameNotRunningError, ScriptError
from module.gacha import banner
from module.gacha.banner import (EventBannerObserver, load_banner_state,
                                  save_banner_state, banner_state_path)
from module.gacha.banner_assets import (EVENT_BANNER_DURATION, EVENT_BANNER_TEXT, EVENT_RATE_INFO,
                                      EVENT_BUILD_ORDERS)
from module.ui.page import page_build, page_main


def controller():
    helper = EventBannerObserver.__new__(EventBannerObserver)
    helper.config = SimpleNamespace(config_name='alas')
    helper.device = SimpleNamespace(app_is_running=Mock(return_value=True), click=Mock())
    helper.ui_page_appear = Mock(return_value=True)
    helper._gacha_side_navbar = SimpleNamespace(get_active=Mock(return_value=0), set=Mock(return_value=True))
    helper._construct_bottom_navbar = SimpleNamespace(
        get_info=Mock(return_value=(0, 0, 3)), set=Mock(return_value=True))
    helper.match_template_color = Mock(return_value=True)
    return helper


def captured_controller():
    helper = controller()
    helper.device.image = np.array(Image.open('tests/gacha/data/event_pool_20261023.png').convert('RGB'))
    helper.match_template_color = lambda asset, **kw: asset.match_template_color(helper.device.image, **kw)
    return helper


def test_actual_current_banner_and_info_are_recognized():
    helper = captured_controller()
    assert helper.on_event_pool()
    assert EVENT_RATE_INFO.button[2] < 930  # The UR Exchange info/control is >1100.


@pytest.mark.parametrize('asset', [EVENT_BANNER_DURATION, EVENT_BANNER_TEXT, EVENT_RATE_INFO,
                                  EVENT_BUILD_ORDERS])
def test_missing_current_banner_or_control_fails_closed(asset):
    helper = captured_controller()
    x1, y1, x2, y2 = asset.area
    helper.device.image[y1 - 5:y2 + 5, x1 - 5:x2 + 5] = 0
    assert helper.on_event_pool() is False
    helper.device.click.assert_not_called()


@pytest.mark.parametrize('nav', [(1, 0, 3), (0, 0, 2), (None, 0, 3)])
def test_light_missing_event_and_unknown_navbar_fail_closed(nav):
    helper = controller()
    helper._construct_bottom_navbar.get_info.return_value = nav
    assert helper.on_event_pool() is False


def test_missing_event_tab_never_selects_light(monkeypatch):
    helper = controller()
    monkeypatch.setattr(banner, 'ensure_event_page', Mock(return_value=True))
    helper._wait = Mock(return_value=True)
    helper._construct_bottom_navbar.get_info.return_value = (0, 0, 2)
    assert helper.enter_event_pool() is False
    helper._construct_bottom_navbar.set.assert_not_called()
    helper.device.click.assert_not_called()


def test_known_event_navigation_requires_final_exact_banner(monkeypatch):
    helper = controller()
    monkeypatch.setattr(banner, 'ensure_event_page', Mock(return_value=True))
    helper._wait = Mock(side_effect=[True, False])
    assert helper.enter_event_pool() is False
    helper._construct_bottom_navbar.set.assert_called_once_with(helper, left=1)
    assert helper._wait.call_args_list[-1].args[0] == helper.on_event_pool


def test_durable_once_only_info_capture_never_submits_and_unknown_exit_is_untouched(tmp_path):
    helper = controller()
    helper._return_main = Mock(return_value=False)
    helper._capture = Mock(return_value='capture.png')
    helper._wait = Mock(return_value=True)

    def enter():
        assert next(iter(load_banner_state('alas', tmp_path).values()))['info_clicks'] == 0
        return True

    helper.enter_event_pool = Mock(side_effect=enter)

    def click(button):
        saved = next(iter(load_banner_state('alas', tmp_path).values()))
        assert saved['info_clicks'] == 1
        assert button is EVENT_RATE_INFO

    helper.device.click.side_effect = click
    result = helper.run(folder=tmp_path)
    assert result['status'] == 'observed' and result['returned_main'] is False
    assert result['orders_submitted'] == 0 and result['banner_ownership_verified'] is False
    assert result['preferred_pool'] == 'event'
    helper.device.click.assert_called_once_with(EVENT_RATE_INFO)
    helper.enter_event_pool.reset_mock()
    again = helper.run(folder=tmp_path)
    assert again['reused'] is True
    helper.enter_event_pool.assert_not_called()
    assert helper.device.click.call_count == 1


def test_closed_game_does_not_spend_budget(tmp_path):
    helper = controller()
    helper.device.app_is_running.return_value = False
    with pytest.raises(GameNotRunningError):
        helper.run(folder=tmp_path)
    assert load_banner_state('alas', tmp_path) == {}


def test_interrupted_observation_never_repeats_info_click(tmp_path):
    helper = controller()
    key = f'{banner.CURRENT_EVENT}:{banner.BANNER_VERSION}'
    save_banner_state('alas', {key: {'status': 'observing', 'info_clicks': 1}}, tmp_path)
    helper.enter_event_pool = Mock()
    result = helper.run(folder=tmp_path)
    assert result['status'] == 'blocked'
    helper.enter_event_pool.assert_not_called()
    helper.device.click.assert_not_called()


def test_unrecognized_pool_cannot_click_info(tmp_path):
    helper = controller()
    helper.enter_event_pool = Mock(return_value=False)
    helper._capture = Mock(return_value='blocked.png')
    helper._return_main = Mock(return_value=True)
    result = helper.run(folder=tmp_path)
    assert result['status'] == 'blocked' and result['info_clicks'] == 0
    helper.device.click.assert_not_called()


def test_unknown_rate_info_panel_has_no_guessed_exit(monkeypatch):
    helper = controller()
    helper.ui_page_appear.return_value = False
    helper.on_event_pool = Mock(return_value=False)
    monkeypatch.setattr(banner, 'ensure_event_page', Mock())
    assert helper._return_main() is False
    banner.ensure_event_page.assert_not_called()
    helper.device.click.assert_not_called()


def test_known_pool_uses_bounded_home_route(monkeypatch):
    helper = controller()
    helper.ui_page_appear.return_value = False
    helper.on_event_pool = Mock(return_value=True)
    monkeypatch.setattr(banner, 'ensure_event_page', Mock(return_value=True))
    assert helper._return_main() is True
    banner.ensure_event_page.assert_called_once_with(helper, page_main)


def test_info_failure_preserves_budget_and_attempts_only_known_return(tmp_path):
    helper = controller()
    helper.enter_event_pool = Mock(return_value=True)
    helper._capture = Mock(return_value='before.png')
    helper.device.click.side_effect = RuntimeError('interrupted input')
    helper._return_main = Mock(return_value=False)
    with pytest.raises(RuntimeError):
        helper.run(folder=tmp_path)
    saved = next(iter(load_banner_state('alas', tmp_path).values()))
    assert saved['info_clicks'] == 1 and saved['status'] == 'blocked'


@pytest.mark.parametrize('contents', ['not JSON', '[]', '{"event":1}'])
def test_corrupt_budget_fails_closed(tmp_path, contents):
    with open(banner_state_path('alas', tmp_path), 'w') as stream:
        stream.write(contents)
    with pytest.raises(ScriptError):
        load_banner_state('alas', tmp_path)


def test_invalid_account_cannot_escape_state_directory(tmp_path):
    with pytest.raises(ScriptError):
        banner_state_path('../other', tmp_path)


def test_wait_needs_two_fresh_settled_known_frames(monkeypatch):
    helper = controller()
    timer = Mock()
    timer.start.return_value = timer
    timer.reached.return_value = True
    monkeypatch.setattr(banner, 'Timer', Mock(return_value=timer))
    helper.loop = Mock(return_value=iter(range(4)))
    predicate = Mock(side_effect=[True, False, True, True])
    assert helper._wait(predicate) is True
    assert predicate.call_count == 4
    timer.reset.assert_called_once()
    helper.loop.assert_called_once_with(skip_first=False, timeout=8)
