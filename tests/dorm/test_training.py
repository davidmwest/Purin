from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from module.dorm.training import DormTraining, training_replacement
from module.mood.dorm_rest import DormReadError, DormShip, PickerCard, corroborated_train_level
from module.mood.assets import DORM_PICKER_CANCEL, DORM_PICKER_CONFIRM


@pytest.mark.parametrize('header,body,expected', [
    (116, 116, 116), (16, 116, 116), (11, 111, 111), (99, 99, 99),
    (16, 0, 0), (16, 115, 0), (111, 11, 0), (0, 116, 0), (1, 111, 0),
    (126, 126, 0), (True, 1, 0), (120, 119, 0),
])
def test_training_levels_need_independent_printed_corroboration(header, body, expected):
    assert corroborated_train_level(header, body) == expected


def resident(name, level=111, place='train', slot=0):
    return DormShip(place, name, level, 150, 40, sig=list(range(144)), slot=slot)


def card(index, name, level=111, **kwargs):
    return PickerCard(index // 7, index % 7, name, level, 150, **kwargs)


def roster():
    train = [resident('Haruna'), resident('London (Retrofit)', 120, slot=4)]
    rest = [resident('Hatakaze', 119, 'rest')]
    cards = [card(0, 'Haruna', selected=True), card(1, 'London (Retrofit)', 120),
             card(2, 'Hatakaze', 119), card(3, 'Gangut', fleet=1)]
    return train, rest, cards


def test_replacement_keeps_training_and_rest_residents_and_uses_fleet_ship():
    train, rest, cards = roster()
    assert training_replacement(train, rest, cards, train[1]) is cards[3]


@pytest.mark.parametrize('change', ['submarine', 'maxed', 'not_in_fleet', 'duplicate', 'unknown_level'])
def test_ineligible_or_ambiguous_replacement_never_selected(change):
    train, rest, cards = roster()
    if change == 'submarine':
        cards[3].submarine = True
    elif change == 'maxed':
        cards[3].level = 120
    elif change == 'not_in_fleet':
        cards[3].fleet = 0
    elif change == 'duplicate':
        cards.append(card(4, 'Gangut', 90, fleet=2))
    else:
        cards[3].level = 0
    assert training_replacement(train, rest, cards, train[1]) is None


def test_changed_selection_or_ambiguous_resident_aborts_before_mutation():
    train, rest, cards = roster()
    cards[0].selected = False
    with pytest.raises(DormReadError, match='selected cards'):
        training_replacement(train, rest, cards, train[1])
    cards[0].selected = True
    cards.append(card(4, 'London (Retrofit)', 120))
    with pytest.raises(DormReadError, match='matching picker cards'):
        training_replacement(train, rest, cards, train[1])


def test_missing_replacement_cancels_picker_and_does_not_confirm(monkeypatch):
    owner = DormTraining.__new__(DormTraining)
    train, rest, cards = roster()
    cards[3].level = 125
    owner.device = SimpleNamespace(image=object())
    for method in ('ui_ensure', 'dorm_ship_list_enter', 'dorm_ship_list_quit',
                   'dorm_ship_list_set_tab', 'picker_enter', 'picker_confirm'):
        setattr(owner, method, Mock())
    owner._food_floor = Mock(side_effect=[train, rest, train])
    owner.picker_scan = Mock(return_value=cards)
    owner._training_diagnostic = Mock()
    assert owner.rotate_training() is True
    owner.picker_enter.assert_called_once_with(place='train', slot=4)
    owner.picker_confirm.assert_called_once_with(DORM_PICKER_CANCEL)
    owner.dorm_ship_list_quit.assert_called_once()


def test_no_finished_resident_never_opens_picker_or_rest_floor():
    owner = DormTraining.__new__(DormTraining)
    owner.ui_ensure = Mock()
    owner.dorm_ship_list_enter = Mock()
    owner.dorm_ship_list_quit = Mock()
    owner._food_floor = Mock(return_value=[resident('Haruna')])
    owner.picker_enter = Mock()
    owner._training_diagnostic = Mock()
    assert owner.rotate_training() is True
    owner._food_floor.assert_called_once_with('train')
    owner.picker_enter.assert_not_called()


def rotation_owner():
    owner = DormTraining.__new__(DormTraining)
    train, rest, cards = roster()
    owner.device = SimpleNamespace(image=object(), click=Mock(), sleep=Mock(), screenshot=Mock())
    for method in ('ui_ensure', 'dorm_ship_list_enter', 'dorm_ship_list_quit',
                   'dorm_ship_list_set_tab', 'picker_enter', 'picker_confirm',
                   'dock_filter_set', 'dock_sort_method_dsc_set', '_training_diagnostic'):
        setattr(owner, method, Mock())
    after = [resident('Haruna'), resident('Gangut', slot=4)]
    owner._food_floor = Mock(side_effect=[train, rest, train, after, rest])
    owner.picker_scan = Mock(return_value=cards)
    owner.picker_selected_count = Mock(side_effect=[1, 1, 2])
    owner._training_find = Mock(return_value=(93, 76))
    owner.handle_dorm_food_popup = Mock(return_value=False)
    return owner, train


def test_verified_rotation_preserves_other_residents_and_confirms_once():
    owner, _ = rotation_owner()
    assert owner.rotate_training() is True
    owner.picker_enter.assert_called_once_with(place='train', slot=4)
    assert owner.device.click.call_count == 1
    owner.picker_confirm.assert_called_once_with(DORM_PICKER_CONFIRM)
    assert owner._food_floor.call_args_list[-1].args == ('rest',)


@pytest.mark.parametrize('slot', [None, -1, 6, True])
def test_unknown_physical_slot_does_not_open_picker(slot):
    owner, train = rotation_owner()
    train[1].slot = slot
    assert owner.rotate_training() is False
    owner.picker_enter.assert_not_called()
    owner.device.click.assert_not_called()


@pytest.mark.parametrize('count', [0, 2, 3])
def test_unexpected_picker_count_cancels_before_replacement_click(count):
    owner, _ = rotation_owner()
    owner.picker_selected_count = Mock(return_value=count)
    assert owner.rotate_training() is False
    owner.device.click.assert_not_called()
    owner.picker_confirm.assert_called_once_with(DORM_PICKER_CANCEL)


def test_failed_fresh_replacement_lookup_cancels_before_click():
    owner, _ = rotation_owner()
    owner._training_find.side_effect = DormReadError('identity changed')
    assert owner.rotate_training() is False
    owner.device.click.assert_not_called()
    owner.picker_confirm.assert_called_once_with(DORM_PICKER_CANCEL)


def test_missing_selection_after_click_cancels_instead_of_confirming():
    owner, _ = rotation_owner()
    owner.picker_selected_count = Mock(side_effect=[1, 1, 1])
    assert owner.rotate_training() is False
    owner.picker_confirm.assert_called_once_with(DORM_PICKER_CANCEL)


def test_changed_rest_roster_denies_food_even_after_training_confirmation():
    owner, train = rotation_owner()
    owner._food_floor = Mock(side_effect=[train, roster()[1], train,
                                         [resident('Haruna'), resident('Gangut', slot=4)], []])
    assert owner.rotate_training() is False
    owner.picker_confirm.assert_called_once_with(DORM_PICKER_CONFIRM)
