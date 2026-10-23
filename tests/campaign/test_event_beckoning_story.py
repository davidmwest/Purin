from copy import deepcopy
import re
from types import SimpleNamespace
from unittest.mock import Mock, call

import numpy as np
import pytest
from PIL import Image

from module.campaign import event_beckoning_story as story
from module.campaign.event_beckoning import BeckoningTickets, EVENT
from module.campaign.event_beckoning_assets import (
    BECKONING_MONTHLY_COLLECT, BECKONING_VICTORY_COLLECT)
from module.campaign.event_beckoning_story import BeckoningStoryContinuation
from module.campaign.event_beckoning_story_assets import (
    MONTHLY_MISSION, OCR_MONTHLY_MILESTONE, STORY_CARDS, STORY_UNLOCKED_TITLES,
    STORY_VIEWER_LOG, STORY_VIEWER_SKIP, VICTORY_MISSION)
from module.combat.assets import GET_ITEMS_1
from module.exception import ScriptError
from module.ocr.ocr import Ocr


@pytest.fixture
def immediate(monkeypatch):
    timer = Mock()
    timer.start.return_value = timer
    timer.reached.return_value = True
    monkeypatch.setattr(story, 'Timer', Mock(return_value=timer))
    monkeypatch.setattr(story.EventRewards, '_reward_dialog', Mock(return_value=None))


def controller(frames=30, state=None):
    owner = SimpleNamespace(
        config=SimpleNamespace(temporary=Mock(return_value=SimpleNamespace(recover=Mock()))),
        device=SimpleNamespace(image=None, click=Mock(), stuck_record_clear=Mock()),
        _on_choice=Mock(return_value=True), _tickets=Mock(return_value=2),
        _matches=Mock(return_value=False), _capture=Mock(), story_skip=Mock())
    owner.loop = Mock(side_effect=lambda **kwargs: iter(range(frames)))
    record = dict(rows={'monthly': {}, 'victories': {}}, diagnostics=[])
    saved = []
    state = {} if state is None else state

    def persist():
        saved.append((deepcopy(state), owner.device.click.call_count, owner.story_skip.call_count))

    helper = BeckoningStoryContinuation(owner, record, state, EVENT, persist)
    helper.visible = Mock(return_value=False)
    return helper, owner, record, state, saved


def test_actual_six_locked_cards_and_escalating_missions_are_recognized():
    image = np.asarray(Image.open('tests/campaign/data/beckoning_escalating_missions.png').convert('RGB'))
    helper, owner, *_ = controller()
    owner.device.image = image
    for button in [button for _, title, lock in STORY_CARDS for button in (title, lock)] + [MONTHLY_MISSION, VICTORY_MISSION]:
        assert BeckoningStoryContinuation.visible(helper, button)
        owner.device.image = np.zeros_like(image)
        assert not BeckoningStoryContinuation.visible(helper, button)
        owner.device.image = image
    assert OCR_MONTHLY_MILESTONE.ocr(image) == (2, 0, 2)
    # The counter reader clamps the actual 155 victories to the earned 30 goal.
    assert story.OCR_BECKONING_VICTORIES.ocr(image) == (30, 0, 30)


def test_actual_counter_preserves_visible_leading_digit_at_sixty_goal():
    image = np.asarray(Image.open(
        'tests/campaign/data/beckoning_card3_unlocked.png').convert('RGB')).copy()
    counter = story.OCR_BECKONING_VICTORIES
    # Check raw model output too: DigitCounter clamps earned progress to its
    # goal, so the parsed tuple alone could hide a missing leading digit.
    raw = Ocr.ocr(counter, image)
    assert re.search(r'(\d+)/(\d+)', raw).groups() == ('155', '60')
    assert counter.ocr(image) == (60, 0, 60)
    # Removing the actual first digit must not reconstruct an assumed 155.
    image[594:614, 918:930] = (193, 185, 168)
    assert counter.ocr(image) == (55, 5, 60)


