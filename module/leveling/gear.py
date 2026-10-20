"""
Gear from storage for a ship that comes into a fleet without gear worth the name.

On the detail page of a ship, the Info tab shows her 5 gear slots and their enhancement. On the Gear tab, a tap on an
empty slot opens the depot with the gear that fits it, the rarest first. A tap on gear opens its popup instead, whose
Change button opens the depot with a REMOVE card first. The EQUIPPED switch of the depot lists the gear other ships
wear too: it is turned off, so only gear in storage is listed and no ship loses hers. Gear picked there asks to
Confirm, beside the gear it replaces.

The most enhanced gear of the depot goes in each empty slot, and in place of gear below GEARED_LEVEL when storage has
better: gear at +0 lost a battle of 12-4 that gear at +10 won. Her enhancements are read again at the end.
A step that does not go as planned comes back to the Gear tab, closing what it opened.
"""
import cv2
import numpy as np

from module.base.decorator import cached_property
from module.base.utils import crop, load_image
from module.equipment.assets import (EQUIP_TAKE_ON_0, EQUIP_TAKE_ON_1, EQUIP_TAKE_ON_2, EQUIP_TAKE_ON_3,
                                     EQUIP_TAKE_ON_4, EQUIPMENT_OPEN, SHIP_INFO_EQUIPMENT_CHECK)
from module.equipment.equipment_change import EQUIPMENT_GRID
from module.inventory.info import GEAR_LEVEL_AREAS, parse_trailing_number
from module.leveling.fleet import LevelingFleet, area_button
from module.leveling.plan import GEARED_LEVEL
from module.logger import logger
from module.ocr.ocr import Digit, Ocr
from module.retire.assets import DOCK_CHECK
from module.ui.assets import BACK_ARROW
from module.ui.page import page_dock

DEPOT_TITLE = './assets/leveling/depot_title.png'
DEPOT_TITLE_AREA = (90, 0, 240, 60)
DEPOT_SIMILARITY = 0.85
# The EQUIPPED switch of the depot, on or off
EQUIPPED_ON = './assets/leveling/equipped_on.png'
EQUIPPED_OFF = './assets/leveling/equipped_off.png'
EQUIPPED_AREA = (0, 480, 110, 600)
EQUIPPED_SWITCH = area_button((14, 510, 76, 566), 'EQUIPPED_SWITCH')
# Cards of the depot, 7 a row and 3 rows on screen, the rarest first. A card is bright and busy, the background
# without one dark and plain. The enhancement of a gear is on a tag at the middle of the left side of its card.
DEPOT_CARD_LEFTS = [137 + round(157.9 * column) for column in range(7)]
DEPOT_CARD_TOPS = [84 + 178 * row for row in range(3)]
DEPOT_CARDS = [area_button((x + 6, y + 6, x + 124, y + 126), f'DEPOT_CARD_{row}_{column}')
               for row, y in enumerate(DEPOT_CARD_TOPS) for column, x in enumerate(DEPOT_CARD_LEFTS)]
DEPOT_TAGS = [area_button((x + 2, y + 80, x + 48, y + 102), f'DEPOT_TAG_{row}_{column}')
              for row, y in enumerate(DEPOT_CARD_TOPS) for column, x in enumerate(DEPOT_CARD_LEFTS)]
FIRST_ITEM = DEPOT_CARDS[0]
# What Digit reads for the plus of a tag, or for the point at its right
TAG_MISREAD = '41'
ITEM_MEAN = 100
ITEM_STD = 30
# Gear slots of the Info tab, their enhancement
INFO_GEAR_LEVELS = [area_button(area, f'INFO_GEAR_LEVEL_{index}') for index, area in enumerate(GEAR_LEVEL_AREAS)]
# A gear slot of the Info tab is empty below this sharpness, gear at +0 is about 30
EMPTY_SLOT_EDGE = 10
GEAR_SLOTS = [EQUIP_TAKE_ON_0, EQUIP_TAKE_ON_1, EQUIP_TAKE_ON_2, EQUIP_TAKE_ON_3, EQUIP_TAKE_ON_4]
# The popup of gear in a slot: Unequip, Change, Enhance. A tap below it, out of it, closes it.
GEAR_CHANGE = './assets/leveling/gear_change.png'
GEAR_CHANGE_AREA = (540, 600, 740, 700)
GEAR_CHANGE_BUTTON = area_button((562, 628, 717, 672), 'GEAR_CHANGE')
GEAR_POPUP_CLOSE = area_button((180, 630, 230, 670), 'GEAR_POPUP_CLOSE')
# Gear picked in the depot, beside what the slot has: Cancel, Confirm
GEAR_CONFIRM = './assets/leveling/gear_confirm.png'
GEAR_CONFIRM_AREA = (770, 600, 980, 700)
GEAR_CONFIRM_BUTTON = area_button((800, 630, 952, 670), 'GEAR_CONFIRM')
GEAR_CANCEL_BUTTON = area_button((328, 630, 482, 670), 'GEAR_CANCEL')
TEMPLATE_SIMILARITY = 0.85
# Seconds for each step
GEAR_TIMEOUT = 10


