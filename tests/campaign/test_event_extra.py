import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from module.campaign import event_extra
from module.campaign.event_extra import EventExtraScout, load_extra_state, save_extra_state
from module.exception import ScriptError
from module.map.assets import FLEET_PREPARATION, MAP_PREPARATION

EVENT = 'event_20261022_cn'


def controller():
    helper = EventExtraScout.__new__(EventExtraScout)
    helper.config = SimpleNamespace(config_name='f2p', STAGE_ENTRANCE=['old'], override=Mock())
    helper.device = SimpleNamespace(screenshot=Mock(), click=Mock(), image='screen')
    helper._extra_capture = Mock()
    helper._observe_rewards = Mock(return_value={'status': 'observed', 'diagnostics': ['/tmp/event_list.png']})
    return helper


def test_scout_budget_is_persisted_before_any_navigation_and_is_not_a_combat_attempt(tmp_path):
    helper = controller()

    def inspect(record):
        stored = load_extra_state('f2p', folder=tmp_path)[EVENT]
        assert stored['status'] == 'inspecting'
        assert stored['combat_attempted'] is False
        helper._observe_rewards.assert_called_once_with(EVENT)
        return 'observed', 'fleet_preparation_requires_supported_runner'

    helper._inspect_extra = Mock(side_effect=inspect)
    first = helper.run(EVENT, folder=tmp_path)
    second = helper.run(EVENT, folder=tmp_path)

    assert first['status'] == second['status'] == 'observed'
    assert first['combat_attempted'] is second['combat_attempted'] is False
    assert second['reused'] is True
    helper._inspect_extra.assert_called_once()
    helper._observe_rewards.assert_called_once_with(EVENT)
    assert first['diagnostics'] == ['/tmp/event_list.png']
    assert helper.config.override.call_args_list[-1].kwargs == {'STAGE_ENTRANCE': ['old']}


def test_interrupted_scout_is_not_automatically_retried(tmp_path):
    save_extra_state('f2p', {EVENT: {'status': 'inspecting', 'combat_attempted': False}}, folder=tmp_path)
    helper = controller()
    helper._inspect_extra = Mock()
    result = helper.run(EVENT, folder=tmp_path)
    assert result['status'] == 'blocked'
    assert result['reason'] == 'previous_inspection_interrupted'
    helper._inspect_extra.assert_not_called()
    helper.device.screenshot.assert_not_called()


def test_scout_exception_keeps_the_inspection_spent(tmp_path):
    helper = controller()
    helper._inspect_extra = Mock(side_effect=RuntimeError('unknown page'))
    with pytest.raises(RuntimeError, match='unknown page'):
        helper.run(EVENT, folder=tmp_path)
    state = load_extra_state('f2p', folder=tmp_path)[EVENT]
    assert state['status'] == 'blocked'
    assert state['combat_attempted'] is False
    helper._inspect_extra.reset_mock()
    assert helper.run(EVENT, folder=tmp_path)['reused'] is True
    helper._inspect_extra.assert_not_called()


@pytest.mark.parametrize('contents', ['not json', '[]', '{"event": 1}'])
def test_invalid_budget_fails_closed_without_navigation(tmp_path, contents):
    (tmp_path / 'f2p-ex.json').write_text(contents)
    helper = controller()
    helper._inspect_extra = Mock()
    with pytest.raises(ScriptError):
        helper.run(EVENT, folder=tmp_path)
    helper._inspect_extra.assert_not_called()


def test_unsupported_event_has_no_budget_or_device_actions(tmp_path):
    helper = controller()
    helper._inspect_extra = Mock()
    assert helper.run('event_unknown', folder=tmp_path)['status'] == 'unsupported'
    assert list(tmp_path.iterdir()) == []
    helper._inspect_extra.assert_not_called()


def test_preparation_is_captured_but_no_fleet_or_battle_start_is_tapped(monkeypatch):
    helper = controller()
    monkeypatch.setattr(event_extra, 'ensure_event_page', Mock(return_value=True))
    monkeypatch.setattr(event_extra, 'ensure_event_switch', Mock(return_value=True))
    entrance = SimpleNamespace(name='extra')
    helper.stage_entrance = {'extra': entrance}
    helper.campaign_chapter = 'ex_ex'
    helper._get_stage_name = Mock()
    helper.loop = Mock(side_effect=[iter(range(1)), iter(range(3))])
    page = iter(['map', 'fleet'])
    current = ['map']

    def appear(button, **kwargs):
        if button is MAP_PREPARATION:
            return current[0] == 'map'
        if button is FLEET_PREPARATION:
            return current[0] == 'fleet'
        return False

    def click(button):
        if button is MAP_PREPARATION:
            current[0] = 'fleet'

    helper.appear = Mock(side_effect=appear)
    helper.device.click.side_effect = click
    record = {'diagnostics': []}

    assert helper._inspect_extra(record) == ('observed', 'fleet_preparation_requires_supported_runner')
    assert [call.args[0] for call in helper.device.click.call_args_list] == [entrance, MAP_PREPARATION]
    assert record['entrance'] == 'extra'
    assert helper._extra_capture.call_args_list[-1].args[-1] == 'fleet_preparation'


def test_unrecognized_ex_entrance_is_never_tapped(monkeypatch):
    helper = controller()
    monkeypatch.setattr(event_extra, 'ensure_event_page', Mock(return_value=True))
    monkeypatch.setattr(event_extra, 'ensure_event_switch', Mock(return_value=True))
    helper.stage_entrance = {'sp': SimpleNamespace(name='sp')}
    helper.campaign_chapter = 'ex_sp'
    helper._get_stage_name = Mock()
    helper.loop = Mock(return_value=iter(range(3)))
    assert helper._inspect_extra({'diagnostics': []}) == ('blocked', 'ex_entrance_unrecognized')
    helper.device.click.assert_not_called()
