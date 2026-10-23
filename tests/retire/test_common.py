from collections import Counter
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

import module.retire.common as common
from module.base.utils import load_image
from module.dock.policy import Card
from module.exception import RequestHumanTakeover
from module.retire.assets import IN_RETIREMENT_CHECK, SHIP_CONFIRM, SHIP_CONFIRM_2
from module.retire.scheduled import RetireShips


def card(**kwargs):
    defaults = dict(index=0, read='Downes', ship='Downes', rarity='Normal', frame='Normal',
                    level=1, locked=False, fleet=0, status='free', bulin=False, retrofit=False)
    defaults.update(kwargs)
    return Card(**defaults)


def test_common_copies_do_not_reserve_future_limit_break_sets():
    cards = [card(locked=True)] + [card(index=index + 1) for index in range(5)]

    assert common.common_duplicate_targets(cards) == {'Downes': 5}


@pytest.mark.parametrize('changes', [
    {'locked': True}, {'level': 2}, {'level': 0}, {'level': 15}, {'fleet': 1},
    {'status': 'commission'}, {'status': 'battle'}, {'status': 'dorm'}, {'status': 'unknown'},
    {'rarity': 'Rare'}, {'rarity': 'Elite'}, {'rarity': 'Super Rare'}, {'frame': 'Rare'}, {'frame': ''},
    {'ship': ''}, {'read': 'Downe'}, {'bulin': True},
    {'ship': 'Downes (Retrofit)', 'read': 'Downes (Retrofit)'},
])
def test_common_candidate_preserves_every_protected_or_uncertain_card(changes):
    candidate = card(**changes)
    assert common.common_duplicate_fits(candidate, Counter({'Downes': 5})) is False


@pytest.mark.parametrize('keeper', [
    None, card(locked=False, level=50), card(locked=True, read='Downe'),
    card(locked=True, rarity='Rare'), card(locked=True, frame=''), card(locked=True, level=0),
])
def test_unlocked_last_copy_or_unverified_keeper_never_enables_retirement(keeper):
    cards = [card()] + ([keeper] if keeper is not None else [])
    assert not common.common_duplicate_targets(cards)


def test_locked_commission_keeper_is_preserved_while_idle_level_one_copy_is_eligible():
    cards = [card(locked=True, level=15, status='commission'), card(), card(level=15, status='commission')]
    assert common.common_duplicate_targets(cards) == {'Downes': 1}


def test_name_normalization_only_ignores_accents_case_and_punctuation():
    assert common.common_duplicate_targets([card(locked=True, read='DOWNES'), card(read='Downes.')]) == {'Downes': 1}


@pytest.fixture(autouse=True)
def audit_storage(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)


def task(monkeypatch, cards=None, dry_run=False):
    obj = object.__new__(RetireShips)
    obj.config = SimpleNamespace(SERVER='en', Retirement_DryRun=dry_run, config_name='f2p')
    obj.device = SimpleNamespace(image=np.zeros((720, 1280, 3), dtype=np.uint8),
                                 click=Mock(), screenshot=Mock(), sleep=Mock())
    obj.appear = Mock(side_effect=lambda button, **kwargs: button is IN_RETIREMENT_CHECK)
    obj.match_template_color = Mock(return_value=True)
    obj._common_retire_count = Mock(side_effect=[0, 0, 1, 0])
    obj._common_retire_amount = Mock(side_effect=[(151, 160), (150, 160)])
    candidate = (SimpleNamespace(button=object()), card())
    obj._common_retire_candidate = Mock(return_value=candidate)
    for name in ('ui_ensure', 'retire_page_enter', 'handle_dock_cards_loading', 'dock_favourite_set',
                 'dock_sort_method_dsc_set', 'dock_filter_set', '_retirement_confirm', '_retirement_quit',
                 '_common_retire_recover', '_common_retire_prepare'):
        setattr(obj, name, Mock())
    obj._common_retire_recover.return_value = False
    monkeypatch.setattr(common, 'load_ship_data', Mock(return_value={}))
    obj._common_retire_census = Mock(return_value=cards if cards is not None else [card(locked=True), card()])
    return obj


