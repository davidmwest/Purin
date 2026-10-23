from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from module.campaign import event_build_orders_observation as observations
from module.campaign.event_completion import load_reward_observations


def owner(monkeypatch):
    value = observations.EventBuildOrdersObservation.__new__(observations.EventBuildOrdersObservation)
    value.config = SimpleNamespace(config_name='alas')
    value.device = SimpleNamespace(click=Mock(), app_is_running=Mock(return_value=True))
    value._receipt_present = Mock(return_value=False)
    value.orders_page_present = Mock(return_value=False)
    value.ui_page_appear = Mock(return_value=True)
    value._gacha_side_navbar = SimpleNamespace(get_info=Mock(return_value=(0, 0, 3)),
                                              grids=SimpleNamespace(buttons=list(range(5))))
    value.loop = Mock(return_value=iter(range(5)))
    value.return_to_main = Mock(return_value=True)
    monkeypatch.setattr(observations, 'ensure_event_page', Mock(return_value=True))
    monkeypatch.setattr(observations, 'save_event_diagnostic', Mock(return_value='proof.png'))
    timer = Mock()
    timer.start.return_value = timer
    timer.reached.return_value = True
    monkeypatch.setattr(observations, 'Timer', Mock(return_value=timer))
    return value


@pytest.mark.parametrize('last', [3, 4])
def test_orders_navigation_reserved_once_without_any_finish_or_build(monkeypatch, tmp_path, last):
    value = owner(monkeypatch)
    value._gacha_side_navbar.get_info.return_value = (0, 0, last)
    on_orders = [False]
    value.orders_page_present.side_effect = lambda: on_orders[0]

    def click(button):
        state = load_reward_observations('alas', tmp_path)
        assert state['event_20261022_cn:build_orders_observation']['orders_clicks'] == 1
        assert button == last - 2
        on_orders[0] = True

    value.device.click.side_effect = click
    record = value.observe('event_20261022_cn', tmp_path)
    assert record['status'] == 'observed' and record['returned_main']
    value.device.click.assert_called_once_with(last - 2)
    assert value.observe('event_20261022_cn', tmp_path)['reused']
    assert value.device.click.call_count == 1


@pytest.mark.parametrize('info,receipt', [((0, 1, 4), False), ((None, 0, 3), False),
                                         ((0, 0, 2), False), ((0, 0, 3), True)])
def test_unknown_sidebar_or_owned_receipt_does_not_tap(monkeypatch, tmp_path, info, receipt):
    value = owner(monkeypatch)
    value._gacha_side_navbar.get_info.return_value = info
    value._receipt_present.return_value = receipt
    assert value.observe('event_20261022_cn', tmp_path)['status'] == 'blocked'
    value.device.click.assert_not_called()
    value.return_to_main.assert_not_called()


def test_crashed_navigation_is_not_replayed(monkeypatch, tmp_path):
    value = owner(monkeypatch)
    value.device.click.side_effect = RuntimeError('interrupted')
    with pytest.raises(RuntimeError):
        value.observe('event_20261022_cn', tmp_path)
    record = value.observe('event_20261022_cn', tmp_path)
    assert record['reused'] and record['status'] == 'failed'
    value.device.click.assert_called_once()
