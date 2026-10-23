import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest
from PIL import Image

import module.config.server as server
from module.base.button import Button
from module.combat.assets import GET_ITEMS_1
from module.dorm import daily
from module.dorm.assets import DORM_BUY_FOOD_CHECK, DORM_FEED_CHECK
from module.exception import GameNotRunningError, ScriptError
from module.ui.page import page_main, page_mission

DATA = Path(__file__).parent / 'data'
PERIOD = datetime(2026, 10, 23, 4).isoformat()


class ImmediateTimer:
    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        return self

    def reset(self):
        return self

    def reached(self):
        return True


def screenshot():
    return np.array(Image.open(DATA / 'restock_daily.png').convert('RGB'))


def record(**updates):
    value = dict(period=PERIOD, version=daily.VERSION, food_clicks=0, claim_clicks=0,
                 dialogs=0, receipt_seen=False, mission_reward_claimed=False,
                 diagnostics=[], status='blocked', reason='mission_not_verified')
    value.update(updates)
    return value


def controller(monkeypatch, account='alas'):
    owner = daily.DormDaily.__new__(daily.DormDaily)
    owner.config = SimpleNamespace(config_name=account, Scheduler_ServerUpdate='04:00', task_delay=Mock())
    owner.device = SimpleNamespace(image=screenshot(), click=Mock(), swipe=Mock(), screenshot=Mock())
    owner._period = Mock(return_value=PERIOD)
    owner._capture = Mock()
    owner._receipt = Mock(return_value=None)
    owner._daily_list = Mock(return_value=True)
    owner._reward_get_state = Mock(return_value=object())
    owner.reward_side_navbar_ensure = Mock(return_value=True)
    owner.loop = lambda **kwargs: iter(range(12))
    monkeypatch.setattr(daily, 'Timer', ImmediateTimer)
    monkeypatch.setattr(daily, 'ensure_event_page', Mock(return_value=True))
    monkeypatch.setattr(daily, 'ensure_event_running', Mock())
    return owner


def current(tmp_path, account='alas'):
    return daily.load_state(account, tmp_path)[f'{PERIOD}:{daily.VERSION}']


def earned_composite():
    # Composition only: generic earned controls are from an actual Daily row;
    # no assertion that the restock mission has already been earned live.
    image = screenshot()
    other = np.array(Image.open(Path(__file__).parents[1] / 'gacha/data/build_three_main.png').convert('RGB'))
    image[164:182, 523:660] = other[164:182, 523:660]
    image[142:172, 1118:1224] = other[142:172, 1118:1224]
    return image


def test_actual_daily_row_authorizes_only_exact_uncredited_restock():
    assert daily.locate_restock(screenshot())['title_y'] == 130
    assert daily.read_restock(screenshot()) == dict(kind='unfinished', current=0)


def test_actual_daily_sidebar_requires_all_six_known_tabs(monkeypatch):
    owner = controller(monkeypatch)
    del owner._daily_list
    owner.ui_page_appear = Mock(return_value=True)
    assert owner._reward_side_navbar.get_info(owner) == (3, 0, 5)
    assert owner._daily_list()
    # A cropped/missing last tab is not fresh proof of this known layout.
    owner.device.image[590:680, :100] = 0
    assert not owner._daily_list()


@pytest.mark.parametrize('area', [(258, 130, 598, 149), (176, 89, 193, 151), (616, 161, 660, 183)])
def test_missing_title_daily_marker_or_counter_never_authorizes_food(area, monkeypatch):
    image = screenshot()
    x1, y1, x2, y2 = area
    image[y1:y2, x1:x2] = 0
    monkeypatch.setattr(daily.Ocr, 'ocr', Mock(return_value='1'))
    assert daily.read_restock(image) not in (dict(kind='unfinished', current=0), dict(kind='earned'))


def test_changed_counter_cannot_reuse_zero_witness(monkeypatch):
    image = screenshot()
    # Replace square zero with the captured trailing one, leaving /1 intact.
    image[164:181, 619:633] = 63
    image[164:181, 623:631] = screenshot()[164:181, 646:654]
    monkeypatch.setattr(daily.Ocr, 'ocr', Mock(return_value='1/1'))
    assert daily.read_restock(image) == dict(kind='unfinished', current=1)


