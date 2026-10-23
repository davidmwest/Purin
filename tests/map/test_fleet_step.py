from types import SimpleNamespace

import pytest

from module.exception import MapWalkError
from module.map.fleet import Fleet


class StepFleet(Fleet):
    fleets_reversed = False


def make_fleet(fleet_step=True, step_1=3, step_2=2, location=(3, 3), route=None):
    """
    Args:
        route (list[tuple]): What the map gives as the route to any location, None for no map
    """
    fleet = StepFleet.__new__(StepFleet)
    fleet.config = SimpleNamespace(MAP_HAS_FLEET_STEP=fleet_step, Fleet_Fleet1Step=step_1, Fleet_Fleet2Step=step_2)
    fleet.fleet_current_index = 1
    fleet.fleet_1_location = location
    fleet.fleet_2_location = ()
    if route is not None:
        fleet.map = SimpleNamespace(_find_path=lambda location: route)
    return fleet


def test_a_fleet_sent_too_far_walks_less_from_then_on():
    # Event T3 on the F2P account: from D4 to a siren on D7, the fleet walks 2 cells and Fleet1Step said 3
    fleet = make_fleet()
    assert fleet.fleet_step == 3

    with pytest.raises(MapWalkError):
        fleet.walk_timeout_out_of_step((3, 6))

    assert fleet.fleet_step == 2


def test_the_step_never_goes_below_2():
    # Fleet steps of the game are 2 at least: a timeout 2 cells away is something else, the retry goes on
    fleet = make_fleet(step_1=2)
    fleet.walk_timeout_out_of_step((3, 5))
    assert fleet.fleet_step == 2

    fleet = make_fleet()
    with pytest.raises(MapWalkError):
        fleet.walk_timeout_out_of_step((3, 6))
    fleet.walk_timeout_out_of_step((3, 5))
    assert fleet.fleet_step == 2


def test_a_node_planned_with_the_step_before_does_not_lower_it_again():
    # Code review, 2026-10-22: nodes 3 cells apart planned with step 3 took the step down to 1, the fleet walks 2
    fleet = make_fleet()
    with pytest.raises(MapWalkError):
        fleet.walk_timeout_out_of_step((3, 6))
    fleet.fleet_1_location = (3, 6)

    with pytest.raises(MapWalkError):
        fleet.walk_timeout_out_of_step((3, 9))

    assert fleet.fleet_step == 2


def test_the_route_counts_not_the_straight_line():
    # T3: B3 to B5 goes round the land on B4, 4 cells for a Manhattan distance of 2
    route = [(1, 2), (0, 2), (0, 3), (0, 4), (1, 4)]
    fleet = make_fleet(step_1=4, location=(1, 2), route=route)
    assert fleet.walk_distance((1, 4)) == 4

    with pytest.raises(MapWalkError):
        fleet.walk_timeout_out_of_step((1, 4))

    assert fleet.fleet_step == 3


def test_a_timeout_one_cell_away_is_not_the_step():
    fleet = make_fleet()
    fleet.walk_timeout_out_of_step((3, 4))
    assert fleet.fleet_step == 3


def test_a_map_with_no_fleet_step_is_left_alone():
    fleet = make_fleet(fleet_step=False)
    fleet.walk_timeout_out_of_step((3, 7))
    assert fleet.fleet_step == 0


def test_walking_a_cell_at_a_time_never_raises():
    # The fallback of goto() walks outside its try: a MapWalkError there would end the task
    fleet = make_fleet()
    fleet._walk_step_fallback = True
    fleet.walk_timeout_out_of_step((3, 6))
    assert fleet.fleet_step == 3


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


def test_a_fleet_that_walks_2_reaches_a_far_cell_of_event_t3():
    # A fleet sent from A3 to I6 with Fleet1Step 3: the first node 3 cells away times out, the step goes to 2, the
    # rest of the route is planned again with it, and no node times out after that
    import copy
    from campaign.event_20261022_cn.t3 import MAP as T3
    from module.map.map_base import location_ensure
    MAP = copy.deepcopy(T3)
    MAP.load_map_data(use_loop=False)
    MAP.grid_connection_initial(wall=False, portal=False)
    real_step = 2
    fleet = make_fleet(location=location_ensure('A3'))
    fleet.config.MAP_HAS_PORTAL = False
    fleet.config.MAP_HAS_MAZE = False
    fleet.config.MAP_HAS_AMBUSH = False
    fleet.map = MAP
    fleet.predict = lambda: None
    fleet.ensure_edge_insight = lambda *args, **kwargs: None
    fleet.maze_active_on = lambda node: False
    timeouts = []

    def find_path_initial():
        MAP.find_path_initial(fleet.fleet_1_location, has_ambush=False, has_enemy=False)

    def goto_cell(location, expected=''):
        if fleet.walk_distance(location) > real_step and not fleet._walk_step_fallback:
            timeouts.append(location)
            fleet.walk_timeout_out_of_step(location)
            raise AssertionError(f'Too many clicks on {location}')
        fleet.fleet_1_location = location
        find_path_initial()

    fleet._goto = goto_cell
    find_path_initial()

    fleet.goto(location_ensure('I6'))

    assert fleet.fleet_1_location == location_ensure('I6')
    assert fleet.fleet_step == 2
    assert len(timeouts) == 1
