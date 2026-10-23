"""Earned mission controls captured on the actual EN Daily list.

Only the COMPLETE and blue CLAIM pixels are retained. The caller must bind
both controls to the independently recognized Build 3 times row.
"""
from module.base.button import Button

_FILE = './assets/en/gacha/BUILD_THREE_CLAIM_20261023.png'
BUILD_MISSION_COMPLETE = Button(
    area=(523, 164, 660, 182), color=(158, 177, 202),
    button=(523, 164, 660, 182), file=_FILE, name='BUILD_MISSION_COMPLETE')
BUILD_MISSION_CLAIM = Button(
    area=(1118, 142, 1224, 172), color=(134, 180, 230),
    button=(1118, 142, 1224, 172), file=_FILE, name='BUILD_MISSION_CLAIM')
