"""
Copies of a ship in the dock, told apart by the slot each was seen in and by their level: the Codex review of
2026-10-21 found both slots of two copies of Cooper taken for the copy at 110.
"""
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from module.leveling.leveling import Leveling, inventory_ships_of
from module.leveling.plan import SLOT_KEY, Slot, Target, gear_record, load_ship_data, slot_records, update_levels


@pytest.fixture(scope='module')
def data():
    return load_ship_data()


def coopers():
    # A copy at 70 without gear in fleet 1, one at 110 with gear checked in fleet 2
    ships = [dict(name='Cooper', index=0, level=70, hull='DD', **gear_record([0] * 6)),
             dict(name='Cooper', index=1, level=110, hull='DD', **gear_record([10] * 6, checked=True))]
    slots = [Slot(1, 'vanguard', 0, 'Cooper', 70), Slot(2, 'vanguard', 0, 'Cooper', 110)]
    return ships, slots


def test_copies_in_two_fleets_are_told_apart(data):
    ships, slots = coopers()

    records = inventory_ships_of(ships, slots, data)

    assert records[id(slots[0])] is ships[0]
    assert records[id(slots[1])] is ships[1]


def test_the_copy_without_gear_gets_her_trip(data):
    ships, slots = coopers()
    records = inventory_ships_of(ships, slots, data)
    leveling = SimpleNamespace(leveling_member=Mock(return_value=(gear_record([10] * 6, checked=True), None)))

    assert Leveling.leveling_gear_members(leveling, slots, records) == 1
    assert leveling.leveling_member.call_args == call(slots[0], gear=True, hull='DD')
    assert ships[0]['gear_levels'] == [10] * 5


def test_packs_raise_the_copy_of_the_slot(data):
    ships, slots = coopers()
    records = inventory_ships_of(ships, slots, data)
    leveling = SimpleNamespace(leveling_member=Mock(return_value=(None, 100)))

    assert Leveling.leveling_boost_members(leveling, [slots[0]], 100, records) == 1

    assert [slot.level for slot in slots] == [100, 110]
    assert [ship['level'] for ship in ships] == [100, 110]


def test_copies_that_could_both_be_her_are_left_out(data):
    # Both copies are below the level read, neither was seen in the slot: the copy at 70 may be the one at 101 now
    ships = [dict(name='Cooper', index=0, level=70), dict(name='Cooper', index=1, level=100)]
    slots = [Slot(1, 'vanguard', 0, 'Cooper', 101)]

    assert slot_records(ships, slots, data) == {}
    assert update_levels(ships, slots, data) == 0
    assert [ship['level'] for ship in ships] == [70, 100]


def test_the_copy_seen_in_the_slot_is_hers(data):
    ships = [dict(name='Cooper', index=0, level=70, **{SLOT_KEY: [1, 'vanguard', 0]}),
             dict(name='Cooper', index=1, level=100)]
    slots = [Slot(1, 'vanguard', 0, 'Cooper', 101)]

    assert update_levels(ships, slots, data) == 1
    assert [ship['level'] for ship in ships] == [101, 100]


def test_a_copy_at_the_level_of_the_slot_is_hers_and_seen_there(data):
    ships = [dict(name='Cooper', index=0, level=70), dict(name='Cooper', index=1, level=100)]
    slots = [Slot(1, 'vanguard', 0, 'Cooper', 100)]

    assert update_levels(ships, slots, data) == 0
    assert ships[1][SLOT_KEY] == [1, 'vanguard', 0] and SLOT_KEY not in ships[0]
    # Seen there, she is still hers once she levels past the copy in the dock
    slots[0].level = 102
    assert slot_records(ships, slots, data)[id(slots[0])] is ships[1]


def test_a_copy_above_the_level_read_is_not_in_the_slot(data):
    # The copy last seen there went back to the dock, the one at 70 is there now
    ships = [dict(name='Cooper', index=0, level=110, **{SLOT_KEY: [1, 'vanguard', 0]}),
             dict(name='Cooper', index=1, level=70)]
    slots = [Slot(1, 'vanguard', 0, 'Cooper', 70)]

    assert slot_records(ships, slots, data)[id(slots[0])] is ships[1]


def test_a_copy_that_comes_in_is_seen_in_her_slot():
    slot = Slot(1, 'vanguard', 0, 'Hasty', 110)
    ships = [dict(name='Hasty', index=3, level=110, **{SLOT_KEY: [1, 'vanguard', 0]}),
             dict(name='Cooper', index=0, level=70), dict(name='Cooper', index=1, level=110)]
    target = Target(name='Cooper', ship='Cooper', hull='DD', row='vanguard', rarity='Rare', level=110, goal=120,
                    chips=880, arrays=0, gain=0, tech=0, score=1, priority=1, geared=True, index=1)
    leveling = SimpleNamespace(formation_goto_fleet=Mock(), fleet_swap=Mock(return_value=True))

    assert Leveling.leveling_execute(leveling, [(slot, [target])], [slot], ships) == 1

    assert ships[2][SLOT_KEY] == [1, 'vanguard', 0]
    # Hasty left the slot, the copy at 70 was never there
    assert SLOT_KEY not in ships[0] and SLOT_KEY not in ships[1]
