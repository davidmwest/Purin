"""No gem purchase, immediate between-battle reserve stop, and safe retry."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import cv2
import numpy as np
import pytest

import module.config.server as server
from alas import AzurLaneAutoScript
from module.campaign import campaign_status
from module.campaign.campaign_status import CampaignStatus, OilReadError
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
    combat.get_oil = Mock(side_effect=OilReadError('unverified') if oil < 100 else None,
                          return_value=oil)
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


@pytest.mark.parametrize('oil', [100, 141, 499])
def test_between_battle_oil_guard_stops_before_another_battle(oil):
    combat = moving(oil)
    with pytest.raises(OilExhausted):
        combat.auto_search_moving()
    combat.handle_retirement.assert_not_called()
    combat.handle_auto_search_map_option.assert_not_called()
    combat.is_combat_loading.assert_not_called()


@pytest.mark.parametrize('oil', [0, 24])
def test_unknown_between_battle_oil_never_sets_a_low_oil_flag_or_starts_battle(oil):
    combat = moving(oil)
    with pytest.raises(OilReadError):
        combat.auto_search_moving()
    assert combat.auto_search_oil_limit_triggered is False
    combat.handle_retirement.assert_not_called()
    combat.handle_auto_search_map_option.assert_not_called()
    combat.is_combat_loading.assert_not_called()


@pytest.mark.parametrize('oil,stopped', [(4999, True), (5000, False)])
def test_between_battle_guard_respects_mature_account_reserve(oil, stopped):
    combat = moving(oil)
    combat.config.StopCondition_OilLimit = 5000
    assert combat.auto_search_watch_oil() is True
    assert combat.auto_search_oil_limit_triggered is stopped
    combat.get_oil.assert_called_once_with()


def test_replenished_oil_clears_stale_stop_before_enabling_auto_search():
    combat = moving(500)
    combat.auto_search_oil_limit_triggered = True
    combat.is_combat_loading.side_effect = [False, True]
    combat.map_offensive_auto_search()
    assert combat.auto_search_oil_limit_triggered is False
    combat.get_oil.assert_called_once_with()
    combat._get_oil.assert_not_called()
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
    combat.get_oil.assert_not_called()
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


def oil_reader(monkeypatch, readings, icon=True):
    reader = CampaignStatus.__new__(CampaignStatus)
    reader.device = SimpleNamespace(screenshot=Mock())
    reader.appear = Mock(return_value=icon)
    reader._get_oil = Mock(side_effect=readings)
    timer = Mock()
    timer.start.return_value = timer
    timer.reached.side_effect = [False] * len(readings) + [True]
    factory = Mock(return_value=timer)
    monkeypatch.setattr(campaign_status, 'Timer', factory)
    return reader


def test_missing_icon_cannot_be_reported_as_zero_oil(monkeypatch):
    reader = oil_reader(monkeypatch, [0, 0], icon=False)
    with pytest.raises(OilReadError, match='could not be verified'):
        reader.get_oil()
    reader._get_oil.assert_not_called()
    assert reader.device.screenshot.call_count == 2


@pytest.mark.parametrize('reading', [0, 24, 99])
def test_blank_or_ambiguous_oil_ocr_is_unknown_after_bounded_rereads(monkeypatch, reading):
    reader = oil_reader(monkeypatch, [reading, reading])
    with pytest.raises(OilReadError):
        reader.get_oil()
    assert reader._get_oil.call_count == 2
    assert reader.device.screenshot.call_count == 2


@pytest.mark.parametrize('readings', [[100], [12435], [0, 12435]])
def test_oil_reader_returns_only_a_verified_value_and_refreshes_after_bad_ocr(monkeypatch, readings):
    reader = oil_reader(monkeypatch, readings)
    assert reader.get_oil() == readings[-1]
    assert reader.device.screenshot.call_count == len(readings) - 1


def test_observed_in_map_oil_bar_supports_the_verified_reader():
    # This existing EX tactical-map capture has the same bar used while fleets
    # move. Check the actual icon and OCR together before using this reader in
    # auto_search_watch_oil, rather than assuming stage-list coordinates work.
    path = Path(__file__).parents[1] / 'campaign/data/ex_tactical_map.png'
    reader = CampaignStatus.__new__(CampaignStatus)
    reader.device = SimpleNamespace(image=cv2.cvtColor(cv2.imread(str(path)), cv2.COLOR_BGR2RGB),
                                    screenshot=Mock(), stuck_record_add=Mock())
    reader.interval_timer = {}
    assert reader.get_oil() == 5085
    reader.device.screenshot.assert_not_called()


def test_readable_continue_menu_keeps_auto_search_continue_fast_path():
    runner = campaign_runner(oil=12435, limit=5000)
    runner.campaign.is_in_auto_search_menu = Mock()
    runner.campaign.handle_auto_search_exit = Mock()
    runner.loop = Mock()
    assert runner.triggered_stop_condition() is False
    runner.campaign.is_in_auto_search_menu.assert_not_called()
    runner.campaign.handle_auto_search_exit.assert_not_called()
    runner.loop.assert_not_called()
    runner.config.task_delay.assert_not_called()


@pytest.mark.parametrize('oil,stopped', [(4999, True), (5000, False), (12435, False)])
def test_hidden_continue_oil_returns_to_stage_list_and_rechecks_actual_reserve(oil, stopped):
    runner = campaign_runner(limit=5000)
    runner.get_oil.side_effect = [OilReadError('No icon'), oil]
    runner.campaign.is_in_auto_search_menu = Mock(return_value=True)
    runner.campaign.is_in_stage = Mock(side_effect=[False, True])
    runner.campaign.handle_auto_search_exit = Mock(return_value=True)
    runner.loop = Mock(return_value=range(2))
    assert runner.triggered_stop_condition() is stopped
    runner.loop.assert_called_once_with(skip_first=False, timeout=10)
    runner.campaign.handle_auto_search_exit.assert_called_once_with()
    assert [call.kwargs for call in runner.get_oil.call_args_list] == [
        {}, dict(skip_first_screenshot=False)]
    if stopped:
        runner.config.task_delay.assert_called_once_with(minute=(120, 240))
        runner.config.task_call.assert_called_once_with('Reward', force_call=False)
    else:
        assert runner.campaign.auto_search_oil_limit_triggered is False
        runner.config.task_delay.assert_not_called()
        runner.config.task_call.assert_not_called()


def test_unknown_page_does_not_receive_an_auto_search_exit_click():
    runner = campaign_runner()
    runner.get_oil.side_effect = OilReadError('No icon')
    runner.campaign.is_in_auto_search_menu = Mock(return_value=False)
    runner.campaign.handle_auto_search_exit = Mock()
    runner.loop = Mock()
    with pytest.raises(OilReadError):
        runner.sortie_oil()
    runner.campaign.handle_auto_search_exit.assert_not_called()
    runner.loop.assert_not_called()


def test_unresponsive_continue_menu_has_a_click_bound_and_short_retry():
    runner = campaign_runner()
    runner.get_oil.side_effect = OilReadError('No icon')
    runner.campaign.is_in_auto_search_menu = Mock(return_value=True)
    runner.campaign.is_in_stage = Mock(return_value=False)
    runner.campaign.handle_auto_search_exit = Mock(return_value=True)
    runner.loop = Mock(return_value=range(20))
    runner._run = Mock(side_effect=lambda *args, **kwargs: runner.triggered_stop_condition())
    runner.run('t6', folder='event_test')
    assert runner.campaign.handle_auto_search_exit.call_count == 3
    runner.config.task_delay.assert_called_once_with(minute=1)
    runner.config.task_call.assert_not_called()
    assert runner.scheduler_deferred is True
    assert runner.oil_deferred is False
    assert runner.campaign.auto_search_oil_limit_triggered is True
    assert runner.run_count == 0
    assert runner.config.StopCondition_RunCount == 1


def full_campaign_runner(oil=12435):
    """Exercise the real run loop through its pre-sortie and in-sortie guards."""
    runner = campaign_runner(oil=oil, limit=5000)
    runner.stage = 't6'
    runner.config.StopCondition_RunCount = 4
    runner.config.task = SimpleNamespace(command='Event')
    runner.config.override = Mock()
    runner.config.task_switched = Mock(return_value=False)
    runner.device = SimpleNamespace(stuck_record_clear=Mock(), click_record_clear=Mock(),
                                    has_cached_image=True, image=None)
    runner.campaign.device = SimpleNamespace(image=None)
    runner.campaign.config = SimpleNamespace(Campaign_Mode='normal', MAP_IS_ONE_TIME_STAGE=False)
    runner.campaign.event_time_limit_triggered = Mock(return_value=False)
    runner.campaign.is_in_map = Mock(return_value=False)
    runner.campaign.is_in_auto_search_menu = Mock(return_value=False)
    runner.campaign.ensure_campaign_ui = Mock()
    runner.campaign.ensure_auto_search_exit = Mock()
    runner.campaign.withdraw = Mock(side_effect=CampaignEnd('Withdraw'))
    runner.campaign.run = Mock()
    runner.event_completion = Mock()
    runner.handle_stage_name = Mock(return_value=('t6', 'event_test'))
    runner.load_campaign = Mock()
    runner.disable_raid_on_event = Mock()
    runner.handle_commission_notice = Mock()
    runner.ui_page_appear = Mock(return_value=False)
    runner.handle_end_on_loss = Mock(return_value=False)
    runner.handle_stage_retreat = Mock(return_value=False)
    runner.handle_star_runs_max = Mock(return_value=False)
    return runner


def test_unverified_pre_sortie_oil_never_enters_map_or_uses_run_quota():
    runner = full_campaign_runner()
    runner.get_oil.side_effect = OilReadError('No icon')
    runner.run('t6', folder='event_test', total=1)
    runner.campaign.run.assert_not_called()
    runner.campaign.withdraw.assert_not_called()
    assert runner.run_count == 0
    assert runner.config.StopCondition_RunCount == 4
    assert runner.oil_deferred is False
    assert runner.scheduler_deferred is True
    runner.config.task_delay.assert_called_once_with(minute=1)
    runner.config.task_call.assert_not_called()


def test_unknown_between_battle_oil_withdraws_before_short_retry_without_spending_quota():
    runner = full_campaign_runner()
    runner.campaign.run.side_effect = OilReadError('Unverified map oil')
    runner.run('t6', folder='event_test', total=1)
    runner.campaign.withdraw.assert_called_once_with(timeout=20)
    assert runner.run_count == 0
    assert runner.config.StopCondition_RunCount == 4
    assert runner.campaign.auto_search_oil_limit_triggered is False
    assert runner.oil_deferred is False
    assert runner.scheduler_deferred is True
    runner.config.task_delay.assert_called_once_with(minute=1)
    runner.config.task_call.assert_not_called()


def test_unknown_between_battle_oil_escalates_if_bounded_withdrawal_cannot_be_verified():
    runner = full_campaign_runner()
    runner.campaign.run.side_effect = OilReadError('Unverified map oil')
    runner.campaign.withdraw.side_effect = GamePageUnknownError('Map withdrawal timed out')
    with pytest.raises(GamePageUnknownError, match='withdrawal timed out'):
        runner.run('t6', folder='event_test', total=1)
    runner.campaign.withdraw.assert_called_once_with(timeout=20)
    runner.config.task_delay.assert_not_called()
    runner.config.task_call.assert_not_called()
    assert runner.run_count == 0
    assert runner.config.StopCondition_RunCount == 4


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
