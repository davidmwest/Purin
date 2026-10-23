"""EN Tempesta UI templates taken from a Purin scheduler observation.

Source: 2026-10-22_21-37-41_089728_alas_ex_blocked.png. The asset retains
only the recognized labels and lock; account data and scenery are omitted.
These templates intentionally fail closed on another event or language.
"""
from module.base.button import Button

_TEMPESTA = './assets/en/event_completion/TEMPESTA_20261022.png'

TEMPESTA_CAMPAIGN = Button(
    area=(216, 77, 554, 99), color=(113, 114, 127), button=(216, 77, 554, 99),
    file=_TEMPESTA, name='TEMPESTA_CAMPAIGN')
TEMPESTA_SUPPLIES = Button(
    area=(1163, 657, 1255, 685), color=(97, 113, 161),
    # The emblem is the observed entry control; the text can sit outside its
    # interaction region. Keep recognition on the stable Supplies label.
    button=(1125, 660, 1150, 684), file=_TEMPESTA, name='TEMPESTA_SUPPLIES')
TEMPESTA_EX_LOCKED = Button(
    area=(84, 447, 101, 467), color=(112, 123, 135), button=(84, 447, 101, 467),
    file=_TEMPESTA, name='TEMPESTA_EX_LOCKED')

# Observed main-screen event entry, 2026-10-22_22-05-26_128271.
# Only the event logo is retained; no account or main-screen scenery.
TEMPESTA_MAIN_ENTRY = Button(
    area=(1126, 370, 1254, 410), color=(63, 85, 137), button=(1140, 370, 1230, 405),
    file='./assets/en/event_completion/TEMPESTA_MAIN_20261022.png', name='TEMPESTA_MAIN_ENTRY')

# Separate featured-event portrait observed on both accounts' fresh Main screen.
# Exclude the changing notification badge and the surrounding background.
TEMPESTA_FEATURED_ENTRY = Button(
    area=(1077, 90, 1143, 157), color=(178, 182, 196), button=(1080, 95, 1140, 150),
    file='./assets/en/event_completion/TEMPESTA_FEATURED_20261022.png', name='TEMPESTA_FEATURED_ENTRY')

# Featured hub observed at 2026-10-22_22-27-55_087155. The first-tab
# decoration is present only while that landing tab is selected.
_HUB = './assets/en/event_completion/TEMPESTA_HUB_20261022.png'
TEMPESTA_HUB = Button(
    area=(43, 151, 202, 187), color=(54, 74, 110), button=(43, 151, 202, 187),
    file=_HUB, name='TEMPESTA_HUB')
TEMPESTA_HUB_HOME = Button(
    area=(1224, 34, 1257, 66), color=(184, 186, 184), button=(1220, 29, 1260, 69),
    file=_HUB, name='TEMPESTA_HUB_HOME')
TEMPESTA_REWARDS_TAB = Button(
    area=(43, 468, 123, 491), color=(57, 66, 85), button=(45, 466, 147, 493),
    file=_HUB, name='TEMPESTA_REWARDS_TAB')
TEMPESTA_HUB_LANDING = Button(
    area=(282, 376, 801, 467), color=(70, 88, 127), button=(282, 376, 801, 467),
    file=_HUB, name='TEMPESTA_HUB_LANDING')

# Catalog observed at 2026-10-22_22-42-10_021165. Other tabs retain the
# current-event title without its selected-tab decoration. The Beckoning
# label excludes the changing notification badge. No catalog GO is an action.
_PANELS = './assets/en/event_completion/TEMPESTA_HUB_PANELS_20261022.png'
TEMPESTA_HUB_INACTIVE = Button(
    area=(44, 156, 184, 183), color=(49, 56, 72), button=(44, 156, 184, 183),
    file=_PANELS, name='TEMPESTA_HUB_INACTIVE')
TEMPESTA_BECKONING_TAB = Button(
    area=(44, 360, 181, 386), color=(53, 61, 79), button=(45, 359, 192, 388),
    file=_PANELS, name='TEMPESTA_BECKONING_TAB')
TEMPESTA_FOGGY_SUPPLY_TAB = Button(
    area=(44, 258, 230, 284), color=(54, 61, 77), button=(45, 257, 230, 287),
    file=_PANELS, name='TEMPESTA_FOGGY_SUPPLY_TAB')

# Beckoning side-story landing observed at 2026-10-22_22-57-59_605503.
# These are known entrance labels only; their destination actions are unsupported.
_BECKONING = './assets/en/event_completion/TEMPESTA_BECKONING_20261022.png'
TEMPESTA_BECKONING_LANDING = Button(
    area=(509, 548, 882, 676), color=(106, 96, 111), button=(509, 548, 882, 676),
    file=_BECKONING, name='TEMPESTA_BECKONING_LANDING')
TEMPESTA_BECKONING_PREPARE = Button(
    area=(211, 614, 444, 664), color=(92, 67, 89), button=(215, 613, 444, 665),
    file=_BECKONING, name='TEMPESTA_BECKONING_PREPARE')
TEMPESTA_BECKONING_CHOICE = Button(
    area=(944, 617, 1168, 664), color=(70, 87, 106), button=(946, 615, 1168, 665),
    file=_BECKONING, name='TEMPESTA_BECKONING_CHOICE')
