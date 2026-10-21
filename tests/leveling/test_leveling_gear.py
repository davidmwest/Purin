import os

import cv2
import numpy as np
import pytest

import module.config.server as server
from module.leveling.gear import LevelingGear, augment_depot_layout, best_card, depot_card_levels, depot_cards_read, \
    depot_has_item, empty_gear_slots, gear_levels, gear_rarities, popup_read, tag_level
from module.leveling.policy import gear_item, name_match

DATA = os.path.join(os.path.dirname(__file__), 'data')


@pytest.fixture(autouse=True)
def en():
    previous = server.server
    server.set_server('en')
    yield
    server.set_server(previous)


def load(name):
    return cv2.cvtColor(cv2.imread(os.path.join(DATA, name)), cv2.COLOR_BGR2RGB)


def frame(*parts):
    """
    A screenshot of the EN client at 1280x720 of the main account, black but for the parts kept, (file, x, y).
    """
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    for name, x, y in parts:
        part = load(name)
        height, width = part.shape[:2]
        image[y:y + height, x:x + width] = part
    return image


def depot(state):
    """
    The depot of the Anti-Air slot of Isokaze: its title, the EQUIPPED switch and the first item.
    """
    return frame((f'depot_equipped_{state}_title.png', 90, 0), (f'depot_equipped_{state}_switch.png', 0, 480),
                 (f'depot_equipped_{state}_first.png', 143, 90))


class FakeDevice:
    def __init__(self, image):
        self.image = image


def gear_page(image):
    gear = LevelingGear.__new__(LevelingGear)
    gear.device = FakeDevice(image)
    return gear


def test_empty_gear_slots_of_the_info_tab():
    # Isokaze: a gun and torpedoes at +0, no anti-air nor auxiliary
    image = frame(('gear_info_isokaze.png', 690, 160))

    assert empty_gear_slots(image) == [2, 3, 4]


@pytest.mark.parametrize('state, equipped', [('on', True), ('off', False)])
def test_depot_and_its_switch(state, equipped):
    gear = gear_page(depot(state))

    assert gear.is_in_depot()
    assert gear.depot_lists_equipped() is equipped
    assert depot_has_item(gear.device.image)


def test_depot_without_items():
    image = depot('off')
    # Storage has nothing for the slot, the list is empty
    image[90:210, 143:261] = (40, 45, 60)

    assert not depot_has_item(image)


def test_not_in_depot_on_the_detail_page():
    gear = gear_page(frame(('gear_info_isokaze.png', 690, 160)))

    assert not gear.is_in_depot()


def test_enhancement_of_the_cards_of_the_depot():
    # Anti-air guns in storage: +0, +10, +10, +0, +10, +10, +0 on the first row
    image = frame(('depot_equipped_off_row.png', 137, 84))

    levels = depot_card_levels(image)

    assert levels[:7] == [0, 10, 10, 0, 10, 10, 0]
    # No cards below, black
    assert levels[7:] == [-1] * 14
    assert best_card(levels) == 1


@pytest.mark.parametrize('levels, best', [
    ([0, 10, 11, 10], 2),
    ([0, 0, 0], 0),
    ([-1, 0, 10], 2),
    # A dimmed first card, no card at all
    ([-1, 6], 1),
    ([-1, -1], -1),
])
def test_best_card(levels, best):
    assert best_card(levels) == best


def test_enhancement_of_her_gear():
    # Isokaze: a gun and torpedoes at +0, nothing else
    image = frame(('gear_info_isokaze.png', 690, 160))

    assert gear_levels(image) == [0, 0, 0, 0, 0]


@pytest.mark.parametrize('digit, level', [
    (10, 10), (13, 13), (2, 2), (0, 0),
    # The plus of the tag read as a 4, or its point
    (411, 11), (410, 10), (413, 13), (104, 10), (101, 10), (42, 2), (110, 10),
    # Both: a +6 Advanced Sonar was taken for +13 over gear at +10
    (464, 6), (461, 6), (4104, 10),
    # Unreadable, not the most enhanced
    (416, 0), (99, 0),
])
def test_level_of_a_tag(digit, level):
    assert tag_level(digit) == level


@pytest.mark.parametrize('name, popup, confirm', [
    ('gear_popup_buttons.png', True, False),
    ('gear_confirm_change_buttons.png', False, True),
    ('gear_confirm_empty_buttons.png', False, True),
    ('gear_tab_bottom.png', False, False),
])
def test_popups_of_gear(name, popup, confirm):
    # Unequip, Change, Enhance on gear in a slot; Cancel, Confirm on gear picked in the depot, for a slot with gear
    # or an empty one
    gear = gear_page(frame((name, 300, 600)))

    assert gear.is_gear_popup() is popup
    assert gear.is_gear_confirm() is confirm


