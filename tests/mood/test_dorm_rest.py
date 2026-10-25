import os
from collections import deque
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import cv2
import numpy as np
import pytest

import module.config.server as server
from module.base.button import Button
from module.base.utils import crop
from module.device.device import Device
from module.map.lineup_rotation import SLOTS_PER_FLEET, lineup_slot
from module.mood.assets import DORM_PICKER_CANCEL
from module.mood.dorm_rest import DormRest, DormReadError, PickerOverlapError, PickerCard, DormShip, dorm_mood, is_dorm_food_popup, picker_drawn, \
    picker_fleets_ended, picker_is_top, picker_low_mood_ended, picker_name_similarity, picker_row_tops, \
    read_dorm_floor, read_picker_row, resident_level, ship_list_tab, update_moods
from module.mood.dorm_rest import PICKER_SCROLL_NAME
from module.mood.lineup import learn_lineup_ships, level_area, match_lineup, portrait_area, record_lineup
from module.mood.ship import ShipMood, ShipMoods, portrait_signature
from module.ocr.ocr import Digit

DATA = os.path.join(os.path.dirname(__file__), 'data')
NOW = datetime(2026, 10, 14, 8, 0, 0)


@pytest.fixture(autouse=True)
def en():
    # Templates take the files of the server set when they are loaded, set_server() releases them
    previous = server.server
    server.set_server('en')
    yield
    server.set_server(previous)


def load(name):
    """
    Frames of the EN client at 2560x1440 as Alas sees them, black except the parts read.
    """
    return cv2.cvtColor(cv2.imread(os.path.join(DATA, name)), cv2.COLOR_BGR2RGB)


class Config:
    def cross_get(self, keys, default=None):
        return default

    def cross_set(self, keys, value):
        pass


def ships(found):
    return [(ship.name, ship.level, ship.mood, ship.speed) for ship in found]


def test_tab_of_the_dorm_ship_list():
    assert ship_list_tab(load('dorm_train.png')) == 'train'
    assert ship_list_tab(load('dorm_rest.png')) == 'rest'
    # The EXP popup of the dorm is white where the tabs are, it is not the ship list
    assert ship_list_tab(load('dorm_exp_popup.png')) is None
    assert ship_list_tab(load('picker.png')) is None


def test_reads_the_first_floor_of_the_dorm():
    found, slots = read_dorm_floor(load('dorm_train.png'), 'train')

    assert ships(found) == [('Victorious META', 109, 150, 40), ('Haruna', 99, 150, 40)]
    assert slots == 6


def test_reads_the_second_floor_of_the_dorm():
    found, slots = read_dorm_floor(load('dorm_rest.png'), 'rest')

    assert ships(found) == [('Hatakaze', 119, 150, 50), ('Oite', 119, 150, 50), ('Fusou META', 119, 150, 50),
                            ('Dmitri Donskoi', 100, 150, 50)]
    assert all(ship.place == 'rest' and ship.sig for ship in found)


def test_finds_the_rows_of_cards_of_the_picker():
    assert picker_row_tops(load('picker.png'))[:2] == [76, 303]
    # After a drag, the list does not move by whole rows
    assert picker_row_tops(load('picker_scrolled.png'))[:2] == [121, 348]


def test_reads_the_cards_of_the_picker():
    image = load('picker.png')
    first = read_picker_row(image, 76, 0)
    second = read_picker_row(image, 303, 1)

    assert first[0].remove
    hatakaze = first[1]
    assert (hatakaze.name, hatakaze.level, hatakaze.mood, hatakaze.fleet, hatakaze.selected) == \
           ('Hatakaze', 119, 150, 1, True)
    nana, gangut, whydah = second[0], second[5], second[6]
    assert (nana.name, nana.fleet, nana.selected, nana.truncated) == ('Nana Astar', 2, False, True)
    assert (gangut.name, gangut.level, gangut.fleet, gangut.submarine) == ('Gangut', 93, 1, False)
    # In the submarine fleet, her badge says fleet 1 too
    assert (whydah.name, whydah.mood, whydah.fleet, whydah.submarine) == ('Whydah', 119, 1, True)


def test_remove_card_is_found_at_the_top_of_the_list():
    first = read_picker_row(load('picker.png'), 76, 0)

    assert picker_is_top(first, {}, None)


def test_row_at_the_top_of_the_screen_is_the_first_of_the_list():
    first = [card(i, name) for i, name in enumerate(['Oite', 'Fusou META', 'Nevada META', 'Kawakaze ME', 'London',
                                                       'Hatakaze', 'Nana Astar'])]
    middle = [card(14 + i, name) for i, name in enumerate(['Alsace', 'Z52', 'Musashi', 'Little Illu', 'Hermes',
                                                             'Javelin', 'Arizona'])]
    again = [card(i, name) for i, name in enumerate(['0ite', 'Fusou META', 'Nevada META', 'Kawakaze ME', 'London',
                                                       'Hatakaze', 'Nana Astar'])]

    # A row of the middle of the list at the top of the screen
    assert not picker_is_top(middle, {0: first}, None)
    # The first row read before, OCR may differ a little
    assert picker_is_top(again, {0: first}, None)
    # Nothing read yet, a drag down that does not move the list
    assert picker_is_top(first, {}, again)
    assert not picker_is_top(first, {}, None)


def card(index, name, fleet=0, mood=150, level=100, submarine=False, truncated=False, remove=False):
    return PickerCard(row=index // 7, col=index % 7, name=name, level=level, mood=mood, fleet=fleet,
                      submarine=submarine, truncated=truncated, remove=remove)


def test_picker_alignment_uses_the_second_readable_row_when_it_is_the_overlap(monkeypatch):
    dorm = DormRest.__new__(DormRest)
    dorm.device = SimpleNamespace(image=None)
    known = [card(7 + i, f'Known {i}') for i in range(7)]
    dorm._picker_rows = {1: known}
    unknown = [card(i, f'New {i}') for i in range(7)]
    monkeypatch.setattr('module.mood.dorm_rest.read_picker_row',
                        lambda image, top, row: known if top == 303 else unknown)

    assert dorm.picker_align(0, [76, 303]) == 0