@pytest.mark.parametrize('missing_leading_digit', [False, True])
def test_actual_sixty_milestone_claim_requires_visible_earned_counter(
        immediate, missing_leading_digit):
    helper, owner, record, state, saved = controller(frames=12)
    owner.device.image = np.asarray(Image.open(
        'tests/campaign/data/beckoning_card3_unlocked.png').convert('RGB')).copy()
    if missing_leading_digit:
        owner.device.image[594:614, 918:930] = (193, 185, 168)
    claimed = [False]
    helper.visible = lambda button: BeckoningStoryContinuation.visible(helper, button)
    owner._matches = lambda button: (
        not (button is BECKONING_VICTORY_COLLECT and claimed[0])
        and button.match_template_color(owner.device.image, offset=(3, 3), similarity=.85))
    owner._on_choice = lambda: BeckoningTickets._on_choice(owner)
    owner._tickets = lambda: 3 if claimed[0] else 2

    def click(button):
        assert button is BECKONING_VICTORY_COLLECT
        audit = saved[-1][0][EVENT + ':beckoning_stories_v1']['milestones']['victories:60']
        assert audit['pending'] and audit['clicks'] == 1 and saved[-1][1] == 0
        claimed[0] = True

    owner.device.click.side_effect = click
    assert helper.collect_milestones() is True
    if missing_leading_digit:
        owner.device.click.assert_not_called()
        assert helper.ledger['milestones'] == {}
    else:
        owner.device.click.assert_called_once_with(BECKONING_VICTORY_COLLECT)
        audit = helper.ledger['milestones']['victories:60']
        assert audit['verified'] and not audit['pending'] and audit['tickets_after'] == 3


def test_remaining_locked_cards_match_actual_post_third_story_screen():
    image = np.asarray(Image.open(
        'tests/campaign/data/beckoning_card3_unlocked.png').convert('RGB'))
    helper, owner, *_ = controller()
    owner.device.image = image
    for name, title, lock in STORY_CARDS[3:]:
        assert BeckoningStoryContinuation.visible(helper, title), name
        assert BeckoningStoryContinuation.visible(helper, lock), name
        assert name not in STORY_UNLOCKED_TITLES


def actual_card3_return(frames=5):
    state = {EVENT + ':beckoning_stories_v1': dict(cards={
        '1': dict(clicks=2, complete=True, pending=False),
        '2': dict(clicks=1, complete=True, pending=False),
        '3': dict(clicks=1, complete=False, pending=True, story_seen=True,
                  tickets_before=3, handler_calls=3)}, milestones={})}
    helper, owner, record, state, saved = controller(frames=frames, state=state)
    owner.device.image = np.asarray(Image.open(
        'tests/campaign/data/beckoning_card3_unlocked.png').convert('RGB')).copy()
    owner._matches = lambda button: button.match_template_color(
        owner.device.image, offset=(3, 3), similarity=.85)
    owner._on_choice = lambda: BeckoningTickets._on_choice(owner)
    owner._tickets = lambda: BeckoningTickets._tickets(owner)
    helper.visible = lambda button: BeckoningStoryContinuation.visible(helper, button)
    return helper, owner, record, state, saved


def test_actual_card3_unlocked_title_needs_its_observed_alternative():
    helper, owner, *_ = actual_card3_return()
    assert owner._on_choice() and owner._tickets() == 2
    assert not helper.visible(STORY_CARDS[2][1])
    assert helper.visible(STORY_UNLOCKED_TITLES['3'])
    assert helper.card_title_visible('3')
    assert not helper.visible(STORY_CARDS[2][2])
    owner.device.image[:] = 0
    assert not helper.card_title_visible('3')
    owner.device.click.assert_not_called()


def test_actual_paid_story_return_finishes_without_another_input(immediate):
    helper, owner, _, _, saved = actual_card3_return()
    assert helper.finish_story('3') is True
    assert helper.cards['3']['complete'] and not helper.cards['3']['pending']
    assert helper.cards['3']['clicks'] == 1
    assert helper.cards['3']['tickets_after'] == 2
    assert saved[-1][0][EVENT + ':beckoning_stories_v1']['cards']['3']['complete']
    owner.device.click.assert_not_called()
    owner.story_skip.assert_not_called()


