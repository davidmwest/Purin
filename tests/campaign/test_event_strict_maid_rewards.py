from types import SimpleNamespace
from unittest.mock import Mock, call

import numpy as np
import pytest
from PIL import Image

from module.campaign import event_strict_maid_rewards as rewards
from module.campaign.event_completion import load_reward_observations
from module.campaign.event_strict_maid_rewards import StrictMaidRewards
from module.campaign.event_strict_maid_rewards_assets import (
    OCR_STRICT_MAID_TOTAL, STRICT_MAID_COLLECT_ALL, STRICT_MAID_EARNED, STRICT_MAID_HOME,
    STRICT_MAID_PAGE, STRICT_MAID_PROCEED, STRICT_MAID_ROW_COLLECT,
    STRICT_MAID_ROW_COLLECTED)
from module.combat.assets import GET_ITEMS_1, GET_SHIP
from module.exception import GameNotRunningError

EVENT = 'event_20261022_cn'


def captured():
    return np.asarray(Image.open('tests/campaign/data/strict_maid_rewards.png').convert('RGB')).copy()


def actual(account):
    return np.asarray(Image.open(f'tests/campaign/data/strict_maid_{account}_failed.png').convert('RGB')).copy()


def all_collected():
    image = actual('alas')
    image[519:570, 1138:1201] = image[519:570, 665:728]
    return image


def controller(frames=10, image=None):
    helper = StrictMaidRewards.__new__(StrictMaidRewards)
    helper.config = SimpleNamespace(config_name='f2p')
    helper.device = SimpleNamespace(app_is_running=Mock(return_value=True), click=Mock(), stuck_record_clear=Mock(),
                                    image=captured() if image is None else image)
    helper.loop = Mock(return_value=iter(range(frames)))
    helper.appear = Mock(return_value=False)
    helper.match_template_color = lambda button, **kwargs: button.match_template_color(helper.device.image, **kwargs)
    return helper


@pytest.fixture
def immediate(monkeypatch):
    timer = Mock()
    timer.start.return_value = timer
    timer.reached.return_value = True
    monkeypatch.setattr(rewards, 'Timer', Mock(return_value=timer))


def record():
    return dict(status='collecting', claims=0, pending_claim=False)


def remove_pink_controls(image):
    for left, top, right, bottom in ((657, 511, 735, 578), (657, 585, 735, 654), (1131, 511, 1208, 578)):
        image[top:bottom, left:right] = (245, 242, 236)
    return image


def test_actual_screen_recognizes_page_home_all_and_earned_but_proceed_is_detection_only():
    image = captured()
    for button in (STRICT_MAID_PAGE, STRICT_MAID_HOME, STRICT_MAID_COLLECT_ALL,
                   STRICT_MAID_EARNED, STRICT_MAID_PROCEED):
        assert button.match_template_color(image, offset=(3, 3), similarity=.9)
        assert not button.match_template_color(image * 0, offset=(3, 3), similarity=.85)
    assert controller(image=image)._strict_state() == 'claimable'


@pytest.mark.parametrize('account,expected,total', [
    ('f2p', ['claimable', 'claimable', 'claimable', 'proceed'], 0),
    ('alas', ['collected', 'claimable', 'collected', 'collected'], 2100)])
def test_actual_failure_frames_recognize_each_row_and_total(account, expected, total):
    helper = controller(image=actual(account))
    assert helper._strict_rows() == expected
    assert helper._strict_total() == total
    for index, state in enumerate(expected):
        if state != 'claimable':
            continue
        left, top, right, bottom = STRICT_MAID_ROW_COLLECT[index].button
        assert right - left < 35 and bottom - top < 25
        assert 510 < top < bottom < 655
        assert (665 <= left < right <= 729) if index % 2 == 0 else (1138 <= left < right <= 1202)


def test_missing_rows_do_not_prove_empty_and_all_gray_is_positive_empty():
    assert controller(image=remove_pink_controls(captured()))._strict_state() == 'unknown'
    assert controller(image=all_collected())._strict_state() == 'empty'


