import json
from types import SimpleNamespace
from unittest.mock import Mock, call

import numpy as np
import pytest

import module.retire.retirement as retirement_module
from module.exception import RequestHumanTakeover
from module.retire.assets import IN_RETIREMENT_CHECK, RETIRE_APPEAR_1, RETIRE_SETTING_QUIT, SHIP_CONFIRM_2
from module.retire.retirement import Retirement
from module.retire.scheduled import RetireShips


@pytest.mark.parametrize('mode', ['one_click_retire', 'old_retire', 'enhance'])
def test_dry_run_blocks_central_confirmation_before_any_device_or_popup_action(mode):
    retirement = SimpleNamespace(
        config=SimpleNamespace(Retirement_DryRun=True, Retirement_RetireMode=mode),
        device=SimpleNamespace(click=Mock(), screenshot=Mock()),
        interval_clear=Mock(), popup_interval_clear=Mock(), handle_popup_confirm=Mock(),
    )

    with pytest.raises(RequestHumanTakeover, match='blocks every retirement confirmation'):
        Retirement._retirement_confirm(retirement)

    retirement.device.click.assert_not_called()
    retirement.device.screenshot.assert_not_called()
    retirement.interval_clear.assert_not_called()
    retirement.popup_interval_clear.assert_not_called()
    retirement.handle_popup_confirm.assert_not_called()


@pytest.mark.parametrize('mode', ['one_click_retire', 'old_retire', 'enhance'])
@pytest.mark.parametrize('enhance_failed', [False, True])
def test_dry_run_blocks_full_dock_and_enhancement_fallback_before_selection(mode, enhance_failed):
    retirement = SimpleNamespace(
        config=SimpleNamespace(Retirement_DryRun=True, Retirement_RetireMode=mode),
        handle_game_tips=Mock(return_value=False),
        appear=Mock(side_effect=lambda button, **kwargs: button is RETIRE_APPEAR_1),
        appear_then_click=Mock(), _unable_to_enhance=enhance_failed,
        _retire_handler=Mock(), _enhance_handler=Mock(),
    )

    with pytest.raises(RequestHumanTakeover):
        Retirement.handle_retirement(retirement)

    retirement.appear_then_click.assert_not_called()
    retirement._retire_handler.assert_not_called()
    retirement._enhance_handler.assert_not_called()


def test_dry_run_does_not_interrupt_an_unrelated_game_screen():
    retirement = SimpleNamespace(
        config=SimpleNamespace(Retirement_DryRun=True, Retirement_RetireMode='one_click_retire'),
        handle_game_tips=Mock(return_value=False), appear=Mock(return_value=False),
        appear_then_click=Mock(), _retire_handler=Mock(),
    )

    assert Retirement.handle_retirement(retirement) is False

    retirement.appear_then_click.assert_not_called()
    retirement._retire_handler.assert_not_called()


@pytest.mark.parametrize('mode', ['one_click_retire', 'old_retire', 'enhance'])
def test_scheduled_dry_run_sets_preserve_option_then_previews_without_old_retirement(mode):
    task = object.__new__(RetireShips)
    task.config = SimpleNamespace(Retirement_DryRun=True, Retirement_RetireMode=mode, task_delay=Mock())
    task.appear = Mock(return_value=False)
    events = Mock()
    for name in ('ui_ensure', 'retire_page_enter', 'handle_dock_cards_loading', 'quick_retire_setting_set',
                 'retire_ships_one_click', 'retire_ships_old', '_retirement_quit', '_retirement_confirm'):
        method = Mock()
        setattr(task, name, method)
        events.attach_mock(method, name)

    task.run()

    assert events.mock_calls[1:] == [
        call.retire_page_enter(), call.handle_dock_cards_loading(),
        call.quick_retire_setting_set(filter_5='keep_limit_break'),
        call.retire_ships_one_click(), call._retirement_quit(),
    ]
    task.retire_ships_old.assert_not_called()
    task._retirement_confirm.assert_not_called()
    task.config.task_delay.assert_called_once_with(server_update=True)


def test_scheduled_do_not_retire_takes_precedence_over_preview():
    task = object.__new__(RetireShips)
    task.config = SimpleNamespace(Retirement_DryRun=True, Retirement_RetireMode='do_not_retire', task_delay=Mock())
    task.ui_ensure = Mock()
    task.quick_retire_setting_set = Mock()
    task.retire_ships_one_click = Mock()

    task.run()

    task.ui_ensure.assert_not_called()
    task.quick_retire_setting_set.assert_not_called()
    task.retire_ships_one_click.assert_not_called()


