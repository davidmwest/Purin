from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from module.campaign import event_completion as completion
from module.campaign.event_completion import EventCompletion, load_reward_observations, save_reward_observations
from module.campaign.event_completion_assets import TEMPESTA_CAMPAIGN, TEMPESTA_EX_LOCKED, TEMPESTA_SUPPLIES
from module.exception import GameNotRunningError, ScriptError
from module.reward.assets import MISSION_MULTI, MISSION_SINGLE, MISSION_UNFINISH
from module.ui.page import page_event_list, page_main, page_mission, page_sp


def controller():
    helper = EventCompletion.__new__(EventCompletion)
    helper.config = SimpleNamespace(config_name='f2p')
    helper.device = SimpleNamespace(stuck_record_clear=Mock(), click_record_clear=Mock(), click=Mock(),
                                    app_is_running=Mock(return_value=True), display_scale=1)
    helper.interval_clear = Mock()
    return helper


def test_disabled_options_do_not_even_construct_story_or_navigate(monkeypatch):
    helper = controller()
    story = Mock()
    helper.collect_event_missions = Mock()
    monkeypatch.setattr(completion, 'EventStory', story)

    assert helper.run('event_20261022_cn', collect_rewards=False, complete_story=False) == {
        'story': 'disabled', 'rewards': 'disabled', 'event_hub_rewards': 'disabled'}
    story.assert_not_called()
    helper.collect_event_missions.assert_not_called()


def test_story_uses_the_requested_event_and_explicit_bounded_battle_skip(monkeypatch):
    helper = controller()
    story = Mock()
    story.return_value.run.return_value = True
    monkeypatch.setattr(completion, 'EventStory', story)

    result = helper.run('event_20261022_cn', collect_rewards=False, complete_story=True)

    assert result['story'] == 'complete'
    story.return_value.run.assert_called_once_with(event='event_20261022_cn', skip_battle=True)


def test_unsupported_story_never_calls_its_tapping_detector(monkeypatch):
    helper = controller()
    story = Mock()
    monkeypatch.setattr(completion, 'EventStory', story)
    assert helper.run('campaign_main', collect_rewards=False, complete_story=True)['story'] == 'unsupported'
    story.assert_not_called()


def test_mission_claims_do_not_report_the_unsupported_pt_ladder_as_collected():
    helper = controller()
    helper.collect_event_missions = Mock(return_value=True)
    helper.observe_event_main = Mock(return_value={'status': 'observed'})
    helper.observe_event_rewards = Mock(return_value={'status': 'observed'})
    helper.observe_event_supplies = Mock(return_value={'status': 'observed'})
    result = helper.run('event_20261022_cn')
    assert result == {'story': 'disabled', 'rewards': 'complete', 'event_hub_rewards': 'unsupported',
                      'event_main_observation': {'status': 'observed'},
                      'event_reward_observation': {'status': 'observed'},
                      'event_supplies_observation': {'status': 'observed'}}
    helper.observe_event_rewards.assert_called_once_with('event_20261022_cn')
    helper.observe_event_supplies.assert_called_once_with('event_20261022_cn')


def test_main_observation_is_durable_before_navigation_and_captures_fresh_recognized_frame(monkeypatch, tmp_path):
    helper = controller()
    helper.loop = Mock(return_value=iter(range(2)))
    helper.ui_page_appear = Mock(side_effect=[False, True])

    def navigate(main, destination):
        assert load_reward_observations('f2p', folder=tmp_path)['event_20261022_cn:main']['status'] == 'observing'
        return True

    monkeypatch.setattr(completion, 'ensure_event_page', Mock(side_effect=navigate))
    monkeypatch.setattr(completion, 'save_event_diagnostic', Mock(return_value='/tmp/main.png'))
    first = helper.observe_event_main('event_20261022_cn', folder=tmp_path)
    second = helper.observe_event_main('event_20261022_cn', folder=tmp_path)
    assert first['status'] == 'observed'
    assert first['diagnostics'] == ['/tmp/main.png']
    assert second['reused'] is True
    completion.ensure_event_page.assert_called_once_with(helper, page_main)
    helper.loop.assert_called_once_with(skip_first=False, timeout=5)
    completion.save_event_diagnostic.assert_called_once_with(helper.device, 'f2p_event_main_observe')
    helper.device.click.assert_not_called()


