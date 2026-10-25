"""
Rest the ships that the upcoming tasks need on the second floor of the dorm, where mood recovers the fastest.
The dorm is also where the game shows mood exactly, every ship read here is followed by module/mood/ship.py.

The ship list of the dorm has a tab for each floor, Train for the first floor and Rest for the second.
A card of the Rest tab opens a picker: a dock that lists the ships of the second floor first, then those
of the first floor, then the ships in fleets, then all others. Sorted by mood, the ships in fleets come
lowest mood first, then the others lowest mood first. The ships selected there rest on the second floor
once confirmed.
"""
import re
from math import ceil
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional

import cv2
import numpy as np

from module.base.button import Button
from module.base.template import Template
from module.base.timer import Timer
from module.base.utils import color_similar, crop, get_color
from module.combat.level import LevelOcr
from module.exception import GameTooManyClickError
from module.logger import logger
from module.mood.assets import *
from module.mood.lineup import learn_lineup_ships
from module.mood.plan import rest_plan, upcoming_jobs
from module.mood.ship import DOCK_MOOD_MAX, DOCK_SPEED, ShipMood, ShipMoods, name_key, name_similarity, \
    plausible_name, portrait_signature, tidy_name
from module.ocr.ocr import Digit, DigitCounter, Ocr
from module.retire.assets import DOCK_CHECK, TEMPLATE_FLEET_1, TEMPLATE_FLEET_2, TEMPLATE_FLEET_3, \
    TEMPLATE_FLEET_4, TEMPLATE_FLEET_5, TEMPLATE_FLEET_6
from module.retire.dock import OCR_DOCK_SELECTED, Dock
from module.retire.scanner import EmotionDigit

FLEET_TEMPLATES = [TEMPLATE_FLEET_1, TEMPLATE_FLEET_2, TEMPLATE_FLEET_3, TEMPLATE_FLEET_4, TEMPLATE_FLEET_5,
                   TEMPLATE_FLEET_6]
from module.ui.assets import DORM_CHECK
from module.ui.page import page_dorm

# Ship list of the dorm
DORM_SHIP_LIST_ENTER = Button(area=(30, 660, 190, 695), color=(), button=(30, 660, 190, 695),
                              name='DORM_SHIP_LIST_ENTER')
TAB_ON = (241, 243, 241)
TAB_OFF = (45, 45, 45)
DORM_TABS = {
    'train': Button(area=(300, 120, 340, 130), color=TAB_ON, button=(150, 95, 340, 130), name='DORM_TAB_TRAIN'),
    'rest': Button(area=(515, 120, 555, 130), color=TAB_ON, button=(365, 95, 555, 130), name='DORM_TAB_REST'),
}
OCR_DORM_SHIP_COUNT = DigitCounter(
    Button(area=(976, 86, 1022, 110), color=(), button=(976, 86, 1022, 110), name='DORM_SHIP_COUNT'),
    letter=(180, 180, 180), threshold=128, name='OCR_DORM_SHIP_COUNT')
DIALOG_SLOTS = 6
DIALOG_CARD_X = 142
DIALOG_CARD_DELTA = 170
DIALOG_NAME_Y = {'train': (453, 474), 'rest': (517, 538)}
# Speed of recovery shown on the cards, used when it can not be read
DIALOG_SPEED = {'train': 40, 'rest': 50}

# Picker, cards of the dock
CARD_X = 93
CARD_DELTA_X = 164 + 2 / 3
CARD_WIDTH = 138
CARD_HEIGHT = 204
ROW_HEIGHT = 227
CARDS_PER_ROW = 7
PICKER_FIRST_TOP = 76
# A row is read if the name at the bottom of its cards is above the bar of buttons
ROW_TOP_MIN = 50
ROW_TOP_MAX = 454
# A card can be tapped if its middle is above the bar
TAP_TOP_MAX = 500
# Dragging two rows moves the list about 90% as far
PICKER_DRAG = ((640, 560), (640, 106))
PICKER_DRAG_MOVES = 409
PICKER_MAX_ROWS = 10
# ADB's drag fallback can move twice the requested distance. A half-row gesture still leaves
# an identified row visible; a lost overlap retries from the verified top with a shorter gesture.
PICKER_SCROLL_ROWS = 0.5
PICKER_SCAN_RETRIES = 2
PICKER_SCAN_SECONDS = 120
PICKER_SCROLL_NAME = 'DORM_PICKER_SCROLL'
PICKER_ALIGN_READS = 3
SELECTED_SIMILARITY = 0.55
REMOVE_SIMILARITY = 0.8
LV_SIMILARITY = 0.75
# A ship not seen in the dorm or in a fleet for this long is no longer followed, unless in a lineup
FORGET_AFTER = timedelta(days=3)
# A mood read on the dorm list this far below the one followed is a misread
MOOD_READ_SLACK = 10
# Taps on a button of the dorm before giving up, the game may lag
DORM_TAPS_MAX = 6
# Share of names misread on the picker from which it is taken for not drawn yet
PICKER_GARBLED_MAX = 0.25
# A dorm out of food asks for some as a ship comes in: Ignore, Give Food. Ignore closes it, feeding the dorm is for
# the Dorm task, when its Feed is on.
DORM_FOOD_GIVE = Template(file='./assets/mood/dorm_food_give.png')
DORM_FOOD_AREA = (650, 480, 880, 570)
DORM_FOOD_IGNORE = Button(area=(440, 505, 600, 540), color=(), button=(440, 505, 600, 540), name='DORM_FOOD_IGNORE')
# The picker cuts long names, it never adds letters: a name read with this many letters more than the one looked
# for is an other ship, "Arizona META.." for Arizona. A name read whole with this many fewer is one too.
PICKER_OTHER_SHIP_LETTERS = 3
# A run that could not read the dorm tries again after this
DORM_RETRY_MINUTES = 10


