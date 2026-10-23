import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import numpy as np
from PIL import Image

from module.campaign import event_extra
from module.campaign.event_extra import (EventExtraRunner, EventExtraScout, load_extra_state,
                                         save_extra_state, valid_extra_fleet_options)
from module.campaign.event_extra_assets import (EX_DISABLED_FLEET_2, EX_DISABLED_SUBMARINE,
                                               EX_MAP_ATTACK, EX_MAP_COLUMNS, EX_MAP_TIME_LIMIT)
from module.config import server
from module.exception import GameNotRunningError, ScriptError
from module.map.assets import FLEET_PREPARATION, MAP_PREPARATION

EVENT = 'event_20261022_cn'


@pytest.fixture
def english_assets():
    previous = server.server
    server.set_server('en')
    yield
    server.set_server(previous)


def controller():
    helper = EventExtraScout.__new__(EventExtraScout)
    helper.config = SimpleNamespace(config_name='f2p', STAGE_ENTRANCE=['old'], override=Mock())
    helper.device = SimpleNamespace(screenshot=Mock(), click=Mock(), image='screen',
                                    app_is_running=Mock(return_value=True))
    helper._extra_capture = Mock()
    helper.appear = Mock(return_value=False)
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
    assert helper._observe_rewards.call_count == 2
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


def test_closed_game_never_spends_scout_or_reward_observation_budget(tmp_path):
    helper = controller()
    helper.device.app_is_running.return_value = False
    helper._inspect_extra = Mock()
    with pytest.raises(GameNotRunningError):
        helper.run(EVENT, folder=tmp_path)
    assert list(tmp_path.iterdir()) == []
    helper._inspect_extra.assert_not_called()
    helper._observe_rewards.assert_not_called()


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


def test_locked_ex_does_not_tap_the_selector(monkeypatch):
    helper = controller()
    monkeypatch.setattr(event_extra, 'ensure_event_page', Mock(return_value=True))
    switch = Mock(return_value=True)
    monkeypatch.setattr(event_extra, 'ensure_event_switch', switch)
    helper.appear = Mock(return_value=True)
    assert helper._inspect_extra({'diagnostics': []}) == ('locked', 'required_stage_not_cleared')
    assert switch.call_count == 1  # Combat only; the locked EX tab is not clicked.
    helper.device.click.assert_not_called()


def runner_controller():
    helper = EventExtraRunner.__new__(EventExtraRunner)
    helper.__dict__.update(controller().__dict__)
    helper.config.Fleet_Fleet1 = 6
    helper.config.Fleet_Fleet2 = 0
    helper.config.Submarine_Fleet = 0
    helper.config.StopCondition_OilLimit = 500
    helper.get_oil = Mock(return_value=1000)
    helper._inspect_extra = Mock(return_value=('observed', 'fleet_preparation_requires_supported_runner'))
    helper._prepare_extra_fleet = Mock(return_value=(True, 'fleet_6_verified'))
    return helper


def test_actual_attempt_budget_is_separate_from_locked_scout_and_reserved_before_start(monkeypatch, tmp_path):
    save_extra_state('f2p', {EVENT: dict(status='locked', combat_attempted=False)}, folder=tmp_path)
    helper = runner_controller()
    monkeypatch.setattr(event_extra, 'ensure_event_page', Mock(return_value=True))

    def battle(record, reserve):
        assert load_extra_state('f2p', tmp_path)[f'{EVENT}/combat']['combat_attempted'] is False
        reserve()
        assert load_extra_state('f2p', tmp_path)[f'{EVENT}/combat']['combat_attempted'] is True
        helper.device.click('final_start')
        return 'lost', 'battle_lost'

    helper._direct_extra_battle = Mock(side_effect=battle)
    first = helper.run(EVENT, folder=tmp_path)
    second = helper.run(EVENT, folder=tmp_path)
    assert first['status'] == second['status'] == 'lost'
    assert first['combat_attempted'] is second['combat_attempted'] is True
    assert first['returned_event'] is True
    assert second['reused'] is True
    helper._direct_extra_battle.assert_called_once()
    helper.device.click.assert_called_once_with('final_start')
    assert helper.config.override.call_args_list[-1].kwargs == {'STAGE_ENTRANCE': ['old']}