def test_picker_alignment_rejects_an_estimate_that_would_skip_a_new_row(monkeypatch):
    dorm = DormRest.__new__(DormRest)
    dorm.device = SimpleNamespace(image=None)
    dorm._picker_rows = {0: [card(i, f'Old {i}') for i in range(7)],
                         1: [card(7 + i, f'Previous {i}') for i in range(7)]}
    monkeypatch.setattr('module.mood.dorm_rest.read_picker_row',
                        lambda image, top, row: [card(i, f'New {top} {i}') for i in range(7)])

    with pytest.raises(DormReadError, match='lost row overlap'):
        dorm.picker_align(227, [76, 303])


def test_picker_scan_uses_half_row_gestures_to_allow_adb_overshoot(monkeypatch):
    dorm = DormRest.__new__(DormRest)
    dorm.device = SimpleNamespace(image=None, click_record_clear=Mock())
    dorm.dock_favourite_set = Mock()
    dorm.dock_filter_set = Mock()
    dorm.dock_sort_method_dsc_set = Mock()
    dorm.picker_scroll_top = Mock(return_value=(0, [76, 303]))
    dorm.picker_scroll = Mock(return_value=(227, [76, 303]))
    monkeypatch.setattr('module.mood.dorm_rest.picker_fleets_ended', Mock(side_effect=[False, True]))
    monkeypatch.setattr('module.mood.dorm_rest.read_picker_row',
                        lambda image, top, row: [card(7 * row + i, f'Row {row} ship {i}', fleet=1)
                                                 for i in range(7)])

    found = dorm.picker_scan([])

    dorm.picker_scroll.assert_called_once_with(0, rows=0.5)
    assert {entry.row for entry in found} == {0, 1, 2}
    assert len(found) == 21


def physical_picker(monkeypatch, overshoot):
    """Run real scan/alignment against a list that moves farther than the requested drag."""
    dorm = DormRest.__new__(DormRest)
    dorm.config = SimpleNamespace(Emulator_ControlMethod='minitouch')
    state = {'offset': 0, 'up': [], 'resets': 0}

    def drag(start, end, **kwargs):
        distance = start[1] - end[1]
        if distance > 0:
            state['up'].append(distance)
            state['offset'] += distance * overshoot
        else:
            state['resets'] += 1
            state['offset'] = max(0, state['offset'] + distance * overshoot)

    def tops(image):
        return [76 + row * 227 - state['offset'] for row in range(10)
                if 0 <= 76 + row * 227 - state['offset'] <= 640]

    def read(image, top, row):
        actual = round((top + state['offset'] - 76) / 227)
        names = [f'Row {actual} ship {col}' for col in range(7)]
        if actual == 0:
            names[0] = ''
        if actual == 1:
            names[6] = 'Tirpitz'
        if actual == 2:
            names[5] = 'Isokaze'
        return [card(row * 7 + col, name, fleet=1, remove=actual == 0 and col == 0)
                for col, name in enumerate(names)]

    dorm.device = SimpleNamespace(image=None, drag=Mock(side_effect=drag), screenshot=Mock(),
                                  click_record_clear=Mock(), click_record_remove=Mock(), sleep=Mock())
    dorm.handle_dock_cards_loading = Mock()
    dorm.dock_favourite_set = Mock()
    dorm.dock_filter_set = Mock()
    dorm.dock_sort_method_dsc_set = Mock()
    monkeypatch.setattr('module.mood.dorm_rest.picker_row_tops', tops)
    monkeypatch.setattr('module.mood.dorm_rest.read_picker_row', read)
    monkeypatch.setattr('module.mood.dorm_rest.picker_fleets_ended',
                        lambda cards, residents: any(c.name == 'Isokaze' for c in cards.values()))
    return dorm, state


def test_actual_double_adb_movement_preserves_tirpitz_and_isokaze(monkeypatch):
    dorm, state = physical_picker(monkeypatch, overshoot=2)

    found = dorm.picker_scan([])

    assert {c.row for c in found} == {0, 1, 2}
    assert len(found) == 21
    assert next(c for c in found if c.name == 'Tirpitz').row == 1
    assert next(c for c in found if c.name == 'Isokaze').row == 2
    assert state['up'] == [114]
    assert state['resets'] == 0


def test_larger_adb_overshoot_restarts_from_verified_top_with_smaller_drag(monkeypatch):
    dorm, state = physical_picker(monkeypatch, overshoot=4)

    found = dorm.picker_scan([])

    assert {c.row for c in found} == {0, 1, 2}
    assert len(found) == 21
    assert state['up'] == [114, 57]
    assert state['resets'] == 1


def test_picker_overlap_retries_are_bounded_without_trusting_an_estimate(monkeypatch):
    dorm, state = physical_picker(monkeypatch, overshoot=2)
    dorm.picker_scroll = Mock(side_effect=PickerOverlapError('Dorm picker lost row overlap'))
    original_top = dorm.picker_scroll_top
    dorm.picker_scroll_top = Mock(side_effect=original_top)

    with pytest.raises(PickerOverlapError, match='lost row overlap'):
        dorm.picker_scan([])

    assert [call.kwargs['rows'] for call in dorm.picker_scroll.call_args_list] == [0.5, 0.25, 0.125]
    assert dorm.picker_scroll_top.call_count == 3
    assert set(dorm._picker_rows) == {0, 1}


def test_unverified_picker_top_cannot_be_relabelled_as_row_zero(monkeypatch):
    dorm, state = physical_picker(monkeypatch, overshoot=2)
    dorm._picker_rows = {0: [card(i, f'Known {i}') for i in range(7)]}
    monkeypatch.setattr('module.mood.dorm_rest.read_picker_row',
                        lambda image, top, row: [card(i, f'Wrong {i}') for i in range(7)])

    with pytest.raises(DormReadError, match='can not get back to the top'):
        dorm.picker_scroll_top()

    assert state['resets'] == 8


