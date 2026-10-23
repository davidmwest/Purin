import os

import numpy as np

from module.base.utils import load_image
from module.leveling.limit_break import (
    COMPLETE, COMPLETE_AREA, QUICK_SELECT, QUICK_SELECT_AREA, SIMILARITY, SLOT_AREA, SLOT_EMPTY,
    confirm_orange, template_score)

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


def test_f2p_final_limit_break_has_max_level_instead_of_quick_select():
    # Exact normalized crop from the 2026-10-22 19:34:31 F2P error. Long Island is at 5 stars;
    # the old code clicked SAFE_TAP repeatedly because Quick Select is no longer present.
    image = screen('limit_break_complete.png')
    assert not is_tab(image)
    assert not confirm_orange(image)
    assert template_score(image, load_image(COMPLETE), COMPLETE_AREA) > SIMILARITY
    for file in ('limit_break_tab.png', 'limit_break_selected.png', 'limit_break_enhance_tab.png'):
        assert template_score(screen(file), load_image(COMPLETE), COMPLETE_AREA) < SIMILARITY


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
        detail_name_fits=Mock(return_value=True), is_limit_break_tab=Mock(return_value=True),
        is_limit_break_complete=Mock(return_value=False),
        limit_break_once=Mock(side_effect=[True, False]), handle_popup_cancel_loose=Mock(return_value=False), ui_back=Mock(),
        enhance_fleet=Mock(return_value=0),
    )
    controller.limit_break_ship = lambda *args: LimitBreak.limit_break_ship(controller, *args)

    LimitBreak.run(controller)

    controller.fleet_ship_enter.assert_called_once_with(slot)
    controller.ship_info_enter.assert_not_called()
    assert controller.limit_break_once.call_count == 1


def test_an_other_ship_on_the_detail_page_is_not_limit_broken():
    from types import SimpleNamespace
    from unittest.mock import Mock, patch
    from module.leveling.enhance import FleetEnhance
    from module.leveling.plan import ShipMatcher, load_ship_data
    matcher = ShipMatcher(load_ship_data())
    controller = SimpleNamespace(device=SimpleNamespace(image=np.zeros((720, 1280, 3), dtype=np.uint8)))
    with patch('module.leveling.enhance.Ocr') as ocr:
        ocr.return_value.ocr.return_value = 'Hammann'
        assert FleetEnhance.detail_name_fits(controller, 'Cooper', matcher) is False
        ocr.return_value.ocr.return_value = 'Cooper'
        assert FleetEnhance.detail_name_fits(controller, 'Cooper', matcher) is True
        # Not read, the ship of the slot is trusted
        ocr.return_value.ocr.return_value = ''
        assert FleetEnhance.detail_name_fits(controller, 'Cooper', matcher) is True


def test_the_bulin_of_each_rarity():
    from module.leveling.limit_break import bulin_for
    from module.leveling.plan import ShipMatcher, load_ship_data
    ships = ShipMatcher(load_ship_data()).ships

    # The F2P fleet: Long Island rare, Laffey elite, Nevada common
    assert bulin_for(ships['Long Island']) == 'Universal Bulin'
    assert bulin_for(ships['Laffey']) == 'Universal Bulin'
    assert bulin_for(ships['Nevada']) == 'Universal Bulin'
    assert bulin_for(ships['Belfast']) == 'Prototype Bulin MKII'
    # No Bulin for a Bulin, a retrofit form or a ship not known
    assert bulin_for(ships['Universal Bulin']) == ''
    assert bulin_for(ships['Laffey (Retrofit)']) == ''
    assert bulin_for(None) == ''


def limit_break_controller(cards, bulins, completed=None):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from module.leveling.limit_break import LimitBreak
    from module.leveling.plan import ShipMatcher, Slot, load_ship_data
    slot = Slot(1, 'main', 0, 'Long Island', 80)
    searches = {'Long Island': cards, 'Universal Bulin': bulins}
    controller = SimpleNamespace(
        config=SimpleNamespace(task_delay=Mock()),
        device=SimpleNamespace(sleep=Mock(), screenshot=Mock()),
        ui_ensure=Mock(), formation_goto_fleet=Mock(),
        limit_break_cards=Mock(side_effect=lambda name: searches[name]),
        fleet_ship_enter=Mock(), ship_side_navbar_ensure=Mock(), fleet_formation_ensure=Mock(),
        detail_name_fits=Mock(return_value=True), is_limit_break_tab=Mock(return_value=True),
        is_limit_break_complete=Mock(side_effect=completed) if completed else Mock(return_value=False),
        limit_break_once=Mock(return_value=True), handle_popup_cancel_loose=Mock(return_value=False), ui_back=Mock(),
    )
    done = LimitBreak.limit_break_ship(controller, slot, 'Long Island', ShipMatcher(load_ship_data()))
    return controller, done


