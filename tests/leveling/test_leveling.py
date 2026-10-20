from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from module.leveling.leveling import Leveling, gear_tried_recently
from module.leveling.plan import Slot, Target


@pytest.fixture
def one_passenger(monkeypatch):
    # How the limit counts, with a vanguard that takes one ship below 90
    monkeypatch.setattr('module.leveling.leveling.PASSENGERS', {'vanguard': 1, 'main': 2})


def target(name):
    return Target(name=name, ship=name, hull='DD', row='vanguard', rarity='Elite', level=70, goal=120, chips=1320,
                  arrays=0, gain=0, tech=0, score=0.5, priority=1, geared=True, index=0)


def test_each_ship_comes_in_once_and_a_missing_one_is_not_tried_again(one_passenger):
    slots = [Slot(1, 'vanguard', 0, 'Dmitri Donskoi', 100), Slot(2, 'vanguard', 1, 'Nana Astar Deviluke', 110),
             Slot(2, 'vanguard', 2, 'Vittorio Cuniberti', 105)]
    candidates = [target('Hasty'), target('Cooper'), target('Vampire')]
    plans = [(slot, candidates) for slot in slots]
    swaps = []

    def fleet_swap(slot, ship):
        # Cooper is in an other fleet
        if ship.ship == 'Cooper':
            return False
        swaps.append((slot.fleet, slot.index, ship.ship))
        return True

    leveling = SimpleNamespace(formation_goto_fleet=Mock(), fleet_swap=Mock(side_effect=fleet_swap))

    assert Leveling.leveling_execute(leveling, plans) == 2
    assert swaps == [(1, 0, 'Hasty'), (2, 1, 'Vampire')]
    # Cooper was tried once only, the last slot had no one left
    assert [args[1].ship for args, _ in leveling.fleet_swap.call_args_list] == ['Hasty', 'Cooper', 'Vampire']
    assert leveling.formation_goto_fleet.call_args_list == [call(1), call(2), call(2)]


def test_no_inventory_waits_for_the_next_day(monkeypatch):
    monkeypatch.setattr('module.leveling.leveling.load_inventory', lambda name: None)
    leveling = SimpleNamespace(config=SimpleNamespace(config_name='alas', task_delay=Mock()), ui_ensure=Mock())

    Leveling.run(leveling)

    leveling.config.task_delay.assert_called_once_with(server_update=True)
    leveling.ui_ensure.assert_not_called()


def test_a_vanguard_takes_one_passenger(one_passenger):
    # Hasty at 72 stays in the vanguard of fleet 1, Ping Hai at 91 makes room
    slots = [Slot(1, 'vanguard', 0, 'Hasty', 72), Slot(1, 'vanguard', 1, 'Ping Hai Retrofit', 91),
             Slot(1, 'main', 2, 'Kansas', 85)]
    cooper, london = target('Cooper'), target('London (Retrofit)')
    london.level = 118
    plans = [(slots[1], [cooper, london]), (slots[2], [target('Gangut')])]
    leveling = SimpleNamespace(formation_goto_fleet=Mock(), fleet_swap=Mock(return_value=True))

    assert Leveling.leveling_execute(leveling, plans, slots) == 2
    # Cooper at 70 would be a second passenger there, London comes in
    assert [args[1].ship for args, _ in leveling.fleet_swap.call_args_list] == ['London (Retrofit)', 'Gangut']


GEARED = [10, 10, 10, 10, 0]


def leveling_with(fleet_swap, gear_ship=None):
    return SimpleNamespace(formation_goto_fleet=Mock(), fleet_swap=Mock(side_effect=fleet_swap),
                           gear_ship=Mock(side_effect=gear_ship or (lambda target: list(GEARED))), ui_ensure=Mock(),
                           fleet_formation_ensure=Mock())


def test_a_ship_without_gear_gets_some_before_she_comes_in(one_passenger):
    slots = [Slot(1, 'vanguard', 0, 'Hasty', 73), Slot(1, 'vanguard', 1, 'Ning Hai(Retrofit', 91)]
    isokaze, hamakaze, maury = target('Isokaze'), target('Hamakaze'), target('Maury (Retrofit)')
    maury.level = 91
    for ship in (isokaze, hamakaze, maury):
        ship.geared = False
    ships = [{'name': 'Isokaze', 'index': 0}, {'name': 'Maury (Retrofit)', 'index': 0}]
    plans = [(slots[0], [isokaze, hamakaze]), (slots[1], [isokaze, hamakaze, maury])]
    leveling = leveling_with(lambda slot, ship: True)

    assert Leveling.leveling_execute(leveling, plans, slots, ships) == 2

    # Isokaze in place of Hasty, the one passenger of the vanguard; Hamakaze would be a second, Maury is at 91
    assert [args[1].ship for args, _ in leveling.fleet_swap.call_args_list] == ['Isokaze', 'Maury (Retrofit)']
    assert [args[0].ship for args, _ in leveling.gear_ship.call_args_list] == ['Isokaze', 'Maury (Retrofit)']
    # Her gear as read after it
    assert ships[0]['gear_levels'] == GEARED and ships[1]['gear_levels'] == GEARED
    assert ships[0]['gear_tried']


