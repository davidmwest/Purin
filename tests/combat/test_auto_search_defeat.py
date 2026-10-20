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
    blank = np.zeros((720, 1280, 3), dtype=np.uint8)
    frames = iter([defeat_tips(), blank, blank, blank])
    device = SimpleNamespace(image=None, click=Mock(), stuck_record_clear=Mock(), click_record_clear=Mock(),
                             stuck_record_add=Mock())
    shown = []

    def screenshot():
        device.image = next(frames)
        shown.append(device.image)
        return device.image

    device.screenshot = screenshot
    combat = object.__new__(AutoSearchCombat)
    combat.device = device
    combat.interval_timer = {}
    combat._auto_search_status_confirm = False
    # Running again two frames after the tips are closed
    combat.is_auto_search_running = Mock(side_effect=lambda: len(shown) >= 4)
    combat.is_in_auto_search_menu = Mock(return_value=False)
    combat._handle_auto_search_menu_missing = Mock(return_value=False)
    for name in ['handle_get_ship', 'handle_auto_search_map_option', 'handle_popup_confirm',
                 'handle_urgent_commission', 'handle_story_skip', 'handle_guild_popup_cancel', 'handle_vote_popup',
                 'handle_mission_popup_ack', 'handle_get_items', 'handle_battle_status', 'handle_exp_info']:
        setattr(combat, name, Mock(return_value=False))
    device.screenshot()

    combat.auto_search_combat_status()

    button = device.click.call_args[0][0]
    assert button.name == 'DEFEAT_TIPS'
    x1, y1, x2, y2 = button.button
    # "Close" in the middle at the bottom
    assert 500 < x1 < x2 < 760 and 580 < y1 < y2 < 650
    # The status C or D of the battle lost is looked for next
    assert combat.handle_battle_status.called


def test_battle_lost_ends_the_battle_in_auto_search():
    # A battle of the mobs of 12-4 lost: the battle waited for S, A or B and stayed on these tips
    combat = object.__new__(AutoSearchCombat)
    combat.device = SimpleNamespace(image=defeat_tips(), stuck_record_add=Mock())
    combat.interval_timer = {}

    assert combat.is_battle_lost_shown()

    combat.device.image = np.zeros((720, 1280, 3), dtype=np.uint8)
    assert not combat.is_battle_lost_shown()