def test_one_card_retirement_rechecks_selection_and_dock_decrease(monkeypatch):
    obj = task(monkeypatch)

    assert obj.retire_common_duplicates() == 1

    obj.device.click.assert_called_once_with(obj._common_retire_candidate.return_value[0].button)
    obj._retirement_confirm.assert_called_once_with()
    assert obj._common_retire_count.call_count == 4
    assert obj._common_retire_amount.call_count == 2
    assert obj.dock_filter_set.call_args_list[0].kwargs['rarity'] == 'common'
    obj._retirement_quit.assert_called_once_with()


def test_dry_run_scans_and_logs_without_navigation_selection_or_confirmation(monkeypatch):
    obj = task(monkeypatch, dry_run=True)

    assert obj.retire_common_duplicates() == 0

    obj._common_retire_census.assert_called_once()
    obj.ui_ensure.assert_not_called()
    obj._common_retire_candidate.assert_not_called()
    obj.device.click.assert_not_called()
    obj._retirement_confirm.assert_not_called()


def test_no_locked_keeper_leaves_dock_untouched(monkeypatch):
    obj = task(monkeypatch, cards=[card()])

    assert obj.retire_common_duplicates() == 0

    obj.device.click.assert_not_called()
    obj._retirement_confirm.assert_not_called()


@pytest.mark.parametrize('count', [None, 1, 2])
def test_nonempty_or_unread_initial_selection_fails_before_any_card_click(monkeypatch, count):
    obj = task(monkeypatch)
    obj._common_retire_count = Mock(return_value=count)

    with pytest.raises(RequestHumanTakeover, match='verified empty selection'):
        obj.retire_common_duplicates()

    obj.device.click.assert_not_called()
    obj._retirement_confirm.assert_not_called()


@pytest.mark.parametrize('count, confirm, other_dialog', [(0, True, False), (2, True, False),
                                                      (None, True, False), (1, False, False), (1, True, True)])
def test_unverified_single_selection_never_reaches_confirmation(monkeypatch, count, confirm, other_dialog):
    obj = task(monkeypatch)
    obj._common_retire_count = Mock(side_effect=[0, 0, count])
    obj.match_template_color = Mock(return_value=confirm)
    obj.appear = Mock(side_effect=lambda button, **kwargs:
                      button is IN_RETIREMENT_CHECK or (button is SHIP_CONFIRM_2 and other_dialog))

    with pytest.raises(RequestHumanTakeover, match='exactly one selected'):
        obj.retire_common_duplicates()

    obj._retirement_confirm.assert_not_called()


@pytest.mark.parametrize('after', [(151, 160), (149, 160), (150, 170)])
def test_wrong_dock_change_stops_after_first_confirmation_without_retry(monkeypatch, after):
    obj = task(monkeypatch, cards=[card(locked=True), card(), card()])
    obj._common_retire_amount = Mock(side_effect=[(151, 160), after])

    with pytest.raises(RequestHumanTakeover, match='exactly one ship removed'):
        obj.retire_common_duplicates()

    obj._retirement_confirm.assert_called_once_with()
    obj.device.click.assert_called_once()


def test_removed_candidates_are_decremented_and_bounded_by_fresh_census(monkeypatch):
    obj = task(monkeypatch, cards=[card(locked=True)] + [card() for _ in range(3)])
    seen = []
    obj._common_retire_candidate = Mock(side_effect=lambda matcher, remaining:
                                      (seen.append(remaining.copy()) or (SimpleNamespace(button=object()), card())))
    obj._common_retire_count = Mock(side_effect=[0, 0, 1, 0] * 3)
    obj._common_retire_amount = Mock(side_effect=[(151, 160), (150, 160), (149, 160), (148, 160)])

    assert obj.retire_common_duplicates() == 3

    assert seen == [Counter({'Downes': 3}), Counter({'Downes': 2}), Counter({'Downes': 1})]
    assert obj.device.click.call_count == 3