def test_duplicate_or_partially_hidden_target_is_not_selected():
    image = screenshot()
    image[289:351, 176:193] = image[89:151, 176:193]
    image[330:349, 258:598] = image[130:149, 258:598]
    assert daily.locate_restock(image) is None
    assert daily.locate_restock(screenshot()[:600]) is None


def test_exact_earned_target_controls_share_its_row():
    image = earned_composite()
    assert daily.read_restock(image)['kind'] == 'earned'
    image[164:182, 523:660] = 0
    assert daily.read_restock(image)['kind'] != 'earned'
    image = earned_composite()
    image[142:172, 1118:1224] = screenshot()[142:172, 1118:1224]
    assert daily.read_restock(image)['kind'] != 'earned'


@pytest.mark.parametrize('amounts,capacity,expected', [
    ([9, 2, 3, 4, 5, 6], 2000, 1),  # Secret Coolant before Oxy-cola.
    ([9, 2, 3, 4, 5, 6], 1999, 0),
    ([0, 0, 1, 2, 3, 4], 3000, 2),
    ([0, 1, 0, 0, 0, 0], 1999, None),
    ([0, 0, 0, 0, 0, 0], 90000, None),
    ([1, 2, 3, 4, 5, 6], 0, None),
    ([1, 2, 3, 4, 5, 6], -1, None),
    ([1, 2, 3, 4, 5, 6], 0.5, None),
    ([1, 2, 3, 4, 5, True], 90000, None),
    ([1, 2, -1, 4, 5, 6], 90000, None),
    (None, 90000, None),
])
def test_one_owned_food_preference_and_capacity(amounts, capacity, expected):
    assert daily.choose_food(amounts, capacity) == expected


def test_feed_reserves_before_exactly_one_raw_click_and_captures(monkeypatch):
    owner = controller(monkeypatch)
    owner._feed_panel = Mock(return_value=True)
    owner.appear = Mock(side_effect=lambda button, **kwargs: button is DORM_FEED_CHECK)
    owner._food_values = Mock(return_value=((5, 2, 0, 0, 0, 0), 2000))
    value, saved = record(), []
    persist = lambda: saved.append(dict(value))
    def click(button):
        assert saved[-1]['food_clicks'] == 1
        assert saved[-1]['food_identity'] == 'Secret Coolant'
        assert button is owner._dorm_food.buttons[1]
    owner.device.click.side_effect = click
    owner._feed_one(value, persist)
    assert value['food_index'] == 1 and value['food_feed'] == 2000
    owner.device.click.assert_called_once()
    assert owner._food_values.call_count == 3
    owner._capture.assert_any_call(value, 'food_before')
    owner._capture.assert_any_call(value, 'food_clicked')
    owner.device.screenshot.assert_called_once()
    owner._feed_one(value, persist)
    owner.device.click.assert_called_once()


@pytest.mark.parametrize('snapshot', [None, ((0, 0, 0, 0, 0, 0), 2000), ((5, 2, 0, 0, 0, 0), 0)])
def test_full_empty_or_unreadable_never_uses_food(monkeypatch, snapshot):
    owner = controller(monkeypatch)
    owner._feed_panel = Mock(return_value=True)
    owner.appear = Mock(side_effect=lambda button, **kwargs: button is DORM_FEED_CHECK)
    owner._food_values = Mock(return_value=snapshot)
    value = record()
    owner._feed_one(value, Mock())
    owner.device.click.assert_not_called()
    assert value['food_clicks'] == 0


def test_unstable_food_never_uses_food(monkeypatch):
    owner = controller(monkeypatch)
    owner._feed_panel = Mock(return_value=True)
    owner.appear = Mock(side_effect=lambda button, **kwargs: button is DORM_FEED_CHECK)
    owner._food_values = Mock(side_effect=[((i, 2, 0, 0, 0, 0), 2000) for i in range(12)])
    owner._feed_one(record(), Mock())
    owner.device.click.assert_not_called()


@pytest.mark.parametrize('cross_after_persist', [False, True])
def test_reset_crossing_cannot_feed(monkeypatch, cross_after_persist):
    owner = controller(monkeypatch)
    owner._feed_panel = Mock(return_value=True)
    owner.appear = Mock(side_effect=lambda button, **kwargs: button is DORM_FEED_CHECK)
    owner._food_values = Mock(return_value=((5, 2, 0, 0, 0, 0), 2000))
    owner._period.side_effect = [PERIOD, 'next'] if cross_after_persist else ['next']
    value = record()
    owner._feed_one(value, Mock())
    owner.device.click.assert_not_called()
    assert value['food_clicks'] == int(cross_after_persist)


