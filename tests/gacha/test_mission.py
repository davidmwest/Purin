import json
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from module.exception import GameNotRunningError, ScriptError
from module.gacha import mission
from module.gacha.gacha_reward import RewardGacha
from module.gacha.mission import (
    GachaMissionObserver, choose_mission_pool, load_mission_state,
    mission_state_path, remaining_mission_builds, save_mission_state)
from module.ui.page import page_build, page_main, page_mission


class ImmediateTimer:
    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        return self

    def reset(self):
        return self

    def reached(self):
        return True


def controller():
    helper = GachaMissionObserver.__new__(GachaMissionObserver)
    helper.config = SimpleNamespace(config_name='f2p', Gacha_FallbackPool='light')
    helper.device = SimpleNamespace(click=Mock(), screenshot=Mock())
    helper.ui_get_current_page = Mock()
    helper._capture_mission = Mock()
    return helper


@pytest.mark.parametrize('current,remaining', [(0, 3), (1, 2), (2, 1), (3, 0)])
def test_already_credited_builds_reduce_daily_three(current, remaining):
    assert remaining_mission_builds(current, 3) == remaining


@pytest.mark.parametrize('current,total', [(None, 3), (1, 1), (4, 3), (-1, 3), ('1', 3), (True, 3)])
def test_unknown_or_different_counter_cannot_authorize_orders(current, total):
    assert remaining_mission_builds(current, total) is None


@pytest.mark.parametrize('roster,owned,expected', [
    (None, ['Old ship'], 'event'), ([], ['Old ship'], 'event'),
    (['New ship', 'Other ship'], ['New ship'], 'event'),
    (['New ship'], None, 'event'), (['New ship'], ['New ship'], 'light')])
def test_only_positive_complete_current_banner_ownership_allows_fallback(roster, owned, expected):
    assert choose_mission_pool(roster, owned) == expected


def test_completed_banner_fallback_is_configurable_and_validated():
    assert choose_mission_pool(['New ship'], ['New ship'], 'special') == 'special'
    with pytest.raises(ScriptError):
        choose_mission_pool(['New ship'], ['New ship'], 'wishing_well')


def test_budget_is_durable_before_navigation_and_reused_without_actions(tmp_path, monkeypatch):
    helper = controller()
    monkeypatch.setattr(mission, 'ensure_event_page', Mock(return_value=True))

    def mission_tab(record, name, upper):
        saved = load_mission_state('f2p', tmp_path)
        assert next(iter(saved.values()))['status'] == 'observing'
        record['mission_tabs'].append(name)
        return True

    helper._observe_mission_tab = Mock(side_effect=mission_tab)
    helper._observe_event_pool = Mock(return_value=True)
    result = helper.run(folder=tmp_path)
    assert result['status'] == 'observed'
    assert result['mission_tabs'] == ['daily', 'event']
    assert result['orders_submitted'] == 0 and result['mission_credit'] is None
    assert result['preferred_pool'] == 'event' and result['banner_ownership_verified'] is False
    assert result['returned_main'] is True
    helper._observe_mission_tab.assert_has_calls([call(result, 'daily', 4), call(result, 'event', 6)])
    again = helper.run(folder=tmp_path)
    assert again['reused'] is True
    assert helper._observe_mission_tab.call_count == 2
    assert helper._observe_event_pool.call_count == 1
    helper.device.click.assert_not_called()


def test_closed_game_does_not_spend_observation_budget(tmp_path):
    helper = controller()
    helper.ui_get_current_page.side_effect = GameNotRunningError
    with pytest.raises(GameNotRunningError):
        helper.run(folder=tmp_path)
    assert load_mission_state('f2p', tmp_path) == {}


def test_interrupted_budget_does_not_repeat_navigation(tmp_path):
    helper = controller()
    key = f'{(mission.datetime.now() - mission.server_time_offset()).date().isoformat()}:{mission.OBSERVATION_VERSION}'
    save_mission_state('f2p', {key: {'status': 'observing'}}, tmp_path)
    helper._observe_mission_tab = Mock()
    result = helper.run(folder=tmp_path)
    assert result['reason'] == 'previous_observation_interrupted'
    helper._observe_mission_tab.assert_not_called()


def test_missing_mission_tab_stops_before_pool_and_returns_recognized_main(tmp_path, monkeypatch):
    helper = controller()
    monkeypatch.setattr(mission, 'ensure_event_page', Mock(return_value=True))
    helper._observe_mission_tab = Mock(return_value=False)
    helper._observe_event_pool = Mock()
    result = helper.run(folder=tmp_path)
    assert result['status'] == 'blocked' and result['reason'] == 'daily_mission_tab_unrecognized'
    assert result['returned_main'] is True and result['orders_submitted'] == 0
    helper._observe_event_pool.assert_not_called()
    mission.ensure_event_page.assert_called_once_with(helper, page_main)


def test_unknown_home_return_is_not_reported_success(tmp_path, monkeypatch):
    helper = controller()
    monkeypatch.setattr(mission, 'ensure_event_page', Mock(return_value=False))
    helper._observe_mission_tab = Mock(return_value=True)
    helper._observe_event_pool = Mock(return_value=True)
    result = helper.run(folder=tmp_path)
    assert result['status'] == 'blocked' and result['reason'] == 'main_return_unrecognized'


