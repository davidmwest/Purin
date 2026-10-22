from types import SimpleNamespace

import pytest

from module.exception import MapWalkError
from module.map.fleet import Fleet


class StepFleet(Fleet):
    fleets_reversed = False


def make_fleet(fleet_step=True, step_1=3, step_2=2, location=(3, 3)):
    fleet = StepFleet.__new__(StepFleet)
    fleet.config = SimpleNamespace(MAP_HAS_FLEET_STEP=fleet_step, Fleet_Fleet1Step=step_1, Fleet_Fleet2Step=step_2)
    fleet.fleet_current_index = 1
    fleet.fleet_1_location = location
    fleet.fleet_2_location = ()
    return fleet


def test_a_fleet_sent_too_far_walks_less_from_then_on():
    # Event T3 on the F2P account: from D4 to a siren on D7, the fleet walks 2 cells and Fleet1Step said 3
    fleet = make_fleet()
    assert fleet.fleet_step == 3

    with pytest.raises(MapWalkError):
        fleet.walk_timeout_out_of_step((3, 6))

    assert fleet.fleet_step == 2


def test_the_step_lowers_again_on_another_timeout_but_never_below_one():
    fleet = make_fleet()
    for _ in range(5):
        fleet.fleet_1_location = (0, 0)
        try:
            fleet.walk_timeout_out_of_step((2, 0))
        except MapWalkError:
            pass
    assert fleet.fleet_step == 1


def test_a_timeout_one_cell_away_is_not_the_step():
    fleet = make_fleet()
    fleet.walk_timeout_out_of_step((3, 4))
    assert fleet.fleet_step == 3


def test_a_map_with_no_fleet_step_is_left_alone():
    fleet = make_fleet(fleet_step=False)
    fleet.walk_timeout_out_of_step((3, 7))
    assert fleet.fleet_step == 0


def test_each_fleet_keeps_its_own_step():
    fleet = make_fleet(step_1=3, step_2=3)
    with pytest.raises(MapWalkError):
        fleet.walk_timeout_out_of_step((3, 6))
    fleet.fleet_current_index = 2
    assert fleet.fleet_step == 3
    fleet.fleet_current_index = 1
    assert fleet.fleet_step == 2


def test_a_step_found_is_not_shared_with_other_fleet_handlers():
    fleet = make_fleet()
    with pytest.raises(MapWalkError):
        fleet.walk_timeout_out_of_step((3, 6))
    assert make_fleet().fleet_step == 3