def test_depot_of_a_change_has_a_remove_card_first():
    # Guns for Maury in storage: REMOVE, Twin 127mm +11, +10, +0, Twin 100mm +10, +0, Prototype +10
    image = frame(('depot_change_title.png', 90, 0), ('depot_change_off_row.png', 137, 84))

    assert gear_page(image).is_in_depot()
    levels = depot_card_levels(image)
    assert levels[:7] == [-1, 11, 10, 0, 10, 0, 10]
    assert best_card(levels) == 1


def test_tags_read_with_their_point():
    # Auxiliaries in storage: +0, +10, +0, +10, +10, an Advanced Sonar dimmed as she has one, +10
    image = frame(('depot_aux_off_row_2.png', 137, 440))

    assert depot_card_levels(image)[14:] == [0, 10, 0, 10, 10, -1, 10]


def test_rarity_of_her_gear_on_the_info_tab():
    # Isokaze: a grey gun, a blue torpedo, a gold anti-air gun, a gold Advanced Sonar, an empty auxiliary slot
    image = frame(('gear_info_icons_isokaze.png', 690, 125))

    assert gear_rarities(image, empty_gear_slots(image)) == ['grey', 'blue', 'gold', 'gold', None]


@pytest.mark.parametrize('tag, name, stats', [
    ('toolkit', 'Repair Toolkit', {'HP': 500}),
    ('seal', 'Seal of the Four Gods', {'HP': 550}),
    ('sg', 'SG Radar', {'HIT': 35, 'EVA': 15}),
    ('air_radar', 'High Performance Air Radar', {'AA': 100}),
])
def test_popup_of_gear_picked_in_the_depot(tag, name, stats):
    image = frame((f'popup_{tag}_title.png', 680, 88), (f'popup_{tag}_rows.png', 680, 272))

    item = popup_read(image, 'confirm')

    assert name_match(item['name'], name)
    assert item['stats'] == stats


def test_popup_of_gear_with_a_long_name():
    # The name scrolls, its start is cut
    image = frame(('popup_steering_title.png', 680, 88), ('popup_steering_rows.png', 680, 272))

    item = popup_read(image, 'confirm')

    assert name_match(item['name'], 'High Performance Hydraulic Steering Gear')
    assert item['stats'] == {'EVA': 40, 'HP': 60}


def test_popup_of_gear_in_a_slot():
    image = frame(('popup_worn_sonar_title.png', 440, 88), ('popup_worn_sonar_rows.png', 440, 272))

    assert popup_read(image, 'worn') == gear_item('Advanced Sonar', {'ASW': 35, 'HIT': 12, 'ASR': 8}, rarity='grey')


# Cards of the depot of the second auxiliary of Isokaze, EQUIPPED off, and where they are
AUX_CARDS = [('depot_aux_card_steering.png', 291, 84), ('depot_aux_card_sg.png', 607, 84),
             ('depot_aux_card_jclass.png', 923, 84), ('depot_aux_card_air_radar.png', 1080, 84),
             ('depot_aux_card_seal.png', 449, 262), ('depot_aux_card_toolkit.png', 607, 440)]


def test_cards_of_the_depot_their_name_rarity_and_enhancement():
    cards = depot_cards_read(frame(*AUX_CARDS))

    assert [(card['index'], card['rarity'], card['level']) for card in cards] == [
        (1, 'gold', 10), (3, 'gold', 10), (5, 'gold', 10), (6, 'gold', 10), (9, 'gold', 10), (17, 'purple', 10)]
    assert name_match(cards[0]['name'], 'High Performance Hydraulic Steering Gear')
    assert name_match(cards[1]['name'], 'SG Radar')
    assert name_match(cards[5]['name'], 'Repair Toolkit')


