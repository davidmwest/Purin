"""
Which gear goes in which slot of a ship for 12-4, the user's rules of 2026-10-20: decent, not optimal.

gear_score() is the whole choice, lower is better, None for gear that may not go on her:
- Weapons, slots 0 to 2: gold or better first, then the most enhanced. A purple only when no gold fits.
  Nothing against submarines.
- Auxiliaries, slots 3 and 4, those at +10 first:
    - Nothing against submarines, 12-4 has none: sonar, depth charges, hedgehogs, anything that adds ASW.
      No AVI but on a carrier, no TRP on a battleship nor a carrier.
    - A destroyer: a Repair Toolkit, +500 HP and repairs, the user's tip, then gear that adds HP, FP, EVA or HIT,
      the first of these it adds and the most of it. Gold first, then a purple that adds 400 HP or more.
    - Other hulls: gear of their hull type, in the order of HULL_AUX that ends with the Repair Toolkit, then any
      other gold, then a purple that adds 400 HP or more.
    - One Repair Toolkit is enough, its repairs do not stack.
- The augment: any at +10 the depot lists for the slot, they only fit their hull types (module.leveling.gear).

gear_good() keeps the gear a ship wears, no trip to the depot, the user's rule: gear she wears goes if the rules above
leave it out, if it is a weapon below gold, or below +10. Auxiliaries she wears at +10 stay if gold, or a purple
that adds 400 HP or more like the Repair Toolkit, ship-specific gear on her among them.
gear_ideal() ends the look through the popups of the depot: nothing it lists does much better.
card_order() is the order the cards of the depot are looked at, guessed from what a card shows: rarity,
enhancement, and the name cut by the game, "Repair To..".

Gear is a dict: name, stats {label: amount}, rarity, level (enhancement). Names are the English names of the game,
whole on a popup but for long ones that scroll and can be cut at both ends.
"""
import re
from difflib import SequenceMatcher

from module.leveling.plan import GEARED_LEVEL

# Rarity of a card by the color of its top left corner, RGB
RARITY_COLORS = {
    'ultra': (37, 190, 245),
    'gold': (219, 179, 100),
    'purple': (107, 104, 173),
    'blue': (88, 134, 213),
    'grey': (150, 150, 150),
}
RARITY_ORDER = ['ultra', 'gold', 'purple', 'blue', 'grey']
GOLD_PLUS = ('ultra', 'gold')
# Slots 0 to 2 hold weapons, 3 and 4 auxiliaries
WEAPON_SLOTS = (0, 1, 2)
AUX_SLOTS = (3, 4)

# Against submarines, of no use on 12-4. Words of their names, and names a card can cut
ASW_WORDS = ('sonar', 'depthcharge', 'hedgehog', 'asw', 'antisub')
ASW_NAMES = ('Sonar', 'Depth Charge', 'Hedgehog', 'Anti-Submarine')
AVIATION_HULLS = ('CV', 'CVL')
NO_TORPEDO_HULLS = ('BB', 'BC', 'BM', 'BBV', 'CV', 'CVL')
DESTROYERS = ('DD',)
TOOLKIT = 'Repair Toolkit'
# Auxiliaries of each hull type, by their name, the first ones first
HULL_AUX = {
    'CL': ('Improved Hydraulic Rudder', 'High Performance Hydraulic Steering Gear', 'High Performance Fire Control',
           'Fire Control Radar', 'SG Radar', TOOLKIT),
    'CA': ('Improved Hydraulic Rudder', 'High Performance Hydraulic Steering Gear', 'High Performance Fire Control',
           'Fire Control Radar', 'SG Radar', TOOLKIT),
    'CB': ('Super Heavy Shell', 'High Performance Fire Control', 'Fire Control Radar', TOOLKIT),
    'BB': ('Super Heavy Shell', 'Type 1 AP Shell', 'High Performance Fire Control', 'Fire Control Radar', TOOLKIT),
    'BC': ('Super Heavy Shell', 'Type 1 AP Shell', 'High Performance Fire Control', 'Fire Control Radar', TOOLKIT),
    'BBV': ('Super Heavy Shell', 'Type 1 AP Shell', TOOLKIT),
    'BM': ('Super Heavy Shell', 'Type 1 AP Shell', TOOLKIT),
    'CV': ('Steam Catapult', 'Hydraulic Catapult', TOOLKIT),
    'CVL': ('Steam Catapult', 'Hydraulic Catapult', TOOLKIT),
}
DEFAULT_AUX = (TOOLKIT,)
# Stats a destroyer wants of her second auxiliary, the first ones first
DD_AUX_STATS = ('HP', 'FP', 'EVA', 'HIT')
# A purple that adds this much HP is a fine fallback, the Repair Toolkit adds 500
HP_PURPLE = 400
# Stats of popups, as read: "Hit", "LcK", "AS R"
STAT_LABELS = ('HP', 'FP', 'TRP', 'AVI', 'AA', 'RLD', 'EVA', 'HIT', 'ASW', 'ASR', 'LCK', 'SPD', 'ACC', 'OXY')
# Rows of a popup after its stats
POPUP_ENDS = ('SKILL', 'GEARPROP', 'EQUIPPABLE')
# Popups of the depot looked at for a slot, each takes 2 seconds or so
MAX_LOOKS = 6


