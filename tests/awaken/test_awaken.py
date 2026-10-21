import os
from types import SimpleNamespace
from unittest.mock import Mock, call

import cv2
import numpy as np
import pytest

from module.awaken.awaken import Awaken

DATA = os.path.join(os.path.dirname(__file__), 'data')


def awaken_popup():
    """
    ./data/awaken_cost.png is the area (530, 355, 750, 475) of the awaken popup on the EN client:
    coins 88712/2400 and cognitive chips 2389/240, no cognitive arrays.
    """
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    image[355:475, 530:750] = cv2.cvtColor(cv2.imread(os.path.join(DATA, 'awaken_cost.png')), cv2.COLOR_BGR2RGB)
    awaken = Awaken.__new__(Awaken)
    awaken.device = SimpleNamespace(image=image)
    return awaken


def test_chips_owned_and_cost_are_read_from_the_popup():
    awaken = awaken_popup()

    assert awaken._get_awaken_cost() is True
    assert awaken._get_chip_amount() == (2389, 240)


@pytest.mark.parametrize('reserve, kept', [
    (0, True),
    (1500, True),
    (2149, True),
    (2150, False),
    (5000, False),
])
def test_chip_reserve_on_the_popup(reserve, kept):
    awaken = awaken_popup()
    awaken.config = SimpleNamespace(Awaken_ChipReserve=reserve)
    awaken._get_awaken_cost()

    assert awaken.is_chip_reserve_kept() is kept


@pytest.mark.parametrize('owned, cost', [
    # Not read
    (0, 0),
    (2389, 0),
    # Read wrong: the popup only gets this far with enough chips
    (238, 400),
])
def test_chips_are_not_spent_on_numbers_that_make_no_sense(owned, cost):
    awaken = SimpleNamespace(config=SimpleNamespace(Awaken_ChipReserve=1500), _get_chip_amount=lambda: (owned, cost))

    assert Awaken.is_chip_reserve_kept(awaken) is False


def make_run(results, order='highest_level_first', fleets=None):
    """
    Args:
        results (list[str]): Result of awakening the ships entered, one after another.
            There is no ship to enter after them.
        fleets (list[int]): Fleets whose ships are awakened, None for any ship
    """
    entered = iter([True] * len(results) + [False])
    return SimpleNamespace(
        awaken_fleets=Mock(return_value=fleets),
        awaken_enter_fleet_ship=Mock(side_effect=lambda fleets, index: next(entered)),
        config=SimpleNamespace(Awaken_Order=order, Awaken_Index='dd', Awaken_Faction='all', Awaken_Rarity='elite'),
        ui_ensure=Mock(),
        dock_favourite_set=Mock(),
        dock_sort_method_dsc_set=Mock(),
        dock_filter_set=Mock(),
        appear=Mock(return_value=False),
        awaken_enter_ship=Mock(side_effect=lambda index: next(entered)),
        awaken_ship=Mock(side_effect=results),
        awaken_exit=Mock(),
    )


def test_ships_passed_over_for_the_reserve_are_not_entered_again():
    run = make_run(['reserve', 'no_exp', 'reserve'])

    assert Awaken.awaken_run(run) == 'finish'

    # A ship that was awakened leaves the dock, a ship passed over stays in front
    assert run.awaken_enter_ship.call_args_list == [call(0), call(1), call(1), call(2)]


def test_awaken_run_stops_when_resources_run_out():
    run = make_run(['no_exp', 'insufficient', 'no_exp'])

    assert Awaken.awaken_run(run) == 'insufficient'

    assert run.awaken_ship.call_count == 2


@pytest.mark.parametrize('order, descending', [
    ('highest_level_first', True),
    ('lowest_level_first', False),
])
def test_awaken_run_shows_the_ships_asked_for(order, descending):
    run = make_run([], order=order)

    Awaken.awaken_run(run, favourite=True)

    run.dock_favourite_set.assert_called_once_with(enable=True, wait_loading=False)
    run.dock_sort_method_dsc_set.assert_called_once_with(descending, wait_loading=False)
    run.dock_filter_set.assert_called_once_with(index='dd', faction='all', rarity='elite', extra=['can_awaken'])


@pytest.mark.parametrize('interval, delay', [
    (0, call(server_update=True)),
    (90, call(minute=90, server_update=True)),
])
def test_awaken_is_checked_as_often_as_asked(interval, delay):
    awaken = SimpleNamespace(
        config=SimpleNamespace(Awaken_Favourite=False, Awaken_LevelCap='level120', Awaken_CheckInterval=interval,
                               task_delay=Mock()),
        awaken_run=Mock(return_value='finish'),
        dock_favourite_set=Mock(),
        dock_filter_set=Mock(),
    )

    Awaken.run(awaken)

    assert awaken.config.task_delay.call_args == delay


def test_only_ships_of_the_fleets_are_entered():
    run = make_run(['reserve', 'no_exp'], fleets=[1, 2])

    assert Awaken.awaken_run(run) == 'finish'

    assert run.awaken_enter_fleet_ship.call_args_list == [call([1, 2], 0), call([1, 2], 1), call([1, 2], 1)]
    run.awaken_enter_ship.assert_not_called()


def test_dock_filter_takes_the_rarity_asked_for():
    run = make_run([])

    Awaken.awaken_run(run, use_array=True, rarity='ultra')

    run.dock_filter_set.assert_called_once_with(index='dd', faction='all', rarity='ultra', extra=['can_awaken_plus'])


