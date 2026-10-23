import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest
import numpy as np
from PIL import Image

from module.exception import GameNotRunningError, GamePageUnknownError, ScriptError
from module.gacha import mission
from module.gacha.gacha_reward import RewardGacha
from module.gacha.mission import (
    GachaMissionObserver, GachaMissionOrderObserver, choose_mission_pool, load_mission_state,
    locate_build_mission, parse_build_counter, read_build_mission,
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
    monkeypatch.setattr(mission, 'GachaMissionOrderObserver', Mock(return_value=SimpleNamespace(
        run=Mock(return_value={'build_open_clicked': False}))))
    monkeypatch.setitem(sys.modules, 'module.gacha.banner', SimpleNamespace(
        EventBannerObserver=Mock(return_value=SimpleNamespace(
            run=Mock(return_value={'returned_main': True})))))
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


def mission_image(name='main'):
    return np.asarray(Image.open(f'tests/gacha/data/build_three_{name}.png').convert('RGB')).copy()


@pytest.mark.parametrize('name,current', [('main', 0), ('f2p', 0)])
def test_actual_account_build_three_rows_are_read_without_digitcounter_clamping(name, current):
    row = read_build_mission(mission_image(name))
    assert row['current'] == current and row['total'] == 3
    assert 87 <= row['row_top'] < 550
    assert row['title_area'][0] == 257


@pytest.mark.parametrize('text,expected', [
    ('0/3', 0), ('1 / 3', 1), ('2/3', 2), ('3/3', 3), ('D/3', 0),
    ('I/3', 1), ('3/10', None), ('4/3', None), ('13', None), ('03/3', None),
    ('progress 0/3', None), ('0/3x', None), ('/3', None), (None, None), ('S/3', None)])
def test_raw_mission_counter_requires_the_entire_valid_current_over_three(text, expected):
    assert parse_build_counter(text) == expected


def test_exact_title_requires_same_row_known_mission_marker():
    image = mission_image()
    image[396:472, 175:197] = 0
    assert locate_build_mission(image) is None


def test_missing_title_and_partially_hidden_row_do_not_authorize_orders():
    image = mission_image()
    image[435:460, 253:393] = 0
    assert locate_build_mission(image) is None
    original = mission_image()
    image[:] = 0
    image[640:665, 257:389] = original[435:460, 257:389]
    assert locate_build_mission(image) is None


def test_duplicate_exact_build_mission_titles_fail_closed():
    image = mission_image()
    image[126:151, 257:389] = image[435:460, 257:389]
    with pytest.raises(ScriptError, match='Ambiguous'):
        locate_build_mission(image)


def test_unknown_counter_does_not_turn_into_zero(monkeypatch):
    monkeypatch.setattr(mission, 'Ocr', Mock(return_value=SimpleNamespace(ocr=Mock(return_value='3/10'))))
    assert read_build_mission(mission_image()) is None
    # The locator remains useful for positively earned COMPLETE/CLAIM states.
    assert locate_build_mission(mission_image()) is not None


def order_controller():
    helper = GachaMissionOrderObserver.__new__(GachaMissionOrderObserver)
    helper.config = SimpleNamespace(config_name='f2p', Scheduler_ServerUpdate='00:00')
    helper.device = SimpleNamespace(click=Mock(), screenshot=Mock(), image='image')
    helper.ui_get_current_page = Mock()
    helper._capture_mission = Mock()
    helper._period = Mock(return_value='2026-10-23T00:00:00')
    return helper


def test_credit_reader_requires_two_matching_fresh_same_tab_frames(monkeypatch):
    helper = order_controller()
    helper._observe_mission_tab = Mock(return_value=True)
    helper.ui_page_appear = Mock(return_value=True)
    helper._reward_side_navbar = SimpleNamespace(get_info=Mock(return_value=(3, 0, 5)))
    helper.loop = Mock(return_value=iter(range(4)))
    monkeypatch.setattr(mission, 'Timer', ImmediateTimer)
    reader = Mock(side_effect=[dict(current=0, total=3), None, dict(current=1, total=3), dict(current=1, total=3)])
    monkeypatch.setattr(mission, 'read_build_mission', reader)
    result = helper._read_mission_tab({}, 'daily', 4)
    assert result == dict(current=1, total=3, tab='daily')
    assert reader.call_count == 4
    helper.loop.assert_called_once_with(skip_first=False, timeout=8)


@pytest.mark.parametrize('credits,remaining,reason', [
    ([0, 0], 3, None), ([1, None], 2, None), ([None, 2], 1, None),
    ([3, 3], 0, 'three_builds_already_credited'),
    ([0, 1], None, 'conflicting_build_mission_credit'),
    ([None, None], None, 'build_mission_not_positively_identified')])
def test_daily_and_event_equivalents_deduplicate_and_never_guess_absence(
        tmp_path, monkeypatch, credits, remaining, reason):
    helper = order_controller()
    monkeypatch.setattr(mission, 'ensure_event_page', Mock(return_value=True))
    helper._read_mission_tab = Mock(side_effect=[
        None if current is None else dict(current=current, total=3) for current in credits])
    helper._open_order_dialog = Mock()
    record = helper.run(folder=tmp_path)
    assert record['remaining'] == remaining
    assert record['orders_submitted'] == 0 and record['mission_reward_claimed'] is False
    if reason:
        assert record['reason'] == reason
        helper._open_order_dialog.assert_not_called()
    else:
        helper._open_order_dialog.assert_called_once()
    helper.device.click.assert_not_called()


def test_order_evidence_and_open_budget_are_durable_before_click(tmp_path, monkeypatch):
    helper = order_controller()
    monkeypatch.setattr(mission, 'ensure_event_page', Mock(return_value=True))
    helper._read_mission_tab = Mock(side_effect=[dict(current=2, total=3), None])
    def opener(record, reserve):
        record['build_open_clicked'] = True
        reserve()
        persisted = next(iter(load_mission_state('f2p', tmp_path).values()))
        assert persisted['mission_credit'] == 2 and persisted['remaining'] == 1
        assert persisted['build_open_clicked'] is True
        assert persisted['orders_submitted'] == 0
    helper._open_order_dialog = Mock(side_effect=opener)
    record = helper.run(folder=tmp_path)
    assert record['build_open_clicked'] and record['returned_main'] is False
    mission.ensure_event_page.assert_not_called()  # Never tap behind an unknown dialog.
    reused = helper.run(folder=tmp_path)
    assert reused['reused'] is True
    assert helper._read_mission_tab.call_count == 2
    helper._open_order_dialog.assert_called_once()


def fake_banner(monkeypatch, *, entered=True, on_pool=True):
    banner = SimpleNamespace(enter_event_pool=Mock(return_value=entered),
                             on_event_pool=Mock(return_value=on_pool))
    monkeypatch.setitem(sys.modules, 'module.gacha.banner', SimpleNamespace(
        EventBannerObserver=Mock(return_value=banner)))
    return banner


@pytest.mark.parametrize('entered,period', [(False, '2026-10-23T00:00:00'), (True, 'next reset')])
def test_missing_exact_event_pool_or_reset_crossover_never_clicks_build(monkeypatch, entered, period):
    helper = order_controller()
    fake_banner(monkeypatch, entered=entered)
    helper._period.return_value = period
    reserve = Mock()
    record = dict(period='2026-10-23T00:00:00')
    helper._open_order_dialog(record, reserve)
    assert record['status'] == 'blocked'
    reserve.assert_not_called()
    helper.device.click.assert_not_called()


@pytest.mark.parametrize('count_dialog,reason', [(True, 'order_count_dialog_requires_review'),
                                                (False, 'order_warning_requires_review')])
def test_one_build_opener_captures_settled_dialog_without_confirmation(monkeypatch, count_dialog, reason):
    helper = order_controller()
    banner = fake_banner(monkeypatch)
    banner.on_event_pool.side_effect = [True, False, False]
    helper.loop = Mock(return_value=iter(range(2)))
    helper.match_template_color = Mock(return_value=count_dialog)
    helper.handle_popup_confirm = Mock()
    helper.handle_popup_cancel = Mock()
    monkeypatch.setattr(mission, 'Timer', ImmediateTimer)
    reserve = Mock()
    record = dict(period='2026-10-23T00:00:00')
    helper._open_order_dialog(record, reserve)
    assert record['reason'] == reason and record['build_open_clicked']
    helper.device.click.assert_called_once_with(mission.EVENT_BUILD_ORDERS)
    reserve.assert_called_once_with()
    helper.handle_popup_confirm.assert_not_called()
    helper.handle_popup_cancel.assert_not_called()


def test_closed_game_does_not_reserve_order_observation(tmp_path):
    helper = order_controller()
    helper.ui_get_current_page.side_effect = GameNotRunningError
    with pytest.raises(GameNotRunningError):
        helper.run(folder=tmp_path)
    assert load_mission_state('f2p', tmp_path) == {}


def test_fresh_unknown_rateinfo_exit_stops_before_order_navigation(monkeypatch):
    helper = RewardGacha.__new__(RewardGacha)
    helper.config = SimpleNamespace(task_delay=Mock())
    helper.device = object()
    monkeypatch.setattr(mission, 'GachaMissionObserver', Mock(return_value=SimpleNamespace(run=Mock())))
    order = Mock()
    monkeypatch.setattr(mission, 'GachaMissionOrderObserver', order)
    monkeypatch.setitem(sys.modules, 'module.gacha.banner', SimpleNamespace(
        EventBannerObserver=Mock(return_value=SimpleNamespace(
            run=Mock(return_value={'returned_main': False})))))
    with pytest.raises(GamePageUnknownError, match='RateInfo'):
        helper.run()
    order.assert_not_called()
    helper.config.task_delay.assert_not_called()


def test_newly_observed_order_dialog_requests_scheduler_recovery_without_submission(monkeypatch):
    helper = RewardGacha.__new__(RewardGacha)
    helper.config = SimpleNamespace(task_delay=Mock())
    helper.device = object()
    monkeypatch.setattr(mission, 'GachaMissionObserver', Mock(return_value=SimpleNamespace(run=Mock())))
    monkeypatch.setattr(mission, 'GachaMissionOrderObserver', Mock(return_value=SimpleNamespace(
        run=Mock(return_value={'build_open_clicked': True}))))
    monkeypatch.setitem(sys.modules, 'module.gacha.banner', SimpleNamespace(
        EventBannerObserver=Mock(return_value=SimpleNamespace(
            run=Mock(return_value={'returned_main': True})))))
    with pytest.raises(GamePageUnknownError, match='no submission'):
        helper.run()
    helper.config.task_delay.assert_not_called()