@dataclass
class DormShip:
    place: str
    name: str
    level: int
    mood: int
    speed: int
    sig: Optional[List[int]] = None
    # Name of the ship followed, once told apart
    known: str = ''


@dataclass
class PickerCard:
    row: int
    col: int
    name: str = ''
    level: int = 0
    mood: int = 0
    fleet: int = 0
    submarine: bool = False
    selected: bool = False
    remove: bool = False
    # The name ended with '..', cut by the dock
    truncated: bool = False
    sig: Optional[List[int]] = None
    # Name of the ship followed, once known
    ship: str = ''

    @property
    def index(self):
        return self.row * CARDS_PER_ROW + self.col


def dialog_buttons(x1, y1, x2, y2, name):
    """
    Args:
        x1, x2: Relative to each card of the dorm ship list.
        y1, y2: On the screen.
    """
    buttons = []
    for index in range(DIALOG_SLOTS):
        x = DIALOG_CARD_X + DIALOG_CARD_DELTA * index
        area = (x + x1, y1, x + x2, y2)
        buttons.append(Button(area=area, color=(), button=area, name=f'{name}_{index}'))
    return buttons


def card_buttons(top, x1, y1, x2, y2, name):
    """
    Args:
        top (int): Top of a row of cards of the picker.
        x1, y1, x2, y2: Relative to each card.
    """
    buttons = []
    for col in range(CARDS_PER_ROW):
        x = int(CARD_X + CARD_DELTA_X * col)
        area = (x + x1, top + y1, x + x2, top + y2)
        buttons.append(Button(area=area, color=(), button=area, name=f'{name}_{col}'))
    return buttons


def clean_name(name):
    """
    'Kawakaze ME..' -> 'Kawakaze ME', ' Ha run a ' -> 'Haruna'
    """
    return tidy_name(re.sub(r'[\s.]+$', '', str(name)).strip())


def is_truncated(name):
    """
    The dock ends long names with '..'
    """
    return str(name).strip().endswith('.')


def is_dorm_food_popup(image):
    """
    Returns:
        bool: If the dorm asks for food, out of it
    """
    return template_score(image, DORM_FOOD_GIVE, DORM_FOOD_AREA) > 0.85


def picker_name_similarity(read, name):
    """
    Args:
        read (str): Name of a card of the picker, as OCR reads it
        name (str): Name of the ship looked for

    Returns:
        float: name_similarity(), 0 for a ship whose name only starts like the one looked for or is in it:
            "Arizona META.." is not Arizona, "Arizona" is not Arizona META.
    """
    clean = clean_name(read)
    letters, wanted = len(name_key(clean)), len(name_key(name))
    if letters >= wanted + PICKER_OTHER_SHIP_LETTERS:
        return 0.
    if not is_truncated(read) and letters <= wanted - PICKER_OTHER_SHIP_LETTERS:
        return 0.
    return name_similarity(clean, name)


# Darkness of a card selected on the picker: selected = original * a + b, it washes out the fleet badge
SELECTED_WASH = (0.826, 42.4)
FLEET_SIMILARITY = 0.7


def read_fleet(image, x, top, selected):
    """
    Returns:
        int: Fleet on the badge of a card, 0 for none.
    """
    area = image[top + 117:top + 162, x:x + 35]
    if selected:
        a, b = SELECTED_WASH
        area = np.clip((area.astype(float) - b) / a, 0, 255).astype(np.uint8)
    _, binary = cv2.threshold(area[:, :, 1], 205, 255, cv2.THRESH_BINARY)
    binary = cv2.merge([binary, binary, binary])
    fleet, best = 0, FLEET_SIMILARITY
    for number, template in enumerate(FLEET_TEMPLATES, start=1):
        similarity = float(cv2.matchTemplate(binary, template.image, cv2.TM_CCOEFF_NORMED).max())
        if similarity > best:
            fleet, best = number, similarity
    return fleet


def gray(image):
    return cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)


def template_score(image, template, area):
    """
    Returns:
        float: Best similarity of a template within an area.
    """
    image = gray(crop(image, area, copy=False))
    template = gray(template.image)
    if image.shape[0] < template.shape[0] or image.shape[1] < template.shape[1]:
        return 0.
    return float(cv2.matchTemplate(image, template, cv2.TM_CCOEFF_NORMED).max())


def picker_row_tops(image):
    """
    Find the rows of cards of the picker by the "Lv." of each card.

    Returns:
        list[int]: Top of each row, from the top of the screen.
    """
    template = gray(TEMPLATE_DOCK_LV.image)
    found = []
    for col in range(CARDS_PER_ROW):
        x = int(CARD_X + CARD_DELTA_X * col)
        strip = gray(image[ROW_TOP_MIN - 20:640, x + 70:x + 110])
        result = cv2.matchTemplate(strip, template, cv2.TM_CCOEFF_NORMED).max(axis=1)
        for y in np.where(result > LV_SIMILARITY)[0]:
            found.append(int(y) + ROW_TOP_MIN - 20 - 6)
    # Group the tops found in each column
    tops = []
    for y in sorted(found):
        if tops and y - tops[-1][-1] <= 4:
            tops[-1].append(y)
        else:
            tops.append([y])
    # Half of the columns at least, a card may be loading
    return [int(np.median(group)) for group in tops if len(group) >= 3]


def is_submarine(image, x, top):
    """
    Submarines are in fleets too, the badge on their card does not say it is the submarine fleet.
    Their hull icon is green, brown and dark for sailing ships.
    """
    hsv = cv2.cvtColor(image[top + 8:top + 20, x + 4:x + 24], cv2.COLOR_RGB2HSV).reshape(-1, 3)
    colored = hsv[hsv[:, 1] > 60]
    if not len(colored):
        return False
    hue, value = np.median(colored[:, 0]), np.median(colored[:, 2])
    return 35 <= hue <= 55 or (9 <= hue <= 15 and value < 128)


