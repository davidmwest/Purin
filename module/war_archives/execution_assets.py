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

# All six empty slots, above the stage-specific ship-type labels.
_EMPTY_FILE = './assets/en/war_archives/ARCHIVE_EMPTY_HARD_FLEETS_20261029.png'
ARCHIVE_EMPTY_HARD_ROWS = (
    Button(area=(372, 177, 975, 235), color=(60, 59, 63),
           button=(372, 177, 975, 235), file=_EMPTY_FILE, name='ARCHIVE_EMPTY_HARD_ROW_1'),
    Button(area=(372, 292, 975, 349), color=(52, 51, 56),
           button=(372, 292, 975, 349), file=_EMPTY_FILE, name='ARCHIVE_EMPTY_HARD_ROW_2'),
)
ARCHIVE_EMPTY_HARD_SUB = Button(area=(435, 425, 1154, 513), color=(63, 66, 77),
    button=(435, 425, 1154, 513), file=_EMPTY_FILE, name='ARCHIVE_EMPTY_HARD_SUB')

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

# The 2560x1440 F2P capture downscaled to 720p has different text rasterization.
# Keep both captured layouts, with the same complete OCR and five-key checks.
_SCALED_KEY_FILE = './assets/en/war_archives/ARCHIVE_DATA_KEY_SCALED_20261029.png'
ARCHIVE_KEY_HEADER_SCALED = Button(area=(353, 175, 430, 215), color=(117, 145, 191),
    button=(353, 175, 430, 215), file=_SCALED_KEY_FILE, name='ARCHIVE_KEY_HEADER_SCALED')
ARCHIVE_KEY_MESSAGE_SCALED = Button(area=(361, 316, 919, 374), color=(150, 152, 149),
    button=(361, 316, 919, 374), file=_SCALED_KEY_FILE, name='ARCHIVE_KEY_MESSAGE_SCALED')
ARCHIVE_KEY_FIVE_SCALED = Button(area=(737, 319, 757, 347), color=(152, 145, 107),
    button=(737, 319, 757, 347), file=_SCALED_KEY_FILE, name='ARCHIVE_KEY_FIVE_SCALED')

ARCHIVE_KEY_LAYOUTS = (
    (ARCHIVE_KEY_HEADER, ARCHIVE_KEY_MESSAGE, ARCHIVE_KEY_FIVE),
    (ARCHIVE_KEY_HEADER_SCALED, ARCHIVE_KEY_MESSAGE_SCALED, ARCHIVE_KEY_FIVE_SCALED),
)