def test_locked_actual_preflight_can_retry_after_sp_without_spending_a_combat_attempt(monkeypatch, tmp_path):
    helper = runner_controller()
    monkeypatch.setattr(event_extra, 'ensure_event_page', Mock(return_value=True))
    helper._inspect_extra.return_value = ('locked', 'required_stage_not_cleared')
    helper._direct_extra_battle = Mock()
    assert helper.run(EVENT, folder=tmp_path)['combat_attempted'] is False
    helper._direct_extra_battle.assert_not_called()
    helper._inspect_extra.return_value = ('observed', 'fleet_preparation_requires_supported_runner')
    helper._direct_extra_battle.side_effect = lambda record, reserve: (reserve() or 'complete', 'battle_won')
    assert helper.run(EVENT, folder=tmp_path)['status'] == 'complete'
    assert load_extra_state('f2p', tmp_path)[f'{EVENT}/combat']['combat_attempted'] is True


def test_interrupted_combat_preparation_is_not_retried(monkeypatch, tmp_path):
    save_extra_state('f2p', {f'{EVENT}/combat': dict(status='preparing', combat_attempted=False)}, folder=tmp_path)
    helper = runner_controller()
    result = helper.run(EVENT, folder=tmp_path)
    assert result['reason'] == 'previous_preparation_interrupted'
    helper._inspect_extra.assert_not_called()


@pytest.mark.parametrize('preparation', ['battle_preparation_requires_supported_runner', 'unrecognized'])
def test_actual_runner_never_starts_without_fleet_proof(monkeypatch, tmp_path, preparation):
    helper = runner_controller()
    monkeypatch.setattr(event_extra, 'ensure_event_page', Mock(return_value=True))
    helper._inspect_extra.return_value = ('observed', preparation)
    helper._direct_extra_battle = Mock()
    result = helper.run(EVENT, folder=tmp_path)
    assert result['status'] == 'blocked'
    assert result['combat_attempted'] is False
    helper._prepare_extra_fleet.assert_not_called()
    helper._direct_extra_battle.assert_not_called()


def test_unverified_return_never_reports_completed_objective(monkeypatch, tmp_path):
    helper = runner_controller()
    monkeypatch.setattr(event_extra, 'ensure_event_page', Mock(return_value=False))
    helper._direct_extra_battle = Mock(side_effect=lambda record, reserve: (reserve() or 'complete', 'battle_won'))
    result = helper.run(EVENT, folder=tmp_path)
    assert result['status'] == 'blocked'
    assert result['reason'] == 'battle_won_but_event_return_unverified'
    assert result['combat_attempted'] is True


def test_combat_exception_keeps_budget_spent(monkeypatch, tmp_path):
    helper = runner_controller()
    def battle(record, reserve):
        reserve()
        raise ScriptError('unknown result')
    helper._direct_extra_battle = Mock(side_effect=battle)
    with pytest.raises(ScriptError, match='unknown result'):
        helper.run(EVENT, folder=tmp_path)
    result = helper.run(EVENT, folder=tmp_path)
    assert result['status'] == 'blocked'
    assert result['combat_attempted'] is True
    helper._direct_extra_battle.assert_called_once()


def fleet_options(selected=6):
    return {index: SimpleNamespace(index=index, area=(100, 270 + (index - 1) * 40, 269, 303 + (index - 1) * 40),
                                   selected=index == selected)
            for index in range(1, 7)}


def test_fleet_option_validation_requires_six_contiguous_rows_and_one_selected():
    choose = SimpleNamespace(button=(100, 193, 160, 251))
    options = fleet_options()
    assert valid_extra_fleet_options(options, choose)
    assert not valid_extra_fleet_options({key: row for key, row in options.items() if key != 1}, choose)
    options[1].selected = True
    assert not valid_extra_fleet_options(options, choose)
    options[1].selected = False
    options[6].area = (100, 600, 269, 633)
    assert not valid_extra_fleet_options(options, choose)


