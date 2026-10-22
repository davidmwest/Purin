"""
What each card of the dock shows: level, rarity and whether she is on a commission, read on the walk of the dock.

The dock page and the dock of a fleet slot draw cards alike, 138 by 204 at 1280x720. The scanner of the first screen
(module/retire/scanner.py) reads these from fixed grids; cards further down sit where a drag left them, so they are
read from the top of each card the walk finds instead.

On the F2P account (2560x1440 brought to 1280x720, 2026-10-22):
- the rarity stripe on top of a card is 5 px high and moves by up to 4 px from a row to the next: it is searched for.
- the template of "In action" scores too low, every ship on a commission read as free. The green banner itself covers
  a third of its band on a card on commission and none on the others.
"""
import numpy as np

from module.base.button import Button
from module.base.utils import color_similar
from module.inventory.inventory import CARD_LEFTS, CARD_WIDTH
from module.retire.scanner import LevelOcr

# Card tops of the walk sit 2 px below the grid of the scanner
GRID_SHIFT = -2
# Level on the top right of a card, from the grid's top left (EN)
LEVEL_AREA = (77, 5, 138, 27)
# Colours of the rarity stripe, those of the scanner
RARITY_COLORS = {
    'Normal': (171, 174, 186),
    'Rare': (106, 194, 248),
    'Elite': (151, 134, 254),
    'Super Rare': (247, 221, 101),
}
STRIPE_SEARCH = (-5, 9)
STRIPE_THRESHOLD = 15
# "In action": a green banner over the lower part of the portrait
BANNER_SEARCH = (120, 165)
BANNER_HEIGHT = 14
BANNER_X = (4, 100)
BANNER_COVER = 0.15


def card_level(image, column, top):
    """
    Args:
        image (np.ndarray): Screenshot of the dock
        column (int): Column of the card
        top (int): Top of the card as the walk found it

    Returns:
        int: Her level, 0 if not read
    """
    left, top = CARD_LEFTS[column], top + GRID_SHIFT
    x1, y1, x2, y2 = LEVEL_AREA
    area = (left + x1, top + y1, left + x2, top + y2)
    button = Button(area=area, color=(), button=area, name='CENSUS_LEVEL')
    level = LevelOcr([button], name='CENSUS_LEVEL', threshold=64).ocr(image)
    if isinstance(level, list):
        level = level[0] if level else 0
    return level if 1 <= level <= 125 else 0


def card_rarity(image, column, top):
    """
    Returns:
        str: 'Normal', 'Rare', 'Elite' or 'Super Rare', '' if the stripe is not found (Ultra Rare shines too much)
    """
    left = CARD_LEFTS[column]
    for dy in range(*STRIPE_SEARCH):
        y = top + dy
        if not 0 <= y < image.shape[0]:
            continue
        color = tuple(int(c) for c in image[y, left + 10:left + CARD_WIDTH - 10].mean(axis=0))
        for rarity, stripe in RARITY_COLORS.items():
            if color_similar(color, stripe, threshold=STRIPE_THRESHOLD):
                return rarity
    return ''


def card_status(image, column, top):
    """
    Returns:
        str: 'commission' when her card shows "In action", else 'free'
    """
    left = CARD_LEFTS[column]
    x1, x2 = BANNER_X
    best = 0.
    for y in range(top + BANNER_SEARCH[0], top + BANNER_SEARCH[1], 2):
        if y + BANNER_HEIGHT > image.shape[0]:
            break
        band = image[y:y + BANNER_HEIGHT, left + x1:left + x2].astype(np.int16)
        r, g, b = band[:, :, 0], band[:, :, 1], band[:, :, 2]
        green = (g > 140) & (g > r + 30) & (g > b + 20)
        best = max(best, float(green.mean()))
    return 'commission' if best > BANNER_COVER else 'free'
