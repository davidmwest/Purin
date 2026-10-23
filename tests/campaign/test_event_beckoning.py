from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, call

import numpy as np
import pytest
from PIL import Image

from module.campaign import event_beckoning as beckoning
from module.campaign.event_beckoning import BeckoningTickets, EVENT
from module.campaign.event_beckoning_assets import (
    BECKONING_FIRST_LOCK, BECKONING_FIRST_STORY, BECKONING_MONTHLY_COLLECT,
    BECKONING_MONTHLY_EARNED, BECKONING_VICTORY_COLLECT, BECKONING_VICTORY_MISSION)
from module.campaign.event_completion import load_reward_observations, save_reward_observations
from module.campaign.event_completion_assets import TEMPESTA_SIDE_CHOICE_BACK, TEMPESTA_SIDE_CHOICE_PAGE
from module.combat.assets import GET_ITEMS_1
from module.exception import GameNotRunningError


def controller(frames=8):
    helper = BeckoningTickets.__new__(BeckoningTickets)
    helper.config = SimpleNamespace(config_name='f2p')
    helper.device = SimpleNamespace(click=Mock(), app_is_running=Mock(return_value=True), image=None)
    helper.loop = Mock(side_effect=lambda **kwargs: iter(range(frames)))
    helper._on_choice = Mock(return_value=True)
    helper._matches = Mock(return_value=True)
    helper._tickets = Mock(return_value=0)
    helper._earned = Mock(return_value=True)
    helper._capture = Mock(return_value='/tmp/beckoning.png')
    return helper


@pytest.fixture
def immediate(monkeypatch):
    timer = Mock()
    timer.start.return_value = timer
    timer.reached.return_value = True
    monkeypatch.setattr(beckoning, 'Timer', Mock(return_value=timer))


def record(rows=None):
    return dict(status='collecting', rows=rows or {}, diagnostics=[], story_complete=False)


def test_observed_missions_claim_controls_and_first_lock_match_actual_choice():
    image = np.asarray(Image.open('tests/campaign/data/tempesta_side_choice.png').convert('RGB'))
    for button in (BECKONING_FIRST_LOCK, BECKONING_FIRST_STORY, BECKONING_MONTHLY_COLLECT,
                   BECKONING_MONTHLY_EARNED, BECKONING_VICTORY_COLLECT, BECKONING_VICTORY_MISSION):
        assert button.match_template_color(image, offset=(3, 3), similarity=.85)
        assert not button.match_template_color(np.zeros_like(image), offset=(3, 3), similarity=.85)
    # The mission crop itself includes the monthly completed (1/1).
    assert BECKONING_MONTHLY_EARNED.area == (918, 481, 1063, 531)


@pytest.mark.parametrize('missing', [TEMPESTA_SIDE_CHOICE_BACK, TEMPESTA_SIDE_CHOICE_PAGE])
def test_choice_requires_both_title_and_back(missing):
    helper = controller()
    helper._matches = Mock(side_effect=lambda button: button != missing)
    assert BeckoningTickets._on_choice(helper) is False
    helper.device.click.assert_not_called()


@pytest.mark.parametrize('counter,earned', [((15, 0, 15), True), ((14, 1, 15), False),
                                           ((0, 0, 0), False), ((15, 0, 20), False)])
def test_victory_claim_requires_the_earned_fifteen_mission(monkeypatch, counter, earned):
    helper = controller()
    monkeypatch.setattr(beckoning.OCR_BECKONING_VICTORIES, 'ocr', Mock(return_value=counter))
    assert BeckoningTickets._earned(helper, 'victories') is earned


def test_each_row_is_audited_before_its_single_claim_and_postclaim_capture(immediate):
    helper = controller()
    helper._tickets.side_effect = [0, 0, 1, 1, 2, 2]
    audit = record()
    saved = []

    def persist():
        saved.append((deepcopy(audit), helper.device.click.call_count))

    assert helper._collect(audit, persist) is True
    assert helper.device.click.call_args_list == [call(BECKONING_MONTHLY_COLLECT), call(BECKONING_VICTORY_COLLECT)]
    assert saved[0][0]['rows']['monthly']['pending'] is True and saved[0][1] == 0
    victory_pending = [(data, clicks) for data, clicks in saved
                       if data['rows'].get('victories', {}).get('pending')]
    assert victory_pending[0][1] == 1
    assert all(row['verified'] and row['clicks'] == 1 and not row['pending'] for row in audit['rows'].values())
    assert audit['tickets'] == 2 and audit['story_complete'] is False
    helper._capture.assert_any_call(audit, 'postclaims')
    helper.loop.assert_called_once_with(skip_first=False, timeout=45)