def test_changed_candidate_fails_before_selection(monkeypatch):
    obj = task(monkeypatch)
    dock_card = SimpleNamespace(column=0, top=78, locked=False)
    walker = SimpleNamespace(cards=lambda: iter([(dock_card, 0)]),
                             reader=SimpleNamespace(is_locked=Mock(return_value=False)),
                             fleet_of=Mock(return_value=0))
    monkeypatch.setattr(common, 'DockWalker', Mock(return_value=walker))
    obj._common_retire_card = Mock(side_effect=[card(), card(level=2)])

    with pytest.raises(RequestHumanTakeover, match='card changed'):
        common.CommonDuplicates._common_retire_candidate(obj, object(), Counter({'Downes': 1}))

    obj.device.click.assert_not_called()


@pytest.mark.parametrize('reads, expected', [
    (['Selected: 0', 'ters to:'], 0), (['Obtainable: X14', 'Selected:1'], 1),
    (['obainablesi', ' selected: 2 '], 2), (['0', '1'], None), (['', ''], None),
    (['Selected: 0', 'Selected:1'], None), (['Selected: 1', ''], None),
    (['', 'Selected:0'], None), (['', 'Selected:111'], None), (['Selected:', 'None'], None),
])
def test_selection_counter_requires_exact_label_and_matching_layout(monkeypatch, reads, expected):
    obj = task(monkeypatch)
    ocr = Mock(side_effect=reads)
    monkeypatch.setattr(common, 'Ocr', Mock(return_value=SimpleNamespace(ocr=ocr)))

    assert common.CommonDuplicates._common_retire_count(obj) == expected


@pytest.mark.parametrize('image, expected', [('common_before_f2p.png', 0),
                                          ('common_selected_karlsruhe_f2p.png', 1),
                                          ('common_selected_aoba_f2p.png', 1)])
def test_actual_retirement_images_read_selected_label_instead_of_coin_rewards(monkeypatch, image, expected):
    obj = task(monkeypatch)
    obj.device.image = load_image(str(Path(__file__).parent / 'data' / image))
    # OCR models use project-relative paths; this test does not run the retirement task.
    monkeypatch.chdir(Path(__file__).resolve().parents[2])

    assert common.CommonDuplicates._common_retire_count(obj) == expected


def test_unsupported_server_fails_without_scan_or_click(monkeypatch):
    obj = task(monkeypatch)
    obj.config.SERVER = 'jp'

    with pytest.raises(RequestHumanTakeover, match='verified EN layout'):
        obj.retire_common_duplicates()

    obj._common_retire_census.assert_not_called()
    obj.device.click.assert_not_called()


def test_initial_census_is_bounded_before_any_retirement(monkeypatch):
    obj = task(monkeypatch)
    obj._common_retire_card = Mock(return_value=card())
    monkeypatch.setattr(common, 'DockWalker', Mock(return_value=SimpleNamespace(
        cards=lambda: iter([(object(), 0)] * 5001))))

    with pytest.raises(RequestHumanTakeover, match='census exceeded'):
        common.CommonDuplicates._common_retire_census(obj, object())

    assert obj._common_retire_card.call_count == 5000
    obj.device.click.assert_not_called()


def test_pass_persists_exact_names_and_verified_capacity_change(monkeypatch, tmp_path):
    obj = task(monkeypatch)

    obj.retire_common_duplicates()

    reports = list((tmp_path / 'config/retire/f2p').glob('*-common/common.json'))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text())
    assert report['complete'] and not report['dry_run']
    assert report['targets'] == {'Downes': 1}
    assert report['dock_before'] == [151, 160]
    assert report['dock_after'] == [150, 160]
    assert report['retired'] == [dict(ship='Downes', level=1, dock_before=[151, 160], dock_after=[150, 160])]


def test_audit_directory_failure_stops_before_selecting_or_confirming(monkeypatch):
    obj = task(monkeypatch)
    monkeypatch.setattr(common.Path, 'mkdir', Mock(side_effect=OSError('storage unavailable')))

    with pytest.raises(OSError, match='storage unavailable'):
        obj.retire_common_duplicates()

    obj.device.click.assert_not_called()
    obj._retirement_confirm.assert_not_called()


