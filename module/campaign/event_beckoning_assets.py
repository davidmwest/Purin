"""Earned ticket controls and first locked story observed by Purin, 2026-10-22."""
from module.base.button import Button
from module.ocr.ocr import Ocr, DigitCounter

_IMAGE = './assets/en/event_completion/TEMPESTA_BECKONING_TICKETS_20261022.png'

BECKONING_MONTHLY_EARNED = Button(
    area=(918, 481, 1063, 531), color=(171, 158, 147), button=(918, 481, 1063, 531),
    file=_IMAGE, name='BECKONING_MONTHLY_EARNED')
BECKONING_MONTHLY_COLLECT = Button(
    area=(1102, 491, 1209, 529), color=(125, 72, 70), button=(1111, 498, 1200, 521),
    file=_IMAGE, name='BECKONING_MONTHLY_COLLECT')
BECKONING_VICTORY_MISSION = Button(
    area=(918, 562, 1072, 597), color=(172, 159, 148), button=(918, 562, 1072, 597),
    file=_IMAGE, name='BECKONING_VICTORY_MISSION')
BECKONING_VICTORY_COLLECT = Button(
    area=(1102, 574, 1209, 612), color=(124, 73, 70), button=(1111, 582, 1200, 604),
    file=_IMAGE, name='BECKONING_VICTORY_COLLECT')
BECKONING_FIRST_STORY = Button(
    area=(110, 365, 255, 403), color=(83, 81, 126), button=(110, 365, 255, 403),
    file=_IMAGE, name='BECKONING_FIRST_STORY')
BECKONING_FIRST_LOCK = Button(
    area=(88, 239, 278, 341), color=(114, 104, 148), button=(145, 251, 215, 300),
    file=_IMAGE, name='BECKONING_FIRST_LOCK')

OCR_BECKONING_TICKETS = Ocr(
    (1198, 40, 1251, 69), letter=(230, 231, 249), threshold=128,
    alphabet='0123456789', name='BECKONING_TICKETS')
OCR_BECKONING_VICTORIES = DigitCounter(
    # Include padding around the opening parenthesis/leading digit. The tighter
    # crop read the observed (155/60) as 55/60 and hid an earned milestone.
    (914, 594, 1018, 617), letter=(92, 79, 78), threshold=80, name='BECKONING_VICTORIES')
