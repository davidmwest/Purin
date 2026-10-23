from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest
from PIL import Image

from module.campaign import event_strict_maid_milestones as milestones
from module.campaign.event_completion import load_reward_observations, save_reward_observations
from module.campaign.event_strict_maid_milestone_assets import (
    MILESTONE_CLOSE, MILESTONE_COMPLETED, MILESTONE_FINAL_PHASES,
    MILESTONE_GRAY_COLLECT, MILESTONE_HEADER, MILESTONE_POSSESSION)
from module.campaign.event_strict_maid_milestones import STRICT_MAID_MILESTONES, StrictMaidMilestones
from module.campaign.event_strict_maid_rewards_assets import STRICT_MAID_HOME

EVENT = 'event_20261022_cn'
OBSERVATION = EVENT + ':strict_maid_milestone_observation'
RECOVERY = EVENT + ':strict_maid_milestone_recovery'
FIXTURE = 'tests/campaign/data/strict_maid_milestones_completed.png'


def controller(monkeypatch, initial='strict', sequences=None):
    owner = StrictMaidMilestones.__new__(StrictMaidMilestones)
    owner.config = SimpleNamespace(config_name='alas')
    owner.device = SimpleNamespace(click=Mock(), screenshot=Mock(), stuck_record_clear=Mock(),
                                   app_is_running=Mock(return_value=True))
    owner.frame = initial
    owner._strict_page = Mock(side_effect=lambda: owner.frame == 'strict')
    owner._milestone_modal = Mock(side_effect=lambda: owner.frame in ('modal', 'unknown_modal'))
    owner._milestone_completed = Mock(side_effect=lambda: owner.frame == 'modal')
    owner.ui_page_appear = Mock(side_effect=lambda page: owner.frame == 'main')
    owner.match_template_color = Mock(return_value=True)
    frames = iter(sequences or [[]])

    def loop(**kwargs):
        for frame in next(frames, []):
            owner.frame = frame
            yield None

    owner.loop = Mock(side_effect=loop)
    helper = Mock()
    helper._inspect_event_rewards_panel.side_effect = lambda record, **kwargs: record.update(status='observed')
    monkeypatch.setattr(milestones, 'EventCompletion', Mock(return_value=helper))
    monkeypatch.setattr(milestones, 'save_event_diagnostic', Mock(return_value='proof.png'))
    timer = Mock()
    timer.start.return_value = timer
    timer.reached.return_value = True
    monkeypatch.setattr(milestones, 'Timer', Mock(return_value=timer))
    return owner


def actual_controller(image):
    owner = StrictMaidMilestones.__new__(StrictMaidMilestones)
    owner.device = SimpleNamespace(image=image)
    owner.match_template_color = lambda button, **kwargs: button.match_template_color(image, **kwargs)
    return owner


def test_exact_pill_template_rejects_missing_control():
    image = np.asarray(Image.open(STRICT_MAID_MILESTONES.file).convert('RGB'))
    assert STRICT_MAID_MILESTONES.match_template_color(image, offset=(3, 3), similarity=.9)
    assert not STRICT_MAID_MILESTONES.match_template_color(image * 0, offset=(3, 3), similarity=.9)


def test_actual_final_phases_are_completed_and_collect_is_gray():
    owner = actual_controller(np.asarray(Image.open(FIXTURE).convert('RGB')))
    assert owner._milestone_modal()
    assert owner._milestone_completed()


@pytest.mark.parametrize('missing', [MILESTONE_HEADER, MILESTONE_POSSESSION, MILESTONE_CLOSE,
                                    *MILESTONE_FINAL_PHASES, *MILESTONE_COMPLETED,
                                    MILESTONE_GRAY_COLLECT])
def test_actual_completed_layout_requires_every_exact_witness(missing):
    image = np.asarray(Image.open(FIXTURE).convert('RGB')).copy()
    x1, y1, x2, y2 = missing.area
    image[y1 - 4:y2 + 4, x1 - 4:x2 + 4] = 0
    assert not actual_controller(image)._milestone_completed()


def test_observation_persists_entry_close_home_before_each_input(monkeypatch, tmp_path):
    owner = controller(monkeypatch, sequences=[['strict', 'modal'],
                        ['modal'] * 3 + ['strict'] + ['main'] * 3])
    inputs = []

    def verify_budget(button):
        record = load_reward_observations('alas', folder=tmp_path)[OBSERVATION]
        if button is STRICT_MAID_MILESTONES:
            assert record['clicks'] == 1
        elif button is MILESTONE_CLOSE:
            assert record['close_clicks'] == 1 and record['completed_verified']
        elif button is STRICT_MAID_HOME:
            assert record['home_clicks'] == 1
        else:
            pytest.fail('Unexpected destination input')
        assert record['claims'] == 0
        inputs.append(button)

    owner.device.click.side_effect = verify_budget
    result = owner.observe(EVENT, folder=tmp_path)
    assert result['status'] == 'complete' and result['returned_main'] is True
    assert result['reward_state'] == 'final_completed'
    assert inputs == [STRICT_MAID_MILESTONES, MILESTONE_CLOSE, STRICT_MAID_HOME]
    assert owner.observe(EVENT, folder=tmp_path)['reused'] is True
    assert owner.device.click.call_count == 3


