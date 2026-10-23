from types import SimpleNamespace
from unittest.mock import Mock, call

import numpy as np
import pytest
from PIL import Image

from module.campaign import event_rewards as rewards
from module.campaign.event_completion import load_reward_observations, save_reward_observations
from module.campaign.event_rewards import EventRewards
from module.campaign.event_rewards_assets import (
    FOGGY_CLAIM_BADGE, FOGGY_COLLECT_ALL, FOGGY_SUPPLY_HOME, FOGGY_SUPPLY_PAGE)
from module.combat.assets import GET_ITEMS_1, GET_ITEMS_2, GET_SHIP
from module.exception import GameNotRunningError

EVENT = 'event_20261022_cn'


def controller(frames=10):
    helper = EventRewards.__new__(EventRewards)
    helper.config = SimpleNamespace(config_name='f2p')
    helper.device = SimpleNamespace(app_is_running=Mock(return_value=True), click=Mock())
    helper.loop = Mock(return_value=iter(range(frames)))
    helper.appear = Mock(return_value=False)
    helper._foggy_state = Mock(return_value='unknown')
    return helper


@pytest.fixture
def immediate(monkeypatch):
    timer = Mock()
    timer.start.return_value = timer
    timer.reached.return_value = True
    monkeypatch.setattr(rewards, 'Timer', Mock(return_value=timer))


def record():
    return dict(status='collecting', claims=0, pending_claim=False)


def test_captured_ladder_recognizes_page_home_collect_and_claim_badge():
    image = np.asarray(Image.open('tests/campaign/data/foggy_supply.png').convert('RGB'))
    for button in (FOGGY_SUPPLY_PAGE, FOGGY_SUPPLY_HOME, FOGGY_COLLECT_ALL, FOGGY_CLAIM_BADGE):
        assert button.match_template_color(image, offset=(3, 3), similarity=.85)
        assert not button.match_template_color(image * 0, offset=(3, 3), similarity=.85)
    helper = controller()
    helper.match_template_color = lambda button, **kwargs: button.match_template_color(image, **kwargs)
    assert EventRewards._foggy_state(helper) == 'claimable'


@pytest.mark.parametrize('missing', [FOGGY_SUPPLY_PAGE, FOGGY_SUPPLY_HOME, FOGGY_COLLECT_ALL])
def test_missing_exact_page_or_control_is_unknown(missing):
    helper = controller()
    helper.match_template_color = Mock(side_effect=lambda button, **kwargs: button != missing)
    assert EventRewards._foggy_state(helper) == 'unknown'


def test_red_unmatched_badge_is_not_misread_as_all_claimed():
    helper = controller()
    helper.match_template_color = Mock(side_effect=lambda button, **kwargs: button != FOGGY_CLAIM_BADGE)
    helper.image_color_count = Mock(return_value=True)
    assert EventRewards._foggy_state(helper) == 'unknown'
    helper.image_color_count.return_value = False
    assert EventRewards._foggy_state(helper) == 'empty'


def test_collect_all_audited_before_click_then_reward_and_stable_empty(immediate):
    helper = controller(5)
    helper._foggy_state.side_effect = ['claimable', 'unknown', 'empty', 'empty', 'empty']
    helper._reward_dialog = Mock(return_value=GET_ITEMS_1)
    state = record()
    persisted = []

    def persist():
        assert not helper.device.click.called
        persisted.append(dict(state))

    helper._collect(state, persist)
    assert persisted[0]['claims'] == 1 and persisted[0]['pending_claim'] is True
    assert state['status'] == 'complete' and state['pending_claim'] is False
    assert helper.device.click.call_args_list == [call(FOGGY_COLLECT_ALL), call(GET_ITEMS_1)]
    helper.loop.assert_called_once_with(skip_first=False, timeout=90)


def test_already_empty_requires_three_fresh_frames_without_click(immediate):
    helper = controller(3)
    helper._foggy_state.return_value = 'empty'
    state = record()
    helper._collect(state, Mock())
    assert state['status'] == 'complete'
    helper.device.click.assert_not_called()


def test_single_empty_frame_cannot_claim_completion(immediate):
    helper = controller(3)
    helper._foggy_state.side_effect = ['empty', 'unknown', 'empty']
    state = record()
    helper._collect(state, Mock())
    assert state['status'] == 'failed'
    helper.device.click.assert_not_called()


def test_collect_button_rejection_is_capped_at_two_clicks(immediate):
    helper = controller()
    helper._foggy_state.return_value = 'claimable'
    state = record()
    helper._collect(state, Mock())
    assert state['reason'] == 'collect_all_did_not_clear'
    assert helper.device.click.call_args_list == [call(FOGGY_COLLECT_ALL)] * 2