class ScreenDevice:
    """
    Shows a screen, a click on a button goes to the screen of moves[screen, button name] if any.
    """

    def __init__(self, moves, screen='tab'):
        depot_title = ('depot_change_title.png', 90, 0)
        self.screens = {
            'tab': frame(('gear_tab_bottom.png', 300, 600)),
            'popup': frame(('gear_popup_buttons.png', 300, 600)),
            'depot_on': frame(depot_title, ('depot_equipped_on_switch.png', 0, 480),
                              ('depot_change_row.png', 137, 84)),
            'depot_off': frame(depot_title, ('depot_equipped_off_switch.png', 0, 480),
                               ('depot_change_off_row.png', 137, 84)),
            'confirm': frame(('gear_confirm_change_buttons.png', 300, 600)),
            'worn_sonar': frame(('gear_popup_buttons.png', 300, 600), ('popup_worn_sonar_title.png', 440, 88),
                                ('popup_worn_sonar_rows.png', 440, 272)),
            'aux_on': frame(depot_title, ('depot_equipped_on_switch.png', 0, 480), *AUX_CARDS),
            'aux_off': frame(depot_title, ('depot_equipped_off_switch.png', 0, 480), *AUX_CARDS),
        }
        for tag in PICKS.values():
            self.screens[f'pick_{tag}'] = frame(('gear_confirm_empty_buttons.png', 300, 600),
                                                (f'popup_{tag}_title.png', 680, 88),
                                                (f'popup_{tag}_rows.png', 680, 272))
        self.moves = moves
        self.screen = screen
        self.clicks = []

    @property
    def image(self):
        return self.screens[self.screen]

    def screenshot(self):
        return self.image

    def click(self, button):
        self.clicks.append(button.name)
        self.screen = self.moves.get((self.screen, button.name), self.screen)

    def click_record_clear(self):
        pass

    def stuck_record_add(self, button):
        pass

    def sleep(self, second):
        pass


# Changing the gun of Maury, and the ways back
CHANGE = {
    ('tab', 'EQUIP_TAKE_ON_0'): 'popup',
    ('popup', 'GEAR_CHANGE'): 'depot_on',
    ('popup', 'GEAR_POPUP_CLOSE'): 'tab',
    ('depot_on', 'EQUIPPED_SWITCH'): 'depot_off',
    ('depot_on', 'BACK_ARROW'): 'tab',
    ('depot_off', 'DEPOT_CARD_0_1'): 'confirm',
    ('depot_off', 'BACK_ARROW'): 'tab',
    ('confirm', 'GEAR_CONFIRM'): 'tab',
    ('confirm', 'GEAR_CANCEL'): 'depot_off',
}
# Cards of the depot of auxiliaries and the popups they open
PICKS = {'DEPOT_CARD_0_1': 'steering', 'DEPOT_CARD_0_3': 'sg', 'DEPOT_CARD_0_5': 'jclass',
         'DEPOT_CARD_0_6': 'air_radar', 'DEPOT_CARD_1_2': 'seal', 'DEPOT_CARD_2_3': 'toolkit'}
# The auxiliary slots of Isokaze: the first wears an Advanced Sonar, the second is empty
AUXILIARY = {
    ('tab', 'EQUIP_TAKE_ON_3'): 'worn_sonar',
    ('worn_sonar', 'GEAR_CHANGE'): 'aux_on',
    ('worn_sonar', 'GEAR_POPUP_CLOSE'): 'tab',
    ('tab', 'EQUIP_TAKE_ON_4'): 'aux_on',
    ('aux_on', 'EQUIPPED_SWITCH'): 'aux_off',
    ('aux_on', 'BACK_ARROW'): 'tab',
    ('aux_off', 'BACK_ARROW'): 'tab',
}
for _card, _tag in PICKS.items():
    AUXILIARY['aux_off', _card] = f'pick_{_tag}'
    AUXILIARY[f'pick_{_tag}', 'GEAR_CANCEL'] = 'aux_off'
    AUXILIARY[f'pick_{_tag}', 'GEAR_CONFIRM'] = 'tab'
TOOLKIT = gear_item('Repair Toolkit', {'HP': 500}, 'purple', 10)
SEAL = gear_item('Seal of the Four Gods', {'HP': 550}, 'gold', 10)
STEERING = gear_item('High Performance Hydraulic Steering Gear', {'EVA': 40, 'HP': 60}, 'gold', 10)


def gear_on(device):
    gear = LevelingGear.__new__(LevelingGear)
    gear.device = device
    gear.interval_timer = {}
    return gear


@pytest.fixture
def short_steps(monkeypatch):
    monkeypatch.setattr('module.leveling.gear.GEAR_TIMEOUT', 1)


def test_a_weapon_below_gold_is_changed_for_gold_from_storage():
    device = ScreenDevice(CHANGE)

    # Her gun is purple at +0, storage has a gold one at +11
    put = gear_on(device).gear_slot_fill(0, hull='DD', current=gear_item(rarity='purple', level=0))

    assert (put['rarity'], put['level']) == ('gold', 11)
    assert device.clicks == ['EQUIP_TAKE_ON_0', 'GEAR_CHANGE', 'EQUIPPED_SWITCH', 'DEPOT_CARD_0_1', 'GEAR_CONFIRM']
    assert device.screen == 'tab'


