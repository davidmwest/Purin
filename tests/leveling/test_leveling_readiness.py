from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from module.campaign.run import CampaignRun
from module.config.config import TaskEnd
from module.leveling.leveling import Leveling
from module.leveling.plan import Slot, gear_record
from module.leveling.readiness import BLOCKED, READY, RECOVERY_MINUTES, RUNS_PER_ASSESSMENT, STALE, assess_fleets, \
    main_readiness, readiness
from module.leveling.record import TIME_FORMAT, new_record, record_leveling_run

NOW = datetime(2026, 10, 21, 14, 0, 0)
FLEETS = ([1], [2])


def fleet(fleet, *levels, names=None):
    names = names or [f'Ship {fleet}{index}' for index in range(len(levels))]
    return [Slot(fleet, 'main' if index < 3 else 'vanguard', index % 3, name, level)
            for index, (name, level) in enumerate(zip(names, levels))]


def assessed(mob, boss, minimum=95, now=NOW, problems=()):
    record = new_record()
    record['minimum'] = minimum
    record['assessment'] = assess_fleets(mob, boss, minimum, fleets=FLEETS, stage='12-4', problems=problems, now=now)
    return record


def test_fleets_that_keep_the_rules():
    record = assessed(fleet(1, 100, 99, 101, 100, 97, 78), fleet(2, 110, 109, 103, 119, 110, 100))

    assert record['assessment']['ok'] and record['assessment']['reasons'] == []
    assert record['assessment']['average'] == 95.8
    assert readiness(record, FLEETS, NOW + timedelta(minutes=5)) == (READY, '')


def test_rules_broken_hold_main():
    # The Codex review of 2026-10-21: an average of 78.3 against 95, a ship at 120, packs that did not raise anyone
    mob = fleet(1, 70, 70, 70, 70, 70, 120)
    boss = fleet(2, 110, 109, 103, 119, 125, 77)
    record = assessed(mob, boss, problems=['EXP Data Packs did not raise Ship 10 to level 100'])

    state, reason = readiness(record, FLEETS, NOW)
    assert state == BLOCKED
    assert 'average level 78.3, below its minimum 95' in reason
    assert 'Ship 15 Lv.120 is in fleet 1' in reason and 'Ship 24 Lv.125 is in fleet 2' in reason
    assert 'Ship 25 Lv.77 is in the boss fleet 2, below 100' in reason
    # What kept Leveling from repairing them is said too
    assert 'EXP Data Packs did not raise Ship 10 to level 100' in reason


def test_problems_are_said_only_with_rules_broken():
    record = assessed(fleet(1, 100, 100, 100, 100, 100, 100), fleet(2, 110), problems=['no inventory of the dock'])

    assert record['assessment']['ok'] and record['assessment']['reasons'] == []


@pytest.mark.parametrize('change, reason', [
    (lambda record: record.pop('assessment'), 'not looked at yet'),
    (lambda record: record['assessment'].update(fleets=[[3], [2]]), 'were looked at, not'),
    (lambda record: record.update(minimum=105), 'minimum average went from 95 to 105'),
    (lambda record: record['assessment'].update(time=(NOW - timedelta(hours=5)).strftime(TIME_FORMAT)), 'ago'),
    (lambda record: record['assessment'].update(runs=RUNS_PER_ASSESSMENT), f'{RUNS_PER_ASSESSMENT} runs ago'),
    (lambda record: record['assessment'].update(version=0), 'other rules'),
])
def test_fleets_not_looked_at_lately_are_looked_at_first(change, reason):
    record = assessed(fleet(1, 100, 100, 100, 100, 100, 100), fleet(2, 110))
    change(record)

    state, why = readiness(record, FLEETS, NOW)
    assert state == STALE and reason in why


def test_a_ship_near_120_is_looked_at_after_each_run():
    record = assessed(fleet(1, 100, 100, 100, 100, 100, 118), fleet(2, 110))
    assert record['assessment']['near_cap']
    assert readiness(record, FLEETS, NOW)[0] == READY

    record['assessment']['runs'] = 1
    assert readiness(record, FLEETS, NOW)[0] == STALE


def test_main_runs_without_leveling():
    config = SimpleNamespace(config_name='alas', cross_get=lambda key, default=None: {
        'Leveling.Scheduler.Enable': False}.get(key, default))

    assert main_readiness(config) == (READY, '')