def test_atomic_failure_before_food_prevents_input(monkeypatch):
    owner = controller(monkeypatch)
    owner._feed_panel = Mock(return_value=True)
    owner.appear = Mock(side_effect=lambda button, **kwargs: button is DORM_FEED_CHECK)
    owner._food_values = Mock(return_value=((5, 2, 0, 0, 0, 0), 2000))
    with pytest.raises(OSError):
        owner._feed_one(record(), Mock(side_effect=OSError('disk full')))
    owner.device.click.assert_not_called()


def test_food_shop_is_vetoed_without_confirmation(monkeypatch):
    owner = controller(monkeypatch)
    owner.appear = Mock(return_value=True)
    assert not owner._feed_panel(True)
    owner.device.click.assert_not_called()


@pytest.mark.parametrize('opened', [False, True])
def test_captured_dorm_xp_summary_is_dismissed_before_panel_detection(monkeypatch, opened):
    previous_server = server.server
    server.set_server('en')
    try:
        owner = controller(monkeypatch)
        actual = np.array(Image.open(DATA / 'dorm_info.png').convert('RGB'))
        phase = ['summary']
        def appear(button, **kwargs):
            if phase[0] == 'summary':
                return button.match(actual, offset=kwargs.get('offset', 0), similarity=.85)
            return button is (DORM_FEED_CHECK if phase[0] == 'food' else daily.DORM_CHECK)
        def click(button):
            if button is daily.DORM_INFO:
                phase[0] = 'dorm'
            elif button is daily.DORM_FEED_ENTER:
                phase[0] = 'food'
            else:
                pytest.fail('Only exact Dorm summary or food-toggle input is allowed')
        owner.appear = Mock(side_effect=appear)
        owner.device.click.side_effect = click
        assert owner._feed_panel(opened)
        expected = [daily.DORM_INFO, daily.DORM_FEED_ENTER] if opened else [daily.DORM_INFO]
        assert [call.args[0] for call in owner.device.click.call_args_list] == expected
        actual[590:665, 1040:1175] = 0
        assert not daily.DORM_INFO.match(actual, offset=(30, 30), similarity=.85)
    finally:
        server.set_server(previous_server)


def test_persistent_dorm_summary_has_bounded_confirm_and_no_background_toggle(monkeypatch):
    owner = controller(monkeypatch)
    owner.appear = Mock(side_effect=lambda button, **kwargs: button in (daily.DORM_INFO, daily.DORM_CHECK))
    assert not owner._feed_panel(True)
    assert [call.args[0] for call in owner.device.click.call_args_list] == [daily.DORM_INFO] * 2


def test_unowned_receipt_precedes_summary_shop_and_background_panel_detection(monkeypatch):
    owner = controller(monkeypatch)
    owner._receipt.return_value = GET_ITEMS_1
    owner.appear = Mock(return_value=True)
    assert not owner._feed_panel(True)
    owner.appear.assert_not_called()
    owner.device.click.assert_not_called()


@pytest.mark.parametrize('fill', [(0, 0, 0), (40000, 1, 40000), (0, 100000, 100000), (1, 2), ('0', 40000, 40000)])
def test_unknown_or_invalid_capacity_fails_closed(monkeypatch, fill):
    owner = controller(monkeypatch)
    owner._dorm_food_ocr = SimpleNamespace(ocr=Mock(return_value=[1] * 6))
    monkeypatch.setattr(daily.OCR_FILL, 'ocr', Mock(return_value=fill))
    assert owner._food_values() is None


def test_food_counts_filter_grayed_empty_slots_and_validate_total(monkeypatch):
    owner = controller(monkeypatch)
    owner._dorm_food_ocr = SimpleNamespace(ocr=Mock(return_value=[4, 2, 0, 9, 0, 0]))
    owner._dorm_has_food = Mock(side_effect=[True, True, False, False, False, False])
    monkeypatch.setattr(daily.OCR_FILL, 'ocr', Mock(return_value=(38000, 2000, 40000)))
    assert owner._food_values() == ((4, 2, 0, 0, 0, 0), 2000)


