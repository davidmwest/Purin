from types import SimpleNamespace

import pytest

from module.dock.care import DockCare, breaks_at_least, copies_needed
from module.dock.policy import Card


@pytest.mark.parametrize('level, breaks, needed', [
    # Under the cap of no limit break, she may have none
    (1, 0, 4), (69, 0, 4), (70, 0, 4),
    # Above 70 she broke at least once: Long Island at 80
    (71, 1, 3), (80, 1, 3),
    (81, 2, 2), (90, 2, 2),
    (91, 3, 0), (100, 3, 0),
])
def test_copies_a_fleet_ship_may_still_need(level, breaks, needed):
    assert breaks_at_least(level) == breaks
    assert copies_needed(level) == needed


def card(ship, level, fleet=0, bulin=False):
    return Card(index=0, read=ship, ship=ship, rarity='Rare', frame='Rare', level=level, locked=True, fleet=fleet,
                status='free', bulin=bulin, retrofit=False)


def test_needs_of_the_fleets_purin_levels():
    care = DockCare.__new__(DockCare)
    care.formation_fleets = lambda: [1]
    cards = [
        card('Long Island', 80, fleet=1),
        card('Laffey', 75, fleet=1),
        card('Hammann', 66, fleet=1),
        # Fleet 2 is not levelled, its Bulin needs nothing
        card('Universal Bulin', 1, fleet=2, bulin=True),
        card('Cassin', 10, fleet=2),
        # A copy in the dock
        card('Laffey', 1),
    ]

    assert care.fleet_needs(cards) == {'Long Island': 3, 'Laffey': 3, 'Hammann': 4}