def test_resume_exact_modal_without_reopening_or_resetting_other_ledgers(monkeypatch, tmp_path):
    old = {'unrelated_story': {'status': 'complete', 'clicks': 6}}
    save_reward_observations('alas', old, folder=tmp_path)
    owner = controller(monkeypatch, initial='modal', sequences=[
        ['modal'] * 3 + ['strict'] + ['main'] * 3])
    assert owner.resume(EVENT, folder=tmp_path) is True
    owner.device.screenshot.assert_called_once()
    assert owner.device.click.call_args_list == [((MILESTONE_CLOSE,),), ((STRICT_MAID_HOME,),)]
    state = load_reward_observations('alas', folder=tmp_path)
    assert state['unrelated_story'] == old['unrelated_story']
    assert OBSERVATION not in state
    assert state[RECOVERY]['status'] == 'complete' and state[RECOVERY]['claims'] == 0
    milestones.EventCompletion.assert_not_called()


def test_resume_absent_modal_is_fresh_noop_without_journal(monkeypatch, tmp_path):
    owner = controller(monkeypatch)
    assert owner.resume(EVENT, folder=tmp_path) is True
    owner.device.screenshot.assert_called_once()
    owner.device.click.assert_not_called()
    assert list(tmp_path.iterdir()) == []


def test_unknown_milestone_controls_are_not_closed_or_claimed(monkeypatch, tmp_path):
    owner = controller(monkeypatch, initial='unknown_modal', sequences=[['unknown_modal'] * 12])
    assert owner.resume(EVENT, folder=tmp_path) is False
    owner.device.click.assert_not_called()
    assert load_reward_observations('alas', folder=tmp_path)[RECOVERY]['status'] == 'blocked'


def test_close_and_home_are_capped_and_return_requires_fresh_main(monkeypatch, tmp_path):
    owner = controller(monkeypatch, initial='modal', sequences=[['modal'] * 15])
    assert owner.resume(EVENT, folder=tmp_path) is False
    assert owner.device.click.call_args_list == [((MILESTONE_CLOSE,),), ((MILESTONE_CLOSE,),)]
    assert load_reward_observations('alas', folder=tmp_path)[RECOVERY]['close_clicks'] == 2
    # Exhausted durable budget survives another attempt on the same modal.
    owner.loop.side_effect = lambda **kwargs: iter([None] * 12)
    assert owner.resume(EVENT, folder=tmp_path) is False
    assert owner.device.click.call_count == 2


def test_home_budget_survives_crash_after_close(monkeypatch, tmp_path):
    pending = dict(status='recovering', diagnostics=[], claims=0, returned_main=False,
                   completed_verified=True, close_clicks=1, home_clicks=1)
    save_reward_observations('alas', {RECOVERY: pending}, folder=tmp_path)
    owner = controller(monkeypatch, initial='modal', sequences=[
        ['modal'] * 3 + ['strict'] * 5 + ['main'] * 3])
    assert owner.resume(EVENT, folder=tmp_path) is True
    state = load_reward_observations('alas', folder=tmp_path)[RECOVERY]
    assert state['close_clicks'] == 2 and state['home_clicks'] == 2
    assert owner.device.click.call_count == 2


def test_unsettled_single_main_frame_never_proves_return(monkeypatch, tmp_path):
    owner = controller(monkeypatch, initial='modal', sequences=[
        ['modal'] * 3 + ['main', 'unknown', 'main', 'unknown']])
    assert owner.resume(EVENT, folder=tmp_path) is False
    assert owner.device.click.call_args_list == [((MILESTONE_CLOSE,),)]


def test_persist_failure_prevents_close(monkeypatch, tmp_path):
    owner = controller(monkeypatch, initial='modal', sequences=[['modal'] * 3])
    monkeypatch.setattr(milestones, 'save_reward_observations', Mock(side_effect=OSError('disk')))
    with pytest.raises(OSError, match='disk'):
        owner.resume(EVENT, folder=tmp_path)
    owner.device.click.assert_not_called()


@pytest.mark.parametrize('missing_page', [True, False])
def test_unknown_page_or_pill_never_clicks(monkeypatch, tmp_path, missing_page):
    owner = controller(monkeypatch, sequences=[['strict'] * 6])
    owner._strict_page.return_value = not missing_page
    owner._strict_page.side_effect = None
    owner.match_template_color.return_value = missing_page
    assert owner.observe(EVENT, folder=tmp_path)['status'] == 'blocked'
    owner.device.click.assert_not_called()


def test_other_event_has_no_navigation_or_journal(monkeypatch, tmp_path):
    owner = controller(monkeypatch)
    assert owner.observe('other', folder=tmp_path)['status'] == 'unsupported'
    assert owner.resume('other', folder=tmp_path) is True
    owner.device.app_is_running.assert_not_called()
    owner.device.click.assert_not_called()
    assert list(tmp_path.iterdir()) == []
