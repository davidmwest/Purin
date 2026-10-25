from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import module.dorm.awakening as awakening
from module.dorm.awakening import (DormAwakening, census_key, level_can_gain_exp, needs_exp_read,
                                   same_residents)
from module.mood.dorm_rest import DormReadError, DormShip
from module.ui.page import page_dock, page_dorm


def ship(name='Haruna', level=101, place='train'):
    return DormShip(place=place, name=name, level=level, mood=150, speed=40,
                    sig=list(range(144)))


@pytest.fixture
def gate():
    result = object.__new__(DormAwakening)
    result.device = Mock(image=object())
    result.ui_ensure = Mock()
    result.dorm_ship_list_enter = Mock()
    result.dorm_ship_list_quit = Mock()
    result.dorm_ship_list_set_tab = Mock()
    return result


@pytest.mark.parametrize('level', [101, 104, 106, 109, 111, 114, 116, 119, 121, 124])
def test_levels_that_prove_room_for_exp(level):
    assert level_can_gain_exp(level)
    assert not needs_exp_read(level)


@pytest.mark.parametrize('level', [100, 105, 110, 115, 120])
def test_awakening_boundaries_require_live_exp(level):
    assert needs_exp_read(level)
    assert not level_can_gain_exp(level)


@pytest.mark.parametrize('level', [1, 11, 70, 91, 99])
def test_lower_levels_require_fresh_info_instead_of_guessing_a_missing_digit(level):
    assert needs_exp_read(level)
    assert not level_can_gain_exp(level)


@pytest.mark.parametrize('level', [None, 0, -1, 1, 11, 70, 99, 125, 126, True, '101'])
def test_unknown_or_capped_levels_do_not_authorize_food(level):
    assert not level_can_gain_exp(level)


@pytest.mark.parametrize('residents,count', [
    ([ship()], (2, 4, 6)),
    ([ship()], (1, 4, 6)),
    ([ship()], (1, 0, 0)),
    ([ship()], (1, 6, 7)),
    ([ship()], ('1', 5, 6)),
    ([ship(level=0)], (1, 5, 6)),
    ([ship(name='')], (1, 5, 6)),
    ([ship(level=126)], (1, 5, 6)),
])
def test_incomplete_or_unknown_census_is_rejected(residents, count):
    assert census_key(residents, count) is None


def test_explicit_empty_floor_can_be_read():
    assert census_key([], (0, 6, 6)) is not None


def test_known_two_character_ship_names_are_valid_but_noise_is_not():
    assert census_key([ship('Z1')], (1, 5, 6)) is not None
    assert census_key([ship('qq')], (1, 5, 6)) is None


def test_census_needs_two_new_agreeing_frames(gate, monkeypatch):
    reads = Mock(side_effect=[([ship(level=100)], 6), ([ship(level=101)], 6), ([ship(level=101)], 6)])
    monkeypatch.setattr(awakening, 'read_dorm_floor', reads)
    monkeypatch.setattr(awakening.OCR_DORM_SHIP_COUNT, 'ocr', Mock(return_value=(1, 5, 6)))
    result = gate._food_floor('train')
    assert result[0].level == 101
    assert gate.device.screenshot.call_count == 3
    gate.dorm_ship_list_set_tab.assert_called_once_with('train')


def test_scrolling_names_can_agree_with_stable_portrait_level_and_counts(gate, monkeypatch):
    names = ['Nana Astar Devilu', 'ana Astar Deviluk']
    reads = Mock(side_effect=[([ship(name, 114)], 6) for name in names])
    monkeypatch.setattr(awakening, 'read_dorm_floor', reads)
    monkeypatch.setattr(awakening.OCR_DORM_SHIP_COUNT, 'ocr', Mock(return_value=(1, 5, 6)))
    assert gate._food_floor('train')[0].name == names[-1]
    assert gate.device.screenshot.call_count == 2


def test_scrolling_ocr_edge_error_still_requires_strong_name_overlap():
    assert same_residents([ship('Nana Astar Devilu')], [ship('lana Astar Deviluk')])


@pytest.mark.parametrize('first,second', [
    ('Nana Astar Devilu', 'ana Astar Deviluk'),
    ('ana Astar Deviluk', 'na Astar Deviluke'),
    ('Nana Astar Devilu', 'Astar Deviluke'),
    ('Future Ship Alpha', 'uture Ship Alpha'),
    ('Future Ship Alpha', 'Future Ship Alpha'),
])
def test_observed_scrolling_fragments_and_unlisted_ship_names_remain_supported(first, second):
    assert same_residents([ship(first)], [ship(second)])


