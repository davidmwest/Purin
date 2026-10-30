from types import SimpleNamespace
from unittest.mock import Mock, call

import numpy as np
import pytest
from PIL import Image

from module.campaign import event_rewards as rewards
from module.campaign.event_completion import load_reward_observations, save_reward_observations
from module.campaign.event_rewards import EventRewards
from module.campaign.event_rewards_assets import (
    FOGGY_CLAIM_BADGE, FOGGY_COLLECT_ALL, FOGGY_COLLECT_ALL_DISABLED,
    FOGGY_SUPPLY_HOME, FOGGY_SUPPLY_PAGE, REWARD_SHIP_LOCK_HEADER,
    REWARD_SHIP_LOCK_MESSAGE, REWARD_SHIP_LOCK_CANCEL, REWARD_SHIP_LOCK_CONFIRM)
from module.combat.assets import GET_ITEMS_1, GET_ITEMS_2, GET_SHIP
from module.exception import GameNotRunningError

EVENT = 'event_20261022_cn'


def controller(frames=10):
    helper = EventRewards.__new__(EventRewards)
    helper.config = SimpleNamespace(config_name='f2p')
    helper.device = SimpleNamespace(app_is_running=Mock(return_value=True), click=Mock(),
                                    stuck_record_clear=Mock())
    helper.loop = Mock(return_value=iter(range(frames)))
    helper.appear = Mock(return_value=False)
    helper.match_template_color = Mock(return_value=False)
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


def captured_controller(filename):
    helper = controller(3)
    image = np.asarray(Image.open('tests/campaign/data/' + filename).convert('RGB')).copy()
    helper.device.image = image
    helper.match_template_color = lambda button, **kwargs: button.match_template_color(image, **kwargs)
    helper._foggy_state = lambda: EventRewards._foggy_state(helper)
    return helper


def test_actual_post_claim_disabled_frame_verifies_completion_without_another_claim(immediate):
    helper = captured_controller('foggy_supply_empty.png')
    assert FOGGY_COLLECT_ALL_DISABLED.match_template_color(helper.device.image, offset=(3, 3), similarity=.85)
    assert not FOGGY_COLLECT_ALL.match_template_color(helper.device.image, offset=(3, 3), similarity=.85)
    assert helper._foggy_state() == 'empty'
    state = record()
    state.update(claims=1, pending_claim=True)
    helper._collect(state, Mock())
    assert state['status'] == 'complete' and state['pending_claim'] is False
    helper.device.click.assert_not_called()


@pytest.mark.parametrize('missing', [FOGGY_SUPPLY_PAGE, FOGGY_SUPPLY_HOME, FOGGY_COLLECT_ALL_DISABLED])
def test_actual_disabled_frame_requires_exact_title_home_and_disabled_control(missing):
    helper = captured_controller('foggy_supply_empty.png')
    x1, y1, x2, y2 = missing.area
    helper.device.image[y1 - 6:y2 + 6, x1 - 6:x2 + 6] = 0
    assert helper._foggy_state() == 'unknown'


def test_active_button_with_missing_badge_cannot_be_mistaken_for_disabled_completion():
    helper = captured_controller('foggy_supply.png')
    x1, y1, x2, y2 = FOGGY_CLAIM_BADGE.area
    helper.device.image[y1 - 6:y2 + 6, x1 - 6:x2 + 6] = 0
    assert helper._foggy_state() == 'unknown'


def test_disabled_button_with_unmatched_red_badge_fails_closed():
    helper = captured_controller('foggy_supply_empty.png')
    x1, y1, x2, y2 = FOGGY_CLAIM_BADGE.area
    helper.device.image[y1:y2, x1:x2] = (255, 66, 85)
    assert helper._foggy_state() == 'unknown'


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


@pytest.mark.parametrize('known', [True, False])
def test_watchdog_refresh_requires_the_exact_page_and_stays_locally_bounded(immediate, known):
    helper = controller(3)
    helper._foggy_page_appear = Mock(return_value=known)
    state = record()
    helper._collect(state, Mock())
    assert helper.device.stuck_record_clear.call_count == (3 if known else 0)
    helper.loop.assert_called_once_with(skip_first=False, timeout=90)
    assert state['status'] == 'failed'
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


def test_captured_lock_question_requires_owned_ship_reward():
    helper = captured_controller('reward_ship_lock.png')
    assert helper._ship_lock_appear()
    assert helper._reward_dialog() is None
    assert helper._reward_dialog(allow_ship_lock=True) is REWARD_SHIP_LOCK_CONFIRM


