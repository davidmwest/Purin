from dataclasses import replace

import pytest

from module.dock.policy import Card, decide, summary, verdict_line

# Rarities of the ship data
RARITY = {
    'Aulick': 'Normal', 'Cassin': 'Normal', 'Downes': 'Normal', 'Leander': 'Normal',
    'Benson': 'Rare', 'Hammann': 'Rare', 'Long Island': 'Rare',
    'Laffey': 'Elite', 'Ayanami': 'Elite', 'Universal Bulin': 'Elite',
    'Belfast': 'Super Rare', 'Prototype Bulin MKII': 'Super Rare',
    'Leander (Retrofit)': 'Rare',
}
RETROFITS = ('Leander', 'Ayanami')


def card(ship='Aulick', level=1, **fields):
    """
    A free and unlocked card, read and matched as her ship, her frame of her rarity.
    """
    rarity = RARITY.get(ship, '')
    values = dict(index=0, read=ship, ship=ship, rarity=rarity, frame=rarity, level=level, locked=False, fleet=0,
                  status='free', bulin='Bulin' in ship, retrofit=ship in RETROFITS)
    values.update(fields)
    return Card(**values)


def dock(*cards):
    """
    Returns:
        list[Card]: The cards, indexed in the order given
    """
    return [replace(one, index=index) for index, one in enumerate(cards)]


def actions(cards, needs=None, **kwargs):
    return [verdict.action for verdict in decide(cards, needs or {}, **kwargs)]


def verdict_of(one, needs=None):
    return decide([one], needs or {})[0]


# Rule 1


def test_locked_kept():
    verdict = verdict_of(card(locked=True))
    assert (verdict.action, verdict.reason) == ('keep', 'locked')


def test_fleet_kept():
    verdict = verdict_of(card(fleet=2))
    assert (verdict.action, verdict.reason) == ('keep', 'in fleet 2')


@pytest.mark.parametrize('status, reason', [('commission', 'on commission'), ('battle', 'in battle'),
                                            ('dorm', 'in the dorm')])
def test_busy_kept(status, reason):
    verdict = verdict_of(card(status=status))
    assert (verdict.action, verdict.reason) == ('keep', reason)


def test_unknown_status_kept():
    verdict = verdict_of(card(status=''))
    assert (verdict.action, verdict.reason) == ('keep', 'status not read')


def test_odd_status_kept():
    assert verdict_of(card(status='sortie')).action == 'keep'


# Rule 2


def test_unmatched_name_kept():
    verdict = verdict_of(card(read='Au1..', ship='', rarity='', frame='Normal'))
    assert verdict.action == 'keep'
    assert 'Au1..' in verdict.reason


def test_unknown_rarity_kept():
    assert verdict_of(card(rarity='')).action == 'keep'


def test_misspelt_rarity_kept():
    verdict = verdict_of(card(rarity='Rarre', frame=''))
    assert (verdict.action, verdict.reason) == ('keep', 'rarity unknown')


def test_odd_frame_kept():
    verdict = verdict_of(card(frame='Purple?'))
    assert (verdict.action, verdict.reason) == ('keep', 'frame reads Purple?, Aulick is Normal')


def test_frame_disagreeing_kept():
    verdict = verdict_of(card(frame='Rare'))
    assert (verdict.action, verdict.reason) == ('keep', 'frame reads Rare, Aulick is Normal')


def test_frame_not_read_trusts_rarity():
    assert verdict_of(card(frame='')).action == 'enhance'


def test_rule_2_before_rule_4():
    # An unsure Long Island is not taken for her limit breaks
    assert verdict_of(card('Long Island', frame='Elite'), {'Long Island': 2}).action == 'keep'


# Rule 3


@pytest.mark.parametrize('rarity', ['Super Rare', 'Ultra Rare', 'Priority', 'Decisive'])
def test_super_rare_and_above_kept(rarity):
    verdict = verdict_of(card('Belfast', rarity=rarity, frame=rarity))
    assert (verdict.action, verdict.reason) == ('keep', rarity)


@pytest.mark.parametrize('ship', ['Universal Bulin', 'Prototype Bulin MKII'])
def test_bulin_kept_unlocked_at_level_1(ship):
    verdict = verdict_of(card(ship, level=1))
    assert (verdict.action, verdict.reason) == ('keep', 'a Bulin')


def test_retrofitted_kept():
    # A retrofitted Leander is a rare, past the commission pool she would be retired
    verdict = decide([card('Leander (Retrofit)', 20)], {}, commission_pool=0)[0]
    assert (verdict.action, verdict.reason) == ('keep', 'retrofitted')


def test_retrofitted_not_taken_for_limit_breaks():
    cards = dock(card('Leander (Retrofit)', 20), card('Leander', 1))
    assert actions(cards, {'Leander': 1}) == ['keep', 'limit_break']


# Rule 4


