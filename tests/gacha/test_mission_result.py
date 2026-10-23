from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest
from PIL import Image

from module.combat.assets import GET_ITEMS_1
from module.exception import GameNotRunningError
from module.gacha import mission_result as result
from module.gacha.mission_result import GachaSubmissionResult
from module.gacha.mission_result_assets import ORDERS_HOME, ORDERS_WITNESSES
from module.ui.page import page_main


class ImmediateTimer:
    def __init__(self, *args, **kwargs): pass
    def start(self): return self
    def reset(self): return self
    def reached(self): return True


def controller(monkeypatch, frames=30):
    helper = GachaSubmissionResult.__new__(GachaSubmissionResult)
    helper.config = SimpleNamespace(config_name='alas')
    helper.device = SimpleNamespace(click=Mock())
    helper.loop = Mock(return_value=iter(range(frames)))
    helper._receipt_present = Mock(return_value=False)
    helper.orders_page_present = Mock(return_value=True)
    helper.ui_page_appear = Mock(return_value=False)
    monkeypatch.setattr(result, 'Timer', ImmediateTimer)
    monkeypatch.setattr(result, 'ensure_event_running', Mock())
    monkeypatch.setattr(result, 'save_event_diagnostic', Mock(return_value='blocked.png'))
    return helper


@pytest.mark.parametrize('account', ['alas', 'f2p'])
def test_actual_orders_pages_require_every_exact_witness(monkeypatch, account):
    helper = controller(monkeypatch)
    del helper.orders_page_present
    del helper._receipt_present
    original = np.asarray(Image.open(f'tests/gacha/data/orders_result_{account}.png').convert('RGB')).copy()
    helper.device.image = original
    helper.match_template_color = lambda button, **kw: button.match_template_color(helper.device.image, **kw)
    assert helper.orders_page_present()
    for button in ORDERS_WITNESSES:
        helper.device.image = original.copy()
        x1, y1, x2, y2 = button.area
        helper.device.image[y1-4:y2+4, x1-4:x2+4] = 0
        assert not helper.orders_page_present(), button.name
    helper.device.click.assert_not_called()


def test_build_pool_is_not_orders(monkeypatch):
    helper = controller(monkeypatch)
    del helper.orders_page_present
    del helper._receipt_present
    helper.device.image = np.asarray(Image.open('tests/gacha/data/event_build_count.png').convert('RGB'))
    helper.match_template_color = lambda button, **kw: button.match_template_color(helper.device.image, **kw)
    assert not helper.orders_page_present()


def test_known_receipt_over_matching_background_vetoes_home(monkeypatch):
    helper = controller(monkeypatch)
    del helper.orders_page_present
    del helper._receipt_present
    helper.match_template_color = Mock(side_effect=lambda button, **kw: button in ORDERS_WITNESSES or button == GET_ITEMS_1)
    assert not helper.orders_page_present()
    assert not helper.return_to_main()
    helper.device.click.assert_not_called()


def test_orders_home_only_then_verified_fresh_main(monkeypatch):
    helper = controller(monkeypatch)
    phase = {'main': False}
    helper.ui_page_appear.side_effect = lambda page: phase['main']
    helper.orders_page_present.side_effect = lambda: not phase['main']
    helper.device.click.side_effect = lambda button: phase.update(main=True)
    assert helper.return_to_main()
    helper.device.click.assert_called_once_with(ORDERS_HOME)
    assert helper.ui_current == page_main
    result.save_event_diagnostic.assert_not_called()
    helper.loop.assert_called_once_with(skip_first=False, timeout=12)


def test_already_main_has_no_input(monkeypatch):
    helper = controller(monkeypatch)
    helper.ui_page_appear.return_value = True
    assert helper.return_to_main()
    helper.device.click.assert_not_called()


def test_unknown_page_has_no_generic_navigation(monkeypatch):
    helper = controller(monkeypatch)
    helper.orders_page_present.return_value = False
    assert not helper.return_to_main()
    helper.device.click.assert_not_called()
    result.save_event_diagnostic.assert_called_once()


def test_repeated_orders_frames_are_bounded_to_two_home_clicks(monkeypatch):
    helper = controller(monkeypatch)
    assert not helper.return_to_main()
    assert [call.args[0] for call in helper.device.click.call_args_list] == [ORDERS_HOME, ORDERS_HOME]
    result.save_event_diagnostic.assert_called_once()


def test_transient_orders_match_cannot_authorize_home(monkeypatch):
    helper = controller(monkeypatch, frames=10)
    helper.orders_page_present.side_effect = [True, False] * 5
    assert not helper.return_to_main()
    helper.device.click.assert_not_called()


def test_return_requires_three_fresh_main_frames(monkeypatch):
    helper = controller(monkeypatch, frames=2)
    helper.ui_page_appear.return_value = True
    assert not helper.return_to_main()
    helper.device.click.assert_not_called()


def test_closed_game_does_not_touch_queue(monkeypatch):
    helper = controller(monkeypatch)
    result.ensure_event_running.side_effect = GameNotRunningError
    with pytest.raises(GameNotRunningError):
        helper.return_to_main()
    helper.device.click.assert_not_called()
    helper.loop.assert_not_called()
