import pytest

from module.leveling.policy import aux_allowed, card_order, gear_good, gear_ideal, gear_item, gear_score, is_asw, \
    is_better, name_match, parse_stats, rarity_of

# Gear of the depot of Isokaze, 2026-10-21, as its popups show it
TOOLKIT = gear_item('Repair Toolkit', {'HP': 500}, 'purple', 10)
SEAL = gear_item('Seal of the Four Gods', {'HP': 550}, 'gold', 10)
HEAVENLY = gear_item('Heavenly Armillary', {'HP': 550}, 'gold', 10)
JCLASS = gear_item('J-class Crown', {'HP': 350, 'LCK': 2}, 'gold', 10)
STEERING = gear_item('igh Performance Hydraulic Stee', {'EVA': 40, 'HP': 60}, 'gold', 10)
SG = gear_item('SG Radar', {'HIT': 35, 'EVA': 15}, 'gold', 10)
AIR_RADAR = gear_item('High Performance Air Radar', {'AA': 100}, 'gold', 10)
WHALE = gear_item('My Precious Whale', {'HP': 500}, 'purple', 10)
AUTOLOADER = gear_item('Autoloader', {'RLD': 35, 'FP': 7}, 'purple', 10)
SONAR = gear_item('Advanced Sonar', {'ASW': 35, 'HIT': 12, 'ASR': 8}, 'gold', 10)
SHELL = gear_item('Super Heavy Shell', {'FP': 15}, 'gold', 10)
FIRE_CONTROL = gear_item('Fire Control Radar', {'FP': 5, 'HIT': 10}, 'gold', 10)


def ranked(index, hull, items, other=None):
    scored = [(gear_score(index, hull, item, other), item['name']) for item in items]
    return [name for score, name in sorted(pair for pair in scored if pair[0] is not None)]


@pytest.mark.parametrize('color, rarity', [
    ((216, 174, 97), 'gold'), ((86, 130, 210), 'blue'), ((123, 123, 123), 'grey'), ((105, 100, 170), 'purple'),
    ((40, 185, 240), 'ultra'),
])
def test_rarity_by_color(color, rarity):
    assert rarity_of(color) == rarity


@pytest.mark.parametrize('read, wanted, match', [
    # Cut by a card
    ('Repair To..', 'Repair Toolkit', True),
    ('High Perf..', 'High Performance Hydraulic Steering Gear', True),
    # A title that scrolls, an OCR slip at its start
    ('iigh Performance Hydraulic Stee', 'High Performance Hydraulic Steering Gear', True),
    ('iigh Performance Hydraulic Stee', 'High Performance Fire Control', False),
    ('High Performance Air Radar', 'High Performance Fire Control', False),
    ('SGRadar', 'SG Radar', True),
    ('SG Radar', 'Fire Control Radar', False),
    ('Fire Control Radar', 'High Performance Fire Control', False),
    ('Type 1 AP Shell', 'Super Heavy Shell', False),
    ('', 'Repair Toolkit', False),
])
def test_names_as_read(read, wanted, match):
    assert name_match(read, wanted) is match


def test_stats_of_a_popup_as_read():
    assert parse_stats(['Asw', 'Hit', 'AS R', 'skills'], [35, 12, 8, 5003]) == {'ASW': 35, 'HIT': 12, 'ASR': 8}
    assert parse_stats(['HP', 'LcK', 'skills', ''], [350, 2, 11131055, 0]) == {'HP': 350, 'LCK': 2}
    assert parse_stats(['RLD', 'FP', 'Gear Properties ', 'Equippable by'], [35, 7, 0, 0]) == {'RLD': 35, 'FP': 7}
    assert parse_stats(['HP', 'skills', '', 'ca-unaaio-'], [500, 310015111, 0, 0]) == {'HP': 500}


def test_gear_against_submarines():
    assert is_asw('Advanced Sonar')
    assert is_asw('Depth Cha..')
    assert is_asw('Hedgehog')
    assert is_asw('Mystery Gear', {'ASW': 10})
    assert not is_asw('Repair Toolkit', {'HP': 500})
    assert not is_asw('Anti-Air Radar')