def ship_list_tab(image):
    """
    Args:
        image (np.ndarray):

    Returns:
        str: 'train' or 'rest', the tab of the dorm ship list shown, None if the list is not shown.
            The active tab is white, the other dark. Other white panels, like the EXP popup of the dorm,
            are not taken for it.
    """
    if not DORM_SHIP_LIST_CLOSE.match(image, offset=(20, 20)):
        return None
    on = {place: color_similar(get_color(image, button.area), TAB_ON, threshold=30)
          for place, button in DORM_TABS.items()}
    off = {place: color_similar(get_color(image, button.area), TAB_OFF, threshold=40)
           for place, button in DORM_TABS.items()}
    if on['train'] and off['rest']:
        return 'train'
    if on['rest'] and off['train']:
        return 'rest'
    return None


def read_dorm_floor(image, place):
    """
    Args:
        image (np.ndarray): Screenshot of the dorm ship list.
        place (str): 'train' or 'rest', the tab shown.

    Returns:
        tuple[list[DormShip], int]: Ships on the floor, and its slots.
    """
    y1, y2 = DIALOG_NAME_Y[place]
    names = Ocr(dialog_buttons(4, y1, 152, y2, 'DORM_NAME'), lang='cnocr', letter=(255, 255, 255),
                threshold=128, name='OCR_DORM_NAME').ocr(image)
    levels = Digit(dialog_buttons(108, 210, 152, 232, 'DORM_LEVEL'), letter=(255, 255, 255), threshold=128,
                   name='OCR_DORM_LEVEL').ocr(image)
    moods = Digit(dialog_buttons(100, 578, 150, 597, 'DORM_MOOD'), letter=(255, 255, 255), threshold=128,
                  name='OCR_DORM_MOOD').ocr(image)
    speeds = Digit(dialog_buttons(118, 605, 136, 626, 'DORM_SPEED'), letter=(255, 255, 255), threshold=128,
                   name='OCR_DORM_SPEED').ocr(image)
    _, _, slots = OCR_DORM_SHIP_COUNT.ocr(image)
    portraits = dialog_buttons(4, 235, 152, 440, 'PORTRAIT')

    ships = []
    for name, level, mood, speed, portrait in zip(names, levels, moods, speeds, portraits):
        name = clean_name(name)
        if len(name_key(name)) < 2:
            continue
        if not 15 <= speed <= 100 or speed % 5:
            speed = DIALOG_SPEED[place]
        ships.append(DormShip(place=place, name=name, level=level, mood=min(mood, 150), speed=speed,
                              sig=portrait_signature(crop(image, portrait.area))))
    return ships, slots if slots else DIALOG_SLOTS


def read_picker_row(image, top, row):
    """
    Args:
        image (np.ndarray): Screenshot of the picker.
        top (int): Top of the row on screen.
        row (int): Index of the row in the list.

    Returns:
        list[PickerCard]: The cards of the row.
    """
    levels = LevelOcr(card_buttons(top, 77, 5, 138, 27, 'PICKER_LEVEL'), name='PICKER_LEVEL',
                      threshold=64).ocr(image)
    moods = EmotionDigit(card_buttons(top, 23, 29, 48, 52, 'PICKER_MOOD'), name='PICKER_MOOD',
                         threshold=176).ocr(image)
    names = Ocr(card_buttons(top, 4, 163, 134, 186, 'PICKER_NAME'), lang='cnocr', letter=(255, 255, 255),
                threshold=128, name='OCR_PICKER_NAME').ocr(image)

    cards = []
    for col, (level, mood, name) in enumerate(zip(levels, moods, names)):
        x = int(CARD_X + CARD_DELTA_X * col)
        card = PickerCard(row=row, col=col, name=clean_name(name), level=level if 1 <= level <= 125 else 0,
                          mood=min(mood, 150), truncated=is_truncated(name))
        card.remove = template_score(image, TEMPLATE_DOCK_REMOVE,
                                     (x - 5, top + 82, x + 80, top + 122)) > REMOVE_SIMILARITY
        if card.remove:
            cards.append(card)
            continue
        card.selected = is_selected(image, x, top)
        card.fleet = read_fleet(image, x, top, card.selected)
        card.submarine = is_submarine(image, x, top)
        card.sig = portrait_signature(crop(image, (x + 30, top + 30, x + CARD_WIDTH - 4, top + 160)))
        cards.append(card)
    return cards


def same_row(a, b):
    """
    Args:
        a (list[PickerCard]):
        b (list[PickerCard]):

    Returns:
        bool: If two reads of a row of the picker, on two screens, are the same row.
            OCR may read a name a little differently.
    """
    same = sum(1 for x, y in zip(a, b) if name_key(x.name) and name_key(x.name) == name_key(y.name))
    return same >= 4


def picker_is_top(first, rows, previous):
    """
    A row at the top of the screen may be any row of the list, it is the first of the list if:
    the REMOVE card is in it, or it is the first row read before, or the list did not move with a drag down.

    Args:
        first (list[PickerCard]): Row at the top of the screen.
        rows (dict[int, list[PickerCard]]): Rows read before, by index.
        previous (list[PickerCard]): Row at the top of the screen before the last drag down, None if none.

    Returns:
        bool:
    """
    if any(card.remove for card in first):
        return True
    if rows and 0 in rows:
        return same_row(first, rows[0])
    return previous is not None and same_row(first, previous)


def is_selected(image, x, top):
    return template_score(image, TEMPLATE_DOCK_SELECTED, (x + 24, top + 81, x + 118, top + 121)) \
        > SELECTED_SIMILARITY