@pytest.mark.parametrize('fail_at, expected_picks', [(1, 0), (2, 1)])
def test_audit_image_failure_recovers_without_confirming(monkeypatch, fail_at, expected_picks):
    obj = task(monkeypatch)
    monkeypatch.setattr(common, 'save_image', Mock(side_effect=[None] * (fail_at - 1) + [OSError('image failure')]))

    with pytest.raises(OSError, match='image failure'):
        obj.retire_common_duplicates()

    assert obj.device.click.call_count == expected_picks
    obj._common_retire_recover.assert_called_once_with()
    obj._retirement_confirm.assert_not_called()


def test_walker_screenshot_budget_stops_even_if_no_cards_are_ever_yielded(monkeypatch):
    import module.retire.dock_walk as dock_walk
    obj = task(monkeypatch)
    monkeypatch.setattr(dock_walk, 'DockReader', Mock(return_value=SimpleNamespace(rows=Mock())))
    monkeypatch.setattr(dock_walk, 'FleetScanner', Mock())
    walker = dock_walk.DockWalker(obj, max_screens=2)
    walker.list_top = Mock()
    walker.drag = Mock()
    walker.reader.rows = Mock(return_value=[SimpleNamespace(top=100, cards=[], signature=object())])
    monkeypatch.setattr(dock_walk, 'find_row', Mock(return_value=0))

    with pytest.raises(RequestHumanTakeover, match='screenshot budget'):
        list(walker.cards())

    assert obj.device.screenshot.call_count == 2
    obj.device.click.assert_not_called()


def test_failed_selection_cancels_the_card_then_restores_filter_without_confirming(monkeypatch):
    obj = task(monkeypatch)
    obj._common_retire_count = Mock(side_effect=[1, 0])

    assert common.CommonDuplicates._common_retire_recover(obj) is True

    obj.device.click.assert_called_once_with(IN_RETIREMENT_CHECK)
    obj._retirement_confirm.assert_not_called()
    obj.dock_filter_set.assert_called_once_with()
    obj._retirement_quit.assert_called_once_with()


def test_recovery_cancels_a_known_modal_before_touching_the_underlying_retirement_page(monkeypatch):
    obj = task(monkeypatch)
    modal = {'open': True}
    obj.appear = Mock(side_effect=lambda button, **kwargs:
                      modal['open'] if button is common.POPUP_CANCEL or button is SHIP_CONFIRM_2 else
                      not modal['open'] if button is IN_RETIREMENT_CHECK else False)
    obj.device.click = Mock(side_effect=lambda button: modal.update(open=False))
    obj._common_retire_count = Mock(return_value=0)

    assert common.CommonDuplicates._common_retire_recover(obj) is True

    obj.device.click.assert_called_once_with(common.POPUP_CANCEL)
    obj._retirement_confirm.assert_not_called()


def test_recovery_does_not_click_unknown_ui_or_modal_without_cancel(monkeypatch):
    obj = task(monkeypatch)
    obj.appear = Mock(side_effect=lambda button, **kwargs: button is SHIP_CONFIRM_2)

    assert common.CommonDuplicates._common_retire_recover(obj) is False

    obj.device.click.assert_not_called()
    obj.dock_filter_set.assert_not_called()
    obj._retirement_confirm.assert_not_called()


def test_recovery_is_bounded_when_cancel_does_not_clear_selection(monkeypatch):
    obj = task(monkeypatch)
    obj._common_retire_count = Mock(return_value=1)

    assert common.CommonDuplicates._common_retire_recover(obj) is False

    assert obj.device.click.call_count == 3
    assert all(call.args[0] is IN_RETIREMENT_CHECK for call in obj.device.click.call_args_list)
    obj._retirement_confirm.assert_not_called()


@pytest.mark.parametrize('dry_run', [False, True])
def test_scheduled_common_pass_runs_after_the_conservative_selection_and_quit(monkeypatch, dry_run):
    obj = task(monkeypatch, dry_run=dry_run)
    obj.config.Retirement_RetireMode = 'one_click_retire'
    obj.config.Retirement_CommonDuplicates = True
    obj.config.override = Mock()
    obj.config.task_delay = Mock()
    obj.retire_ships_one_click = Mock(return_value=0)
    obj.quick_retire_setting_set = Mock()
    obj.retire_common_duplicates = Mock(return_value=2)
    events = Mock()
    events.attach_mock(obj._common_retire_prepare, 'prepare')
    events.attach_mock(obj.retire_ships_one_click, 'quick')
    events.attach_mock(obj._retirement_quit, 'quit')
    events.attach_mock(obj.retire_common_duplicates, 'common')

    obj.run()

    assert [call[0] for call in events.mock_calls] == ['prepare', 'quick', 'quit', 'common']
    obj.config.task_delay.assert_called_once_with(server_update=True)


