from types import SimpleNamespace
from unittest.mock import Mock, call

import numpy as np
import pytest
from PIL import Image

from module.campaign import event_treasure_free_packs as treasure
from module.campaign.event_completion import load_reward_observations, save_reward_observations
from module.campaign.event_treasure_free_packs import EventTreasureFreePacks, EVENT, KEY
from module.campaign.event_treasure_free_pack_assets import (
    TREASURE_CARDS, TREASURE_DATES, TREASURE_HEADER, TREASURE_HOME)
from module.campaign.event_main_notifications import DAY7
from module.combat.assets import GET_ITEMS_1
from module.exception import GamePageUnknownError, ScriptError
from module.ui.page import page_main


def captured():
    return np.asarray(Image.open('tests/campaign/data/treasure_free_packs.png').convert('RGB')).copy()


def controller(frames=24):
    helper = EventTreasureFreePacks.__new__(EventTreasureFreePacks)
    helper.config = SimpleNamespace(config_name='alas')
    helper.device = SimpleNamespace(image=captured(), click=Mock(), screenshot=Mock(),
                                    app_is_running=Mock(return_value=True), stuck_record_clear=Mock())
    helper.loop = Mock(side_effect=lambda **kwargs: iter(range(frames)))
    helper.match_template_color = lambda button, **kwargs: button.match_template_color(helper.device.image, **kwargs)
    helper.ui_page_appear = Mock(return_value=False)
    helper._reward_dialog = Mock(return_value=None)
    return helper


def record():
    return dict(status='collecting', diagnostics=[], cards={day: dict(claim_clicks=0, pending=False,
                receipt_seen=False, verified=False) for day in TREASURE_CARDS},
                entry_clicks=0, home_clicks=0, reward_dialogs=0, invocations=0)


@pytest.fixture
def immediate(monkeypatch):
    timer = Mock()
    timer.start.return_value = timer
    timer.reached.return_value = True
    monkeypatch.setattr(treasure, 'Timer', Mock(return_value=timer))


def remove(image, button):
    left, top, right, bottom = button.area
    image[top:bottom, left:right] = 0


def test_actual_free_day2_and_earlier_day1_are_recognized_and_paid_cards_are_outside_clicks():
    helper = controller()
    assert helper._treasure_page()
    for day, (free, name, claim) in TREASURE_CARDS.items():
        assert helper._claimable(day)
        assert all(helper._exact(button) for button in (free, name, claim))
        left, top, right, bottom = claim.button
        assert 555 < top < bottom < 595
        assert (365 < left < right < 490) if day == 'day2' else (810 < left < right < 935)
        assert not (575 <= left <= 720 or 1015 <= left <= 1165)  # Actual paid card controls.


@pytest.mark.parametrize('missing', [TREASURE_HEADER, TREASURE_DATES, TREASURE_HOME,
    *TREASURE_CARDS['day2'], *TREASURE_CARDS['day1']])
def test_every_exact_page_free_name_and_claimable_witness_is_required(missing):
    helper = controller()
    remove(helper.device.image, missing)
    if missing in (TREASURE_HEADER, TREASURE_DATES, TREASURE_HOME):
        assert not helper._treasure_page()
        assert not helper._claimable('day1') and not helper._claimable('day2')
    else:
        day = 'day2' if missing in TREASURE_CARDS['day2'] else 'day1'
        assert not helper._claimable(day)


def test_both_free_cards_claim_once_only_after_durable_proof_and_known_receipts(immediate):
    helper = controller()
    state = record()
    received = []
    receipt = [None]
    snapshots = []

    def persist():
        snapshots.append((state['cards']['day2'].copy(), state['cards']['day1'].copy()))

    def click(button):
        if button is GET_ITEMS_1:
            day = receipt[0]
            assert state['cards'][day]['receipt_seen']
            received.append(day)
            remove(helper.device.image, TREASURE_CARDS[day][2])
            receipt[0] = None
            return
        day = next(day for day, buttons in TREASURE_CARDS.items() if button is buttons[2])
        assert state['cards'][day]['claim_clicks'] == 1 and state['cards'][day]['pending']
        assert snapshots and snapshots[-1][0 if day == 'day2' else 1]['pending']
        receipt[0] = day

    helper.device.click.side_effect = click
    helper._reward_dialog = Mock(side_effect=lambda: GET_ITEMS_1 if receipt[0] else None)
    helper._collect_free(state, persist)
    assert state['status'] == 'complete'
    assert received == ['day2', 'day1']
    assert all(row['verified'] and not row['pending'] and row['claim_clicks'] == 1
               for row in state['cards'].values())
    assert helper.device.click.call_args_list == [call(TREASURE_CARDS['day2'][2]), call(GET_ITEMS_1),
                                                call(TREASURE_CARDS['day1'][2]), call(GET_ITEMS_1)]