def test_fleet_6_selection_must_be_reopened_and_verified_before_success():
    helper = runner_controller()
    fleet = SimpleNamespace(allow=Mock(return_value=True), is_hard=Mock(return_value=False),
                            options=fleet_options(selected=1), _choose='choose', update_options=Mock())
    helper._fleet_operators = Mock(return_value=(fleet, Mock(), Mock()))
    helper._clear_extra_fleet = Mock(return_value=True)
    calls = []
    def dropdown(current):
        calls.append('open')
        if len(calls) == 2:
            current.options = fleet_options(selected=6)
        return True
    helper._extra_dropdown = Mock(side_effect=dropdown)
    def closed():
        fleet.options = {}
    fleet.update_options.side_effect = closed
    helper.loop = Mock(return_value=iter([0]))
    helper._close_extra_dropdown = Mock(return_value=True)
    target = fleet.options[6]
    record = {'diagnostics': []}
    assert EventExtraRunner._prepare_extra_fleet(helper, record) == (True, 'fleet_6_verified')
    helper.device.click.assert_called_once_with(target)
    assert calls == ['open', 'open']
    assert record['fleet'] == 6
    helper._close_extra_dropdown.assert_called_once_with(fleet)


def test_incorrect_fleet_selection_proof_blocks_start():
    helper = runner_controller()
    fleet = SimpleNamespace(allow=Mock(return_value=True), is_hard=Mock(return_value=False),
                            options=fleet_options(selected=1), _choose='choose', update_options=Mock())
    helper._fleet_operators = Mock(return_value=(fleet, Mock(), Mock()))
    helper._clear_extra_fleet = Mock(return_value=True)
    helper._extra_dropdown = Mock(return_value=True)
    # The selection closes, but reopening still shows fleet 1.
    def closed():
        fleet.options = {}
    fleet.update_options.side_effect = closed
    helper.loop = Mock(return_value=iter([0]))
    def dropdown(current):
        current.options = fleet_options(selected=1)
        return True
    helper._extra_dropdown.side_effect = dropdown
    assert EventExtraRunner._prepare_extra_fleet(helper, {'diagnostics': []}) == (False, 'fleet_6_not_selected')


@pytest.mark.parametrize('oil', [0, 499])
def test_oil_floor_or_unreadable_oil_blocks_before_fleet_changes(oil):
    helper = runner_controller()
    helper.get_oil.return_value = oil
    helper._fleet_operators = Mock()
    assert EventExtraRunner._prepare_extra_fleet(helper, {'diagnostics': []}) == (False, 'oil_floor_reached')
    helper._fleet_operators.assert_not_called()
    helper.device.click.assert_not_called()


@pytest.mark.parametrize('outcome', ['won', 'lost', 'unknown'])
def test_direct_battle_uses_auto_once_and_reports_only_observed_results(outcome):
    helper = runner_controller()
    current = ['fleet']
    # Defeat may lead back to a start screen; it must never authorize a retry.
    screens = ['prep', 'prep', 'loading', 'combat', outcome, 'prep' if outcome == 'lost' else 'event']
    def states(**kwargs):
        for screen in screens:
            current[0] = screen
            yield screen
    helper.loop = states
    helper.appear = Mock(side_effect=lambda button, **kwargs:
                         (button is FLEET_PREPARATION and current[0] == 'fleet') or
                         (button is event_extra.BATTLE_PREPARATION and current[0] == 'prep') or
                         (button is event_extra.AUTOMATION_ON and current[0] == 'prep'))
    helper.ui_page_appear = Mock(side_effect=lambda page: current[0] == 'event')
    helper.is_combat_executing = Mock(side_effect=lambda: current[0] == 'combat')
    helper.is_combat_loading = Mock(side_effect=lambda: current[0] == 'loading')
    helper.handle_combat_automation_set = Mock(return_value=False)
    helper.handle_combat_automation_confirm = Mock(return_value=False)
    helper.handle_combat_auto = Mock()
    helper.combat_auto_reset = Mock()
    def result():
        helper.combat_won = current[0] == 'won'
        helper.combat_lost = current[0] == 'lost'
        return helper.combat_won or helper.combat_lost
    helper.handle_battle_status = Mock(side_effect=result)
    helper.handle_get_items = Mock(return_value=False)
    helper.handle_exp_info = Mock(return_value=False)
    helper.appear_then_click = Mock(return_value=False)
    reserved = []
    def click(button):
        assert reserved, 'start before reservation'
    helper.device.click.side_effect = click
    result = helper._direct_extra_battle({'diagnostics': []}, lambda: reserved.append(True))
    assert result[0] == {'won': 'complete', 'lost': 'lost', 'unknown': 'blocked'}[outcome]
    assert [call.args[0] for call in helper.device.click.call_args_list] == [FLEET_PREPARATION, event_extra.BATTLE_PREPARATION]
    helper.handle_combat_automation_set.assert_called_once_with(auto=True)
    helper.handle_combat_auto.assert_called_once_with('combat_auto')