def test_missing_event_pool_does_not_switch_to_light(monkeypatch):
    helper = controller()
    monkeypatch.setattr(mission, 'ensure_event_page', Mock(return_value=True))
    helper.gacha_side_navbar_ensure = Mock(return_value=True)
    helper._wait_mission_page = Mock(return_value=True)
    helper._construct_bottom_navbar = SimpleNamespace(get_total=Mock(return_value=3), set=Mock())
    helper.gacha_goto_pool = Mock()
    record = dict(diagnostics=[])
    assert helper._observe_event_pool(record) is False
    assert record['event_pool_available'] is False
    helper._construct_bottom_navbar.set.assert_not_called()
    helper.gacha_goto_pool.assert_not_called()
    helper.device.click.assert_not_called()


def test_event_pool_uses_verified_navigation_only_and_no_order_action(monkeypatch):
    helper = controller()
    monkeypatch.setattr(mission, 'ensure_event_page', Mock(return_value=True))
    helper.gacha_side_navbar_ensure = Mock(return_value=True)
    helper._wait_mission_page = Mock(return_value=True)
    helper._construct_bottom_navbar = SimpleNamespace(
        get_total=Mock(return_value=4), set=Mock(return_value=True))
    record = dict(diagnostics=[])
    assert helper._observe_event_pool(record) is True
    assert record['event_pool_available'] is True
    helper.gacha_side_navbar_ensure.assert_called_once_with(upper=1)
    helper._construct_bottom_navbar.set.assert_called_once_with(helper, left=1)
    helper.device.click.assert_not_called()
    helper._capture_mission.assert_has_calls([
        call(record, 'build_pool_before_selection'), call(record, 'event_build_pool')])


def test_navbar_failure_does_not_capture_wrong_mission_tab(monkeypatch):
    helper = controller()
    monkeypatch.setattr(mission, 'ensure_event_page', Mock(return_value=True))
    helper.reward_side_navbar_ensure = Mock(return_value=False)
    helper._wait_mission_page = Mock()
    assert helper._observe_mission_tab(dict(mission_tabs=[]), 'daily', 4) is False
    helper._wait_mission_page.assert_not_called()
    helper._capture_mission.assert_not_called()


def test_fresh_page_and_navbar_must_settle_before_capture(monkeypatch):
    helper = controller()
    monkeypatch.setattr(mission, 'Timer', ImmediateTimer)
    helper.loop = Mock(return_value=iter(range(4)))
    helper.ui_page_appear = Mock(side_effect=[False, True, True, True])
    predicate = Mock(side_effect=[False, True, True])
    assert helper._wait_mission_page(page_mission, predicate) is True
    assert helper.ui_page_appear.call_count == 4
    helper.loop.assert_called_once_with(skip_first=False, timeout=8)


@pytest.mark.parametrize('contents', ['not JSON', '[]', '{"day": 3}'])
def test_corrupt_observation_state_fails_closed(tmp_path, contents):
    with open(mission_state_path('f2p', tmp_path), 'w', encoding='utf-8') as stream:
        stream.write(contents)
    with pytest.raises(ScriptError):
        load_mission_state('f2p', tmp_path)


def test_invalid_account_cannot_escape_state_folder(tmp_path):
    with pytest.raises(ScriptError):
        mission_state_path('../alas', tmp_path)


def test_default_mission_mode_bypasses_legacy_fixed_light_orders(monkeypatch):
    helper = RewardGacha.__new__(RewardGacha)
    helper.config = SimpleNamespace(task_delay=Mock())
    helper.device = object()
    helper.gacha_run = Mock()
    observer = SimpleNamespace(run=Mock(return_value={'orders_submitted': 0}))
    factory = Mock(return_value=observer)
    monkeypatch.setattr(mission, 'GachaMissionObserver', factory)
    helper.run()
    helper.gacha_run.assert_not_called()
    factory.assert_called_once_with(config=helper.config, device=helper.device)
    observer.run.assert_called_once_with()
    helper.config.task_delay.assert_called_once_with(server_update=True)


def test_explicit_legacy_mode_preserves_existing_fixed_order_path():
    helper = RewardGacha.__new__(RewardGacha)
    helper.config = SimpleNamespace(Gacha_MissionDriven=False, task_delay=Mock())
    helper.gacha_run = Mock()
    helper.run()
    helper.gacha_run.assert_called_once_with()
    helper.config.task_delay.assert_called_once_with(server_update=True)


def test_new_schema_defaults_to_mission_mode_and_preserves_legacy_settings():
    from module.config.config_generated import GeneratedConfig
    from module.config.config_updater import ConfigUpdater

    old = {'Gacha': {'Gacha': {'Pool': 'special', 'Amount': 2}}}
    updated = ConfigUpdater().config_update(old)['Gacha']['Gacha']
    assert updated['MissionDriven'] is True and updated['FallbackPool'] == 'light'
    assert (updated['Pool'], updated['Amount']) == ('special', 2)
    assert GeneratedConfig.Gacha_MissionDriven is True
    assert GeneratedConfig.Gacha_FallbackPool == 'light'
    with open('module/config/argument/args.json', encoding='utf-8') as stream:
        arguments = json.load(stream)['Gacha']['Gacha']
    assert arguments['FallbackPool']['option'] == ['light', 'heavy', 'special']
    with open('config/template.json', encoding='utf-8') as stream:
        defaults = json.load(stream)['Gacha']['Gacha']
    assert defaults['MissionDriven'] is True and defaults['FallbackPool'] == 'light'
