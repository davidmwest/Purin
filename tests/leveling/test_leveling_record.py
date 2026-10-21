import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from module.leveling.record import TRIAL_RUNS, load_record, new_record, record_average, record_leveling_run, \
    record_run, save_record

NOW = datetime(2026, 10, 21, 8, 0, 0)


@pytest.fixture
def two_steps(monkeypatch):
    # How the minimum goes up, with the steps the user gave first
    monkeypatch.setattr('module.leveling.record.AVERAGE_STEPS', (85, 95))


def test_a_new_record_starts_at_95(tmp_path, monkeypatch):
    # 86.8 was tried and lost a run in three, the user said to go straight to 95
    monkeypatch.chdir(tmp_path)
    record = load_record('alas')

    assert record['minimum'] == 95 and record['losses'] == [] and record['average'] is None
    assert [failed['average'] for failed in record['failed']] == [86.8]
    # A file that can not be read starts again
    (tmp_path / 'config' / 'leveling').mkdir(parents=True)
    (tmp_path / 'config' / 'leveling' / 'alas.json').write_text('{oops')
    assert load_record('alas')['minimum'] == 95


def test_a_battle_lost_at_the_last_minimum_is_only_recorded():
    record = new_record()
    record_average(record, 95.5, NOW)

    assert not record_run(record, '12-4', True, NOW)
    assert record['minimum'] == 95 and record['trials']['95']['lost'] == 1
    assert record['losses'][0]['average'] == 95.5


def test_a_battle_lost_below_95_raises_the_minimum(two_steps):
    record = new_record()
    record['minimum'] = 85
    record = new_record()
    record_average(record, 86.8, NOW)

    assert not record_run(record, '12-4', False, NOW)
    assert record_run(record, '12-4', True, NOW + timedelta(minutes=10))

    assert record['minimum'] == 95
    assert record['trials']['85'] == {'runs': 2, 'lost': 1, 'since': '2026-10-21 08:00:00', 'clean': 0, 'held': None}
    assert record['losses'] == [{'time': '2026-10-21 08:10:00', 'stage': '12-4', 'average': 86.8, 'minimum': 85}]
    # The trial of 95 starts
    assert record['trials']['95']['runs'] == 0


def test_a_battle_lost_above_the_next_step_keeps_the_minimum(two_steps):
    # The minimum was 85 and the fleet was at 96: 95 would not have saved it
    record = new_record()
    record['minimum'] = 85
    record_average(record, 96.2, NOW)

    assert not record_run(record, '12-4', True, NOW)
    assert record['minimum'] == 85


def test_an_average_holds_after_runs_without_a_battle_lost():
    record = new_record()
    record_average(record, 95.3, NOW)
    for run in range(TRIAL_RUNS - 1):
        record_run(record, '12-4', False, NOW + timedelta(minutes=run))
    assert record['trials']['95']['held'] is None

    record_run(record, '12-4', False, NOW + timedelta(hours=5))

    assert record['trials']['95']['held'] == '2026-10-21 13:00:00'
    assert record['trials']['95']['runs'] == TRIAL_RUNS


def config_of(enabled=True):
    values = {'Leveling.Scheduler.Enable': enabled}
    return SimpleNamespace(config_name='alas', Campaign_Name='12-4',
                           cross_get=lambda keys, default=None: values.get(keys, default))


def test_runs_of_main_go_into_the_record(tmp_path, monkeypatch, two_steps):
    monkeypatch.chdir(tmp_path)
    record = new_record()
    record['minimum'] = 85
    record_average(record, 86.8, NOW)
    save_record('alas', record)

    # A battle of the boss fleet lost does not count against the mob fleet
    record_leveling_run(config_of(), SimpleNamespace(run_lost=True, run_lost_fleet=2))
    assert load_record('alas')['minimum'] == 85
    record_leveling_run(config_of(), SimpleNamespace(run_lost=True, run_lost_fleet=1))

    saved = json.loads((tmp_path / 'config' / 'leveling' / 'alas.json').read_text())
    assert saved['minimum'] == 95
    assert saved['trials']['85']['runs'] == 2 and saved['trials']['85']['lost'] == 1


def test_runs_are_not_recorded_without_leveling(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    record_leveling_run(config_of(enabled=False), SimpleNamespace(run_lost=True, run_lost_fleet=1))

    assert not (tmp_path / 'config' / 'leveling' / 'alas.json').exists()
