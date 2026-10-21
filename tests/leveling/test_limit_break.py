import os

import numpy as np

from module.base.utils import load_image
from module.leveling.limit_break import (
    QUICK_SELECT, QUICK_SELECT_AREA, SIMILARITY, SLOT_AREA, SLOT_EMPTY, confirm_orange, template_score)

DATA = os.path.join(os.path.dirname(__file__), 'data')


def screen(file):
    """
    Long Island on the F2P account, 2560x1440 brought to 1280x720, black but for the lower right:
    the material slot, Quick Select and Confirm of the limit break tab
    """
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    image[480:720, 780:1280] = load_image(os.path.join(DATA, file))
    return image


def is_tab(image):
    return template_score(image, load_image(QUICK_SELECT), QUICK_SELECT_AREA) > SIMILARITY


def is_empty(image):
    return template_score(image, load_image(SLOT_EMPTY), SLOT_AREA) > SIMILARITY


def test_limit_break_tab_with_its_slot_empty():
    image = screen('limit_break_tab.png')
    assert is_tab(image)
    assert is_empty(image)
    assert confirm_orange(image)


def test_quick_select_put_a_copy_in_the_slot():
    # Her copy built that day, Lv. 1
    image = screen('limit_break_selected.png')
    assert is_tab(image)
    assert not is_empty(image)


def test_the_enhance_tab_is_not_the_limit_break_tab():
    image = screen('limit_break_enhance_tab.png')
    assert not is_tab(image)


def test_confirm_not_lit_on_an_empty_screen():
    assert not confirm_orange(np.zeros((720, 1280, 3), dtype=np.uint8))
