"""Stage-owned EX lineup controls observed on 2026-10-22 at 23:42:26.

These disabled slots are a positive witness that EX permits only Fleet 1.
They must never authorize clearing a secondary or submarine fleet.
"""
from module.base.button import Button

_EX_PREPARATION = './assets/en/event_completion/TEMPESTA_EX_PREPARATION_20261022.png'

EX_DISABLED_FLEET_2 = Button(
    area=(373, 303, 974, 367), color=(55, 63, 71), button=(373, 303, 974, 367),
    file=_EX_PREPARATION, name='EX_DISABLED_FLEET_2')
EX_DISABLED_SUBMARINE = Button(
    area=(435, 438, 738, 499), color=(44, 60, 78), button=(435, 438, 738, 499),
    file=_EX_PREPARATION, name='EX_DISABLED_SUBMARINE')

# Actual settled tactical EX board captured at 23:56:28. Its A/B/C column
# positions differ from the initial empty loading board. Exclude the changing
# time value, fleet portraits and resources from recognition.
_EX_MAP = './assets/en/event_completion/TEMPESTA_EX_MAP_20261022.png'
EX_MAP_TIME_LIMIT = Button(
    area=(114, 12, 259, 46), color=(75, 85, 120), button=(114, 12, 259, 46),
    file=_EX_MAP, name='EX_MAP_TIME_LIMIT')
EX_MAP_NO_TIME_LIMIT = Button(
    area=(114, 12, 293, 46), color=(75, 85, 120), button=(114, 12, 293, 46),
    file='./assets/en/event_completion/TEMPESTA_EX_NO_TIME_LIMIT_20261022.png',
    name='EX_MAP_NO_TIME_LIMIT')
EX_MAP_COLUMNS = Button(
    area=(517, 132, 768, 153), color=(75, 95, 106), button=(517, 132, 768, 153),
    file=_EX_MAP, name='EX_MAP_COLUMNS')
# The same settled EX board after game recovery has a second observed camera
# position. Its columns also occur on the initial empty loading grid, so the
# captured static lower island must be present before accepting this variant.
EX_MAP_COLUMNS_SHIFTED = Button(
    area=(437, 115, 688, 136), color=(61, 86, 99), button=(437, 115, 688, 136),
    file='./assets/en/event_completion/TEMPESTA_EX_MAP_SHIFTED_20261023.png',
    name='EX_MAP_COLUMNS_SHIFTED')
EX_MAP_TERRAIN_SHIFTED = Button(
    area=(579, 503, 776, 568), color=(92, 120, 145), button=(579, 503, 776, 568),
    file='./assets/en/event_completion/TEMPESTA_EX_MAP_SHIFTED_20261023.png',
    name='EX_MAP_TERRAIN_SHIFTED')
EX_MAP_ATTACK = Button(
    area=(1147, 652, 1263, 701), color=(232, 182, 108), button=(1147, 652, 1263, 701),
    file=_EX_MAP, name='EX_MAP_ATTACK')
