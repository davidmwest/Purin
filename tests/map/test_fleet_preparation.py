from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from module.exception import HardNotSatisfied
from module.map.assets import FLEET_1_ADVICE
from module.map.map_fleet_preparation import FleetOperator


@pytest.fixture(autouse=True)
def diagnostic(monkeypatch):
    capture = Mock(return_value='/log/event_completion/hard_lineup.png')
    monkeypatch.setattr('module.eventstory.eventstory.save_event_diagnostic', capture)
    return capture


def make_fleet(satisfied):
    """
    Args:
        satisfied (list): What is_hard_satisfied() answers on each screenshot.
    """
    main = SimpleNamespace(
        loop=Mock(side_effect=lambda **kwargs: iter(range(len(satisfied)))),
        device=SimpleNamespace(click=Mock()),
        config=SimpleNamespace(Campaign_Name='sp', config_name='alas'),
    )
    fleet = FleetOperator.__new__(FleetOperator)
    fleet.main = main
    fleet._advice = FLEET_1_ADVICE
    fleet._choose = 'FLEET_1_CHOOSE'
    fleet.is_hard_satisfied = Mock(side_effect=satisfied)
    fleet.capture_hard_lineup = Mock(wraps=fleet.capture_hard_lineup)
    return fleet


def test_advise_fills_a_hard_fleet_that_has_no_ships():
    fleet = make_fleet([False, True])

    assert fleet.advise() is True

    fleet.main.device.click.assert_called_once_with(FLEET_1_ADVICE)
    assert fleet.capture_hard_lineup.call_count == 2  # before advice and verified selected lineup
    fleet.capture_hard_lineup.assert_called_with('recommended')
    fleet.main.loop.assert_called_once_with(timeout=20)


def test_advise_leaves_a_prepared_hard_fleet_alone():
    fleet = make_fleet([True])

    assert fleet.advise() is True

    fleet.main.device.click.assert_not_called()
    fleet.capture_hard_lineup.assert_not_called()


def test_advise_does_nothing_outside_hard_mode():
    fleet = make_fleet([None])

    assert fleet.advise() is None

    fleet.main.device.click.assert_not_called()
    fleet.capture_hard_lineup.assert_not_called()


def test_unprepared_hard_fleet_is_captured_before_human_takeover():
    fleet = make_fleet([False])

    with pytest.raises(HardNotSatisfied):
        fleet.raise_hard_not_satisfied()

    fleet.main.device.click.assert_not_called()
    fleet.capture_hard_lineup.assert_called_once_with('not satisfied')


def test_recommendation_waits_for_verified_hard_state_after_animation():
    fleet = make_fleet([False, None, True])

    assert fleet.advise() is True

    fleet.main.device.click.assert_called_once_with(FLEET_1_ADVICE)
    assert fleet.capture_hard_lineup.call_count == 2


def test_failed_advice_guard_does_not_fall_through_on_unknown_frame():
    fleet = make_fleet([None])

    with pytest.raises(HardNotSatisfied):
        fleet.raise_hard_not_satisfied(force=True)

    fleet.is_hard_satisfied.assert_not_called()
    fleet.main.device.click.assert_not_called()


def test_failed_recommendation_has_a_three_click_cap_and_capture(monkeypatch):
    # Avoid waiting for the normal three-second spacing; the production loop still applies it.
    timer = SimpleNamespace(reached=lambda: True, reset=lambda: None)
    monkeypatch.setattr('module.map.map_fleet_preparation.Timer', lambda *args, **kwargs: timer)
    fleet = make_fleet([False] * 8)

    assert fleet.advise() is False

    assert fleet.main.device.click.call_count == 3
    assert fleet.capture_hard_lineup.call_count == 2
    fleet.main.loop.assert_called_once_with(timeout=20)


def test_recommendation_timeout_captures_and_fails_closed():
    fleet = make_fleet([])

    assert fleet.advise() is False

    fleet.main.device.click.assert_not_called()
    fleet.capture_hard_lineup.assert_called_once_with('recommendation timed out')


def test_hard_lineup_capture_uses_existing_diagnostic_writer(diagnostic):
    fleet = make_fleet([])

    path = fleet.capture_hard_lineup('recommended')

    assert path.endswith('hard_lineup.png')
    diagnostic.assert_called_once()
    assert diagnostic.call_args.args[0] is fleet.main.device
    assert diagnostic.call_args.args[1].startswith('alas_sp_')
    assert diagnostic.call_args.args[1].endswith('_hard_lineup_recommended')