def picker_fleets_end(cards, residents):
    """
    Ships in no fleet come after the ships in fleets. Two in a row out of the dorm and of any fleet,
    a badge may be missed on a card.

    Args:
        cards (dict[int, PickerCard]): By index.
        residents (list[str]): Names of the ships in the dorm.

    Returns:
        int: Index of the first card after the ships in fleets, None if not read yet.
    """
    streak, start = 0, None
    for index in sorted(cards):
        card = cards[index]
        if card.remove or any(name_similarity(card.name, name) > 0 for name in residents):
            continue
        if card.fleet:
            streak = 0
        else:
            if streak == 0:
                start = index
            streak += 1
            if streak >= 2:
                return start
    return None


def picker_fleets_ended(cards, residents):
    return picker_fleets_end(cards, residents) is not None


def picker_low_mood_ended(cards, residents):
    """
    After the ships in fleets, the others come lowest mood first. Those below what the dock recovers to
    fought lately, like ships in the lineup of a stage. They end with a ship at that mood.

    Args:
        cards (dict[int, PickerCard]): By index.
        residents (list[str]): Names of the ships in the dorm.
    """
    end = picker_fleets_end(cards, residents)
    if end is None:
        return False
    return any(card.mood >= DOCK_MOOD_MAX for index, card in cards.items() if index >= end and not card.remove)


def identify(moods, names, level, card_sig=None, dorm_sig=None, taken=()):
    """
    Args:
        moods (ShipMoods):
        names (list[str]): Names read for the ship, the most trusted first.
        level (int): Level read.
        card_sig (list[int]): Portrait of her card on the dock.
        dorm_sig (list[int]): Portrait of her card in the dorm.
        taken (set[str]): Names of the ships told apart already on the screen.

    Returns:
        ShipMood: The known ship, None if not known.
    """
    for name in names:
        if not name:
            continue
        known = moods.find(name)
        # Levels do not go down, a ship below is another of a similar name, like Nevada and Nevada META
        if known is not None and known.name not in taken \
                and not (level and known.level and level < known.level - 1):
            return known
    # A name misread
    known = moods.find_by_portrait(level, card_sig, card=True, exclude=taken)
    if known is None:
        known = moods.find_by_portrait(level, dorm_sig, card=False, exclude=taken)
    if known is not None:
        logger.info(f'{names} is {known.name}, by level and portrait')
    return known


def new_ship(moods, names):
    """
    Returns:
        ShipMood: A new ship followed, named after the first plausible name, None if none is.
    """
    for name in names:
        if name and plausible_name(name) and name not in moods.ships:
            ship = ShipMood(name=name)
            moods.ships[name] = ship
            return ship
    return None


def dorm_mood(read, predicted):
    """
    The dorm list reads moods badly, 0 or a digit of them, 146 as 4.
    A ship in the dorm, or moving into it, has fought no battle since her mood was known and can not be much lower.

    Args:
        read (int): Mood read on the dorm list
        predicted (int): Her mood as followed, recovered until now, None if not followed yet

    Returns:
        int:
    """
    if predicted is not None and read < predicted - MOOD_READ_SLACK:
        return predicted
    return read


def card_misses_fleet(card, known):
    """
    The badge of a card OCR can not read, like the shimmering frame of an ultra rare whose level reads 0, is missed
    at times: Hakuhou of fleet 1 was read in no fleet, and left out of the mood of her fleet.

    Args:
        card (PickerCard): Read with no fleet
        known (ShipMood): Her ship as followed

    Returns:
        bool: If she stays in the fleet she was in
    """
    return not card.level and bool(known.fleet)


def update_moods(moods, dorm, cards, now):
    """
    Remember what has been read.
    A ship is known by her name. A name misread, as on a screen not drawn yet, is told by level and portrait,
    a new ship is only taken from a plausible name, and what can not be told apart is left out.

    Args:
        moods (ShipMoods):
        dorm (list[DormShip]): Ships of both floors.
        cards (list[PickerCard]):
        now (datetime):
    """
    taken = set()
    listed = [card for card in sorted(cards, key=lambda c: c.index) if not card.remove]

    # Ships in the dorm, with their speed of recovery.
    # The picker lists them first, the second floor then the first, in the order of the dorm.
    residents = [ship for ship in dorm if ship.place == 'rest'] + [ship for ship in dorm if ship.place == 'train']
    for position, ship in enumerate(residents):
        # Her card on the picker tells her fleet, and her name if the dock did not cut it
        scores = [(name_similarity(card.name, ship.name), card) for card in listed if not card.ship]
        score, card = max(scores, key=lambda pair: pair[0], default=(0, None))
        if score <= 0:
            card = listed[position] if position < len(listed) and not listed[position].ship else None
            if card is not None and ship.level and card.level and abs(card.level - ship.level) > 1:
                card = None
        names = [ship.name] if card is None else [ship.name, card.name]
        known = identify(moods, names, ship.level, card_sig=card.sig if card is not None else None,
                         dorm_sig=ship.sig, taken=taken)
        if known is None:
            first = [card.name] if card is not None and not card.truncated else []
            known = new_ship(moods, first + names)
        if known is None:
            logger.warning(f'Dorm {ship.place}: {ship.name} can not be told apart, left out')
            continue
        taken.add(known.name)
        ship.known = known.name
        predicted = known.at(now) if known.name in moods.ships else None
        known.mood, known.time, known.place, known.speed = ship.mood, now, ship.place, ship.speed
        if ship.level:
            known.level = ship.level
        known.sig = ship.sig or known.sig
        if card is not None:
            card.ship = known.name
            known.card_sig = card.sig or known.card_sig
            fleet = 0 if card.submarine else card.fleet
            if fleet or not card_misses_fleet(card, known):
                known.fleet = fleet
            # Both screens show her mood, OCR rather drops a digit than adds one
            known.mood = max(known.mood, card.mood)
        else:
            known.mood = dorm_mood(known.mood, predicted)

    # Ships in fleets, out of the dorm
    for card in listed:
        if card.ship or not card.fleet or card.submarine:
            continue
        known = identify(moods, [card.name], card.level, card_sig=card.sig, taken=taken)
        if known is None:
            known = new_ship(moods, [card.name])
        if known is None:
            logger.warning(f'Picker {card.index}: {card.name} can not be told apart, left out')
            continue
        taken.add(known.name)
        card.ship = known.name
        known.mood, known.time, known.place, known.speed = card.mood, now, 'dock', DOCK_SPEED
        known.level, known.fleet = card.level or known.level, card.fleet
        known.sig = known.sig or card.sig
        known.card_sig = card.sig or known.card_sig

    # Ships in no fleet read after the ships in fleets, like ships of a lineup once they are low on mood
    for card in listed:
        if card.ship or card.fleet or card.submarine:
            continue
        known = identify(moods, [card.name], card.level, card_sig=card.sig, taken=taken)
        if known is None:
            continue
        taken.add(known.name)
        card.ship = known.name
        known.mood, known.time, known.place, known.speed = card.mood, now, 'dock', DOCK_SPEED
        if not card_misses_fleet(card, known):
            known.fleet = 0
        known.level = card.level or known.level
        known.sig = known.sig or card.sig
        known.card_sig = card.sig or known.card_sig
    # Those not followed yet, known by level and portrait
    learn_lineup_ships(moods, cards, now)

    # The floor lists establish who left the dorm. A missing picker card does not establish that
    # she left her fleet: a clipped or unread row must not remove her from the fleet's mood check.
    # Only a freshly identified card above clears membership; stale ships expire after a while.
    seen = {card.ship for card in cards if card.ship} | taken
    in_lineups = moods.in_lineups()
    for name in list(moods.ships):
        if name in seen:
            continue
        ship = moods.ships[name]
        if name not in in_lineups and now - ship.time > FORGET_AFTER:
            moods.ships.pop(name)
            continue
        if ship.place != 'dock':
            ship.mood, ship.time = ship.at(now), now
            ship.place, ship.speed = 'dock', DOCK_SPEED
    moods.read = now


