"""
The walk of the dock goes past its first screen, card by card, each once.
"""
from types import SimpleNamespace

import numpy as np

from module.inventory.inventory import CARD_LEFTS, CARD_WIDTH, LIST_BOTTOM, LIST_TOP
from module.retire.dock_walk import DockWalker

CARD_HEIGHT = 204
ROW_DELTA = 227
# The first row of the list starts this far below the bar of the dock, with the dark gap above it
FIRST_ROW = 16


class FakeDock:
    """
    A dock of rows of 7 cards of noise, each with its number painted in its middle, that scrolls as the game does:
    a drag on the list moves it by its length, a drag on the scroll bar takes it to its top.
    """

    def __init__(self, cards, seed=0):
        rng = np.random.default_rng(seed)
        rows = (cards + 6) // 7
        height = max(FIRST_ROW + rows * ROW_DELTA + 60, LIST_BOTTOM - LIST_TOP)
        self.content = np.full((height, 1280, 3), 20, dtype=np.uint8)
        for index in range(cards):
            row, column = divmod(index, 7)
            top, left = FIRST_ROW + row * ROW_DELTA, CARD_LEFTS[column]
            self.content[top:top + CARD_HEIGHT, left:left + CARD_WIDTH] = \
                rng.integers(40, 230, size=(CARD_HEIGHT, CARD_WIDTH, 3), dtype=np.uint8)
            self.content[top + 90:top + 130, left + 50:left + 90] = 10 + index * 8
        self.view = LIST_BOTTOM - LIST_TOP
        self.max_offset = max(height - self.view, 0)
        self.offset = 0
        self.image = None
        self.swipes = 0

    def screenshot(self):
        image = np.full((720, 1280, 3), 90, dtype=np.uint8)
        image[LIST_TOP:LIST_BOTTOM] = self.content[self.offset:self.offset + self.view]
        self.image = image
        return image

    def swipe(self, p1, p2, duration=None, name=None):
        self.swipes += 1
        (x1, y1), (x2, y2) = p1, p2
        if x1 > 1200:
            self.offset = 0
        else:
            self.offset = int(min(max(self.offset + (y1 - y2), 0), self.max_offset))

    def sleep(self, *args, **kwargs):
        pass

    def click_record_clear(self):
        pass


def walk(cards):
    device = FakeDock(cards)
    walker = DockWalker(SimpleNamespace(device=device))
    seen = []
    for card, fleet in walker.cards():
        left = CARD_LEFTS[card.column]
        # The row top found is 4 below the true one, under the gap
        value = int(device.image[card.top + 106, left + 70, 0])
        seen.append((value - 10) // 8)
    return seen, device


def test_every_card_of_a_dock_of_4_rows_once_in_order():
    seen, device = walk(28)
    assert seen == list(range(28))


def test_a_dock_of_one_screen():
    seen, device = walk(10)
    assert seen == list(range(10))


def test_the_walk_starts_from_the_top():
    device = FakeDock(28)
    device.offset = device.max_offset
    walker = DockWalker(SimpleNamespace(device=device))
    first, _ = next(walker.cards())
    assert (first.column, int(device.image[first.top + 106, CARD_LEFTS[0] + 70, 0])) == (0, 10)
