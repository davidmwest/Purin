import pytest

from module.leveling.plan import (
    AWAKEN_CHIPS, HULL_ROW, HULL_WEIGHT, MAX_STARS, POWER_WEIGHTS, RETROFIT, ShipMatcher, Slot, Target, awaken_cost,
    awakenings_done, campaign_fleets, carry_targets, is_geared, leveling_queue, load_ship_data, name_key, plan_carries,
    plan_swaps, update_levels,
)

GEARED = [10, 10, 10, 10, 10]


@pytest.fixture(scope='module')
def data():
    return load_ship_data()


def ship(name, level, stars=6, exp_next=1000, affinity='stranger', gear=None, power=3000, index=0):
    """
    A ship as the Inventory task saves her.
    """
    return {
        'name': name, 'level': level, 'stars': stars, 'exp': 0 if exp_next else None, 'exp_next': exp_next,
        'affinity': affinity, 'power': power, 'gear_levels': gear or [0, 0, 0, 0, 0], 'index': index,
    }


def names(queue):
    return [target.ship for target in queue]


def test_ship_data_is_whole(data):
    assert len(data) > 1000
    # Every name has its own key, a name read from the dock finds one ship
    keys = [name_key(name) for name in data]
    assert len(keys) == len(set(keys))
    for ship in data.values():
        assert ship.rarity in AWAKEN_CHIPS, ship
        assert ship.hull in HULL_ROW and ship.hull in HULL_WEIGHT, ship
        if ship.tech is not None:
            stat, amount, hulls = ship.tech
            assert stat in POWER_WEIGHTS and amount in (1, 2) and set(hulls) <= set(HULL_ROW), ship


def test_ship_data_values(data):
    hakuhou = data['Hakuhou']
    assert (hakuhou.rarity, hakuhou.hull) == ('Ultra Rare', 'CV')
    assert hakuhou.lv100['hp'] == 7248 and hakuhou.lv120['avi'] == 472
    assert hakuhou.tech == ('avi', 1, ('CVL', 'CV'))
    # Limited to a hull of its group
    assert data['Bogue'].tech == ('asw', 1, ('CVL',))
    # A retrofit gives the bonus of the ship before it
    assert data['Laffey (Retrofit)'].tech == data['Laffey'].tech == ('fp', 1, ('DD',))
    # Bulins and collaborations give none
    assert data['Specialized Bulin Custom MKIII'].tech is None
    assert data['Nana Astar Deviluke'].tech is None


def test_matcher(data):
    matcher = ShipMatcher(data)
    # Names as the OCR of the detail page read them
    assert matcher.match('KGnigsberg').name == 'Königsberg'
    assert matcher.match('Shiranui Retrofit)').name == 'Shiranui' + RETROFIT
    assert matcher.match('Ayanami(Retrofit)').name == 'Ayanami' + RETROFIT
    assert matcher.match('Lmplacable').name == 'Implacable'
    assert matcher.match('Valparaiso').name == 'Valparaíso'
    assert matcher.match('Nothing like a ship') is None
    # The µ of muse ships reads as "u" or "H"
    assert matcher.match('Clevelandu').name == 'Cleveland µ'
    assert matcher.match('Boise H').name == 'Boise µ'
    assert matcher.match('Le Temeraireu').name == 'Le Téméraire µ'
    assert matcher.match('Kongouu').name == 'Kongou µ'
    assert matcher.match('Kongou').name == 'Kongou'
    assert matcher.match('Le Temeraire').name == 'Le Téméraire'
    assert matcher.match('Hakuhou').name == 'Hakuhou'
    # Awakenings of a retrofit cost as the ship before it
    crescent = matcher.match('Crescent (Retrofit)')
    assert crescent.rarity != 'Normal'
    assert matcher.base_rarity(crescent) == 'Normal'
    assert crescent.character == 'Crescent'


@pytest.mark.parametrize('level, capped, done', [
    (77, False, 0),
    (100, True, 0),
    (100, False, 1),
    (102, False, 1),
    (105, True, 1),
    (108, False, 2),
    (110, True, 2),
    (118, False, 4),
    (120, True, 4),
    (120, False, 5),
    (123, False, 5),
    (125, True, 5),
])
def test_awakenings_done(level, capped, done):
    assert awakenings_done(level, capped) == done


def test_awaken_cost():
    assert awaken_cost('Normal', 0, 120) == (660, 0)
    assert awaken_cost('Rare', 1, 120) == (800, 0)
    assert awaken_cost('Elite', 4, 120) == (0, 0)
    assert awaken_cost('Ultra Rare', 0, 125) == (3300 + 450, 225)
    assert awaken_cost('Ultra Rare', 4, 125) == (450, 225)
    assert awaken_cost('Decisive', 5, 125) == (0, 0)


