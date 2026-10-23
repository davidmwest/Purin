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

# Featured hub observed at 2026-10-22_22-27-55_087155. The selected event
# title remains in the side navigation while its subpanels are open.
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