def test_stats_by_hull_type():
    assert not aux_allowed('DD', 'Advanced Sonar', {'ASW': 35})
    assert not aux_allowed('CL', 'Hydraulic Catapult', {'AVI': 25})
    assert aux_allowed('CV', 'Hydraulic Catapult', {'AVI': 25})
    assert not aux_allowed('BB', 'Torpedo Gear', {'TRP': 20})
    assert not aux_allowed('CVL', 'Torpedo Gear', {'TRP': 20})
    assert aux_allowed('DD', 'Torpedo Gear', {'TRP': 20})


def test_a_destroyer_gets_a_repair_toolkit_then_the_most_hp():
    gear = [AUTOLOADER, SG, STEERING, JCLASS, SEAL, WHALE, TOOLKIT, AIR_RADAR, SONAR]
    # Gold that adds the most HP, the first of HP, FP, EVA, HIT she wants, then purples of 400 HP or more
    assert ranked(3, 'DD', gear)[:6] == ['Repair Toolkit', 'Seal of the Four Gods', 'J-class Crown',
                                         'igh Performance Hydraulic Stee', 'SG Radar', 'High Performance Air Radar']
    # Beside a toolkit, one more is a purple of 500 HP: its repairs do not stack
    assert ranked(4, 'DD', gear, other=TOOLKIT)[:7] == [
        'Seal of the Four Gods', 'J-class Crown', 'igh Performance Hydraulic Stee', 'SG Radar',
        'High Performance Air Radar', 'My Precious Whale', 'Repair Toolkit']
    assert 'Advanced Sonar' not in ranked(3, 'DD', gear)


def test_other_hulls_get_gear_of_their_type_first():
    gear = [AIR_RADAR, WHALE, TOOLKIT, FIRE_CONTROL, SHELL, SONAR]
    assert ranked(3, 'BB', gear) == ['Super Heavy Shell', 'Fire Control Radar', 'Repair Toolkit',
                                     'High Performance Air Radar', 'My Precious Whale']
    assert ranked(3, 'CL', [SG, TOOLKIT, STEERING, AIR_RADAR, SEAL]) == [
        'igh Performance Hydraulic Stee', 'SG Radar', 'Repair Toolkit', 'Seal of the Four Gods',
        'High Performance Air Radar']
    # One toolkit is enough
    assert ranked(4, 'BB', [TOOLKIT, AIR_RADAR], other=TOOLKIT) == ['High Performance Air Radar', 'Repair Toolkit']


def test_gear_at_10_first():
    toolkit_0 = dict(TOOLKIT, level=0)
    assert ranked(3, 'DD', [toolkit_0, SEAL]) == ['Seal of the Four Gods', 'Repair Toolkit']
    assert ranked(3, 'BB', [dict(SHELL, level=0), TOOLKIT]) == ['Repair Toolkit', 'Super Heavy Shell']


def test_weapons_gold_first_then_the_most_enhanced():
    gold_0 = gear_item('Twin 127mm', rarity='gold', level=0)
    gold_11 = gear_item('Twin 127mm', rarity='gold', level=11)
    purple_10 = gear_item('Twin 127mm', rarity='purple', level=10)
    ultra_10 = gear_item('Prototype', rarity='ultra', level=10)
    assert ranked(0, 'DD', [purple_10, gold_0, ultra_10, gold_11]) == ['Twin 127mm', 'Prototype', 'Twin 127mm',
                                                                        'Twin 127mm']
    assert gear_score(0, 'DD', gold_11) < gear_score(0, 'DD', ultra_10) < gear_score(0, 'DD', gold_0) \
        < gear_score(0, 'DD', purple_10)
    assert gear_score(2, 'DD', gear_item('Depth Charge', rarity='gold', level=10)) is None


