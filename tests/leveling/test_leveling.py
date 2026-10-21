from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from module.leveling.leveling import Leveling, gear_tried_recently
from module.leveling.plan import Slot, Target


@pytest.fixture(autouse=True)
def gear_transfer(monkeypatch):
    monkeypatch.setattr('module.leveling.leveling.GEAR_TRANSFER', True)


def target(name, level=70, geared=True):
    return Target(name=name, ship=name, hull='DD', row='vanguard', rarity='Elite', level=level, goal=120, chips=1320,
                  arrays=0, gain=0, tech=0, score=0.5, priority=1, geared=geared, index=0)


def test_each_ship_comes_in_once_and_a_missing_one_is_not_tried_again():
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


# 5 gear slots and the augment at +10
GEARED = [10, 10, 10, 10, 10, 10]


def leveling_with(fleet_swap=None, prepare=None):
    """
    prepare(target, gear, level, spare) -> (gear levels, level), as ship_prepare()
    """
    def default(target, gear=True, level=None, spare=False):
        return (list(GEARED) if gear else None), level

    return SimpleNamespace(formation_goto_fleet=Mock(), fleet_swap=Mock(side_effect=fleet_swap or (lambda s, t: True)),
                           ship_prepare=Mock(side_effect=prepare or default), ui_ensure=Mock(),
                           fleet_formation_ensure=Mock())


def mob_fleet():
    # Fleet 1 of 12-4 on 2026-10-20, average 93.7
    return [Slot(1, 'main', 0, 'Hakuhou', 89), Slot(1, 'main', 1, 'Gangut', 99), Slot(1, 'main', 2, 'Kansas', 93),
            Slot(1, 'vanguard', 0, 'Maury (Retrofit)', 95), Slot(1, 'vanguard', 1, 'Ping Hai Retrofit', 95),
            Slot(1, 'vanguard', 2, 'Nicholas (Retrofit)', 91)]


def test_a_ship_comes_in_if_the_fleet_keeps_its_minimum():
    slots = mob_fleet()
    plans = [(slots[5], [target('Isokaze')])]
    leveling = leveling_with()

    # Without Nicholas the others are at 471, Isokaze at 70 keeps the fleet above 85
    assert Leveling.leveling_execute(leveling, plans, slots, [], minimum=85) == 1
    assert (slots[5].name, slots[5].level) == ('Isokaze', 70)
    leveling.ship_prepare.assert_not_called()


def test_a_ship_short_of_the_minimum_comes_in_raised_with_packs():
    slots = mob_fleet()
    plans = [(slots[5], [target('Isokaze')])]
    ships = [{'name': 'Isokaze', 'index': 0, 'level': 70}]
    leveling = leveling_with()

    # At 95 she has to be at 99 at least, and spare packs may take her to 100
    assert Leveling.leveling_execute(leveling, plans, slots, ships, minimum=95, use_packs=True) == 1
    leveling.ship_prepare.assert_called_once()
    assert leveling.ship_prepare.call_args[1] == {'gear': False, 'level': 99, 'spare': True}
    assert (slots[5].name, slots[5].level) == ('Isokaze', 99)
    assert ships[0]['level'] == 99


def test_a_ship_short_of_the_minimum_stays_out_without_packs():
    slots = mob_fleet()
    plans = [(slots[5], [target('Isokaze'), target('London (Retrofit)', level=118)])]
    leveling = leveling_with()

    assert Leveling.leveling_execute(leveling, plans, slots, [], minimum=95) == 1
    # London at 118 keeps the minimum
    assert [args[1].ship for args, _ in leveling.fleet_swap.call_args_list] == ['London (Retrofit)']
    leveling.ship_prepare.assert_not_called()


def test_packs_take_a_ship_to_100_at_most():
    slots = mob_fleet()
    # With Maury at 87 the others are at 463, at 95 she has to be at 107
    slots[3].level = 87
    plans = [(slots[5], [target('Isokaze')])]
    leveling = leveling_with()

    assert Leveling.leveling_execute(leveling, plans, slots, [], minimum=95, use_packs=True) == 0
    leveling.ship_prepare.assert_not_called()


