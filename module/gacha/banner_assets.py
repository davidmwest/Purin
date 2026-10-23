"""Exact current EN build banner and its small information control.

Observed by Purin on 2026-10-23 00:19:50. The UR Exchange information
button is deliberately excluded. These templates grant navigation only.
"""
from module.base.button import Button

_FILE = './assets/en/gacha/EVENT_BANNER_20261023.png'

EVENT_BUILD_ORDERS = Button(area=(1040, 612, 1210, 644), color=(238, 198, 137),
                            button=(1040, 612, 1210, 644), file=_FILE,
                            name='EVENT_BUILD_ORDERS')

EVENT_BANNER_DURATION = Button(
    area=(160, 501, 438, 518), color=(77, 80, 91),
    button=(160, 501, 438, 518), file=_FILE, name='EVENT_BANNER_DURATION')
EVENT_BANNER_DURATION_F2P = Button(
    area=(160, 501, 438, 518), color=(76, 79, 92),
    button=(160, 501, 438, 518), file='./assets/en/gacha/EVENT_BANNER_F2P_20261023.png',
    name='EVENT_BANNER_DURATION_F2P')
EVENT_BANNER_TEXT = Button(
    area=(141, 541, 641, 568), color=(254, 167, 53),
    button=(141, 541, 641, 568), file=_FILE, name='EVENT_BANNER_TEXT')
EVENT_RATE_INFO = Button(
    area=(896, 540, 925, 569), color=(67, 77, 118),
    button=(902, 545, 919, 564), file=_FILE, name='EVENT_RATE_INFO')

_RATE_FILE = './assets/en/gacha/EVENT_RATE_INFO_20261023.png'
EVENT_RATE_INFO_HEADER = Button(
    area=(333, 177, 520, 211), color=(103, 134, 184),
    button=(333, 177, 520, 211), file=_RATE_FILE, name='EVENT_RATE_INFO_HEADER')
EVENT_BUILD_RATES_TITLE = Button(
    area=(582, 235, 704, 260), color=(136, 147, 166),
    button=(582, 235, 704, 260), file=_RATE_FILE, name='EVENT_BUILD_RATES_TITLE')
EVENT_RATE_INFO_CLOSE = Button(
    area=(887, 169, 957, 217), color=(196, 86, 82),
    button=(908, 180, 938, 204), file=_RATE_FILE, name='EVENT_RATE_INFO_CLOSE')
EVENT_RATE_INFO_CONFIRM = Button(
    area=(553, 469, 727, 529), color=(108, 150, 202),
    button=(553, 469, 727, 529), file=_RATE_FILE, name='EVENT_RATE_INFO_CONFIRM')