def test_unknown_screen_before_claim_never_dismisses_or_buys(immediate):
    helper = controller()
    helper._reward_dialog = Mock(return_value=GET_SHIP)
    state = record()
    helper._collect(state, Mock())
    assert state['status'] == 'failed'
    helper._reward_dialog.assert_not_called()
    helper.device.click.assert_not_called()


def test_known_ship_popups_are_bounded_after_claim(immediate, monkeypatch):
    monkeypatch.setattr(rewards, 'REWARD_DIALOGS_MAX', 2)
    helper = controller()
    helper._foggy_state.side_effect = ['claimable'] + ['unknown'] * 9
    helper._reward_dialog = Mock(return_value=GET_SHIP)
    state = record()
    helper._collect(state, Mock())
    assert state['reason'] == 'reward_dialog_limit'
    assert helper.device.click.call_args_list == [call(FOGGY_COLLECT_ALL), call(GET_SHIP), call(GET_SHIP)]


@pytest.mark.parametrize('dialog,click', [(GET_ITEMS_1, GET_ITEMS_1), (GET_ITEMS_2, GET_ITEMS_1),
                                       (GET_SHIP, GET_SHIP)])
def test_only_known_reward_dialogs_have_dismissal_actions(dialog, click):
    helper = controller()
    helper.appear.side_effect = lambda button, **kwargs: button == dialog
    assert helper._reward_dialog() is click


def mock_navigation(monkeypatch, *, returned=True):
    helper = Mock()
    helper._inspect_event_rewards_panel.side_effect = lambda record, **kwargs: record.update(status='observed')
    helper._return_event_hub_main.return_value = returned
    monkeypatch.setattr(rewards, 'EventCompletion', Mock(return_value=helper))
    monkeypatch.setattr(rewards, 'save_event_diagnostic', Mock(return_value='/tmp/pt.png'))
    return helper


def test_run_persists_before_navigation_and_claim_rechecks_new_pt(monkeypatch, tmp_path):
    navigation = mock_navigation(monkeypatch)
    helper = controller()

    def collect(state, persist):
        stored = load_reward_observations('f2p', folder=tmp_path)[EVENT + ':free_pt_collection']
        assert stored['status'] == 'collecting'
        state.update(status='complete', pending_claim=False)

    helper._collect = Mock(side_effect=collect)
    first = helper.run(EVENT, folder=tmp_path)
    second = helper.run(EVENT, folder=tmp_path)
    assert first['status'] == second['status'] == 'complete'
    assert second['invocations'] == 2
    assert helper._collect.call_count == navigation._inspect_event_rewards_panel.call_count == 2
    assert second['diagnostics'] == ['/tmp/pt.png']


def test_return_home_failure_cannot_report_collection_complete(monkeypatch, tmp_path):
    mock_navigation(monkeypatch, returned=False)
    helper = controller()
    helper._collect = Mock(side_effect=lambda state, persist: state.update(status='complete'))
    result = helper.run(EVENT, folder=tmp_path)
    assert result['status'] == 'failed'
    assert result['reason'] == 'free_pt_main_return_unrecognized'


def test_unsupported_event_and_closed_game_do_not_spend_or_navigate(tmp_path):
    helper = controller()
    assert helper.run('campaign_main', folder=tmp_path)['status'] == 'unsupported'
    helper.device.app_is_running.assert_not_called()
    helper.device.app_is_running.return_value = False
    with pytest.raises(GameNotRunningError):
        helper.run(EVENT, folder=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_navigation_failure_is_saved_and_returns_without_claim(monkeypatch, tmp_path):
    navigation = mock_navigation(monkeypatch)
    navigation._inspect_event_rewards_panel.side_effect = lambda state, **kwargs: state.update(status='blocked')
    helper = controller()
    helper._collect = Mock()
    result = helper.run(EVENT, folder=tmp_path)
    assert result['status'] == 'failed'
    helper._collect.assert_not_called()
    navigation._return_event_hub_main.assert_called_once()


def test_account_audits_are_separate(monkeypatch, tmp_path):
    mock_navigation(monkeypatch)
    helper = controller()
    helper._collect = Mock(side_effect=lambda state, persist: state.update(status='complete'))
    helper.run(EVENT, folder=tmp_path)
    helper.config.config_name = 'alas'
    helper.run(EVENT, folder=tmp_path)
    assert load_reward_observations('f2p', folder=tmp_path)[EVENT + ':free_pt_collection']['invocations'] == 1
    assert load_reward_observations('alas', folder=tmp_path)[EVENT + ':free_pt_collection']['invocations'] == 1
