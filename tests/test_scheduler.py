from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

import alas
from alas import AzurLaneAutoScript


class Config:
    def __init__(self, paused_checks):
        """
        Args:
            paused_checks (int): How many times the pause file is found before it is gone.
        """
        self.paused_checks = paused_checks

    @property
    def pause_requested(self):
        self.paused_checks -= 1
        return self.paused_checks >= 0


def make_scheduler(paused_checks):
    return SimpleNamespace(config=Config(paused_checks), config_name='alas', stop_event=None)


def test_a_task_that_keeps_failing_is_put_off_longer_each_time():
    delays, previous = [], None
    for _ in range(6):
        previous = alas.failed_task_delay(previous)
        delays.append(previous)

    assert delays == [20, 40, 80, 160, 240, 240]


def test_failed_task_is_put_off_and_counted_from_zero_again(monkeypatch):
    monkeypatch.setattr(alas, 'handle_notify', Mock())
    monkeypatch.setattr(alas, 'del_cached_property', Mock())
    config = SimpleNamespace(Error_OnePushConfig='provider: null', task_delay=Mock())
    scheduler = SimpleNamespace(config=config, config_name='alas', failure_record={'Event2': 3},
                                failure_delay={})

    assert AzurLaneAutoScript.put_off_failed_task(scheduler, 'Event2') == 20
    assert scheduler.failure_record == {'Event2': 0}
    config.task_delay.assert_called_once_with(minute=20, task='Event2')

    # Failing 3 times again without a success in between
    assert AzurLaneAutoScript.put_off_failed_task(scheduler, 'Event2') == 40
    # Another task starts from the beginning
    assert AzurLaneAutoScript.put_off_failed_task(scheduler, 'Event') == 20


class Runner(SimpleNamespace):
    """
    A scheduler whose task named `event2` raises the given error.
    """

    def __init__(self, keep_running, error):
        def task():
            raise error

        super().__init__(
            config=SimpleNamespace(Error_KeepRunning=keep_running, Error_OnePushConfig='provider: null',
                                   task_call=Mock()),
            config_name='alas',
            device=SimpleNamespace(screenshot=Mock(), sleep=Mock(), package='com.YoStarEN.AzurLane'),
            save_error_log=Mock(),
            # The game is not at its login page
            logged_out_on_screen=Mock(return_value=False),
            handle_logged_out=Mock(),
            event2=task,
        )


def test_unexpected_error_restarts_the_game_when_keep_running(monkeypatch):
    runner = Runner(keep_running=True, error=ValueError('something unexpected'))

    assert AzurLaneAutoScript.run(runner, 'event2') is False

    runner.config.task_call.assert_called_once_with('Restart')
    runner.save_error_log.assert_called_once_with()


def test_unexpected_error_ends_alas_without_keep_running(monkeypatch):
    monkeypatch.setattr(alas, 'handle_notify', Mock())
    runner = Runner(keep_running=False, error=ValueError('something unexpected'))

    with pytest.raises(SystemExit):
        AzurLaneAutoScript.run(runner, 'event2')

    runner.config.task_call.assert_not_called()


def test_scheduler_does_not_wait_without_a_pause(monkeypatch):
    sleep = Mock()
    monkeypatch.setattr(alas.time, 'sleep', sleep)
    write_state = Mock()
    monkeypatch.setattr(alas, 'write_scheduler_state', write_state)

    assert AzurLaneAutoScript.wait_while_paused(make_scheduler(paused_checks=0)) is False

    sleep.assert_not_called()
    write_state.assert_not_called()


def test_scheduler_waits_until_the_pause_is_over(monkeypatch):
    sleep = Mock()
    monkeypatch.setattr(alas.time, 'sleep', sleep)
    write_state = Mock()
    monkeypatch.setattr(alas, 'write_scheduler_state', write_state)

    # Found at the first look, and on 3 more looks while waiting
    assert AzurLaneAutoScript.wait_while_paused(make_scheduler(paused_checks=4)) is True

    assert sleep.call_count == 3
    # Tells the GUI it holds, then that it goes on
    assert write_state.call_args_list == [call('alas', paused=True), call('alas')]