def gray(image):
    return cv2.cvtColor(image, cv2.COLOR_RGB2GRAY) if image.ndim == 3 else image


def template_score(image, template, area):
    """
    Returns:
        float: Best similarity of the template within the area
    """
    region = gray(crop(image, area, copy=False))
    return float(cv2.minMaxLoc(cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED))[1])


def empty_gear_slots(image):
    """
    Args:
        image (np.ndarray): Info tab of the detail page of a ship

    Returns:
        list[int]: Gear slots without gear, 0 to 4
    """
    empty = []
    for index, button in enumerate(EQUIPMENT_GRID.buttons):
        area = crop(image, button.area, copy=False)
        if np.mean(np.abs(cv2.Sobel(area, 3, 1, 1))) < EMPTY_SLOT_EDGE:
            empty.append(index)
    return empty


def is_card(image, button):
    """
    Returns:
        bool: If a card of a gear is there, bright, not dimmed nor missing
    """
    area = crop(image, button.area, copy=False)
    return bool(area.mean() > ITEM_MEAN and area.std() > ITEM_STD)


def depot_has_item(image):
    """
    Returns:
        bool: If the depot lists an item first, False when storage has nothing for the slot
    """
    return is_card(image, FIRST_ITEM)


def gear_levels(image):
    """
    Args:
        image (np.ndarray): Info tab of the detail page of a ship

    Returns:
        list[int]: Enhancement of her 5 gear slots, 0 for gear at +0 and for an empty slot
    """
    texts = Ocr(INFO_GEAR_LEVELS, letter=(255, 255, 255), threshold=128, name='GEAR_LEVEL').ocr(image)
    return [parse_trailing_number(str(text), maximum=13) for text in texts]


def tag_level(digit):
    """
    Args:
        digit (int): What Digit read on the tag of a card, "+10"

    Returns:
        int: Enhancement, 0 to 13. Digit can take the plus of the tag for a 4 or a 1, and the point at its right
            too: "+11" reads 411, "+10" 410, 104 or 101, "+6" 464. A tag read as none of these is taken as +0,
            rather than as the most enhanced gear.
    """
    if digit <= 13:
        return digit
    text = str(digit)
    point = text[-1] in TAG_MISREAD
    plus = text[0] in TAG_MISREAD
    candidates = []
    if point:
        candidates.append(text[:-1])
    if plus:
        candidates.append(text[1:])
    if point and plus:
        candidates.append(text[1:-1])
    for candidate in candidates:
        if candidate and int(candidate) <= 13:
            return int(candidate)
    return 0


def depot_card_levels(image):
    """
    Args:
        image (np.ndarray): The depot of a slot

    Returns:
        list[int]: Enhancement of each card on screen, -1 where there is no card, a dimmed one or the REMOVE card.
            A digit read where a card has no tag is dropped, a tag reads "+10".
    """
    digits = Digit(DEPOT_TAGS, letter=(255, 255, 255), threshold=128, name='DEPOT_TAG').ocr(image)
    texts = Ocr(DEPOT_TAGS, lang='cnocr', letter=(255, 255, 255), threshold=128, name='DEPOT_TAG_TEXT').ocr(image)
    levels = []
    for card, digit, text in zip(DEPOT_CARDS, digits, texts):
        if not is_card(image, card):
            levels.append(-1)
        elif '+' in str(text):
            levels.append(tag_level(digit))
        else:
            levels.append(0)
    return levels


def best_card(levels):
    """
    Args:
        levels (list[int]): depot_card_levels()

    Returns:
        int: Index of the most enhanced card, the rarest of them, -1 if there is none
    """
    best = -1
    for index, level in enumerate(levels):
        if level >= 0 and (best < 0 or level > levels[best]):
            best = index
    return best