def test_gear_she_keeps():
    # Isokaze: a grey gun, a blue torpedo, a gold anti-air gun at +0, an Advanced Sonar at +10
    assert not gear_good(0, 'DD', gear_item(rarity='grey', level=0))
    assert not gear_good(2, 'DD', gear_item(rarity='gold', level=0))
    assert gear_good(2, 'DD', gear_item(rarity='gold', level=10))
    assert not gear_good(1, 'DD', gear_item(rarity='purple', level=10))
    assert not gear_good(3, 'DD', SONAR)
    assert not gear_good(3, 'DD', None)
    # Auxiliaries at +10 stay if gold or a purple of 400 HP or more
    assert gear_good(3, 'DD', TOOLKIT, other=SEAL)
    assert gear_good(4, 'DD', SEAL, other=TOOLKIT)
    assert gear_good(4, 'DD', SEAL, other=STEERING)
    assert gear_good(4, 'DD', WHALE, other=TOOLKIT)
    assert not gear_good(4, 'DD', AUTOLOADER, other=TOOLKIT)
    assert not gear_good(4, 'DD', dict(SEAL, level=6), other=TOOLKIT)
    assert gear_good(3, 'BB', TOOLKIT, other=SHELL)
    assert gear_good(3, 'BB', AIR_RADAR)
    # Not what the rules leave out
    assert not gear_good(3, 'BB', gear_item('Torpedo Gear', {'TRP': 20}, 'gold', 10))
    assert not gear_good(3, 'DD', gear_item('Hydraulic Catapult', {'AVI': 25}, 'gold', 10))


def test_when_the_look_through_the_depot_ends():
    assert gear_ideal(0, 'DD', gear_score(0, 'DD', gear_item(rarity='purple', level=0)))
    assert not gear_ideal(0, 'DD', None)
    assert gear_ideal(3, 'DD', gear_score(3, 'DD', TOOLKIT))
    assert gear_ideal(4, 'DD', gear_score(4, 'DD', SEAL, TOOLKIT))
    assert not gear_ideal(4, 'DD', gear_score(4, 'DD', JCLASS, TOOLKIT))
    # Without a toolkit, one may be further in the depot
    assert not gear_ideal(3, 'DD', gear_score(3, 'DD', SEAL))
    assert not gear_ideal(3, 'DD', gear_score(3, 'DD', dict(TOOLKIT, level=0)))
    assert gear_ideal(3, 'BB', gear_score(3, 'BB', SHELL))
    assert not gear_ideal(3, 'BB', gear_score(3, 'BB', FIRE_CONTROL))


def test_better():
    assert is_better((0, -10, 1), None)
    assert is_better((0, -11, 1), (0, -10, 1))
    assert not is_better((0, -10, 1), (0, -10, 1))
    assert not is_better(None, None)


def test_cards_of_the_depot_in_the_order_they_are_looked_at():
    # The depot of the second auxiliary of Isokaze, as its cards read
    cards = [
        {'index': 1, 'name': 'High Perf..', 'rarity': 'gold', 'level': 10},
        {'index': 2, 'name': 'High Perf..', 'rarity': 'gold', 'level': 0},
        {'index': 3, 'name': 'SG Radar', 'rarity': 'gold', 'level': 10},
        {'index': 9, 'name': 'Seal of t..', 'rarity': 'gold', 'level': 10},
        {'index': 15, 'name': 'My Precio.u', 'rarity': 'purple', 'level': 10},
        {'index': 17, 'name': 'Repair To.', 'rarity': 'purple', 'level': 10},
        {'index': 18, 'name': 'Repair To.', 'rarity': 'purple', 'level': 0},
        {'index': 19, 'name': 'Depth Cha..', 'rarity': 'gold', 'level': 10},
    ]
    order = [card['index'] for card in card_order(3, 'DD', cards)]
    # Below +10 last
    assert order == [17, 1, 3, 9, 15, 18, 2]
    order = [card['index'] for card in card_order(4, 'DD', cards, other=TOOLKIT)]
    assert order == [1, 3, 9, 15, 17, 2, 18]
    # Weapons: gold first
    order = [card['index'] for card in card_order(0, 'DD', cards)]
    assert order == [1, 3, 9, 2, 15, 17, 18]
