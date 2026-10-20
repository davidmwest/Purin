import os
from types import SimpleNamespace
from unittest.mock import Mock

import cv2
import numpy as np
import pytest

import module.config.server as server
from module.combat.assets import DEFEAT_TIPS
from module.combat.combat import Combat

DATA = os.path.join(os.path.dirname(__file__), 'data')


@pytest.fixture(autouse=True)
def en():
    previous = server.server
    server.set_server('en')
    yield
    server.set_server(previous)


def lost_battle():
    """
    EXP info of a battle lost, "ANNIHILATED D", of the EN client at 1280x720.
    Only the rank letter is kept, the rest is black.
    """
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    crop = cv2.cvtColor(cv2.imread(os.path.join(DATA, 'exp_info_d.png')), cv2.COLOR_BGR2RGB)
    image[30:140, 300:420] = crop
    return image


def make_combat(image):
    combat = object.__new__(Combat)
    combat.device = SimpleNamespace(image=image, click=Mock(), sleep=Mock(), stuck_record_add=Mock())
    combat.interval_timer = {}
    combat.is_combat_executing = lambda: False
    return combat


def test_exp_info_of_a_battle_lost_is_closed():
    # It used to wait there forever
    combat = make_combat(lost_battle())

    assert combat.handle_exp_info() is True

    assert combat.device.click.call_args[0][0].name == 'EXP_INFO_D'


def test_nothing_to_close_without_exp_info():
    combat = make_combat(np.zeros((720, 1280, 3), dtype=np.uint8))

    assert combat.handle_exp_info() is False
    combat.device.click.assert_not_called()


def defeat_tips():
    """
    "Improve your fleet in the following ways", shown after the EXP info of a battle lost.
    Only its banner is kept, the rest is black.
    """
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    image[128:200, 385:892] = cv2.cvtColor(cv2.imread(os.path.join(DATA, 'defeat_tips.png')), cv2.COLOR_BGR2RGB)
    return image


def test_defeat_tips_are_closed_with_their_close_button():
    combat = make_combat(defeat_tips())

    assert combat.appear_then_click(DEFEAT_TIPS, offset=(20, 20), interval=2) is True

    button = combat.device.click.call_args[0][0]
    x1, y1, x2, y2 = button.button
    # "Close" in the middle at the bottom
    assert 500 < x1 < x2 < 760 and 580 < y1 < y2 < 650


def test_gray_screen_coming_in_is_not_a_battle_lost():
    # A screen coming in, like the one of a new ship, may have the gray of the D where it is, without the D
    image = np.full((720, 1280, 3), (199, 208, 199), dtype=np.uint8)
    combat = make_combat(image)

    assert combat.handle_exp_info() is False

    combat.device.click.assert_not_called()
    assert combat.combat_lost is False


def test_battle_won_is_not_lost_on_a_fading_frame():
    # Fading out of S, a frame took the gray of D, and a run won was left as lost
    combat = make_combat(lost_battle())
    combat.battle_status_click_interval = 0
    combat.combat_won = True
    lost = ['BATTLE_STATUS_C', 'BATTLE_STATUS_D', 'EXP_INFO_C', 'EXP_INFO_D']
    combat.appear = Mock(side_effect=lambda button, **kwargs: button.name in lost)

    assert combat.handle_battle_status() is False
    assert combat.handle_exp_info() is False
    assert combat.combat_lost is False
    combat.device.click.assert_not_called()


def test_battle_status_s_marks_a_win():
    combat = make_combat(np.zeros((720, 1280, 3), dtype=np.uint8))
    combat.battle_status_click_interval = 0
    combat.appear = Mock(side_effect=lambda button, **kwargs: button.name == 'BATTLE_STATUS_S')

    assert combat.handle_battle_status() is True

    assert combat.combat_won is True
    assert combat.combat_lost is False
