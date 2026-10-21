import os

import cv2
import numpy as np

from module.leveling.fleet import input_ok, is_input_shown

DATA = os.path.join(os.path.dirname(__file__), 'data')


def f2p_screen():
    """
    The dock of the F2P account with the search field tapped, 2560x1440 brought to 1280x720:
    the input bar of Android is the bottom 60 pixels, OK at about (1240, 688)
    """
    image = np.full((720, 1280, 3), 40, dtype=np.uint8)
    image[600:720] = cv2.cvtColor(cv2.imread(os.path.join(DATA, 'dock_search_input_bar_f2p.png')), cv2.COLOR_BGR2RGB)
    return image


def main_screen():
    """
    The main account at 1280x720: the bar covers y 600 to 720, OK at about (1200, 657)
    """
    image = np.full((720, 1280, 3), 40, dtype=np.uint8)
    image[600:720] = 255
    image[648:666, 1185:1215] = 30
    return image


def test_input_bar_of_the_f2p_account():
    image = f2p_screen()
    assert is_input_shown(image)
    x1, y1, x2, y2 = input_ok(image).area
    assert 1220 < (x1 + x2) / 2 < 1260 and 678 < (y1 + y2) / 2 < 698


def test_input_bar_of_the_main_account():
    image = main_screen()
    assert is_input_shown(image)
    x1, y1, x2, y2 = input_ok(image).area
    assert 1190 < (x1 + x2) / 2 < 1210 and 650 < (y1 + y2) / 2 < 665


def test_no_input_bar():
    assert not is_input_shown(np.full((720, 1280, 3), 40, dtype=np.uint8))