@pytest.mark.parametrize('missing', [None, 'title', 'lock_removed', 'payment', 'viewer', 'choice'])
def test_interrupted_actual_return_verifies_only_paid_seen_unlocked_card(immediate, missing):
    helper, owner, *_ = actual_card3_return()
    if missing == 'title':
        x1, y1, x2, y2 = STORY_CARDS[2][1].area
        owner.device.image[y1-4:y2+4, x1-4:x2+4] = 0
    elif missing == 'lock_removed':
        locked = np.asarray(Image.open(
            'tests/campaign/data/beckoning_escalating_missions.png').convert('RGB'))
        x1, y1, x2, y2 = STORY_CARDS[2][2].area
        owner.device.image[y1-4:y2+4, x1-4:x2+4] = locked[y1-4:y2+4, x1-4:x2+4]
    elif missing == 'payment':
        owner._tickets = lambda: 3
    elif missing == 'viewer':
        helper.cards['3']['story_seen'] = False
    elif missing == 'choice':
        owner._on_choice = lambda: False
    helper.observe_unlock_payment()
    assert helper.cards['3'].get('complete', False) is (missing is None)
    assert helper.cards['3']['clicks'] == 1
    assert helper.cards['1']['complete'] and helper.cards['2']['complete']
    owner.device.click.assert_not_called()
    owner.story_skip.assert_not_called()


def test_interrupted_return_cannot_verify_from_unstable_ticket_readings(immediate):
    helper, owner, *_ = actual_card3_return(frames=5)
    owner._tickets = Mock(side_effect=[2, 1, 2, 1, 2])
    assert helper.observe_unlock_payment() is False
    assert not helper.cards['3']['complete']
    owner.device.click.assert_not_called()
    owner.story_skip.assert_not_called()


@pytest.mark.parametrize('missing', [None, STORY_VIEWER_LOG, STORY_VIEWER_SKIP])
def test_actual_story_viewer_requires_both_observed_controls(missing):
    helper, owner, *_ = controller()
    image = np.asarray(Image.open('tests/campaign/data/beckoning_story_viewer.png').convert('RGB')).copy()
    if missing:
        x1, y1, x2, y2 = missing.area
        image[y1-4:y2+4, x1-4:x2+4] = 0
    owner.device.image = image
    helper.visible = lambda button: BeckoningStoryContinuation.visible(helper, button)
    assert helper.on_story() is (missing is None)
    owner.device.click.assert_not_called()


def test_old_one_shot_is_preserved_as_pending_not_as_completed():
    state = {EVENT + ':beckoning_first_unlock': dict(card_clicks=1,
             reason='unlock_destination_captured', tickets_before=2)}
    helper, owner, record, *_ = controller(state=state)
    assert helper.cards['1'] == dict(clicks=1, pending=True, complete=False,
                                     story_seen=False, tickets_before=2, handler_calls=0)
    assert record['stories'] is helper.ledger
    owner.device.click.assert_not_called()


@pytest.mark.parametrize('cards', [{'7': dict(clicks=1)}, {'1': dict(clicks=3)}, {'1': dict(clicks=True)}])
def test_invalid_durable_card_budget_cannot_be_reset(cards):
    with pytest.raises(ScriptError):
        controller(state={EVENT + ':beckoning_stories_v1': dict(cards=cards, milestones={})})


def test_earned_escalating_milestone_is_audited_before_click_and_ticket_verified(immediate, monkeypatch):
    helper, owner, record, state, saved = controller()
    tickets = [2]
    owner._tickets.side_effect = lambda: tickets[0]
    helper.visible.side_effect = lambda button: button is MONTHLY_MISSION
    owner._matches.side_effect = lambda button: button is BECKONING_MONTHLY_COLLECT and tickets[0] == 2
    monkeypatch.setattr(story.OCR_MONTHLY_MILESTONE, 'ocr', Mock(return_value=(2, 0, 2)))
    owner.device.click.side_effect = lambda button: tickets.__setitem__(0, 3)
    assert helper.collect_milestones() is True
    owner.device.click.assert_called_once_with(BECKONING_MONTHLY_COLLECT)
    assert saved[0][1] == 0
    audit = saved[0][0][EVENT + ':beckoning_stories_v1']['milestones']['monthly:2']
    assert audit['pending'] and audit['clicks'] == 1
    assert helper.ledger['milestones']['monthly:2']['verified'] is True
    assert record['tickets'] == 3