@pytest.mark.parametrize('missing', [STRICT_MAID_PAGE, STRICT_MAID_HOME, STRICT_MAID_COLLECT_ALL])
def test_missing_exact_page_or_action_never_authorizes_claim(missing):
    helper = controller()
    helper.match_template_color = Mock(side_effect=lambda button, **kwargs: button != missing)
    assert helper._strict_state() == 'unknown'
    assert helper.device.stuck_record_clear.call_count == (1 if missing == STRICT_MAID_COLLECT_ALL else 0)


def test_unmatched_pink_control_is_unknown_and_absent_rows_do_not_prove_empty():
    image = remove_pink_controls(captured())
    image[527:559, 674:718] = (244, 190, 210)
    helper = controller(image=image)
    assert helper._strict_state() == 'unknown'
    image[511:654, 657:735] = (245, 242, 236)
    image[585:654, 1131:1208] = (245, 242, 236)
    assert helper._strict_state() == 'unknown'  # No known unfinished/empty-row witness.


def test_actual_main_claim_is_persisted_then_verified_by_total_growth(immediate):
    helper = controller(8, image=actual('alas'))
    helper._strict_total = Mock(side_effect=lambda: 2300 if helper.device.click.called else 2100)
    helper._reward_dialog = Mock(return_value=None)
    state = record()
    audit = []

    def persist():
        if not audit:
            assert not helper.device.click.called
        audit.append(dict(state))

    def click(button):
        assert button is STRICT_MAID_ROW_COLLECT[1]
        helper.device.image = all_collected()

    helper.device.click.side_effect = click
    helper._collect_strict(state, persist)
    assert audit[0]['claims'] == 1 and audit[0]['pending_claim'] is True
    assert state['status'] == 'complete' and state['pending_claim'] is False
    assert state['verified_claims'] == 1
    assert state['claim_audit'] == [dict(row=1, total_before=2100, verified=True, total_after=2300)]
    assert helper.device.click.call_args_list == [call(STRICT_MAID_ROW_COLLECT[1])]
    helper.loop.assert_called_once_with(skip_first=False, timeout=90)


def test_actual_f2p_three_earned_rows_claim_once_each_never_proceed(immediate):
    helper = controller(14, image=actual('f2p'))
    helper._strict_total = Mock(side_effect=lambda: 100 * helper.device.click.call_count)
    helper._reward_dialog = Mock(return_value=None)
    gray = actual('alas')[519:570, 665:728]

    def click(button):
        index = STRICT_MAID_ROW_COLLECT.index(button)
        left, top = ((665, 519), (1138, 519), (665, 594))[index]
        helper.device.image[top:top + 51, left:left + 63] = gray

    helper.device.click.side_effect = click
    state = record()
    helper._collect_strict(state, Mock())
    assert state['status'] == 'complete' and state['verified_claims'] == 3
    assert state['total'] == 300 and state['pending_claim'] is False
    assert helper.device.click.call_args_list == [call(button) for button in STRICT_MAID_ROW_COLLECT[:3]]
    assert all(audit['verified'] for audit in state['claim_audit'])


def test_no_earned_rewards_requires_three_frames_and_never_clicks_proceed(immediate):
    helper = controller(3, image=all_collected())
    helper._strict_total = Mock(return_value=2300)
    state = record()
    helper._collect_strict(state, Mock())
    assert state['status'] == 'complete'
    helper.device.click.assert_not_called()


def test_transient_empty_does_not_report_complete(immediate):
    helper = controller(3)
    helper._strict_rows = Mock(return_value=['collected'] * 4)
    helper._strict_state = Mock(side_effect=['empty', 'unknown', 'empty'])
    helper._strict_total = Mock(return_value=2300)
    helper._reward_dialog = Mock()
    state = record()
    helper._collect_strict(state, Mock())
    assert state['status'] == 'failed'
    helper._reward_dialog.assert_not_called()
    helper.device.click.assert_not_called()


def test_rejected_row_click_is_not_repeated_without_verified_growth(immediate):
    helper = controller()
    helper._strict_total = Mock(return_value=0)
    helper._reward_dialog = Mock(return_value=None)
    state = record()
    helper._collect_strict(state, Mock())
    assert state['reason'] == 'earned_mission_collection_timeout'
    assert state['pending_claim'] is True and not state.get('verified_claims')
    assert helper.device.click.call_args_list == [call(STRICT_MAID_ROW_COLLECT[0])]


