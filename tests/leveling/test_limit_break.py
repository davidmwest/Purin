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


def result_screen():
    """
    Long Island limit broken, 2 stars to 3: the "LIMIT BREAK" result, black but for its title
    """
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    image[100:280, 640:1110] = load_image(os.path.join(DATA, 'limit_break_result.png'))
    return image


def test_the_result_of_a_limit_break():
    from module.leveling.limit_break import RESULT, RESULT_AREA
    assert template_score(result_screen(), load_image(RESULT), RESULT_AREA) > SIMILARITY
    assert template_score(screen('limit_break_tab.png'), load_image(RESULT), RESULT_AREA) < SIMILARITY
    # The result is not the tab, its slot empty or not
    assert not is_tab(result_screen())


def test_the_ship_of_the_fleet_is_limit_broken_not_a_higher_copy():
    # Code review (Codex, 2026-10-21): Cooper 70 in fleet 1, an other Cooper at 90 in fleet 2. The highest card of
    # her name was opened and limit broken, the one of the fleet stayed capped.
    from types import SimpleNamespace
    from unittest.mock import Mock
    from module.leveling.limit_break import LimitBreak
    from module.leveling.plan import Slot
    slot = Slot(1, 'vanguard', 0, 'Cooper', 70)
    fleet_ship = SimpleNamespace(level=70, fleet=1, button='active_fleet_copy_70')
    other_ship = SimpleNamespace(level=90, fleet=2, button='different_copy_90')
    controller = SimpleNamespace(
        config=SimpleNamespace(task_delay=Mock()),
        device=SimpleNamespace(sleep=Mock(), screenshot=Mock()),
        ui_ensure=Mock(), formation_fleets=Mock(return_value=[1]), formation_goto_fleet=Mock(),
        fleet_read_slots=Mock(return_value=[slot]),
        fleet_formation_ensure=Mock(), limit_break_cards=Mock(return_value=[other_ship, fleet_ship]),
        fleet_ship_enter=Mock(), ship_info_enter=Mock(), ship_side_navbar_ensure=Mock(),
        limit_break_name_fits=Mock(return_value=True), is_limit_break_tab=Mock(return_value=True),
        limit_break_once=Mock(side_effect=[True, False]), handle_popup_cancel=Mock(return_value=False), ui_back=Mock(),
    )
    controller.limit_break_ship = lambda *args: LimitBreak.limit_break_ship(controller, *args)

    LimitBreak.run(controller)

    controller.fleet_ship_enter.assert_called_once_with(slot)
    controller.ship_info_enter.assert_not_called()
    assert controller.limit_break_once.call_count == 1


def test_an_other_ship_on_the_detail_page_is_not_limit_broken():
    from types import SimpleNamespace
    from unittest.mock import Mock, patch
    from module.leveling.limit_break import LimitBreak
    from module.leveling.plan import ShipMatcher, load_ship_data
    matcher = ShipMatcher(load_ship_data())
    controller = SimpleNamespace(device=SimpleNamespace(image=np.zeros((720, 1280, 3), dtype=np.uint8)))
    with patch('module.leveling.limit_break.Ocr') as ocr:
        ocr.return_value.ocr.return_value = 'Hammann'
        assert LimitBreak.limit_break_name_fits(controller, 'Cooper', matcher) is False
        ocr.return_value.ocr.return_value = 'Cooper'
        assert LimitBreak.limit_break_name_fits(controller, 'Cooper', matcher) is True
        # Not read, the ship of the slot is trusted
        ocr.return_value.ocr.return_value = ''
        assert LimitBreak.limit_break_name_fits(controller, 'Cooper', matcher) is True
