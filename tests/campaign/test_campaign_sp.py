import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from module.campaign.campaign_status import OilReadError
from module.campaign.run import CampaignRun
from module.config.config import TaskEnd
from module.event import campaign_sp as sp
from module.exception import ScriptError


PERIOD = datetime(2026, 10, 23, 4)


def controller(monkeypatch, tmp_path, account='alas', event=sp.DAILY_SP_EVENT):
    owner = sp.CampaignSP.__new__(sp.CampaignSP)
    owner.config = SimpleNamespace(
        config_name=account, Campaign_Event=event, Campaign_Name='sp',
        Scheduler_ServerUpdate='04:00', Scheduler_Enable=True,
        task_delay=Mock(), task_stop=Mock(side_effect=TaskEnd))
    owner.device = SimpleNamespace(click=Mock(), screenshot=Mock())
    owner._daily_sp_folder = tmp_path
    monkeypatch.setattr(sp, 'stage_names', Mock(return_value=['sp']))
    monkeypatch.setattr(sp, 'get_server_last_update', Mock(return_value=PERIOD))
    return owner


def standard_run(monkeypatch, owner, *, count=1, lost=False, error=None):
    def run(**kwargs):
        owner.run_count = count
        owner.campaign = SimpleNamespace(run_lost=lost)
        if error:
            raise error
    base = Mock(side_effect=run)
    monkeypatch.setattr(sp.EventBase, 'run', base)
    return base


def current_record(tmp_path, period=PERIOD):
    return sp.load_daily_sp(tmp_path)['periods'][period.isoformat()]


def test_main_reserves_before_standard_navigation_and_marks_verified_success(monkeypatch, tmp_path):
    owner = controller(monkeypatch, tmp_path)
    sentinels = {'alas_EventStars.json': '{"sp":"complete"}',
                 'alas-event-extra.json': '{"combat_attempted":true}',
                 'alas-sp-observation.json': '{"inspected":true}'}
    for name, contents in sentinels.items():
        (tmp_path / name).write_text(contents)

    def navigate(**kwargs):
        assert kwargs == dict(name='sp', folder=sp.DAILY_SP_EVENT, total=1)
        assert current_record(tmp_path)['status'] == 'reserved'
        assert current_record(tmp_path)['attempts'] == 1
        owner.run_count = 1
        owner.campaign = SimpleNamespace(run_lost=False)
    base = Mock(side_effect=navigate)
    monkeypatch.setattr(sp.EventBase, 'run', base)
    owner.run()
    record = current_record(tmp_path)
    assert record['status'] == 'complete' and record['run_lost'] is False
    assert record['run_count'] == 1
    base.assert_called_once()
    sp.get_server_last_update.assert_called_once_with('04:00')
    owner.config.task_delay.assert_called_once_with(server_update=True)
    assert owner.config.Scheduler_Enable is True
    assert {name: (tmp_path / name).read_text() for name in sentinels} == sentinels


@pytest.mark.parametrize('first_error', [None, RuntimeError('navigation failed'), TaskEnd()])
def test_restart_never_replays_any_spent_period(monkeypatch, tmp_path, first_error):
    owner = controller(monkeypatch, tmp_path)
    base = standard_run(monkeypatch, owner, error=first_error)
    if isinstance(first_error, RuntimeError):
        with pytest.raises(RuntimeError, match='navigation failed'):
            owner.run()
    else:
        owner.run()
    before = (tmp_path / 'alas-daily-sp.json').read_text()
    restarted = controller(monkeypatch, tmp_path)
    base.reset_mock()
    restarted.run()
    base.assert_not_called()
    restarted.device.click.assert_not_called()
    restarted.device.screenshot.assert_not_called()
    restarted.config.task_delay.assert_called_once_with(server_update=True)
    restarted.config.task_stop.assert_not_called()
    assert (tmp_path / 'alas-daily-sp.json').read_text() == before


def test_configured_next_server_period_allows_exactly_one_new_invocation(monkeypatch, tmp_path):
    owner = controller(monkeypatch, tmp_path)
    base = standard_run(monkeypatch, owner)
    owner.run()
    next_period = PERIOD + timedelta(days=1)
    sp.get_server_last_update.return_value = next_period
    owner.run()
    owner.run()
    assert base.call_count == 2
    assert set(sp.load_daily_sp(tmp_path)['periods']) == {PERIOD.isoformat(), next_period.isoformat()}
    assert current_record(tmp_path, next_period)['status'] == 'complete'
    assert all(call.kwargs == {'server_update': True} for call in owner.config.task_delay.call_args_list)