def gear_item(name='', stats=None, rarity='grey', level=0):
    return {'name': name, 'stats': dict(stats or {}), 'rarity': rarity, 'level': level}


def card_item(card):
    """
    Args:
        card (dict): Card of the depot: index, level, rarity, name cut by the game

    Returns:
        dict: What the card tells of its gear
    """
    return gear_item(name=card.get('name') or '', rarity=card.get('rarity'), level=card.get('level') or 0)


def rarity_of(color):
    """
    Args:
        color (tuple): Mean RGB of the top left corner of a card

    Returns:
        str: Nearest of RARITY_COLORS
    """

    def distance(name):
        return sum((a - b) ** 2 for a, b in zip(color, RARITY_COLORS[name]))

    return min(RARITY_COLORS, key=distance)


def rarity_index(rarity):
    return RARITY_ORDER.index(rarity) if rarity in RARITY_ORDER else len(RARITY_ORDER)


def name_key(name):
    return re.sub(r'[^a-z0-9]', '', str(name).lower())


def name_match(read, wanted):
    """
    Args:
        read (str): A name as read: cut by a card, "Repair To..", or by a title that scrolls, "igh Performance Hy"
        wanted (str): A whole name, "Repair Toolkit"

    Returns:
        bool: If what was read is that name, or a part of it
    """
    a, b = name_key(read), name_key(wanted)
    if len(a) < 4 or not b:
        return False
    if a in b or b in a:
        return True
    # A letter missed by the OCR
    match = SequenceMatcher(None, a, b).find_longest_match(0, len(a), 0, len(b))
    return match.size >= 0.8 * min(len(a), len(b))


def parse_stats(labels, values):
    """
    Args:
        labels (list[str]): Labels of the rows of a popup, as read
        values (list[int]): Their amounts

    Returns:
        dict[str, int]: Stats, like {'HP': 500}. Rows after them, Skills and such, are left out.
            Gear picked to replace other gear lists the stats of the other gear too, at 0: they are left out.
    """
    stats = {}
    for label, value in zip(labels, values):
        key = re.sub(r'[^A-Z]', '', str(label).upper())
        if any(key.startswith(end) for end in POPUP_ENDS):
            break
        if key in STAT_LABELS and int(value) > 0:
            stats[key] = int(value)
    return stats


def is_toolkit(name):
    return name_match(name or '', TOOLKIT)


def is_asw(name, stats=()):
    """
    Returns:
        bool: If gear is against submarines, by its name, maybe cut, or the stats it adds
    """
    key = name_key(name)
    if any(word in key for word in ASW_WORDS) or 'ASW' in stats:
        return True
    return any(name_match(name, asw) for asw in ASW_NAMES)


def aux_allowed(hull, name, stats=()):
    """
    Args:
        hull (str): Hull type of the ship, 'DD'
        name (str): Name of the auxiliary
        stats (iterable[str]): Stats it adds, like 'HP', 'AVI'

    Returns:
        bool: If it may go on her for 12-4
    """
    if is_asw(name, stats):
        return False
    if 'AVI' in stats and hull not in AVIATION_HULLS:
        return False
    if 'TRP' in stats and hull in NO_TORPEDO_HULLS:
        return False
    return True


def weapon_score(item):
    """
    Returns:
        tuple: Gold or better first, the most enhanced first. None if it is against submarines.
    """
    if is_asw(item.get('name') or '', item.get('stats') or {}):
        return None
    rarity = item.get('rarity')
    level = item.get('level') or 0
    return (0 if rarity in GOLD_PLUS else rarity_index(rarity), -level, rarity_index(rarity))


