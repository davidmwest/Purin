from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from module.eventstory import eventstory
from module.eventstory.eventstory import EventStory, ensure_event_page, ensure_event_switch
from module.ui.page import Page, page_main


def controller(monkeypatch):
    helper = EventStory.__new__(EventStory)
    helper.config = SimpleNamespace(override=Mock(), cross_get=Mock(return_value='wrong_event'))
    helper.device = SimpleNamespace(app_is_running=Mock(return_value=True))
    helper.ui_goto_event_story = Mock(return_value='story')
    helper.event_story = Mock(return_value='unknown')
    helper.app_stop = Mock()
    helper.app_start = Mock()
    monkeypatch.setattr(eventstory, 'save_event_diagnostic', Mock())
    return helper


def test_unrecognized_story_entrance_never_runs_the_tapping_story_loop(monkeypatch):
    helper = controller(monkeypatch)
    helper.ui_goto_event_story.return_value = 'unknown'
    assert helper.run_event_story(event='event_20261022_cn') is False
    helper.event_story.assert_not_called()
    helper.app_stop.assert_not_called()
    eventstory.save_event_diagnostic.assert_called_once()


def test_story_completion_requires_the_final_marker(monkeypatch):
    helper = controller(monkeypatch)
    helper.ui_goto_event_story.side_effect = ['story', 'finish']
    helper.event_story.return_value = 'finish'
    monkeypatch.setattr(eventstory, 'ensure_event_page', Mock(return_value=True))
    assert helper.run_event_story(event='event_20261022_cn') is True
    assert helper.ui_goto_event_story.call_count == 2
    helper.ui_goto_event_story.assert_called_with(event='event_20261022_cn')


def test_story_battle_is_not_restarted_without_explicit_skip(monkeypatch):
    helper = controller(monkeypatch)
    helper.event_story.return_value = 'battle'
    assert helper.run_event_story(event='event_20261022_cn', skip_battle=False) is False
    helper.app_stop.assert_not_called()
    helper.app_start.assert_not_called()


def test_story_battle_restarts_are_bounded_even_when_progress_never_changes(monkeypatch):
    helper = controller(monkeypatch)
    helper.event_story.return_value = 'battle'
    monkeypatch.setattr(eventstory, 'STORY_BATTLES_MAX', 2)
    assert helper.run_event_story(event='event_20261022_cn', skip_battle=True) is False
    assert helper.app_stop.call_count == 2
    assert helper.app_start.call_count == 2
    assert helper.event_story.call_count == 3


def test_story_scene_transitions_have_an_independent_budget(monkeypatch):
    helper = controller(monkeypatch)
    helper.event_story.return_value = 'finish'
    monkeypatch.setattr(eventstory, 'STORY_TRANSITIONS_MAX', 2)
    monkeypatch.setattr(eventstory, 'ensure_event_page', Mock(return_value=True))
    assert helper.run_event_story(event='event_20261022_cn', skip_battle=True) is False
    assert helper.event_story.call_count == 2


@pytest.mark.parametrize('skip', [False, True])
def test_standalone_story_honors_skipbattle_and_explicit_event(monkeypatch, skip):
    helper = controller(monkeypatch)
    helper.config.EventStory_SkipBattle = skip
    helper.run_event_story = Mock(return_value=True)
    assert helper.run(event='event_20261022_cn') is True
    helper.run_event_story.assert_called_once_with(event='event_20261022_cn', skip_battle=skip)
    helper.config.cross_get.assert_not_called()


def test_unknown_switch_is_not_clicked_even_after_its_timeout():
    main = SimpleNamespace(loop=Mock(return_value=iter(range(4))))
    switch = SimpleNamespace(get=Mock(return_value='unknown'), click=Mock())
    assert ensure_event_switch(main, switch, 'ex') is False
    switch.click.assert_not_called()
    main.loop.assert_called_once_with(timeout=10)


def test_known_switch_verifies_the_requested_state_after_click():
    main = SimpleNamespace(loop=Mock(return_value=iter(range(4))))
    switch = SimpleNamespace(get=Mock(side_effect=['part2', 'ex']), click=Mock())
    assert ensure_event_switch(main, switch, 'ex') is True
    switch.click.assert_called_once_with('ex', main=main)


def test_unknown_page_never_uses_a_page_graph_coordinate():
    main = SimpleNamespace(
        loop=Mock(return_value=iter(range(3))), interval_clear=Mock(),
        ui_page_appear=Mock(return_value=False), appear=Mock(return_value=False),
        device=SimpleNamespace(click=Mock()), ui_button_interval_reset=Mock())
    assert ensure_event_page(main, page_main) is False
    main.device.click.assert_not_called()
    assert all(page.parent is None for page in Page.iter_pages())
