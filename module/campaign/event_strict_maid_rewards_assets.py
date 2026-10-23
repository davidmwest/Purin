"""Strict Maid free mission controls observed by Purin on 2026-10-22.

Proceed is a detection-only witness that unfinished rows remain rendered. It is
never an action in this collector; neither is the separate Rewards catalog tab.
"""
from module.base.button import Button

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
