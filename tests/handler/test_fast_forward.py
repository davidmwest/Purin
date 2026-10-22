from types import SimpleNamespace

import pytest

from module.handler.fast_forward import FastForwardHandler


def make_handler(stage_increase_ab, event='event_20250912_cn'):
    config = SimpleNamespace(STAGE_INCREASE_AB=stage_increase_ab, STAGE_INCREASE_CUSTOM=None, Campaign_Event=event)
    return SimpleNamespace(config=config, STAGE_INCREASE=FastForwardHandler.STAGE_INCREASE)


@pytest.mark.parametrize('stage, following', [
    ('A3', 'B1'),
    ('B3', 'B3'),
    ('C1', 'C2'),
    ('C3', 'D1'),
    ('D2', 'D3'),
    ('D3', 'D3'),
])
def test_stage_increase_continues_into_the_second_part(stage, following):
    handler = make_handler(stage_increase_ab=True)

    assert FastForwardHandler.campaign_name_increase(handler, stage) == following


@pytest.mark.parametrize('stage, following', [
    ('A3', 'A3'),
    ('C2', 'C3'),
    ('C3', 'C3'),
])
def test_stage_increase_stays_in_its_chapter_without_the_ab_chain(stage, following):
    handler = make_handler(stage_increase_ab=False)

    assert FastForwardHandler.campaign_name_increase(handler, stage) == following


def stopped_on(stage, event='event_20261022_cn', one_time=False):
    """
    The config after handle_map_stop on a stage cleared, with stage increase.
    """
    config = SimpleNamespace(
        STAGE_INCREASE_AB=False, STAGE_INCREASE_CUSTOM=None, Campaign_Event=event, Campaign_Name=stage,
        StopCondition_StageIncrease=True, StopCondition_MapAchievement='100_percent_clear',
        MAP_IS_ONE_TIME_STAGE=one_time, Scheduler_Enable=True)
    handler = SimpleNamespace(config=config, STAGE_INCREASE=FastForwardHandler.STAGE_INCREASE)
    handler.campaign_name_increase = lambda name: FastForwardHandler.campaign_name_increase(handler, name)
    handler.campaign_name_last = lambda name: FastForwardHandler.campaign_name_last(handler, name)
    handler.call_then_task = lambda: FastForwardHandler.call_then_task(handler)
    FastForwardHandler.handle_map_stop(handler)
    return config


def test_a_stage_cleared_goes_to_the_next():
    config = stopped_on('T3')

    assert config.Campaign_Name == 'T4'
    assert config.Scheduler_Enable is True


def test_the_last_stage_of_an_event_is_farmed_on():
    # Tempesta and the Corridor of Fog ends at T6, 2026-10-22
    config = stopped_on('T6')

    assert config.Campaign_Name == 'T6'
    assert config.StopCondition_MapAchievement == 'non_stop'
    assert config.Scheduler_Enable is True


def test_a_one_time_stage_still_stops():
    config = stopped_on('SP', one_time=True)

    assert config.Scheduler_Enable is False


def test_a_stage_out_of_the_chains_still_stops():
    config = stopped_on('EX', one_time=False)

    assert config.Scheduler_Enable is False


def test_a_pass_that_stops_enables_the_task_it_held():
    # The 3-star pass over the campaign holds the event, which runs again once the pass stops
    from unittest.mock import Mock
    config = SimpleNamespace(
        STAGE_INCREASE_AB=False, STAGE_INCREASE_CUSTOM=None, Campaign_Event='event_20261022_cn', Campaign_Name='EX',
        StopCondition_StageIncrease=True, StopCondition_MapAchievement='map_3_stars', MAP_IS_ONE_TIME_STAGE=False,
        Scheduler_Enable=True, StopCondition_ThenTask='Event', task=SimpleNamespace(command='Main2'), task_call=Mock())
    handler = SimpleNamespace(config=config, STAGE_INCREASE=FastForwardHandler.STAGE_INCREASE)
    handler.campaign_name_increase = lambda name: FastForwardHandler.campaign_name_increase(handler, name)
    handler.campaign_name_last = lambda name: FastForwardHandler.campaign_name_last(handler, name)
    handler.call_then_task = lambda: FastForwardHandler.call_then_task(handler)

    FastForwardHandler.handle_map_stop(handler)

    assert config.Scheduler_Enable is False
    config.task_call.assert_called_once_with('Event', force_call=True)


def star_handler(stars, clear_all=False, use_clear_mode=True):
    """
    A handler on a stage with these stars, in its map preparation.
    """
    config = SimpleNamespace(
        STAR_REQUIRE_1=1, STAR_REQUIRE_2=2, STAR_REQUIRE_3=3, MAP_CLEAR_ALL_THIS_TIME=clear_all,
        Campaign_UseClearMode=use_clear_mode, Campaign_UseAutoSearch=True, Campaign_Use2xBook=False)
    handler = FastForwardHandler.__new__(FastForwardHandler)
    handler.config = config
    handler.map_has_clear_mode = True
    for star in (1, 2, 3):
        setattr(handler, f'map_achieved_star_{star}', star in stars)
    return handler


@pytest.mark.parametrize('stars, missing', [
    # 1-1 of the F2P account, 2026-10-22: the escort fleets were never enough
    ((1, 3), [2]),
    ((1, 2), [3]),
    ((1,), [2, 3]),
    ((1, 2, 3), []),
    # The boss star alone is had on any clear
    ((2, 3), []),
])
def test_the_stars_a_full_clear_gets(stars, missing):
    assert star_handler(stars).map_stars_missing() == missing


def test_a_stage_with_stars_to_get_is_played_out_of_clear_mode(monkeypatch):
    switch = SimpleNamespace(calls=[])
    switch.set = lambda state, main: switch.calls.append(state) or False
    monkeypatch.setattr('module.handler.fast_forward.CLEAR_MODE', switch)
    handler = star_handler((1, 3), clear_all=True)

    FastForwardHandler.handle_fast_forward(handler)

    assert switch.calls == ['off']
    assert handler.map_is_clear_mode is False
    assert handler.map_is_auto_search is False


def test_a_stage_with_every_star_keeps_clear_mode(monkeypatch):
    switch = SimpleNamespace(calls=[])
    switch.set = lambda state, main: switch.calls.append(state) or False
    monkeypatch.setattr('module.handler.fast_forward.CLEAR_MODE', switch)
    handler = star_handler((1, 2, 3))

    FastForwardHandler.handle_fast_forward(handler)

    assert switch.calls == ['on']
    assert handler.map_is_clear_mode is True
    assert handler.map_is_auto_search is True


def settled(reads):
    handler = FastForwardHandler.__new__(FastForwardHandler)
    handler.device = SimpleNamespace(screenshot=lambda: None)
    reads = iter(reads)
    handler.map_read_stars = lambda: next(reads)
    return FastForwardHandler.map_stars_settled(handler)


def test_stars_still_lighting_up_are_read_again():
    # 1-4 of the F2P account, 2026-10-22: star 3 alone as the map information opened
    lit = (True, True, True)
    assert settled([(False, False, True), (True, False, True), lit, lit]) == lit


def test_stars_that_agree_at_once_are_kept():
    stars = (True, False, True)
    assert settled([stars, stars]) == stars


def test_stars_that_never_settle_end_on_the_last_read(monkeypatch):
    monkeypatch.setattr('module.handler.fast_forward.Timer', lambda *a, **k: SimpleNamespace(
        start=lambda: SimpleNamespace(reached=lambda: True)))
    assert settled([(True, False, False), (False, True, False)]) == (False, True, False)
