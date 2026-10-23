"""Strict Maid free mission controls observed by Purin on 2026-10-22.

Proceed is a detection-only witness that unfinished rows remain rendered. It is
never an action in this collector; neither is the separate Rewards catalog tab.
"""
from module.base.button import Button
from module.ocr.ocr import Ocr

_SOURCE = './assets/en/event_completion/STRICT_MAID_REWARDS_20261022.png'

STRICT_MAID_PAGE = Button(
    area=(286, 166, 806, 287), color=(237, 198, 196), button=(286, 166, 806, 287),
    file=_SOURCE, name='STRICT_MAID_PAGE')
STRICT_MAID_HOME = Button(
    area=(1225, 35, 1258, 65), color=(179, 181, 179), button=(1220, 29, 1261, 70),
    file=_SOURCE, name='STRICT_MAID_HOME')
STRICT_MAID_COLLECT_ALL = Button(
    area=(1128, 450, 1216, 471), color=(197, 197, 197), button=(1127, 444, 1219, 490),
    file=_SOURCE, name='STRICT_MAID_COLLECT_ALL')
STRICT_MAID_EARNED = Button(
    area=(665, 519, 728, 570), color=(238, 195, 201), button=(665, 519, 728, 570),
    file=_SOURCE, name='STRICT_MAID_EARNED')
STRICT_MAID_PROCEED = Button(
    area=(1138, 594, 1200, 644), color=(239, 211, 183), button=(1138, 594, 1200, 644),
    file=_SOURCE, name='STRICT_MAID_PROCEED')

# Each instance retains its own template offset. The search for a row is confined
# to that row's captured location; action bounds stay inside the pink heart.
STRICT_MAID_ROW_OFFSETS = ((0, 0), (474, 0), (0, 74), (474, 74))
STRICT_MAID_ROW_COLLECT = tuple(Button(
    area=(665, 519, 728, 570), color=(238, 195, 201), button=(681, 534, 712, 553),
    file=_SOURCE, name=f'STRICT_MAID_ROW_{index}_COLLECT') for index in range(4))
STRICT_MAID_ROW_COLLECTED = tuple(Button(
    area=(665, 519, 728, 570), color=(202, 192, 202), button=(681, 534, 712, 553),
    file='./assets/en/event_completion/STRICT_MAID_COLLECTED_20261022.png',
    name=f'STRICT_MAID_ROW_{index}_COLLECTED') for index in range(4))

# The current total has dark brown digits; the lighter /1400 is excluded by
# this letter color. Keep the raw OCR string so an unreadable crop is not zero.
OCR_STRICT_MAID_TOTAL = Ocr(
    (901, 466, 1000, 487), letter=(146, 86, 74), threshold=100,
    alphabet='0123456789', name='STRICT_MAID_TOTAL')