def test_unknown_auto_setting_never_clicks_battle_start():
    helper = runner_controller()
    helper.appear = Mock(side_effect=lambda button, **kwargs: button in (FLEET_PREPARATION, event_extra.BATTLE_PREPARATION))
    helper.ui_page_appear = Mock(return_value=False)
    helper.combat_auto_reset = Mock()
    helper.handle_combat_automation_set = Mock(return_value=False)
    helper.loop = Mock(return_value=iter(range(3)))
    assert helper._direct_extra_battle({'diagnostics': []}, Mock())[0] == 'blocked'
    helper.device.click.assert_called_once_with(FLEET_PREPARATION)


@pytest.mark.parametrize('seen', ['loading', 'combat'])
def test_possible_direct_battle_then_unreadable_defeat_never_starts_retry(seen):
    helper = runner_controller()
    current = ['fleet']
    def states(**kwargs):
        for screen in (seen, 'prep'):
            current[0] = screen
            yield screen
    helper.loop = states
    helper.appear = Mock(side_effect=lambda button, **kwargs:
                         (button is FLEET_PREPARATION and current[0] == 'fleet') or
                         (button is event_extra.BATTLE_PREPARATION and current[0] == 'prep'))
    helper.ui_page_appear = Mock(return_value=False)
    helper.is_combat_executing = Mock(side_effect=lambda: current[0] == 'combat')
    helper.is_combat_loading = Mock(side_effect=lambda: current[0] == 'loading')
    helper.combat_auto_reset = Mock()
    helper.handle_combat_auto = Mock()
    helper.handle_combat_automation_set = Mock()
    assert helper._direct_extra_battle({'diagnostics': []}, Mock()) == ('blocked', 'battle_end_not_verified')
    helper.device.click.assert_called_once_with(FLEET_PREPARATION)
    helper.handle_combat_automation_set.assert_not_called()


def test_actual_entrance_frame_after_go_waits_for_board_instead_of_ending_sortie(monkeypatch, english_assets):
    helper = runner_controller()
    transition = np.asarray(Image.open('tests/campaign/data/ex_entry_transition.png').convert('RGB'))
    board = np.asarray(Image.open('tests/campaign/data/ex_tactical_map_settled.png').convert('RGB'))
    current = ['fleet']
    helper.device.image = transition
    helper.appear = lambda button, offset=0, **kwargs: (
        (button is FLEET_PREPARATION and current[0] == 'fleet')
        or button.match(helper.device.image, offset=offset))
    helper.ui_page_appear = lambda page: page.check_button.match(helper.device.image, offset=(30, 30))
    assert helper.ui_page_appear(event_extra.page_sp)
    frames_seen = []
    def frames(**kwargs):
        for image in (transition, transition, board):
            current[0] = 'entry'
            helper.device.image = image
            frames_seen.append(image)
            yield image
    helper.loop = frames
    helper.combat_auto_reset = Mock()
    helper._launch_extra_map = Mock(return_value=False)
    monkeypatch.setattr(event_extra, 'monotonic', lambda: 100.)
    reserve = Mock()
    record = {}
    assert helper._direct_extra_battle(record, reserve) == ('blocked', 'map_launch_not_verified')
    assert len(frames_seen) == 3
    helper._launch_extra_map.assert_called_once_with(record, reserve)
    helper.device.click.assert_called_once_with(FLEET_PREPARATION)
    reserve.assert_called_once_with()


def test_entrance_that_never_loads_is_bounded_without_repeating_go(monkeypatch):
    helper = runner_controller()
    helper.appear = Mock(return_value=True)
    helper.ui_page_appear = Mock(return_value=True)
    helper.combat_auto_reset = Mock()
    helper._launch_extra_map = Mock()
    now = [100.]
    frames_seen = []
    monkeypatch.setattr(event_extra, 'monotonic', lambda: now[0])
    def frames(**kwargs):
        for timestamp in (100.5, 129.9, 130., 131.):
            now[0] = timestamp
            frames_seen.append(timestamp)
            yield timestamp
    helper.loop = frames
    reserve = Mock()
    assert helper._direct_extra_battle({}, reserve) == ('blocked', 'returned_without_verified_result')
    assert frames_seen == [100.5, 129.9, 130.]
    helper.device.click.assert_called_once_with(FLEET_PREPARATION)
    helper._launch_extra_map.assert_not_called()
    reserve.assert_called_once_with()


