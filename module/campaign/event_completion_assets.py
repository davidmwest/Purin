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
