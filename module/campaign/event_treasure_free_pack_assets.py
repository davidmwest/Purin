"""Exact free treasure cards observed by Purin on 2026-10-23; paid cards excluded."""
from module.base.button import Button
from module.ocr.ocr import Ocr


_FILE = './assets/en/event_completion/TREASURE_FREE_PACKS_20261023.png'


def _witness(name, area, color, button=None):
    return Button(area=area, color=color, button=button or area, file=_FILE, name=name)


TREASURE_HEADER = _witness('TREASURE_HEADER', (337, 73, 662, 180), (88, 112, 173))
TREASURE_DATES = _witness('TREASURE_DATES', (569, 101, 689, 123), (77, 105, 165))
TREASURE_HOME = _witness('TREASURE_HOME', (1212, 23, 1265, 77), (223, 223, 223), button=(1222, 33, 1254, 65))
TREASURE_FREE_DAY2 = _witness('TREASURE_FREE_DAY2', (381, 327, 419, 351), (97, 118, 153))
TREASURE_NAME_DAY2 = _witness('TREASURE_NAME_DAY2', (348, 397, 499, 430), (73, 91, 128))
TREASURE_CLAIM_DAY2 = _witness('TREASURE_CLAIM_DAY2', (377, 562, 474, 585), (86, 99, 149), button=(389, 566, 462, 581))
TREASURE_FREE_DAY1 = _witness('TREASURE_FREE_DAY1', (829, 327, 866, 351), (96, 117, 151))
TREASURE_NAME_DAY1 = _witness('TREASURE_NAME_DAY1', (795, 397, 947, 430), (73, 94, 139))
TREASURE_CLAIM_DAY1 = _witness('TREASURE_CLAIM_DAY1', (823, 562, 921, 585), (89, 104, 157), button=(835, 566, 909, 581))


TREASURE_CARDS = {
    'day2': (TREASURE_FREE_DAY2, TREASURE_NAME_DAY2, TREASURE_CLAIM_DAY2),
    'day1': (TREASURE_FREE_DAY1, TREASURE_NAME_DAY1, TREASURE_CLAIM_DAY1),
}


_CONFIRM_FILE = './assets/en/event_completion/TREASURE_FREE_CONFIRM_20261023.png'


def _confirmation(name, area, color, button=None):
    return Button(area=area, color=color, button=button or area, file=_CONFIRM_FILE, name=name)


TREASURE_CONFIRM_FREE = _confirmation('TREASURE_CONFIRM_FREE', (264, 244, 420, 407), (109, 124, 150))
TREASURE_CONFIRM_NAME = _confirmation('TREASURE_CONFIRM_NAME', (257, 423, 429, 455), (154, 162, 165))
TREASURE_CONFIRM_CONTENTS = _confirmation('TREASURE_CONFIRM_CONTENTS', (441, 179, 791, 319), (122, 127, 140))
TREASURE_CONFIRM_ZERO = _confirmation('TREASURE_CONFIRM_ZERO', (619, 506, 713, 548), (135, 126, 137))
TREASURE_CONFIRM_BUY = _confirmation('TREASURE_CONFIRM_BUY', (699, 580, 871, 637), (80, 127, 187),
                                    button=(742, 592, 834, 627))
TREASURE_CONFIRM_WITNESSES = (TREASURE_CONFIRM_FREE, TREASURE_CONFIRM_NAME, TREASURE_CONFIRM_CONTENTS,
                             TREASURE_CONFIRM_ZERO, TREASURE_CONFIRM_BUY)
# Preserve raw text: unreadable is never the numeric zero. The full observed
# amount strip includes room for additional digits rather than a zero-only crop.
OCR_TREASURE_CONFIRM_PRICE = Ocr((654, 505, 751, 548), letter=(255, 255, 255), threshold=80,
                                 alphabet='0123456789', name='TREASURE_CONFIRM_PRICE')