def test_is_geared():
    assert is_geared({'gear_levels': GEARED})
    assert is_geared({'gear_levels': [10, 10, 10, 7, 10]})
    assert is_geared({'gear_levels': [13, 10, 11, 10, 0]})
    assert not is_geared({'gear_levels': [10, 0, 0, 10, 10]})
    assert not is_geared({'gear_levels': [0, 0, 0, 0, 0]})
    assert not is_geared({})


def test_ultra_rares_first(data):
    dock = [
        ship('Isokaze', 70, stars=5),
        ship('Hakuhou', 77, gear=GEARED),
        ship('Specialized Bulin Custom MKIII', 54),
        ship('Shinano', 125, exp_next=0),
    ]
    queue = leveling_queue(dock, data)
    assert names(queue) == ['Hakuhou', 'Isokaze', 'Specialized Bulin Custom MKIII']
    hakuhou, isokaze, bulin = queue
    assert (hakuhou.priority, hakuhou.goal, hakuhou.chips, hakuhou.arrays) == (0, 125, 3750, 225)
    assert hakuhou.geared and hakuhou.row == 'main'
    assert (isokaze.goal, isokaze.chips, isokaze.row, isokaze.geared) == (120, 880, 'vanguard', False)
    # A Bulin is an ultra rare but not part of it, she comes last and stops at 120
    assert (bulin.priority, bulin.goal) == (2, 120)


def test_ultra_rares_in_the_queue(data):
    # An account short of chips does not take ultra rares to 125 first
    dock = [ship('Hakuhou', 77), ship('Isokaze', 70, stars=5)]
    queue = leveling_queue(dock, data, ultra_to_125=False)
    assert {target.priority for target in queue} == {1}
    assert {target.ship: target.goal for target in queue} == {'Hakuhou': 120, 'Isokaze': 120}
    # 3 300 chips for an ultra rare against 880 for a rare
    assert names(queue) == ['Isokaze', 'Hakuhou']


def test_no_chips_left_first(data):
    dock = [
        ship('Isokaze', 70, stars=5),
        # Awakened to the cap of 120, two levels to go
        ship('London (Retrofit)', 118, stars=5, exp_next=190000),
    ]
    queue = leveling_queue(dock, data)
    assert names(queue) == ['London (Retrofit)', 'Isokaze']
    london = queue[0]
    assert london.chips == 0 and london.score == float('inf')
    assert london.rarity == 'Elite'


def test_left_out(data):
    dock = [
        # Not at max limit break, she can not be awakened
        ship('Isokaze', 70, stars=4),
        # Done
        ship('Cassin', 120, stars=4, exp_next=0),
        # Misread level
        ship('Yorktown', 1201),
        # Past 100, she has been awakened, at max limit break then whatever the stars read
        ship('Craven', 104, stars=3),
    ]
    assert names(leveling_queue(dock, data)) == ['Craven']


def test_best_copy(data):
    # Only the best copy counts, for Fleet Technology and for fleet power
    dock = [ship('Z20', 71, stars=4, index=1), ship('Z20', 85, stars=4, index=2)]
    queue = leveling_queue(dock, data)
    assert [(target.ship, target.index) for target in queue] == [('Z20', 2)]
    # A copy at 120 has done it
    dock = [ship('Z20', 71, stars=4), ship('Z20', 120, stars=4, exp_next=0)]
    assert leveling_queue(dock, data) == []
    # A retrofit is the same ship
    dock = [ship('Crescent', 72, stars=4), ship('Crescent (Retrofit)', 120, stars=4, exp_next=0)]
    assert leveling_queue(dock, data) == []
    # A muse ship is not
    dock = [ship('Le Temeraire', 54, stars=5), ship('Le Temeraireu', 120, stars=5, exp_next=0)]
    assert names(leveling_queue(dock, data)) == ['Le Téméraire']


def test_tech_counts_the_hull(data):
    # +1 accuracy, x2 in power, to every destroyer of the dock
    dock = [ship('Isokaze', 70, stars=5)] + [ship(name, 120, stars=4, exp_next=0) for name in ['Cassin', 'Downes']]
    isokaze = leveling_queue(dock, data)[0]
    assert isokaze.tech == 2 * 3
    # Gain of her own stats from 100 to 120
    expected = data['Isokaze'].power_per_level() * 20
    assert isokaze.gain == round(expected, 1)
    assert isokaze.score == pytest.approx(HULL_WEIGHT['DD'] * (isokaze.gain + isokaze.tech) / 880, rel=1e-3)


def test_affinity(data):
    plain = leveling_queue([ship('Isokaze', 70, stars=5)], data)[0]
    oath = leveling_queue([ship('Isokaze', 70, stars=5, affinity='oath')], data)[0]
    assert oath.gain == pytest.approx(plain.gain * 1.09, abs=0.1)


