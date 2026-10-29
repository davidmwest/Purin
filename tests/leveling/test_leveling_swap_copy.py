"""
A swap takes the card of the ship the plan chose, not an other copy of her.

From a code review (2026-10-21): the dock read had Cooper geared at level 110, that copy was on a commission,
and the only free card of her name was a level 1 duplicate. It came in as the level 110 one, without gear or EXP,
and the fleet's average fell to 79 under its minimum of 95. A card at 125 came in the same way, past the farm cap.
"""
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from module.leveling.fleet import LevelingFleet
from module.leveling.leveling import Leveling
from module.leveling.plan import Slot, Target
from module.retire.dock import CARD_GRIDS


def swap(actual_level, records=None):
    """
    Args:
        actual_level (int): Level of the only free card named Cooper in the dock
        records (list[dict]): Ships of the dock read

    Returns:
        tuple: Swaps done, the slot, the controller
    """
    target = Target(name='Cooper', ship='Cooper', hull='DD', row='vanguard', rarity='Elite', level=110,
                    goal=120, chips=1320, arrays=0, gain=0, tech=0, score=0.5, priority=1, geared=True, index=0)
    slots = [Slot(1, 'main', i, f'Other main {i}', 95) for i in range(3)]
    slots += [Slot(1, 'vanguard', i, f'Other vanguard {i}', 95) for i in range(2)]
    slots += [Slot(1, 'vanguard', 2, 'Outgoing', 125)]
    card = SimpleNamespace(level=actual_level, button=CARD_GRIDS[(1, 0)])
    controller = SimpleNamespace(
        device=SimpleNamespace(image=np.zeros((720, 1280, 3), dtype=np.uint8), sleep=Mock(), screenshot=Mock()),
        formation_goto_fleet=Mock(), fleet_slot_enter=Mock(), dock_search=Mock(return_value=True),
        dock_select_one=Mock(), dock_select_confirm=Mock(), ui_back=Mock(), ship_prepare=Mock(),
    )
    controller.dock_find = lambda t: LevelingFleet.dock_find(controller, t)
    controller.fleet_swap = lambda s, t: LevelingFleet.fleet_swap(controller, s, t)
    with patch('module.leveling.fleet.ShipScanner') as scanner, patch('module.leveling.fleet.Ocr') as ocr:
        scanner.return_value.scan.return_value = [card]
        ocr.return_value.ocr.return_value = 'Cooper'
        swaps = Leveling.leveling_execute(controller, [(slots[-1], [target])], slots, records or [], minimum=95)
    return swaps, slots[-1], controller


def test_a_level_1_duplicate_is_not_taken_for_the_geared_ship():
    swaps, slot, controller = swap(1)
    assert swaps == 0
    controller.dock_select_one.assert_not_called()
    assert slot.name == 'Outgoing'


def test_a_ship_past_the_farm_cap_is_not_taken():
    swaps, slot, controller = swap(125)
    assert swaps == 0
    controller.dock_select_one.assert_not_called()


def test_a_ship_levelled_up_since_the_dock_read_comes_in_at_her_level():
    records = [{'name': 'Cooper', 'index': 0, 'level': 110}]
    swaps, slot, controller = swap(112, records=records)
    assert swaps == 1
    assert (slot.name, slot.level) == ('Cooper', 112)
    assert records[0]['level'] == 112


def test_the_copy_of_the_dock_read_comes_in():
    swaps, slot, controller = swap(110)
    assert swaps == 1
    assert slot.level == 110
