"""
What to do with each card of the dock of the F2P account, from a census of the dock. This only decides: DockCare
reads the census from the game and acts on the verdicts.

The dock of the F2P account holds 150 ships and the campaign fills it. Option (d) of the design the user approved,
2026-10-22, the first rule that matches decides:
1. Keep a card that is locked, in a fleet, on a commission, in battle or in the dorm, or whose status was not read.
2. Keep a card that may not be the ship it was matched to: no confident match, her rarity unknown, or the colour of
   her frame is not the rarity of her ship.
3. Keep super rares and above, every Bulin, and every ship already retrofitted.
4. Limit break: free copies of a ship of the fleets Purin levels, as many as her limit breaks still need, the highest
   levels first. They stay in the dock for the LimitBreak task, the copies past her need go on to the next rules.
   The needs of a ship and of her retrofit add up: they are two cards in the fleets, each with her limit breaks.
5. Keep one copy of a ship that has a retrofit, when no card of hers is kept by the rules above.
6. Keep the highest unlocked ships above material level for commissions, which ask for ships of Lv 12, 25 and 30,
   up to a pool of them.
7. Enhance with commons at material level, the wiki says to feed commons.
8. Retire the rest: rare and elite extras, which give medals, and commons above material level past the pool.
A card whose level was not read is kept by the rules that go by level, 6 to 8: she could be any level. So is a card
whose level breaks the order of the dock, see out_of_order().
"""
from collections import Counter
from dataclasses import dataclass

from module.leveling.plan import RETROFIT

KEEP = 'keep'
LIMIT_BREAK = 'limit_break'
ENHANCE = 'enhance'
RETIRE = 'retire'
ACTIONS = (KEEP, LIMIT_BREAK, ENHANCE, RETIRE)

RARITIES = ('Normal', 'Rare', 'Elite', 'Super Rare', 'Ultra Rare', 'Priority', 'Decisive')
# Never retired nor fed, the F2P account has few of them
KEPT_RARITIES = ('Super Rare', 'Ultra Rare', 'Priority', 'Decisive')
MATERIAL_RARITY = 'Normal'
MATERIAL_LEVEL_MAX = 3
COMMISSION_POOL = 24
# Status of a card that keeps her, and why
STATUS_KEPT = {'commission': 'on commission', 'battle': 'in battle', 'dorm': 'in the dorm'}


@dataclass(frozen=True)
class Card:
    """
    A card of the dock as the census read it.
    """
    # Place of the card in the census, from the top of the dock sorted by level, highest first
    index: int
    # Her name as the OCR read it
    read: str
    # Name on the wiki of the ship she was matched to with confidence, '' if none
    ship: str
    # Rarity of that ship in the ship data, one of RARITIES, '' if unknown
    rarity: str
    # Rarity told by the colour of her frame, '' if not read
    frame: str
    # 0 if not read
    level: int
    locked: bool
    # Fleet she is in, 0 for none
    fleet: int
    # 'free', 'commission', 'battle', 'dorm', '' if not read
    status: str
    # A Universal Bulin, Prototype Bulin MKII or Specialized Bulin Custom MKIII
    bulin: bool
    # Her ship has a retrofit form in the ship data, and she is not that form
    retrofit: bool


@dataclass(frozen=True)
class Verdict:
    card: Card
    # One of ACTIONS
    action: str
    # Why, for a log line
    reason: str


def character(ship):
    """
    A retrofit is the same ship as before it, to the dock and to her limit breaks.
    """
    if ship.endswith(RETROFIT):
        return ship[:-len(RETROFIT)]
    return ship


def level_read(card):
    """
    Returns:
        int: Her level, 0 if not read or not a level (a census bug)
    """
    if card.level and card.level > 0:
        return card.level
    return 0


def out_of_order(cards, order):
    """
    The census walks the dock sorted by level, highest first, so a card read lower than a card below her had her
    level misread: an OCR that drops a digit reads Lv.71 as 7, and would have her fed or retired. The card just below
    her is kept too, as the misread card of the pair may be that one, read too high, taking a place in the commission
    pool. Only her neighbour: a card read too low would otherwise keep every card below her.

    The levels are compared among the free cards in no fleet: the dock of a fleet slot sorts the ships on commission
    after the free ones (tests/dock/data/slot_dock_f2p.png, a free Lv 1 Bulin above Lv 14 ships on commission), and
    the fleet ships may be sorted apart too. A walk of the dock lowest first keeps nearly every card.

    Args:
        cards (list[Card]):
        order (list[int]): Positions in cards, by index

    Returns:
        set[int]: Positions in cards of the cards whose level is out of order
    """
    walk = [i for i in order if cards[i].status == 'free' and not cards[i].fleet and level_read(cards[i])]
    found = set()
    highest_below = 0
    for i in reversed(walk):
        if level_read(cards[i]) < highest_below:
            found.add(i)
        highest_below = max(highest_below, level_read(cards[i]))
    for above, below in zip(walk, walk[1:]):
        if level_read(cards[below]) > level_read(cards[above]):
            found.add(below)
    return found