def test_tie_goes_to_lower_rarity():
    def target(rarity, level):
        return Target(name='', ship='', hull='DD', row='vanguard', rarity=rarity, level=level, goal=120, chips=1,
                      arrays=0, gain=0, tech=0, score=0.5, priority=1, geared=False, index=0)

    queue = sorted([target('Elite', 90), target('Normal', 70), target('Normal', 80)], key=Target.sort_key)
    assert [(t.rarity, t.level) for t in queue] == [('Normal', 80), ('Normal', 70), ('Elite', 90)]


def test_max_stars_of_retrofits(data):
    # Stars follow the rarity before a retrofit, a common retrofit stays at 4
    matcher = ShipMatcher(data)
    nevada = matcher.match('Nevada (Retrofit)')
    assert MAX_STARS[matcher.base_rarity(nevada)] == 4


def test_levels_of_the_fleets_go_into_the_dock(data):
    dock = [ship('Hasty', 72, stars=5), ship('Cooper', 73, stars=5)]
    slots = [Slot(1, 'vanguard', 0, 'Hasty', 80), Slot(1, 'vanguard', 1, 'C ooper', 73), Slot(1, 'vanguard', 2, '', 0)]

    assert update_levels(dock, slots, data) == 1
    assert dock[0]['level'] == 80 and dock[0]['exp_next'] == 1
    assert dock[1]['level'] == 73
    # At a level cap she waits for her awakening
    update_levels(dock, [Slot(1, 'vanguard', 0, 'Hasty', 100)], data)
    assert dock[0]['exp_next'] == 0
    # A level misread lower does not take her down
    assert update_levels(dock, [Slot(1, 'vanguard', 0, 'Hasty', 7)], data) == 0
    assert dock[0]['level'] == 100


def queue_of(*rows):
    """
    Targets in queue order, (ship, row, geared)
    """
    return [Target(name=name, ship=name, hull='DD', row=row, rarity='Elite', level=70, goal=120, chips=1320,
                   arrays=0, gain=0, tech=0, score=1 - index / 100, priority=1, geared=geared, index=index)
            for index, (name, row, geared) in enumerate(rows)]


def slots_of(fleet, row, *names):
    return [Slot(fleet, row, index, name, 70) for index, name in enumerate(names)]


def plan_names(plans):
    return [((slot.fleet, slot.index), [target.ship for target in targets]) for slot, targets in plans]


def test_swaps_keep_the_top_and_fill_the_first_fleet_first(data):
    queue = queue_of(
        ('London (Retrofit)', 'vanguard', True),
        ('Hasty', 'vanguard', True),
        ('Cooper', 'vanguard', True),
        ('Vampire', 'vanguard', True),
        ('Ping Hai (Retrofit)', 'vanguard', True),
        ('Ning Hai (Retrofit)', 'vanguard', True),
        ('Vittorio Cuniberti', 'vanguard', True),
        ('Dmitri Donskoi', 'vanguard', True),
        ('Nana Astar Deviluke', 'vanguard', True),
        # Without gear she is not taken
        ('Isokaze', 'vanguard', False),
    )
    slots = slots_of(1, 'vanguard', 'Dmitri Donskoi', 'Ning Hai(Retrofit', 'Ping Hai Retrofit') \
        + slots_of(2, 'vanguard', 'London (Retrofit)', 'na Astar Deviluk', 'Vittorio Cuniberti')

    assert plan_names(plan_swaps(slots, queue, data)) == [
        ((1, 0), ['Hasty', 'Cooper', 'Vampire']),
        ((2, 1), ['Hasty', 'Cooper', 'Vampire']),
        ((2, 2), ['Hasty', 'Cooper', 'Vampire']),
    ]


def test_swaps_only_for_better_ships(data):
    queue = queue_of(
        ('Hasty', 'vanguard', True),
        ('Cooper', 'vanguard', True),
        ('Vampire', 'vanguard', True),
        ('Dmitri Donskoi', 'vanguard', True),
    )
    # Cooper is 2nd, below the top of 2 slots only by Hasty; Donskoi below everyone
    slots = slots_of(1, 'vanguard', 'Vampire', 'Dmitri Donskoi')
    assert plan_names(plan_swaps(slots, queue, data)) == [
        ((1, 0), ['Hasty', 'Cooper']),
        ((1, 1), ['Hasty', 'Cooper']),
    ]
    # The top in the slots, nothing to do
    slots = slots_of(1, 'vanguard', 'Cooper', 'Hasty')
    assert plan_swaps(slots, queue, data) == []