@pytest.mark.parametrize('scroll', ['down', 'top'])
def test_adb_picker_scroll_does_not_tap_endpoint_or_change_selection(monkeypatch, scroll):
    dorm, state = physical_picker(monkeypatch, overshoot=1)
    dorm.config.Emulator_ControlMethod = 'ADB'
    device = dorm.device
    device.config = dorm.config
    device.handle_control_check = Mock()
    physical_drag = device.drag.side_effect
    device.swipe_adb = Mock(side_effect=physical_drag)
    # Use the real ADB dispatch paths: drag would also tap a card and change this count.
    state['selected'] = 5

    def endpoint_click(*args):
        state['selected'] += 1

    device.click = Mock(side_effect=endpoint_click)
    device.swipe = Mock(wraps=Device.swipe.__get__(device))
    device.drag = Mock(wraps=Device.drag.__get__(device))
    if scroll == 'down':
        dorm._picker_drag(0.5)
        start, end = (640, 560), (640, 446)
    else:
        dorm._picker_rows = {0: [card(i, f'Row 0 ship {i}', remove=i == 0) for i in range(7)]}
        state['offset'] = 227
        assert dorm.picker_scroll_top()[0] == 0
        start, end = (640, 150), (640, 600)

    device.swipe_adb.assert_called_once_with(list(start), list(end), duration=0.5)
    device.handle_control_check.assert_called_once_with(PICKER_SCROLL_NAME)
    device.click.assert_not_called()
    device.drag.assert_not_called()
    assert state['selected'] == 5


def test_picker_no_progress_fails_instead_of_returning_a_partial_scan(monkeypatch):
    dorm, state = physical_picker(monkeypatch, overshoot=2)
    dorm.picker_scroll = Mock(return_value=(0, [76, 303]))

    with pytest.raises(DormReadError, match='did not advance'):
        dorm.picker_scan([])

    dorm.picker_scroll.assert_called_once()


def real_picker_click_guard(dorm):
    """Use the actual device guard, once per gesture as control dispatch does."""
    device = dorm.device
    device.click_record = deque(maxlen=15)
    device.user_input_check = Mock()
    device.stuck_record_clear = Mock()
    for method in ('handle_control_check', 'click_record_add', 'click_record_check',
                   'click_record_clear', 'click_record_remove'):
        setattr(device, method, getattr(Device, method).__get__(device))
    original_drag = device.drag

    def drag(*args, **kwargs):
        device.handle_control_check(kwargs['name'])
        original_drag(*args, **kwargs)

    device.drag = Mock(side_effect=drag)
    return device


def test_verified_scan_progress_survives_real_adb_click_guard(monkeypatch):
    dorm, state = physical_picker(monkeypatch, overshoot=1)
    device = real_picker_click_guard(dorm)
    original_clear = device.click_record_clear

    def initial_clear():
        original_clear()
        device.click_record_add('UNRELATED_CONFIRM')

    device.click_record_clear = initial_clear
    monkeypatch.setattr('module.mood.dorm_rest.picker_fleets_ended', lambda *args: False)

    found = dorm.picker_scan([])

    assert len(found) == 70
    assert len(state['up']) > 12
    assert list(device.click_record) == ['UNRELATED_CONFIRM']


@pytest.mark.parametrize('aligned', [0, -20])
def test_picker_stagnant_or_wrong_direction_does_not_clear_history(monkeypatch, aligned):
    dorm, state = physical_picker(monkeypatch, overshoot=0)
    device = real_picker_click_guard(dorm)
    dorm._picker_rows = {0: [card(i, f'Known {i}') for i in range(7)]}
    dorm.picker_align = Mock(return_value=aligned)

    assert dorm.picker_scroll(0, 0.5)[0] == aligned

    assert list(device.click_record) == [PICKER_SCROLL_NAME]


def test_picker_unverified_top_retains_real_stuck_guard(monkeypatch):
    dorm, state = physical_picker(monkeypatch, overshoot=0)
    device = real_picker_click_guard(dorm)
    dorm._picker_rows = {0: [card(i, f'Known {i}') for i in range(7)]}
    monkeypatch.setattr('module.mood.dorm_rest.read_picker_row',
                        lambda *args, **kwargs: [card(i, f'Wrong {i}') for i in range(7)])

    with pytest.raises(DormReadError, match='can not get back to the top'):
        dorm.picker_scroll_top()

    assert device.drag.call_count == 8
    assert state['resets'] == 8
    assert list(device.click_record) == [PICKER_SCROLL_NAME] * 8


def test_verified_top_removes_only_picker_history(monkeypatch):
    dorm, state = physical_picker(monkeypatch, overshoot=2)
    device = real_picker_click_guard(dorm)
    dorm._picker_rows = {}
    device.click_record.extend(['UNRELATED_CONFIRM', PICKER_SCROLL_NAME, PICKER_SCROLL_NAME])

    assert dorm.picker_scroll_top() == (0, [76, 303, 530])

    assert list(device.click_record) == ['UNRELATED_CONFIRM']


def test_garbled_overlap_uses_fresh_settled_reads_without_more_drag(monkeypatch):
    dorm, state = physical_picker(monkeypatch, overshoot=2)
    device = real_picker_click_guard(dorm)
    dorm._picker_rows = {0: [card(i, f'Known {i}') for i in range(7)]}
    dorm.picker_align = Mock(side_effect=[PickerOverlapError('garbled'),
                                        PickerOverlapError('garbled'), 227])

    assert dorm.picker_scroll(0, 0.5)[0] == 227

    assert device.drag.call_count == 1
    assert device.screenshot.call_count == 3
    assert device.sleep.call_args_list == [((0.3,),), ((0.3,),)]
    assert not device.click_record


def test_permanently_garbled_overlap_keeps_history_and_read_bound(monkeypatch):
    dorm, state = physical_picker(monkeypatch, overshoot=2)
    device = real_picker_click_guard(dorm)
    dorm._picker_rows = {0: [card(i, f'Known {i}') for i in range(7)]}
    dorm.picker_align = Mock(side_effect=PickerOverlapError('garbled'))

    with pytest.raises(PickerOverlapError, match='garbled'):
        dorm.picker_scroll(0, 0.5)

    assert device.drag.call_count == 1
    assert dorm.picker_align.call_count == 3
    assert device.screenshot.call_count == 3
    assert device.sleep.call_count == 2
    assert list(device.click_record) == [PICKER_SCROLL_NAME]