@pytest.mark.parametrize('lost', [True, None, 0])
def test_loss_or_unknown_loss_proof_is_spent_and_never_complete(monkeypatch, tmp_path, lost):
    owner = controller(monkeypatch, tmp_path)
    base = standard_run(monkeypatch, owner, lost=lost)
    owner.run()
    assert current_record(tmp_path)['status'] == 'reserved'
    owner.run()
    base.assert_called_once()


def test_end_on_loss_disable_and_taskend_are_preserved(monkeypatch, tmp_path):
    owner = controller(monkeypatch, tmp_path)

    def lost_run(**kwargs):
        owner.run_count = 1
        owner.campaign = SimpleNamespace(run_lost=True)
        owner.config.Scheduler_Enable = False
        raise TaskEnd
    base = Mock(side_effect=lost_run)
    monkeypatch.setattr(sp.EventBase, 'run', base)
    owner.run()
    assert owner.config.Scheduler_Enable is False
    assert current_record(tmp_path)['status'] == 'reserved'
    owner.config.task_delay.assert_called_once_with(server_update=True)


def test_task_switch_before_first_run_stops_without_claiming_success(monkeypatch, tmp_path):
    owner = controller(monkeypatch, tmp_path)
    monkeypatch.setattr(sp.EventBase, 'run', Mock(side_effect=TaskEnd))
    with pytest.raises(TaskEnd):
        owner.run()
    assert owner.run_count == 0
    assert current_record(tmp_path)['status'] == 'reserved'
    owner.config.task_stop.assert_called_once()
    owner.config.task_delay.assert_not_called()


@pytest.mark.parametrize('entered', [False, True])
def test_unreadable_oil_refunds_only_verified_pre_entry_daily_attempt(monkeypatch, tmp_path, entered):
    owner = controller(monkeypatch, tmp_path)
    executions = []

    def load(current, *args, **kwargs):
        current.campaign = SimpleNamespace(run=Mock(), run_lost=False)

    def execute(current, *args, **kwargs):
        assert current_record(tmp_path)['status'] == 'reserved'
        current.load_campaign('sp', sp.DAILY_SP_EVENT)
        executions.append(current)
        if len(executions) == 1:
            if entered:
                current.campaign.run()
            raise OilReadError('oil is hidden')
        current.campaign.run()
        current.run_count = 1

    monkeypatch.setattr(sp.EventBase, 'load_campaign', load)
    monkeypatch.setattr(CampaignRun, '_run', execute)
    owner.run()

    assert owner.oil_unverified is True
    assert owner._daily_sp_attempt_started is entered
    assert owner.campaign.run_lost is False
    owner.config.task_delay.assert_called_once_with(minute=1)
    owner.config.task_stop.assert_not_called()
    assert owner.config.Scheduler_Enable is True
    if entered:
        assert current_record(tmp_path)['status'] == 'reserved'
    else:
        assert sp.load_daily_sp(tmp_path)['periods'] == {}

    restarted = controller(monkeypatch, tmp_path)
    restarted.run()
    assert len(executions) == (1 if entered else 2)
    restarted.config.task_delay.assert_called_once_with(server_update=True)
    assert current_record(tmp_path)['status'] == ('reserved' if entered else 'complete')


def test_failed_oil_preflight_refund_write_keeps_durable_daily_reservation(monkeypatch, tmp_path):
    owner = controller(monkeypatch, tmp_path)

    def execute(current, *args, **kwargs):
        raise OilReadError('oil is hidden')

    save = sp.save_daily_sp
    calls = []

    def save_once(state, folder):
        calls.append(True)
        if len(calls) > 1:
            raise OSError('refund write failed')
        save(state, folder)

    monkeypatch.setattr(CampaignRun, '_run', execute)
    monkeypatch.setattr(sp, 'save_daily_sp', save_once)
    with pytest.raises(OSError, match='refund write failed'):
        owner.run()
    assert current_record(tmp_path)['status'] == 'reserved'
    owner.config.task_delay.assert_called_once_with(minute=1)


