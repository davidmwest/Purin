"""
Which ships to level next, from the dock as the Inventory task read it.

A ship is worth levelling for the fleet power she adds per cognitive chip still to pay for her:
- Her own stats past level 100, the levels that take awakenings. Stats grow evenly by level while every
  awakening costs more chips than the one before, so a first awakening is the cheapest power.
- The first time she reaches level 120, Fleet Technology adds a stat to every ship of her hull type.
  A common ship gives as much as an ultra rare, and nothing is given before 120,
  so a ship close to 120 is close to the best deal of the dock.
Power of a hull type that is used counts more, battleships over ammunition ships.
Lower rarity breaks a tie, its awakenings are cheaper.

On a mature account ultra rares go to level 125 before anyone else, chips and arrays are not short there.
Bulins are not part of it, they give no Fleet Technology and are levelled last.
"""
import difflib
import json
import os
import re
import unicodedata
from dataclasses import dataclass

DATA_FILE = os.path.join(os.path.dirname(__file__), 'ship_data.json')
RETROFIT = ' (Retrofit)'

# Power of a ship from her stats: HP*0.2 + FP + AA + TRP + AVI + RLD + ASW + EVA*2 + ACC*2
POWER_WEIGHTS = {'hp': 0.2, 'fp': 1, 'aa': 1, 'trp': 1, 'eva': 2, 'avi': 1, 'rld': 1, 'asw': 1, 'acc': 2}
# Stat bonus of affinity
AFFINITY_BONUS = {'friendly': 1.01, 'crush': 1.03, 'love': 1.06, 'oath': 1.09}

# Cognitive chips of the awakenings 100 to 105, 105 to 110, 110 to 115 and 115 to 120,
# by the rarity of a ship before her retrofit
AWAKEN_CHIPS = {
    'Normal': (60, 120, 180, 300),
    'Rare': (80, 160, 240, 400),
    'Elite': (120, 240, 360, 600),
    'Super Rare': (200, 400, 600, 1000),
    'Priority': (200, 400, 600, 1000),
    'Ultra Rare': (300, 600, 900, 1500),
    'Decisive': (300, 600, 900, 1500),
}
# Cognitive arrays and cognitive chips of Awakening+, 120 to 125
AWAKEN_PLUS = {
    'Normal': (40, 80),
    'Rare': (60, 120),
    'Elite': (100, 200),
    'Super Rare': (150, 300),
    'Priority': (150, 300),
    'Ultra Rare': (225, 450),
    'Decisive': (225, 450),
}
RARITY_ORDER = ['Normal', 'Rare', 'Elite', 'Super Rare', 'Priority', 'Ultra Rare', 'Decisive']
ULTRA = ('Ultra Rare', 'Decisive')
# Research ships show their limit break as stars too, up to the stars of the rarity
MAX_STARS = {'Normal': 4, 'Rare': 5, 'Elite': 5, 'Super Rare': 6, 'Priority': 6, 'Ultra Rare': 6, 'Decisive': 6}

# How much the power of a hull type is worth, by how much the type is used
HULL_WEIGHT = {
    'BB': 1.0, 'BC': 1.0, 'CV': 1.0,
    'CA': 0.9, 'CB': 0.9, 'CL': 0.9, 'DD': 0.9, 'CVL': 0.9,
    'BBV': 0.7, 'BM': 0.7, 'SS': 0.7, 'SSV': 0.7,
    'IXs': 0.5, 'IXv': 0.5, 'IXm': 0.5,
    'AR': 0.3, 'AE': 0.3,
}
# Row of a fleet a hull type goes in
HULL_ROW = {
    'BB': 'main', 'BC': 'main', 'BBV': 'main', 'CV': 'main', 'CVL': 'main', 'BM': 'main', 'AR': 'main',
    'IXm': 'main',
    'DD': 'vanguard', 'CL': 'vanguard', 'CA': 'vanguard', 'CB': 'vanguard', 'AE': 'vanguard', 'IXv': 'vanguard',
    'SS': 'submarine', 'SSV': 'submarine', 'IXs': 'submarine',
}
# A ship fights her share in a stage like 12-4, enemies at level 113, with this many gear slots enhanced
# to this level. A ship without gear sinks early and misses the EXP of the battles after.
# Gear of a destroyer adds less to her stats than the gear of a battleship, so the enhancements tell,
# the ships that clear 12-4 have +10 in all of their 5 slots.
GEARED_SLOTS = 4
GEARED_LEVEL = 6
# Ships at level 120 and above are done, unless they go to 125
LEVEL_DONE = 120
LEVEL_MAX = 125