def test_waiting_for_the_next_task_ends_for_a_pause(monkeypatch):
    sleep = Mock()
    monkeypatch.setattr(alas.time, 'sleep', sleep)
    config = SimpleNamespace(start_watching=Mock(), should_reload=lambda: False, pause_requested=True)
    scheduler = SimpleNamespace(config=config, config_name='alas', stop_event=None)

    # Not after 3 hours of waiting for oil
    assert AzurLaneAutoScript.wait_until(scheduler, datetime.now() + timedelta(hours=3)) is False
    assert sleep.call_count == 1


def test_next_task_holds_while_paused_first(monkeypatch):
    import module.base.resource
    monkeypatch.setattr(module.base.resource, 'release_resources', Mock())
    calls = []
    task = SimpleNamespace(command='Event2', next_run=datetime.now() - timedelta(minutes=1))

    def get_next():
        calls.append('get_next')
        return task

    config = SimpleNamespace(get_next=get_next, bind=Mock(), task=None)
    scheduler = SimpleNamespace(config=config, wait_while_paused=lambda: calls.append('hold'),
                                wait_while_logged_out=lambda: calls.append('logged out'))

    assert AzurLaneAutoScript.get_next_task(scheduler) == 'Event2'
    assert calls == ['hold', 'logged out', 'get_next']


def test_logged_out_during_a_task_waits_instead_of_restarting_at_once():
    from module.exception import GameLoggedOutError
    runner = Runner(keep_running=True, error=GameLoggedOutError('Login page during a task'))

    assert AzurLaneAutoScript.run(runner, 'event2') is False

    runner.handle_logged_out.assert_called_once_with()
    runner.config.task_call.assert_not_called()


def test_stuck_at_the_login_page_waits_too():
    from module.exception import GameStuckError
    runner = Runner(keep_running=True, error=GameStuckError('Wait too long'))
    runner.logged_out_on_screen = Mock(return_value=True)

    assert AzurLaneAutoScript.run(runner, 'event2') is False

    runner.handle_logged_out.assert_called_once_with()
    runner.config.task_call.assert_not_called()


class Waiting(SimpleNamespace):
    """
    A scheduler whose next task `Event` is due after the given wait.
    """

    def __init__(self, wait, method='close_emulator', emulator_stops=True, device=True, playing=False):
        tasks = [SimpleNamespace(command='Event', next_run=datetime.now() + wait),
                 SimpleNamespace(command='Restart', next_run=datetime.now() - timedelta(seconds=1))]
        config = SimpleNamespace(get_next=lambda: tasks.pop(0), bind=Mock(), task=None, task_call=Mock(),
                                 Optimization_WhenTaskQueueEmpty=method, pause_requested=False)
        super().__init__(config=config, is_first_task=False,
                         wait_while_paused=Mock(), wait_while_logged_out=Mock(),
                         wait_until=Mock(return_value=True), run=Mock())
        if device:
            self.device = SimpleNamespace(app_stop=Mock(), release_during_wait=Mock(),
                                          emulator_stop=Mock(return_value=emulator_stops),
                                          user_playing=Mock(return_value=playing), user_input_stop=Mock())
        self.close_emulator = lambda: AzurLaneAutoScript.close_emulator(self)


@pytest.fixture
def waiting(monkeypatch):
    import module.base.resource
    monkeypatch.setattr(module.base.resource, 'release_resources', Mock())

    # Keep the config of the test, a device deleted is gone
    def delete(obj, name):
        if name == 'device':
            obj.__dict__.pop('device', None)

    monkeypatch.setattr(alas, 'del_cached_property', delete)
    return Waiting


def test_long_wait_closes_the_game_and_the_emulator(waiting):
    scheduler = waiting(timedelta(hours=1))
    device = scheduler.device

    assert AzurLaneAutoScript.get_next_task(scheduler) == 'Restart'

    device.app_stop.assert_called_once_with()
    device.emulator_stop.assert_called_once_with()
    # The device of the next task starts the emulator again
    assert 'device' not in scheduler.__dict__
    scheduler.config.task_call.assert_called_once_with('Restart')