def test_reserved_milestone_never_clicks_again_without_ticket_growth(immediate):
    state = {EVENT + ':beckoning_stories_v1': dict(cards={}, milestones={
        'monthly:2': dict(clicks=1, pending=True, verified=False, tickets_before=2)})}
    helper, owner, record, *_ = controller(state=state)
    assert helper.collect_milestones() is False
    assert record['reason'] == 'milestone_claim_unverified'
    owner.device.click.assert_not_called()


def test_unknown_ui_cannot_dismiss_a_receipt_without_a_pending_claim(immediate, monkeypatch):
    helper, owner, *_ = controller()
    owner._on_choice.return_value = False
    receipt = Mock(return_value=GET_ITEMS_1)
    monkeypatch.setattr(story.EventRewards, '_reward_dialog', receipt)
    assert helper.collect_milestones() is False
    receipt.assert_not_called()
    owner.device.click.assert_not_called()


def test_all_six_exact_cards_are_reserved_then_verified_once(immediate):
    helper, owner, record, state, saved = controller()
    tickets = [6]
    owner._tickets.side_effect = lambda: tickets[0]
    helper.visible.return_value = True

    def complete(name):
        assert helper.cards[name]['pending'] and helper.cards[name]['clicks'] == 1
        helper.cards[name].update(pending=False, complete=True)
        tickets[0] -= 1
        return True

    helper.finish_story = Mock(side_effect=complete)
    assert helper.run_cards() is True
    assert record['status'] == 'complete' and record['story_complete'] is True
    assert owner.device.click.call_args_list == [call(lock) for _, _, lock in STORY_CARDS]
    assert helper.finish_story.call_args_list == [call(str(n)) for n in range(1, 7)]
    for index in range(6):
        assert any(snapshot[1] == index and snapshot[0][EVENT + ':beckoning_stories_v1']['cards']
                   .get(str(index + 1), {}).get('pending') for snapshot in saved)


@pytest.mark.parametrize('tickets', [None, 0])
def test_unknown_or_zero_tickets_never_authorize_unlock(immediate, tickets):
    helper, owner, record, *_ = controller()
    owner._tickets.return_value = tickets
    helper.visible.return_value = True
    result = helper.run_cards()
    assert result is (tickets == 0)
    assert record['story_complete'] is False
    owner.device.click.assert_not_called()


def test_unknown_choice_does_not_become_a_successful_story_pass(immediate):
    helper, owner, record, *_ = controller()
    owner._on_choice.return_value = False
    assert helper.run_cards() is False
    assert record['reason'] == 'story_cards_not_stably_recognized'
    owner.device.click.assert_not_called()


def test_interrupted_unlocked_card_may_replay_once_after_proven_ticket_spend(immediate):
    state = {EVENT + ':beckoning_stories_v1': dict(cards={'1': dict(
        clicks=1, pending=True, tickets_before=2)}, milestones={})}
    helper, owner, record, *_ = controller(state=state)
    owner._tickets.return_value = 1
    helper.visible.side_effect = lambda button: button is STORY_CARDS[0][1]
    helper.finish_story = Mock(return_value=False)
    assert helper.run_cards() is False
    owner.device.click.assert_called_once_with(STORY_CARDS[0][1])
    assert helper.cards['1']['clicks'] == 2 and helper.cards['1']['replay'] is True
    assert helper.run_cards() is True
    assert owner.device.click.call_count == 1
    assert record['story_complete'] is False