@pytest.mark.parametrize('settings_open', [False, True])
def test_scheduled_preview_recovers_existing_full_dock_without_unrelated_navigation(settings_open):
    task = object.__new__(RetireShips)
    task.config = SimpleNamespace(Retirement_DryRun=True, Retirement_RetireMode='one_click_retire', task_delay=Mock())
    opened = {'settings': settings_open}
    events = Mock()

    def close_settings():
        opened['settings'] = False

    task.appear = Mock(side_effect=lambda button, **kwargs:
                       opened['settings'] if button is RETIRE_SETTING_QUIT else
                       not opened['settings'] if button is IN_RETIREMENT_CHECK else False)
    for name in ('ui_ensure', 'retire_page_enter', 'handle_dock_cards_loading', 'quick_retire_setting_set',
                 'retire_ships_one_click', 'retire_ships_old', '_retirement_quit', '_retirement_confirm'):
        method = Mock()
        setattr(task, name, method)
        events.attach_mock(method, name)
    task._retire_setting_quit = Mock(side_effect=close_settings)
    events.attach_mock(task._retire_setting_quit, '_retire_setting_quit')

    task.run()

    expected = [call._retire_setting_quit()] if settings_open else []
    expected += [call.handle_dock_cards_loading(), call.quick_retire_setting_set(filter_5='keep_limit_break'),
                 call.retire_ships_one_click(), call._retirement_quit()]
    assert events.mock_calls == expected
    task.ui_ensure.assert_not_called()
    task.retire_page_enter.assert_not_called()
    task.retire_ships_old.assert_not_called()
    task._retirement_confirm.assert_not_called()
    task.config.task_delay.assert_called_once_with(server_update=True)


def test_one_click_dry_run_routes_selected_dialog_to_preview_and_returns_zero():
    retirement = object.__new__(Retirement)
    retirement.config = SimpleNamespace(Retirement_DryRun=True, is_task_enabled=lambda task: False)
    retirement.dock_favourite_set = Mock()
    retirement.dock_sort_method_dsc_set = Mock()
    retirement.handle_info_bar = Mock()
    retirement.appear = Mock(side_effect=lambda button, **kwargs: button is SHIP_CONFIRM_2)
    retirement._retirement_preview = Mock()
    retirement._retirement_confirm = Mock()
    retirement.device = SimpleNamespace(click=Mock(), screenshot=Mock())

    assert retirement.retire_ships_one_click() == 0

    retirement._retirement_preview.assert_called_once_with()
    retirement._retirement_confirm.assert_not_called()
    retirement.device.click.assert_not_called()


def preview(monkeypatch, tmp_path, cancel_available=True):
    monkeypatch.chdir(tmp_path)
    retirement = object.__new__(Retirement)
    retirement.config = SimpleNamespace(config_name='f2p', Retirement_DryRun=True)
    state = {'dialog': True}
    clicks = []

    def appear(button, **kwargs):
        if button is SHIP_CONFIRM_2:
            return state['dialog']
        if button is IN_RETIREMENT_CHECK:
            return not state['dialog']
        if button is retirement_module.POPUP_CANCEL:
            return state['dialog'] and cancel_available
        raise AssertionError(f'Unexpected button inspected: {button}')

    def click(button):
        # Any Confirm or unrelated navigation click fails the test immediately.
        assert button is retirement_module.POPUP_CANCEL
        clicks.append(button)
        state['dialog'] = False

    retirement.appear = appear
    retirement.device = SimpleNamespace(image=np.zeros((8, 8, 3), dtype=np.uint8),
                                        click=Mock(side_effect=click), screenshot=Mock(), swipe=Mock())
    retirement.loop = Mock(side_effect=lambda **kwargs: iter(range(5)))
    retirement.handle_popup_confirm = Mock(side_effect=AssertionError('No confirmation handler in preview'))
    retirement._retirement_confirm = Mock(side_effect=AssertionError('No retirement in preview'))
    scroll = SimpleNamespace(
        appear=Mock(return_value=False), at_bottom=Mock(return_value=False), next_page=Mock(),
        cal_position=Mock(side_effect=[index / 100 for index in range(80)]), length=100, total=500,
        position_to_screen=Mock(side_effect=lambda position, **kwargs:
                                (1092, 114 + int(400 * position), 1095, 114 + int(400 * position))),
    )
    monkeypatch.setattr(retirement_module, 'RETIRE_CONFIRM_SCROLL', scroll)
    return retirement, scroll, clicks


