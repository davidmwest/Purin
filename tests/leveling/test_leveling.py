from types import SimpleNamespace
from unittest.mock import Mock, call

from module.leveling.leveling import Leveling
from module.leveling.plan import Slot, Target


def target(name):
    return Target(name=name, ship=name, hull='DD', row='vanguard', rarity='Elite', level=70, goal=120, chips=1320,
                  arrays=0, gain=0, tech=0, score=0.5, priority=1, geared=True, index=0)


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


def test_a_vanguard_takes_one_passenger():
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
