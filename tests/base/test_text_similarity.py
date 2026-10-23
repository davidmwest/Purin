import os
from types import SimpleNamespace
from unittest.mock import Mock

import cv2
import numpy as np
import pytest

import module.config.server as server
from module.handler.info_handler import InfoHandler
from module.ui.assets import SUPPLY_PACK_CHECK
from module.ui_white.assets import POPUP_CANCEL_WHITE, POPUP_CONFIRM_WHITE

DATA = os.path.join(os.path.dirname(__file__), 'data')


@pytest.fixture(autouse=True)
def en():
    previous = server.server
    server.set_server('en')
    yield
    server.set_server(previous)


def screen(file, left, top):
    """
    A screenshot of the F2P account, its emulator at 2560x1440 scaled down to 1280x720, black but for a part of it.
    """
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    part = cv2.cvtColor(cv2.imread(os.path.join(DATA, file)), cv2.COLOR_BGR2RGB)
    height, width = part.shape[:2]
    image[top:top + height, left:left + width] = part
    return image


def make_handler(image, scale=2.0):
    handler = InfoHandler.__new__(InfoHandler)
    handler.device = SimpleNamespace(image=image, click=Mock(), stuck_record_add=Mock(), display_scale=scale)
    handler.interval_timer = {}
    return handler


def test_native_screen_keeps_the_usual_similarity():
    assert make_handler(None, scale=1.0).text_similarity == 0.85
    assert make_handler(None, scale=2.0).text_similarity == 0.75


def test_tab_of_the_shop_scaled_down():
    # "Akashi Recommends", where the supply pack is, scores 0.78
    handler = make_handler(screen('shop_menu_scaled.png', 0, 60))

    assert handler.appear(SUPPLY_PACK_CHECK, offset=(20, 20)) is False
    assert handler.appear(SUPPLY_PACK_CHECK, offset=(20, 20), similarity=handler.text_similarity) is True


def test_white_popup_scaled_down_is_confirmed():
    # "No mail found meeting these conditions.", "Confirm" scores 0.80
    handler = make_handler(screen('popup_no_mail_scaled.png', 335, 150))

    assert POPUP_CONFIRM_WHITE.match(handler.device.image, offset=(3, 30)) is False
    assert handler.handle_popup_confirm('MAIL_CLAIM') is True
    assert handler.device.click.call_args[0][0] is POPUP_CONFIRM_WHITE


def test_white_popup_needs_the_usual_similarity_on_a_native_screen():
    handler = make_handler(screen('popup_no_mail_scaled.png', 335, 150), scale=1.0)

    assert handler.handle_popup_confirm('MAIL_CLAIM') is False


def test_white_popup_scaled_down_is_cancelled():
    # "Cancel" scores 0.81, the default 0.85 left the popup open
    handler = make_handler(screen('popup_no_mail_scaled.png', 335, 150))

    assert POPUP_CANCEL_WHITE.match(handler.device.image, offset=(3, 30)) is False
    assert handler.handle_popup_cancel('DOCK_EXPANSION') is True
    assert handler.device.click.call_args[0][0] is POPUP_CANCEL_WHITE


def test_white_popup_cancel_needs_the_usual_similarity_on_a_native_screen():
    handler = make_handler(screen('popup_no_mail_scaled.png', 335, 150), scale=1.0)

    assert handler.handle_popup_cancel('DOCK_EXPANSION') is False


def test_no_popup():
    handler = make_handler(np.zeros((720, 1280, 3), dtype=np.uint8))

    assert handler.handle_popup_confirm('MAIL_CLAIM') is False
    assert handler.handle_popup_cancel('MAIL_CLAIM') is False
    handler.device.click.assert_not_called()