def hard_preparation_controller():
    helper = runner_controller()
    helper.appear = Mock(return_value=True)
    helper.match_template_color = Mock(return_value=True)
    fleet = SimpleNamespace(allow=Mock(return_value=True), is_hard=Mock(return_value=True),
                            advise=Mock(return_value=True), is_hard_satisfied=Mock(return_value=True),
                            in_use=Mock(return_value=True))
    secondary = SimpleNamespace(allow=Mock(return_value=False), is_hard=Mock(return_value=False))
    submarine = SimpleNamespace(allow=Mock(return_value=False), is_hard=Mock(return_value=False))
    helper._fleet_operators = Mock(return_value=(fleet, secondary, submarine))
    helper._clear_extra_fleet = Mock()
    helper._extra_dropdown = Mock()
    return helper, fleet, secondary, submarine


def test_captured_ex_has_hard_empty_primary_and_exact_disabled_secondary_rows(english_assets):
    helper = runner_controller()
    image = np.asarray(Image.open('tests/campaign/data/ex_hard_preparation.png').convert('RGB'))
    helper.device.image = image
    helper.appear = lambda button, offset=0, **kwargs: button.match(image, offset=offset) if offset else button.appear_on(image)
    helper.match_template_color = lambda button, **kwargs: button.match_template_color(image, **kwargs)
    helper.image_crop = lambda area, **kwargs: image[area[1]:area[3], area[0]:area[2]]
    fleet, secondary, submarine = helper._fleet_operators()
    assert helper.appear(FLEET_PREPARATION, offset=(20, 50))
    assert fleet.allow() and fleet.is_hard()
    assert fleet.is_hard_satisfied() is False
    assert not fleet.in_use()
    assert helper._extra_hard_secondary_disabled(secondary, submarine)
    for button in (EX_DISABLED_FLEET_2, EX_DISABLED_SUBMARINE):
        assert button.match_template_color(image, offset=(3, 3))
        assert not button.match_template_color(image * 0, offset=(3, 3))


@pytest.mark.parametrize('missing', [EX_DISABLED_FLEET_2, EX_DISABLED_SUBMARINE])
def test_missing_disabled_row_witness_blocks_before_recommendation(missing):
    helper, fleet, _, _ = hard_preparation_controller()
    helper.match_template_color.side_effect = lambda button, **kwargs: button is not missing
    assert EventExtraRunner._prepare_extra_fleet(helper, {}) == (False, 'hard_lineup_layout_unrecognized')
    fleet.advise.assert_not_called()
    helper._clear_extra_fleet.assert_not_called()
    helper._extra_dropdown.assert_not_called()
    helper.device.click.assert_not_called()


@pytest.mark.parametrize('slot', [1, 2])
@pytest.mark.parametrize('control', ['allow', 'is_hard'])
def test_active_secondary_or_submarine_is_never_cleared_or_recommended(slot, control):
    helper, *fleets = hard_preparation_controller()
    getattr(fleets[slot], control).return_value = True
    assert EventExtraRunner._prepare_extra_fleet(helper, {}) == (False, 'hard_lineup_layout_unrecognized')
    fleets[0].advise.assert_not_called()
    helper._clear_extra_fleet.assert_not_called()
    helper.device.click.assert_not_called()


def test_hard_recommendation_requires_fresh_verified_occupied_lineup_without_common_fleet_changes():
    helper, fleet, _, _ = hard_preparation_controller()
    record = {'diagnostics': []}
    assert EventExtraRunner._prepare_extra_fleet(helper, record) == (True, 'hard_lineup_verified')
    fleet.advise.assert_called_once_with()
    helper.device.screenshot.assert_called_once_with()
    fleet.is_hard_satisfied.assert_called_once_with()
    fleet.in_use.assert_called_once_with()
    assert record['fleet'] == 'stage_owned_recommended'
    assert helper._extra_capture.call_args_list[-1].args[-1] == 'hard_lineup_verified'
    helper._clear_extra_fleet.assert_not_called()
    helper._extra_dropdown.assert_not_called()
    helper.device.click.assert_not_called()