def test_a_ship_storage_can_not_gear_stays_out(one_passenger):
    slots = [Slot(1, 'vanguard', 0, 'Hasty', 73)]
    isokaze, hamakaze = target('Isokaze'), target('Hamakaze')
    isokaze.geared = False
    ships = [{'name': 'Isokaze', 'index': 0}]
    plans = [(slots[0], [isokaze, hamakaze])]
    # Storage had gear at +10 for 2 slots, her own stays at +0
    leveling = leveling_with(lambda slot, ship: True, gear_ship=lambda target: [0, 0, 10, 10, 0])

    assert Leveling.leveling_execute(leveling, plans, slots, ships) == 1
    # Hamakaze has her gear
    assert [args[1].ship for args, _ in leveling.fleet_swap.call_args_list] == ['Hamakaze']
    # Not tried again for a day
    assert ships[0]['gear_levels'] == [0, 0, 10, 10, 0]
    leveling = leveling_with(lambda slot, ship: True)
    Leveling.leveling_execute(leveling, plans, slots, ships)
    leveling.gear_ship.assert_not_called()


def test_gear_tried_is_written_before_the_trip_to_the_depot(one_passenger):
    # A trip that ends in an error does not take her there again and again
    slots = [Slot(1, 'vanguard', 0, 'Hasty', 73)]
    isokaze = target('Isokaze')
    isokaze.geared = False
    ships = [{'name': 'Isokaze', 'index': 0}]
    events = []
    save = Mock(side_effect=lambda: events.append(('save', dict(ships[0]))))
    leveling = leveling_with(lambda slot, ship: True,
                             gear_ship=lambda target: events.append(('gear', dict(ships[0]))) or list(GEARED))

    assert Leveling.leveling_execute(leveling, [(slots[0], [isokaze])], slots, ships, save=save) == 1
    assert [event for event, _ in events] == ['save', 'gear', 'save']
    assert 'gear_tried' in events[0][1] and 'gear_levels' not in events[0][1]
    assert events[2][1]['gear_levels'] == GEARED


def test_gear_tried_a_day_ago_is_tried_again():
    now = datetime(2026, 10, 20, 12, 0, 0)
    assert gear_tried_recently({'gear_tried': '2026-10-20 08:00:00'}, now)
    assert not gear_tried_recently({'gear_tried': '2026-10-19 08:00:00'}, now)
    assert not gear_tried_recently({}, now)
    assert not gear_tried_recently(None, now)


def test_ships_come_in_a_few_at_a_time():
    slots = [Slot(1, 'main', index, name, 100) for index, name in enumerate(['Gangut', 'Kansas', 'Hiei'])]
    plans = [(slot, [target(name)]) for slot, name in zip(slots, ['Tirpitz', 'Kirishima', 'Langley II'])]
    for _, candidates in plans:
        candidates[0].level = 95
    leveling = leveling_with(lambda slot, ship: True)

    assert Leveling.leveling_execute(leveling, plans, slots, [], limit=2) == 2
    assert [args[1].ship for args, _ in leveling.fleet_swap.call_args_list] == ['Tirpitz', 'Kirishima']


def test_main_fleet_takes_two_passengers():
    # Hakuhou at 80 is one, a second may come in, a third not
    slots = [Slot(1, 'main', 0, 'Hakuhou', 80), Slot(1, 'main', 1, 'Gangut', 97), Slot(1, 'main', 2, 'Haruna', 102)]
    plans = [(slots[1], [target('Tirpitz')]), (slots[2], [target('Kirishima')])]
    leveling = leveling_with(lambda slot, ship: True)

    assert Leveling.leveling_execute(leveling, plans, slots, []) == 1
    assert [args[1].ship for args, _ in leveling.fleet_swap.call_args_list] == ['Tirpitz']


def test_mob_vanguard_takes_no_passenger():
    # Hasty at 73 sank in the sixth battle of the mobs of 12-4 and lost it
    slots = [Slot(1, 'vanguard', 0, 'Dmitri Donskoi', 100)]
    hasty, vittorio = target('Hasty'), target('Vittorio Cuniberti')
    hasty.level, vittorio.level = 73, 105
    plans = [(slots[0], [hasty, vittorio])]
    leveling = leveling_with(lambda slot, ship: True)

    assert Leveling.leveling_execute(leveling, plans, slots, []) == 1
    assert [args[1].ship for args, _ in leveling.fleet_swap.call_args_list] == ['Vittorio Cuniberti']
