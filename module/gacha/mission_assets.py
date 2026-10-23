"""Exact EVENT build-mission identity observed by Purin on 2026-10-23.

The text and green EVENT label identify one row; neither GO nor a missing row
authorizes orders. The row may move as completed missions disappear.
"""
from module.base.button import Button

_FILE = './assets/en/gacha/BUILD_THREE_MISSION_20261023.png'
BUILD_THREE_TEXT = Button(
    area=(257, 435, 389, 460), color=(137, 156, 180),
    button=(257, 435, 389, 460), file=_FILE, name='BUILD_THREE_TEXT')
BUILD_THREE_TEXT_F2P = Button(
    area=(257, 280, 389, 305), color=(137, 156, 180),
    button=(257, 280, 389, 305),
    file='./assets/en/gacha/BUILD_THREE_MISSION_F2P_20261023.png', name='BUILD_THREE_TEXT_F2P')
EVENT_MISSION_MARKER = Button(
    area=(177, 398, 195, 468), color=(140, 212, 178),
    button=(177, 398, 195, 468), file=_FILE, name='EVENT_MISSION_MARKER')
DAILY_MISSION_MARKER = Button(
    area=(177, 90, 195, 160), color=(140, 185, 214),
    button=(177, 90, 195, 160), file=_FILE, name='DAILY_MISSION_MARKER')
