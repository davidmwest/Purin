from types import SimpleNamespace
from unittest.mock import Mock

import module.campaign.run as run_module
import module.handler.fast_forward as fast_forward
from module.campaign.run import CampaignRun
from module.campaign.star_pass import (STAR_RUNS_MAX, add_star_run, clear_star_runs, load_star_runs,
                                       star_stage_key)
from module.handler.fast_forward import FastForwardHandler

MAIN_1_1 = star_stage_key('campaign_main', 'normal', '1-1')


def test_runs_are_kept_by_stage(tmp_path):
    assert load_star_runs('f2p', tmp_path) == {}
    assert add_star_run('f2p', MAIN_1_1, tmp_path) == 1
    assert add_star_run('f2p', MAIN_1_1, tmp_path) == 2
    assert add_star_run('f2p', 'campaign_main/normal/1-2', tmp_path) == 1
    assert load_star_runs('f2p', tmp_path) == {MAIN_1_1: 2, 'campaign_main/normal/1-2': 1}
    assert load_star_runs('alas', tmp_path) == {}


def test_a_stage_of_an_other_event_or_mode_is_an_other_stage():
    assert star_stage_key('event_20261022_cn', 'normal', 'T1') != star_stage_key('event_20261105_cn', 'normal', 'T1')
    assert star_stage_key('campaign_main', 'hard', '1-1') != MAIN_1_1


def test_a_broken_file_starts_over(tmp_path):
    (tmp_path / 'f2p.json').write_text('{not json')
    assert load_star_runs('f2p', tmp_path) == {}
    assert add_star_run('f2p', MAIN_1_1, tmp_path) == 1


def test_runs_cleared_when_the_pass_leaves_the_stage(tmp_path):
    add_star_run('f2p', MAIN_1_1, tmp_path)
    assert clear_star_runs('f2p', MAIN_1_1, tmp_path) is True
    assert load_star_runs('f2p', tmp_path) == {}
    assert clear_star_runs('f2p', MAIN_1_1, tmp_path) is False


def make_run(monkeypatch, tmp_path, achievement='map_3_stars', increase=True, retreat_remain=0, lost=False):
    monkeypatch.setattr(run_module, 'add_star_run', lambda name, key: add_star_run(name, key, tmp_path))
    monkeypatch.setattr(run_module, 'clear_star_runs', lambda name, key: clear_star_runs(name, key, tmp_path))
    run = CampaignRun.__new__(CampaignRun)
    run.config = SimpleNamespace(
        config_name='f2p', Campaign_Name='campaign_1_1', Campaign_Event='campaign_main', Campaign_Mode='normal',
        StopCondition_MapAchievement=achievement, StopCondition_StageIncrease=increase,
        StopCondition_RetreatRemain=retreat_remain)
    run.campaign = SimpleNamespace(handle_map_stop=Mock(), run_lost=lost)
    return run


def test_a_stage_with_no_3_stars_is_left_after_the_most_runs(monkeypatch, tmp_path):
    run = make_run(monkeypatch, tmp_path)
    for _ in range(STAR_RUNS_MAX - 1):
        assert run.handle_star_runs_max() is False
    run.campaign.handle_map_stop.assert_not_called()

    assert run.handle_star_runs_max() is True
    run.campaign.handle_map_stop.assert_called_once_with()
    # A later pass, with a stronger fleet, starts the stage over
    assert load_star_runs('f2p', tmp_path) == {}


def test_runs_that_are_not_a_pass_for_stars_are_not_counted(monkeypatch, tmp_path):
    for run in (make_run(monkeypatch, tmp_path, achievement='non_stop'),
                make_run(monkeypatch, tmp_path, increase=False)):
        for _ in range(STAR_RUNS_MAX + 1):
            assert run.handle_star_runs_max() is False
    assert load_star_runs('f2p', tmp_path) == {}


def test_leveling_runs_of_a_stage_retreat_are_not_runs_of_the_pass(monkeypatch, tmp_path):
    # Code review, 2026-10-22: 3-1 capped earlier, a loss on 3-2 sent the fleet back to level on 3-1, and its first
    # leveling run sent it on to 3-2 again
    run = make_run(monkeypatch, tmp_path, retreat_remain=2)
    for _ in range(STAR_RUNS_MAX + 1):
        assert run.handle_star_runs_max() is False
    run.campaign.handle_map_stop.assert_not_called()
    assert load_star_runs('f2p', tmp_path) == {}


def test_lost_runs_are_not_counted(monkeypatch, tmp_path):
    # 6 losses would send a fleet that cannot win on to a harder stage
    run = make_run(monkeypatch, tmp_path, lost=True)
    for _ in range(STAR_RUNS_MAX + 1):
        assert run.handle_star_runs_max() is False
    run.campaign.handle_map_stop.assert_not_called()


def test_a_stage_that_gets_its_3_stars_drops_its_runs(monkeypatch, tmp_path):
    monkeypatch.setattr(fast_forward, 'clear_star_runs', lambda name, key: clear_star_runs(name, key, tmp_path))
    add_star_run('f2p', MAIN_1_1, tmp_path)
    add_star_run('f2p', 'campaign_main/normal/1-2', tmp_path)
    config = SimpleNamespace(
        config_name='f2p', STAGE_INCREASE_AB=False, STAGE_INCREASE_CUSTOM=None, Campaign_Event='campaign_main',
        Campaign_Mode='normal', Campaign_Name='campaign_1_1', StopCondition_StageIncrease=True,
        StopCondition_MapAchievement='map_3_stars', MAP_IS_ONE_TIME_STAGE=False, Scheduler_Enable=True)
    handler = FastForwardHandler.__new__(FastForwardHandler)
    handler.config = config

    handler.handle_map_stop()

    assert config.Campaign_Name == '1-2'
    assert load_star_runs('f2p', tmp_path) == {'campaign_main/normal/1-2': 1}