def test_needs_reserve_their_copies_lowest_level_first():
    # Quick Select spends a copy whatever her level: the Lv 1 and Lv 12 copies go, the higher ones stay for commissions
    cards = dock(card('Long Island', 30), card('Long Island', 25), card('Long Island', 12), card('Long Island', 1))
    verdicts = decide(cards, {'Long Island': 2})
    assert [verdict.action for verdict in verdicts] == ['keep', 'keep', 'limit_break', 'limit_break']
    assert verdicts[3].reason == 'copy 1 of 2 for her limit breaks'
    assert verdicts[0].reason == 'commission pool, 1 of 24'


def test_needs_skip_cards_kept_before():
    cards = dock(card('Long Island', 50, fleet=1), card('Long Island', 1, locked=True), card('Long Island', 1),
                 card('Long Island', 1))
    assert actions(cards, {'Long Island': 1}) == ['keep', 'keep', 'retire', 'limit_break']


def test_fully_limit_broken_reserves_none():
    cards = dock(card('Laffey', 1), card('Laffey', 1))
    assert actions(cards, {'Laffey': 0}) == ['retire', 'retire']


def test_needs_of_a_retrofit_take_copies_of_the_ship_before_it():
    cards = dock(card('Ayanami', 1), card('Ayanami', 1))
    assert actions(cards, {'Ayanami (Retrofit)': 1}) == ['retire', 'limit_break']


def test_needs_of_a_ship_and_her_retrofit_add_up():
    cards = dock(card('Ayanami', 1), card('Ayanami', 1), card('Ayanami', 1), card('Ayanami', 1))
    verdicts = decide(cards, {'Ayanami': 1, 'Ayanami (Retrofit)': 2})
    assert [verdict.action for verdict in verdicts] == ['retire'] + ['limit_break'] * 3
    assert verdicts[1].reason == 'copy 3 of 3 for her limit breaks'


def test_needs_take_a_card_whose_level_was_not_read():
    # Rule 4 does not go by level
    assert verdict_of(card('Long Island', 0), {'Long Island': 1}).action == 'limit_break'


def test_needs_not_spent_on_super_rares():
    # Rule 3 comes first, a copy of Belfast is kept either way
    assert verdict_of(card('Belfast'), {'Belfast': 1}).action == 'keep'


# Rule 5


def test_retrofit_ship_keeps_one_copy():
    cards = dock(card('Leander', 2), card('Leander', 1), card('Leander', 1))
    verdicts = decide(cards, {})
    assert [verdict.action for verdict in verdicts] == ['keep', 'enhance', 'enhance']
    assert verdicts[0].reason == 'the copy kept to retrofit'


@pytest.mark.parametrize('kept', [dict(locked=True), dict(fleet=3), dict(status='commission')])
def test_retrofit_copy_not_kept_when_one_is(kept):
    # The card that keeps her comes after the free ones, it still counts
    cards = dock(card('Leander', 2), card('Leander', 1), card('Leander', 1, **kept))
    assert actions(cards) == ['enhance', 'enhance', 'keep']


def test_retrofit_copy_not_kept_when_one_is_for_limit_breaks():
    cards = dock(card('Ayanami', 2), card('Ayanami', 1))
    assert actions(cards, {'Ayanami': 1}) == ['retire', 'limit_break']


def test_retrofit_copy_not_kept_when_she_is_retrofitted():
    cards = dock(card('Leander (Retrofit)', 20), card('Leander', 1))
    assert actions(cards) == ['keep', 'enhance']


def test_retrofit_copy_kept_past_the_pool():
    cards = dock(card('Cassin', 20), card('Leander', 10))
    verdicts = decide(cards, {}, commission_pool=1)
    assert [(verdict.action, verdict.reason) for verdict in verdicts] == \
           [('keep', 'commission pool, 1 of 1'), ('keep', 'the copy kept to retrofit')]


def test_retrofit_copy_kept_when_the_other_card_is_doubtful():
    cards = dock(card('Leander', 1, locked=True, frame='Rare'), card('Leander', 1))
    assert actions(cards) == ['keep', 'keep']


# Rule 6


def test_commission_pool_keeps_the_highest():
    cards = dock(card('Aulick', 40), card('Benson', 30), card('Laffey', 25), card('Cassin', 12), card('Hammann', 4),
                 card('Downes', 3))
    verdicts = decide(cards, {}, commission_pool=3)
    assert [verdict.action for verdict in verdicts] == ['keep', 'keep', 'keep', 'retire', 'retire', 'enhance']
    assert verdicts[2].reason == 'commission pool, 3 of 3'
    assert verdicts[3].reason == 'above Lv.3, commission pool full'


def test_commission_pool_by_index_not_by_list_order():
    cards = [card('Aulick', 4, index=1), card('Cassin', 20, index=0)]
    assert actions(cards, commission_pool=1) == ['retire', 'keep']


def test_commission_pool_counts_only_its_own_keeps():
    # Cards above Lv 3 kept by rules 1 to 5 take no place in the pool
    cards = dock(card('Benson', 40, fleet=1), card('Laffey', 35, locked=True), card('Long Island', 30),
                 card('Leander', 20), card('Aulick', 10), card('Cassin', 5))
    verdicts = decide(cards, {'Long Island': 1}, commission_pool=1)
    assert [verdict.action for verdict in verdicts] == ['keep', 'keep', 'limit_break', 'keep', 'keep', 'retire']
    assert verdicts[3].reason == 'the copy kept to retrofit'
    assert verdicts[4].reason == 'commission pool, 1 of 1'


