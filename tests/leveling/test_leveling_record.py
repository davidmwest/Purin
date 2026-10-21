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
    assert record['minimum'] == 95 and record['trials']['95@12-4']['lost'] == 1
    assert record['losses'][0]['average'] == 95.5


def test_a_battle_lost_below_95_raises_the_minimum(two_steps):
    record = new_record()
    record['minimum'] = 85
    record = new_record()
    record_average(record, 86.8, NOW)

    assert not record_run(record, '12-4', False, NOW)
    assert record_run(record, '12-4', True, NOW + timedelta(minutes=10))

    assert record['minimum'] == 95
    trial = record['trials']['85@12-4']
    assert (trial['runs'], trial['lost'], trial['since'], trial['clean'], trial['held']) == \
           (2, 1, '2026-10-21 08:00:00', 0, None)
    assert record['losses'] == [{'time': '2026-10-21 08:10:00', 'stage': '12-4', 'average': 86.8, 'minimum': 85,
                                 'fleet': ''}]
    # The trial of 95 starts
    assert record['trials']['95@12-4']['runs'] == 0


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
    assert record['trials']['95@12-4']['held'] is None

    record_run(record, '12-4', False, NOW + timedelta(hours=5))

    assert record['trials']['95@12-4']['held'] == '2026-10-21 13:00:00'
    assert record['trials']['95@12-4']['runs'] == TRIAL_RUNS


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
    assert saved['trials']['85@12-4']['runs'] == 2 and saved['trials']['85@12-4']['lost'] == 1


def test_runs_are_not_recorded_without_leveling(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    record_leveling_run(config_of(enabled=False), SimpleNamespace(run_lost=True, run_lost_fleet=1))

    assert not (tmp_path / 'config' / 'leveling' / 'alas.json').exists()


def assessed(record, average, ships):
    record['assessment'] = {'average': average, 'ships': ships, 'runs': 0}


def test_runs_on_other_stages_are_other_trials():
    # The Codex review of 2026-10-21: 19 runs of 2-1 and one of 12-4 held 12-4
    record = new_record()
    record_average(record, 110, NOW)
    for run in range(TRIAL_RUNS - 1):
        record_run(record, '2-1', False, NOW + timedelta(minutes=run))
    record_run(record, '12-4', False, NOW + timedelta(hours=3))

    assert record['average'] == 110
    assert record['trials']['95@2-1']['runs'] == TRIAL_RUNS - 1 and record['trials']['95@2-1']['held'] is None
    assert record['trials']['95@12-4']['runs'] == 1 and record['trials']['95@12-4']['held'] is None


def test_a_trial_keeps_the_averages_measured_and_the_fleets():
    record = new_record()
    fleets = [[1, 'Hakuhou', 97, True], [1, 'Hamakaze', 89, None], [1, 'Isokaze', 102, False], [2, 'Hiei', 109, True]]
    assessed(record, 96.4, fleets)
    for run in range(TRIAL_RUNS - 1):
        record_run(record, '12-4', False, NOW + timedelta(minutes=run))
    assessed(record, 101.3, fleets)

    record_run(record, '12-4', False, NOW + timedelta(hours=3))

    trial = record['trials']['95@12-4']
    assert trial['held'] and (trial['low'], trial['high']) == (96.4, 101.3)
    assert len(trial['recent']) == TRIAL_RUNS
    assert trial['recent'][-1]['average'] == 101.3
    assert trial['recent'][-1]['fleet'] == 'Hakuhou 97, Hamakaze 89, Isokaze 102 no gear / Hiei 109'


def test_a_battle_lost_keeps_the_fleets_then():
    record = new_record()
    assessed(record, 95.0, [[1, 'Hamakaze', 72, True], [1, 'Tanikaze', 78, True]])

    record_run(record, '12-4', True, NOW)

    assert record['losses'][-1]['average'] == 95.0
    assert record['losses'][-1]['fleet'] == 'Hamakaze 72, Tanikaze 78'
    assert record['trials']['95@12-4']['low'] is None


def test_a_trial_of_before_is_kept_as_it_is():
    record = new_record()
    record['trials']['95'] = {'runs': 19, 'lost': 0, 'since': '2026-10-21 00:15:46', 'clean': 19, 'held': None}

    record_run(record, '12-4', False, NOW)

    assert record['trials']['95']['runs'] == 19
    assert record['trials']['95@12-4']['runs'] == 1