def test_picker_find_small_drag_does_not_skip_isokaze_under_double_adb_movement(monkeypatch):
    dorm, state = physical_picker(monkeypatch, overshoot=2)
    dorm._picker_rows = {}
    dorm.picker_find_on_screen = Mock(side_effect=lambda name: (100, 302)
                                     if 76 + 2 * 227 - state['offset'] <= 454 else None)

    assert dorm.picker_find('Isokaze') == (100, 302)
    assert state['up'] == [114]


@pytest.mark.parametrize('read, expected', [('Isokaz', (93, 76)), ('Isokae', None), ('Hamakaze', None)])
def test_picker_find_requires_same_ship_name_not_weak_similarity(monkeypatch, read, expected):
    dorm = DormRest.__new__(DormRest)
    dorm.device = SimpleNamespace(image=None)
    monkeypatch.setattr('module.mood.dorm_rest.picker_row_tops', lambda image: [76])
    monkeypatch.setattr('module.mood.dorm_rest.Ocr', Mock(return_value=SimpleNamespace(ocr=lambda image: [read])))

    assert dorm.picker_find_on_screen('Isokaze') == expected


def test_ships_in_fleets_end_with_two_cards_without_a_badge():
    residents = ['Hatakaze', 'Dmitri Donskoi']
    cards = {c.index: c for c in [
        card(0, '', remove=True), card(1, 'Hatakaze', fleet=1), card(2, 'Dmitri Dons', truncated=True),
        card(3, 'London (Ret', fleet=2), card(4, 'Missed badge'), card(5, 'Hermes', fleet=5)]}
    assert not picker_fleets_ended(cards, residents)

    cards[6] = card(6, 'Random')
    cards[7] = card(7, 'Another')
    assert picker_fleets_ended(cards, residents)


def test_update_follows_the_dorm_and_the_fleets():
    moods = ShipMoods(Config())
    long_ago = NOW - timedelta(days=4)
    moods.ships['Old ship'] = ShipMood('Old ship', time=long_ago)
    moods.ships['In a lineup'] = ShipMood('In a lineup', time=long_ago)
    # Out of the dorm since the last time
    moods.ships['Left the dorm'] = ShipMood('Left the dorm', mood=100, time=NOW - timedelta(hours=1), place='rest',
                                            speed=50, fleet=3)
    moods.lineups = {'Event2': {'1': ['In a lineup'], '2': []}}
    dorm = [
        # The dorm dropped a digit of 124
        DormShip(place='rest', name='Kawakaze META', level=115, mood=14, speed=50, sig=[1]),
        DormShip(place='train', name='lana Astar Deviluk', level=110, mood=150, speed=40),
        DormShip(place='train', name='yHieim', level=107, mood=120, speed=40),
    ]
    cards = [card(0, '', remove=True), card(1, 'Kawakaze ME', fleet=1, mood=124, truncated=True),
             card(2, 'Nana Astar', fleet=2, truncated=True), card(3, 'Hiei', fleet=2),
             card(4, 'London (Ret', fleet=2, mood=119, level=118, truncated=True),
             card(5, 'Whydah', fleet=1, mood=119, submarine=True), card(6, 'Random')]

    update_moods(moods, dorm, cards, NOW)

    # Named after the dock when it does not cut the name, after the dorm otherwise.
    # Ships not seen for days are forgotten, unless in a lineup.
    assert sorted(moods.ships) == ['Hiei', 'In a lineup', 'Kawakaze META', 'Left the dorm', 'London (Ret',
                                   'lana Astar Deviluk']
    left = moods.ships['Left the dorm']
    # The floor lists prove she left the dorm, but an omitted picker card cannot prove she left fleet 3.
    assert (left.mood, left.time, left.place, left.speed, left.fleet) == (150, NOW, 'dock', 20, 3)
    kawakaze = moods.ships['Kawakaze META']
    assert (kawakaze.mood, kawakaze.place, kawakaze.speed, kawakaze.fleet, kawakaze.time) == (124, 'rest', 50, 1, NOW)
    assert (moods.ships['Hiei'].place, moods.ships['Hiei'].fleet) == ('train', 2)
    london = moods.ships['London (Ret']
    assert (london.mood, london.place, london.speed, london.level, london.fleet) == (119, 'dock', 20, 118, 2)
    assert moods.read == NOW


def test_skipped_picker_row_cannot_remove_an_exhausted_ship_from_fleet_morale():
    moods = ShipMoods(Config())
    moods.ships['Tirpitz'] = ShipMood('Tirpitz', mood=101, time=NOW - timedelta(hours=1), level=103, fleet=1)
    moods.ships['Hakuhou'] = ShipMood('Hakuhou', mood=140, time=NOW, level=101, fleet=1)

    update_moods(moods, [], [card(1, 'Hakuhou', mood=140, level=101, fleet=1)], NOW)

    assert set(moods.fleet(1)) == {'Hakuhou', 'Tirpitz'}
    assert moods.ships['Tirpitz'].at(NOW) == 119


def test_fresh_picker_badge_repairs_previously_lost_fleet_membership():
    moods = ShipMoods(Config())
    moods.ships['Tirpitz'] = ShipMood('Tirpitz', mood=101, time=NOW - timedelta(hours=1), level=103, fleet=0)

    update_moods(moods, [], [card(14, 'Tirpitz', mood=119, level=103, fleet=1)], NOW)

    assert moods.fleet(1) == ['Tirpitz']
    assert moods.ships['Tirpitz'].mood == 119


NEVADA_CARD = [10, 200, 30, 5]


def test_garbled_card_is_the_ship_of_her_portrait():
    """
    On a picker not drawn yet, 'Nevada META' reads 'NN-d- AMET' and her fleet badge is missed.
    """
    moods = ShipMoods(Config())
    moods.ships['Nevada META'] = ShipMood('Nevada META', mood=140, time=NOW, level=118, fleet=1,
                                          card_sig=NEVADA_CARD)
    garbled = PickerCard(row=0, col=1, name='NN-d- AMET', level=118, mood=112, sig=[11, 199, 30, 5])

    update_moods(moods, [], [card(0, '', remove=True), garbled], NOW)

    assert sorted(moods.ships) == ['Nevada META']
    assert moods.ships['Nevada META'].mood == 112