@dataclass(frozen=True)
class ShipData:
    name: str
    # Rarity of this form, a retrofit is one rarity above the ship before it
    rarity: str
    hull: str
    lv100: dict
    lv120: dict
    # (stat, amount, hulls) given by Fleet Technology at level 120, or None
    tech: tuple

    @property
    def character(self):
        """
        A retrofit is the same ship as before it, to Fleet Technology and to the dock.
        """
        if self.name.endswith(RETROFIT):
            return self.name[:-len(RETROFIT)]
        return self.name

    @property
    def is_bulin(self):
        return 'Bulin' in self.name

    def power_per_level(self):
        """
        Returns:
            float: Power gained by a level past 100, stats grow evenly by level
        """
        return (power(self.lv120) - power(self.lv100)) / (LEVEL_DONE - 100)


def load_ship_data(file=DATA_FILE):
    """
    Returns:
        dict[str, ShipData]: Name of a ship on the wiki to her data
    """
    with open(file, encoding='utf-8') as f:
        data = json.load(f)
    keys = data['stats']
    ships = {}
    for name, rarity, hull, lv100, lv120, tech in data['ships']:
        if tech:
            stat, amount, hulls = tech
            tech = (stat, amount, tuple(hulls))
        ships[name] = ShipData(
            name=name, rarity=rarity, hull=hull,
            lv100=dict(zip(keys, lv100)), lv120=dict(zip(keys, lv120)), tech=tech)
    return ships


def power(stats):
    """
    Args:
        stats (dict[str, int | float]):

    Returns:
        float:
    """
    return sum(weight * stats.get(key, 0) for key, weight in POWER_WEIGHTS.items())


def is_geared(ship):
    """
    Args:
        ship (dict): Ship of the inventory, with the enhancement levels of her 5 gear slots.
            An empty slot and gear at +0 both read 0.

    Returns:
        bool: If she has gear to fight with
    """
    levels = ship.get('gear_levels') or []
    return sum(1 for level in levels if (level or 0) >= GEARED_LEVEL) >= GEARED_SLOTS


def name_key(name):
    """
    Letters and digits of a name, without accents, what the OCR of a name mostly gets right.
    The µ of a muse ship is kept as "mu", "Cleveland µ" is an other ship than Cleveland.
    """
    name = name.replace('µ', 'mu').replace('μ', 'mu')
    name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode()
    return re.sub(r'[^a-z0-9]', '', name.lower())


class ShipMatcher:
    """
    Finds a ship of the dock in the ship data, by her name read with OCR.
    """
    # Names closer than this to a name of the data are taken as it, "KGnigsberg" is Königsberg
    SIMILARITY = 0.8

    def __init__(self, ships):
        """
        Args:
            ships (dict[str, ShipData]):
        """
        self.ships = ships
        self.keys = {name_key(name): data for name, data in ships.items()}

    def match(self, name):
        """
        Args:
            name (str): Name read on the detail page of a ship

        Returns:
            ShipData: Or None if no ship is close
        """
        key = name_key(name)
        if key in self.keys:
            return self.keys[key]
        # OCR reads the µ of a muse ship as "u" or "H": "Clevelandu", "Boise H".
        # Kongou is found as she is above, "Kongouu" is Kongou µ.
        if key[-1:] in ('u', 'h') and key[:-1] + 'mu' in self.keys:
            return self.keys[key[:-1] + 'mu']
        close = difflib.get_close_matches(key, list(self.keys), n=1, cutoff=self.SIMILARITY)
        if close:
            return self.keys[close[0]]
        return None

    def base_rarity(self, data):
        """
        Awakenings of a retrofit cost as the ship before it.

        Args:
            data (ShipData):

        Returns:
            str:
        """
        if data.name.endswith(RETROFIT):
            base = self.ships.get(data.character)
            if base is not None:
                return base.rarity
        return data.rarity


def awakenings_done(level, capped):
    """
    Args:
        level (int):
        capped (bool): If the EXP of the ship is full, "MAX", she waits at the cap of her level

    Returns:
        int: Awakenings done, 4 when the cap is 120, 5 after Awakening+
    """
    if level < 100:
        return 0
    done = (level - 100) // 5
    if level % 5 or not capped:
        # Past a cap, or levelling towards the next
        done += 1
    return min(done, 5)