def test_list_scan_is_bounded_and_only_proven_daily_list_can_scroll(monkeypatch):
    owner = controller(monkeypatch)
    owner._read_mission_view = Mock(side_effect=[None, None, dict(kind='unfinished', current=0)])
    assert owner._mission() == dict(kind='unfinished', current=0)
    assert owner.device.swipe.call_count == 2
    owner._read_mission_view = Mock(return_value=None)
    owner.device.swipe.reset_mock()
    assert owner._mission() is None
    assert owner.device.swipe.call_count == 9
    owner._daily_list.return_value = False
    owner.device.swipe.reset_mock()
    assert owner._mission() is None
    owner.device.swipe.assert_not_called()


def test_receipt_blocks_list_scroll(monkeypatch):
    owner = controller(monkeypatch)
    owner._read_mission_view = Mock(return_value=None)
    owner._receipt.return_value = GET_ITEMS_1
    assert owner._mission() is None
    owner.device.screenshot.assert_called_once()
    daily.ensure_event_page.assert_not_called()
    owner.reward_side_navbar_ensure.assert_not_called()
    owner._read_mission_view.assert_not_called()
    owner.device.swipe.assert_not_called()


def test_claim_reserves_input_receipt_first_then_stable_absence(monkeypatch):
    owner = controller(monkeypatch)
    value, saved = record(), []
    persist = lambda: saved.append(dict(value))
    button = Button((1118, 142, 1224, 172), (0, 0, 0), (1118, 142, 1224, 172), name='RESTOCK_CLAIM')
    frames = {'receipt': False, 'after': False}
    def loop(**kwargs):
        for index in range(7):
            frames.update(receipt=index == 3, after=index > 3)
            yield
    owner.loop = loop
    # Background Daily marker deliberately remains true beneath receipt.
    owner._receipt.side_effect = lambda: GET_ITEMS_1 if frames['receipt'] else None
    monkeypatch.setattr(daily, 'read_restock', Mock(side_effect=lambda image:
        None if frames['after'] else dict(kind='earned', button=button)))
    def click(target):
        assert saved[-1]['claim_clicks'] == 1
        if target is GET_ITEMS_1:
            assert saved[-1]['receipt_seen'] and saved[-1]['dialogs'] == 1
    owner.device.click.side_effect = click
    owner._claim(value, persist)
    assert value['mission_reward_claimed'] and value['status'] == 'complete'
    assert owner.device.click.call_args_list[0].args == (button,)
    assert owner.device.click.call_args_list[1].args == (GET_ITEMS_1,)


@pytest.mark.parametrize('prior', [record(), record(claim_clicks=1), record(claim_clicks=1, receipt_seen=True)])
def test_no_row_alone_or_unknown_list_never_verifies_claim(monkeypatch, prior):
    owner = controller(monkeypatch)
    monkeypatch.setattr(daily, 'read_restock', Mock(return_value=None))
    owner._reward_get_state.return_value = None
    owner._claim(prior, Mock())
    assert not prior['mission_reward_claimed']
    owner.device.click.assert_not_called()


def test_earned_claim_does_not_repeat_after_restart(monkeypatch):
    owner = controller(monkeypatch)
    monkeypatch.setattr(daily, 'read_restock', Mock(return_value=dict(kind='earned', button=object())))
    prior = record(claim_clicks=1)
    owner._claim(prior, Mock())
    owner.device.click.assert_not_called()
    assert not prior['mission_reward_claimed']


def test_non_daily_frame_resets_settled_claim_verification(monkeypatch):
    owner = controller(monkeypatch)
    prior = record(claim_clicks=1, receipt_seen=True)
    owner.loop = lambda **kwargs: iter(range(5))
    owner._daily_list.side_effect = [True, True, False, True, True]
    monkeypatch.setattr(daily, 'read_restock', Mock(return_value=None))
    owner._claim(prior, Mock())
    assert not prior['mission_reward_claimed']


@pytest.mark.parametrize('account', ['alas', 'f2p'])
def test_both_accounts_recover_pending_food_without_a_second_snack(monkeypatch, tmp_path, account):
    owner = controller(monkeypatch, account)
    prior = record(food_clicks=1)
    daily.save_state(account, {f'{PERIOD}:{daily.VERSION}': prior}, tmp_path)
    owner._mission = Mock(return_value=dict(kind='unfinished', current=0))
    owner._feed_one = Mock()
    result = owner.run(folder=tmp_path)
    assert result['food_clicks'] == 1 and not result['mission_reward_claimed']
    owner._feed_one.assert_not_called()
    owner.device.click.assert_not_called()
    owner.config.task_delay.assert_called_once_with(minute=30)