@pytest.mark.parametrize('first,second', [
    ('Nana Astar Deviluke', 'Momo Astar Deviluke'),
    ('Arizona', 'Arizona META'),
    ('Arizona', 'Arizona METn'),
    ('Hiei', 'Hiei META'),
    ('Hiei', 'Hiei METn'),
    ('Cleveland', 'Cleveland µ'),
    ('Cleveland', 'Cleveland μ'),
    ('Nana Astar Deviluke', 'Deviluke'),
    ('Deviluke', 'Deviluke'),
    ('Ning Hai Retrofit', 'ing Hai Retrofit'),
    ('Ning Hai', 'Ning Hai Retrotit'),
    ('Haruna', 'Unrelated New Ship'),
    ('Future Ship Alpha', 'Future Ship Beta'),
])
def test_ambiguous_fragment_or_changed_name_denies_even_with_same_portrait(first, second):
    assert not same_residents([ship(first)], [ship(second)])


@pytest.mark.parametrize('signature', [None, [], [1] * 144, [0] * 144, [0] * 143 + [255],
                                       [0] * 143, [-1] + list(range(143))])
def test_missing_or_blank_portrait_never_authorizes_identity(signature):
    resident = ship()
    resident.sig = signature
    assert census_key([resident], (1, 5, 6)) is None
    assert not same_residents([resident], [ship()])


def test_changed_portrait_denies_even_when_name_and_level_repeat():
    after = ship()
    after.sig.reverse()
    assert not same_residents([ship()], [after])


def test_census_does_not_accept_name_overlap_without_stable_portrait(gate, monkeypatch):
    original, changed = ship('Nana Astar Devilu'), ship('ana Astar Deviluk')
    changed.sig.reverse()
    reads = Mock(side_effect=[([original], 6), ([changed], 6)] * 2)
    monkeypatch.setattr(awakening, 'read_dorm_floor', reads)
    monkeypatch.setattr(awakening.OCR_DORM_SHIP_COUNT, 'ocr', Mock(return_value=(1, 5, 6)))
    with pytest.raises(DormReadError, match='no two complete agreeing'):
        gate._food_floor('train')


def test_complete_count_must_also_agree_across_frames(gate, monkeypatch):
    monkeypatch.setattr(awakening, 'read_dorm_floor', Mock(return_value=([ship()], 6)))
    monkeypatch.setattr(awakening.OCR_DORM_SHIP_COUNT, 'ocr',
                        Mock(side_effect=[(1, 5, 6), (1, 4, 5)] * 2))
    with pytest.raises(DormReadError):
        gate._food_floor('train')


def test_order_and_floor_stay_part_of_resident_identity():
    original = [ship('Haruna'), ship('Tirpitz')]
    assert not same_residents(original, original[::-1])
    assert not same_residents([ship()], [ship(place='rest')])


def test_underread_residents_cannot_pass_by_repeating(gate, monkeypatch):
    monkeypatch.setattr(awakening, 'read_dorm_floor', Mock(return_value=([ship()], 6)))
    monkeypatch.setattr(awakening.OCR_DORM_SHIP_COUNT, 'ocr', Mock(return_value=(2, 4, 6)))
    with pytest.raises(DormReadError):
        gate._food_floor('train')
    assert gate.device.screenshot.call_count == awakening.CENSUS_READS


def test_dorm_requires_occupied_training_floor_without_inspecting_rest(gate):
    gate._food_floor = Mock(return_value=[])
    with pytest.raises(DormReadError, match='training floor is empty'):
        gate._food_residents()
    gate._food_floor.assert_called_once_with('train')


def test_food_census_only_reads_the_training_floor(gate):
    training = [ship()]
    gate._food_floor = Mock(return_value=training)
    assert gate._food_residents() == training
    gate._food_floor.assert_called_once_with('train')


def test_bad_census_still_closes_the_resident_list(gate):
    gate._food_floor = Mock(side_effect=DormReadError('unread count'))
    with pytest.raises(DormReadError):
        gate._food_residents()
    gate.dorm_ship_list_quit.assert_called_once()