def test_runs_of_main_age_the_assessment(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from module.leveling.record import load_record, save_record
    record = assessed(fleet(1, 100, 100, 100, 100, 100, 100), fleet(2, 110))
    save_record('alas', record)
    config = SimpleNamespace(config_name='alas', Campaign_Name='12-4',
                             cross_get=lambda key, default=None: {'Leveling.Scheduler.Enable': True}.get(key, default))

    record_leveling_run(config, SimpleNamespace(run_lost=False, run_lost_fleet=None))
    record_leveling_run(config, SimpleNamespace(run_lost=False, run_lost_fleet=None))

    assert load_record('alas')['assessment']['runs'] == 2


def gate_on(state, reason='', leveling_next=None):
    config = SimpleNamespace(task_call=Mock(), task_delay=Mock(), task_stop=Mock(side_effect=TaskEnd),
                             cross_get=lambda key, default=None: {
                                 'Leveling.Scheduler.NextRun': leveling_next}.get(key, default))
    run = SimpleNamespace(config=config, campaign=SimpleNamespace(ensure_auto_search_exit=Mock()))
    return run, config


def test_main_calls_leveling_for_fleets_not_looked_at(monkeypatch):
    monkeypatch.setattr('module.campaign.run.main_readiness', lambda config: (STALE, 'not looked at yet'))
    run, config = gate_on(STALE)

    with pytest.raises(TaskEnd):
        CampaignRun.leveling_gate(run)
    config.task_call.assert_called_once_with('Leveling')
    config.task_delay.assert_not_called()
    run.campaign.ensure_auto_search_exit.assert_called_once()


def test_main_holds_until_leveling_runs_again(monkeypatch):
    monkeypatch.setattr('module.campaign.run.main_readiness', lambda config: (BLOCKED, 'average 78.3 below 95'))
    leveling_next = datetime.now() + timedelta(minutes=RECOVERY_MINUTES)
    run, config = gate_on(BLOCKED, leveling_next=leveling_next)

    with pytest.raises(TaskEnd):
        CampaignRun.leveling_gate(run)
    config.task_call.assert_not_called()
    assert config.task_delay.call_args[1]['target'] == leveling_next + timedelta(minutes=1)


def test_main_runs_with_fleets_that_keep_the_rules(monkeypatch):
    monkeypatch.setattr('module.campaign.run.main_readiness', lambda config: (READY, ''))
    run, config = gate_on(READY)

    CampaignRun.leveling_gate(run)
    config.task_stop.assert_not_called()


def leveling_on(slots, ships, record, use_packs=True, interval=180):
    leveling = Leveling.__new__(Leveling)
    leveling.config = SimpleNamespace(config_name='alas', Leveling_ExpDataPacks=use_packs, Leveling_UltraTo125=False,
                                      Leveling_CheckInterval=interval, task_delay=Mock(), task_call=Mock(),
                                      cross_get=lambda key, default=None: {'Main.Campaign.Name': '12-4'}.get(key, default))
    leveling.ui_ensure = Mock()
    leveling.leveling_fleets = lambda: FLEETS
    leveling.leveling_read = lambda fleets: slots
    leveling.leveling_log_queue = Mock()
    leveling.leveling_gear_members = Mock()
    # Packs raise no one: storage ran out
    leveling.leveling_member = Mock(return_value=(None, None))
    leveling.formation_goto_fleet = Mock()
    leveling.fleet_swap = Mock(return_value=True)
    leveling.ship_prepare = Mock(return_value=(None, None))
    leveling.leveling_execute = lambda *args, **kwargs: Leveling.leveling_execute(leveling, *args, **kwargs)
    leveling.leveling_boost_members = lambda *args, **kwargs: Leveling.leveling_boost_members(
        leveling, *args, **kwargs)
    return leveling


@pytest.fixture
def stored(monkeypatch):
    saved = {}
    monkeypatch.setattr('module.leveling.leveling.save_record', lambda name, record: saved.update(record=record))
    monkeypatch.setattr('module.leveling.leveling.save_inventory', Mock())
    return saved


def test_leveling_that_can_not_repair_the_fleets_holds_main(monkeypatch, stored):
    # The Codex review of 2026-10-21, a fleet at 78.3 against 95 with a ship at 120, no ship free, no packs left
    names = ['Hakuhou', 'Gangut', 'Kansas', 'Maury (Retrofit)', 'Ping Hai', 'Nicholas (Retrofit)']
    slots = fleet(1, 70, 70, 70, 70, 70, 120, names=names)
    ships = [dict(name=slot.name, level=slot.level, index=index, **gear_record([10] * 6, checked=True))
             for index, slot in enumerate(slots)]
    record = new_record()
    monkeypatch.setattr('module.leveling.leveling.load_inventory', lambda name: {'ships': ships})
    monkeypatch.setattr('module.leveling.leveling.load_record', lambda name: record)
    leveling = leveling_on(slots, ships, record)

    Leveling.run(leveling)

    assessment = stored['record']['assessment']
    assert not assessment['ok']
    assert any('below its minimum 95' in reason for reason in assessment['reasons'])
    assert any('Nicholas (Retrofit) Lv.120' in reason for reason in assessment['reasons'])
    assert any('EXP Data Packs did not raise' in reason for reason in assessment['reasons'])
    # Leveling comes back soon, Main holds meanwhile
    leveling.config.task_delay.assert_called_once_with(minute=RECOVERY_MINUTES)
    assert readiness(stored['record'], FLEETS)[0] == BLOCKED


def test_leveling_without_an_inventory_still_looks_at_the_fleets(monkeypatch, stored):
    slots = fleet(1, 70, 70, 70, 70, 70, 120)
    monkeypatch.setattr('module.leveling.leveling.load_inventory', lambda name: None)
    monkeypatch.setattr('module.leveling.leveling.load_record', lambda name: new_record())
    leveling = leveling_on(slots, [], None)

    Leveling.run(leveling)

    assessment = stored['record']['assessment']
    assert not assessment['ok']
    assert any('no inventory of the dock' in reason for reason in assessment['reasons'])
    # The Inventory task reads the dock, Leveling comes back soon
    leveling.config.task_call.assert_called_once_with('Inventory')
    leveling.config.task_delay.assert_called_once_with(minute=RECOVERY_MINUTES)

    # Fleets that keep the rules need no inventory
    slots = fleet(1, 100, 100, 100, 100, 100, 100)
    leveling = leveling_on(slots, [], None)
    Leveling.run(leveling)
    assert stored['record']['assessment']['ok']
    leveling.config.task_call.assert_not_called()
    leveling.config.task_delay.assert_called_once_with(minute=180)