def test_a_ship_the_packs_could_not_raise_stays_out():
    slots = mob_fleet()
    plans = [(slots[5], [target('Isokaze')])]
    leveling = leveling_with(prepare=lambda target, gear=True, level=None, spare=False: (None, None))

    assert Leveling.leveling_execute(leveling, plans, slots, [], minimum=95, use_packs=True) == 0
    leveling.fleet_swap.assert_not_called()


def test_a_ship_without_gear_gets_some_before_she_comes_in():
    slots = [Slot(1, 'vanguard', 0, 'Hasty', 73), Slot(1, 'vanguard', 1, 'Ning Hai(Retrofit', 91)]
    isokaze, maury = target('Isokaze', geared=False), target('Maury (Retrofit)', level=91, geared=False)
    ships = [{'name': 'Isokaze', 'index': 0}, {'name': 'Maury (Retrofit)', 'index': 0}]
    plans = [(slots[0], [isokaze]), (slots[1], [isokaze, maury])]
    leveling = leveling_with()

    assert Leveling.leveling_execute(leveling, plans, slots, ships) == 2

    assert [args[1].ship for args, _ in leveling.fleet_swap.call_args_list] == ['Isokaze', 'Maury (Retrofit)']
    assert [(args[0].ship, kwargs) for args, kwargs in leveling.ship_prepare.call_args_list] == [
        ('Isokaze', {'gear': True, 'level': None, 'spare': False}),
        ('Maury (Retrofit)', {'gear': True, 'level': None, 'spare': False})]
    # Her gear as read after it
    assert ships[0]['gear_levels'] == GEARED[:5] and ships[0]['augment_level'] == 10
    assert ships[1]['gear_levels'] == GEARED[:5]
    assert ships[0]['gear_tried']


def test_a_ship_storage_can_not_gear_stays_out():
    slots = [Slot(1, 'vanguard', 0, 'Hasty', 73)]
    isokaze, hamakaze = target('Isokaze', geared=False), target('Hamakaze')
    ships = [{'name': 'Isokaze', 'index': 0}]
    plans = [(slots[0], [isokaze, hamakaze])]
    # Storage had gear at +10 for 2 slots, her own stays at +0
    leveling = leveling_with(prepare=lambda target, gear=True, level=None, spare=False: ([0, 0, 10, 10, 0, 0], None))

    assert Leveling.leveling_execute(leveling, plans, slots, ships) == 1
    # Hamakaze has her gear
    assert [args[1].ship for args, _ in leveling.fleet_swap.call_args_list] == ['Hamakaze']
    # Not tried again for a day
    assert ships[0]['gear_levels'] == [0, 0, 10, 10, 0]
    slots = [Slot(1, 'vanguard', 0, 'Hasty', 73)]
    leveling = leveling_with()
    Leveling.leveling_execute(leveling, [(slots[0], [isokaze])], slots, ships)
    leveling.ship_prepare.assert_not_called()


def test_gear_tried_is_written_before_the_trip_to_the_depot():
    # A trip that ends in an error does not take her there again and again
    slots = [Slot(1, 'vanguard', 0, 'Hasty', 73)]
    isokaze = target('Isokaze', geared=False)
    ships = [{'name': 'Isokaze', 'index': 0}]
    events = []
    save = Mock(side_effect=lambda: events.append(('save', dict(ships[0]))))

    def prepare(target, gear=True, level=None, spare=False):
        events.append(('gear', dict(ships[0])))
        return list(GEARED), None

    leveling = leveling_with(prepare=prepare)

    assert Leveling.leveling_execute(leveling, [(slots[0], [isokaze])], slots, ships, save=save) == 1
    assert [event for event, _ in events] == ['save', 'gear', 'save']
    assert 'gear_tried' in events[0][1] and 'gear_levels' not in events[0][1]
    assert events[2][1]['gear_levels'] == GEARED[:5]


def test_gear_tried_a_day_ago_is_tried_again():
    now = datetime(2026, 10, 20, 12, 0, 0)
    assert gear_tried_recently({'gear_tried': '2026-10-20 08:00:00'}, now)
    assert not gear_tried_recently({'gear_tried': '2026-10-19 08:00:00'}, now)
    assert not gear_tried_recently({}, now)
    assert not gear_tried_recently(None, now)


