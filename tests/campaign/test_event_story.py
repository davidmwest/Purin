from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from module.eventstory import eventstory
from module.eventstory.eventstory import EventStory, ensure_event_page, ensure_event_switch
from module.base import timer
from module.combat.assets import BATTLE_PREPARATION
from module.exception import GameNotRunningError
from module.ui.assets import BACK_ARROW, GOTO_MAIN
from module.ui.page import Page, page_campaign, page_main, page_sp


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
        device=SimpleNamespace(click=Mock(), app_is_running=Mock(return_value=True)), ui_button_interval_reset=Mock(), handle_idle_page=Mock(return_value=False), match_template_color=Mock(return_value=False))
    assert ensure_event_page(main, page_main) is False
    main.device.click.assert_not_called()
    assert all(page.parent is None for page in Page.iter_pages())


def test_closed_game_raises_before_page_navigation_or_stale_image_detection():
    main = SimpleNamespace(device=SimpleNamespace(app_is_running=Mock(return_value=False)),
                           loop=Mock(), ui_page_appear=Mock(return_value=True))
    with pytest.raises(GameNotRunningError):
        ensure_event_page(main, page_main)
    main.loop.assert_not_called()
    main.ui_page_appear.assert_not_called()


def test_recognized_battle_preparation_uses_existing_back_arrow_exit():
    main = SimpleNamespace(
        loop=Mock(return_value=iter(range(2))), interval_clear=Mock(),
        ui_page_appear=Mock(side_effect=[False, True]),
        appear=Mock(side_effect=lambda button, **kwargs: button is BATTLE_PREPARATION),
        device=SimpleNamespace(click=Mock(), app_is_running=Mock(return_value=True)),
        ui_button_interval_reset=Mock(), handle_idle_page=Mock(return_value=False), match_template_color=Mock(return_value=False))
    assert ensure_event_page(main, page_main) is True
    main.device.click.assert_called_once_with(BACK_ARROW)


def test_page_transition_cannot_repeat_home_from_another_source_page(monkeypatch):
    clock = [1000.0]
    frame = [0]
    monkeypatch.setattr(timer, 'time', lambda: clock[0])

    def screenshots(**kwargs):
        for index, elapsed in enumerate((0, .75, 1.5, 2.25)):
            frame[0] = index
            clock[0] = 1000 + elapsed
            yield index

    def appear(button, **kwargs):
        # During the observed sub-second home transition, a different page
        # check can also match. Both sources would click the same home area.
        source = page_sp if frame[0] == 0 else page_campaign
        return button is source.check_button

    main = SimpleNamespace(
        loop=Mock(side_effect=screenshots), interval_clear=Mock(),
        ui_page_appear=Mock(side_effect=lambda destination: frame[0] == 3),
        appear=Mock(side_effect=appear),
        device=SimpleNamespace(click=Mock(), app_is_running=Mock(return_value=True)),
        ui_button_interval_reset=Mock(), handle_idle_page=Mock(return_value=False), match_template_color=Mock(return_value=False))
    assert ensure_event_page(main, page_main) is True
    main.device.click.assert_called_once_with(GOTO_MAIN)
    assert main.ui_current is page_main


def test_unchanged_known_page_can_retry_only_after_time_and_fresh_frames(monkeypatch):
    clock = [1000.0]
    frame = [0]
    clicked_at = []
    monkeypatch.setattr(timer, 'time', lambda: clock[0])

    def screenshots(**kwargs):
        # Even a long first screenshot cannot bypass the two-frame guard.
        for index, elapsed in enumerate((0, 3, 3.5, 4, 4.5, 5, 7)):
            frame[0] = index
            clock[0] = 1000 + elapsed
            yield index

    main = SimpleNamespace(
        loop=Mock(side_effect=screenshots), interval_clear=Mock(),
        ui_page_appear=Mock(return_value=False),
        appear=Mock(side_effect=lambda button, **kwargs: button is page_sp.check_button),
        device=SimpleNamespace(click=Mock(side_effect=lambda button: clicked_at.append(frame[0])),
                               app_is_running=Mock(return_value=True)),
        ui_button_interval_reset=Mock(), handle_idle_page=Mock(return_value=False), match_template_color=Mock(return_value=False))
    assert ensure_event_page(main, page_main, max_clicks=2) is False
    assert clicked_at == [0, 3]
    assert all(page.parent is None for page in Page.iter_pages())