@pytest.mark.parametrize('receipt,disappears', [(False, True), (True, False), (False, False)])
def test_absence_or_receipt_alone_never_verifies_or_repeats_a_free_claim(immediate, receipt, disappears):
    helper = controller()
    state = record()
    dialog = [None]
    def click(button):
        if button is TREASURE_CARDS['day2'][2]:
            if disappears:
                remove(helper.device.image, button)
            if receipt:
                dialog[0] = GET_ITEMS_1
        elif button is GET_ITEMS_1:
            dialog[0] = None
    helper.device.click.side_effect = click
    helper._reward_dialog = Mock(side_effect=lambda: dialog[0])
    helper._collect_free(state, Mock())
    assert state['status'] == 'blocked'
    assert state['cards']['day2']['pending'] and not state['cards']['day2']['verified']
    assert helper.device.click.call_args_list.count(call(TREASURE_CARDS['day2'][2])) == 1
    assert call(TREASURE_CARDS['day1'][2]) not in helper.device.click.call_args_list


def test_unknown_postclaim_confirmation_is_preserved_without_exit_or_confirmation(immediate):
    helper = controller()
    state = record()
    helper._treasure_page = Mock(side_effect=lambda: not helper.device.click.called)
    helper.device.click.side_effect = lambda button: None
    helper._collect_free(state, Mock())
    assert not helper._return_main(state, Mock())
    helper.device.click.assert_called_once_with(TREASURE_CARDS['day2'][2])


def test_transient_claim_disappearance_does_not_settle_pending(immediate):
    helper = controller(frames=4)
    state = record()
    state['cards']['day2'].update(claim_clicks=1, pending=True, receipt_seen=True)
    helper._exact = Mock(side_effect=lambda button: button is not TREASURE_CARDS['day2'][2])
    helper._card_base = Mock(return_value=True)
    helper._treasure_page = Mock(side_effect=[True, False, True, True])
    helper._collect_free(state, Mock())
    assert state['cards']['day2']['pending'] and not state['cards']['day2']['verified']
    helper.device.click.assert_not_called()


def test_persisted_pending_claim_never_reclicks_after_restart(immediate, monkeypatch, tmp_path):
    state = record()
    state['cards']['day2'].update(claim_clicks=1, pending=True, receipt_seen=False)
    save_reward_observations('alas', {KEY: state}, folder=str(tmp_path))
    helper = controller()
    monkeypatch.setattr(treasure, 'save_event_diagnostic', Mock(return_value='pending.png'))
    helper.run(EVENT, folder=str(tmp_path))
    saved = load_reward_observations('alas', folder=str(tmp_path))[KEY]
    assert saved['cards']['day2']['pending'] and saved['cards']['day2']['claim_clicks'] == 1
    assert saved['returned_main'] is False
    helper.device.click.assert_not_called()


def test_durable_owned_receipt_can_settle_after_restart_without_reclaim(immediate, monkeypatch, tmp_path):
    state = record()
    state['cards']['day2'].update(claim_clicks=1, pending=True, receipt_seen=True)
    state['cards']['day1'].update(claim_clicks=1, verified=True, receipt_seen=True)
    save_reward_observations('alas', {KEY: state}, folder=str(tmp_path))
    helper = controller()
    remove(helper.device.image, TREASURE_CARDS['day2'][2])
    helper._return_main = Mock(return_value=True)
    monkeypatch.setattr(treasure, 'save_event_diagnostic', Mock(return_value='complete.png'))
    result = helper.run(EVENT, folder=str(tmp_path))
    assert result['status'] == 'complete' and result['cards']['day2']['verified']
    helper.device.click.assert_not_called()


def test_claim_crash_is_durably_reserved_before_click(immediate, monkeypatch, tmp_path):
    helper = controller()
    monkeypatch.setattr(treasure, 'save_event_diagnostic', Mock(return_value='crash.png'))
    def crash(button):
        saved = load_reward_observations('alas', folder=str(tmp_path))[KEY]
        assert saved['cards']['day2']['pending'] and saved['cards']['day2']['claim_clicks'] == 1
        raise RuntimeError('interrupted')
    helper.device.click.side_effect = crash
    with pytest.raises(RuntimeError):
        helper.run(EVENT, folder=str(tmp_path))
    saved = load_reward_observations('alas', folder=str(tmp_path))[KEY]
    assert saved['cards']['day2']['pending'] and saved['returned_main'] is False
    assert saved['diagnostics'] == ['crash.png']