def held(card):
    """
    Rule 1.

    Returns:
        str: Why she may not leave the dock, '' if she is free
    """
    if card.locked:
        return 'locked'
    if card.fleet:
        return f'in fleet {card.fleet}'
    if card.status == 'free':
        return ''
    if card.status in STATUS_KEPT:
        return STATUS_KEPT[card.status]
    # A ship whose status was not read is never retired nor fed
    return f'status {card.status}' if card.status else 'status not read'


def doubtful(card):
    """
    Rule 2.

    Returns:
        str: Why she may not be the ship she was matched to, '' if she surely is
    """
    if not card.ship:
        return f'"{card.read}" matches no ship'
    if card.rarity not in RARITIES:
        return 'rarity unknown'
    # A retrofit is one rarity above the ship before it, but the card of a ship not retrofitted has the rarity of her
    # own ship, the one she was matched to
    if card.frame and card.frame != card.rarity:
        return f'frame reads {card.frame}, {card.ship} is {card.rarity}'
    return ''


def valued(card):
    """
    Rule 3.

    Returns:
        str: Why she is too good to retire or feed, '' if she is not
    """
    if card.bulin:
        return 'a Bulin'
    # Retrofits of commons and rares are rares and elites, but a retrofit is a large investment
    if card.ship.endswith(RETROFIT):
        return 'retrofitted'
    if card.rarity in KEPT_RARITIES:
        return card.rarity
    return ''


def decide(cards, needs, commission_pool=COMMISSION_POOL, material_level_max=MATERIAL_LEVEL_MAX):
    """
    Args:
        cards (list[Card]): The whole dock
        needs (dict[str, int]): Name on the wiki of each ship of the fleets Purin levels, to the copies her limit
            breaks still need, 0 when she is fully limit broken
        commission_pool (int): Unlocked ships above material level kept for commissions
        material_level_max (int): Highest level of a common fed as enhancement material

    Returns:
        list[Verdict]: One for each card, in the order of the cards. The rules go through the cards by their index,
            the highest levels first, see the docstring of this module.
    """
    order = sorted(range(len(cards)), key=lambda i: cards[i].index)
    # The copies of a retrofit are cards of the ship before it. A ship and her retrofit in needs are two cards of the
    # fleets, their needs add up: too few copies kept would have one retired, too many only wait in the dock.
    wanted = Counter()
    for name, count in needs.items():
        wanted[character(name)] += count
    verdicts = [None] * len(cards)
    reserved = Counter()
    # Ships with a card that rules 1 to 4 keep and that is surely hers, rule 5 keeps a copy only of the others
    kept = set()
    for i in order:
        card = cards[i]
        reason = held(card) or doubtful(card) or valued(card)
        if reason:
            verdicts[i] = Verdict(card, KEEP, reason)
    # Rule 4 takes the lowest copies first, from the end of a dock sorted by level: Quick Select spends a copy
    # whatever her level, and a copy above material level is worth more in the commission pool
    for i in reversed(order):
        if verdicts[i] is not None:
            continue
        card = cards[i]
        ship = character(card.ship)
        if reserved[ship] < wanted.get(ship, 0):
            reserved[ship] += 1
            verdicts[i] = Verdict(card, LIMIT_BREAK, f'copy {reserved[ship]} of {wanted[ship]} for her limit breaks')
    for i in order:
        if verdicts[i] is not None and not doubtful(cards[i]):
            kept.add(character(cards[i].ship))

    misread = out_of_order(cards, order)
    pool = 0
    for i in order:
        if verdicts[i] is not None:
            continue
        card = cards[i]
        ship = character(card.ship)
        if card.retrofit and ship not in kept:
            kept.add(ship)
            verdicts[i] = Verdict(card, KEEP, 'the copy kept to retrofit')
        elif not level_read(card):
            verdicts[i] = Verdict(card, KEEP, 'level not read')
        elif i in misread:
            verdicts[i] = Verdict(card, KEEP, 'level out of order')
        elif card.level > material_level_max and pool < commission_pool:
            pool += 1
            verdicts[i] = Verdict(card, KEEP, f'commission pool, {pool} of {commission_pool}')
        elif card.level > material_level_max:
            verdicts[i] = Verdict(card, RETIRE, f'above Lv.{material_level_max}, commission pool full')
        elif card.rarity == MATERIAL_RARITY:
            verdicts[i] = Verdict(card, ENHANCE, 'enhancement material')
        else:
            verdicts[i] = Verdict(card, RETIRE, 'extra copy')
    return verdicts


def summary(verdicts):
    """
    Returns:
        dict[str, int]: Cards of each action, all of ACTIONS listed
    """
    counts = Counter(verdict.action for verdict in verdicts)
    return {action: counts[action] for action in ACTIONS}


def verdict_line(verdict):
    """
    Returns:
        str: The verdict for a log line, 'retire Benson Lv.1 (Rare): extra copy'
    """
    card = verdict.card
    name = card.ship or f'"{card.read}"'
    level = f'Lv.{card.level}' if level_read(card) else 'Lv.?'
    rarity = card.rarity or card.frame or 'rarity ?'
    return f'{verdict.action} {name} {level} ({rarity}): {verdict.reason}'
