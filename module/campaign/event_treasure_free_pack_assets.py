"""Exact free treasure cards observed by Purin on 2026-10-23; paid cards excluded."""
from module.base.button import Button


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
