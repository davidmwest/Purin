import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from module.config.config import AzurLaneConfig, LogSummary, log_value
from module.config.utils import dict_to_kv

DAILIES = ['Commission', 'Research', 'Reward']


def make_config(enabled):
    now = datetime.now().replace(microsecond=0)
    data = {task: {'Scheduler': {'Enable': enabled, 'NextRun': now}} for task in DAILIES}
    config = SimpleNamespace(data=data, modified={}, overridden={}, args={task: {} for task in DAILIES})
    config.is_task_enabled = lambda task: data[task]['Scheduler']['Enable']
    return config


def test_override_leaves_disabled_dailies_disabled():
    config = make_config(enabled=False)

    AzurLaneConfig.override(config)

    assert config.modified == {}
    assert [config.is_task_enabled(task) for task in DAILIES] == [False, False, False]


def test_override_still_pulls_in_a_run_scheduled_too_far_ahead():
    config = make_config(enabled=True)
    config.data['Commission']['Scheduler']['NextRun'] = datetime.now() + timedelta(days=3)

    AzurLaneConfig.override(config)

    assert config.data['Commission']['Scheduler']['NextRun'] <= datetime.now()


def test_override_keeps_the_weekly_scan_of_the_dock_a_week_away():
    # Pulled in to now every time the config was read again, the scan of an hour ran back to back
    now = datetime.now().replace(microsecond=0)
    week = now + timedelta(days=7)
    data = {'Inventory': {'Scheduler': {'Enable': True, 'NextRun': week}},
            'Commission': {'Scheduler': {'Enable': True, 'NextRun': week}}}
    config = SimpleNamespace(data=data, modified={}, overridden={}, args={task: {} for task in data})
    config.is_task_enabled = lambda task: data[task]['Scheduler']['Enable']

    AzurLaneConfig.override(config)

    assert data['Inventory']['Scheduler']['NextRun'] == week
    # Other tasks are still kept within a day
    assert data['Commission']['Scheduler']['NextRun'] <= datetime.now()


def test_pause_is_requested_with_a_file_next_to_the_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'config').mkdir()
    config = SimpleNamespace(config_name='alas')

    assert AzurLaneConfig.pause_requested.fget(config) is False

    (tmp_path / 'config' / 'alas.pause').write_text('')

    assert AzurLaneConfig.pause_requested.fget(config) is True


def test_a_running_task_ends_at_its_next_check_when_pause_is_requested():
    def load():
        raise AssertionError('The task queue should not be looked at')

    config = SimpleNamespace(stop_event=None, pause_requested=True, task='Event2', load=load)

    assert AzurLaneConfig.task_switched(config) is True


@pytest.mark.parametrize('task', DAILIES)
def test_dailies_can_be_switched_off(task):
    with open('./module/config/argument/args.json', encoding='utf-8') as f:
        enable = json.load(f)[task]['Scheduler']['Enable']

    assert enable['type'] == 'checkbox'
    assert enable['value'] is True
    assert False in enable['option']


def test_long_values_are_summed_up_in_the_log():
    storage = {'ships': {f'Ship {i}': {'mood': 150, 'sig': list(range(144))} for i in range(30)}}

    text = dict_to_kv({'DormRest.Storage.Storage': log_value(storage), 'Main.Emotion.Fleet1Value': log_value(119)})

    assert isinstance(log_value(storage), LogSummary)
    assert text == "DormRest.Storage.Storage=<dict of 1>, Main.Emotion.Fleet1Value=119"