class LevelingGear(LevelingFleet):
    @cached_property
    def _depot_title(self):
        return gray(load_image(DEPOT_TITLE))

    @cached_property
    def _equipped_on(self):
        return gray(load_image(EQUIPPED_ON))

    @cached_property
    def _equipped_off(self):
        return gray(load_image(EQUIPPED_OFF))

    @cached_property
    def _gear_change(self):
        return gray(load_image(GEAR_CHANGE))

    @cached_property
    def _gear_confirm(self):
        return gray(load_image(GEAR_CONFIRM))

    def is_in_depot(self):
        return template_score(self.device.image, self._depot_title, DEPOT_TITLE_AREA) > DEPOT_SIMILARITY

    def is_gear_popup(self):
        """
        Returns:
            bool: If the popup of gear in a slot is shown, with its Change button
        """
        return template_score(self.device.image, self._gear_change, GEAR_CHANGE_AREA) > TEMPLATE_SIMILARITY

    def is_gear_confirm(self):
        """
        Returns:
            bool: If gear picked in the depot asks to Confirm
        """
        return template_score(self.device.image, self._gear_confirm, GEAR_CONFIRM_AREA) > TEMPLATE_SIMILARITY

    def is_gear_tab(self):
        """
        Returns:
            bool: If on the Gear tab of the detail page of a ship, no popup over it
        """
        return self.appear(SHIP_INFO_EQUIPMENT_CHECK, offset=(20, 20)) \
            and not self.is_gear_popup() and not self.is_gear_confirm()

    def depot_lists_equipped(self):
        """
        Returns:
            bool: If the depot lists the gear other ships wear too
        """
        on = template_score(self.device.image, self._equipped_on, EQUIPPED_AREA)
        off = template_score(self.device.image, self._equipped_off, EQUIPPED_AREA)
        return on > off

    def gear_click(self, button, interval=2):
        """
        Click a button, once in an interval.

        Returns:
            bool: If clicked
        """
        timer = self.get_interval_timer(button, interval=interval, renew=True)
        if not timer.reached():
            return False
        self.device.click(button)
        timer.reset()
        return True

    def gear_tab_ensure(self):
        """
        Back to the Gear tab: closes the popup of gear, cancels gear picked, leaves the depot.

        Returns:
            bool: If on the Gear tab

        Pages:
            in: the Gear tab, the popup of gear, the depot of a slot, gear picked there
            out: SHIP_INFO_EQUIPMENT_CHECK, the Gear tab
        """
        self.interval_clear([GEAR_CANCEL_BUTTON, GEAR_POPUP_CLOSE, BACK_ARROW])
        for _ in self.loop(timeout=GEAR_TIMEOUT):
            if self.is_gear_tab():
                return True
            if self.is_gear_confirm():
                self.gear_click(GEAR_CANCEL_BUTTON)
                continue
            if self.is_gear_popup():
                self.gear_click(GEAR_POPUP_CLOSE)
                continue
            if self.is_in_depot():
                self.gear_click(BACK_ARROW)
                continue
        logger.warning('Gear tab not found')
        return False

    def gear_depot_enter(self, index):
        """
        Open the depot of a gear slot. A tap on an empty slot opens it, a tap on gear opens its popup and Change the
        depot.

        Args:
            index (int): 0 to 4

        Returns:
            bool: If the depot opened, else back on the Gear tab

        Pages:
            in: SHIP_INFO_EQUIPMENT_CHECK, the Gear tab
            out: the depot of the slot
        """
        slot = GEAR_SLOTS[index]
        self.interval_clear([slot, GEAR_CHANGE_BUTTON])
        for _ in self.loop(timeout=GEAR_TIMEOUT):
            if self.is_in_depot():
                return True
            if self.is_gear_popup():
                self.gear_click(GEAR_CHANGE_BUTTON)
                continue
            if self.is_gear_tab():
                self.gear_click(slot)
                continue
        logger.warning(f'Depot of gear slot {index} did not open')
        self.gear_tab_ensure()
        return False

    def gear_slot_fill(self, index, current=None):
        """
        Put the most enhanced gear of storage that fits in a slot.

        Args:
            index (int): 0 to 4
            current (int): Enhancement of the gear in the slot, None if empty.
                Gear is only replaced by gear at GEARED_LEVEL or above, more enhanced.

        Returns:
            int: Enhancement of the gear that went on, None if none did

        Pages:
            in: SHIP_INFO_EQUIPMENT_CHECK, the Gear tab
            out: SHIP_INFO_EQUIPMENT_CHECK, the Gear tab, unless a step could not come back to it
        """
        if not self.gear_depot_enter(index):
            return None

        # Gear in storage only
        self.interval_clear(EQUIPPED_SWITCH)
        for _ in self.loop(timeout=GEAR_TIMEOUT):
            if not self.depot_lists_equipped():
                break
            self.gear_click(EQUIPPED_SWITCH)
        else:
            logger.warning('The depot keeps listing gear worn by other ships')
            self.gear_tab_ensure()
            return None
        # The list is drawn again
        self.device.sleep(0.5)
        self.device.screenshot()
        levels = depot_card_levels(self.device.image)
        best = best_card(levels)
        logger.attr(f'Depot of slot {index}', levels)
        if best < 0:
            logger.info(f'No gear in storage for slot {index}')
            self.gear_tab_ensure()
            return None
        level = levels[best]
        if current is not None and (level < GEARED_LEVEL or level <= current):
            logger.info(f'Storage has no better gear for slot {index}, +{level} at best for +{current}')
            self.gear_tab_ensure()
            return None

        card = DEPOT_CARDS[best]
        self.interval_clear(card)
        for _ in self.loop(timeout=GEAR_TIMEOUT):
            if self.is_gear_confirm():
                break
            if self.is_in_depot():
                self.gear_click(card)
        else:
            logger.warning(f'Gear {best} of the depot did not open')
            self.gear_tab_ensure()
            return None

        self.interval_clear(GEAR_CONFIRM_BUTTON)
        for _ in self.loop(timeout=GEAR_TIMEOUT):
            if self.is_gear_tab():
                logger.info(f'Gear slot {index} takes gear at +{level}')
                return level
            if self.is_gear_confirm():
                self.gear_click(GEAR_CONFIRM_BUTTON)
                continue
            if self.handle_popup_confirm('GEAR_EQUIP'):
                continue
        logger.warning(f'Gear slot {index} not confirmed')
        self.gear_tab_ensure()
        return None

    def gear_read(self):
        """
        Returns:
            list[int]: Enhancement of the 5 gear slots, -1 for an empty one

        Pages:
            in: detail page of a ship
            out: detail page of a ship, the Info tab
        """
        self.ship_side_navbar_ensure(bottom=1)
        for _ in self.loop(timeout=GEAR_TIMEOUT):
            if self.appear(EQUIPMENT_OPEN, offset=(20, 20)):
                break
        empty = empty_gear_slots(self.device.image)
        levels = gear_levels(self.device.image)
        return [-1 if index in empty else level for index, level in enumerate(levels)]

    def gear_fill(self):
        """
        Gear from storage for the ship shown: in each empty slot, and in place of gear below GEARED_LEVEL.

        Returns:
            list[int]: Enhancement of her 5 gear slots after it, 0 for an empty one.
                None if a step could not come back to the Gear tab.

        Pages:
            in: detail page of a ship
            out: detail page of a ship, the Info tab. Unknown if None.
        """
        levels = self.gear_read()
        logger.attr('Gear', levels)
        slots = [index for index, level in enumerate(levels) if level < GEARED_LEVEL]
        if not slots:
            return levels

        self.ship_side_navbar_ensure(bottom=2)
        if not self.gear_tab_ensure():
            return None
        for index in slots:
            self.gear_slot_fill(index, current=None if levels[index] < 0 else levels[index])
            # A popup that stays open hides the side navbar
            if not self.gear_tab_ensure():
                return None
        levels = self.gear_read()
        logger.attr('Gear', levels)
        return [max(level, 0) for level in levels]

    def gear_ship(self, target):
        """
        Find a ship in the dock and give her gear from storage, see gear_fill().

        Args:
            target (Target):

        Returns:
            list[int]: Enhancement of her 5 gear slots after it, None if she was not found or the gear steps did not
                end well

        Pages:
            in: any
            out: page_dock
        """
        logger.hr(f'Gear {target.ship}', level=2)
        self.ui_ensure(page_dock)
        ship = None
        if self.dock_search(target.ship):
            ship = self.dock_find(target)
        if ship is None:
            return None
        self.ship_info_enter(click_button=ship.button, check_button=EQUIPMENT_OPEN, long_click=False,
                             skip_first_screenshot=False)
        levels = self.gear_fill()
        if levels is None:
            logger.warning(f'Gear of {target.ship} left unfinished')
            self.ui_ensure(page_dock)
            return None
        logger.info(f'{target.ship} has gear at {levels}')
        self.ui_back(check_button=DOCK_CHECK)
        return levels
