import json

import pytest
from filelock import FileLock

from module.exception import ScriptError
from module.war_archives.state import ArchiveState


def test_daily_budget_survives_reload_and_is_shared_between_jobs(tmp_path):
    period = '2026-10-23'
    state = ArchiveState('f2p', str(tmp_path))
    for i in range(4):
        assert state.reserve(period, f'story/{i}', 800, 300)
        state = ArchiveState('f2p', str(tmp_path))
    assert state.used(period) == 4
    assert not state.reserve(period, 'prize', 4999, 280)
    assert state.reserve(period, 'prize', 5000, 280)
    assert state.used(period) == 5
    assert state.reserve('2026-10-24', 'story/5', 500, 275)


def test_unknown_and_insufficient_resources_never_reserve(tmp_path):
    state = ArchiveState('alas', str(tmp_path))
    for oil, keys in ((499, 300), (500, 4), (None, 300), (500, None), (True, 300)):
        assert not state.reserve('day', 'stage', oil, keys)
    assert not tmp_path.joinpath('alas.json').exists()


def test_uncertain_entry_stays_spent_and_only_explicit_preentry_refund_counts(tmp_path):
    state = ArchiveState('alas', str(tmp_path))
    assert state.reserve('day', 'a', 1000, 10)
    state.finish('day', 'a', 'uncertain', reason='crash_after_start')
    assert state.used('day') == 1
    assert state.reserve('day', 'b', 1000, 5)
    state.finish('day', 'b', 'refunded', reason='verified_preentry_cancel')
    assert state.used('day') == 1


def test_corrupt_journal_fails_closed(tmp_path):
    path = tmp_path / 'alas.json'
    for data in ('{', '[]', json.dumps({'version':1, 'periods': {'day': {'entries':[{}]}}, 'goals':{}})):
        path.write_text(data)
        with pytest.raises(ScriptError):
            ArchiveState('alas', str(tmp_path))


def test_failed_write_does_not_authorize_entry(tmp_path, monkeypatch):
    state = ArchiveState('alas', str(tmp_path))
    monkeypatch.setattr(state, 'save', lambda: (_ for _ in ()).throw(OSError('disk full')))
    with pytest.raises(OSError):
        state.reserve('day', 'a', 1000, 300)
    assert state.used('day') == 0


def test_preloaded_jobs_reload_shared_budget_before_reserving_or_finishing(tmp_path):
    stories = ArchiveState('alas', str(tmp_path))
    prizes = ArchiveState('alas', str(tmp_path))
    for index in range(4):
        current = stories if index % 2 == 0 else prizes
        assert current.reserve('day', f'goal/{index}', 500, 300 - 5 * index)
    assert stories.used('day') == prizes.used('day') == 4
    assert not prizes.reserve('day', 'extra', 4999, 280)
    stories.finish('day', 'goal/0', 'won', boss_won=True)
    prizes.finish('day', 'goal/1', 'lost')
    assert [entry['status'] for entry in stories.entries('day')] == ['won', 'lost', 'reserved', 'reserved']


def test_crashed_reserved_and_uncertain_lost_entries_all_consume_daily_budget(tmp_path):
    state = ArchiveState('f2p', str(tmp_path))
    for index, status in enumerate(('reserved', 'uncertain', 'lost', 'won')):
        assert state.reserve('day', str(index), 500, 300 - 5 * index)
        if status != 'reserved':
            state.finish('day', str(index), status)
        state = ArchiveState('f2p', str(tmp_path))
    assert state.used('day') == 4
    assert not state.reserve('day', 'crash-retry', 4999, 280)
    assert state.reserve('day', 'extra', 5000, 280)
    assert state.used('day') == 5


def test_shared_goal_updates_merge_and_copies_cannot_mutate_journal(tmp_path):
    first = ArchiveState('alas', str(tmp_path))
    second = ArchiveState('alas', str(tmp_path))
    first.update('archive/stories', observed=True)
    second.update('archive/stories', complete=True)
    first.update('archive/prize/1', claimed=False)
    assert second.record('archive/stories') == dict(observed=True, complete=True)
    copy = first.record('archive/stories')
    copy['complete'] = False
    assert first.record('archive/stories')['complete'] is True
    assert second.record('archive/prize/1') == {'claimed': False}