def test_gear_stays_when_storage_has_nothing_better():
    device = ScreenDevice(CHANGE)

    assert gear_on(device).gear_slot_fill(0, hull='DD', current=gear_item(rarity='gold', level=11)) is None
    # No card is worth a look
    assert device.clicks == ['EQUIP_TAKE_ON_0', 'GEAR_CHANGE', 'EQUIPPED_SWITCH', 'BACK_ARROW']
    assert device.screen == 'tab'


def test_a_depot_that_does_not_open_closes_the_popup(short_steps):
    moves = dict(CHANGE)
    del moves['popup', 'GEAR_CHANGE']
    device = ScreenDevice(moves)

    assert gear_on(device).gear_slot_fill(0, hull='DD', current=gear_item(rarity='purple', level=0)) is None
    assert device.clicks[:2] == ['EQUIP_TAKE_ON_0', 'GEAR_CHANGE']
    assert device.clicks[-1] == 'GEAR_POPUP_CLOSE'
    assert device.screen == 'tab'


def test_a_confirm_that_does_not_go_through_is_cancelled(short_steps):
    moves = dict(CHANGE)
    del moves['confirm', 'GEAR_CONFIRM']
    device = ScreenDevice(moves)

    assert gear_on(device).gear_slot_fill(0, hull='DD', current=gear_item(rarity='purple', level=0)) is None
    assert device.clicks[-2:] == ['GEAR_CANCEL', 'BACK_ARROW']
    assert device.screen == 'tab'


def test_the_gear_tab_from_where_a_step_stopped():
    for screen in ('tab', 'popup', 'depot_on', 'depot_off', 'confirm', 'worn_sonar', 'aux_off', 'pick_seal'):
        device = ScreenDevice({**CHANGE, **AUXILIARY}, screen=screen)

        assert gear_on(device).gear_tab_ensure()
        assert device.screen == 'tab'


def test_gear_in_a_slot_is_read_on_its_popup():
    device = ScreenDevice(AUXILIARY)

    item = gear_on(device).gear_worn_read(3)

    assert (item['name'], item['stats']) == ('Advanced Sonar', {'ASW': 35, 'HIT': 12, 'ASR': 8})
    assert device.clicks == ['EQUIP_TAKE_ON_3', 'GEAR_POPUP_CLOSE']
    assert device.screen == 'tab'


def test_a_sonar_goes_for_a_repair_toolkit_on_a_destroyer():
    device = ScreenDevice(AUXILIARY)
    sonar = gear_item('Advanced Sonar', {'ASW': 35, 'HIT': 12, 'ASR': 8}, 'gold', 10)

    put = gear_on(device).gear_slot_fill(3, hull='DD', current=sonar)

    assert put['name'] == 'Repair Toolkit'
    # The toolkit is looked at first, nothing beats it
    assert device.clicks == ['EQUIP_TAKE_ON_3', 'GEAR_CHANGE', 'EQUIPPED_SWITCH', 'DEPOT_CARD_2_3', 'GEAR_CONFIRM']
    assert device.screen == 'tab'


def test_beside_a_toolkit_a_destroyer_gets_the_most_hp():
    device = ScreenDevice(AUXILIARY)

    put = gear_on(device).gear_slot_fill(4, hull='DD', current=None, other=TOOLKIT)

    assert put['name'] == 'Seal of the Four Gods'
    # Gold at +10 in the order of the depot, until gear that adds 400 HP or more
    assert device.clicks == ['EQUIP_TAKE_ON_4', 'EQUIPPED_SWITCH',
                             'DEPOT_CARD_0_1', 'GEAR_CANCEL', 'DEPOT_CARD_0_3', 'GEAR_CANCEL',
                             'DEPOT_CARD_0_5', 'GEAR_CANCEL', 'DEPOT_CARD_0_6', 'GEAR_CANCEL',
                             'DEPOT_CARD_1_2', 'GEAR_CONFIRM']


