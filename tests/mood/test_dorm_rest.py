import os
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import cv2
import numpy as np
import pytest

import module.config.server as server
from module.base.button import Button
from module.base.utils import crop
from module.map.lineup_rotation import SLOTS_PER_FLEET, lineup_slot
from module.mood.dorm_rest import DormRest, PickerCard, DormShip, dorm_mood, is_dorm_food_popup, picker_drawn, \
    picker_fleets_ended, picker_is_top, picker_low_mood_ended, picker_name_similarity, picker_row_tops, \
    read_dorm_floor, read_picker_row, ship_list_tab, update_moods
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
    # Counted on the second floor until now, out of it from now on
    assert (left.mood, left.time, left.place, left.speed, left.fleet) == (150, NOW, 'dock', 20, 0)
    kawakaze = moods.ships['Kawakaze META']
    assert (kawakaze.mood, kawakaze.place, kawakaze.speed, kawakaze.fleet, kawakaze.time) == (124, 'rest', 50, 1, NOW)
    assert (moods.ships['Hiei'].place, moods.ships['Hiei'].fleet) == ('train', 2)
    london = moods.ships['London (Ret']
    assert (london.mood, london.place, london.speed, london.level, london.fleet) == (119, 'dock', 20, 118, 2)
    assert moods.read == NOW


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