@pytest.mark.parametrize('totals', [[0] * 8, [0, 0, 100, 0, 100, 0, 100, 0],
                                  [0, 0, None, None, None, None, None, None]])
def test_disappearing_collect_or_transient_total_never_verifies_claim(immediate, totals):
    helper = controller(8)
    helper._strict_total = Mock(side_effect=totals)
    helper._reward_dialog = Mock(return_value=None)
    helper.device.click.side_effect = lambda button: setattr(helper.device, 'image', all_collected())
    state = record()
    helper._collect_strict(state, Mock())
    assert state['status'] == 'failed' and state['pending_claim'] is True
    assert state['claims'] == 1 and not state.get('verified_claims')
    helper.device.click.assert_called_once_with(STRICT_MAID_ROW_COLLECT[0])


@pytest.mark.parametrize('raw', ['', 'bad', '1400/1400', '999999', '2301'])
def test_invalid_total_is_unknown_and_never_authorizes_claim(immediate, monkeypatch, raw):
    monkeypatch.setattr(OCR_STRICT_MAID_TOTAL, 'ocr', Mock(return_value=raw))
    helper = controller()
    assert helper._strict_total() is None
    helper._collect_strict(record(), Mock())
    helper.device.click.assert_not_called()


def test_claim_limit_is_checked_before_action(immediate, monkeypatch):
    monkeypatch.setattr(rewards, 'CLAIMS_MAX', 0)
    helper = controller()
    helper._strict_total = Mock(return_value=0)
    state = record()
    helper._collect_strict(state, Mock())
    assert state['reason'] == 'earned_mission_claim_limit'
    helper.device.click.assert_not_called()


def test_known_receipts_are_not_dismissed_before_claim_and_are_capped_after(immediate, monkeypatch):
    helper = controller()
    helper._strict_rows = Mock(return_value=None)
    helper._reward_dialog = Mock(return_value=GET_SHIP)
    state = record()
    helper._collect_strict(state, Mock())
    helper._reward_dialog.assert_not_called()
    helper.device.click.assert_not_called()

    helper.loop = Mock(return_value=iter(range(8)))
    helper._strict_rows.side_effect = [['claimable'] * 4] * 2 + [None] * 6
    helper._strict_state = Mock(return_value='claimable')
    helper._strict_total = Mock(return_value=0)
    monkeypatch.setattr(rewards, 'REWARD_DIALOGS_MAX', 2)
    state = record()
    helper._collect_strict(state, Mock())
    assert state['reason'] == 'reward_dialog_limit'
    assert helper.device.click.call_args_list == [call(STRICT_MAID_ROW_COLLECT[0]), call(GET_SHIP), call(GET_SHIP)]


def test_exact_strict_page_home_is_bounded_and_requires_verified_main(immediate):
    helper = controller(3)
    helper.ui_page_appear = Mock(side_effect=[False, False, True])
    navigation = Mock()
    assert helper._return_strict_main(navigation) is True
    assert helper.device.click.call_args_list == [call(STRICT_MAID_HOME)] * 2
    navigation._return_event_hub_main.assert_not_called()

    helper = controller()
    helper.ui_page_appear = Mock(return_value=False)
    assert helper._return_strict_main(navigation) is False
    assert helper.device.click.call_args_list == [call(STRICT_MAID_HOME)] * 2


@pytest.mark.parametrize('missing', [STRICT_MAID_PAGE, STRICT_MAID_HOME])
def test_missing_strict_title_or_home_does_not_click_home(immediate, missing):
    helper = controller()
    helper.ui_page_appear = Mock(return_value=False)
    helper.match_template_color = Mock(side_effect=lambda button, **kwargs: button != missing)
    navigation = Mock()
    navigation._return_event_hub_main.return_value = False
    assert helper._return_strict_main(navigation) is False
    helper.device.click.assert_not_called()
    navigation._return_event_hub_main.assert_called_once()