def test_missing_per_row_mission_cannot_authorize_a_collect(immediate):
    helper = controller()
    helper._matches.side_effect = lambda button: button not in (BECKONING_MONTHLY_EARNED, BECKONING_VICTORY_MISSION)
    assert helper._collect(record(), Mock()) is True
    helper.device.click.assert_not_called()


def test_unearned_or_unreadable_victory_never_spends_its_row_budget(immediate):
    helper = controller()
    helper._earned.return_value = False
    audit = record(dict(monthly=dict(clicks=1, pending=False, verified=True)))
    assert helper._collect(audit, Mock()) is False
    assert audit['reason'] == 'victories_mission_not_verified_earned'
    assert 'victories' not in audit['rows']
    helper.device.click.assert_not_called()


def test_rejected_or_interrupted_row_claim_is_never_clicked_again(immediate):
    helper = controller()
    audit = record(dict(monthly=dict(clicks=1, pending=True, tickets_before=0)))
    assert helper._collect(audit, Mock()) is False
    assert audit['reason'] == 'ticket_collection_timeout'
    helper.device.click.assert_not_called()


@pytest.mark.parametrize('tickets', [0, None])
def test_disappearing_collect_without_verified_ticket_growth_stays_pending(immediate, tickets):
    helper = controller()
    helper._matches.return_value = False
    helper._tickets.return_value = tickets
    audit = record(dict(monthly=dict(clicks=1, pending=True, verified=False, tickets_before=0)))
    assert helper._collect(audit, Mock()) is False
    assert audit['rows']['monthly']['pending'] is True
    assert audit['rows']['monthly']['verified'] is False
    helper.device.click.assert_not_called()


def test_one_transient_increased_ticket_frame_cannot_verify_a_claim(immediate):
    helper = controller(4)
    helper._tickets.side_effect = [0, 1, 0, 0]
    audit = record(dict(monthly=dict(clicks=1, pending=True, verified=False, tickets_before=0)))
    assert helper._collect(audit, Mock()) is False
    assert audit['rows']['monthly']['pending'] is True
    helper.device.click.assert_not_called()


def test_unreadable_baseline_never_spends_a_claim_budget(immediate):
    helper = controller()
    helper._tickets.return_value = None
    audit = record()
    assert helper._collect(audit, Mock()) is False
    assert audit['rows'] == {}
    helper.device.click.assert_not_called()


def test_unknown_ui_before_our_claim_never_dismisses_a_reward(immediate, monkeypatch):
    helper = controller()
    helper._on_choice.return_value = False
    receipts = Mock(return_value=GET_ITEMS_1)
    monkeypatch.setattr(beckoning.EventRewards, '_reward_dialog', receipts)
    assert helper._collect(record(), Mock()) is False
    receipts.assert_not_called()
    helper.device.click.assert_not_called()


def test_known_receipts_are_bounded_only_after_pending_claim(immediate, monkeypatch):
    helper = controller(20)
    helper._on_choice.side_effect = [True, True] + [False] * 18
    monkeypatch.setattr(beckoning.EventRewards, '_reward_dialog', Mock(return_value=GET_ITEMS_1))
    monkeypatch.setattr(beckoning, 'RECEIPTS_MAX', 2)
    audit = record()
    assert helper._collect(audit, Mock()) is False
    assert audit['reason'] == 'ticket_receipt_limit'
    assert helper.device.click.call_args_list == [call(BECKONING_MONTHLY_COLLECT), call(GET_ITEMS_1), call(GET_ITEMS_1)]


def test_positive_stable_ticket_and_exact_card_allow_one_audited_observation(immediate):
    helper = controller()
    helper._tickets.return_value = 2
    helper._on_choice.side_effect = [True, True, False]
    state, audit, saved = {}, record(), []

    def persist():
        saved.append((deepcopy(state), helper.device.click.call_count))

    helper._inspect_first_unlock(audit, state, 'unlock', persist)
    assert saved[0][0]['unlock']['card_clicks'] == 1 and saved[0][1] == 0
    helper.device.click.assert_called_once_with(BECKONING_FIRST_LOCK)
    assert audit['unlock']['status'] == 'observed'
    assert audit['unlock']['diagnostic'] == '/tmp/beckoning.png'
    assert audit['story_complete'] is False
    helper._capture.assert_called_once_with(audit, 'first_unlock_observed')


