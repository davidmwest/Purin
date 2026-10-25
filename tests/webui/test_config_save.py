import ast
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Dict

import pytest

from module.config.deep import deep_get, deep_iter, deep_set
from module.webui.utils import parse_pin_value, re_fullmatch, to_pin_value


@pytest.fixture
def save_config():
    # Exercise the GUI's real save method without importing app.py, which replaces
    # PIL globally and installs GUI process/thread patches at import time.
    path = Path(__file__).resolve().parents[2] / 'module/webui/app.py'
    tree = ast.parse(path.read_text())
    gui = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'AlasGUI')
    method = next(node for node in gui.body if isinstance(node, ast.FunctionDef) and node.name == '_save_config')
    errors = []
    namespace = {
        'Dict': Dict,
        'AzurLaneConfig': object,
        'State': SimpleNamespace(config_updater=None),
        'datetime': datetime,
        'deep_get': deep_get,
        'deep_iter': deep_iter,
        'deep_set': deep_set,
        'parse_pin_value': parse_pin_value,
        're_fullmatch': re_fullmatch,
        'to_pin_value': to_pin_value,
        'pin': {},
        'toast': lambda *args, **kwargs: None,
        't': lambda value: value,
        'filepath_config': lambda name: name,
        'dict_to_kv': str,
        'logger': SimpleNamespace(info=lambda *args: None, warning=lambda *args: None,
                                  exception=errors.append),
    }
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), 'exec'), namespace)
    return namespace['_save_config'], errors


def make_gui(task, group, key, default):
    return SimpleNamespace(
        ALAS_ARGS={task: {group: {key: {'value': default}}}},
        pin_remove_invalid_mark=lambda keys: None,
        pin_set_invalid_mark=lambda keys: None,
    )


@pytest.mark.parametrize('value', [4, 0, False, None, '', '2026-10-24 23:00:00'])
def test_unrelated_run_values_do_not_block_saving_settings(save_config, value):
    save, errors = save_config
    config = {
        'WarArchivesComplete': {'Archive': {'StagesPerRun': value}},
        'Event': {'StopCondition': {'OilLimit': 10000}},
    }
    writes = []
    updater = SimpleNamespace(read_file=lambda name: config, save_callback=lambda key, value: (),
                              write_file=lambda name, data: writes.append((name, data)))

    save(make_gui('Event', 'StopCondition', 'OilLimit', 500),
         {'Event.StopCondition.OilLimit': '5000'}, 'alas', updater)

    assert errors == []
    assert writes == [('alas', config)]
    assert config['Event']['StopCondition']['OilLimit'] == 5000
    assert config['WarArchivesComplete']['Archive']['StagesPerRun'] == value


def test_clear_next_run_still_saves_with_integer_run_limits_and_distant_dates(save_config):
    save, errors = save_config
    now = datetime.now()
    near = now + timedelta(days=30)
    default = datetime(2020, 1, 1)
    config = {
        'Research': {'Scheduler': {'NextRun': now + timedelta(hours=5)}},
        'Event': {'Scheduler': {'NextRun': near}},
        'DisabledTask': {'Scheduler': {'NextRun': now + timedelta(days=32)}},
        'EventStars': {'StarPass': {'StagesPerRun': 2}},
    }
    writes = []
    updater = SimpleNamespace(read_file=lambda name: config, save_callback=lambda key, value: (),
                              write_file=lambda name, data: writes.append((name, data)))

    save(make_gui('Research', 'Scheduler', 'NextRun', default),
         {'Research.Scheduler.NextRun': ''}, 'alas', updater)

    assert errors == []
    assert writes == [('alas', config)]
    assert config['Research']['Scheduler']['NextRun'] == default
    assert config['Event']['Scheduler']['NextRun'] == near
    assert config['DisabledTask']['Scheduler']['NextRun'] == ''
    assert config['EventStars']['StarPass']['StagesPerRun'] == 2