def test_unknown_main_frame_is_never_captured_as_an_event_entry(monkeypatch, tmp_path):
    helper = controller()
    helper.loop = Mock(return_value=iter(range(3)))
    helper.ui_page_appear = Mock(return_value=False)
    monkeypatch.setattr(completion, 'ensure_event_page', Mock(return_value=True))
    monkeypatch.setattr(completion, 'save_event_diagnostic', Mock())
    result = helper.observe_event_main('event_20261022_cn', folder=tmp_path)
    assert result['status'] == 'blocked'
    assert result['reason'] == 'main_page_unrecognized'
    completion.save_event_diagnostic.assert_not_called()
    helper.device.click.assert_not_called()


def test_interrupted_main_observation_does_not_navigate_again(monkeypatch, tmp_path):
    save_reward_observations('f2p', {'event_20261022_cn:main': {'status': 'observing'}}, folder=tmp_path)
    monkeypatch.setattr(completion, 'ensure_event_page', Mock())
    result = controller().observe_event_main('event_20261022_cn', folder=tmp_path)
    assert result['reason'] == 'previous_main_observation_interrupted'
    completion.ensure_event_page.assert_not_called()


def test_closed_game_does_not_spend_main_observation_budget(tmp_path):
    helper = controller()
    helper.device.app_is_running.return_value = False
    with pytest.raises(GameNotRunningError):
        helper.observe_event_main('event_20261022_cn', folder=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_main_observation_precedes_story_and_other_reward_navigation(monkeypatch):
    helper = controller()
    order = []
    helper.observe_event_main = Mock(side_effect=lambda event: order.append('main'))
    helper.observe_event_rewards = Mock(side_effect=lambda event: order.append('list'))
    helper.observe_event_supplies = Mock(side_effect=lambda event: order.append('supplies'))
    helper.collect_event_missions = Mock(side_effect=lambda: order.append('missions'))
    story = Mock()
    story.return_value.run.side_effect = lambda **kwargs: order.append('story')
    monkeypatch.setattr(completion, 'EventStory', story)
    helper.run('event_20261022_cn', complete_story=True)
    assert order == ['main', 'story', 'list', 'supplies', 'missions']


def test_supplies_budget_is_independent_and_saved_before_any_inspection(monkeypatch, tmp_path):
    helper = controller()
    save_reward_observations('f2p', {'event_20261022_cn': {'status': 'observed'}}, folder=tmp_path)

    def inspect(record):
        state = load_reward_observations('f2p', folder=tmp_path)
        assert state['event_20261022_cn']['status'] == 'observed'
        assert state['event_20261022_cn:supplies']['status'] == 'observing'
        record.update(status='observed', reason='supplies_panel_assets_required', supplies_clicked=True)

    helper._inspect_event_supplies = Mock(side_effect=inspect)
    monkeypatch.setattr(completion, 'ensure_event_page', Mock(return_value=False))
    monkeypatch.setattr(completion, 'save_event_diagnostic', Mock(return_value='/tmp/supplies.png'))
    first = helper.observe_event_supplies('event_20261022_cn', folder=tmp_path)
    second = helper.observe_event_supplies('event_20261022_cn', folder=tmp_path)

    assert first['status'] == 'observed'
    assert first['diagnostics'] == ['/tmp/supplies.png']
    assert first['returned_main'] is False
    assert first['return_reason'] == 'main_return_unrecognized'
    assert second['reused'] is True
    helper._inspect_event_supplies.assert_called_once()
    completion.ensure_event_page.assert_called_once_with(helper, page_main, timeout=8, max_clicks=3)


def test_interrupted_supplies_observation_does_not_retry_click(monkeypatch, tmp_path):
    save_reward_observations('f2p', {'event_20261022_cn:supplies': {'status': 'observing'}}, folder=tmp_path)
    helper = controller()
    helper._inspect_event_supplies = Mock()
    result = helper.observe_event_supplies('event_20261022_cn', folder=tmp_path)
    assert result['reason'] == 'previous_supplies_observation_interrupted'
    assert result['status'] == 'blocked'
    helper._inspect_event_supplies.assert_not_called()


def test_supplies_observation_exception_spends_budget_and_attempts_known_return(monkeypatch, tmp_path):
    helper = controller()
    helper._inspect_event_supplies = Mock(side_effect=RuntimeError('interrupted'))
    monkeypatch.setattr(completion, 'ensure_event_page', Mock(return_value=True))
    with pytest.raises(RuntimeError, match='interrupted'):
        helper.observe_event_supplies('event_20261022_cn', folder=tmp_path)
    state = load_reward_observations('f2p', folder=tmp_path)['event_20261022_cn:supplies']
    assert state['status'] == 'blocked'
    assert state['returned_main'] is True


def test_closed_game_does_not_spend_supplies_observation_budget(tmp_path):
    helper = controller()
    helper.device.app_is_running.return_value = False
    with pytest.raises(GameNotRunningError):
        helper.observe_event_supplies('event_20261022_cn', folder=tmp_path)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('event', ['campaign_main', 'event_20251023_cn'])
def test_unsupported_supplies_event_does_not_inspect_or_create_budget(tmp_path, event):
    helper = controller()
    helper._inspect_event_supplies = Mock()
    assert helper.observe_event_supplies(event, folder=tmp_path)['status'] == 'unsupported'
    helper._inspect_event_supplies.assert_not_called()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('recognized', [False, True])
def test_supplies_click_requires_page_and_both_observed_templates(monkeypatch, recognized):
    helper = controller()
    helper.loop = Mock(side_effect=lambda **kwargs: iter(range(3)))
    helper.ui_page_appear = Mock(side_effect=[True, False] if recognized else [True] * 3)
    helper.match_template_color = Mock(side_effect=lambda button, **kwargs:
                                      button == TEMPESTA_CAMPAIGN or recognized)
    monkeypatch.setattr(completion, 'ensure_event_page', Mock(return_value=True))
    timer = Mock()
    timer.start.return_value = timer
    timer.reached.return_value = True
    monkeypatch.setattr(completion, 'Timer', Mock(return_value=timer))
    record = {}
    helper._inspect_event_supplies(record)
    if recognized:
        helper.device.click.assert_called_once_with(TEMPESTA_SUPPLIES)
        assert record['status'] == 'observed'
        assert record['supplies_clicked'] is True
    else:
        helper.device.click.assert_not_called()
        assert record['reason'] == 'supplies_label_unrecognized'


def test_supplies_are_never_clicked_from_an_unknown_page(monkeypatch):
    helper = controller()
    helper.loop = Mock(return_value=iter(range(3)))
    helper.ui_page_appear = Mock(return_value=False)
    helper.match_template_color = Mock(return_value=True)
    monkeypatch.setattr(completion, 'ensure_event_page', Mock(return_value=True))
    record = {}
    helper._inspect_event_supplies(record)
    helper.device.click.assert_not_called()
    helper.match_template_color.assert_not_called()
    assert record['status'] == 'blocked'


def test_unchanged_supplies_screen_has_only_three_verified_spaced_retries(monkeypatch):
    helper = controller()
    helper.loop = Mock(return_value=iter(range(20)))
    helper.ui_page_appear = Mock(return_value=True)
    helper.match_template_color = Mock(return_value=True)
    monkeypatch.setattr(completion, 'ensure_event_page', Mock(return_value=True))
    timer = Mock()
    timer.start.return_value = timer
    timer.reached.return_value = True
    factory = Mock(return_value=timer)
    monkeypatch.setattr(completion, 'Timer', factory)
    record = {}

    helper._inspect_event_supplies(record)

    assert record['status'] == 'blocked'
    assert record['reason'] == 'supplies_did_not_open'
    assert record['supplies_clicks'] == helper.device.click.call_count == 3
    assert [call.args[0] for call in helper.device.click.call_args_list] == [TEMPESTA_SUPPLIES] * 3
    assert [call.args for call in factory.call_args_list] == [(1,), (3,), (3,), (3,)]
    assert factory.call_args_list[1].kwargs == {'count': 4}
    helper.loop.assert_called_once_with(skip_first=False, timeout=20)
    x1, y1, x2, y2 = TEMPESTA_SUPPLIES._button
    assert 1110 <= x1 < x2 <= 1157 and 648 <= y1 < y2 <= 698


def test_old_failed_label_tap_gets_only_one_new_persisted_emblem_budget(monkeypatch, tmp_path):
    save_reward_observations('f2p', {'event_20261022_cn:supplies': {
        'status': 'blocked', 'reason': 'supplies_did_not_open', 'supplies_clicked': True}}, folder=tmp_path)
    helper = controller()

    def inspect(record):
        state = load_reward_observations('f2p', folder=tmp_path)
        assert state['event_20261022_cn:supplies:emblem_retry']['status'] == 'observing'
        record.update(status='blocked', reason='supplies_did_not_open', supplies_clicks=3)

    helper._inspect_event_supplies = Mock(side_effect=inspect)
    monkeypatch.setattr(completion, 'ensure_event_page', Mock(return_value=True))
    monkeypatch.setattr(completion, 'save_event_diagnostic', Mock(return_value='/tmp/supplies.png'))

    first = helper.observe_event_supplies('event_20261022_cn', folder=tmp_path)
    second = helper.observe_event_supplies('event_20261022_cn', folder=tmp_path)

    assert first['entry'] == 'emblem'
    assert second['reused'] is True
    helper._inspect_event_supplies.assert_called_once()
    assert len(load_reward_observations('f2p', folder=tmp_path)) == 2


def test_failed_new_emblem_budget_is_not_given_a_second_attempt(monkeypatch, tmp_path):
    save_reward_observations('f2p', {'event_20261022_cn:supplies': {
        'status': 'blocked', 'reason': 'supplies_did_not_open', 'entry': 'emblem'}}, folder=tmp_path)
    helper = controller()
    helper._inspect_event_supplies = Mock()
    result = helper.observe_event_supplies('event_20261022_cn', folder=tmp_path)
    assert result['reused'] is True
    helper._inspect_event_supplies.assert_not_called()
    assert len(load_reward_observations('f2p', folder=tmp_path)) == 1


def test_observed_tempesta_labels_and_lock_match_but_missing_controls_do_not():
    from pathlib import Path
    import numpy as np
    from PIL import Image

    image = np.asarray(Image.open(Path(__file__).parent / 'data/tempesta_locked.png').convert('RGB'))
    for button in (TEMPESTA_CAMPAIGN, TEMPESTA_SUPPLIES, TEMPESTA_EX_LOCKED):
        assert button.match_template_color(image, offset=(3, 3), similarity=.85)
        removed = image.copy()
        x1, y1, x2, y2 = button.area
        removed[y1 - 3:y2 + 3, x1 - 3:x2 + 3] = 0
        assert not button.match_template_color(removed, offset=(3, 3), similarity=.85)


def test_event_list_observation_is_persisted_before_navigation_and_reused(monkeypatch, tmp_path):
    helper = controller()

    def navigate(main, destination):
        assert load_reward_observations('f2p', folder=tmp_path)['event_20261022_cn']['status'] in (
            'observing', 'observed')
        return True

    navigate = Mock(side_effect=navigate)
    monkeypatch.setattr(completion, 'ensure_event_page', navigate)
    monkeypatch.setattr(completion, 'save_event_diagnostic', Mock(return_value='/tmp/event_list.png'))
    first = helper.observe_event_rewards('event_20261022_cn', folder=tmp_path)
    second = helper.observe_event_rewards('event_20261022_cn', folder=tmp_path)

    assert first['status'] == second['status'] == 'observed'
    assert first['returned_main'] is True
    assert first['diagnostics'] == ['/tmp/event_list.png']
    assert second['reused'] is True
    assert [call.args[1] for call in navigate.call_args_list] == [page_event_list, page_main]
    helper.device.click.assert_not_called()


def test_unrecognized_activity_list_still_attempts_only_bounded_main_return(monkeypatch, tmp_path):
    helper = controller()
    navigate = Mock(side_effect=[False, True])
    monkeypatch.setattr(completion, 'ensure_event_page', navigate)
    monkeypatch.setattr(completion, 'save_event_diagnostic', Mock(return_value='/tmp/blocked.png'))
    result = helper.observe_event_rewards('event_20261022_cn', folder=tmp_path)
    assert result['status'] == 'blocked'
    assert result['reason'] == 'event_list_unrecognized'
    assert result['returned_main'] is True
    helper.device.click.assert_not_called()


def test_activity_list_main_return_failure_is_not_reported_as_observed(monkeypatch, tmp_path):
    helper = controller()
    monkeypatch.setattr(completion, 'ensure_event_page', Mock(side_effect=[True, False]))
    monkeypatch.setattr(completion, 'save_event_diagnostic', Mock(return_value='/tmp/screen.png'))
    result = helper.observe_event_rewards('event_20261022_cn', folder=tmp_path)
    assert result['status'] == 'blocked'
    assert result['reason'] == 'main_return_unrecognized'
    assert result['returned_main'] is False
    assert load_reward_observations('f2p', folder=tmp_path)['event_20261022_cn']['status'] == 'blocked'


def test_interrupted_activity_observation_never_navigates_again(monkeypatch, tmp_path):
    save_reward_observations('f2p', {'event_20261022_cn': {'status': 'observing'}}, folder=tmp_path)
    navigate = Mock()
    monkeypatch.setattr(completion, 'ensure_event_page', navigate)
    result = controller().observe_event_rewards('event_20261022_cn', folder=tmp_path)
    assert result['status'] == 'blocked'
    assert result['reason'] == 'previous_observation_interrupted'
    navigate.assert_not_called()


def test_activity_observation_exception_keeps_budget_spent_and_returns_to_main(monkeypatch, tmp_path):
    helper = controller()
    navigate = Mock(side_effect=[RuntimeError('capture navigation failed'), True])
    monkeypatch.setattr(completion, 'ensure_event_page', navigate)
    with pytest.raises(RuntimeError, match='capture navigation failed'):
        helper.observe_event_rewards('event_20261022_cn', folder=tmp_path)
    record = load_reward_observations('f2p', folder=tmp_path)['event_20261022_cn']
    assert record['status'] == 'blocked'
    assert record['returned_main'] is True
    assert navigate.call_args_list[-1].args[1] is page_main


def test_unsupported_activity_event_does_not_navigate_or_create_budget(monkeypatch, tmp_path):
    navigate = Mock()
    monkeypatch.setattr(completion, 'ensure_event_page', navigate)
    assert controller().observe_event_rewards('campaign_main', folder=tmp_path)['status'] == 'unsupported'
    navigate.assert_not_called()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('contents', ['not json', '[]', '{"event": 1}'])
def test_unreadable_observation_budget_fails_closed(monkeypatch, tmp_path, contents):
    (tmp_path / 'f2p-event-rewards.json').write_text(contents)
    navigate = Mock()
    monkeypatch.setattr(completion, 'ensure_event_page', navigate)
    with pytest.raises(ScriptError):
        controller().observe_event_rewards('event_20261022_cn', folder=tmp_path)
    navigate.assert_not_called()


def test_closed_game_does_not_spend_activity_observation_budget(tmp_path):
    helper = controller()
    helper.device.app_is_running.return_value = False
    with pytest.raises(GameNotRunningError):
        helper.observe_event_rewards('event_20261022_cn', folder=tmp_path)
    assert list(tmp_path.iterdir()) == []


def mission_controller(monkeypatch, state):
    helper = controller()
    monkeypatch.setattr(completion, 'ensure_event_page', Mock(return_value=True))
    monkeypatch.setattr(completion, 'save_event_diagnostic', Mock())
    helper.reward_mission_notice = Mock(return_value=True)
    helper.reward_side_navbar_ensure = Mock(return_value=True)
    helper._reward_wait_mission_list = Mock(return_value=state)
    helper._event_mission_click = Mock(return_value=True)
    helper._event_mission_receive = Mock(return_value=MISSION_UNFINISH)
    return helper


def test_clear_and_star_mission_claims_are_followed_until_no_claims_remain(monkeypatch):
    helper = mission_controller(monkeypatch, MISSION_MULTI)
    helper._event_mission_receive.side_effect = [MISSION_SINGLE, MISSION_UNFINISH]

    assert helper.collect_event_missions() is True

    assert completion.ensure_event_page.call_args_list[0].args == (helper, page_main)
    assert completion.ensure_event_page.call_args_list[1].args == (helper, page_mission)
    helper.reward_side_navbar_ensure.assert_called_once_with(upper=1)
    assert helper._event_mission_click.call_count == 2


@pytest.mark.parametrize('state', [None, 'timeout', 'new_unrecognized_ui'])
def test_unknown_mission_state_stops_without_claiming(monkeypatch, state):
    helper = mission_controller(monkeypatch, state)
    assert helper.collect_event_missions() is False
    helper._event_mission_click.assert_not_called()
    completion.save_event_diagnostic.assert_called_once()


def test_repeated_claims_have_a_finite_budget(monkeypatch):
    helper = mission_controller(monkeypatch, MISSION_MULTI)
    helper._event_mission_receive.return_value = MISSION_MULTI
    monkeypatch.setattr(completion, 'MISSION_CLAIMS_MAX', 2)
    assert helper.collect_event_missions() is False
    assert helper._event_mission_click.call_count == 2
    completion.save_event_diagnostic.assert_called_once()


def test_missing_notice_does_not_enter_the_mission_screen(monkeypatch):
    helper = mission_controller(monkeypatch, MISSION_MULTI)
    helper.reward_mission_notice.return_value = False
    assert helper.collect_event_missions() is True
    completion.ensure_event_page.assert_called_once_with(helper, page_main)
    helper.reward_side_navbar_ensure.assert_not_called()


def test_failed_all_missions_navbar_does_not_claim_another_tabs_empty_state(monkeypatch):
    helper = mission_controller(monkeypatch, MISSION_UNFINISH)
    helper.reward_side_navbar_ensure.return_value = False
    assert helper.collect_event_missions() is False
    helper._reward_wait_mission_list.assert_not_called()
    helper._event_mission_click.assert_not_called()
    completion.save_event_diagnostic.assert_called_once()


def test_claim_button_is_never_tapped_off_the_mission_page():
    helper = controller()
    helper.loop = Mock(return_value=iter(range(3)))
    helper.ui_page_appear = Mock(return_value=False)
    helper.appear_then_click = Mock(return_value=True)
    helper.match_template_color = Mock(return_value=True)
    assert helper._event_mission_click() is False
    helper.appear_then_click.assert_not_called()
    helper.device.click.assert_not_called()
    helper.loop.assert_called_once_with(timeout=15)


def test_unknown_reward_popup_expires_instead_of_resetting_forever():
    helper = controller()
    helper.loop = Mock(return_value=iter(range(3)))
    helper.ui_page_appear = Mock(return_value=False)
    helper.appear_then_click = Mock(return_value=False)
    for method in ('handle_mission_popup_ack', 'handle_vote_popup', 'handle_story_skip', 'handle_popup_confirm'):
        setattr(helper, method, Mock(return_value=False))
    assert helper._event_mission_receive() == 'timeout'
    helper.loop.assert_called_once_with(timeout=20)