def test_main_reentry_is_bounded_and_reserved_before_the_exact_day7_icon(immediate, monkeypatch):
    helper = controller()
    state = record()
    helper._treasure_page = Mock(side_effect=lambda: helper.device.click.called)
    helper.ui_page_appear = Mock(return_value=True)
    helper._exact = Mock(return_value=True)
    monkeypatch.setattr(treasure, 'ensure_event_page', Mock(return_value=True))
    persist = Mock()
    helper.device.click.side_effect = lambda button: (
        pytest.fail('wrong entry') if button is not DAY7 else
        (None if state['entry_clicks'] == 1 and persist.called else pytest.fail('not reserved')))
    assert helper._enter(state, persist)
    helper.device.click.assert_called_once_with(DAY7)


def test_unknown_or_exhausted_entry_budget_never_taps(immediate, monkeypatch):
    helper = controller()
    helper._treasure_page = Mock(return_value=False)
    helper.ui_page_appear = Mock(return_value=True)
    helper._exact = Mock(return_value=True)
    monkeypatch.setattr(treasure, 'ensure_event_page', Mock(return_value=True))
    state = record()
    state['entry_clicks'] = 2
    assert not helper._enter(state, Mock())
    helper.device.click.assert_not_called()


def test_home_uses_only_exact_panel_and_persists_one_click(immediate):
    helper = controller()
    state = record()
    helper.ui_page_appear = Mock(side_effect=lambda page: helper.device.click.called)
    persist = Mock()
    helper.device.click.side_effect = lambda button: (
        None if button is TREASURE_HOME and state['home_clicks'] == 1 and persist.called
        else pytest.fail('unsafe Home'))
    assert helper._return_main(state, persist)
    helper.device.click.assert_called_once_with(TREASURE_HOME)


def test_unknown_panel_cannot_return_with_home(immediate):
    helper = controller()
    remove(helper.device.image, TREASURE_HEADER)
    assert not helper._return_main(record(), Mock())
    helper.device.click.assert_not_called()


@pytest.mark.parametrize('account,event', [('f2p', EVENT), ('other', EVENT), ('alas', 'event_other')])
def test_other_account_or_event_has_no_actions_or_budget(tmp_path, account, event):
    helper = controller()
    helper.config.config_name = account
    assert helper.run(event, folder=str(tmp_path))['status'] == 'unsupported'
    assert not list(tmp_path.iterdir())
    helper.device.screenshot.assert_not_called()
    helper.device.click.assert_not_called()


def test_completion_hook_recovers_treasure_before_generic_observers_and_stops_unknown(monkeypatch):
    from module.campaign.event_completion import EventCompletion
    helper = EventCompletion.__new__(EventCompletion)
    helper.config = SimpleNamespace(config_name='alas')
    order = []
    helper._resume_completed_event_panel = Mock(side_effect=lambda event: order.append('milestone') or True)
    helper.resume_treasure_free_packs = Mock(side_effect=lambda event: order.append('treasure') or
                                             dict(status='blocked', returned_main=False))
    helper._resume_event_mission_receipt = Mock()
    helper.observe_event_main = Mock()
    with pytest.raises(GamePageUnknownError, match='Free treasure'):
        helper.run(EVENT)
    assert order == ['milestone', 'treasure']
    helper._resume_event_mission_receipt.assert_not_called()
    helper.observe_event_main.assert_not_called()


def test_rewards_disabled_never_constructs_treasure_collector(monkeypatch):
    from module.campaign.event_completion import EventCompletion
    helper = EventCompletion.__new__(EventCompletion)
    helper.config = SimpleNamespace(config_name='alas')
    helper.collect_treasure_free_packs = Mock()
    helper.run(EVENT, collect_rewards=False, complete_story=False)
    helper.collect_treasure_free_packs.assert_not_called()


@pytest.mark.parametrize('broken', [
    {}, dict(cards={}), dict(cards={'day1': {}}),
    {'claim_clicks': False}, {'claim_clicks': 2}, {'pending': 'true'},
    {'pending': True}, {'verified': True}, {'receipt_seen': True},
    {'claim_clicks': 1}, {'claim_clicks': 1, 'verified': True},
    {'claim_clicks': 1, 'pending': True, 'verified': True, 'receipt_seen': True},
])
def test_corrupted_claim_journal_fails_before_any_navigation_or_input(tmp_path, broken):
    helper = controller()
    state = record()
    if not broken or 'cards' in broken:
        state = broken
    else:
        state['cards']['day2'].update(broken)
    save_reward_observations('alas', {KEY: state}, folder=str(tmp_path))
    with pytest.raises(ScriptError):
        helper.run(EVENT, folder=str(tmp_path))
    helper.device.click.assert_not_called()
    helper.device.screenshot.assert_not_called()


