from types import SimpleNamespace

import pytest

from module.campaign.run import CampaignRun
from module.handler.fast_forward import FastForwardHandler


@pytest.mark.parametrize('name, before', [
    ('1-3', '1-2'), ('2-1', '1-4'), ('campaign_7_2', '7-1'), ('1-1', '1-1'), ('A2', 'A1'), ('SP1', 'SP1'),
])
def test_stage_before(name, before):
    handler = FastForwardHandler.__new__(FastForwardHandler)

    assert handler.campaign_name_decrease(name) == before


def make_run(stage='1-3', retreat=3, increase=True, remain=0, target=''):
    run = CampaignRun.__new__(CampaignRun)
    run.config = SimpleNamespace(
        Campaign_Name=stage,
        StopCondition_StageIncrease=increase,
        StopCondition_StageRetreat=retreat,
        StopCondition_RetreatFrom=target,
        StopCondition_RetreatRemain=remain,
    )
    handler = FastForwardHandler.__new__(FastForwardHandler)
    run.campaign = SimpleNamespace(run_lost=False, campaign_name_decrease=handler.campaign_name_decrease)
    return run


def test_battle_lost_goes_back_to_the_stage_before():
    run = make_run('1-3')
    run.campaign.run_lost = True

    assert run.handle_stage_retreat() is True

    assert run.config.Campaign_Name == '1-2'
    assert (run.config.StopCondition_RetreatFrom, run.config.StopCondition_RetreatRemain) == ('1-3', 3)


def test_stage_lost_is_tried_again_after_the_runs_to_level():
    run = make_run('1-2', remain=3, target='1-3')

    # 2 runs of the stage before go on in the same task
    assert run.handle_stage_retreat() is False
    assert run.handle_stage_retreat() is False
    assert run.config.StopCondition_RetreatRemain == 1
    # The last one switches back
    assert run.handle_stage_retreat() is True
    assert run.config.Campaign_Name == '1-3'
    assert (run.config.StopCondition_RetreatFrom, run.config.StopCondition_RetreatRemain) == ('', 0)


def test_lost_again_while_levelling_goes_further_back():
    run = make_run('1-2', remain=2, target='1-3')
    run.campaign.run_lost = True

    assert run.handle_stage_retreat() is True

    # The stage to come back to stays the one lost first
    assert run.config.Campaign_Name == '1-1'
    assert (run.config.StopCondition_RetreatFrom, run.config.StopCondition_RetreatRemain) == ('1-3', 3)


@pytest.mark.parametrize('kwargs', [{'retreat': 0}, {'increase': False}, {'stage': '1-1'}])
def test_no_retreat(kwargs):
    # Retreat off, no stage increase, or the first stage: try again as Alas does
    run = make_run(**kwargs)
    run.campaign.run_lost = True

    assert run.handle_stage_retreat() is False

    assert run.config.Campaign_Name == kwargs.get('stage', '1-3')
    assert run.config.StopCondition_RetreatRemain == 0


def test_map_achievement_is_not_a_stop_while_levelling():
    handler = FastForwardHandler.__new__(FastForwardHandler)
    handler.config = SimpleNamespace(StopCondition_RetreatRemain=2, StopCondition_MapAchievement='100_percent_clear')
    handler.map_is_100_percent_clear = True

    assert handler.triggered_map_stop() is False

    handler.config.StopCondition_RetreatRemain = 0
    assert handler.triggered_map_stop() is True


def test_retreat_survives_loading_the_config():
    # Hidden arguments go back to their default at every load of the config. The task run next, on the stage to
    # level on, forgot the retreat and increased the stage back to the one lost.
    from module.config.config_updater import ConfigUpdater
    from module.config.deep import deep_get

    old = {'Main': {'StopCondition': {'StageIncrease': True, 'StageRetreat': 3, 'RetreatFrom': '2-4', 'RetreatRemain': 3}}}
    new = ConfigUpdater().config_update(old)

    assert deep_get(new, 'Main.StopCondition.RetreatFrom') == '2-4'
    assert deep_get(new, 'Main.StopCondition.RetreatRemain') == 3