def test_garbled_name_of_no_known_portrait_is_left_out():
    moods = ShipMoods(Config())
    garbled = PickerCard(row=0, col=1, name='me～u METn', level=119, mood=142, fleet=1, sig=[1, 2, 3, 4])

    update_moods(moods, [], [card(0, '', remove=True), garbled], NOW)

    assert moods.ships == {}


def test_resident_with_a_garbled_name_is_told_by_her_card():
    """
    The picker lists the ships of the second floor first, in the order of the dorm.
    """
    moods = ShipMoods(Config())
    moods.ships['Kawakaze META'] = ShipMood('Kawakaze META', level=115, fleet=1)
    dorm = [DormShip(place='rest', name='i-wi-L--^ Mc', level=115, mood=136, speed=50)]
    cards = [card(0, '', remove=True), card(1, 'Kawakaze ME', fleet=1, mood=136, level=115, truncated=True)]

    update_moods(moods, dorm, cards, NOW)

    assert sorted(moods.ships) == ['Kawakaze META']
    kawakaze = moods.ships['Kawakaze META']
    assert (kawakaze.place, kawakaze.speed, kawakaze.mood) == ('rest', 50, 136)
    assert dorm[0].known == 'Kawakaze META'


def test_picker_read_before_it_is_drawn():
    names = ['Fusou META', 'NN-d- AMET', 'i-wi-L--^ Mc', 'London (Ret', 'Hatakaze', 'ii++I Dwin']
    drawn = ['Fusou META', 'Nevada META', 'Kawakaze ME', 'London (Ret', 'Hatakaze', '_Alsace']

    assert not picker_drawn([card(0, '', remove=True)] + [card(i + 1, name) for i, name in enumerate(names)])
    assert picker_drawn([card(0, '', remove=True)] + [card(i + 1, name) for i, name in enumerate(drawn)])


def test_update_reads_ships_out_of_the_fleets_after_them():
    moods = ShipMoods(Config())
    two_hours_ago = NOW - timedelta(hours=2)
    moods.ships['Dmitri Donskoi'] = ShipMood('Dmitri Donskoi', mood=119, time=two_hours_ago, level=100)
    moods.ships['Nevada META'] = ShipMood('Nevada META', mood=119, time=two_hours_ago, level=117)
    moods.lineups = {'Event2': {'1': ['Nevada META'], '2': ['Dmitri Donskoi']}}
    cards = [card(0, '', remove=True), card(1, 'Hatakaze', fleet=1),
             card(2, 'Dmitri Dons', mood=96, level=100, truncated=True), card(3, 'Nevada', mood=20, level=80)]

    update_moods(moods, [], cards, NOW)

    dmitri = moods.ships['Dmitri Donskoi']
    assert (dmitri.mood, dmitri.time, dmitri.place, dmitri.fleet) == (96, NOW, 'dock', 0)
    # Below her level, another ship
    assert (moods.ships['Nevada META'].mood, moods.ships['Nevada META'].time) == (119, two_hours_ago)


def lineup_slots(image):
    """
    Returns:
        list[tuple[int, list[int]]]: Level and portrait signature of each slot of the lineup of a stage.
    """
    slots = [lineup_slot(fleet, position) for fleet in (1, 2) for position in range(SLOTS_PER_FLEET)]
    buttons = [Button(area=level_area(slot), color=(), button=(), name=f'LEVEL_{i}') for i, slot in enumerate(slots)]
    levels = Digit(buttons, letter=(255, 255, 255), threshold=128, name='LINEUP_LEVEL').ocr(image)
    signatures = [portrait_signature(crop(image, portrait_area(slot))) for slot in slots]
    return list(zip(levels, signatures))


def dorm_ships():
    """
    Returns:
        list[ShipMood]: Ships read in the dorm.
    """
    known = read_dorm_floor(load('dorm_rest.png'), 'rest')[0] + read_dorm_floor(load('dorm_train.png'), 'train')[0]
    return [ShipMood(ship.name, level=ship.level, sig=ship.sig) for ship in known]


def test_lineup_of_a_hard_mode_stage_is_matched_by_level_and_portrait():
    """
    Hatakaze, Oite and Fusou META are all at level 119, Hatakaze and Oite in fleet 1 of the lineup,
    Fusou META in fleet 2.
    """
    slots = lineup_slots(load('lineup.png'))

    assert [level for level, _ in slots] == [109, 117, 99, 118, 119, 119, 119, 107, 93, 100, 115, 105]
    names = match_lineup(slots, dorm_ships())
    assert names[:6] == ['Victorious META', None, 'Haruna', None, 'Oite', 'Hatakaze']
    assert names[6:] == ['Fusou META', None, None, 'Dmitri Donskoi', None, None]


class StorageConfig:
    def __init__(self):
        self.storage = {}
        self.task = SimpleNamespace(command='Event2')

    def is_task_enabled(self, task):
        return task == 'DormRest'

    def cross_get(self, keys, default=None):
        return self.storage or default

    def cross_set(self, keys, value):
        self.storage = value


def test_lineup_ships_not_followed_yet_are_kept_by_level_and_portrait():
    config = StorageConfig()
    moods = ShipMoods(config)
    for ship in dorm_ships():
        moods.ships[ship.name] = ship
    moods.save()
    main = SimpleNamespace(config=config, device=SimpleNamespace(image=load('lineup.png')))

    assert not record_lineup(main)

    saved = ShipMoods(config)
    assert saved.lineups == {'Event2': {'1': ['Victorious META', 'Haruna', 'Oite', 'Hatakaze'],
                                        '2': ['Fusou META', 'Dmitri Donskoi']}}
    assert [(slot['fleet'], slot['level']) for slot in saved.unknown['Event2']] == \
           [(1, 117), (1, 118), (2, 107), (2, 93), (2, 115), (2, 105)]


def test_same_lineup_read_badly_keeps_the_one_read_before():
    config = StorageConfig()
    moods = ShipMoods(config)
    for ship in dorm_ships():
        moods.ships[ship.name] = ship
    moods.save()
    main = SimpleNamespace(config=config, device=SimpleNamespace(image=load('lineup.png')))
    record_lineup(main)
    first = ShipMoods(config).lineups['Event2']

    # The same lineup, its portraits misread
    moods = ShipMoods(config)
    for ship in moods.ships.values():
        ship.sig = None
    moods.save()
    record_lineup(main)

    assert ShipMoods(config).lineups['Event2'] == first