@pytest.mark.parametrize('only, order, submarine, fleets', [
    (False, 'fleet1_mob_fleet2_boss', 1, None),
    # Both fleets farm, the boss fleet with its ships from level 100 to 119, capped ones awaken to go on.
    # Submarine fleet 1 shows on the dock as fleet 1.
    (True, 'fleet1_mob_fleet2_boss', 1, [1, 2]),
    (True, 'fleet1_mob_fleet2_boss', 3, [1, 2, 3]),
    (True, 'fleet1_boss_fleet2_mob', 0, [1, 2]),
])
def test_fleets_of_the_campaign(only, order, submarine, fleets):
    keys = {'Main.Fleet.Fleet1': 1, 'Main.Fleet.Fleet2': 2, 'Main.Fleet.FleetOrder': order,
            'Main.Submarine.Fleet': submarine}
    awaken = SimpleNamespace(config=SimpleNamespace(
        Awaken_OnlyFleets=only, cross_get=lambda keys_, default=None: keys.get(keys_, default)))

    assert Awaken.awaken_fleets(awaken) == fleets


def test_arrays_for_ultra_rares_only():
    awaken = SimpleNamespace(
        config=SimpleNamespace(Awaken_Favourite=False, Awaken_LevelCap='ultra_level125', Awaken_CheckInterval=0,
                               task_delay=Mock()),
        awaken_run=Mock(return_value='finish'),
        dock_favourite_set=Mock(),
        dock_filter_set=Mock(),
    )

    Awaken.run(awaken)

    assert awaken.awaken_run.call_args_list == [
        call(use_array=True, favourite=False, rarity='ultra'),
        call(favourite=False),
    ]


def offscreen_run(results, walk):
    """
    The real awaken_run and awaken_enter_ship, the walk down the dock mocked.

    Args:
        results (list[str]): Of awaken_ship, one after another
        walk (list[bool]): Of awaken_walk_enter, one after another
    """
    from module.retire.assets import SHIP_DETAIL_CHECK
    run = SimpleNamespace(
        config=SimpleNamespace(Awaken_Order='highest_level_first', Awaken_Index='all', Awaken_Faction='all',
                               Awaken_Rarity='all'),
        device=SimpleNamespace(screenshot=Mock(), click=Mock()),
        ui_ensure=Mock(), dock_favourite_set=Mock(), dock_sort_method_dsc_set=Mock(), dock_filter_set=Mock(),
        awaken_fleets=Mock(return_value=None), dock_enter_first=Mock(return_value=True),
        interval_clear=Mock(), awaken_ship=Mock(side_effect=results), awaken_exit=Mock(),
        appear=lambda button, **kwargs: button == SHIP_DETAIL_CHECK,
        awaken_walk_back=Mock(),
        awaken_walk_enter=Mock(side_effect=walk),
    )
    run.awaken_enter_ship = lambda index: Awaken.awaken_enter_ship(run, index)
    run.awaken_click_card = lambda button, index, skip_first_screenshot=True: \
        Awaken.awaken_click_card(run, button, index, skip_first_screenshot=skip_first_screenshot)
    return run


def test_a_cheaper_ship_past_the_first_screen_is_entered():
    # Code review (Codex, 2026-10-21): 14 ships passed over for the chip reserve fill the first screen of the
    # dock, 7 by 2, and the run ended there, a cheaper 15th ship further down was never tried
    run = offscreen_run(['reserve'] * 14 + ['no_exp'], walk=[True, False])

    assert Awaken.awaken_run(run) == 'finish'

    assert run.awaken_ship.call_count == 15
    # The 15th ship, then the end of the dock
    assert run.awaken_walk_enter.call_args_list == [call(14), call(14)]


def test_a_ship_of_the_fleets_further_down_is_looked_for():
    # Other ships that can awaken fill the first screen, the ones of the fleets come after them
    from unittest.mock import patch
    awaken = SimpleNamespace(
        device=SimpleNamespace(image=np.zeros((720, 1280, 3), dtype=np.uint8), screenshot=Mock()),
        awaken_walk_back=Mock(),
        awaken_walk_enter=Mock(return_value=True),
    )
    with patch('module.awaken.awaken.ShipScanner') as scanner:
        scanner.return_value.scan.return_value = []
        assert Awaken.awaken_enter_fleet_ship(awaken, [1], 0) is True
    awaken.awaken_walk_enter.assert_called_once_with(0, fleets=[1])


def test_a_capped_ship_of_the_boss_fleet_is_awakened():
    # Code review (Codex, 2026-10-21): the boss fleet keeps a ship at level 100, she does not rotate out under 120,
    # and only an awakening lets her level on. The main account: fleet 1 mobs, fleet 2 boss, submarines in fleet 1.
    from module.leveling.plan import Slot, plan_carries
    slot = Slot(2, 'vanguard', 0, 'Cooper', 100)
    assert plan_carries([slot], [slot], []) == []
    keys = {'Main.Fleet.Fleet1': 1, 'Main.Fleet.Fleet2': 2, 'Main.Fleet.FleetOrder': 'fleet1_mob_fleet2_boss',
            'Main.Submarine.Fleet': 1}
    awaken = SimpleNamespace(config=SimpleNamespace(
        Awaken_OnlyFleets=True, cross_get=lambda keys_, default=None: keys.get(keys_, default)))
    assert slot.fleet in Awaken.awaken_fleets(awaken)