@pytest.mark.parametrize('account,event', [('f2p', sp.DAILY_SP_EVENT), ('alas', 'event_other'),
                                          ('other', sp.DAILY_SP_EVENT)])
def test_other_account_or_event_keeps_standard_behavior_and_ignores_guard_journal(
        monkeypatch, tmp_path, account, event):
    owner = controller(monkeypatch, tmp_path, account, event)
    (tmp_path / 'alas-daily-sp.json').write_text('{corrupt')
    base = standard_run(monkeypatch, owner)
    owner.run()
    owner.run()
    assert base.call_count == 2
    sp.get_server_last_update.assert_not_called()
    assert (tmp_path / 'alas-daily-sp.json').read_text() == '{corrupt'


def test_event_without_sp_preserves_disable_and_stop_before_reservation(monkeypatch, tmp_path):
    owner = controller(monkeypatch, tmp_path)
    sp.stage_names.return_value = []
    base = standard_run(monkeypatch, owner)
    with pytest.raises(TaskEnd):
        owner.run()
    assert owner.config.Scheduler_Enable is False
    base.assert_not_called()
    assert not (tmp_path / 'alas-daily-sp.json').exists()


@pytest.mark.parametrize('invalid', ['{bad', '[]', '{"version":1}',
    '{"version":true,"account":"alas","event":"event_20261022_cn","periods":{}}',
    '{"version":1,"account":"f2p","event":"event_20261022_cn","periods":{}}',
    '{"version":1,"account":"alas","event":"event_other","periods":{}}'])
def test_corrupt_or_wrong_identity_budget_fails_closed_before_navigation(monkeypatch, tmp_path, invalid):
    owner = controller(monkeypatch, tmp_path)
    path = tmp_path / 'alas-daily-sp.json'
    path.write_text(invalid)
    base = standard_run(monkeypatch, owner)
    with pytest.raises(ScriptError, match='daily SP budget'):
        owner.run()
    base.assert_not_called()
    owner.device.click.assert_not_called()
    owner.config.task_delay.assert_not_called()
    assert path.read_text() == invalid


@pytest.mark.parametrize('mutate', [lambda record: record.update(attempts=0),
    lambda record: record.update(attempts=True), lambda record: record.update(status='retry'),
    lambda record: record.update(period='wrong'), lambda record: record.update(reserved_at=None),
    lambda record: record.update(status='complete', run_count=1, run_lost=True, completed_at=PERIOD.isoformat())])
def test_malformed_spent_record_never_falls_back_to_fresh_attempt(monkeypatch, tmp_path, mutate):
    owner = controller(monkeypatch, tmp_path)
    record = dict(period=PERIOD.isoformat(), status='reserved', attempts=1, reserved_at=PERIOD.isoformat())
    mutate(record)
    state = dict(version=1, account='alas', event=sp.DAILY_SP_EVENT, periods={PERIOD.isoformat(): record})
    sp.save_daily_sp(state, tmp_path)
    base = standard_run(monkeypatch, owner)
    with pytest.raises(ScriptError, match='Invalid daily SP budget'):
        owner.run()
    base.assert_not_called()


def test_failed_reservation_write_prevents_navigation(monkeypatch, tmp_path):
    owner = controller(monkeypatch, tmp_path)
    base = standard_run(monkeypatch, owner)
    monkeypatch.setattr(sp, 'save_daily_sp', Mock(side_effect=OSError('disk full')))
    with pytest.raises(OSError, match='disk full'):
        owner.run()
    base.assert_not_called()


def test_failed_atomic_replace_preserves_prior_budget_and_cleans_temporary_file(monkeypatch, tmp_path):
    state = sp.load_daily_sp(tmp_path)
    sp.save_daily_sp(state, tmp_path)
    original = (tmp_path / 'alas-daily-sp.json').read_text()
    monkeypatch.setattr(sp.os, 'replace', Mock(side_effect=OSError('replace failed')))
    with pytest.raises(OSError, match='replace failed'):
        sp.save_daily_sp(state, tmp_path)
    assert (tmp_path / 'alas-daily-sp.json').read_text() == original
    assert list(tmp_path.iterdir()) == [tmp_path / 'alas-daily-sp.json']