@pytest.mark.parametrize('missing', [REWARD_SHIP_LOCK_HEADER, REWARD_SHIP_LOCK_MESSAGE,
                                   REWARD_SHIP_LOCK_CANCEL, REWARD_SHIP_LOCK_CONFIRM])
def test_incomplete_lock_question_cannot_confirm(missing):
    helper = captured_controller('reward_ship_lock.png')
    x1, y1, x2, y2 = missing.area
    helper.device.image[y1 - 6:y2 + 6, x1 - 6:x2 + 6] = 0
    assert helper._reward_dialog(allow_ship_lock=True) is None


def test_lock_keyword_cannot_be_missing_from_an_otherwise_exact_question():
    helper = captured_controller('reward_ship_lock.png')
    helper.device.image[330:361, 646:704] = 0
    assert helper._reward_dialog(allow_ship_lock=True) is None


def test_archive_key_purchase_popup_is_not_a_ship_lock_question():
    helper = controller()
    image = np.asarray(Image.open('tests/war_archives/data/archive_data_key_dialog.png').convert('RGB'))
    helper.match_template_color = lambda button, **kwargs: button.match_template_color(image, **kwargs)
    assert helper._reward_dialog(allow_ship_lock=True) is None


def test_claimed_ship_gets_one_audited_lock_confirmation_then_verifies_empty(immediate):
    helper = controller(6)
    helper._foggy_state.side_effect = ['claimable'] + ['unknown'] * 2 + ['empty'] * 3
    helper.appear.side_effect = lambda button, **kwargs: button == GET_SHIP
    helper._ship_lock_appear = Mock(return_value=True)
    state = record()
    audits = []

    def persist():
        audits.append((dict(state), list(helper.device.click.call_args_list)))

    helper._collect(state, persist)
    assert helper.device.click.call_args_list == [call(FOGGY_COLLECT_ALL), call(GET_SHIP),
                                                call(REWARD_SHIP_LOCK_CONFIRM)]
    assert audits[1][0]['ship_lock_attempts'] == 1
    assert call(REWARD_SHIP_LOCK_CONFIRM) not in audits[1][1]
    assert state['status'] == 'complete'


@pytest.mark.parametrize('pending_claim', [False, True])
def test_lock_prompt_without_fresh_ship_dismissal_never_confirms(immediate, pending_claim):
    helper = captured_controller('reward_ship_lock.png')
    state = record()
    state['pending_claim'] = pending_claim
    helper._collect(state, Mock())
    helper.device.click.assert_not_called()
    assert state['status'] == 'failed'


def test_same_lock_prompt_is_not_confirmed_twice_without_another_ship(immediate):
    helper = controller(7)
    helper._foggy_state.side_effect = ['claimable'] + ['unknown'] * 6
    ship_dismissed = False

    def appear(button, **kwargs):
        nonlocal ship_dismissed
        if button == GET_SHIP and not ship_dismissed:
            ship_dismissed = True
            return True
        return False

    helper.appear.side_effect = appear
    helper._ship_lock_appear = Mock(return_value=True)
    state = record()
    helper._collect(state, Mock())
    assert helper.device.click.call_args_list == [call(FOGGY_COLLECT_ALL), call(GET_SHIP),
                                                call(REWARD_SHIP_LOCK_CONFIRM)]
    assert state['ship_lock_attempts'] == 1


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


def test_collector_exception_is_captured_before_home_replaces_the_failed_frame(monkeypatch, tmp_path):
    navigation = mock_navigation(monkeypatch)
    helper = controller()
    error = RuntimeError('collector watchdog')
    helper._collect = Mock(side_effect=error)

    def return_main():
        rewards.save_event_diagnostic.assert_called_once_with(helper.device, 'f2p_event_free_pt_exception')
        return True

    navigation._return_event_hub_main.side_effect = return_main
    with pytest.raises(RuntimeError) as caught:
        helper.run(EVENT, folder=tmp_path)
    assert caught.value is error
    saved = load_reward_observations('f2p', folder=tmp_path)[EVENT + ':free_pt_collection']
    assert saved['diagnostics'] == ['/tmp/pt.png']
    assert saved['status'] == 'failed' and saved['returned_main'] is True


def test_account_audits_are_separate(monkeypatch, tmp_path):
    mock_navigation(monkeypatch)
    helper = controller()
    helper._collect = Mock(side_effect=lambda state, persist: state.update(status='complete'))
    helper.run(EVENT, folder=tmp_path)
    helper.config.config_name = 'alas'
    helper.run(EVENT, folder=tmp_path)
    assert load_reward_observations('f2p', folder=tmp_path)[EVENT + ':free_pt_collection']['invocations'] == 1
    assert load_reward_observations('alas', folder=tmp_path)[EVENT + ':free_pt_collection']['invocations'] == 1
