import os

import cv2
import pytest

import module.config.server as server
from module.dock.census import card_level, card_rarity, card_status
from module.inventory.inventory import DockReader

DATA = os.path.join(os.path.dirname(__file__), 'data')


@pytest.fixture(autouse=True)
def en():
    previous = server.server
    server.set_server('en')
    yield
    server.set_server(previous)


def cards():
    # The dock of a slot of fleet 1, F2P account 2026-10-22, 2560x1440 brought to 1280x720: REMOVE first, then
    # Laffey, Indianapolis, Hammann, a Universal Bulin, and ships "In action" on commissions
    image = cv2.cvtColor(cv2.imread(os.path.join(DATA, 'slot_dock_f2p.png')), cv2.COLOR_BGR2RGB)
    found = [(card.column, card.top) for row in DockReader().rows(image) for card in row.cards if card.top + 204 <= 720]
    return image, found


def test_levels():
    image, found = cards()

    assert [card_level(image, column, top) for column, top in found] == \
           [75, 66, 66, 1, 14, 14, 14, 14, 13, 13, 13, 12, 12]


def test_rarities_on_rows_whose_stripe_sits_apart():
    image, found = cards()

    assert [card_rarity(image, column, top) for column, top in found] == \
           ['Elite', 'Elite', 'Rare', 'Elite', 'Normal', 'Normal',
            'Normal', 'Normal', 'Rare', 'Rare', 'Normal', 'Rare', 'Normal']


def test_ships_on_commissions():
    # The template of "In action" read them all as free at 1440p
    image, found = cards()

    assert [card_status(image, column, top) for column, top in found] == \
           ['free'] * 4 + ['commission'] * 9