@pytest.mark.parametrize('tickets', [0, None])
def test_zero_or_unreadable_ticket_does_not_spend_the_unlock_budget(immediate, tickets):
    helper = controller()
    helper._tickets.return_value = tickets
    state, audit = {}, record()
    helper._inspect_first_unlock(audit, state, 'unlock', Mock())
    assert state == {} and audit['unlock']['status'] == 'deferred'
    helper.device.click.assert_not_called()


def test_unstable_ticket_count_never_authorizes_a_card(immediate):
    helper = controller(4)
    helper._tickets.side_effect = [1, 2, 1, 2]
    state, audit = {}, record()
    helper._inspect_first_unlock(audit, state, 'unlock', Mock())
    assert audit['unlock']['reason'] == 'ticket_count_unstable' and state == {}
    helper.device.click.assert_not_called()


@pytest.mark.parametrize('missing', [BECKONING_FIRST_STORY, BECKONING_FIRST_LOCK])
def test_missing_exact_locked_card_prevents_the_observation(immediate, missing):
    helper = controller()
    helper._tickets.return_value = 1
    helper._matches.side_effect = lambda button: button != missing
    state, audit = {}, record()
    helper._inspect_first_unlock(audit, state, 'unlock', Mock())
    assert state == {} and audit['unlock']['status'] == 'unsupported'
    helper.device.click.assert_not_called()


def test_interrupted_unlock_observation_is_never_replayed(immediate):
    helper = controller()
    state, audit = {'unlock': dict(status='inspecting', card_clicks=1)}, record()
    helper._inspect_first_unlock(audit, state, 'unlock', Mock())
    assert audit['unlock']['reused'] is True
    helper._on_choice.assert_not_called()
    helper.device.click.assert_not_called()


def test_rejected_card_click_is_bounded_without_unknown_confirmation(immediate):
    helper = controller()
    helper._tickets.return_value = 1
    state, audit = {}, record()
    helper._inspect_first_unlock(audit, state, 'unlock', Mock())
    assert audit['unlock']['reason'] == 'first_card_did_not_open'
    assert audit['unlock']['status'] == 'blocked'
    helper.device.click.assert_called_once_with(BECKONING_FIRST_LOCK)


@pytest.mark.parametrize('event,account', [(EVENT, 'alas'), ('campaign_main', 'f2p')])
def test_unrequested_account_or_event_has_no_navigation_or_audit(tmp_path, event, account):
    helper = controller()
    helper.config.config_name = account
    assert helper.run(event, folder=tmp_path)['status'] == 'unsupported'
    assert list(tmp_path.iterdir()) == []
    helper.device.app_is_running.assert_not_called()
    helper.device.click.assert_not_called()


def test_closed_game_does_not_spend_any_budget(tmp_path):
    helper = controller()
    helper.device.app_is_running.return_value = False
    with pytest.raises(GameNotRunningError):
        helper.run(EVENT, folder=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_run_preserves_row_audits_and_returns_through_known_helper(monkeypatch, tmp_path):
    previous = dict(monthly=dict(clicks=1, pending=False, verified=True))
    save_reward_observations('f2p', {EVENT + ':beckoning_ticket_collection': dict(rows=previous)}, folder=tmp_path)
    helper = controller()

    def navigate(audit, **kwargs):
        saved = load_reward_observations('f2p', folder=tmp_path)
        assert saved[EVENT + ':beckoning_ticket_collection']['rows'] == previous
        audit['status'] = 'observed'

    navigator = Mock()
    navigator._inspect_event_side_story.side_effect = navigate
    navigator._return_event_hub_main.return_value = True
    monkeypatch.setattr(beckoning, 'EventCompletion', Mock(return_value=navigator))
    helper._collect = Mock(return_value=True)
    helper._inspect_first_unlock = Mock()
    audit = helper.run(EVENT, folder=tmp_path)
    assert audit['returned_main'] is True and audit['story_complete'] is False
    assert audit['rows'] == previous
    helper._inspect_first_unlock.assert_called_once()
    navigator._return_event_hub_main.assert_called_once()
    assert load_reward_observations('f2p', folder=tmp_path)[EVENT + ':beckoning_ticket_collection']['returned_main'] is True