@pytest.mark.parametrize('advice', [False, None])
def test_failed_or_unknown_advice_never_falls_through_to_common_fleet(advice):
    helper, fleet, _, _ = hard_preparation_controller()
    fleet.advise.return_value = advice
    assert EventExtraRunner._prepare_extra_fleet(helper, {}) == (False, 'hard_lineup_recommendation_failed')
    helper.device.screenshot.assert_not_called()
    helper._clear_extra_fleet.assert_not_called()
    helper._extra_dropdown.assert_not_called()
    helper.device.click.assert_not_called()


@pytest.mark.parametrize('proof', ['go', 'allow', 'hard', 'satisfied', 'unknown_satisfied', 'occupied', 'disabled'])
def test_changed_or_unreadable_post_recommendation_state_cannot_authorize_start(proof):
    helper, fleet, _, _ = hard_preparation_controller()
    def advised():
        if proof == 'go':
            helper.appear.return_value = False
        elif proof == 'allow':
            fleet.allow.return_value = False
        elif proof == 'hard':
            fleet.is_hard.return_value = False
        elif proof in ('satisfied', 'unknown_satisfied'):
            fleet.is_hard_satisfied.return_value = False if proof == 'satisfied' else None
        elif proof == 'occupied':
            fleet.in_use.return_value = False
        elif proof == 'disabled':
            helper.match_template_color.return_value = False
        return True
    fleet.advise.side_effect = advised
    record = {}
    assert EventExtraRunner._prepare_extra_fleet(helper, record) == (False, 'hard_lineup_not_verified')
    assert 'fleet' not in record
    helper.device.screenshot.assert_called_once_with()
    helper.device.click.assert_not_called()


def test_unverified_map_launch_is_captured_and_blocked_without_grid_or_battle_actions():
    helper = runner_controller()
    helper.appear = Mock(side_effect=lambda button, **kwargs: button in (FLEET_PREPARATION, event_extra.IN_MAP))
    helper.ui_page_appear = Mock(return_value=False)
    helper.combat_auto_reset = Mock()
    helper.handle_combat_automation_set = Mock()
    helper._launch_extra_map = Mock(return_value=False)
    helper.loop = Mock(return_value=iter(range(3)))
    reserve = Mock()
    record = {}
    assert helper._direct_extra_battle(record, reserve) == ('blocked', 'map_launch_not_verified')
    reserve.assert_called_once_with()
    helper.device.click.assert_called_once_with(FLEET_PREPARATION)
    helper._launch_extra_map.assert_called_once_with(record, reserve)
    helper.handle_combat_automation_set.assert_not_called()


def immediate_timers(monkeypatch):
    timer = SimpleNamespace(reached=Mock(return_value=True), reset=Mock())
    timer.start = lambda: timer
    monkeypatch.setattr(event_extra, 'Timer', lambda *args, **kwargs: timer)
    return timer


def map_fixture_controller():
    helper = runner_controller()
    helper.device.image = np.asarray(Image.open('tests/campaign/data/ex_tactical_map_settled.png').convert('RGB')).copy()
    helper.appear = lambda button, offset=0, **kwargs: button.match(helper.device.image, offset=offset)
    helper.match_template_color = lambda button, **kwargs: button.match_template_color(helper.device.image, **kwargs)
    def frames(**kwargs):
        assert kwargs['skip_first'] is False
        for _ in range(8):
            helper.device.screenshot()
            yield helper.device.image
    helper.loop = frames
    return helper


def test_actual_ex_board_and_attack_wait_for_four_fresh_stable_frames_before_one_click(monkeypatch, english_assets):
    helper = map_fixture_controller()
    immediate_timers(monkeypatch)
    record = {}
    reserve = Mock(side_effect=lambda: record['map_attack_clicked'] is True)
    assert helper._extra_map_appear()
    assert helper._launch_extra_map(record, reserve) is True
    assert helper.device.screenshot.call_count == 4
    reserve.assert_called_once_with()
    helper.device.click.assert_called_once_with(EX_MAP_ATTACK)
    helper._extra_capture.assert_called_once_with(record, 'settled_map_attack')


def test_actual_no_time_limit_ex_board_allows_one_settled_attack(monkeypatch, english_assets):
    helper = map_fixture_controller()
    helper.device.image = np.asarray(Image.open(
        'tests/campaign/data/ex_tactical_map_no_time_limit.png').convert('RGB')).copy()
    immediate_timers(monkeypatch)
    assert helper._extra_map_appear()
    reserve = Mock()
    assert helper._launch_extra_map({}, reserve) is True
    reserve.assert_called_once_with()
    helper.device.click.assert_called_once_with(EX_MAP_ATTACK)


