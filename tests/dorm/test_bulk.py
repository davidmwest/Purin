from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from module.dorm import dorm


def controller(monkeypatch, snapshots=None):
    owner = dorm.RewardDorm.__new__(dorm.RewardDorm)
    owner.config = SimpleNamespace(Dorm_FeedFilter='20000 > 10000 > 5000 > 3000 > 2000 > 1000',
                                   Scheduler_SuccessInterval=278)
    owner.device = SimpleNamespace(image=object())
    owner._dorm_feed_click = Mock()
    owner.handle_info_bar = Mock(return_value=False)
    owner.loop = lambda **kwargs: iter(range(4))
    monkeypatch.setattr(dorm, 'Timer', lambda *a, **kw: SimpleNamespace(
        start=lambda: SimpleNamespace(reached=lambda: False)))
    if snapshots is not None:
        owner.dorm_food_get = Mock(side_effect=snapshots)
    return owner


def foods(amounts):
    return [dorm.Food(feed, amount) for feed, amount in zip(dorm.FOOD_FEED_AMOUNT, amounts)]


@pytest.mark.parametrize('counts,counter', [
    ([1] * 5, (0, 102000, 102000)),
    ([1] * 5 + [-1], (0, 102000, 102000)),
    ([1] * 5 + [None], (0, 102000, 102000)),
    ([1] * 5 + [True], (0, 102000, 102000)),
    ([1] * 6, (0, 90000, 102000)),
    ([1] * 6, (0, -1, 102000)),
    ([1] * 6, (0, 0, 0)),
    ([1] * 6, (0, 102000, None)),
    ([1] * 6, (0, 102000)),
])
def test_invalid_food_and_capacity_never_authorize_input(counts, counter, monkeypatch):
    owner = controller(monkeypatch)
    owner._dorm_has_food = Mock(return_value=True)
    owner._dorm_food_ocr = SimpleNamespace(ocr=Mock(return_value=counts))
    monkeypatch.setattr(dorm.OCR_FILL, 'ocr', Mock(return_value=counter))
    assert owner.dorm_food_get() == ([], -1)
    assert owner.dorm_feed_once() is False
    owner._dorm_feed_click.assert_not_called()


def test_six_digit_capacity_and_empty_food_icons(monkeypatch):
    owner = controller(monkeypatch)
    owner._dorm_has_food = Mock(side_effect=[True, False, True, True, True, True])
    owner._dorm_food_ocr = SimpleNamespace(ocr=Mock(return_value=[2] * 6))
    monkeypatch.setattr(dorm.OCR_FILL, 'ocr', Mock(return_value=(0, 102000, 102000)))
    found, fill = owner.dorm_food_get()
    assert [f.amount for f in found] == [2, 0, 2, 2, 2, 2]
    assert fill == 102000


def test_exact_fit_uses_owned_food_after_two_agreeing_reads(monkeypatch):
    snapshot = (foods([0, 3, 0, 0, 0, 0]), 6000)
    owner = controller(monkeypatch, [snapshot, snapshot])
    assert owner.dorm_feed_once() is True
    owner._dorm_feed_click.assert_called_once_with(button=owner._dorm_food.buttons[1], count=3)


@pytest.mark.parametrize('amounts,capacity', [([0] * 6, 102000), ([5] * 6, 0), ([0, 1, 0, 0, 0, 0], 1999)])
def test_empty_full_or_nonfitting_food_makes_no_input(amounts, capacity, monkeypatch):
    snapshot = foods(amounts), capacity
    owner = controller(monkeypatch, [snapshot, snapshot])
    assert owner.dorm_feed_once() is False
    owner._dorm_feed_click.assert_not_called()


def test_unsettled_reads_cannot_feed_on_the_final_frame(monkeypatch):
    owner = controller(monkeypatch, [(foods([5] * 6), capacity)
                                    for capacity in [102000, 101000, 100000, 99000]])
    assert owner.dorm_feed_once() is False
    owner._dorm_feed_click.assert_not_called()


@pytest.mark.parametrize('unconditional,conditional,allowed,expected', [
    (False, True, True, True), (False, True, False, False),
    (True, True, False, True), (False, False, True, False),
])
def test_conditional_gate_cannot_override_explicit_unconditional_mode(
        unconditional, conditional, allowed, expected, monkeypatch):
    from module.dorm import awakening
    gate = Mock(return_value=SimpleNamespace(can_feed=Mock(return_value=allowed)))
    monkeypatch.setattr(awakening, 'DormAwakening', gate)
    owner = controller(monkeypatch)
    for method in ('ui_ensure', 'handle_info_bar', 'ui_goto', 'dorm_feed_enter', 'dorm_feed',
                   'dorm_feed_quit', 'dorm_collect'):
        setattr(owner, method, Mock())
    assert owner.dorm_run(feed=unconditional, collect=True, feed_when_awakened=conditional) == expected
    assert owner.dorm_feed.call_count == int(expected)
    assert gate.call_count == int(conditional and not unconditional)
    owner.dorm_collect.assert_called_once()


def test_conditional_refusal_retries_in_thirty_minutes(monkeypatch):
    owner = controller(monkeypatch)
    owner.config.Dorm_Feed = False
    owner.config.Dorm_FeedWhenAwakened = True
    owner.config.Dorm_Collect = True
    owner.config.BuyFurniture_Enable = False
    owner.config.task_delay = Mock()
    owner.dorm_run = Mock(return_value=False)
    owner.get_dorm_ship_amount = Mock(return_value=6)
    owner.run()
    owner.config.task_delay.assert_called_once_with(minute=30)