def saved_report(tmp_path):
    reports = list((tmp_path / 'config' / 'retire' / 'f2p').glob('*/preview.json'))
    assert len(reports) == 1
    return json.loads(reports[0].read_text()), reports[0].parent


@pytest.mark.parametrize('preexisting_toast', [False, True])
def test_no_eligible_ships_saves_evidence_only_after_one_selection_attempt(monkeypatch, tmp_path, preexisting_toast):
    retirement, scroll, clicks = preview(monkeypatch, tmp_path)
    retirement.config.is_task_enabled = lambda task: False
    retirement.dock_favourite_set = Mock()
    retirement.dock_sort_method_dsc_set = Mock()
    retirement.handle_info_bar = Mock()
    retirement.appear = Mock(side_effect=lambda button, **kwargs:
                             button is retirement_module.ONE_CLICK_RETIREMENT or button is IN_RETIREMENT_CHECK)
    retirement.info_bar_count = Mock(side_effect=[preexisting_toast, True])
    retirement.device.click = Mock()
    retirement._retirement_preview = Mock()

    assert retirement.retire_ships_one_click() == 0

    report, directory = saved_report(tmp_path)
    assert report['selection_empty'] and report['dry_run'] and report['complete'] and report['cancelled']
    assert report['pages'] == [str(directory.relative_to(tmp_path) / 'empty-selection.png')]
    assert (directory / 'empty-selection.png').is_file()
    assert retirement.device.click.call_count == 1
    assert retirement.device.click.call_args[0][0].name == 'ONE_CLICK_RETIREMENT'
    retirement._retirement_preview.assert_not_called()
    retirement._retirement_confirm.assert_not_called()
    retirement.handle_popup_confirm.assert_not_called()


def test_empty_preview_refuses_unknown_screen_without_writing_or_clicking(monkeypatch, tmp_path):
    retirement, scroll, clicks = preview(monkeypatch, tmp_path)
    retirement.appear = Mock(return_value=False)

    with pytest.raises(RequestHumanTakeover, match='Could not verify empty Quick Retire preview'):
        retirement._retirement_preview_empty()

    assert not (tmp_path / 'config').exists()
    retirement.device.click.assert_not_called()
    retirement._retirement_confirm.assert_not_called()
    retirement.handle_popup_confirm.assert_not_called()


def test_preview_saves_selected_cards_and_cancels_without_clicking_confirm(monkeypatch, tmp_path):
    retirement, scroll, clicks = preview(monkeypatch, tmp_path)

    retirement._retirement_preview()

    report, directory = saved_report(tmp_path)
    assert report['dry_run'] and report['complete'] and report['cancelled']
    assert report['pages'] == [str(directory.relative_to(tmp_path) / 'page-01.png')]
    assert (directory / 'page-01.png').is_file()
    assert clicks == [retirement_module.POPUP_CANCEL]
    retirement._retirement_confirm.assert_not_called()
    retirement.handle_popup_confirm.assert_not_called()
    retirement.loop.assert_called_once_with(timeout=12)


def test_preview_overlaps_pages_until_scroll_bottom_then_cancels(monkeypatch, tmp_path):
    retirement, scroll, clicks = preview(monkeypatch, tmp_path)
    scroll.appear.return_value = True
    scroll.at_bottom.side_effect = [False, True]

    retirement._retirement_preview()

    report, directory = saved_report(tmp_path)
    assert report['complete'] and report['cancelled']
    assert len(report['pages']) == 2
    retirement.device.swipe.assert_called_once_with((1093, 114), (1093, 179),
                                                     name='RETIRE_PREVIEW_SCROLL', distance_check=False)
    scroll.next_page.assert_not_called()
    retirement.device.screenshot.assert_called_once_with()
    assert clicks == [retirement_module.POPUP_CANCEL]


def test_preview_cancels_in_finally_when_image_capture_fails(monkeypatch, tmp_path):
    retirement, scroll, clicks = preview(monkeypatch, tmp_path)
    monkeypatch.setattr(retirement_module, 'save_image', Mock(side_effect=OSError('capture save failed')))

    with pytest.raises(OSError, match='capture save failed'):
        retirement._retirement_preview()

    report, _ = saved_report(tmp_path)
    assert report['cancelled'] and not report['complete'] and not report['pages']
    assert clicks == [retirement_module.POPUP_CANCEL]
    retirement._retirement_confirm.assert_not_called()


