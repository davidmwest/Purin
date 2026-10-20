import os
from types import SimpleNamespace
from unittest.mock import Mock

import cv2
import numpy as np
import pytest

import module.config.server as server
from module.handler.info_handler import InfoHandler
from module.ui_white.assets import POPUP_CONFIRM_WHITE

DATA = os.path.join(os.path.dirname(__file__), 'data')


@pytest.fixture(autouse=True)
def en():
    previous = server.server
    server.set_server('en')
    yield
    server.set_server(previous)


def make_handler(image):
    handler = InfoHandler.__new__(InfoHandler)
    handler.device = SimpleNamespace(image=image, click=Mock(), stuck_record_add=Mock())
    handler.interval_timer = {}
    return handler


def popup():
    # "No mail found meeting these conditions." on the F2P account, 2560x1440 scaled down to 1280x720
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    part = cv2.cvtColor(cv2.imread(os.path.join(DATA, 'popup_no_mail_scaled.png')), cv2.COLOR_BGR2RGB)
    height, width = part.shape[:2]
    image[150:150 + height, 335:335 + width] = part
    return image


def test_white_popup_scaled_down_is_confirmed():
    handler = make_handler(popup())

    assert POPUP_CONFIRM_WHITE.match(handler.device.image, offset=(3, 30)) is False
    assert handler.handle_popup_confirm('MAIL_CLAIM') is True
    assert handler.device.click.call_args[0][0] is POPUP_CONFIRM_WHITE


def test_no_popup():
    handler = make_handler(np.zeros((720, 1280, 3), dtype=np.uint8))

    assert handler.handle_popup_confirm('MAIL_CLAIM') is False
    handler.device.click.assert_not_called()
