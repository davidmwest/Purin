import json
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from module.exception import ScriptError
from module.shop_event.clerk import ItemNotFoundError
from module.shop_event.observation import EventShopObservation, observation_request_path
from module.shop_event.shop_event import EventShop


def observer(monkeypatch, items=None, available=True):
    shop = EventShopObservation.__new__(EventShopObservation)
    shop.config = SimpleNamespace(config_name='alas', cross_set=Mock(), task_delay=Mock(), task_call=Mock())
    shop.device = SimpleNamespace(image=np.zeros((720, 1280, 3), dtype=np.uint8), screenshot=Mock())
    shop.event_shop_load_ensure = Mock()
    shop.ui_goto_main = Mock()
    shop.__dict__['event_shop_has_urpt'] = False
    shop.pt = 27500
    shop.get_current_pts = Mock()
    shop.scan_all = Mock(return_value=items if items is not None else [
        SimpleNamespace(name='EquipSSR', amount=1, count=0, total_count=1, cost='pt', price=2000,
                        tag=None, is_ship=False),
        SimpleNamespace(name='Chip', amount=100, count=7, total_count=10, cost='pt', price=200,
                        tag=None, is_ship=False)])
    shop.handle_items_related_with_urpt = Mock(side_effect=AssertionError('purchase handler entered'))
    shop.handle_unobtained_items = Mock(side_effect=AssertionError('purchase handler entered'))

    def parent_run(self):
        # The durable disable must precede the first navigation or stock read.
        self.config.cross_set.assert_called_once_with('EventShop.Scheduler.Enable', False)
        if available:
            self._run()
        return available

    monkeypatch.setattr(EventShop, 'run', parent_run)
    return shop


def test_observation_keeps_sold_out_and_available_stock_without_purchases(monkeypatch, tmp_path):
    shop = observer(monkeypatch)
    result = shop.run(folder=tmp_path)

    assert result['status'] == 'complete' and result['purchases'] == 0 and result['returned_main']
    assert result['shops'][0]['pt'] == 27500
    assert [item['count'] for item in result['shops'][0]['items']] == [0, 7]
    assert result['shops'][0]['items'][1]['price'] == 200
    assert json.loads((shop.observation_directory / 'stock.json').read_text()) == result
    shop.scan_all.assert_called_once()
    assert shop.scan_all.call_args[1]['max_pages'] == 40
    shop.handle_items_related_with_urpt.assert_not_called()
    shop.handle_unobtained_items.assert_not_called()
    shop.ui_goto_main.assert_called_once()


def test_no_shop_is_distinct_from_verified_sold_out_stock(monkeypatch, tmp_path):
    shop = observer(monkeypatch, available=False)
    result = shop.run(folder=tmp_path)
    assert result['status'] == 'no_event_shop' and result['shops'] == []
    shop.scan_all.assert_not_called()


@pytest.mark.parametrize('failure', ['empty_stock', 'scroll_stalled', 'balance_changed'])
def test_incomplete_observation_stays_disabled_and_never_claims_stock_complete(monkeypatch, tmp_path, failure):
    shop = observer(monkeypatch)
    if failure == 'empty_stock':
        shop.scan_all.return_value = []
    elif failure == 'scroll_stalled':
        shop.scan_all.side_effect = ItemNotFoundError('scan page budget exhausted')
    else:
        def changed():
            shop.pt += 1
        shop.get_current_pts.side_effect = changed
    result = shop.run(folder=tmp_path)
    saved = json.loads((shop.observation_directory / 'stock.json').read_text())
    assert saved['status'] == 'blocked' and saved['purchases'] == 0
    assert result == saved and saved['returned_main'] is True
    shop.config.task_call.assert_not_called()
    shop.config.cross_set.assert_called_once_with('EventShop.Scheduler.Enable', False)
    shop.handle_items_related_with_urpt.assert_not_called()
    shop.handle_unobtained_items.assert_not_called()


def test_blocked_audit_queues_game_restart_if_recognized_home_navigation_fails(monkeypatch, tmp_path):
    shop = observer(monkeypatch, items=[])
    shop.ui_goto_main.side_effect = ScriptError('page not recognized')

    result = shop.run(folder=tmp_path)

    assert result['status'] == 'blocked' and result['returned_main'] is False and result['purchases'] == 0
    shop.config.task_call.assert_called_once_with('Restart')
    shop.config.cross_set.assert_called_once_with('EventShop.Scheduler.Enable', False)


@pytest.mark.parametrize('method', ['event_shop_buy_item', 'event_shop_buy_item_execute'])
def test_purchase_entry_points_are_forbidden_even_if_called_by_accident(method):
    shop = EventShopObservation.__new__(EventShopObservation)
    with pytest.raises(ScriptError, match='Purchases are forbidden'):
        getattr(shop, method)(object(), amount=1)


@pytest.mark.parametrize('requested', [True, False])
def test_scheduler_uses_the_durable_inspection_request_even_after_a_restart(monkeypatch, tmp_path, requested):
    from alas import AzurLaneAutoScript
    import module.shop_event.shop_event as regular
    import module.shop_event.observation as inspection
    monkeypatch.chdir(tmp_path)
    if requested:
        marker = observation_request_path('alas')
        marker.parent.mkdir(parents=True)
        marker.write_text('inspect stock only')
    normal, audit = Mock(), Mock()
    monkeypatch.setattr(regular, 'EventShop', normal)
    monkeypatch.setattr(inspection, 'EventShopObservation', audit)
    runner = SimpleNamespace(config=SimpleNamespace(config_name='alas'), device=object())

    AzurLaneAutoScript.event_shop(runner)

    (audit if requested else normal).return_value.run.assert_called_once_with()
    (normal if requested else audit).assert_not_called()