def mock_navigation(monkeypatch):
    navigation = Mock()
    navigation._inspect_event_rewards_panel.side_effect = lambda state, **kwargs: state.update(status='observed')
    monkeypatch.setattr(rewards, 'EventCompletion', Mock(return_value=navigation))
    monkeypatch.setattr(rewards, 'save_event_diagnostic', Mock(return_value='/tmp/maid.png'))
    return navigation


def test_run_audits_before_navigation_and_rechecks_later_earned_missions(monkeypatch, tmp_path):
    navigation = mock_navigation(monkeypatch)
    helper = controller()
    helper._return_strict_main = Mock(return_value=True)

    def collect(state, persist):
        saved = load_reward_observations('f2p', folder=tmp_path)[EVENT + ':strict_maid_collection']
        assert saved['status'] == 'collecting'
        state.update(status='complete', pending_claim=False)

    helper._collect_strict = Mock(side_effect=collect)
    assert helper.run(EVENT, folder=tmp_path)['status'] == 'complete'
    result = helper.run(EVENT, folder=tmp_path)
    assert result['status'] == 'complete' and result['invocations'] == 2
    assert result['diagnostics'] == ['/tmp/maid.png']
    assert navigation._inspect_event_rewards_panel.call_count == helper._collect_strict.call_count == 2
    helper._return_strict_main.assert_called_with(navigation)


def test_failed_return_cannot_claim_completion_and_is_persisted(monkeypatch, tmp_path):
    mock_navigation(monkeypatch)
    helper = controller()
    helper._collect_strict = Mock(side_effect=lambda state, persist: state.update(status='complete'))
    helper._return_strict_main = Mock(return_value=False)
    result = helper.run(EVENT, folder=tmp_path)
    assert result['status'] == 'failed' and result['reason'] == 'strict_maid_main_return_unrecognized'
    assert load_reward_observations('f2p', folder=tmp_path)[EVENT + ':strict_maid_collection'] == result


def test_unrecognized_entry_never_claims(monkeypatch, tmp_path):
    navigation = mock_navigation(monkeypatch)
    navigation._inspect_event_rewards_panel.side_effect = lambda state, **kwargs: state.update(status='blocked')
    helper = controller()
    helper._collect_strict = Mock()
    helper._return_strict_main = Mock(return_value=True)
    assert helper.run(EVENT, folder=tmp_path)['status'] == 'failed'
    helper._collect_strict.assert_not_called()


def test_exception_captures_evidence_before_home_then_persists_failure(monkeypatch, tmp_path):
    mock_navigation(monkeypatch)
    helper = controller()
    helper._collect_strict = Mock(side_effect=RuntimeError('unrecognized reward state'))
    order = []
    rewards.save_event_diagnostic.side_effect = lambda *args: order.append('capture') or '/tmp/failed-maid.png'
    helper._return_strict_main = Mock(side_effect=lambda *args: order.append('home') or True)
    with pytest.raises(RuntimeError, match='unrecognized reward state'):
        helper.run(EVENT, folder=tmp_path)
    assert order == ['capture', 'home']
    saved = load_reward_observations('f2p', folder=tmp_path)[EVENT + ':strict_maid_collection']
    assert saved['status'] == 'failed' and saved['reason'] == 'strict_maid_collection_exception'
    assert saved['diagnostics'] == ['/tmp/failed-maid.png'] and saved['returned_main'] is True


def test_unsupported_event_and_closed_game_do_not_spend_or_navigate(tmp_path):
    helper = controller()
    assert helper.run('campaign_main', folder=tmp_path)['status'] == 'unsupported'
    helper.device.app_is_running.assert_not_called()
    helper.device.app_is_running.return_value = False
    with pytest.raises(GameNotRunningError):
        helper.run(EVENT, folder=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_accounts_keep_separate_claim_audits(monkeypatch, tmp_path):
    mock_navigation(monkeypatch)
    helper = controller()
    helper._collect_strict = Mock(side_effect=lambda state, persist: state.update(status='complete'))
    helper._return_strict_main = Mock(return_value=True)
    helper.run(EVENT, folder=tmp_path)
    helper.config.config_name = 'alas'
    helper.run(EVENT, folder=tmp_path)
    for account in ('f2p', 'alas'):
        assert load_reward_observations(account, folder=tmp_path)[EVENT + ':strict_maid_collection']['invocations'] == 1