def test_only_known_item_receipts_are_dismissed_never_ship_skin_or_generic_confirm():
    helper = controller()
    del helper._reward_dialog
    helper.appear = Mock(return_value=False)
    assert helper._reward_dialog() is None
    assert helper.appear.call_args_list == [call(button, offset=5) for button in
        (treasure.GET_ITEMS_1, treasure.GET_ITEMS_2, treasure.GET_ITEMS_3)]
    helper.appear = Mock(side_effect=lambda button, **kwargs: button is treasure.GET_ITEMS_2)
    assert helper._reward_dialog() is GET_ITEMS_1


def test_completed_journal_is_inactive_on_unrelated_receipt_and_does_not_intercept_recovery(monkeypatch, tmp_path):
    state = record()
    for row in state['cards'].values():
        row.update(claim_clicks=1, pending=False, receipt_seen=True, verified=True)
    save_reward_observations('alas', {KEY: state}, folder=str(tmp_path))
    helper = controller()
    helper._treasure_page = Mock(return_value=False)
    helper._reward_dialog = Mock(return_value=GET_ITEMS_1)
    helper._return_main = Mock()
    helper._enter = Mock()
    result = helper.run(EVENT, folder=str(tmp_path))
    assert result['status'] == 'complete' and result['inactive'] and result['returned_main']
    helper._reward_dialog.assert_not_called()
    helper._return_main.assert_not_called()
    helper._enter.assert_not_called()
    helper.device.click.assert_not_called()


def test_completed_inactive_reuse_still_recovers_exact_panel_on_next_invocation(monkeypatch, tmp_path):
    state = record()
    for row in state['cards'].values():
        row.update(claim_clicks=1, pending=False, receipt_seen=True, verified=True)
    save_reward_observations('alas', {KEY: state}, folder=str(tmp_path))
    helper = controller()
    helper._treasure_page = Mock(return_value=False)
    helper._return_main = Mock(return_value=True)
    monkeypatch.setattr(treasure, 'save_event_diagnostic', Mock(return_value='complete.png'))
    assert helper.run(EVENT, folder=str(tmp_path))['inactive']
    helper._return_main.assert_not_called()

    helper._treasure_page.return_value = True
    result = helper.run(EVENT, folder=str(tmp_path))
    assert result['status'] == 'complete' and result['returned_main'] and not result['inactive']
    helper._return_main.assert_called_once()
    helper.device.click.assert_not_called()


def test_first_invocation_on_unrelated_receipt_reserves_nothing_and_leaves_it_for_existing_recovery(tmp_path):
    helper = controller()
    helper._treasure_page = Mock(return_value=False)
    helper._reward_dialog = Mock(return_value=GET_ITEMS_1)
    helper.run = Mock()
    assert helper.resume(EVENT, folder=str(tmp_path)) is None
    helper.run.assert_not_called()
    helper._reward_dialog.assert_not_called()
    assert not list(tmp_path.iterdir())
    helper.device.click.assert_not_called()


@pytest.mark.parametrize('pending,on_page', [(True, False), (False, True)])
def test_startup_recovery_only_owns_pending_claim_or_exact_treasure_page(tmp_path, pending, on_page):
    helper = controller()
    helper._treasure_page = Mock(return_value=on_page)
    state = record()
    if pending:
        state['cards']['day2'].update(claim_clicks=1, pending=True)
        save_reward_observations('alas', {KEY: state}, folder=str(tmp_path))
    helper.run = Mock(return_value={'returned_main': True})
    assert helper.resume(EVENT, folder=str(tmp_path)) == {'returned_main': True}
    helper.run.assert_called_once_with(EVENT, folder=str(tmp_path))


def test_routine_collection_follows_existing_receipt_recovery_on_first_invocation():
    from module.campaign.event_completion import EventCompletion
    helper = EventCompletion.__new__(EventCompletion)
    helper.config = SimpleNamespace(config_name='alas')
    order = []
    helper._resume_completed_event_panel = Mock(return_value=True)
    helper.resume_treasure_free_packs = Mock(side_effect=lambda event: order.append('treasure_resume') or None)
    helper._resume_event_mission_receipt = Mock(side_effect=lambda: order.append('mission_receipt') or True)
    helper.collect_treasure_free_packs = Mock(side_effect=lambda event: order.append('treasure_run') or
                                             dict(status='blocked', returned_main=False))
    helper.observe_event_main = Mock()
    with pytest.raises(GamePageUnknownError, match='Free treasure'):
        helper.run(EVENT)
    assert order == ['treasure_resume', 'mission_receipt', 'treasure_run']
    helper.observe_event_main.assert_not_called()