def aux_score(hull, item, other=None):
    """
    Args:
        hull (str): Hull type of the ship
        item (dict): An auxiliary
        other (dict): Her other auxiliary, None if none

    Returns:
        tuple: (below +10, group, order in the group, rarity, enhancement), lower is better.
            None if it may not go on her.
    """
    name = item.get('name') or ''
    stats = item.get('stats') or {}
    if not aux_allowed(hull, name, stats):
        return None
    rarity = item.get('rarity')
    level = item.get('level') or 0
    gold = rarity in GOLD_PLUS
    hp = stats.get('HP', 0)
    other_toolkit = other is not None and is_toolkit(other.get('name'))
    toolkit = is_toolkit(name)
    if hull in DESTROYERS:
        if toolkit and not other_toolkit:
            group, order = 0, ()
        else:
            stat = next((index for index, wanted in enumerate(DD_AUX_STATS) if stats.get(wanted, 0) > 0),
                        len(DD_AUX_STATS))
            amount = stats.get(DD_AUX_STATS[stat], 0) if stat < len(DD_AUX_STATS) else 0
            if gold:
                # The toolkit first, while she has none
                group = 1 if other_toolkit else 2
            else:
                group = 3 if hp >= HP_PURPLE else 4
            order = (stat, -amount)
    else:
        preferred = HULL_AUX.get(hull, DEFAULT_AUX)
        rank = next((index for index, start in enumerate(preferred) if name_match(name, start)), None)
        if toolkit and other_toolkit:
            rank = None
        if rank is not None:
            group, order = 0, (rank,)
        else:
            group, order = (1 if gold else 2 if hp >= HP_PURPLE else 3), (-hp,)
    return level < GEARED_LEVEL, group, order, rarity_index(rarity), -level


def gear_score(index, hull, item, other=None):
    """
    The choice of gear for a slot, see the docstring of this module.

    Args:
        index (int): Slot, 0 to 4
        hull (str): Hull type of the ship
        item (dict): Gear, None for none
        other (dict): Her other auxiliary, for slots 3 and 4

    Returns:
        tuple: Lower is better, None if it may not go on her
    """
    if item is None:
        return None
    if index in WEAPON_SLOTS:
        return weapon_score(item)
    return aux_score(hull, item, other)


def is_better(score, current):
    """
    Returns:
        bool: If gear of this score replaces gear of the current score, None for none or gear left out
    """
    if score is None:
        return False
    return current is None or score < current


def gear_good(index, hull, item, other=None):
    """
    Returns:
        bool: If she keeps the gear she wears in a slot, no trip to the depot
    """
    score = gear_score(index, hull, item, other)
    if score is None or (item.get('level') or 0) < GEARED_LEVEL:
        return False
    if item.get('rarity') in GOLD_PLUS:
        return True
    if index in WEAPON_SLOTS:
        return False
    return is_toolkit(item.get('name')) or (item.get('stats') or {}).get('HP', 0) >= HP_PURPLE


def gear_ideal(index, hull, score):
    """
    Args:
        index (int): Slot
        hull (str):
        score (tuple): gear_score() of gear of the depot

    Returns:
        bool: If no other gear of the depot is worth a look
    """
    if score is None:
        return False
    if index in WEAPON_SLOTS:
        # Cards are looked at in the order of their score, see card_order()
        return True
    below, group, order = score[0], score[1], score[2]
    if below:
        return False
    if hull in DESTROYERS:
        # The toolkit, or gold that adds 400 HP or more beside it
        return group == 0 or (group == 1 and order[0] == 0 and -order[1] >= HP_PURPLE)
    return group == 0 and order[0] == 0


def card_order(index, hull, cards, other=None):
    """
    Args:
        index (int): Slot
        hull (str):
        cards (list[dict]): Cards of the depot: index, level, rarity, name cut by the game
        other (dict): Her other auxiliary

    Returns:
        list[dict]: Cards to look at, the likely best first. Cards against submarines by their name are left out.
    """
    order = []
    for card in cards:
        if is_asw(card.get('name') or ''):
            continue
        score = gear_score(index, hull, card_item(card), other)
        if score is not None:
            order.append((score, card))
    order.sort(key=lambda pair: pair[0])
    return [card for _, card in order]


def describe(item):
    """
    Returns:
        str: Like "Repair Toolkit +10 (purple, HP 500)"
    """
    if item is None:
        return 'nothing'
    stats = ', '.join(f'{label} {amount}' for label, amount in (item.get('stats') or {}).items())
    extra = f'{item.get("rarity")}, {stats}' if stats else f'{item.get("rarity")}'
    return f'{item.get("name") or "?"} +{item.get("level") or 0} ({extra})'