def test_preview_cancels_even_when_report_directory_cannot_be_created(monkeypatch, tmp_path):
    retirement, scroll, clicks = preview(monkeypatch, tmp_path)
    monkeypatch.setattr(retirement_module.Path, 'mkdir', Mock(side_effect=PermissionError('directory denied')))

    with pytest.raises(OSError):
        retirement._retirement_preview()

    assert clicks == [retirement_module.POPUP_CANCEL]
    retirement._retirement_confirm.assert_not_called()
    retirement.handle_popup_confirm.assert_not_called()


@pytest.mark.parametrize('failed_method', ['appear', 'at_bottom', 'cal_position', 'position_to_screen'])
def test_preview_cancels_in_finally_when_scroll_inspection_or_action_fails(monkeypatch, tmp_path, failed_method):
    retirement, scroll, clicks = preview(monkeypatch, tmp_path)
    scroll.appear.return_value = True
    getattr(scroll, failed_method).side_effect = RuntimeError('scroll failed')

    with pytest.raises(RuntimeError, match='scroll failed'):
        retirement._retirement_preview()

    report, _ = saved_report(tmp_path)
    assert report['cancelled'] and not report['complete'] and len(report['pages']) == 1
    assert clicks == [retirement_module.POPUP_CANCEL]
    retirement._retirement_confirm.assert_not_called()


def test_preview_cancels_in_finally_when_next_screenshot_fails(monkeypatch, tmp_path):
    retirement, scroll, clicks = preview(monkeypatch, tmp_path)
    scroll.appear.return_value = True
    retirement.device.screenshot.side_effect = RuntimeError('screenshot failed')

    with pytest.raises(RuntimeError, match='screenshot failed'):
        retirement._retirement_preview()

    report, _ = saved_report(tmp_path)
    assert report['cancelled'] and not report['complete']
    assert clicks == [retirement_module.POPUP_CANCEL]


def test_preview_cancels_in_finally_when_scroll_swipe_fails(monkeypatch, tmp_path):
    retirement, scroll, clicks = preview(monkeypatch, tmp_path)
    scroll.appear.return_value = True
    retirement.device.swipe.side_effect = RuntimeError('swipe failed')

    with pytest.raises(RuntimeError, match='swipe failed'):
        retirement._retirement_preview()

    report, _ = saved_report(tmp_path)
    assert report['cancelled'] and not report['complete']
    assert clicks == [retirement_module.POPUP_CANCEL]


@pytest.mark.parametrize('after', [0.5, 0.3])
def test_preview_stops_and_cancels_after_one_stuck_or_backward_swipe(monkeypatch, tmp_path, after):
    retirement, scroll, clicks = preview(monkeypatch, tmp_path)
    scroll.appear.return_value = True
    scroll.cal_position.side_effect = [0.5, after]

    with pytest.raises(RequestHumanTakeover, match='scrollbar did not advance'):
        retirement._retirement_preview()

    report, _ = saved_report(tmp_path)
    assert report['cancelled'] and not report['complete']
    assert len(report['pages']) == 1
    assert retirement.device.swipe.call_count == 1
    scroll.next_page.assert_not_called()
    assert clicks == [retirement_module.POPUP_CANCEL]


def test_preview_refuses_unrecognized_cancel_without_any_click(monkeypatch, tmp_path):
    retirement, scroll, clicks = preview(monkeypatch, tmp_path, cancel_available=False)

    with pytest.raises(RequestHumanTakeover, match='could not verify Cancel'):
        retirement._retirement_preview()

    report, _ = saved_report(tmp_path)
    assert report['complete'] and not report['cancelled']
    assert not clicks
    retirement.device.click.assert_not_called()
    retirement._retirement_confirm.assert_not_called()
    retirement.handle_popup_confirm.assert_not_called()


def test_preview_stops_after_bounded_pages_and_cancels_incomplete_capture(monkeypatch, tmp_path):
    retirement, scroll, clicks = preview(monkeypatch, tmp_path)
    scroll.appear.return_value = True

    with pytest.raises(RequestHumanTakeover, match='preview is incomplete'):
        retirement._retirement_preview()

    report, _ = saved_report(tmp_path)
    assert report['cancelled'] and not report['complete']
    assert len(report['pages']) == 30
    assert retirement.device.swipe.call_count == 30
    scroll.next_page.assert_not_called()
    assert clicks == [retirement_module.POPUP_CANCEL]