def test_capped_training_resident_denies_food(gate):
    gate._food_residents = Mock(return_value=[ship(level=101), ship('Tirpitz', 125)])
    gate._food_boundary_exp = Mock()
    assert not gate.can_feed()
    gate._food_boundary_exp.assert_not_called()
    gate.ui_ensure.assert_called_with(page_dorm)


def test_all_nonboundary_residents_allow_food_without_inventory(gate):
    gate._food_residents = Mock(return_value=[ship(level=101), ship('Tirpitz', 119)])
    gate._food_boundary_exp = Mock()
    assert gate.can_feed()
    gate._food_boundary_exp.assert_not_called()


def test_unawakened_rest_residents_are_irrelevant_to_food(gate, monkeypatch):
    def floor(image, place):
        if place == 'rest':
            return [ship('Tanikaze', 91, 'rest'), ship('Hamakaze', 95, 'rest')], 6
        return [ship(level=101)], 6

    reads = Mock(side_effect=floor)
    monkeypatch.setattr(awakening, 'read_dorm_floor', reads)
    monkeypatch.setattr(awakening.OCR_DORM_SHIP_COUNT, 'ocr', Mock(return_value=(1, 5, 6)))
    gate._food_boundary_exp = Mock()
    assert gate.can_feed()
    gate._food_boundary_exp.assert_not_called()
    assert reads.call_count == 4
    assert all(call.args[1] == 'train' for call in reads.call_args_list)
    assert all(call.args == ('train',) for call in gate.dorm_ship_list_set_tab.call_args_list)


def test_fresh_resident_change_after_dock_checks_denies_food(gate):
    gate._food_residents = Mock(side_effect=[[ship(level=100)], [ship(level=101)]])
    gate._food_boundary_exp = Mock(return_value=True)
    assert not gate.can_feed()


def test_final_census_accepts_scrolling_text_with_unchanged_residents(gate):
    gate._food_residents = Mock(side_effect=[
        [ship('Nana Astar Devilu', 114)], [ship('na Astar Deviluke', 114)]])
    gate._food_boundary_exp = Mock()
    assert gate.can_feed()
    gate._food_boundary_exp.assert_not_called()


def test_final_census_rejects_new_portrait_with_the_same_name_and_level(gate):
    after = ship(level=100)
    after.sig.reverse()
    gate._food_residents = Mock(side_effect=[[ship(level=100)], [after]])
    gate._food_boundary_exp = Mock(return_value=True)
    assert not gate.can_feed()


def test_capped_resident_skips_unnecessary_boundary_searches(gate):
    gate._food_residents = Mock(return_value=[ship(level=100), ship('Tanikaze', 125)])
    gate._food_boundary_exp = Mock()
    assert not gate.can_feed()
    gate._food_boundary_exp.assert_not_called()


@pytest.mark.parametrize('able', [False, True])
def test_boundary_permission_requires_live_exp_result(gate, able):
    resident = ship(level=100)
    gate._food_residents = Mock(return_value=[resident])
    gate._food_boundary_exp = Mock(return_value=able)
    assert gate.can_feed() is able
    gate._food_boundary_exp.assert_called_once_with(resident)


def test_unreadable_gate_denies_food_and_returns_to_dorm(gate):
    gate._food_residents = Mock(side_effect=DormReadError('incomplete'))
    assert not gate.can_feed()
    gate.ui_ensure.assert_called_with(page_dorm)


def test_unrelated_failure_propagates_after_returning_to_dorm(gate):
    gate._food_residents = Mock(side_effect=RuntimeError('device failed'))
    with pytest.raises(RuntimeError, match='device failed'):
        gate.can_feed()
    gate.ui_ensure.assert_called_with(page_dorm)


def dock_card(level=100, left=100):
    return SimpleNamespace(level=level, button=SimpleNamespace(area=(left, 76, left + 130, 280)))


@pytest.mark.parametrize('copies', [0, 2])
def test_no_match_or_ambiguous_same_level_copy_denies_food(gate, copies):
    gate.dock_reset = Mock()
    gate.dock_search = Mock(return_value=True)
    cards = [(dock_card(left=100 + index * 150), 'haruna', index) for index in range(copies)]
    gate._food_search_cards = Mock(return_value=cards)
    with pytest.raises(DormReadError, match='exact matching copies'):
        gate._food_boundary_card(ship(level=100))
    assert gate.ui_ensure.call_args_list[0].args == (page_dorm,)
    assert gate.ui_ensure.call_args_list[1].args == (page_dock,)


