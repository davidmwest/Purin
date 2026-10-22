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