def test_short_wait_is_spent_at_the_main_page(waiting):
    scheduler = waiting(timedelta(minutes=5))

    # Not worth a restart of the game
    assert AzurLaneAutoScript.get_next_task(scheduler) == 'Event'

    scheduler.run.assert_called_once_with('goto_main')
    scheduler.device.app_stop.assert_not_called()
    scheduler.device.emulator_stop.assert_not_called()
    scheduler.config.task_call.assert_not_called()


@pytest.mark.parametrize('wait', [timedelta(minutes=5), timedelta(hours=1)])
def test_closed_emulator_is_not_started_just_to_close_it(waiting, wait):
    # Waiting again after the config changed, or the first wait since the scheduler started:
    # there is no device, and creating one would start the emulator
    scheduler = waiting(wait, device=False)

    assert AzurLaneAutoScript.get_next_task(scheduler) == 'Restart'

    assert 'device' not in scheduler.__dict__
    scheduler.run.assert_not_called()
    scheduler.config.task_call.assert_called_once_with('Restart')


def test_emulator_that_can_not_be_stopped_stays_with_the_game_closed(waiting):
    # Platforms other than Windows and macOS
    scheduler = waiting(timedelta(hours=1), emulator_stops=None)

    assert AzurLaneAutoScript.get_next_task(scheduler) == 'Restart'

    scheduler.device.app_stop.assert_called_once_with()
    scheduler.device.emulator_stop.assert_called_once_with()
    assert 'device' in scheduler.__dict__
    scheduler.config.task_call.assert_called_once_with('Restart')


def test_close_game_never_stops_the_emulator(waiting):
    scheduler = waiting(timedelta(hours=1), method='close_game')

    assert AzurLaneAutoScript.get_next_task(scheduler) == 'Restart'

    scheduler.device.app_stop.assert_called_once_with()
    scheduler.device.emulator_stop.assert_not_called()


def test_every_task_is_in_the_scheduler_priority():
    import re
    import yaml
    from module.config.config_manual import ManualConfig

    listed = set(re.findall(r'[A-Za-z0-9]+', ManualConfig.SCHEDULER_PRIORITY))
    with open('module/config/argument/task.yaml', encoding='utf-8') as f:
        groups = yaml.safe_load(f)
    # Tasks left out of the priority are never run by the scheduler
    missing = []
    for name, group in groups.items():
        if name in ['Alas', 'Tool'] or not isinstance(group, dict):
            continue
        for task, settings in (group.get('tasks') or {}).items():
            if task.endswith('General'):
                continue
            if 'Scheduler' in (settings or []) and task not in listed:
                missing.append(task)
    assert missing == []


@pytest.mark.parametrize('method', ['close_emulator', 'close_game', 'goto_main'])
def test_wait_begun_while_someone_plays_leaves_the_game_alone(waiting, method):
    # "don't fight me when i'm trying to click around"
    scheduler = waiting(timedelta(hours=1), method=method, playing=True)

    assert AzurLaneAutoScript.get_next_task(scheduler) == 'Event'

    scheduler.device.app_stop.assert_not_called()
    scheduler.device.emulator_stop.assert_not_called()
    scheduler.run.assert_not_called()
    # Nothing was closed, nothing to start again
    scheduler.config.task_call.assert_not_called()


def test_closing_the_emulator_stops_following_its_touches(waiting):
    scheduler = waiting(timedelta(hours=1))
    device = scheduler.device

    AzurLaneAutoScript.get_next_task(scheduler)

    device.user_input_stop.assert_called_once_with()


def test_someone_playing_ends_the_task_without_failure():
    from module.exception import UserPlaying
    runner = Runner(keep_running=True, error=UserPlaying('Someone touched the emulator 2s ago'))

    assert AzurLaneAutoScript.run(runner, 'event2') is None

    # No restart of the game, no error log
    runner.config.task_call.assert_not_called()
    runner.save_error_log.assert_not_called()


def test_scheduler_waits_until_nobody_plays(monkeypatch):
    playing = iter([True, True, False])
    sleeps = []
    monkeypatch.setattr(alas.time, 'sleep', lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(alas, 'write_scheduler_state', Mock())
    scheduler = SimpleNamespace(config_name='alas', stop_event=None,
                                device=SimpleNamespace(user_input_wait=30, user_playing=lambda: next(playing)))

    AzurLaneAutoScript.wait_while_user_plays(scheduler)

    assert sleeps == [1, 1]
