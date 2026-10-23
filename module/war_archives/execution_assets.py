"""Captured stage-owned two-surface-fleet layout; no ship or requirement values."""
from module.base.button import Button

_FILE = './assets/en/war_archives/ARCHIVE_HARD_FLEET_20261023.png'

ARCHIVE_FLEET_TITLE = Button(area=(108, 108, 319, 144), color=(137, 139, 159),
    button=(108, 108, 319, 144), file=_FILE, name='ARCHIVE_FLEET_TITLE')

ARCHIVE_HARD_SURFACE = Button(area=(115, 170, 168, 384), color=(181, 131, 93),
    button=(115, 170, 168, 384), file=_FILE, name='ARCHIVE_HARD_SURFACE')

ARCHIVE_DISABLED_SUB = Button(area=(431, 435, 986, 516), color=(56, 61, 77),
    button=(431, 435, 986, 516), file=_FILE, name='ARCHIVE_DISABLED_SUB')

ARCHIVE_HARD_GO = Button(area=(989, 559, 1144, 605), color=(230, 173, 91),
    button=(989, 559, 1144, 605), file=_FILE, name='ARCHIVE_HARD_GO')

# Exact current EN daily tracking reminder; checkbox is deliberately excluded.
_KEY_FILE = './assets/en/war_archives/ARCHIVE_DATA_KEY_20261023.png'

ARCHIVE_KEY_HEADER = Button(area=(353, 175, 430, 215), color=(116, 146, 192),
    button=(353, 175, 430, 215), file=_KEY_FILE, name='ARCHIVE_KEY_HEADER')

ARCHIVE_KEY_MESSAGE = Button(area=(361, 316, 919, 374), color=(140, 147, 148),
    button=(361, 316, 919, 374), file=_KEY_FILE, name='ARCHIVE_KEY_MESSAGE')

ARCHIVE_KEY_FIVE = Button(area=(737, 319, 757, 347), color=(140, 140, 108),
    button=(737, 319, 757, 347), file=_KEY_FILE, name='ARCHIVE_KEY_FIVE')

ARCHIVE_KEY_CANCEL = Button(area=(401, 481, 578, 542), color=(167, 169, 170),
    button=(401, 481, 578, 542), file=_KEY_FILE, name='ARCHIVE_KEY_CANCEL')

ARCHIVE_KEY_CONFIRM = Button(area=(702, 481, 878, 542), color=(103, 148, 203),
    button=(702, 481, 878, 542), file=_KEY_FILE, name='ARCHIVE_KEY_CONFIRM')