def test_ships_come_in_a_few_at_a_time():
    slots = [Slot(1, 'main', index, name, 100) for index, name in enumerate(['Gangut', 'Kansas', 'Hiei'])]
    plans = [(slot, [target(name, level=95)]) for slot, name in zip(slots, ['Tirpitz', 'Kirishima', 'Langley II'])]
    leveling = leveling_with()

    assert Leveling.leveling_execute(leveling, plans, slots, [], limit=2) == 2
    assert [args[1].ship for args, _ in leveling.fleet_swap.call_args_list] == ['Tirpitz', 'Kirishima']


def test_ships_of_the_fleets_get_gear_at_10():
    slots = mob_fleet()
    # Maury has an auxiliary at +6 and no augment, the others are at +10
    records = {id(slot): {'name': slot.name, 'gear_levels': GEARED[:5], 'augment_level': 10} for slot in slots}
    records[id(slots[3])].update(gear_levels=[11, 10, 11, 13, 6], augment_level=0, hull='DD')
    leveling = SimpleNamespace(leveling_member=Mock(return_value=([11, 10, 11, 13, 10, 10], None)))

    assert Leveling.leveling_gear_members(leveling, slots, records) == 1
    # Her hull type tells her auxiliaries
    assert leveling.leveling_member.call_args == call(slots[3], gear=True, hull='DD')
    assert records[id(slots[3])]['gear_levels'] == [11, 10, 11, 13, 10]
    assert records[id(slots[3])]['augment_level'] == 10
    # Not again the same day
    assert Leveling.leveling_gear_members(leveling, slots, records) == 0


def test_a_fleet_below_its_minimum_has_its_lowest_ships_raised():
    slots = mob_fleet()
    for slot, level in zip(slots[3:], [70, 72, 91]):
        slot.level = level
    records = {id(slot): {'name': slot.name, 'level': slot.level} for slot in slots}
    leveling = SimpleNamespace(leveling_member=Mock(side_effect=lambda slot, level=None: (None, level)))

    # Average 85.7, the minimum went up to 95: 56 levels short, Maury goes to 100, Ping Hai to 98
    assert Leveling.leveling_boost_members(leveling, slots, 95, records) == 2
    assert leveling.leveling_member.call_args_list == [call(slots[3], level=100), call(slots[4], level=98)]
    assert slots[3].level == 100 and records[id(slots[3])]['level'] == 100
    assert slots[4].level == 98


def test_spare_packs_take_a_ship_coming_in_to_100():
    slots = mob_fleet()
    plans = [(slots[5], [target('Isokaze')])]
    ships = [{'name': 'Isokaze', 'index': 0, 'level': 70}]
    # Storage spares the packs to 100
    leveling = leveling_with(prepare=lambda target, gear=True, level=None, spare=False: (None, 100 if spare else None))

    assert Leveling.leveling_execute(leveling, plans, slots, ships, minimum=95, use_packs=True) == 1
    assert (slots[5].name, slots[5].level) == ('Isokaze', 100)
    assert ships[0]['level'] == 100 and ships[0]['exp_next'] == 0


def test_no_spare_packs_without_their_use():
    slots = mob_fleet()
    plans = [(slots[5], [target('Isokaze')])]
    leveling = leveling_with()

    assert Leveling.leveling_execute(leveling, plans, slots, [], minimum=85) == 1
    leveling.ship_prepare.assert_not_called()


def test_without_gear_transfer_a_ship_without_gear_stays_out(monkeypatch):
    monkeypatch.setattr('module.leveling.leveling.GEAR_TRANSFER', False)
    slots = [Slot(1, 'vanguard', 0, 'Hasty', 73)]
    plans = [(slots[0], [target('Isokaze', geared=False), target('Hamakaze')])]
    leveling = leveling_with()

    assert Leveling.leveling_execute(leveling, plans, slots, []) == 1
    assert [args[1].ship for args, _ in leveling.fleet_swap.call_args_list] == ['Hamakaze']
    leveling.ship_prepare.assert_not_called()
