from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import module.config.server as server
from module.config.config_updater import ConfigUpdater
from module.exception import ScriptError
from module.minigame.arcade import ArcadeTickets, next_week


def test_current_scaled_arcade_room_controls_reject_dimmed_background():
    from module.base.utils import load_image
    from module.minigame.arcade import ARCADE_START, ARCADE_TITLE
    image = load_image('assets/en/minigame/arcade_room.png')
    for control in (ARCADE_TITLE, ARCADE_START):
        assert control.match_template_color(image, offset=(2, 2), similarity=.95, threshold=10)
        assert not control.match_template_color((image * .5).astype('uint8'), offset=(2, 2), similarity=.95, threshold=10)


def test_captured_credit_guide_is_distinct_from_the_room_and_does_not_cover_start_input():
    from module.base.utils import load_image
    from module.minigame.arcade import ARCADE_CREDIT_TUTORIAL, ARCADE_ROOM_TUTORIAL, ARCADE_TITLE
    room = load_image('assets/en/minigame/arcade_room_tutorial.png')
    credits = load_image('assets/en/minigame/arcade_credit_tutorial.png')
    assert ARCADE_ROOM_TUTORIAL.match(room, offset=(2, 2), similarity=.97)
    assert ARCADE_CREDIT_TUTORIAL.match(credits, offset=(2, 2), similarity=.97)
    assert not ARCADE_CREDIT_TUTORIAL.match(room, offset=(2, 2), similarity=.97)
    assert not ARCADE_TITLE.match_template_color(room, offset=(2, 2), similarity=.95, threshold=10)
    assert ARCADE_CREDIT_TUTORIAL.button[3] < 300  # The game's Start button is below y380.


def test_arcade_defaults_disabled_and_keeps_farming_and_other_minigame_settings():
    old = {'Main': {'Campaign': {'Name': '12-4'}, 'StopCondition': {'OilLimit': 5000}},
           'Minigame': {'Scheduler': {'Enable': False}}}
    new = ConfigUpdater().config_update(old)
    assert new['ArcadeTickets']['Scheduler']['Enable'] is False
    assert new['ArcadeTickets']['Arcade'] == {'ObserveOnly': True, 'CreditsPerRound': 5}
    assert new['Main']['Campaign']['Name'] == '12-4'
    assert new['Main']['StopCondition']['OilLimit'] == 5000
    assert not new['Minigame']['Scheduler']['Enable']


@pytest.mark.parametrize('now,expected', [
    ('2026-11-04T12:00:00-08:00', '2026-11-09T00:00:00-07:00'),
    ('2026-11-08T22:59:59-08:00', '2026-11-09T00:00:00-07:00'),
    ('2026-11-08T23:00:00-08:00', '2026-11-16T00:00:00-07:00'),
    ('2026-10-31T12:00:00-07:00', '2026-11-02T00:00:00-07:00'),
])
def test_weekly_schedule_uses_fixed_en_server_reset_across_local_dst(now, expected):
    previous = server.server
    server.set_server('en')
    try:
        result = next_week(datetime.fromisoformat(now))
        assert result.astimezone(timezone.utc) == datetime.fromisoformat(expected).astimezone(timezone.utc)
    finally:
        server.set_server(previous)


def task(observe=True):
    job = ArcadeTickets.__new__(ArcadeTickets)
    job.config = SimpleNamespace(Arcade_ObserveOnly=observe, cross_set=Mock(), task_call=Mock(), task_delay=Mock())
    for method in ('enter_arcade', 'open_games', 'capture', 'ui_goto_main'):
        setattr(job, method, Mock())
    return job


def test_observation_does_not_leave_a_repeat_job_enabled():
    job = task()
    job.run()
    job.config.cross_set.assert_called_once_with('ArcadeTickets.Scheduler.Enable', False)
    job.ui_goto_main.assert_called_once()
    job.config.task_call.assert_not_called()


def test_unknown_arcade_layout_defers_and_queues_normal_game_restart():
    job = task()
    job.open_games.side_effect = ScriptError('unknown game list')
    job.run()
    job.config.task_call.assert_called_once_with('Restart')
    job.config.task_delay.assert_called_once_with(minute=60)
