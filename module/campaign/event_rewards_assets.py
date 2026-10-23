"""Free Tempesta PT ladder observed by Purin on 2026-10-22 at 23:13:22.

Recognition excludes the changing PT total and reward cards. Exchange has no
action asset: this collector only receives already-earned cumulative rewards.
"""
from module.base.button import Button

_FOGGY = './assets/en/event_completion/FOGGY_SUPPLY_20261022.png'

FOGGY_SUPPLY_PAGE = Button(
    area=(272, 129, 834, 299), color=(209, 218, 242), button=(272, 129, 834, 299),
    file=_FOGGY, name='FOGGY_SUPPLY_PAGE')
FOGGY_SUPPLY_HOME = Button(
    area=(1224, 34, 1257, 66), color=(184, 186, 184), button=(1220, 29, 1260, 69),
    file=_FOGGY, name='FOGGY_SUPPLY_HOME')
FOGGY_COLLECT_ALL = Button(
    area=(1066, 485, 1186, 511), color=(104, 129, 199), button=(1054, 484, 1187, 510),
    file=_FOGGY, name='FOGGY_COLLECT_ALL')
FOGGY_CLAIM_BADGE = Button(
    area=(1193, 476, 1213, 496), color=(224, 99, 124), button=(1193, 476, 1213, 496),
    file=_FOGGY, name='FOGGY_CLAIM_BADGE')

# Exact disabled state after Purin received the earned rewards at 23:28.
# The changing PT total and reward cards are outside this recognition crop.
FOGGY_COLLECT_ALL_DISABLED = Button(
    area=(1066, 485, 1186, 511), color=(133, 133, 133), button=(1054, 484, 1187, 510),
    file='./assets/en/event_completion/FOGGY_SUPPLY_EMPTY_20261022.png', name='FOGGY_COLLECT_ALL_DISABLED')