def test_shared_file_lock_prevents_permission_when_another_writer_owns_it(tmp_path):
    state = ArchiveState('alas', str(tmp_path))
    with FileLock(state.path + '.lock', timeout=0):
        with pytest.raises(ScriptError, match='already being updated'):
            state.reserve('day', 'stage', 500, 300)
    assert state.used('day') == 0


def test_account_identity_cannot_reuse_another_accounts_goal_or_budget(tmp_path):
    main = ArchiveState('alas', str(tmp_path))
    main.update('archive/stories', complete=True)
    (tmp_path / 'f2p.json').write_text((tmp_path / 'alas.json').read_text())
    with pytest.raises(ScriptError, match='Invalid archive budget'):
        ArchiveState('f2p', str(tmp_path))


@pytest.mark.parametrize('oil,keys,cost,daily,extra', [
    (500, 300, 1, 4, 5000), (500, 301, 5, 4, 5000),
    (500, True, 5, 4, 5000), (500, 300, True, 4, 5000),
    (500, 300, 5, 5, 5000), (500, 300, 5, -1, 5000),
    (500, 300, 5, 4, -1), (500, 300, 5, 4, True),
])
def test_units_and_policy_require_exact_valid_integer_values(tmp_path, oil, keys, cost, daily, extra):
    state = ArchiveState('alas', str(tmp_path))
    assert not state.reserve('day', 'goal', oil, keys, key_cost=cost,
                             daily_tickets=daily, extra_reserve=extra)
    assert state.used('day') == 0


def test_extra_oil_floor_is_max_500_and_reserve_not_their_sum(tmp_path):
    state = ArchiveState('alas', str(tmp_path))
    assert state.reserve('day', 'extra', 5000, 300, daily_tickets=0, extra_reserve=5000)
    assert not state.reserve('day', 'extra', 499, 295, daily_tickets=0, extra_reserve=0)
    assert state.reserve('day', 'extra', 500, 295, daily_tickets=0, extra_reserve=0)
    assert state.entries('day')[0]['raw_keys'] == 5


def test_failed_result_and_goal_writes_preserve_previous_durable_state(tmp_path, monkeypatch):
    state = ArchiveState('alas', str(tmp_path))
    assert state.reserve('day', 'a', 1000, 300)
    monkeypatch.setattr(state, 'save', lambda: (_ for _ in ()).throw(OSError('disk full')))
    with pytest.raises(OSError):
        state.finish('day', 'a', 'won')
    assert state.entries('day')[0]['status'] == 'reserved'
    with pytest.raises(OSError):
        state.update('a', complete=True)
    assert state.record('a') == {}


@pytest.mark.parametrize('entry', [
    {'status': 'reserved', 'raw_keys': 5},
    {'goal': '', 'status': 'reserved', 'raw_keys': 5},
    {'goal': 'a', 'status': 'reserved', 'raw_keys': True},
    {'goal': 'a', 'status': 'reserved', 'raw_keys': 1},
    {'goal': 'a', 'status': 'complete', 'raw_keys': 5},
])
def test_corrupted_entry_identity_status_and_units_fail_closed(tmp_path, entry):
    (tmp_path / 'alas.json').write_text(json.dumps(dict(
        version=1, account='alas', periods={'day': {'entries': [entry]}}, goals={})))
    with pytest.raises(ScriptError, match='Invalid archive reservation'):
        ArchiveState('alas', str(tmp_path))


def test_corrupt_journal_after_loading_cannot_be_overwritten_by_stale_instance(tmp_path):
    state = ArchiveState('alas', str(tmp_path))
    (tmp_path / 'alas.json').write_text('{')
    with pytest.raises(ScriptError, match='Cannot read archive budget'):
        state.reserve('day', 'a', 1000, 300)
    assert (tmp_path / 'alas.json').read_text() == '{'


def test_finish_requires_a_matching_reserved_goal_and_cannot_refinish(tmp_path):
    state = ArchiveState('alas', str(tmp_path))
    assert state.reserve('day', 'a', 1000, 300)
    with pytest.raises(ScriptError, match='no reserved entry'):
        state.finish('day', 'b', 'won')
    state.finish('day', 'a', 'won')
    with pytest.raises(ScriptError, match='no reserved entry'):
        state.finish('day', 'a', 'refunded')
    assert state.used('day') == 1
