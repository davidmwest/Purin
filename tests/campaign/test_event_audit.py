from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from module.campaign import event_completion as completion
from module.campaign import event_strict_maid, event_strict_maid_milestones
from module.campaign.event_completion import EventCompletion, load_reward_observations, save_reward_observations
from module.exception import GameNotRunningError, GamePageUnknownError


EVENT = 'event_20261022_cn'
METHODS = (
    'observe_event_main', 'observe_event_hub', 'observe_event_featured',
    'observe_event_rewards_panel', 'observe_event_beckoning', 'observe_event_foggy_supply',
    'observe_event_rewards', 'observe_event_supplies',
)


class AuditConfig(SimpleNamespace):
    @contextmanager
    def multi_set(self):
        yield
        self.saved = True


@pytest.fixture
def audit(monkeypatch, tmp_path):
    helper = EventCompletion.__new__(EventCompletion)
    helper.config = AuditConfig(config_name='alas', EventCompletion_Audit=True, saved=False)
    helper.device = SimpleNamespace(app_is_running=Mock(return_value=True))
    helper._resume_event_mission_receipt = Mock(return_value=True)
    for name in METHODS:
        setattr(helper, name, Mock(return_value=dict(status='observed', returned_main=True, diagnostics=[])))
    strict = Mock()
    strict.return_value.run.return_value = dict(status='observed', returned_main=True, diagnostics=[])
    milestones = Mock()
    milestones.return_value.observe.return_value = dict(status='observed', returned_main=True, diagnostics=[])
    monkeypatch.setattr(event_strict_maid, 'StrictMaidObserver', strict)
    monkeypatch.setattr(event_strict_maid_milestones, 'StrictMaidMilestones', milestones)
    monkeypatch.setattr(completion, 'EVENT_AUDIT_DIR', str(tmp_path / 'audits'))
    monkeypatch.setattr(completion, 'save_event_diagnostic', Mock(return_value=None))
    return helper, strict, milestones


def test_identity_and_flag_are_durable_before_first_control_with_separate_ledgers(audit, tmp_path):
    helper, strict, milestones = audit
    ordinary = tmp_path / 'ordinary'
    save_reward_observations('alas', {EVENT: dict(status='complete', claims=2)}, folder=str(ordinary))
    original = (ordinary / 'alas-event-rewards.json').read_bytes()

    def first(event, folder):
        assert event == EVENT
        assert helper.config.EventCompletion_Audit is False
        assert helper.config.saved is True
        state = load_reward_observations('alas', folder=folder)
        assert state['_audit']['request']
        assert state['_audit']['status'] == 'observing'
        assert state['_audit']['current'] == 'main'
        # Model an existing observer adding its own durable before-input budget.
        state[EVENT + ':main'] = dict(status='observed')
        save_reward_observations('alas', state, folder=folder)
        return dict(status='observed', diagnostics=[])

    helper.observe_event_main.side_effect = first
    result = helper.audit_event(EVENT)

    assert result['status'] == 'complete'
    assert (ordinary / 'alas-event-rewards.json').read_bytes() == original
    state = load_reward_observations('alas', folder=result['folder'])
    assert state[EVENT + ':main']['status'] == 'observed'
    assert state['_audit']['status'] == 'complete'
    for method in METHODS:
        getattr(helper, method).assert_called_once_with(EVENT, folder=result['folder'])
    strict.return_value.run.assert_called_once_with(EVENT, folder=result['folder'])
    milestones.return_value.observe.assert_called_once_with(EVENT, folder=result['folder'])
    assert list(result['observations'])[-1] == 'strict_maid_milestones'


def test_manual_second_request_has_fresh_identity_without_erasing_first(audit):
    helper, _, _ = audit
    first = helper.audit_event(EVENT)
    helper.config.EventCompletion_Audit = True
    second = helper.audit_event(EVENT)
    assert first['folder'] != second['folder']
    assert first['request'] != second['request']
    assert load_reward_observations('alas', folder=first['folder'])['_audit']['request'] == first['request']


@pytest.mark.parametrize('failure', [dict(status='blocked', returned_main=True),
                                     dict(status='observed', returned_main=False),
                                     dict(status='unknown', returned_main=True)])
def test_unknown_panel_stops_before_further_navigation_and_does_not_replay(audit, failure):
    helper, strict, milestones = audit
    helper.observe_event_hub.return_value = failure
    with pytest.raises(GamePageUnknownError, match='hub'):
        helper.run(EVENT, collect_rewards=False, complete_story=False)
    helper.observe_event_featured.assert_not_called()
    strict.return_value.run.assert_not_called()
    milestones.return_value.observe.assert_not_called()
    paths = list(Path(completion.EVENT_AUDIT_DIR).glob('alas/*'))
    state = load_reward_observations('alas', folder=str(paths[0]))['_audit']
    assert state['status'] == 'blocked'
    assert state['current'] == 'hub'
    assert helper.config.EventCompletion_Audit is False
    helper.run(EVENT, collect_rewards=False, complete_story=False)
    assert helper.observe_event_hub.call_count == 1


