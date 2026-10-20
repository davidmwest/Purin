import os
from types import SimpleNamespace
from unittest.mock import Mock

import cv2
import numpy as np
import pytest

import module.config.server as server
from module.combat.auto_search_combat import AutoSearchCombat

DATA = os.path.join(os.path.dirname(__file__), 'data')


@pytest.fixture(autouse=True)
def en():
    previous = server.server
    server.set_server('en')
    yield
    server.set_server(previous)


def defeat_tips():
    """
    Tips to improve the fleet after the boss of 12-4 was lost in auto search, on the EN client at 1280x720
    of the main account. The part with "Improve your fleet in the following ways" is kept, the rest is black.
    """
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    image[120:200, 380:900] = cv2.cvtColor(cv2.imread(os.path.join(DATA, 'defeat_tips_auto_search.png')),
                                           cv2.COLOR_BGR2RGB)
    return image


def test_defeat_tips_are_closed_in_auto_search():
    frames = iter([defeat_tips(), np.zeros((720, 1280, 3), dtype=np.uint8)])
    device = SimpleNamespace(image=None, click=Mock(), stuck_record_clear=Mock(), click_record_clear=Mock(),
                             stuck_record_add=Mock())

    def screenshot():
        device.image = next(frames)
        return device.image

    device.screenshot = screenshot
    combat = object.__new__(AutoSearchCombat)
    combat.device = device
    combat.interval_timer = {}
    combat._auto_search_status_confirm = False
    # Running again once the tips are closed
    combat.is_auto_search_running = Mock(side_effect=lambda: device.click.called)
    combat.is_in_auto_search_menu = Mock(return_value=False)
    combat._handle_auto_search_menu_missing = Mock(return_value=False)
    for name in ['handle_get_ship', 'handle_auto_search_map_option', 'handle_popup_confirm',
                 'handle_urgent_commission', 'handle_story_skip', 'handle_guild_popup_cancel', 'handle_vote_popup',
                 'handle_mission_popup_ack']:
        setattr(combat, name, Mock(return_value=False))
    device.screenshot()

    combat.auto_search_combat_status()

    button = device.click.call_args[0][0]
    assert button.name == 'DEFEAT_TIPS'
    x1, y1, x2, y2 = button.button
    # "Close" in the middle at the bottom
    assert 500 < x1 < x2 < 760 and 580 < y1 < y2 < 650