def test_no_time_limit_title_alone_never_authorizes_attack(monkeypatch, english_assets):
    helper = map_fixture_controller()
    helper.device.image = np.asarray(Image.open(
        'tests/campaign/data/ex_tactical_map_no_time_limit.png').convert('RGB')).copy()
    x1, y1, x2, y2 = EX_MAP_COLUMNS.area
    helper.device.image[y1:y2, x1:x2] = 0
    immediate_timers(monkeypatch)
    assert helper._launch_extra_map({}, Mock()) is False
    helper.device.click.assert_not_called()


@pytest.mark.parametrize('missing', [EX_MAP_TIME_LIMIT, EX_MAP_COLUMNS, EX_MAP_ATTACK, event_extra.IN_MAP])
def test_missing_ex_map_witness_never_authorizes_attack(monkeypatch, english_assets, missing):
    helper = map_fixture_controller()
    immediate_timers(monkeypatch)
    x1, y1, x2, y2 = missing.area
    helper.device.image[y1:y2, x1:x2] = 0
    reserve = Mock()
    assert helper._launch_extra_map({}, reserve) is False
    reserve.assert_not_called()
    helper.device.click.assert_not_called()


def test_animating_ex_board_does_not_start_until_stable(monkeypatch, english_assets):
    helper = map_fixture_controller()
    immediate_timers(monkeypatch)
    original = helper.device.image.copy()
    def frames(**kwargs):
        for index in range(12):
            helper.device.image = original.copy()
            helper.device.image[625:646, 450:1120] = 0 if index % 2 else 255
            yield helper.device.image
    helper.loop = frames
    assert helper._launch_extra_map({}, Mock()) is False
    helper.device.click.assert_not_called()


def test_initial_empty_board_is_not_launched_before_the_actual_settled_map(monkeypatch, english_assets):
    helper = map_fixture_controller()
    immediate_timers(monkeypatch)
    empty = np.asarray(Image.open('tests/campaign/data/ex_tactical_map.png').convert('RGB'))
    settled = helper.device.image.copy()
    helper.device.image = empty
    assert helper._extra_map_appear() is False
    frames_seen = []
    def frames(**kwargs):
        for image in (empty, empty, settled, settled, settled, settled):
            helper.device.image = image
            frames_seen.append(image)
            yield image
    helper.loop = frames
    def clicked(button):
        assert len(frames_seen) == 6
        assert np.array_equal(helper.device.image, settled)
    helper.device.click.side_effect = clicked
    assert helper._launch_extra_map({}, Mock()) is True
    helper.device.click.assert_called_once_with(EX_MAP_ATTACK)


def test_animated_ships_do_not_prevent_a_settled_map_frame_launch(monkeypatch, english_assets):
    helper = map_fixture_controller()
    immediate_timers(monkeypatch)
    original = helper.device.image.copy()
    def frames(**kwargs):
        for index in range(8):
            helper.device.image = original.copy()
            helper.device.image[200:560, 500:1050] = 0 if index % 2 else 255
            yield helper.device.image
    helper.loop = frames
    assert helper._launch_extra_map({}, Mock()) is True
    helper.device.click.assert_called_once_with(EX_MAP_ATTACK)


