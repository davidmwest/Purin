import os
from collections import Counter
from types import SimpleNamespace
from unittest.mock import Mock

import cv2
import pytest

import module.config.server as server
import module.retire.setting as setting_module
from module.exception import ScriptError
from module.retire.assets import RETIRE_SETTING_1, RETIRE_SETTING_2, RETIRE_SETTING_3, RETIRE_SETTING_4
from module.retire.setting import QuickRetireSettingHandler


@pytest.fixture(autouse=True)
def en():
    previous = server.server
    server.set_server('en')
    yield
    server.set_server(previous)


def settings_popup():
    handler = object.__new__(QuickRetireSettingHandler)
    path = os.path.join(os.path.dirname(__file__), 'data', 'retire_settings_f2p.png')
    handler.device = SimpleNamespace(image=cv2.cvtColor(cv2.imread(path), cv2.COLOR_BGR2RGB))
    return handler


def immediate_timers(monkeypatch):
    monkeypatch.setattr(setting_module, 'Timer', lambda *args, **kwargs: SimpleNamespace(
        reached=lambda: True, reset=lambda: None))


def test_scaled_1440p_none_is_not_misread_as_three_selected_rarity_priorities():
    handler = settings_popup()
    # The old EN asset areas point at None. All three are selected in this captured failure screen.
    assert all(handler.image_color_count(button, color=(255, 255, 255), threshold=30, count=50)
               for button in (RETIRE_SETTING_1, RETIRE_SETTING_2, RETIRE_SETTING_3))

    setting = handler.retire_setting

    assert {key: setting.is_option_active(button) for key, button in setting.settings.items()} == {
        ('filter_1', 'R'): False, ('filter_2', 'E'): False, ('filter_3', 'N'): False,
        ('filter_4', 'all'): True, ('filter_5', 'keep_limit_break'): True, ('filter_5', 'all'): False,
    }
    assert setting.get_buttons_to_click(setting._product_setting_status(filter_5='keep_limit_break')) == [
        setting.settings[('filter_1', 'R')], setting.settings[('filter_2', 'E')], setting.settings[('filter_3', 'N')],
    ]


@pytest.mark.parametrize('server_name', ['cn', 'jp', 'tw'])
def test_priority_button_correction_preserves_other_server_coordinates(server_name):
    server.set_server(server_name)
    setting = settings_popup().retire_setting

    assert [setting.settings[key].area for key in [('filter_1', 'R'), ('filter_2', 'E'), ('filter_3', 'N')]] == [
        button.raw_area[server_name] for button in (RETIRE_SETTING_1, RETIRE_SETTING_2, RETIRE_SETTING_3)]


@pytest.mark.parametrize('filter_5, expected_clicks', [(None, 3), ('keep_limit_break', 3), ('all', 4)])
def test_setter_targets_correct_diamonds_and_verifies_changed_pixels(monkeypatch, filter_5, expected_clicks):
    immediate_timers(monkeypatch)
    handler = settings_popup()
    setting = handler.retire_setting
    handler.loop = lambda **kwargs: iter(range(10))
    clicks = []
    # Reuse the captured selected check's center to simulate the game drawing a newly selected option.
    x1, y1, x2, y2 = RETIRE_SETTING_4.area
    x, y = (x1 + x2) // 2, (y1 + y2) // 2
    selected = handler.device.image[y - 6:y + 6, x - 6:x + 6].copy()

    def select(button):
        clicks.append(button)
        x1, y1, x2, y2 = button.area
        x, y = (x1 + x2) // 2, (y1 + y2) // 2
        handler.device.image[y - 6:y + 6, x - 6:x + 6] = selected

    handler.device.click = select

    assert setting.set(filter_5=filter_5)

    assert len(clicks) == expected_clicks
    assert [button.area for button in clicks[:3]] == [(818, 260, 847, 289), (747, 317, 775, 345),
                                                     (895, 374, 923, 402)]
    if filter_5 is None:
        assert all(button not in clicks for key, button in setting.settings.items() if key[0] == 'filter_5')