def test_captured_f2p_idle_screen_wakes_before_page_graph_navigation():
    from pathlib import Path
    from types import MethodType

    import numpy as np
    from PIL import Image

    from module.base.timer import Timer
    from module.ui.assets import REWARD_GOTO_MAIN
    from module.ui.ui import UI

    image = np.asarray(Image.open(Path(__file__).parent / 'data/f2p_idle.png').convert('RGB'))
    main = SimpleNamespace(
        loop=Mock(return_value=iter(range(2))), interval_clear=Mock(),
        ui_page_appear=Mock(side_effect=[False, True]), appear=Mock(return_value=False),
        get_interval_timer=Mock(return_value=Timer(3)),
        device=SimpleNamespace(image=image, click=Mock(), app_is_running=Mock(return_value=True)),
        ui_button_interval_reset=Mock(), match_template_color=Mock(return_value=False))
    main.handle_idle_page = MethodType(UI.handle_idle_page, main)

    assert ensure_event_page(main, page_main) is True
    main.device.click.assert_called_once_with(REWARD_GOTO_MAIN)
    main.appear.assert_not_called()
    assert main.ui_current is page_main

    # Unknown artwork must never authorize the same wake-up coordinate.
    main.device.image = np.zeros_like(image)
    main.get_interval_timer.return_value.clear()
    main.device.click.reset_mock()
    assert main.handle_idle_page() is False
    main.device.click.assert_not_called()


def test_idle_wakeup_uses_existing_navigation_click_and_settle_budget(monkeypatch):
    clock = [1000.0]
    frame = [0]
    clicked_at = []
    monkeypatch.setattr(timer, 'time', lambda: clock[0])

    def screenshots(**kwargs):
        for index, elapsed in enumerate((0, 3, 3.5, 4, 4.5, 5, 7)):
            frame[0] = index
            clock[0] = 1000 + elapsed
            yield index

    def wake_up():
        clicked_at.append(frame[0])
        return True

    main = SimpleNamespace(
        loop=Mock(side_effect=screenshots), interval_clear=Mock(),
        ui_page_appear=Mock(return_value=False), appear=Mock(return_value=False),
        handle_idle_page=Mock(side_effect=wake_up),
        device=SimpleNamespace(click=Mock(), app_is_running=Mock(return_value=True)),
        ui_button_interval_reset=Mock())
    assert ensure_event_page(main, page_main, timeout=8, max_clicks=2) is False
    assert clicked_at == [0, 3]
    main.loop.assert_called_once_with(timeout=8)
    main.appear.assert_not_called()
    assert all(page.parent is None for page in Page.iter_pages())


@pytest.mark.parametrize('filename,title', [('tempesta_hub.png', 'TEMPESTA_HUB'),
                                         ('tempesta_rewards_catalog.png', 'TEMPESTA_HUB_INACTIVE')])
@pytest.mark.parametrize('missing', [None, 'title', 'home'])
def test_captured_tempesta_hub_exit_requires_title_and_home(missing, filename, title):
    from pathlib import Path

    import numpy as np
    from PIL import Image

    from module.campaign import event_completion_assets as assets
    from module.campaign.event_completion_assets import TEMPESTA_HUB_HOME

    image = np.asarray(Image.open(Path(__file__).parent / 'data' / filename).convert('RGB')).copy()
    if missing:
        button = getattr(assets, title) if missing == 'title' else TEMPESTA_HUB_HOME
        x1, y1, x2, y2 = button.area
        image[y1 - 6:y2 + 6, x1 - 6:x2 + 6] = 0
    main = SimpleNamespace(
        loop=Mock(return_value=iter(range(2))), interval_clear=Mock(),
        ui_page_appear=Mock(side_effect=[False, True] if missing is None else [False, False]),
        appear=Mock(return_value=False), handle_idle_page=Mock(return_value=False),
        device=SimpleNamespace(image=image, click=Mock(), app_is_running=Mock(return_value=True)),
        ui_button_interval_reset=Mock())
    main.match_template_color = lambda button, **kwargs: button.match_template_color(image, **kwargs)

    assert ensure_event_page(main, page_main) is (missing is None)
    if missing is None:
        main.device.click.assert_called_once_with(TEMPESTA_HUB_HOME)
        main.appear.assert_not_called()
    else:
        main.device.click.assert_not_called()


def test_tempesta_hub_exit_shares_navigation_click_and_settle_budget(monkeypatch):
    from module.campaign.event_completion_assets import TEMPESTA_HUB_HOME

    clock = [1000.0]
    frame = [0]
    clicked_at = []
    monkeypatch.setattr(timer, 'time', lambda: clock[0])

    def screenshots(**kwargs):
        for index, elapsed in enumerate((0, 3, 3.5, 4, 4.5, 5, 7)):
            frame[0] = index
            clock[0] = 1000 + elapsed
            yield index

    main = SimpleNamespace(
        loop=Mock(side_effect=screenshots), interval_clear=Mock(),
        ui_page_appear=Mock(return_value=False), appear=Mock(return_value=False),
        handle_idle_page=Mock(return_value=False), match_template_color=Mock(return_value=True),
        device=SimpleNamespace(click=Mock(side_effect=lambda button: clicked_at.append((frame[0], button))),
                               app_is_running=Mock(return_value=True)),
        ui_button_interval_reset=Mock())
    assert ensure_event_page(main, page_main, max_clicks=2) is False
    assert clicked_at == [(0, TEMPESTA_HUB_HOME), (3, TEMPESTA_HUB_HOME)]
    main.appear.assert_not_called()
    assert all(page.parent is None for page in Page.iter_pages())
