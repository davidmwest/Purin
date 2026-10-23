"""No gem purchase, immediate between-battle reserve stop, and safe retry."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import cv2
import numpy as np
import pytest

import module.config.server as server
from alas import AzurLaneAutoScript
from module.campaign.run import CampaignRun
from module.combat.auto_search_combat import AutoSearchCombat
from module.exception import CampaignEnd, GamePageUnknownError, OilExhausted, ScriptError
from module.handler.info_handler import InfoHandler
from module.handler.oil_assets import OIL_PURCHASE_CANCEL, OIL_PURCHASE_HISTORY, OIL_PURCHASE_TITLE
from module.map.map_operation import MapOperation
from module.ui.ui import UI


@pytest.fixture(autouse=True)
def en():
    previous = server.server
    server.set_server('en')
    yield
    server.set_server(previous)


def oil_handler():
    handler = InfoHandler.__new__(InfoHandler)
    image = cv2.cvtColor(cv2.imread(str(Path(__file__).parent / 'data/oil_purchase_f2p.png')), cv2.COLOR_BGR2RGB)
    handler.device = SimpleNamespace(image=image, click=Mock(), stuck_record_add=Mock())
    handler.interval_timer = {}
    return handler


def test_observed_oil_offer_matches_exact_title_history_and_cancel():
    handler = oil_handler()
    assert handler.oil_purchase_appear()
    assert handler.appear(OIL_PURCHASE_CANCEL, offset=(3, 3))
    # Its safe Cancel is the RIGHT button; standard popup Cancel is on the left.
    assert OIL_PURCHASE_CANCEL.button[0] >= 700


@pytest.mark.parametrize('missing', [OIL_PURCHASE_TITLE, OIL_PURCHASE_HISTORY])
def test_partial_or_unrelated_popup_is_not_the_oil_offer(missing):
    handler = oil_handler()
    x1, y1, x2, y2 = missing.area
    handler.device.image[y1-4:y2+4, x1-4:x2+4] = 0
    assert not handler.oil_purchase_appear()
    assert handler.handle_oil_purchase() is False
    handler.device.click.assert_not_called()


@pytest.mark.parametrize('entry', ['handle_popup_confirm', 'handle_popup_cancel'])
def test_generic_popup_handlers_cancel_oil_without_buying(entry):
    handler = oil_handler()
    def frames(**kwargs):
        yield handler.device.image
        handler.device.image = np.zeros_like(handler.device.image)
        yield handler.device.image
    handler.loop = frames
    with pytest.raises(OilExhausted, match='cancelled'):
        getattr(handler, entry)('COMBAT')
    handler.device.click.assert_called_once_with(OIL_PURCHASE_CANCEL)


def test_unverified_cancel_fails_closed_and_has_a_click_bound():
    handler = oil_handler()
    handler.loop = lambda **kwargs: range(20)
    real_appear = handler.appear
    handler.appear = lambda button, **kwargs: real_appear(button, **{k: v for k, v in kwargs.items() if k != 'interval'})
    with pytest.raises(ScriptError, match='not verified'):
        handler.handle_oil_purchase()
    assert handler.device.click.call_count == 3
    assert all(call.args == (OIL_PURCHASE_CANCEL,) for call in handler.device.click.call_args_list)


def test_missing_cancel_does_not_fall_through_to_generic_confirm():
    handler = oil_handler()
    x1, y1, x2, y2 = OIL_PURCHASE_CANCEL.area
    handler.device.image[y1-4:y2+4, x1-4:x2+4] = 0
    handler.loop = lambda **kwargs: range(3)
    with pytest.raises(ScriptError):
        handler.handle_popup_confirm('COMBAT')
    handler.device.click.assert_not_called()


def moving(oil):
    combat = AutoSearchCombat.__new__(AutoSearchCombat)
    combat.config = SimpleNamespace(StopCondition_OilLimit=0)
    combat.device = SimpleNamespace(stuck_record_clear=Mock())
    combat.loop = lambda **kwargs: range(2)
    combat._get_oil = Mock(return_value=oil)
    combat.auto_search_oil_limit_triggered = False
    combat.handle_oil_purchase = Mock(return_value=False)
    combat.is_auto_search_running = Mock(return_value=True)
    combat.auto_search_watch_fleet = Mock(return_value=True)
    combat.auto_search_watch_coin = Mock(return_value=True)
    for name in ['handle_retirement', 'handle_auto_search_map_option', 'handle_combat_low_emotion',
                 'handle_story_skip', 'handle_map_cat_attack', 'handle_vote_popup', 'handle_popup_single',
                 'is_combat_executing', 'is_in_auto_search_menu', '_handle_auto_search_menu_missing',
                 'appear', 'appear_then_click']:
        setattr(combat, name, Mock(return_value=False))
    combat.interval_reset = Mock()
    combat.is_in_map = Mock(return_value=True)
    combat.is_combat_loading = Mock(return_value=True)
    return combat


@pytest.mark.parametrize('oil', [0, 24, 141, 499])
def test_between_battle_oil_guard_stops_before_another_battle(oil):
    combat = moving(oil)
    with pytest.raises(OilExhausted):
        combat.auto_search_moving()
    combat.handle_retirement.assert_not_called()
    combat.handle_auto_search_map_option.assert_not_called()
    combat.is_combat_loading.assert_not_called()


def test_replenished_oil_clears_stale_stop_before_enabling_auto_search():
    combat = moving(500)
    combat.auto_search_oil_limit_triggered = True
    combat.is_combat_loading.side_effect = [False, True]
    combat.map_offensive_auto_search()
    assert combat.auto_search_oil_limit_triggered is False
    combat._get_oil.assert_called_once_with()
    assert combat.is_combat_loading.call_count == 2


def test_map_offensive_never_resumes_a_low_oil_retirement_return():
    combat = moving(141)
    combat.is_combat_loading.return_value = False
    with pytest.raises(OilExhausted):
        combat.map_offensive_auto_search()
    combat.handle_auto_search_map_option.assert_not_called()
    combat.is_combat_loading.assert_called_once_with()


@pytest.mark.parametrize('state', ['is_combat_loading', 'is_combat_executing'])
def test_loading_or_active_battle_does_not_read_map_oil_or_withdraw(state):
    combat = moving(0)
    combat.auto_search_oil_limit_triggered = True
    combat.is_combat_loading.return_value = False
    getattr(combat, state).return_value = True
    combat.map_offensive_auto_search()
    combat._get_oil.assert_not_called()
    combat.handle_auto_search_map_option.assert_not_called()


def campaign_runner(oil=500, limit=0):
    runner = CampaignRun.__new__(CampaignRun)
    runner.config = SimpleNamespace(
        StopCondition_RunCount=1, StopCondition_ReachLevel=False, StopCondition_OilLimit=limit,
        StopCondition_GetNewShip=False, TaskBalancer_Enable=False,
        task_delay=Mock(), task_call=Mock())
    runner.run_limit = 0
    runner.run_count = 0
    runner.campaign = SimpleNamespace(auto_search_oil_limit_triggered=True,
                                     event_pt_limit_triggered=Mock(return_value=False))
    runner.get_oil = Mock(return_value=oil)
    runner.get_coin = Mock(return_value=0)
    return runner


@pytest.mark.parametrize('oil,limit,stopped', [(141, 0, True), (499, 0, True), (500, 0, False),
                                             (9999, 10000, True), (10000, 10000, False)])
def test_sortie_reserve_matches_between_battle_floor_and_user_floor(oil, limit, stopped):
    runner = campaign_runner(oil, limit)
    assert runner.triggered_stop_condition() is stopped
    if stopped:
        runner.config.task_delay.assert_called_once_with(minute=(120, 240))
        runner.config.task_call.assert_called_once_with('Reward', force_call=False)
    else:
        assert runner.campaign.auto_search_oil_limit_triggered is False
        runner.config.task_delay.assert_not_called()


def test_aborted_sortie_withdraws_boundedly_and_preserves_run_quota():
    runner = campaign_runner()
    runner.run_count = 7
    runner._run = Mock(side_effect=OilExhausted('reserve'))
    runner.campaign.withdraw = Mock(side_effect=CampaignEnd('Withdraw'))
    runner.run('t1', folder='event_20261022_cn', total=1)
    runner.campaign.withdraw.assert_called_once_with(timeout=20)
    assert runner.oil_deferred is True
    assert runner.run_count == 0
    assert runner.config.StopCondition_RunCount == 1
    runner.config.task_delay.assert_called_once_with(minute=(120, 240))
    runner.config.task_call.assert_called_once_with('Reward', force_call=False)


def test_next_run_resets_old_oil_deferral():
    runner = campaign_runner()
    runner.oil_deferred = True
    runner._run = Mock()
    runner.run('t1')
    assert runner.oil_deferred is False


def test_withdrawal_unknown_screen_has_a_time_bound_and_never_confirms():
    operation = MapOperation.__new__(MapOperation)
    operation.loop = Mock(return_value=range(2))
    for name in ['handle_popup_confirm', 'handle_popup_single', 'appear_then_click',
                 'handle_auto_search_exit', 'appear', 'handle_in_stage']:
        setattr(operation, name, Mock(return_value=False))
    with pytest.raises(GamePageUnknownError, match='withdrawal timed out'):
        operation.withdraw(timeout=20)
    operation.loop.assert_called_once_with(skip_first=True, timeout=20)


def test_startup_checks_purchase_before_any_background_page():
    ui = UI.__new__(UI)
    ui.device = SimpleNamespace(has_cached_image=True)
    ui.handle_oil_purchase = Mock(side_effect=OilExhausted('cancelled'))
    ui.ui_page_appear = Mock(return_value=True)
    with pytest.raises(OilExhausted):
        ui.ui_get_current_page()
    ui.handle_oil_purchase.assert_called_once_with()
    ui.ui_page_appear.assert_not_called()


def test_scheduler_recovers_cancelled_oil_modal_without_deferring_free_rewards():
    runner = SimpleNamespace(
        device=SimpleNamespace(screenshot=Mock()), config=SimpleNamespace(task_call=Mock(), task_delay=Mock()),
        reward=Mock(side_effect=OilExhausted('cancelled')))
    assert AzurLaneAutoScript.run(runner, 'reward') is False
    runner.config.task_call.assert_called_once_with('Restart')
    runner.config.task_delay.assert_not_called()