def test_setter_stops_after_bounded_retries_without_assuming_success(monkeypatch):
    immediate_timers(monkeypatch)
    handler = settings_popup()
    calls = []

    def frames(**kwargs):
        assert kwargs == {'timeout': setting_module.SETTING_WAIT}
        yield from range(20)

    handler.loop = frames
    handler.device.click = calls.append

    assert not handler.retire_setting.set(filter_5='keep_limit_break')

    assert Counter(button.name for button in calls) == {
        'RETIRE_SETTING_1': 3, 'RETIRE_SETTING_2': 3, 'RETIRE_SETTING_3': 3}


@pytest.mark.parametrize('result', [True, False, RuntimeError('read failed')])
def test_popup_is_closed_on_success_timeout_or_reader_exception(result):
    handler = object.__new__(QuickRetireSettingHandler)
    handler._retire_setting_enter = Mock()
    handler._retire_setting_quit = Mock()
    handler.retire_setting = SimpleNamespace(set=Mock(side_effect=result if isinstance(result, Exception) else None,
                                                     return_value=result))

    if isinstance(result, Exception):
        with pytest.raises(RuntimeError, match='read failed'):
            handler.quick_retire_setting_set(filter_5=None)
    elif not result:
        with pytest.raises(ScriptError, match='could not be verified'):
            handler.quick_retire_setting_set(filter_5=None)
    else:
        handler.quick_retire_setting_set(filter_5=None)

    handler._retire_setting_enter.assert_called_once_with()
    handler.retire_setting.set.assert_called_once_with(filter_5=None)
    handler._retire_setting_quit.assert_called_once_with()


def test_popup_navigation_timeout_is_bounded(monkeypatch):
    immediate_timers(monkeypatch)
    handler = object.__new__(QuickRetireSettingHandler)
    handler.loop = lambda **kwargs: iter(range(20))
    handler.appear = lambda button, **kwargs: button is setting_module.RETIRE_SETTING_QUIT
    handler.device = SimpleNamespace(click=Mock())

    with pytest.raises(ScriptError, match='Could not reach RETIRE_SETTING_ENTER'):
        handler._retire_setting_quit()

    assert handler.device.click.call_count == setting_module.SETTING_MAX_CLICKS


def test_close_does_not_accept_background_enter_icon_while_popup_is_still_open(monkeypatch):
    immediate_timers(monkeypatch)
    handler = settings_popup()
    handler.device.stuck_record_add = lambda button: None
    handler.device.click = Mock()
    handler.loop = lambda **kwargs: iter(range(20))
    # Both templates match the captured open popup; the background icon alone cannot prove it closed.
    assert handler.appear(setting_module.RETIRE_SETTING_ENTER, offset=(30, 100))
    assert handler.appear(setting_module.RETIRE_SETTING_QUIT, offset=(30, 100))

    with pytest.raises(ScriptError, match='Could not reach RETIRE_SETTING_ENTER'):
        handler._retire_setting_quit()

    assert handler.device.click.call_count == setting_module.SETTING_MAX_CLICKS


@pytest.mark.parametrize('entering', [True, False])
def test_popup_transition_verifies_arrival_after_one_click(monkeypatch, entering):
    immediate_timers(monkeypatch)
    handler = settings_popup()
    opened = handler.device.image
    closed = cv2.cvtColor(cv2.imread(setting_module.RETIRE_SETTING_ENTER.file), cv2.COLOR_BGR2RGB)
    handler.device.stuck_record_add = lambda button: None
    handler.device.click = Mock()

    def frames(**kwargs):
        for image in ([closed, opened] if entering else [opened, closed]):
            handler.device.image = image
            yield image

    handler.loop = frames

    if entering:
        handler._retire_setting_enter()
    else:
        handler._retire_setting_quit()

    handler.device.click.assert_called_once_with(
        setting_module.RETIRE_SETTING_ENTER if entering else setting_module.RETIRE_SETTING_QUIT)