def test_prior_unlock_payment_survives_new_ticket_claims_before_single_replay(immediate):
    old = {EVENT + ':beckoning_first_unlock': dict(card_clicks=1,
           reason='unlock_destination_captured', tickets_before=2)}
    helper, owner, record, state, saved = controller(state=old)
    owner._tickets.return_value = 1
    helper.visible.side_effect = lambda button: button is STORY_CARDS[0][1]
    assert helper.observe_unlock_payment() is True
    assert saved[-1][0][EVENT + ':beckoning_stories_v1']['cards']['1']['unlock_paid'] is True
    owner._tickets.return_value = 3  # Later free milestone rewards hide the original 2 -> 1 change.
    helper.finish_story = Mock(return_value=False)
    assert helper.run_cards() is False
    owner.device.click.assert_called_once_with(STORY_CARDS[0][1])
    assert helper.cards['1']['clicks'] == 2


def test_pending_unlock_cannot_record_payment_for_still_locked_card(immediate):
    helper, owner, record, *_ = controller(state={EVENT + ':beckoning_stories_v1': dict(
        cards={'1': dict(clicks=1, pending=True, tickets_before=2)}, milestones={})})
    owner._tickets.return_value = 1
    helper.visible.side_effect = lambda button: button in STORY_CARDS[0][1:]
    assert helper.observe_unlock_payment() is True
    assert not helper.cards['1'].get('unlock_paid')
    helper.finish_story = Mock()
    assert helper.run_cards() is True
    helper.finish_story.assert_not_called()
    owner.device.click.assert_not_called()


@pytest.mark.parametrize('tickets,locked,expected', [(1, False, True), (2, False, False), (1, True, False)])
def test_story_completion_requires_viewer_then_stable_paid_unlocked_return(immediate, tickets, locked, expected):
    helper, owner, record, state, saved = controller(frames=5)
    helper.cards['1'] = dict(clicks=1, pending=True, tickets_before=2, handler_calls=0)
    frame = [0]

    def frames(**kwargs):
        for number in range(5):
            frame[0] = number
            yield number

    owner.loop.side_effect = frames
    owner._on_choice.side_effect = lambda: frame[0] > 0
    owner._tickets.return_value = tickets
    helper.on_story = Mock(return_value=True)
    helper.visible.side_effect = lambda button: button is STORY_CARDS[0][1] or (button is STORY_CARDS[0][2] and locked)
    assert helper.finish_story('1') is expected
    assert helper.cards['1'].get('complete', False) is expected
    assert saved[0][2] == 0 and saved[0][0][EVENT + ':beckoning_stories_v1']['cards']['1']['handler_calls'] == 1
    owner.story_skip.assert_called_once_with()
    owner.config.temporary.assert_called_once_with(STORY_ALLOW_SKIP=True)
    owner.config.temporary.return_value.recover.assert_called_once_with()


def test_receipt_and_story_handler_never_receive_same_fresh_frame(immediate, monkeypatch):
    helper, owner, *_ = controller(frames=1)
    helper.cards['1'] = dict(clicks=1, pending=True, story_seen=True, tickets_before=2)
    owner._on_choice.return_value = False
    helper.on_story = Mock(return_value=False)
    monkeypatch.setattr(story.EventRewards, '_reward_dialog', Mock(return_value=GET_ITEMS_1))
    assert helper.finish_story('1') is False
    owner.device.click.assert_called_once_with(GET_ITEMS_1)
    owner.story_skip.assert_not_called()


def test_viewer_without_unique_pending_card_never_calls_story_handler(immediate):
    helper, owner, record, *_ = controller()
    helper.on_story = Mock(return_value=True)
    assert helper.resume_viewer() is False
    assert record['reason'] == 'story_viewer_has_no_unique_pending_card'
    owner.story_skip.assert_not_called()


def test_story_handler_durable_limit_fails_without_extra_input(immediate):
    helper, owner, record, *_ = controller()
    helper.cards['1'] = dict(clicks=1, pending=True, story_seen=True, handler_calls=30)
    owner._on_choice.return_value = False
    helper.on_story = Mock(return_value=True)
    assert helper.finish_story('1') is False
    assert record['reason'] == 'story_handler_budget_exhausted'
    owner.story_skip.assert_not_called()
