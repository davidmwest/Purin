from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from module.campaign import event_completion as completion
from module.campaign.event_completion import EventCompletion, load_reward_observations, save_reward_observations
from module.exception import GameNotRunningError, ScriptError
from module.reward.assets import MISSION_MULTI, MISSION_SINGLE, MISSION_UNFINISH
from module.ui.page import page_event_list, page_main, page_mission


def controller():
    helper = EventCompletion.__new__(EventCompletion)
    helper.config = SimpleNamespace(config_name='f2p')
    helper.device = SimpleNamespace(stuck_record_clear=Mock(), click_record_clear=Mock(), click=Mock(),
                                    app_is_running=Mock(return_value=True))
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
    helper.observe_event_rewards = Mock(return_value={'status': 'observed'})
    result = helper.run('event_20261022_cn')
    assert result == {'story': 'disabled', 'rewards': 'complete', 'event_hub_rewards': 'unsupported',
                      'event_reward_observation': {'status': 'observed'}}
    helper.observe_event_rewards.assert_called_once_with('event_20261022_cn')


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