def test_ships_of_a_lineup_are_found_on_the_dock_by_level_and_portrait():
    """
    Hatakaze and Gangut out of the fleets, in the lineup of D3, not followed yet.
    """
    moods = ShipMoods(Config())
    moods.unknown = {'Event2': [{'fleet': index // SLOTS_PER_FLEET + 1, 'level': level, 'sig': sig}
                                for index, (level, sig) in enumerate(lineup_slots(load('lineup.png')))]}
    image = load('picker.png')
    cards = [card for card in read_picker_row(image, 76, 0) + read_picker_row(image, 303, 1) if card.name]
    for found in cards:
        found.fleet = 0

    assert sorted(learn_lineup_ships(moods, cards, NOW)) == ['Gangut', 'Hatakaze']

    assert moods.lineups == {'Event2': {'1': ['Hatakaze'], '2': ['Gangut']}}
    assert len(moods.unknown['Event2']) == 10
    hatakaze = moods.ships['Hatakaze']
    assert (hatakaze.mood, hatakaze.time, hatakaze.place, hatakaze.level, hatakaze.fleet) == \
           (150, NOW, 'dock', 119, 0)


def test_picker_reads_on_while_other_ships_are_low_on_mood():
    residents = ['Hatakaze']
    cards = {c.index: c for c in [
        card(0, '', remove=True), card(1, 'Hatakaze', fleet=1), card(2, 'London', fleet=2, mood=90),
        card(3, 'Dmitri Dons', mood=96), card(4, 'Random', mood=110)]}
    assert picker_fleets_ended(cards, residents)
    assert not picker_low_mood_ended(cards, residents)

    cards[5] = card(5, 'Another', mood=119)
    assert picker_low_mood_ended(cards, residents)


@pytest.mark.parametrize('read, predicted, mood', [
    # 146 read as 4, or as nothing
    (4, 146, 146),
    (0, 146, 146),
    # Read close to what is followed, or above it
    (140, 146, 140),
    (150, 146, 150),
    # Not followed yet
    (0, None, 0),
])
def test_mood_misread_on_the_dorm_list(read, predicted, mood):
    assert dorm_mood(read, predicted) == mood


def test_resident_without_her_card_keeps_her_mood_on_a_misread():
    moods = ShipMoods(Config())
    # On the second floor at 140 six minutes ago, she recovered 5 since
    moods.ships['Gangut'] = ShipMood('Gangut', mood=140, time=NOW - timedelta(minutes=6), place='rest', speed=50,
                                     level=97, fleet=1)
    moods.ships['Kansas'] = ShipMood('Kansas', mood=60, time=NOW - timedelta(minutes=6), place='rest', speed=50,
                                     level=85, fleet=1)
    dorm = [
        DormShip(place='rest', name='Gangut', level=97, mood=4, speed=50),
        # Read right, below what was followed: she fought where Purin did not count it
        DormShip(place='rest', name='Kansas', level=85, mood=58, speed=50),
    ]
    cards = [card(0, '', remove=True)]

    update_moods(moods, dorm, cards, NOW)

    assert moods.ships['Gangut'].mood == 145
    assert moods.ships['Kansas'].mood == 58


def test_card_tells_the_mood_of_a_resident():
    moods = ShipMoods(Config())
    moods.ships['Gangut'] = ShipMood('Gangut', mood=140, time=NOW - timedelta(minutes=6), place='rest', speed=50,
                                     level=97, fleet=1)
    dorm = [DormShip(place='rest', name='Gangut', level=97, mood=4, speed=50)]
    cards = [card(0, '', remove=True), card(1, 'Gangut', fleet=1, mood=120, level=97)]

    update_moods(moods, dorm, cards, NOW)

    # The dock reads moods right, a battle not counted is believed there
    assert moods.ships['Gangut'].mood == 120


@pytest.mark.parametrize('read, name, same', [
    ('Arizona', 'Arizona', True),
    ('G angut', 'Gangut', True),
    # Long names cut on the card
    ('Arizona MET..', 'Arizona META', True),
    ('Ning Hai (R..', 'Ning Hai (Retrofit', True),
    ('Maury (Retr..', 'Maury (Retr', True),
    # Other ships: Arizona META on screen was tapped for Arizona, three times, and the dorm stayed full
    ('Arizona META..', 'Arizona', False),
    ('Arizona', 'Arizona META', False),
    ('Little Illu..', 'Illustrious', False),
    ('Illustrious', 'Little Illustrious', False),
])
def test_names_of_other_ships_on_the_picker(read, name, same):
    assert (picker_name_similarity(read, name) > 1) is same


def test_dorm_out_of_food_is_ignored():
    # The dorm asked for food as Maury came in, the picker waited behind the popup until the game was restarted.
    # A frame of the main account at 1280x720.
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    image[480:570, 400:880] = load('dorm_food_popup.png')
    rest = DormRest.__new__(DormRest)
    rest.device = SimpleNamespace(image=image, click=Mock())
    rest.interval_timer = {}

    assert is_dorm_food_popup(image)
    assert not is_dorm_food_popup(np.zeros((720, 1280, 3), dtype=np.uint8))
    assert rest.handle_dorm_food_popup()
    assert rest.device.click.call_args[0][0].name == 'DORM_FOOD_IGNORE'


def test_unrecognized_fleet_badge_preserves_membership_even_when_level_is_readable():
    # Hakuhou of fleet 1, whose level reads 0 on her card, was read in no fleet on the second floor
    moods = ShipMoods(Config())
    moods.ships['Hakuhou'] = ShipMood('Hakuhou', mood=147, time=NOW - timedelta(minutes=15), place='rest', speed=50,
                                      fleet=1, level=82)
    moods.ships['Tiger'] = ShipMood('Tiger', mood=119, time=NOW - timedelta(minutes=15), fleet=2, level=100)
    moods.ships['Hasty'] = ShipMood('Hasty', mood=122, time=NOW - timedelta(minutes=15), fleet=1, level=74)
    dorm = [DormShip(place='rest', name='Hakuhou', level=82, mood=150, speed=50)]
    cards = [card(0, 'Hakuhou', fleet=0, level=0),
             # A readable level does not prove that an unmatched badge is absent.
             card(1, 'Tiger', fleet=0, level=100), card(2, 'Hasty', fleet=0, level=74)]

    update_moods(moods, dorm, cards, NOW)

    assert moods.ships['Hakuhou'].fleet == 1
    assert moods.ships['Tiger'].fleet == 2
    assert moods.ships['Hasty'].fleet == 1


@pytest.mark.parametrize('old_fleet, observed_fleet, expected', [(1, 2, 2), (0, 0, 0)])
def test_positive_badge_updates_membership_and_unknown_zero_does_not_create_one(old_fleet, observed_fleet, expected):
    moods = ShipMoods(Config())
    moods.ships['Hakuhou'] = ShipMood('Hakuhou', level=101, fleet=old_fleet, time=NOW)

    update_moods(moods, [], [card(1, 'Hakuhou', level=101, fleet=observed_fleet)], NOW)

    assert moods.ships['Hakuhou'].fleet == expected


def test_positive_submarine_identity_clears_surface_fleet_membership():
    moods = ShipMoods(Config())
    moods.ships['Whydah'] = ShipMood('Whydah', level=101, fleet=1, time=NOW)
    dorm = [DormShip(place='rest', name='Whydah', level=101, mood=150, speed=50)]

    update_moods(moods, dorm, [card(1, 'Whydah', level=101, fleet=1, submarine=True)], NOW)

    assert moods.ships['Whydah'].fleet == 0


def test_exact_picker_level_repairs_hakuhou_floor_digit_drop_without_losing_rest():
    moods = ShipMoods(Config())
    moods.ships['Hakuhou'] = ShipMood('Hakuhou', level=101, fleet=1, mood=145, time=NOW)
    dorm = [DormShip(place='rest', name='Hakuhou', level=11, mood=4, speed=50)]
    cards = [card(0, '', remove=True), card(1, 'Hakuhou', level=101, mood=145, fleet=0)]

    update_moods(moods, dorm, cards, NOW)

    known = moods.ships['Hakuhou']
    assert (known.level, known.fleet, known.mood, known.place, known.speed) == (101, 1, 145, 'rest', 50)
    assert dorm[0].known == cards[1].ship == 'Hakuhou'


@pytest.mark.parametrize('problem', [
    'duplicate_card', 'duplicate_known_name', 'truncated', 'similar_name', 'conflicting_level',
    'substituted_digit', 'unknown_stored_level', 'unknown_picker_level', 'missing_card', 'already_taken',
    'assigned_other_ship',
])
def test_floor_level_repair_requires_independent_unique_exact_agreement(problem):
    moods = ShipMoods(Config())
    moods.ships['Hakuhou'] = ShipMood('Hakuhou', level=101, time=NOW)
    resident = DormShip(place='rest', name='Hakuhou', level=11, mood=4, speed=50)
    cards = [card(1, 'Hakuhou', level=101)]
    taken = ()
    if problem == 'duplicate_card':
        cards.append(card(2, 'Hakuhou', level=101))
    elif problem == 'duplicate_known_name':
        moods.ships['other'] = ShipMood('Haku hou', level=101, time=NOW)
    elif problem == 'truncated':
        cards[0].truncated = True
    elif problem == 'similar_name':
        cards[0].name = 'Hakuhou META'
    elif problem == 'conflicting_level':
        cards[0].level = 105
    elif problem == 'substituted_digit':
        resident.level = 12
    elif problem == 'unknown_stored_level':
        moods.ships['Hakuhou'].level = 0
    elif problem == 'unknown_picker_level':
        cards[0].level = 0
    elif problem == 'missing_card':
        cards = []
    elif problem == 'already_taken':
        taken = ('Hakuhou',)
    elif problem == 'assigned_other_ship':
        cards[0].ship = 'Another'

    assert resident_level(moods, resident, cards, taken=taken) == resident.level


@pytest.mark.parametrize('place, fleet', [('rest', 1), ('dock', 1), ('dock', 0)])
def test_fresh_one_level_low_ocr_never_lowers_stored_level(place, fleet):
    moods = ShipMoods(Config())
    moods.ships['Hakuhou'] = ShipMood('Hakuhou', level=101, fleet=1, time=NOW)
    dorm = [DormShip(place='rest', name='Hakuhou', level=100, mood=145, speed=50)] if place == 'rest' else []

    update_moods(moods, dorm, [card(1, 'Hakuhou', level=100, fleet=fleet)], NOW)

    assert moods.ships['Hakuhou'].level == 101


@pytest.mark.parametrize('still_resident', [True, False])
def test_post_confirmation_repairs_floor_level_only_for_freshly_observed_resident(monkeypatch, still_resident):
    moods = ShipMoods(Config())
    moods.ships['Hakuhou'] = ShipMood('Hakuhou', level=101, fleet=1, mood=145, time=NOW)
    moods.ships['Gangut'] = ShipMood('Gangut', level=100, fleet=1, mood=100, time=NOW)
    moods.save = Mock()
    monkeypatch.setattr('module.mood.dorm_rest.ShipMoods', lambda config: moods)
    monkeypatch.setattr('module.mood.dorm_rest.upcoming_jobs', lambda *args: [])
    monkeypatch.setattr('module.mood.dorm_rest.rest_plan', lambda *args, **kwargs: ['Hakuhou', 'Gangut'])
    rest = DormRest.__new__(DormRest)
    rest.config = SimpleNamespace(data={})
    rest.device = SimpleNamespace()
    for name in ('ui_ensure', 'dorm_ship_list_enter', 'picker_enter', 'dock_filter_set',
                 'dock_sort_method_dsc_set', 'picker_confirm', 'dorm_ship_list_quit'):
        setattr(rest, name, Mock())
    haku = lambda: DormShip(place='rest', name='Hakuhou', level=11, mood=4, speed=50)
    gangut = lambda: DormShip(place='rest', name='Gangut', level=100, mood=100, speed=50)
    after = [haku(), gangut()] if still_resident else [gangut()]
    rest.dorm_floor_read = Mock(side_effect=[([], 6), ([haku()], 6), (after, 6)])
    rest.picker_scan = Mock(return_value=[card(0, '', remove=True),
                                        card(1, 'Hakuhou', level=101, fleet=1, mood=145),
                                        card(2, 'Gangut', level=100, fleet=1, mood=100)])
    rest.picker_apply = Mock(return_value=True)

    rest.rest_ships()

    assert moods.ships['Hakuhou'].level == 101
    assert moods.ships['Hakuhou'].place == ('rest' if still_resident else 'dock')
    assert moods.ships['Hakuhou'].speed == (50 if still_resident else 20)
    rest.picker_confirm.assert_called_once()


def test_failed_required_selection_cancels_partial_plan_and_retries_without_saving(monkeypatch):
    moods = ShipMoods(Config())
    moods.ships['Hakuhou'] = ShipMood('Hakuhou', level=101, fleet=1, mood=145, time=NOW)
    moods.ships['Gangut'] = ShipMood('Gangut', level=100, fleet=1, mood=100, time=NOW)
    moods.save = Mock()
    monkeypatch.setattr('module.mood.dorm_rest.ShipMoods', lambda config: moods)
    monkeypatch.setattr('module.mood.dorm_rest.upcoming_jobs', lambda *args: [])
    monkeypatch.setattr('module.mood.dorm_rest.rest_plan', lambda *args, **kwargs: ['Gangut', 'Hakuhou'])
    rest = DormRest.__new__(DormRest)
    rest.config = SimpleNamespace(data={}, task_delay=Mock())
    rest.device = SimpleNamespace(click=Mock(), sleep=Mock(), screenshot=Mock(), click_record_clear=Mock())
    for name in ('ui_ensure', 'ui_goto_main', 'dorm_ship_list_enter', 'picker_enter', 'dock_filter_set',
                 'dock_sort_method_dsc_set', 'picker_confirm', 'dorm_ship_list_quit', 'handle_info_bar'):
        setattr(rest, name, Mock())
    rest.handle_dorm_food_popup = Mock(return_value=False)
    rest.dorm_floor_read = Mock(side_effect=[([], 6),
                                           ([DormShip(place='rest', name='Gangut', level=100,
                                                      mood=100, speed=50)], 6)])
    rest.picker_scan = Mock(return_value=[card(0, '', remove=True), card(1, 'Gangut', fleet=1),
                                        card(2, 'Hakuhou', level=101, fleet=1, mood=145)])
    rest.picker_find = Mock(return_value=(100, 76))
    # The first selection succeeds. Hakuhou then cannot enter a full picker on any attempt.
    rest.picker_selected_count = Mock(side_effect=[4, 5, 6, 6, 6, 6, 6, 6])

    rest.run()

    rest.picker_confirm.assert_called_once_with(DORM_PICKER_CANCEL)
    assert rest.device.click.call_count == 4
    rest.config.task_delay.assert_called_once_with(minute=10)
    rest.ui_goto_main.assert_called_once()
    moods.save.assert_not_called()
    assert rest.dorm_floor_read.call_count == 2
    rest.dock_filter_set.assert_not_called()


def test_badge_diagnostic_saves_current_frame_and_keeps_at_most_ten_per_account(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    dorm = DormRest.__new__(DormRest)
    dorm.config = SimpleNamespace(config_name='alas')
    dorm.device = SimpleNamespace(image=np.zeros((720, 1280, 3), dtype=np.uint8))
    moods = ShipMoods(Config())
    moods.ships['Hakuhou'] = ShipMood('Hakuhou', level=101, fleet=1)
    folder = tmp_path / 'log' / 'dorm_rest'
    folder.mkdir(parents=True)
    for index in range(10):
        (folder / f'2000-01-01_00-00-{index:02}_alas_unread_fleet.png').write_bytes(b'old')
    (folder / '2000-01-01_00-00-00_f2p_unread_fleet.png').write_bytes(b'other account')

    assert dorm.picker_badge_diagnostic([card(1, 'Hakuhou', level=101)], moods)

    files = list(folder.glob('*_alas_unread_fleet.png'))
    assert len(files) == 10
    actual = [path for path in files if not path.name.startswith('2000')]
    assert len(actual) == 1
    assert cv2.imread(str(actual[0])).shape == (720, 1280, 3)
    assert (folder / '2000-01-01_00-00-00_f2p_unread_fleet.png').exists()


def test_badge_diagnostic_failure_is_nonfatal_and_counts_as_the_scan_attempt(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    dorm = DormRest.__new__(DormRest)
    dorm.config = SimpleNamespace(config_name='alas')
    dorm.device = SimpleNamespace(image=None)
    moods = ShipMoods(Config())
    moods.ships['Hakuhou'] = ShipMood('Hakuhou', level=101, fleet=1)
    monkeypatch.setattr('module.mood.dorm_rest.save_image', Mock(side_effect=OSError('disk unavailable')))

    assert dorm.picker_badge_diagnostic([card(1, 'Hakuhou', level=101)], moods)


def test_scan_saves_only_one_unread_badge_frame_even_across_adaptive_retries(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    dorm, state = physical_picker(monkeypatch, overshoot=4)
    dorm.config = SimpleNamespace(config_name='alas', Emulator_ControlMethod='minitouch')
    moods = ShipMoods(Config())
    moods.ships['Tirpitz'] = ShipMood('Tirpitz', level=100, fleet=1)
    moods.ships['Isokaze'] = ShipMood('Isokaze', level=100, fleet=1)
    from module.mood import dorm_rest
    original_read = dorm_rest.read_picker_row

    def unread_badges(image, top, row):
        found = original_read(image, top, row)
        for found_card in found:
            if found_card.name in moods.ships:
                found_card.fleet = 0
        return found

    monkeypatch.setattr(dorm_rest, 'read_picker_row', unread_badges)
    save = Mock()
    monkeypatch.setattr(dorm_rest, 'save_image', save)

    found = dorm.picker_scan([], moods=moods)

    assert {'Tirpitz', 'Isokaze'} <= {c.name for c in found}
    assert state['resets'] == 1
    save.assert_called_once()
