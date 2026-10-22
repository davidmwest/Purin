from types import SimpleNamespace
from unittest.mock import Mock

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