def test_the_best_gear_looked_at_is_picked_again():
    device = ScreenDevice(AUXILIARY)

    put = gear_on(device).gear_slot_fill(4, hull='CL', current=None)

    assert put['stats'] == {'EVA': 40, 'HP': 60}
    # The steering gear of a light cruiser, after MAX_LOOKS looks for an Improved Hydraulic Rudder
    assert device.clicks[:2] == ['EQUIP_TAKE_ON_4', 'EQUIPPED_SWITCH']
    assert device.clicks[2:-2:2] == ['DEPOT_CARD_0_1', 'DEPOT_CARD_0_6', 'DEPOT_CARD_0_3', 'DEPOT_CARD_2_3',
                                     'DEPOT_CARD_0_5', 'DEPOT_CARD_1_2']
    assert device.clicks[-2:] == ['DEPOT_CARD_0_1', 'GEAR_CONFIRM']


def gear_stubbed(levels, rarities, worn, picks):
    """
    LevelingGear of which steps are stubs: the Info tab shows levels and rarities, slots wear worn[index],
    and storage gives picks(index, current, other).
    """
    gear = gear_on(ScreenDevice({}))
    calls = []
    gear.gear_read_info = lambda: (list(levels), list(rarities))
    gear.gear_read = lambda: [10] * 6
    gear.ship_side_navbar_ensure = lambda bottom: None
    gear.gear_tab_ensure = lambda: True
    gear.gear_worn_read = lambda index: worn.get(index)

    def fill(index, hull=None, current=None, other=None):
        calls.append((index, current['name'] if current else None, other['name'] if other else None))
        return picks(index, current, other)

    gear.gear_slot_fill = fill
    gear.augment_slot_fill = lambda current=None: calls.append(('augment', current))
    return gear, calls


def test_gear_of_isokaze():
    # Grey, blue, gold at +0, an Advanced Sonar at +10, no second auxiliary, no augment
    sonar = {'name': 'Advanced Sonar', 'stats': {'ASW': 35}}

    def picks(index, current, other):
        if index in (0, 1, 2):
            return gear_item('Gun', rarity='gold', level=10)
        return SEAL if other and other['name'] == 'Repair Toolkit' else TOOLKIT

    gear, calls = gear_stubbed([0, 0, 0, 10, -1, 0], ['grey', 'blue', 'gold', 'gold', None], {3: sonar}, picks)

    assert gear.gear_fill(hull='DD') == [10] * 6
    assert calls == [(0, '', None), (1, '', None), (2, '', None), (3, 'Advanced Sonar', None),
                     (4, None, 'Repair Toolkit'), ('augment', 0)]


def test_a_destroyer_gets_the_toolkit_in_place_of_her_worse_auxiliary():
    # An Autoloader, purple, adds no HP, and an empty slot: the empty one is worse
    autoloader = gear_item('Autoloader', {'RLD': 35, 'FP': 7}, 'purple', 10)

    def picks(index, current, other):
        return SEAL if other and other['name'] == 'Repair Toolkit' else TOOLKIT

    gear, calls = gear_stubbed([10, 10, 10, 10, -1, 10], ['gold'] * 3 + ['purple', None], {3: autoloader}, picks)

    assert gear.gear_fill(hull='DD') == [10] * 6
    assert calls == [(4, None, 'Autoloader'), (3, 'Autoloader', 'Repair Toolkit')]


def test_gold_auxiliaries_she_wears_stay():
    gear, calls = gear_stubbed([10] * 6, ['gold'] * 5, {3: SEAL, 4: STEERING}, lambda *args: TOOLKIT)

    assert gear.gear_fill(hull='DD') == [10] * 6
    assert calls == []


def test_gear_the_policy_keeps_stays():
    shell = gear_item('Super Heavy Shell', {'FP': 15}, 'gold', 10)
    gear, calls = gear_stubbed([10] * 6, ['gold'] * 5, {3: shell, 4: TOOLKIT}, lambda *args: None)

    assert gear.gear_fill(hull='BB') == [10] * 6
    assert calls == []


def test_augments_in_storage_are_below_the_depot_header():
    # The depot of augments of Isokaze, EQUIPPED off: Craft first, then the augments in storage,
    # Hammer +10, Hammer, Hammer, Hammer, Dual Sword +10, Dual Sword +10, Dual Sword
    gear = gear_page(frame(('augment_depot_storage.png', 125, 320)))

    top = gear.augment_header_top()
    assert top == 330
    cards, tags = augment_depot_layout(top)
    # The second row is cut by the bottom bar
    assert len(cards) == 7
    levels = depot_card_levels(gear.device.image, cards, tags)
    assert levels == [10, 0, 0, 0, 10, 10, 0]
    assert best_card(levels) == 0


def test_no_augments_on_the_depot_of_gear():
    gear = gear_page(depot('off'))

    assert gear.augment_header_top() is None
    assert depot_card_levels(gear.device.image, [], []) == []