def test_startup_cancels_stale_selection_before_any_confirmation(monkeypatch):
    obj = task(monkeypatch)
    obj._common_retire_recover = lambda: common.CommonDuplicates._common_retire_recover(obj)
    obj._common_retire_count = Mock(side_effect=[1, 0])

    common.CommonDuplicates._common_retire_prepare(obj)

    obj.device.click.assert_called_once_with(IN_RETIREMENT_CHECK)
    obj._retirement_quit.assert_called_once_with()
    obj._retirement_confirm.assert_not_called()


def test_startup_cannot_continue_if_stale_selection_does_not_clear(monkeypatch):
    obj = task(monkeypatch)
    obj._common_retire_recover = lambda: common.CommonDuplicates._common_retire_recover(obj)
    obj._common_retire_count = Mock(return_value=1)

    with pytest.raises(RequestHumanTakeover, match='safely clear the previous selection'):
        common.CommonDuplicates._common_retire_prepare(obj)

    assert obj.device.click.call_count == 3
    obj._retirement_confirm.assert_not_called()


def test_startup_on_ordinary_page_does_not_click_or_recover(monkeypatch):
    obj = task(monkeypatch)
    obj.appear = Mock(return_value=False)

    common.CommonDuplicates._common_retire_prepare(obj)

    obj.device.click.assert_not_called()
    obj._common_retire_recover.assert_not_called()


def test_atomic_audit_exists_before_confirm_and_after_every_verified_removal(monkeypatch, tmp_path):
    obj = task(monkeypatch, cards=[card(locked=True), card(), card()])
    obj._common_retire_count = Mock(side_effect=[0, 0, 1, 0] * 2)
    obj._common_retire_amount = Mock(side_effect=[(151, 160), (150, 160), (149, 160)])
    observed = []

    def confirm():
        reports = list((tmp_path / 'config/retire/f2p').glob('*-common/common.json'))
        report = json.loads(reports[0].read_text())
        observed.append(report)
        assert report['pending']['ship'] == 'Downes'
        assert not report['complete']
        assert not reports[0].with_name('common.json.tmp').exists()

    obj._retirement_confirm = Mock(side_effect=confirm)

    assert obj.retire_common_duplicates() == 2

    assert [len(report['retired']) for report in observed] == [0, 1]
    assert observed[1]['dock_after'] == [150, 160]
    assert observed[1]['remaining'] == {'Downes': 1}


def test_interruption_before_second_confirmation_keeps_first_verified_retirement(monkeypatch, tmp_path):
    obj = task(monkeypatch, cards=[card(locked=True), card(), card()])
    obj._common_retire_count = Mock(side_effect=[0, 0, 1, 0] * 2)
    obj._common_retire_amount = Mock(side_effect=[(151, 160), (150, 160)])
    obj._retirement_confirm = Mock(side_effect=[None, KeyboardInterrupt()])

    with pytest.raises(KeyboardInterrupt):
        obj.retire_common_duplicates()

    report = json.loads(next((tmp_path / 'config/retire/f2p').glob('*-common/common.json')).read_text())
    assert report['retired'] == [dict(ship='Downes', level=1, dock_before=[151, 160], dock_after=[150, 160])]
    assert report['pending'] == dict(ship='Downes', level=1, dock_before=[150, 160])
    assert not report['complete']


def test_failed_pending_audit_cancels_before_confirming(monkeypatch):
    obj = task(monkeypatch)
    obj._common_retire_audit = Mock(side_effect=[None, None, OSError('audit unavailable'), None])

    with pytest.raises(OSError, match='audit unavailable'):
        obj.retire_common_duplicates()

    obj._common_retire_recover.assert_called_once_with()
    obj._retirement_confirm.assert_not_called()
