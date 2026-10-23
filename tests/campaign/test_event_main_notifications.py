from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest
from PIL import Image

from module.campaign import event_main_notifications as notifications
from module.campaign.event_completion import load_reward_observations
from module.campaign.event_main_notifications import (
    DAY7, ENTRIES, EVENTS_HOME, EVENTS_TITLE, PACKS, RENTAL_OUTFITS, SHOP_HOME,
    SHOP_TITLE, EventMainNotifications)
from module.exception import GameNotRunningError
from module.ui.page import page_event_list, page_main, page_shop

EVENT = 'event_20261022_cn'


def controller(monkeypatch):
    owner = EventMainNotifications.__new__(EventMainNotifications)
    owner.config = SimpleNamespace(config_name='alas')
    owner.device = SimpleNamespace(click=Mock(), app_is_running=Mock(return_value=True))
    owner._source = Mock(return_value=True)
    owner._notification_return_main = Mock(return_value=False)
    owner.loop = Mock(side_effect=lambda **kwargs: iter(range(6)))
    monkeypatch.setattr(notifications, 'ensure_event_page', Mock(return_value=True))
    monkeypatch.setattr(notifications, 'save_event_diagnostic', Mock(return_value='proof.png'))
    timer = Mock()
    timer.start.return_value = timer
    timer.reached.return_value = True
    monkeypatch.setattr(notifications, 'Timer', Mock(return_value=timer))
    return owner


@pytest.mark.parametrize('entry,buttons', [
    ('day7', [DAY7]), ('rental_outfits', [EVENTS_TITLE, RENTAL_OUTFITS, EVENTS_HOME]),
    ('packs', [SHOP_TITLE, PACKS, SHOP_HOME]),
])
def test_actual_entry_assets_and_missing_controls(entry, buttons):
    image = np.asarray(Image.open(Path(__file__).parent / 'data' /
                                  f'main_notification_{entry}.png').convert('RGB'))
    for button in buttons:
        button.clear_offset()
        assert button.match_template_color(image, offset=(3, 3), similarity=.9)
        missing = image.copy()
        x1, y1, x2, y2 = button.area
        missing[y1:y2, x1:x2] = 0
        button.clear_offset()
        assert not button.match_template_color(missing, offset=(3, 3), similarity=.9)


@pytest.mark.parametrize('entry', ENTRIES)
def test_one_entry_tap_reserved_before_input_and_destination_capture(monkeypatch, tmp_path, entry):
    owner = controller(monkeypatch)
    order = []

    def click(button):
        record = load_reward_observations('alas', tmp_path)[EVENT + ':main_notification:' + entry]
        assert record['clicks'] == 1
        assert button is ENTRIES[entry][2]
        order.append('entry')

    owner.device.click.side_effect = click
    notifications.save_event_diagnostic.side_effect = lambda *args: order.append('capture') or 'proof.png'
    owner._notification_return_main.side_effect = lambda *args: order.append('return') or False
    result = owner.observe(EVENT, entry, tmp_path)
    assert result['status'] == 'observed' and result['returned_main'] is False
    assert result['diagnostics'] == ['proof.png']
    assert order == ['entry', 'capture', 'return']
    owner.device.click.assert_called_once_with(ENTRIES[entry][2])
    assert owner.observe(EVENT, entry, tmp_path)['reused'] is True
    assert owner.device.click.call_count == 1


def test_navigation_budget_exists_before_navigation(monkeypatch, tmp_path):
    owner = controller(monkeypatch)

    def navigate(*args, **kwargs):
        record = load_reward_observations('alas', tmp_path)[EVENT + ':main_notification:day7']
        assert record['clicks'] == 0
        return False

    notifications.ensure_event_page.side_effect = navigate
    assert owner.observe_day7(EVENT, tmp_path)['status'] == 'blocked'
    owner.device.click.assert_not_called()