def test_unique_exact_name_and_level_match_opens_the_expected_card(gate):
    gate.dock_reset = Mock()
    gate.dock_search = Mock(return_value=True)
    expected = dock_card()
    gate._food_search_cards = Mock(return_value=[(expected, 'haruna', 0), (dock_card(1), 'haruna', 1)])
    card = gate._food_boundary_card(ship(level=100))
    assert card.button is expected.button
    assert gate._food_search_cards.call_count == 2


def test_observed_nana_dorm_and_dock_clipping_selects_one_canonical_candidate(gate):
    gate.dock_reset = Mock()
    gate.dock_search = Mock(return_value=True)
    expected = dock_card(115)
    gate._food_search_cards = Mock(return_value=[(expected, 'nanaastar', 0)])
    card = gate._food_boundary_card(ship('ana Astar Deviluk', 115))
    assert card.button is expected.button
    assert gate._food_search_cards.call_count == 2


@pytest.mark.parametrize('resident_name,card_name', [
    ('Deviluke', 'Nana Astar'),
    ('Nana Astar Deviluke', 'Deviluke'),
    ('Nana Astar Deviluke', 'Astar'),
    ('Arizona', 'Arizona META'),
    ('Arizona META', 'Arizona'),
    ('Ning Hai', 'Ning Hai Retrofit'),
    ('Cleveland', 'Cleveland µ'),
    ('Cleveland µ', 'Cleveland'),
    ('Future Ship Alpha', 'uture Ship Alpha'),
])
def test_ambiguous_clipping_or_distinct_variant_cannot_select_a_boundary_card(
        gate, resident_name, card_name):
    gate.dock_reset = Mock()
    gate.dock_search = Mock(return_value=True)
    gate._food_search_cards = Mock(return_value=[(dock_card(), awakening.name_key(card_name), 0)])
    with pytest.raises(DormReadError):
        gate._food_boundary_card(ship(resident_name, 100))


def test_unknown_boundary_name_still_requires_exact_label(gate):
    gate.dock_reset = Mock()
    gate.dock_search = Mock(return_value=True)
    expected = dock_card()
    gate._food_search_cards = Mock(return_value=[(expected, 'futureshipalpha', 0)])
    assert gate._food_boundary_card(ship('Future Ship Alpha', 100)).button is expected.button


def test_two_same_level_clipped_candidates_cannot_hide_duplicate_copies(gate):
    gate.dock_reset = Mock()
    gate.dock_search = Mock(return_value=True)
    gate._food_search_cards = Mock(return_value=[
        (dock_card(115), 'nanaastar', 0), (dock_card(115, 250), 'nanaastar', 1)])
    with pytest.raises(DormReadError, match='2 exact matching copies'):
        gate._food_boundary_card(ship('ana Astar Deviluk', 115))


@pytest.mark.parametrize('other_name', ['Deviluke', 'Future Ship', 'Nana Astar DeviluX'])
def test_ambiguous_or_unknown_same_level_card_cannot_hide_a_possible_duplicate(gate, other_name):
    gate.dock_reset = Mock()
    gate.dock_search = Mock(return_value=True)
    gate._food_search_cards = Mock(return_value=[
        (dock_card(115), 'nanaastar', 0), (dock_card(115, 250), awakening.name_key(other_name), 1)])
    with pytest.raises(DormReadError, match='another possible matching copy'):
        gate._food_boundary_card(ship('ana Astar Deviluk', 115))


@pytest.mark.parametrize('resident_name,full_key,base_key', [
    ('Arizona META', 'arizonameta', 'arizona'),
    ('Z23 (Retrofit)', 'z23retrofit', 'z23'),
])
def test_base_label_cannot_hide_a_same_level_variant_copy(gate, resident_name, full_key, base_key):
    gate.dock_reset = Mock()
    gate.dock_search = Mock(return_value=True)
    gate._food_search_cards = Mock(return_value=[
        (dock_card(100), full_key, 0), (dock_card(100, 250), base_key, 1)])
    with pytest.raises(DormReadError, match='another possible matching copy'):
        gate._food_boundary_card(ship(resident_name, 100))