def test_a_ship_with_no_copy_is_limit_broken_with_bulins():
    # 2026-10-22: Long Island at her Lv 80 cap with no copy, 5 Universal Bulins unused in the dock
    from types import SimpleNamespace
    long_island = SimpleNamespace(level=80, fleet=1)
    bulins = [SimpleNamespace(level=1, fleet=0), SimpleNamespace(level=1, fleet=0), SimpleNamespace(level=1, fleet=2)]

    controller, done = limit_break_controller([long_island], bulins)

    # The 2 free Bulins, not the one of fleet 2
    assert done == 2
    assert controller.limit_break_once.call_count == 2


def test_no_copy_and_no_free_bulin_is_not_tried():
    from types import SimpleNamespace
    controller, done = limit_break_controller([SimpleNamespace(level=80, fleet=1)],
                                              [SimpleNamespace(level=1, fleet=2)])

    assert done == 0
    controller.fleet_ship_enter.assert_not_called()


def test_no_more_material_is_selected_after_the_final_limit_break():
    from types import SimpleNamespace
    controller, done = limit_break_controller(
        [SimpleNamespace(level=80, fleet=1)], [SimpleNamespace(level=1, fleet=0)] * 5,
        completed=[False, True])

    assert done == 1
    controller.limit_break_once.assert_called_once()


def run_break_screens(selection, after_confirm):
    """Run the real recognizers and state machine against a replay with a virtual clock, no device."""
    from types import MethodType, SimpleNamespace
    from unittest.mock import Mock, patch
    from module.leveling.limit_break import LimitBreak

    clock = [100.0]
    controller = SimpleNamespace(
        _templates={}, device=SimpleNamespace(image=selection[0], click=Mock()),
        handle_popup_confirm_loose=Mock(return_value=False), save_stuck_screen=Mock())
    for name in ('_template', 'is_limit_break_tab', 'is_slot_empty', 'is_limit_break_result',
                 'is_limit_break_complete'):
        setattr(controller, name, MethodType(getattr(LimitBreak, name), controller))

    def frames(images):
        for index in range(200):
            clock[0] += 1.0
            controller.device.image = images[min(index, len(images) - 1)]
            yield controller.device.image
        raise AssertionError('Limit break replay did not terminate')

    controller.loop = Mock(side_effect=[frames(selection), frames(after_confirm)])
    with patch('module.base.timer.time', side_effect=lambda: clock[0]):
        done = LimitBreak.limit_break_once(controller)
    return controller, done


def clicked_names(controller):
    return [call.args[0].name for call in controller.device.click.call_args_list]


def test_final_break_waits_for_two_completed_screens_and_does_not_tap_them():
    empty = screen('limit_break_tab.png')
    selected = screen('limit_break_selected.png')
    complete = screen('limit_break_complete.png')
    unknown = np.zeros((720, 1280, 3), dtype=np.uint8)
    controller, done = run_break_screens([empty, selected],
                                        [selected, complete, unknown, complete, complete])

    assert done is True
    assert clicked_names(controller) == ['LIMIT_BREAK_QUICK_SELECT', 'LIMIT_BREAK_CONFIRM']
    controller.save_stuck_screen.assert_not_called()
    assert controller.is_limit_break_complete()


def test_result_then_empty_slot_finishes_one_break():
    controller, done = run_break_screens(
        [screen('limit_break_tab.png'), screen('limit_break_selected.png')],
        [result_screen(), screen('limit_break_tab.png')])

    assert done is True
    assert clicked_names(controller).count('LIMIT_BREAK_SAFE') == 1
    controller.save_stuck_screen.assert_not_called()


def test_unknown_page_is_not_tapped_and_is_saved_on_timeout():
    controller, done = run_break_screens(
        [screen('limit_break_selected.png')], [np.zeros((720, 1280, 3), dtype=np.uint8)])

    assert done is False
    assert clicked_names(controller) == ['LIMIT_BREAK_CONFIRM']
    controller.save_stuck_screen.assert_called_once_with('timeout')


def test_stuck_result_has_a_bounded_number_of_dismiss_taps():
    from module.leveling.limit_break import RESULT_TAPS
    controller, done = run_break_screens([screen('limit_break_selected.png')], [result_screen()])

    assert done is True  # The result proves that material was consumed, even if dismissal failed.
    assert clicked_names(controller).count('LIMIT_BREAK_SAFE') == RESULT_TAPS
    controller.save_stuck_screen.assert_called_once_with('timeout')


def test_confirm_with_material_still_selected_has_bounded_retries():
    from module.leveling.limit_break import CONFIRM_RETRIES
    selected = screen('limit_break_selected.png')
    controller, done = run_break_screens([selected], [selected])

    assert done is False
    assert clicked_names(controller).count('LIMIT_BREAK_CONFIRM') == 1 + CONFIRM_RETRIES
    assert [call.args[0] for call in controller.save_stuck_screen.call_args_list] == [
        'confirm_again', 'confirm_stalled']


def test_material_selection_on_an_already_completed_ship_does_not_click():
    controller, done = run_break_screens([screen('limit_break_complete.png')], [])

    assert done is False
    assert clicked_names(controller) == []
