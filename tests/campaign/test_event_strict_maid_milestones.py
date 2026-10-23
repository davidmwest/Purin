from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest
from PIL import Image

from module.campaign import event_strict_maid_milestones as milestones
from module.campaign.event_completion import load_reward_observations
from module.campaign.event_strict_maid_milestones import STRICT_MAID_MILESTONES, StrictMaidMilestones

EVENT = 'event_20261022_cn'


def controller(monkeypatch):
    owner = StrictMaidMilestones.__new__(StrictMaidMilestones)
    owner.config = SimpleNamespace(config_name='alas')
    owner.device = SimpleNamespace(click=Mock(), app_is_running=Mock(return_value=True))
    owner._strict_page = Mock(return_value=True)
    owner.match_template_color = Mock(return_value=True)
    owner.loop = Mock(side_effect=lambda **kwargs: iter(range(6)))
    helper = Mock()
    helper._inspect_event_rewards_panel.side_effect = lambda record, **kwargs: record.update(status='observed')
    monkeypatch.setattr(milestones, 'EventCompletion', Mock(return_value=helper))
    monkeypatch.setattr(milestones, 'save_event_diagnostic', Mock(return_value='proof.png'))
    timer = Mock()
    timer.start.return_value = timer
    timer.reached.return_value = True
    monkeypatch.setattr(milestones, 'Timer', Mock(return_value=timer))
    return owner


def test_exact_pill_template_rejects_missing_control():
    image = np.asarray(Image.open(STRICT_MAID_MILESTONES.file).convert('RGB'))
    assert STRICT_MAID_MILESTONES.match_template_color(image, offset=(3, 3), similarity=.9)
    assert not STRICT_MAID_MILESTONES.match_template_color(image * 0, offset=(3, 3), similarity=.9)


def test_observation_records_budget_before_click_and_never_touches_destination(monkeypatch, tmp_path):
    owner = controller(monkeypatch)

    def verify_budget(button):
        state = load_reward_observations('alas', folder=tmp_path)
        assert state[EVENT + ':strict_maid_milestone_observation']['clicks'] == 1
        assert button is STRICT_MAID_MILESTONES

    owner.device.click.side_effect = verify_budget
    result = owner.observe(EVENT, folder=tmp_path)
    assert result['status'] == 'observed' and result['returned_main'] is False
    assert result['diagnostics'] == ['proof.png']
    owner.device.click.assert_called_once_with(STRICT_MAID_MILESTONES)
    assert owner.observe(EVENT, folder=tmp_path)['reused'] is True
    assert owner.device.click.call_count == 1


@pytest.mark.parametrize('missing_page', [True, False])
def test_unknown_page_or_pill_never_clicks(monkeypatch, tmp_path, missing_page):
    owner = controller(monkeypatch)
    owner._strict_page.return_value = not missing_page
    owner.match_template_color.return_value = missing_page
    assert owner.observe(EVENT, folder=tmp_path)['status'] == 'blocked'
    owner.device.click.assert_not_called()


def test_other_event_has_no_navigation_or_journal(monkeypatch, tmp_path):
    owner = controller(monkeypatch)
    assert owner.observe('other', folder=tmp_path)['status'] == 'unsupported'
    owner.device.app_is_running.assert_not_called()
    owner.device.click.assert_not_called()
    assert list(tmp_path.iterdir()) == []