class DormReadError(Exception):
    """
    The dorm or its picker does not show as expected, like when the game lags.
    Nothing is changed, the task tries again soon.
    """
    pass


class PickerOverlapError(DormReadError):
    """A scan may retry from the top, but cannot assign row indexes from a drag estimate."""


def picker_drawn(cards):
    """
    A picker read before it is drawn gives names like 'NN-d- AMET', and no fleet badges.

    Returns:
        bool: If most names read are plausible.
    """
    listed = [card for card in cards if not card.remove]
    if not listed:
        return False
    garbled = sum(1 for card in listed if not plausible_name(card.name))
    return garbled <= len(listed) * PICKER_GARBLED_MAX


class DormRest(Dock):
    def dorm_ship_list_enter(self):
        """
        Pages:
            in: page_dorm
            out: dorm ship list
        """
        logger.info('Dorm ship list enter')
        click_timer = Timer(3)
        taps = 0
        for _ in self.loop():
            # The dorm loads its room first, popups like the EXP of the first floor come up then
            if self.ui_additional(get_ship=False):
                click_timer.clear()
                continue
            if self.handle_dorm_food_popup():
                click_timer.clear()
                continue
            if self.dorm_ship_list_tab():
                break
            if self.appear(DORM_CHECK, offset=(20, 20)) and click_timer.reached():
                if taps >= DORM_TAPS_MAX:
                    raise DormReadError('Dorm ship list does not open')
                self.device.click(DORM_SHIP_LIST_ENTER)
                taps += 1
                click_timer.reset()
                continue

    def dorm_ship_list_tab(self):
        """
        Returns:
            str: 'train' or 'rest', the tab of the ship list shown, None if the list is not shown.
        """
        return ship_list_tab(self.device.image)

    def dorm_ship_list_set_tab(self, place):
        logger.info(f'Dorm ship list tab: {place}')
        self.device.click_record_clear()
        click_timer = Timer(3)
        taps = 0
        for _ in self.loop():
            if self.dorm_ship_list_tab() == place:
                break
            if click_timer.reached():
                if taps >= DORM_TAPS_MAX:
                    raise DormReadError(f'Dorm ship list does not switch to {place}')
                self.device.click(DORM_TABS[place])
                taps += 1
                click_timer.reset()
        self.dorm_ship_list_wait_portraits()

    def dorm_ship_list_wait_portraits(self):
        """
        Card portraits come after the cards, they are needed to tell ships apart on screens without names.
        """
        timeout = Timer(3).start()
        for _ in self.loop():
            areas = [crop(self.device.image, button.area, copy=False)
                     for button in dialog_buttons(4, 235, 152, 440, 'PORTRAIT')]
            if all(float(np.std(area)) > 20 for area in areas):
                break
            if timeout.reached():
                logger.warning('Wait dorm ship list portraits timeout')
                break

    def dorm_ship_list_quit(self):
        """
        Pages:
            in: dorm ship list
            out: page_dorm
        """
        logger.info('Dorm ship list quit')
        click_timer = Timer(2)
        for _ in self.loop():
            if self.dorm_ship_list_tab() is None and self.appear(DORM_CHECK, offset=(20, 20)):
                break
            if click_timer.reached() and self.dorm_ship_list_tab() is not None:
                self.device.click(DORM_SHIP_LIST_CLOSE)
                click_timer.reset()

    def dorm_floor_read(self, place):
        """
        Args:
            place (str): 'train' or 'rest'

        Returns:
            tuple[list[DormShip], int]: Ships on the floor, and its slots.

        Pages:
            in: dorm ship list
        """
        self.dorm_ship_list_set_tab(place)
        for _ in range(3):
            ships, slots = read_dorm_floor(self.device.image, place)
            logger.info(f'Dorm {place}: {[(ship.name, ship.mood) for ship in ships]}, slots: {slots}')
            # A list not drawn yet reads like [('ec', 0), ('pn', 0)]
            if all(ship.level and len(name_key(ship.name)) >= 3 for ship in ships):
                return ships, slots
            self.device.sleep(1)
            self.device.screenshot()
        raise DormReadError(f'Dorm {place} can not be read')

    def picker_enter(self):
        """
        Pages:
            in: dorm ship list, Rest tab
            out: DOCK_CHECK, the picker of the second floor
        """
        logger.info('Dorm picker enter')
        card = dialog_buttons(20, 300, 136, 400, 'DORM_CARD')[0]
        click_timer = Timer(3)
        taps = 0
        for _ in self.loop():
            if self.appear(DOCK_CHECK, offset=(20, 20)):
                break
            if self.handle_dorm_food_popup():
                continue
            if click_timer.reached() and self.dorm_ship_list_tab() == 'rest':
                if taps >= DORM_TAPS_MAX:
                    raise DormReadError('Dorm picker does not open')
                self.device.click(card)
                taps += 1
                click_timer.reset()
        self.handle_dock_cards_loading()

    # Rows of the picker read, {row index: [PickerCard]}
    _picker_rows = None

    def picker_align(self, estimate, tops):
        """
        Find how far the list is scrolled. Drags do not move the list by whole rows,
        the first row on screen is matched with the rows read before.

        Args:
            estimate (int): Offset expected.
            tops (list[int]): Tops of the rows on screen.

        Returns:
            int: Offset of the list.
        """
        readable = [top for top in tops if ROW_TOP_MIN <= top <= ROW_TOP_MAX]
        if self._picker_rows:
            for top in readable:
                cards = read_picker_row(self.device.image, top, row=-1)
                for row, known in self._picker_rows.items():
                    if same_row(cards, known):
                        return PICKER_FIRST_TOP + row * ROW_HEIGHT - top
            # An inaccurate drag estimate can label a new row as one already cached and skip it.
            # A scan without overlap cannot safely decide which fleets were actually read.
            raise PickerOverlapError('Dorm picker lost row overlap')
        if tops:
            # An estimate within half a row tells which row is where
            top = tops[0]
            row = round((top + estimate - PICKER_FIRST_TOP) / ROW_HEIGHT)
            return PICKER_FIRST_TOP + row * ROW_HEIGHT - top
        return estimate

    def _picker_drag(self, rows):
        """Move without assigning row identities; selection can reorder the cached rows."""
        (x1, y1), (x2, y2) = PICKER_DRAG
        if rows < 0:
            (x1, y1), (x2, y2) = (x2, y2), (x1, y1)
        distance = abs(rows) / 2
        y2 = int(y1 + (y2 - y1) * distance)
        self.device.drag((x1, y1), (x2, y2), point_random=(0, 0, 0, 0), hold_duration=0.5,
                         name=PICKER_SCROLL_NAME)
        self.handle_dock_cards_loading()
        self.device.screenshot()
        return distance

    def picker_scroll(self, offset, rows):
        """
        Drag the list of the picker.

        Args:
            offset (int): How far the list is scrolled.
            rows (float): Rows to scroll, positive to go down the list, negative up.

        Returns:
            tuple[int, list[int]]: The new offset, the tops of the rows on screen.
        """
        distance = self._picker_drag(rows)
        estimate = max(offset + int(PICKER_DRAG_MOVES * distance) * (1 if rows > 0 else -1), 0)
        for read in range(PICKER_ALIGN_READS):
            tops = picker_row_tops(self.device.image)
            try:
                aligned = self.picker_align(estimate, tops)
            except PickerOverlapError:
                if read + 1 >= PICKER_ALIGN_READS:
                    raise
                # Card names can be briefly covered by the scrolling animation. Re-read a fresh
                # settled frame without another gesture; never infer identity from the estimate.
                self.device.sleep(0.3)
                self.device.screenshot()
                continue
            if self._picker_rows and (aligned - offset) * rows > 0:
                # Only verified directional progress acknowledges this scan's own gestures.
                # Other buttons retain their normal stuck-detection history.
                self.device.click_record_remove(PICKER_SCROLL_NAME)
            return aligned, tops

    def picker_scroll_top(self):
        """
        Returns:
            tuple[int, list[int]]: Offset 0, tops of the rows on screen.
        """
        previous = None
        previous_offset = None
        for _ in range(8):
            self.device.screenshot()
            tops = picker_row_tops(self.device.image)
            first = None
            if tops and abs(tops[0] - PICKER_FIRST_TOP) <= 3:
                first = read_picker_row(self.device.image, tops[0], row=0)
                if picker_is_top(first, self._picker_rows, previous):
                    self.device.click_record_remove(PICKER_SCROLL_NAME)
                    return 0, tops
            if self._picker_rows:
                try:
                    aligned = self.picker_align(0, tops)
                except PickerOverlapError:
                    aligned = None
                if aligned is not None:
                    if previous_offset is not None and aligned < previous_offset:
                        self.device.click_record_remove(PICKER_SCROLL_NAME)
                    previous_offset = aligned
            previous = first
            self.device.drag((640, 150), (640, 600), point_random=(0, 0, 0, 0), hold_duration=0.5,
                             name=PICKER_SCROLL_NAME)
            self.handle_dock_cards_loading()
        raise DormReadError('Dorm picker can not get back to the top')

    def picker_scan(self, residents, low_mood=False):
        """
        Read the cards of the picker: the dorm residents, then the ships in fleets, by mood lowest first.

        Args:
            residents (list[str]): Names of the ships in the dorm, both floors.
            low_mood (bool): Read on the other ships that are low on mood, after the ships in fleets.
                Ships in the lineup of a stage are among them.

        Returns:
            list[PickerCard]:

        Pages:
            in: DOCK_CHECK, the picker, at the top of the list
        """
        logger.hr('Dorm picker scan', level=2)
        self.device.click_record_clear()
        self.dock_favourite_set(False, wait_loading=False)
        self.dock_filter_set(sort='mood')
        self.dock_sort_method_dsc_set(False)

        self._picker_rows = {}
        timeout = Timer(PICKER_SCAN_SECONDS).start()
        rows = PICKER_SCROLL_ROWS
        for attempt in range(PICKER_SCAN_RETRIES + 1):
            if timeout.reached():
                raise DormReadError('Dorm picker scan timed out')
            # Keep the prior first-row identity until the reset is verified, then read a fresh scan.
            offset, tops = self.picker_scroll_top()
            self._picker_rows = {}
            try:
                for _ in range(ceil(PICKER_MAX_ROWS * 1.25 / rows)):
                    if timeout.reached():
                        raise DormReadError('Dorm picker scan timed out')
                    image = self.device.image
                    for top in tops:
                        if not ROW_TOP_MIN <= top <= ROW_TOP_MAX:
                            continue
                        row = round((top + offset - PICKER_FIRST_TOP) / ROW_HEIGHT)
                        if row in self._picker_rows:
                            continue
                        self._picker_rows[row] = read_picker_row(image, top, row)
                    cards = {card.index: card for row in self._picker_rows.values() for card in row}
                    if low_mood:
                        ended = picker_low_mood_ended(cards, residents)
                    else:
                        ended = picker_fleets_ended(cards, residents)
                    if ended or len(self._picker_rows) >= PICKER_MAX_ROWS:
                        break
                    previous = offset
                    offset, tops = self.picker_scroll(offset, rows=rows)
                    if not tops or offset <= previous:
                        raise DormReadError('Dorm picker did not advance')
                else:
                    raise DormReadError('Dorm picker scan exceeded scroll limit')
            except PickerOverlapError:
                if attempt >= PICKER_SCAN_RETRIES:
                    raise
                rows /= 2
                logger.warning(f'Dorm picker lost overlap; restart from top with {rows:g}-row drags')
                continue
            break

        cards = [card for row in sorted(self._picker_rows) for card in self._picker_rows[row]]
        for card in cards:
            if not card.remove:
                logger.info(f'Picker {card.index}: {card.name}, Lv.{card.level}, mood {card.mood}, '
                            f'fleet {card.fleet}{" (submarine)" if card.submarine else ""}'
                            f'{", selected" if card.selected else ""}')
        return cards

    def picker_selected_count(self):
        """
        Returns:
            int: Ships selected, as the picker counts them, None if it can not be read.
        """
        current, _, total = OCR_DOCK_SELECTED.ocr(self.device.image)
        return current if total else None

    def picker_find_on_screen(self, name):
        """
        Returns:
            tuple[int, int]: x and top of the card of a ship on screen, None if not there.
        """
        image = self.device.image
        # name_similarity documents only scores above one as the same ship.
        best, score = None, 1.
        for top in picker_row_tops(image):
            if not ROW_TOP_MIN <= top <= ROW_TOP_MAX:
                continue
            names = Ocr(card_buttons(top, 4, 163, 134, 186, 'PICKER_NAME'), lang='cnocr', letter=(255, 255, 255),
                        threshold=128, name='OCR_PICKER_NAME').ocr(image)
            for col, read in enumerate(names):
                # An other ship of a similar name may be on screen, the one looked for further up
                similarity = picker_name_similarity(read, name)
                if similarity > score:
                    best, score = (int(CARD_X + CARD_DELTA_X * col), top), similarity
        return best

    def picker_find(self, name):
        """
        Cards move on the picker when ships are selected, the ships selected are listed first.
        Find a card by the name of her ship, on screen or from the top of the list down.

        Returns:
            tuple[int, int]: x and top of the card, None if not found.
        """
        found = self.picker_find_on_screen(name)
        if found:
            return found
        self.picker_scroll_top()
        for _ in range(ceil(PICKER_MAX_ROWS * 1.25 / PICKER_SCROLL_ROWS)):
            found = self.picker_find_on_screen(name)
            if found:
                return found
            self._picker_drag(PICKER_SCROLL_ROWS)
        return None

    def picker_toggle(self, card):
        """
        Tap the card of a ship on the picker.
        Each tap moves the count of ships selected by one, that tells if it went as wanted.

        Returns:
            bool: If the card is selected after.
        """
        wanted = not card.selected
        step = 1 if wanted else -1
        for _ in range(3):
            # The card moves once selected or not, find her every time
            found = self.picker_find(card.name)
            if found is None:
                logger.warning(f'{card.name} not found on the picker')
                return card.selected
            x, top = found
            area = (x + 30, top + 60, x + CARD_WIDTH - 30, top + 140)
            before = self.picker_selected_count()
            self.device.click(Button(area=area, color=(), button=area, name=f'PICKER_{name_key(card.name)}'))
            self.device.sleep((0.6, 0.8))
            self.device.screenshot()
            # Out of food, the dorm asks for some as she comes in
            if self.handle_dorm_food_popup():
                self.device.sleep((0.6, 0.8))
                self.device.screenshot()
            # A popup tells why a ship can not rest, like resting on the first floor already
            self.handle_info_bar()
            after = self.picker_selected_count()
            if before is None or after is None:
                logger.warning('Can not read how many ships are selected')
                return card.selected
            if after == before + step:
                card.selected = wanted
                return wanted
            if after == before - step:
                # She was as wanted already, the tap undid it, tap her again
                continue
        logger.warning(f'{card.name} did not switch to selected={wanted}')
        return card.selected

    def handle_dorm_food_popup(self):
        """
        Returns:
            bool: If the dorm asks for food, Ignore is tapped
        """
        if not is_dorm_food_popup(self.device.image):
            return False
        timer = self.get_interval_timer(DORM_FOOD_IGNORE, interval=2, renew=True)
        if timer.reached():
            logger.warning('The dorm is out of food, Ignore')
            self.device.click(DORM_FOOD_IGNORE)
            timer.reset()
        return True

    def picker_confirm(self, button):
        """
        Args:
            button (Button): DORM_PICKER_CONFIRM or DORM_PICKER_CANCEL

        Pages:
            in: DOCK_CHECK, the picker
            out: dorm ship list
        """
        logger.info(f'Dorm picker: {button}')
        for _ in self.loop():
            if self.dorm_ship_list_tab() is not None:
                break
            if self.handle_dorm_food_popup():
                continue
            if self.appear_then_click(button, offset=(20, 20), interval=3):
                continue
            if self.handle_popup_confirm('DORM_PICKER'):
                continue

    def run(self):
        """
        Pages:
            in: Any page
            out: page_dorm, or page_main if the dorm could not be read
        """
        try:
            self.rest_ships()
        except (DormReadError, GameTooManyClickError) as e:
            # The game lags, leave everything as it is
            logger.warning(f'Dorm rest: {e}, trying again in {DORM_RETRY_MINUTES} minutes')
            self.device.click_record_clear()
            self.ui_goto_main()
            self.config.task_delay(minute=DORM_RETRY_MINUTES)
            return
        self.config.task_delay(success=True)

    def rest_ships(self):
        """
        Pages:
            in: Any page
            out: page_dorm
        """
        moods = ShipMoods(self.config)
        self.ui_ensure(page_dorm)
        self.dorm_ship_list_enter()
        train, _ = self.dorm_floor_read('train')
        rest, capacity = self.dorm_floor_read('rest')

        self.picker_enter()
        residents = [ship.name for ship in train + rest]
        # Ships of a lineup in no fleet are read with the others low on mood
        lineup_only = [name for name in moods.in_lineups() if name in moods.ships and not moods.ships[name].fleet]
        cards = self.picker_scan(residents, low_mood=bool(moods.unknown or lineup_only))
        if not picker_drawn(cards):
            raise DormReadError('Dorm picker read before it was drawn')
        now = datetime.now().replace(microsecond=0)
        update_moods(moods, train + rest, cards, now)
        # Selected on the picker: the ships of the second floor, but the one whose card opened it
        selected = {ship.known for ship in rest[1:] if ship.known}
        for card in cards:
            if card.ship:
                if card.selected != (card.ship in selected):
                    logger.info(f'Card {card.index} {card.name} is taken as selected={card.ship in selected}')
                card.selected = card.ship in selected

        jobs = upcoming_jobs(self.config.data, moods)
        # Ships whose card was not read can not be selected, like those of a lineup not low on mood
        on_picker = {card.ship for card in cards if card.ship}
        for job in jobs:
            missing = set(job.names) - on_picker
            if missing:
                logger.warning(f'Task {job.task}: fleet members not read on picker, keeping mood history: '
                               f'{sorted(missing)}')
            job.names = [name for name in job.names if name in on_picker]
            logger.info(f'Task {job.task}: {job.names}, weight {job.weight}')
        rest_names = [ship.known for ship in rest if ship.known]
        train_names = [ship.known for ship in train if ship.known]
        plan = rest_plan(jobs, moods, rest_names, train_names, capacity, now)
        logger.attr('Rest now', rest_names)
        logger.attr('Rest plan', plan)

        if set(plan) == set(rest_names):
            # The ship whose card opened the picker shows unselected, cancelling keeps her
            logger.info('Second floor of the dorm stays as it is')
            changed = False
        else:
            changed = self.picker_apply(cards, plan)
        self.dock_filter_set(sort='level')
        self.dock_sort_method_dsc_set(True)
        self.picker_confirm(DORM_PICKER_CONFIRM if changed else DORM_PICKER_CANCEL)

        if changed:
            rest, _ = self.dorm_floor_read('rest')
            resting = set()
            for ship in rest:
                known = identify(moods, [ship.name], ship.level, dorm_sig=ship.sig, taken=resting)
                if known is not None:
                    mood = dorm_mood(ship.mood, known.at(now))
                    known.place, known.speed, known.mood, known.time = 'rest', ship.speed, mood, now
                    resting.add(known.name)
            logger.attr('Rest now', sorted(resting))
            for name in rest_names:
                if name not in resting and name in moods.ships:
                    moods.ships[name].place, moods.ships[name].speed = 'dock', DOCK_SPEED
        moods.save()
        self.dorm_ship_list_quit()

    def picker_apply(self, cards, plan):
        """
        Select on the picker the ships of the plan, unselect the others.

        Returns:
            bool: If anything changed.
        """
        wanted = set(plan)
        toggle, seen = [], set()
        for card in cards:
            if card.remove or not card.ship or card.ship in seen:
                continue
            seen.add(card.ship)
            if card.selected != (card.ship in wanted):
                toggle.append(card)
        missing = wanted - {card.ship for card in cards if card.ship}
        if missing:
            logger.warning(f'Ships of the plan not found on the picker: {missing}')
        if not toggle:
            logger.info('Second floor of the dorm stays as it is')
            return False

        # Make room first
        for card in sorted(toggle, key=lambda c: (not c.selected, c.index)):
            logger.info(f'{"Unselect" if card.selected else "Select"} {card.ship}')
            self.picker_toggle(card)
        self.device.screenshot()
        current, _, total = OCR_DOCK_SELECTED.ocr(self.device.image)
        logger.attr('Selected', f'{current}/{total}')
        return True
