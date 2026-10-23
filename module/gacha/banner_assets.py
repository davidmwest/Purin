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
EVENT_BANNER_TEXT = Button(
    area=(141, 541, 641, 568), color=(254, 167, 53),
    button=(141, 541, 641, 568), file=_FILE, name='EVENT_BANNER_TEXT')
EVENT_RATE_INFO = Button(
    area=(896, 540, 925, 569), color=(67, 77, 118),
    button=(902, 545, 919, 564), file=_FILE, name='EVENT_RATE_INFO')
