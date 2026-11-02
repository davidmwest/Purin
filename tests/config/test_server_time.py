"""Daily reset dates must use the local offset at the reset, across DST changes."""
import os
import time
from datetime import datetime, timezone

import pytest

from module.config import utils


@pytest.fixture
def local_timezone(monkeypatch):
    if not hasattr(time, 'tzset'):
        pytest.skip('Changing the process timezone requires time.tzset')
    original = os.environ.get('TZ')
    monkeypatch.setenv('TZ', 'America/Los_Angeles')
    time.tzset()
    yield
    if original is None:
        os.environ.pop('TZ', None)
    else:
        os.environ['TZ'] = original
    time.tzset()


def freeze(monkeypatch, instant, server='en'):
    instant = datetime.fromisoformat(instant).replace(tzinfo=timezone.utc)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls.fromtimestamp(instant.timestamp(), tz)

    monkeypatch.setattr(utils, 'datetime', FrozenDateTime)
    monkeypatch.setattr(utils.server_, 'server', server)


@pytest.mark.parametrize('instant,next_reset,last_reset', [
    # The daily Hard job finished before fall-back; its next reset is 23:00 PST.
    ('2026-11-01T07:57:51', '2026-11-01T23:00:00', '2026-11-01T00:00:00'),
    # Looking back after fall-back must retain the earlier reset's PDT offset.
    ('2026-11-01T10:30:00', '2026-11-01T23:00:00', '2026-11-01T00:00:00'),
    # EN stays UTC-7 when the local clock springs forward, too.
    ('2026-03-08T08:57:51', '2026-03-09T00:00:00', '2026-03-07T23:00:00'),
    ('2026-03-08T10:30:00', '2026-03-09T00:00:00', '2026-03-07T23:00:00'),
    # Ordinary winter and summer days still use the same server reset.
    ('2026-11-02T08:30:00', '2026-11-02T23:00:00', '2026-11-01T23:00:00'),
    ('2026-07-01T08:30:00', '2026-07-02T00:00:00', '2026-07-01T00:00:00'),
])
def test_daily_reset_across_local_clock_changes(local_timezone, monkeypatch,
                                                instant, next_reset, last_reset):
    freeze(monkeypatch, instant)
    assert utils.get_server_next_update('00:00') == datetime.fromisoformat(next_reset)
    assert utils.get_server_last_update('00:00') == datetime.fromisoformat(last_reset)


@pytest.mark.parametrize('triggers', ['00:00, 12:00, 18:00', ['00:00', '12:00', '18:00']])
def test_multiple_triggers_compare_in_server_time(local_timezone, monkeypatch, triggers):
    freeze(monkeypatch, '2026-11-01T07:57:51')
    assert utils.get_server_next_update(triggers) == datetime(2026, 11, 1, 11)
    assert utils.get_server_last_update(triggers) == datetime(2026, 11, 1, 0)


@pytest.mark.parametrize('microseconds,next_reset,last_reset', [
    ('', '2026-11-01T23:00:00', '2026-11-01T00:00:00'),
    ('.000001', '2026-11-02T23:00:00', '2026-11-01T23:00:00'),
])
def test_exact_trigger_keeps_existing_boundary_semantics(local_timezone, monkeypatch,
                                                        microseconds, next_reset, last_reset):
    freeze(monkeypatch, '2026-11-02T07:00:00' + microseconds)
    assert utils.get_server_next_update('00:00') == datetime.fromisoformat(next_reset)
    assert utils.get_server_last_update('00:00') == datetime.fromisoformat(last_reset)


@pytest.mark.parametrize('server,instant,next_reset,last_reset', [
    ('cn', '2026-11-01T07:57:51', '2026-11-01T08:00:00', '2026-10-31T09:00:00'),
    ('jp', '2026-11-01T07:57:51', '2026-11-01T07:00:00', '2026-10-31T08:00:00'),
])
def test_other_servers_keep_their_own_calendar(local_timezone, monkeypatch, server,
                                             instant, next_reset, last_reset):
    freeze(monkeypatch, instant, server)
    assert utils.get_server_next_update('00:00') == datetime.fromisoformat(next_reset)
    assert utils.get_server_last_update('00:00') == datetime.fromisoformat(last_reset)