def test_swaps_of_empty_unknown_and_done_slots(data):
    queue = queue_of(('Hasty', 'vanguard', True), ('Cooper', 'vanguard', True), ('Gangut', 'main', True))
    slots = slots_of(1, 'vanguard', '', 'xq#z', 'Craven') + slots_of(1, 'main', 'Gangut', '', '')

    assert plan_names(plan_swaps(slots, queue, data)) == [
        # An empty slot takes a ship
        ((1, 0), ['Hasty', 'Cooper']),
        # A name not read stays, a ship done or not in the queue makes room
        ((1, 2), ['Hasty', 'Cooper']),
    ]


@pytest.mark.parametrize('fleet1, fleet2, order, fleets', [
    (1, 2, 'fleet1_mob_fleet2_boss', ([1], [2])),
    (1, 2, 'fleet1_all_fleet2_standby', ([1], [2])),
    (1, 2, 'fleet1_boss_fleet2_mob', ([2], [1])),
    (3, 0, 'fleet1_mob_fleet2_boss', ([3], [])),
])
def test_mob_and_boss_fleets(fleet1, fleet2, order, fleets):
    assert campaign_fleets(fleet1, fleet2, order) == fleets


def test_carries_are_the_most_powerful_geared_ships(data):
    dock = [
        ship('Eldridge (Retrofit)', 125, exp_next=0, gear=GEARED, power=6046),
        ship('Plymouth', 125, exp_next=0, gear=GEARED, power=5780),
        # Without gear, or below 100
        ship('Juneau (Retrofit)', 125, exp_next=0, power=5828),
        ship('Hasty', 72, gear=GEARED, power=3691),
        ship('Lion', 125, exp_next=0, gear=GEARED, power=6215),
    ]
    carries = carry_targets(dock, data)

    assert [(target.ship, target.row) for target in carries] == \
           [('Lion', 'main'), ('Eldridge (Retrofit)', 'vanguard'), ('Plymouth', 'vanguard')]


def test_boss_fleet_takes_carries_for_ships_below_100(data):
    dock = [ship(name, 125, exp_next=0, gear=GEARED, power=power)
            for name, power in [('Eldridge (Retrofit)', 6046), ('Plymouth', 5780), ('London (Retrofit)', 4554),
                                ('Lion', 6215)]]
    boss = slots_of(2, 'vanguard', 'London (Retrofit)', 'Cooper', 'Vampire') + slots_of(2, 'main', '', 'Hiei', 'Haruna')
    for slot, level in zip(boss, [118, 73, 77, 0, 108, 102]):
        slot.level = level
    mob = slots_of(1, 'vanguard', 'Hasty', 'Plymouth', 'Ping Hai Retrofit')

    plans = plan_carries(boss, boss + mob, carry_targets(dock, data), data)

    # Plymouth is in fleet 1 and London in fleet 2 already, an empty slot takes a carry too
    assert plan_names(plans) == [
        ((2, 0), ['Lion']),
        ((2, 1), ['Eldridge (Retrofit)']),
        ((2, 2), ['Eldridge (Retrofit)']),
    ]


def test_ships_of_the_boss_fleet_are_not_taken_into_the_mob_fleet(data):
    queue = queue_of(('Haruna', 'main', True), ('Gangut', 'main', True), ('Hiei', 'main', True),
                     ('Kansas', 'main', True), ('Akashi', 'main', True))
    mob = slots_of(1, 'main', 'Hakuhou', 'Gangut', 'Kansas')
    boss = slots_of(2, 'main', 'victorious META', 'Hiei', 'Haruna')

    # Hakuhou is not in this queue, Akashi is the one free
    assert plan_names(plan_swaps(mob, queue, data, others=boss)) == [((1, 0), ['Akashi'])]


def test_gear_from_storage_at_0_is_not_enough():
    # Two ships with gear at +0 from storage lost a battle of the mobs of 12-4
    assert not is_geared({'gear_levels': [0, 0, 0, 0, 0], 'equipped': True})


def test_ships_without_gear_stay_out(data, monkeypatch):
    monkeypatch.setattr('module.leveling.plan.GEARLESS_ROWS', ())
    queue = queue_of(('Isokaze', 'vanguard', False), ('Hasty', 'vanguard', True), ('Ping Hai (Retrofit)', 'vanguard', True))
    slots = slots_of(1, 'vanguard', 'Hasty', 'Ping Hai Retrofit')

    assert plan_swaps(slots, queue, data) == []


def test_ships_without_gear_come_in_where_rows_take_them(data, monkeypatch):
    monkeypatch.setattr('module.leveling.plan.GEARLESS_ROWS', ('vanguard',))
    queue = queue_of(('Isokaze', 'vanguard', False), ('Hasty', 'vanguard', True), ('Ping Hai (Retrofit)', 'vanguard', True))
    slots = slots_of(1, 'vanguard', 'Hasty', 'Ping Hai Retrofit')

    # Isokaze gets gear from storage as she comes in
    assert plan_names(plan_swaps(slots, queue, data)) == [((1, 1), ['Isokaze'])]
