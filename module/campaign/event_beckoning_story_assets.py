"""Free story cards and viewer controls captured by Purin on 2026-10-23."""
from module.base.button import Button
from module.ocr.ocr import DigitCounter

_CARDS = './assets/en/event_completion/TEMPESTA_BECKONING_STORIES_20261023.png'
_VIEWER = './assets/en/event_completion/TEMPESTA_BECKONING_VIEWER_20261023.png'
_UNLOCKED = './assets/en/event_completion/TEMPESTA_BECKONING_UNLOCKED_20261023.png'
_UNLOCKED4 = './assets/en/event_completion/TEMPESTA_BECKONING_UNLOCKED4_20261023.png'
_UNLOCKED5 = './assets/en/event_completion/TEMPESTA_BECKONING_UNLOCKED5_20261023.png'
_UNLOCKED6 = './assets/en/event_completion/TEMPESTA_BECKONING_UNLOCKED6_20261023.png'
_FINAL = './assets/en/event_completion/TEMPESTA_BECKONING_FINAL_COLLECTED_20261023.png'


def _button(name, area, file, click=None):
    return Button(area=area, color=(0, 0, 0), button=click or area, file=file, name=name)


# Each locked card needs its own observed title AND its own lock. No screen-wide
# lock detection or guessed coordinates authorize spending an earned ticket.
STORY_CARDS = tuple(
    (str(index + 1), _button(f'BECKONING_CARD_{index + 1}', title, _CARDS),
     _button(f'BECKONING_LOCK_{index + 1}', lock, _CARDS))
    for index, (title, lock) in enumerate((
        ((110, 365, 255, 403), (155, 236, 205, 301)),
        ((366, 353, 549, 390), (438, 228, 490, 290)),
        ((649, 276, 914, 302), (755, 153, 807, 215)),
        ((995, 254, 1168, 296), (1048, 129, 1098, 191)),
        ((43, 602, 336, 632), (156, 499, 208, 561)),
        ((406, 592, 702, 620), (538, 476, 589, 538)),
    )))
# These labels change contrast/background after their ticket is spent. Keep
# their separately observed unlocked appearance instead of weakening matches.
STORY_UNLOCKED_TITLES = {
    '3': _button('BECKONING_CARD_3_UNLOCKED', (649, 276, 914, 302), _UNLOCKED),
    '4': _button('BECKONING_CARD_4_UNLOCKED', (995, 254, 1168, 296), _UNLOCKED4),
    '5': _button('BECKONING_CARD_5_UNLOCKED', (43, 602, 336, 632), _UNLOCKED5),
    '6': _button('BECKONING_CARD_6_UNLOCKED', (406, 592, 702, 620), _UNLOCKED6),
}
STORY_FINAL_COLLECTED = _button('BECKONING_FINAL_COLLECTED', (1096, 647, 1215, 669), _FINAL)
STORY_VIEWER_LOG = _button('BECKONING_STORY_LOG', (27, 19, 57, 55), _VIEWER)
STORY_VIEWER_SKIP = _button('BECKONING_STORY_SKIP', (1172, 24, 1255, 52), _VIEWER)
MONTHLY_MISSION = _button('BECKONING_MONTHLY_LABEL', (918, 482, 1069, 513), _CARDS)
VICTORY_MISSION = _button('BECKONING_VICTORY_LABEL', (918, 563, 1079, 581), _CARDS)
OCR_MONTHLY_MILESTONE = DigitCounter(
    (973, 511, 1034, 536), letter=(92, 79, 78), threshold=100,
    name='BECKONING_MONTHLY_MILESTONE')
