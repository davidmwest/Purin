from types import SimpleNamespace
from unittest.mock import Mock

from module.base.timer import Timer
from module.handler.login import LoginHandler

FLOOD = '10-20 02:32:51.120  2519  2602 E AliNetworkDiagnosis: send_packets error: Unknown error -1'
OTHER = '10-20 02:32:51.121  1203  1350 I ActivityManager: Start proc'


def make_handler(monkeypatch, flood):
    """
    Args:
        flood (int): Lines of the network diagnosis among the last 2000 of logcat
    """
    logcat = '\n'.join([OTHER] * (LoginHandler.LOG_FLOOD_LINES - flood) + [FLOOD] * flood)
    handler = LoginHandler.__new__(LoginHandler)
    handler.device = SimpleNamespace(adb_shell=Mock(side_effect=lambda cmd: '' if '-c' in cmd else logcat))
    handler.app_stop = Mock()
    handler.app_start = Mock()
    monkeypatch.setattr(LoginHandler, '_log_flood_restart', Timer(3600))
    return handler


def test_flood_starts_the_game_again(monkeypatch):
    # Seen on the main account at 4 CPU / 12 GB: 8 seconds under 12 FPS in 10 minutes on the main page
    handler = make_handler(monkeypatch, flood=2000)

    assert handler.app_restart_on_log_flood() is True

    handler.device.adb_shell.assert_any_call(['logcat', '-c'])
    handler.app_stop.assert_called_once_with()
    handler.app_start.assert_called_once_with()


def test_healthy_start_is_left_alone(monkeypatch):
    # A start logs single digits of it
    handler = make_handler(monkeypatch, flood=7)

    assert handler.app_restart_on_log_flood() is False

    handler.app_stop.assert_not_called()


def test_restart_once_an_hour_at_most(monkeypatch):
    handler = make_handler(monkeypatch, flood=2000)

    assert handler.app_restart_on_log_flood() is True
    assert handler.app_restart_on_log_flood() is False

    assert handler.app_stop.call_count == 1