@pytest.mark.parametrize('other_name,other_level', [
    ('Haruna', 105), ('Haruna', 1), ('Future Ship', 105),
])
def test_low_floor_level_cannot_choose_between_same_or_uncertain_identity_copies(
        gate, other_name, other_level):
    gate.dock_reset = Mock()
    gate.dock_search = Mock(return_value=True)
    gate._food_residents = Mock(return_value=[ship('Haruna', 5)])
    gate._food_search_cards = Mock(return_value=[
        (dock_card(5), 'haruna', 0), (dock_card(other_level, 250), awakening.name_key(other_name), 1)])
    gate.inventory_open_card = Mock()
    assert not gate.can_feed()
    gate.inventory_open_card.assert_not_called()


def test_low_level_card_with_ambiguous_other_level_copy_is_rejected(gate):
    gate.dock_reset = Mock()
    gate.dock_search = Mock(return_value=True)
    gate._food_search_cards = Mock(return_value=[
        (dock_card(15), 'nanaastar', 0), (dock_card(115, 250), 'deviluke', 1)])
    with pytest.raises(DormReadError, match='another possible matching copy'):
        gate._food_boundary_card(ship('ana Astar Deviluk', 15))


def test_low_level_unique_identity_can_ignore_a_proven_different_ship(gate):
    gate.dock_reset = Mock()
    gate.dock_search = Mock(return_value=True)
    expected = dock_card(5)
    gate._food_search_cards = Mock(return_value=[
        (expected, 'haruna', 0), (dock_card(105, 250), 'hiei', 1)])
    assert gate._food_boundary_card(ship('Haruna', 5)).button is expected.button


def mock_search_scan(monkeypatch, cards, names):
    scanner = Mock()
    scanner.scan.return_value = cards
    monkeypatch.setattr(awakening, 'ShipScanner', Mock(return_value=scanner))
    ocr = Mock()
    ocr.ocr.return_value = names
    monkeypatch.setattr(awakening, 'Ocr', Mock(return_value=ocr))
    return scanner


def test_unread_duplicate_card_cannot_disappear_through_scanner_filters(gate, monkeypatch):
    scanner = mock_search_scan(monkeypatch, [dock_card(100), dock_card(0), dock_card(0)],
                               ['Haruna', 'Haruna', ''])
    with pytest.raises(DormReadError, match='unreadable search-result card'):
        gate._food_search_cards()
    scanner.set_limitation.assert_called_once_with(level=None)
    awakening.ShipScanner.assert_called_once_with(fleet=None, status='any')


def test_full_search_screen_cannot_prove_unique_copy(gate, monkeypatch):
    mock_search_scan(monkeypatch, [dock_card(100), dock_card(1)], ['Haruna', 'Haruna'])
    with pytest.raises(DormReadError, match='continue below'):
        gate._food_search_cards()


def test_readable_search_with_empty_slots_keeps_all_fleets(gate, monkeypatch):
    expected = dock_card(100)
    mock_search_scan(monkeypatch, [expected, dock_card(0)], ['Haruna', ''])
    assert gate._food_search_cards() == [(expected, 'haruna', 0)]


def info_reader(monkeypatch, exp=('20/500', '20/500'), names=('Haruna', 'Haruna'), levels=(100, 100),
                whole=(True, True)):
    reader = Mock()
    reader.read_name.side_effect = list(zip(names, whole))
    reader.ocr_level.ocr.side_effect = levels
    reader.ocr_exp.ocr.side_effect = exp
    monkeypatch.setattr(awakening, 'InfoReader', Mock(return_value=reader))
    return reader


@pytest.mark.parametrize('level', [1, 11, 70, 91, 99])
def test_lower_level_training_ship_with_exact_fresh_agreeing_exp_allows_food(gate, monkeypatch, level):
    resident = ship(level=level)
    gate._food_residents = Mock(return_value=[resident])
    gate._food_boundary_card = Mock(return_value=object())
    gate.inventory_open_card = Mock(return_value=(object(), object(), 4))
    info_reader(monkeypatch, levels=(level, level))
    assert gate.can_feed()
    gate._food_boundary_card.assert_called_once_with(resident)
    assert gate.inventory_open_card.call_count == 2
    assert gate._food_residents.call_count == 2


@pytest.mark.parametrize('floor_level,info_level', [(1, 101), (11, 111), (15, 115), (25, 125)])
def test_missing_leading_digit_cannot_match_higher_info_level(gate, monkeypatch, floor_level, info_level):
    gate._food_residents = Mock(return_value=[ship(level=floor_level)])
    gate._food_boundary_card = Mock(return_value=object())
    gate.inventory_open_card = Mock(return_value=(object(), object(), 4))
    info_reader(monkeypatch, levels=(info_level, info_level))
    assert not gate.can_feed()
    assert gate.inventory_open_card.call_count == 1