def test_crash_after_entry_reservation_never_replays(monkeypatch, tmp_path):
    owner = controller(monkeypatch)
    owner.device.click.side_effect = RuntimeError('interrupted input')
    with pytest.raises(RuntimeError):
        owner.observe_day7(EVENT, tmp_path)
    record = owner.observe_day7(EVENT, tmp_path)
    assert record['reused'] is True and record['clicks'] == 1
    assert record['status'] == 'failed'
    assert owner.device.click.call_count == 1


@pytest.mark.parametrize('entry', ENTRIES)
def test_unrecognized_entry_or_parent_never_taps(monkeypatch, tmp_path, entry):
    owner = controller(monkeypatch)
    owner._source.return_value = False
    assert owner.observe(EVENT, entry, tmp_path)['status'] == 'blocked'
    owner.device.click.assert_not_called()


@pytest.mark.parametrize('event,account,entry', [
    ('other', 'alas', 'day7'), (EVENT, 'other', 'day7'), (EVENT, 'alas', 'unknown')])
def test_unsupported_has_no_input_or_budget(monkeypatch, tmp_path, event, account, entry):
    owner = controller(monkeypatch)
    owner.config.config_name = account
    assert owner.observe(event, entry, tmp_path)['status'] == 'unsupported'
    owner.device.app_is_running.assert_not_called()
    owner.device.click.assert_not_called()
    assert list(tmp_path.iterdir()) == []


def test_closed_game_does_not_consume_observation(monkeypatch, tmp_path):
    owner = controller(monkeypatch)
    owner.device.app_is_running.return_value = False
    with pytest.raises(GameNotRunningError):
        owner.observe_day7(EVENT, tmp_path)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('missing', ['page', 'title', 'entry'])
def test_source_needs_each_exact_witness(missing):
    owner = EventMainNotifications.__new__(EventMainNotifications)
    owner.ui_page_appear = Mock(return_value=missing != 'page')
    owner._exact = Mock(side_effect=lambda button: button is not (
        EVENTS_TITLE if missing == 'title' else RENTAL_OUTFITS) if missing != 'page' else True)
    assert not owner._source('rental_outfits')


def test_unknown_destination_has_no_exit_input(monkeypatch):
    owner = controller(monkeypatch)
    del owner._notification_return_main
    owner.ui_page_appear = Mock(return_value=False)
    owner._exact = Mock(return_value=True)
    assert owner._notification_return_main({}, Mock()) is False
    owner.device.click.assert_not_called()


def test_known_home_reservation_precedes_only_one_home_tap(monkeypatch):
    owner = controller(monkeypatch)
    del owner._notification_return_main
    on_main = [False]
    owner.ui_page_appear = Mock(side_effect=lambda page: on_main[0] if page is page_main
                                else page is page_shop)
    owner._exact = Mock(return_value=True)
    record = dict(home_clicks=0)
    persisted = []

    def click(button):
        assert button is SHOP_HOME
        assert persisted == [1]
        on_main[0] = True

    owner.device.click.side_effect = click
    assert owner._notification_return_main(record, lambda: persisted.append(record['home_clicks']))
    owner.device.click.assert_called_once_with(SHOP_HOME)


def test_home_template_alone_cannot_exit_an_unknown_destination(monkeypatch):
    owner = controller(monkeypatch)
    del owner._notification_return_main
    owner.ui_page_appear = Mock(side_effect=lambda page: page is page_event_list)
    owner._exact = Mock(side_effect=lambda button: button is EVENTS_HOME)
    assert owner._notification_return_main({}, Mock()) is False
    owner.device.click.assert_not_called()


def test_missing_capture_does_not_attempt_return(monkeypatch, tmp_path):
    owner = controller(monkeypatch)
    notifications.save_event_diagnostic.return_value = None
    result = owner.observe_day7(EVENT, tmp_path)
    assert result['status'] == 'blocked' and result['reason'] == 'diagnostic_not_saved'
    owner._notification_return_main.assert_not_called()


def test_accounts_do_not_share_observation_budget(monkeypatch, tmp_path):
    owner = controller(monkeypatch)
    owner.observe_day7(EVENT, tmp_path)
    owner.config.config_name = 'f2p'
    assert not owner.observe_day7(EVENT, tmp_path).get('reused')
    assert owner.device.click.call_count == 2
