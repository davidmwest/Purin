import os
from types import SimpleNamespace
from unittest.mock import Mock

import cv2
import numpy as np
import pytest

import module.config.server as server
import module.handler.login as login
from module.handler.login import LoginHandler


@pytest.fixture(autouse=True)
def en():
    previous = server.server
    server.set_server('en')
    yield
    server.set_server(previous)


def logging_in(update_screens):
    """
    A login that shows a game update to confirm, then nothing it knows for `update_screens` screenshots (the download),
    then the main page.
    """
    handler = LoginHandler.__new__(LoginHandler)
    handler.device = SimpleNamespace(screenshot=Mock(), stuck_record_clear=Mock(), click_record_clear=Mock(),
                                     get_orientation=Mock(), click=Mock(), logged_in=False)
    shots = {'n': 0}

    def screenshot():
        shots['n'] += 1

    handler.device.screenshot = screenshot
    handler.is_in_main = lambda: shots['n'] > update_screens + 1
    handler.match_template_color = Mock(return_value=False)
    handler.appear = Mock(return_value=False)
    handler.appear_then_click = lambda button, **kwargs: button is login.LOGIN_GAME_UPDATE and shots['n'] == 1
    handler.handle_popup_confirm = Mock(return_value=False)
    handler.handle_urgent_commission = Mock(return_value=False)
    handler.ui_page_main_popups = Mock(return_value=False)
    return handler


def test_a_game_update_downloading_is_not_taken_for_a_stuck_game():
    # 2026-10-22, 570 MB: Purin restarted the game after 3 minutes of the download, which started it over
    handler = logging_in(update_screens=20)

    assert handler._handle_app_login() is True

    # Once at the start, then on each screenshot of the download
    assert handler.device.stuck_record_clear.call_count > 20
    assert handler.device.logged_in is True


def test_a_login_with_no_update_keeps_its_stuck_check():
    handler = logging_in(update_screens=20)
    handler.appear_then_click = Mock(return_value=False)

    assert handler._handle_app_login() is True

    handler.device.stuck_record_clear.assert_called_once_with()


def asset_selector():
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    path = os.path.join(os.path.dirname(__file__), 'data', 'asset_download.png')
    image[140:534, 300:978] = cv2.cvtColor(cv2.imread(path), cv2.COLOR_BGR2RGB)
    handler = LoginHandler.__new__(LoginHandler)
    handler.device = SimpleNamespace(image=image, click=Mock(), stuck_record_add=Mock())
    handler.interval_timer = {}
    return handler


def test_asset_selector_that_matches_maintenance_x_confirms_instead_of_exiting():
    handler = asset_selector()
    assert handler.appear(login.MAINTENANCE_ANNOUNCE, offset=(30, 30))

    assert handler.handle_asset_download() is True

    handler.device.click.assert_called_once_with(login._ASSET_DOWNLOAD_CONFIRM)
    x1, y1, x2, y2 = handler.device.click.call_args[0][0].button
    assert 699 <= x1 < x2 <= 875
    assert 447 <= y1 < y2 <= 505


def test_asset_selector_waits_safely_if_confirm_is_not_ready():
    handler = asset_selector()
    handler.device.image[440:520, 690:885] = 0

    assert handler.handle_asset_download() is True

    handler.device.click.assert_not_called()


def test_unknown_dialog_is_not_confirmed_as_asset_download():
    handler = asset_selector()
    handler.device.image[150:200, 315:665] = 0

    assert handler.handle_asset_download() is False

    handler.device.click.assert_not_called()


def test_asset_selector_takes_priority_over_generic_close_handler(monkeypatch):
    handler = logging_in(update_screens=0)
    handler.handle_asset_download = Mock(return_value=True)
    handler.appear_then_click = Mock(return_value=False)
    monkeypatch.setattr(login, 'Timer', lambda *args, **kwargs: SimpleNamespace(
        start=lambda: SimpleNamespace(reached=lambda: True, reset=lambda: None),
        reached=lambda: False, reset=lambda: None, started=lambda: False))

    assert handler._handle_app_login() is True

    assert all(call.args[0] is not login.MAINTENANCE_ANNOUNCE
               for call in handler.appear_then_click.call_args_list)