@pytest.mark.parametrize('outcome', ['won', 'lost', 'unknown'])
def test_map_launch_uses_one_auto_battle_and_never_restarts_on_map_return(outcome):
    helper = runner_controller()
    current = ['fleet']
    def frames(**kwargs):
        for screen in ('map', 'prep', 'loading', 'combat', outcome, 'map', 'prep'):
            current[0] = screen
            yield screen
    helper.loop = frames
    helper.appear = Mock(side_effect=lambda button, **kwargs:
                         (button is FLEET_PREPARATION and current[0] == 'fleet') or
                         (button is event_extra.IN_MAP and current[0] == 'map') or
                         (button is event_extra.BATTLE_PREPARATION and current[0] == 'prep') or
                         (button is event_extra.AUTOMATION_ON and current[0] == 'prep'))
    helper.ui_page_appear = Mock(return_value=False)
    helper.combat_auto_reset = Mock()
    helper.handle_combat_automation_set = Mock(return_value=False)
    helper.handle_combat_automation_confirm = Mock(return_value=False)
    helper.is_combat_executing = Mock(side_effect=lambda: current[0] == 'combat')
    helper.is_combat_loading = Mock(side_effect=lambda: current[0] == 'loading')
    helper.handle_combat_auto = Mock()
    def result():
        if current[0] == outcome and outcome != 'unknown':
            helper.combat_won = outcome == 'won'
            helper.combat_lost = outcome == 'lost'
            return True
        return False
    helper.handle_battle_status = Mock(side_effect=result)
    helper.handle_get_items = helper.handle_exp_info = Mock(return_value=False)
    helper.appear_then_click = Mock(return_value=False)
    def launch(record, reserve):
        record['map_attack_clicked'] = True
        reserve()
        helper.device.click(EX_MAP_ATTACK)
        return True
    helper._launch_extra_map = Mock(side_effect=launch)
    reserved = []
    record = {}
    def reserve():
        reserved.append(dict(record))
    def clicked(button):
        key = {FLEET_PREPARATION: 'fleet_start_clicked', EX_MAP_ATTACK: 'map_attack_clicked',
               event_extra.BATTLE_PREPARATION: 'battle_start_clicked'}[button]
        assert reserved[-1][key] is True
    helper.device.click.side_effect = clicked
    status, reason = helper._direct_extra_battle(record, reserve)
    assert status == {'won': 'complete', 'lost': 'lost', 'unknown': 'blocked'}[outcome]
    assert record['combat_seen'] is True
    assert [call.args[0] for call in helper.device.click.call_args_list] == [
        FLEET_PREPARATION, EX_MAP_ATTACK, event_extra.BATTLE_PREPARATION]
    helper._launch_extra_map.assert_called_once()


def test_map_launch_that_stays_on_map_does_not_click_attack_again(monkeypatch):
    helper = runner_controller()
    helper.appear = Mock(side_effect=lambda button, **kwargs: button in (FLEET_PREPARATION, event_extra.IN_MAP))
    helper.ui_page_appear = Mock(return_value=False)
    helper.combat_auto_reset = Mock()
    helper.loop = Mock(return_value=iter(range(8)))
    helper._launch_extra_map = Mock(return_value=True)
    assert helper._direct_extra_battle({}, Mock()) == ('blocked', 'battle_end_not_verified')
    helper._launch_extra_map.assert_called_once()
    helper.device.click.assert_called_once_with(FLEET_PREPARATION)


def test_actual_ex_map_withdrawal_is_bounded_and_returns_verified_event(monkeypatch, english_assets):
    helper = map_fixture_controller()
    immediate_timers(monkeypatch)
    current = ['map']
    def frames(**kwargs):
        for screen in ('map', 'confirm', 'map', 'event'):
            current[0] = screen
            yield screen
    helper.loop = frames
    helper.ui_page_appear = Mock(side_effect=lambda page: current[0] == 'event')
    helper.handle_popup_confirm = Mock(side_effect=lambda name: current[0] == 'confirm')
    assert helper._return_extra_event({}) is True
    helper.device.click.assert_called_once_with(event_extra.WITHDRAW)
    assert helper.handle_popup_confirm.call_count == 1


def test_unknown_map_is_not_withdrawn_and_does_not_fall_into_generic_navigation(monkeypatch):
    helper = runner_controller()
    helper.appear = Mock(return_value=True)
    helper._extra_map_appear = Mock(return_value=False)
    helper.ui_page_appear = Mock(return_value=False)
    helper.handle_popup_confirm = Mock()
    helper.loop = Mock(return_value=iter(range(8)))
    navigation = Mock()
    monkeypatch.setattr(event_extra, 'ensure_event_page', navigation)
    assert helper._return_extra_event({}) is False
    helper.device.click.assert_not_called()
    helper.handle_popup_confirm.assert_not_called()
    navigation.assert_not_called()


def test_withdraw_confirmation_requires_prior_exact_map_retreat(monkeypatch, english_assets):
    helper = map_fixture_controller()
    immediate_timers(monkeypatch)
    helper.ui_page_appear = Mock(return_value=False)
    helper.handle_popup_confirm = Mock(return_value=False)
    assert helper._return_extra_event({}) is False
    helper.device.click.assert_called_once_with(event_extra.WITHDRAW)
    assert helper.handle_popup_confirm.call_count == 7