def test_pending_claim_resumes_before_any_mission_navigation(monkeypatch, tmp_path):
    owner = controller(monkeypatch)
    prior = record(food_clicks=1, claim_clicks=1)
    daily.save_state('alas', {f'{PERIOD}:{daily.VERSION}': prior}, tmp_path)
    def claim(value, persist):
        value.update(receipt_seen=True, mission_reward_claimed=True, status='complete')
        persist()
    owner._claim = Mock(side_effect=claim)
    owner._mission = Mock()
    owner._feed_one = Mock()
    result = owner.run(folder=tmp_path)
    assert result['mission_reward_claimed']
    owner._mission.assert_not_called()
    owner._feed_one.assert_not_called()
    owner.config.task_delay.assert_called_once_with(server_update=True)


def test_completed_period_is_no_input_and_next_period_is_independent(monkeypatch, tmp_path):
    owner = controller(monkeypatch)
    prior = record(food_clicks=1, claim_clicks=1, receipt_seen=True, mission_reward_claimed=True, status='complete')
    daily.save_state('alas', {f'{PERIOD}:{daily.VERSION}': prior}, tmp_path)
    owner._mission = Mock(return_value=None)
    assert owner.run(folder=tmp_path)['mission_reward_claimed']
    daily.ensure_event_running.assert_not_called()
    owner._mission.assert_not_called()
    owner._period.return_value = (datetime.fromisoformat(PERIOD) + timedelta(days=1)).isoformat()
    assert not owner.run(folder=tmp_path)['mission_reward_claimed']
    owner._mission.assert_called_once()
    assert len(daily.load_state('alas', tmp_path)) == 2


@pytest.mark.parametrize('updates', [dict(food_clicks=2), dict(food_clicks=True), dict(receipt_seen=True),
                                    dict(mission_reward_claimed=True), dict(period='wrong'), dict(diagnostics=None)])
def test_corrupt_budget_or_proof_stops_before_any_navigation(monkeypatch, tmp_path, updates):
    owner = controller(monkeypatch)
    daily.save_state('alas', {f'{PERIOD}:{daily.VERSION}': record(**updates)}, tmp_path)
    with pytest.raises(ScriptError):
        owner.run(folder=tmp_path)
    daily.ensure_event_running.assert_not_called()
    daily.ensure_event_page.assert_not_called()
    owner.device.click.assert_not_called()


def test_malformed_json_and_wrong_shape_fail_closed(tmp_path):
    path = tmp_path / 'alas-dorm-daily.json'
    for text in ('{bad', '[]', '{"x":1}'):
        path.write_text(text)
        with pytest.raises(ScriptError):
            daily.load_state('alas', tmp_path)


def test_closed_game_does_not_consume_food_or_mission_budget(monkeypatch, tmp_path):
    owner = controller(monkeypatch)
    daily.ensure_event_running.side_effect = GameNotRunningError
    with pytest.raises(GameNotRunningError):
        owner.run(folder=tmp_path)
    assert not (tmp_path / 'alas-dorm-daily.json').exists()
    owner.device.click.assert_not_called()


def test_config_and_dispatch_are_independent_of_bulk_dorm_feed(monkeypatch):
    import alas
    from module.config.config_manual import ManualConfig
    template = json.loads(Path('config/template.json').read_text())
    assert template['DormDaily']['Scheduler']['Enable'] is False
    assert template['DormDaily']['Scheduler']['ServerUpdate'] == '00:00'
    assert 'Dorm' not in template['DormDaily']
    assert ManualConfig.SCHEDULER_PRIORITY.index('DormDaily') < ManualConfig.SCHEDULER_PRIORITY.index('> Reward')
    job = Mock()
    monkeypatch.setattr(daily, 'DormDaily', Mock(return_value=job))
    scheduler = SimpleNamespace(config=object(), device=object())
    alas.AzurLaneAutoScript.dorm_daily(scheduler)
    daily.DormDaily.assert_called_once_with(config=scheduler.config, device=scheduler.device)
    job.run.assert_called_once_with()
