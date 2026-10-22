from types import SimpleNamespace
from unittest.mock import Mock

import module.campaign.run as run_module
from module.campaign.run import CampaignRun
from module.campaign.star_pass import STAR_RUNS_MAX, add_star_run, load_star_runs


def test_runs_are_kept_by_stage(tmp_path):
    assert load_star_runs('f2p', tmp_path) == {}
    assert add_star_run('f2p', '1-1', tmp_path) == 1
    assert add_star_run('f2p', '1-1', tmp_path) == 2
    assert add_star_run('f2p', '1-2', tmp_path) == 1
    assert load_star_runs('f2p', tmp_path) == {'1-1': 2, '1-2': 1}
    assert load_star_runs('alas', tmp_path) == {}


def test_a_broken_file_starts_over(tmp_path):
    (tmp_path / 'f2p.json').write_text('{not json')
    assert load_star_runs('f2p', tmp_path) == {}
    assert add_star_run('f2p', '2-1', tmp_path) == 1


def make_run(monkeypatch, tmp_path, achievement='map_3_stars', increase=True):
    monkeypatch.setattr(run_module, 'add_star_run', lambda name, stage: add_star_run(name, stage, tmp_path))
    run = CampaignRun.__new__(CampaignRun)
    run.config = SimpleNamespace(
        config_name='f2p', Campaign_Name='campaign_1_1',
        StopCondition_MapAchievement=achievement, StopCondition_StageIncrease=increase)
    run.campaign = SimpleNamespace(handle_map_stop=Mock())
    return run


def test_a_stage_with_no_3_stars_is_left_after_the_most_runs(monkeypatch, tmp_path):
    run = make_run(monkeypatch, tmp_path)
    for _ in range(STAR_RUNS_MAX - 1):
        assert run.handle_star_runs_max() is False
    run.campaign.handle_map_stop.assert_not_called()

    assert run.handle_star_runs_max() is True
    run.campaign.handle_map_stop.assert_called_once_with()


def test_runs_that_are_not_a_pass_for_stars_are_not_counted(monkeypatch, tmp_path):
    for run in (make_run(monkeypatch, tmp_path, achievement='non_stop'),
                make_run(monkeypatch, tmp_path, increase=False)):
        for _ in range(STAR_RUNS_MAX + 1):
            assert run.handle_star_runs_max() is False
    assert load_star_runs('f2p', tmp_path) == {}
