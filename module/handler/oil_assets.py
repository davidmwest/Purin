"""EN oil purchase observed on F2P, 2026-10-22 23:42.

Only Cancel is actionable. Variable oil, gem and purchase counts are excluded.
"""
from module.base.button import Button

_OIL = './assets/en/handler/OIL_PURCHASE_20261022.png'

OIL_PURCHASE_TITLE = Button(
    area=(531, 272, 566, 296), color=(144, 143, 105), button=(531, 272, 566, 296),
    file=_OIL, name='OIL_PURCHASE_TITLE')
OIL_PURCHASE_HISTORY = Button(
    area=(535, 366, 757, 385), color=(122, 128, 135), button=(535, 366, 757, 385),
    file=_OIL, name='OIL_PURCHASE_HISTORY')
OIL_PURCHASE_CANCEL = Button(
    area=(737, 480, 837, 508), color=(190, 191, 192), button=(710, 474, 869, 519),
    file=_OIL, name='OIL_PURCHASE_CANCEL')