def test_material_level_max():
    cards = dock(card('Aulick', 5), card('Cassin', 5))
    assert actions(cards, commission_pool=0, material_level_max=5) == ['enhance', 'enhance']


# Rules 7 and 8


def test_common_extra_at_level_1_enhanced():
    verdict = verdict_of(card('Aulick', 1))
    assert (verdict.action, verdict.reason) == ('enhance', 'enhancement material')


def test_common_at_material_level_enhanced():
    assert verdict_of(card('Aulick', 3)).action == 'enhance'


def test_rare_extra_at_level_1_retired():
    verdict = verdict_of(card('Benson', 1))
    assert (verdict.action, verdict.reason) == ('retire', 'extra copy')


def test_elite_extra_retired():
    assert verdict_of(card('Laffey', 1)).action == 'retire'


def test_common_above_material_level_past_the_pool_retired():
    cards = dock(card('Cassin', 20), card('Aulick', 5))
    assert actions(cards, commission_pool=1) == ['keep', 'retire']


@pytest.mark.parametrize('ship', ['Aulick', 'Benson', 'Laffey'])
@pytest.mark.parametrize('level', [0, None, -1])
def test_level_not_read_kept(ship, level):
    # A rare or elite would be retired, a common fed
    verdict = verdict_of(card(ship, level))
    assert (verdict.action, verdict.reason) == ('keep', 'level not read')


# Levels out of the order of the dock


def test_level_lower_than_a_card_below_kept():
    # Lv.41 read as 1: she and the card below her are kept, the cards around them are not
    cards = dock(card('Benson', 50), card('Aulick', 1), card('Cassin', 40), card('Downes', 30), card('Hammann', 20))
    verdicts = decide(cards, {}, commission_pool=0)
    assert [verdict.action for verdict in verdicts] == ['retire', 'keep', 'keep', 'retire', 'retire']
    assert verdicts[1].reason == 'level out of order'
    assert verdicts[2].reason == 'level out of order'


def test_two_levels_misread_in_a_row_kept():
    cards = dock(card('Benson', 30), card('Hammann', 2), card('Laffey', 2), card('Long Island', 24),
                 card('Benson', 20))
    assert actions(cards, commission_pool=0) == ['retire', 'keep', 'keep', 'keep', 'retire']


def test_level_read_too_high_kept():
    # Lv.1 read as 11 would take a place in the pool, the cards above her read lower are kept as well
    cards = dock(card('Cassin', 5), card('Aulick', 1), card('Downes', 11), card('Aulick', 1))
    assert actions(cards, commission_pool=1) == ['keep', 'keep', 'keep', 'enhance']


def test_levels_compared_among_free_cards_out_of_fleets():
    # The dock of a fleet slot sorts the ships on commission after the free ones
    cards = dock(card('Aulick', 1), card('Cassin', 14, status='commission'), card('Downes', 14, fleet=2),
                 card('Hammann', 30, status=''), card('Aulick', 1))
    assert actions(cards) == ['enhance', 'keep', 'keep', 'keep', 'enhance']


def test_levels_not_read_not_compared():
    cards = dock(card('Aulick', 0), card('Cassin', 1), card('Downes', 1))
    assert actions(cards) == ['keep', 'enhance', 'enhance']


def test_locked_card_shows_a_misread():
    cards = dock(card('Aulick', 2), card('Laffey', 25, locked=True))
    verdicts = decide(cards, {})
    assert [(verdict.action, verdict.reason) for verdict in verdicts] == \
           [('keep', 'level out of order'), ('keep', 'locked')]


# The whole dock


def test_verdicts_in_the_order_of_the_cards():
    cards = [card('Benson', 1, index=3), card('Aulick', 1, index=1), card('Belfast', 90, index=0),
             card('Long Island', 1, index=2)]
    verdicts = decide(cards, {'Long Island': 1})
    assert [verdict.card for verdict in verdicts] == cards
    assert [verdict.action for verdict in verdicts] == ['retire', 'enhance', 'keep', 'limit_break']


def test_summary_counts():
    cards = dock(card('Belfast', 90), card('Long Island', 20), card('Cassin', 15), card('Aulick', 1),
                 card('Downes', 1), card('Benson', 1))
    verdicts = decide(cards, {'Long Island': 1})
    assert summary(verdicts) == {'keep': 2, 'limit_break': 1, 'enhance': 2, 'retire': 1}
    assert summary([]) == {'keep': 0, 'limit_break': 0, 'enhance': 0, 'retire': 0}


def test_verdict_line():
    assert verdict_line(verdict_of(card('Benson', 1))) == 'retire Benson Lv.1 (Rare): extra copy'
    unread = card(read='Au1..', ship='', rarity='', frame='', level=0)
    assert verdict_line(verdict_of(unread)) == 'keep "Au1.." Lv.? (rarity ?): "Au1.." matches no ship'
    assert verdict_line(verdict_of(card('Benson', -1))) == 'keep Benson Lv.? (Rare): level not read'
