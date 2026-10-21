import os

import cv2
import numpy as np

from module.gacha.gacha_reward import SHIP_FOUND_AREA, ship_found_appear

DATA = os.path.join(os.path.dirname(__file__), 'data')


def screen():
    """
    The results of 10 build orders of the F2P account, 2560x1440 brought to 1280x720, black but for the title area
    """
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    part = cv2.cvtColor(cv2.imread(os.path.join(DATA, 'ship_found_title.png')), cv2.COLOR_BGR2RGB)
    x1, y1, x2, y2 = SHIP_FOUND_AREA
    image[y1:y2, x1:x2] = part
    return image


def test_results_of_many_orders():
    assert ship_found_appear(screen()) is True


def test_no_results():
    assert ship_found_appear(np.zeros((720, 1280, 3), dtype=np.uint8)) is False