def test_milestone_unknown_exit_is_persisted_and_stops_after_all_other_panels(audit):
    helper, strict, milestones = audit
    milestones.return_value.observe.return_value = dict(
        status='observed', reason='milestone_destination_assets_required', returned_main=False)
    with pytest.raises(GamePageUnknownError, match='strict_maid_milestones'):
        helper.audit_event(EVENT)
    for name in METHODS:
        assert getattr(helper, name).call_count == 1
    assert strict.return_value.run.call_count == 1
    path = list(Path(completion.EVENT_AUDIT_DIR).glob('alas/*'))[0]
    record = load_reward_observations('alas', folder=str(path))['_audit']
    assert record['status'] == 'blocked'
    assert record['observations']['strict_maid_milestones']['returned_main'] is False


def test_snapshot_is_archived_outside_pruned_shared_diagnostic_directory(audit, tmp_path):
    helper, _, _ = audit
    image = tmp_path / 'current.png'
    image.write_bytes(b'captured image')
    helper.observe_event_main.return_value = dict(status='observed', diagnostics=[str(image)])
    result = helper.audit_event(EVENT)
    archive = result['observations']['main']['audit_diagnostics'][0]
    image.unlink()
    assert Path(archive).read_bytes() == b'captured image'


def test_flag_persistence_failure_performs_no_control(audit):
    helper, strict, milestones = audit

    @contextmanager
    def cannot_save():
        yield
        raise OSError('config write failed')

    helper.config.multi_set = cannot_save
    with pytest.raises(OSError, match='config write failed'):
        helper.audit_event(EVENT)
    for name in METHODS:
        getattr(helper, name).assert_not_called()
    strict.assert_not_called()
    milestones.assert_not_called()


def test_closed_game_does_not_consume_request_or_create_budget(audit):
    helper, _, _ = audit
    helper.device.app_is_running.return_value = False
    with pytest.raises(GameNotRunningError):
        helper.audit_event(EVENT)
    assert helper.config.EventCompletion_Audit is True
    assert not Path(completion.EVENT_AUDIT_DIR).exists()


def test_unsupported_event_consumes_once_without_controls(audit):
    helper, strict, milestones = audit
    assert helper.audit_event('event_unknown')['status'] == 'unsupported'
    assert helper.config.EventCompletion_Audit is False
    for name in METHODS:
        getattr(helper, name).assert_not_called()
    strict.assert_not_called()
    milestones.assert_not_called()


def test_unsupported_account_consumes_request_without_any_controls(audit):
    helper, strict, milestones = audit
    helper.config.config_name = 'unrelated_bot'
    result = helper.audit_event(EVENT)
    assert result['status'] == 'unsupported'
    assert result['reason'] == 'unsupported_account'
    assert helper.config.EventCompletion_Audit is False
    assert load_reward_observations('unrelated_bot', folder=result['folder'])['_audit']['status'] == 'unsupported'
    for name in METHODS:
        getattr(helper, name).assert_not_called()
    strict.assert_not_called()
    milestones.assert_not_called()


def test_audit_runs_after_ordinary_story_and_collection(audit, monkeypatch):
    helper, _, _ = audit
    order = []
    story = Mock()
    story.return_value.run.side_effect = lambda **kwargs: order.append('story') or True
    monkeypatch.setattr(completion, 'EventStory', story)
    helper.observe_event_side_prepare = Mock(return_value=dict(status='observed'))
    helper.observe_event_side_choice = Mock(return_value=dict(status='observed'))
    helper.observe_event_strict_maid = Mock(return_value=dict(status='observed'))
    helper.collect_beckoning_tickets = Mock(return_value=dict(status='unsupported'))
    helper.collect_event_missions = Mock(side_effect=lambda: order.append('missions') or True)
    helper.collect_current_event_rewards = Mock(side_effect=lambda event: order.append('pt') or dict(status='complete'))
    helper.collect_strict_maid_rewards = Mock(side_effect=lambda event: order.append('strict') or dict(status='complete'))
    helper.audit_event = Mock(side_effect=lambda event: order.append('audit') or dict(status='complete'))
    result = helper.run(EVENT, collect_rewards=True, complete_story=True)
    assert order == ['story', 'missions', 'pt', 'strict', 'audit']
    assert result['event_audit']['status'] == 'complete'


def test_observer_exception_is_recorded_without_attempting_later_panels(audit):
    helper, _, milestones = audit
    helper.observe_event_featured.side_effect = RuntimeError('unexpected destination')
    with pytest.raises(RuntimeError, match='unexpected destination'):
        helper.audit_event(EVENT)
    helper.observe_event_rewards_panel.assert_not_called()
    milestones.return_value.observe.assert_not_called()
    path = list(Path(completion.EVENT_AUDIT_DIR).glob('alas/*'))[0]
    record = load_reward_observations('alas', folder=str(path))['_audit']
    assert record['status'] == 'blocked'
    assert record['current'] == 'featured'
