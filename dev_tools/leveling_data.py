"""
Build module/leveling/ship_data.json from two tables of the Azur Lane wiki, azurlane.koumakan.jp.

Usage: python -m dev_tools.leveling_data <stats.json> <tech120.json>

stats.json is the "List of Ships by Stats" at level 100 and level 120, a list of
    [name, rarity, hull, stats at 100, stats at 120]
with the stats in the order of STATS. The list has a row for the retrofit of a ship, "<name> (Retrofit)",
at the rarity after the retrofit.

tech120.json is the "Level 120 Stat Bonuses" of the Fleet Technology page,
    {"<hull group>|<amount>|<stat>|<note>": [names of ships]}
where the note limits a bonus to one hull of its group, like "CVL only".
Ships missing from it, Bulins, collaborations, children and muses, give no bonus.
"""
import json
import sys

STATS = ['hp', 'fp', 'aa', 'trp', 'eva', 'avi', 'rld', 'asw', 'acc', 'lck', 'oil']
TECH_STATS = {
    'Health': 'hp', 'Firepower': 'fp', 'Anti-Air': 'aa', 'Torpedo': 'trp', 'Evasion': 'eva',
    'Aviation': 'avi', 'Reload': 'rld', 'Anti-Submarine': 'asw', 'Accuracy': 'acc',
}
TECH_GROUPS = {
    'DD': ['DD'], 'CL': ['CL'], 'CA/BM/CB': ['CA', 'BM', 'CB'], 'BC/BB/BBV': ['BC', 'BB', 'BBV'],
    'CVL/CV': ['CVL', 'CV'], 'AR': ['AR'], 'AE': ['AE'], 'SS/SSV': ['SS', 'SSV'], 'IX': ['IXs', 'IXv', 'IXm'],
}
RETROFIT = ' (Retrofit)'


def tech_bonuses(tech):
    """
    Returns:
        dict[str, list]: Name of a ship to [stat, amount, hulls it goes to]
    """
    bonuses = {}
    for key, names in tech.items():
        group, amount, stat, note = key.split('|')
        hulls = TECH_GROUPS[group]
        if note:
            only = note.replace(' only', '')
            assert only in hulls, key
            hulls = [only]
        for name in names:
            bonuses[name] = [TECH_STATS[stat], int(amount), hulls]
    return bonuses


def build(stats, tech):
    bonuses = tech_bonuses(tech)
    names = {row[0] for row in stats}
    missing = sorted(set(bonuses) - names)
    assert not missing, f'Fleet Technology ships without stats: {missing}'

    ships = []
    for name, rarity, hull, lv100, lv120 in stats:
        assert len(lv100) == len(STATS) and len(lv120) == len(STATS), name
        # A retrofit is the same ship to the Fleet Technology
        bonus = bonuses.get(name[:-len(RETROFIT)] if name.endswith(RETROFIT) else name)
        ships.append([name, rarity, hull, lv100, lv120, bonus])
    return {
        'source': 'azurlane.koumakan.jp: List of Ships by Stats, Fleet Technology',
        'stats': STATS,
        'ships': ships,
    }


def dump(data):
    """
    One ship a line, for diffs to read.
    """
    lines = ',\n'.join('  ' + json.dumps(ship, ensure_ascii=False) for ship in data['ships'])
    head = {k: v for k, v in data.items() if k != 'ships'}
    head = json.dumps(head, ensure_ascii=False)[:-1]
    return f'{head}, "ships": [\n{lines}\n]}}\n'


if __name__ == '__main__':
    with open(sys.argv[1], encoding='utf-8') as f:
        stats = json.load(f)
    with open(sys.argv[2], encoding='utf-8') as f:
        tech = json.load(f)
    data = build(stats, tech)
    with open('module/leveling/ship_data.json', 'w', encoding='utf-8') as f:
        f.write(dump(data))
    print(f'{len(data["ships"])} ships, {sum(1 for s in data["ships"] if s[5])} with a level 120 bonus')