def awaken_cost(rarity, done, goal):
    """
    Args:
        rarity (str): Rarity before a retrofit
        done (int): Awakenings done
        goal (int): 120 or 125

    Returns:
        tuple[int, int]: Cognitive chips and cognitive arrays still to pay to reach the goal
    """
    chips = sum(AWAKEN_CHIPS[rarity][done:4])
    arrays = 0
    if goal > LEVEL_DONE and done < 5:
        plus_arrays, plus_chips = AWAKEN_PLUS[rarity]
        chips += plus_chips
        arrays += plus_arrays
    return chips, arrays


@dataclass
class Target:
    # Name as read from the dock
    name: str
    # Name on the wiki
    ship: str
    hull: str
    row: str
    # Before a retrofit, what awakenings cost
    rarity: str
    level: int
    goal: int
    chips: int
    arrays: int
    # Power of her own stats from here to the goal, the levels past 100
    gain: float
    # Power Fleet Technology adds to the ships of her hull type at 120
    tech: float
    # Power per chip, weighted by hull type. Infinite if no chips are left to pay.
    score: float
    # 0 for the ultra rares going to 125, 1 for the queue, 2 for the ships levelled last
    priority: int
    geared: bool
    # Place in the dock of the scan
    index: int

    def sort_key(self):
        if self.priority == 0:
            # Closest to 125 first
            first = -self.level
        else:
            first = -self.score
        return self.priority, first, RARITY_ORDER.index(self.rarity), -self.level


def leveling_queue(ships, data=None, ultra_to_125=True):
    """
    Args:
        ships (list[dict]): Ships of the inventory, from ./config/inventory/<config name>.json
        data (dict[str, ShipData]): Ship data, load_ship_data() if None
        ultra_to_125 (bool): If ultra rares go to level 125 before anyone else

    Returns:
        list[Target]: Ships to level, the first one first.
            Left out are the ships done, the ships that can not be awakened and copies of a ship behind
            the best one, only the best copy counts for Fleet Technology and for fleet power.
    """
    if data is None:
        data = load_ship_data()
    matcher = ShipMatcher(data)

    seen = []
    for ship in ships:
        level = ship.get('level') or 0
        # 1201 is a misread
        if not 1 <= level <= LEVEL_MAX:
            continue
        match = matcher.match(ship.get('name') or '')
        if match is None:
            continue
        seen.append((ship, match))

    # Ships of each hull type in the dock, Fleet Technology at 120 raises all of them
    hulls = {}
    for character in {match.character: match for _, match in seen}.values():
        hulls[character.hull] = hulls.get(character.hull, 0) + 1

    best = {}
    for ship, match in seen:
        current = best.get(match.character)
        if current is None or (ship['level'], ship.get('power') or 0) > (current[0]['level'], current[0].get('power') or 0):
            best[match.character] = (ship, match)

    queue = []
    for ship, match in best.values():
        rarity = matcher.base_rarity(match)
        ultra = ultra_to_125 and rarity in ULTRA and not match.is_bulin
        goal = LEVEL_MAX if ultra else LEVEL_DONE
        level = ship['level']
        if level >= goal:
            continue
        # An awakening takes a ship at max limit break, a ship past 100 has been through one
        if level <= 100 and (ship.get('stars') or 0) < MAX_STARS[rarity]:
            continue

        capped = not ship.get('exp_next')
        done = awakenings_done(level, capped)
        chips, arrays = awaken_cost(rarity, done, goal)
        gain = match.power_per_level() * (goal - max(level, 100))
        gain *= AFFINITY_BONUS.get(ship.get('affinity'), 1.0)
        tech = 0.
        if match.tech is not None and level < LEVEL_DONE:
            stat, amount, tech_hulls = match.tech
            tech = amount * POWER_WEIGHTS[stat] * sum(hulls.get(hull, 0) for hull in tech_hulls)
        weight = HULL_WEIGHT.get(match.hull, 0.5)
        score = weight * (gain + tech) / chips if chips else float('inf')
        if ultra:
            priority = 0
        elif match.is_bulin:
            priority = 2
        else:
            priority = 1

        queue.append(Target(
            name=ship.get('name'), ship=match.name, hull=match.hull, row=HULL_ROW.get(match.hull, 'vanguard'),
            rarity=rarity, level=level, goal=goal, chips=chips, arrays=arrays,
            gain=round(gain, 1), tech=round(tech, 1), score=score, priority=priority,
            geared=is_geared(ship), index=ship.get('index', -1),
        ))

    queue.sort(key=Target.sort_key)
    return queue