@pytest.mark.parametrize('level', [1, 91, 100, 105, 110, 115, 120])
@pytest.mark.parametrize('exp', ['500/MAX', '500/500', 'unreadable'])
def test_training_ship_waiting_for_awakening_or_without_proven_exp_room_denies_food(
        gate, monkeypatch, level, exp):
    gate._food_residents = Mock(return_value=[ship(level=level)])
    gate._food_boundary_card = Mock(return_value=object())
    gate.inventory_open_card = Mock(return_value=(object(), object(), 4))
    info_reader(monkeypatch, exp=(exp, exp), levels=(level, level))
    assert not gate.can_feed()


def test_fresh_positive_agreeing_boundary_exp_allows_food(gate, monkeypatch):
    gate._food_boundary_card = Mock(return_value=object())
    gate.inventory_open_card = Mock(return_value=(object(), object(), 4))
    info_reader(monkeypatch)
    assert gate._food_boundary_exp(ship(level=100))
    assert gate.inventory_open_card.call_count == 2


def test_clipped_resident_requires_full_canonical_info_name_twice(gate, monkeypatch):
    gate._food_boundary_card = Mock(return_value=object())
    gate.inventory_open_card = Mock(return_value=(object(), object(), 4))
    info_reader(monkeypatch, names=('Nana Astar Deviluke',) * 2, levels=(115, 115))
    assert gate._food_boundary_exp(ship('ana Astar Deviluk', 115))
    assert gate.inventory_open_card.call_count == 2


@pytest.mark.parametrize('names,whole', [
    (('Nana Astar', 'Nana Astar'), (True, True)),
    (('Nana Astar Deviluke', 'Momo Belia Deviluke'), (True, True)),
    (('Nana Astar Deviluke', 'Nana Astar Deviluke'), (False, False)),
    (('Nana Astar Deviluke', 'Nana Astar Deviluke'), (True, None)),
])
def test_clipped_or_incomplete_info_name_never_authorizes_canonical_resident(
        gate, monkeypatch, names, whole):
    gate._food_boundary_card = Mock(return_value=object())
    gate.inventory_open_card = Mock(return_value=(object(), object(), 4))
    info_reader(monkeypatch, names=names, whole=whole, levels=(115, 115))
    with pytest.raises(DormReadError, match='detail identity changed'):
        gate._food_boundary_exp(ship('ana Astar Deviluk', 115))


@pytest.mark.parametrize('info_name', ['Arizona META', 'Arizona METn'])
def test_full_info_name_must_keep_variant_suffix(gate, monkeypatch, info_name):
    gate._food_boundary_card = Mock(return_value=object())
    gate.inventory_open_card = Mock(return_value=(object(), object(), 4))
    info_reader(monkeypatch, names=(info_name, info_name))
    with pytest.raises(DormReadError, match='detail identity changed'):
        gate._food_boundary_exp(ship('Arizona', 100))


@pytest.mark.parametrize('exp', ['20/MAX', 'unreadable', '20/0', '500/500'])
def test_boundary_max_or_unknown_exp_does_not_authorize_food(gate, monkeypatch, exp):
    gate._food_boundary_card = Mock(return_value=object())
    gate.inventory_open_card = Mock(return_value=(object(), object(), 4))
    info_reader(monkeypatch, exp=(exp, exp))
    assert not gate._food_boundary_exp(ship(level=100))


@pytest.mark.parametrize('kwargs', [
    {'names': ('Haruna', 'Haruna META')},
    {'levels': (100, 105)},
    {'exp': ('20/500', '20/600')},
])
def test_changed_detail_identity_or_exp_denies_food(gate, monkeypatch, kwargs):
    gate._food_boundary_card = Mock(return_value=object())
    gate.inventory_open_card = Mock(return_value=(object(), object(), 4))
    info_reader(monkeypatch, **kwargs)
    with pytest.raises(DormReadError):
        gate._food_boundary_exp(ship(level=100))


def test_missing_fresh_info_denies_food(gate):
    gate._food_boundary_card = Mock(return_value=object())
    gate.inventory_open_card = Mock(return_value=None)
    with pytest.raises(DormReadError, match='no fresh Info'):
        gate._food_boundary_exp(ship(level=100))
