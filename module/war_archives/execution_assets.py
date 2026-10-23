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
