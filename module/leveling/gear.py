"""
Gear from storage for a ship that comes into a fleet without gear worth the name.

On the detail page of a ship, the Info tab shows her 5 gear slots and their enhancement. On the Gear tab, a tap on a
slot opens the depot with the gear that fits it, the rarest first. Its EQUIPPED switch lists the gear other ships
wear too: it is turned off, so only gear in storage is listed and no ship loses hers.

The most enhanced gear of the depot goes in each empty slot, and in place of gear below GEARED_LEVEL when storage has
better: gear at +0 lost a battle of 12-4 that gear at +10 won. Her enhancements are read again at the end.
"""
import cv2
import numpy as np

from module.base.decorator import cached_property
from module.base.timer import Timer
from module.base.utils import crop, load_image
from module.equipment.assets import (EQUIP_CONFIRM, EQUIP_TAKE_ON_0, EQUIP_TAKE_ON_1, EQUIP_TAKE_ON_2,
                                     EQUIP_TAKE_ON_3, EQUIP_TAKE_ON_4, EQUIPMENT_OPEN, SHIP_INFO_EQUIPMENT_CHECK)
from module.equipment.equipment_change import EQUIPMENT_GRID
from module.inventory.info import GEAR_LEVEL_AREAS, parse_trailing_number
from module.leveling.fleet import LevelingFleet, area_button
from module.leveling.plan import GEARED_LEVEL
from module.logger import logger
from module.ocr.ocr import Digit, Ocr
from module.retire.assets import DOCK_CHECK
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
ITEM_MEAN = 100
ITEM_STD = 30
# Gear slots of the Info tab, their enhancement
INFO_GEAR_LEVELS = [area_button(area, f'INFO_GEAR_LEVEL_{index}') for index, area in enumerate(GEAR_LEVEL_AREAS)]
# A gear slot of the Info tab is empty below this sharpness, gear at +0 is about 30
EMPTY_SLOT_EDGE = 10
GEAR_SLOTS = [EQUIP_TAKE_ON_0, EQUIP_TAKE_ON_1, EQUIP_TAKE_ON_2, EQUIP_TAKE_ON_3, EQUIP_TAKE_ON_4]
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


def depot_card_levels(image):
    """
    Args:
        image (np.ndarray): The depot of a slot

    Returns:
        list[int]: Enhancement of each card on screen, -1 where there is no card or a dimmed one.
            A digit read where a card has no tag is dropped, a tag reads "+10".
    """
    digits = Digit(DEPOT_TAGS, letter=(255, 255, 255), threshold=128, name='DEPOT_TAG').ocr(image)
    texts = Ocr(DEPOT_TAGS, lang='cnocr', letter=(255, 255, 255), threshold=128, name='DEPOT_TAG_TEXT').ocr(image)
    levels = []
    for card, digit, text in zip(DEPOT_CARDS, digits, texts):
        if not is_card(image, card):
            levels.append(-1)
        elif '+' in str(text):
            levels.append(min(digit, 13))
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

    def is_in_depot(self):
        return template_score(self.device.image, self._depot_title, DEPOT_TITLE_AREA) > DEPOT_SIMILARITY

    def depot_lists_equipped(self):
        """
        Returns:
            bool: If the depot lists the gear other ships wear too
        """
        on = template_score(self.device.image, self._equipped_on, EQUIPPED_AREA)
        off = template_score(self.device.image, self._equipped_off, EQUIPPED_AREA)
        return on > off

    def gear_depot_leave(self):
        """
        Pages:
            in: the depot of a slot
            out: SHIP_INFO_EQUIPMENT_CHECK, the Gear tab
        """
        self.ui_back(check_button=SHIP_INFO_EQUIPMENT_CHECK)

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
            out: SHIP_INFO_EQUIPMENT_CHECK, the Gear tab
        """
        click = Timer(2)
        for _ in self.loop(timeout=GEAR_TIMEOUT):
            if self.is_in_depot():
                break
            if self.appear(SHIP_INFO_EQUIPMENT_CHECK, offset=(20, 20)) and (not click.started() or click.reached()):
                self.device.click(GEAR_SLOTS[index])
                click.start()
                click.reset()
        else:
            logger.warning(f'Depot of gear slot {index} did not open')
            return None

        # Gear in storage only
        click.clear()
        for _ in self.loop(timeout=GEAR_TIMEOUT):
            if not self.depot_lists_equipped():
                break
            if not click.started() or click.reached():
                self.device.click(EQUIPPED_SWITCH)
                click.start()
                click.reset()
        else:
            logger.warning('The depot keeps listing gear worn by other ships')
            self.gear_depot_leave()
            return None
        # The list is drawn again
        self.device.sleep(0.5)
        self.device.screenshot()
        levels = depot_card_levels(self.device.image)
        best = best_card(levels)
        logger.attr(f'Depot of slot {index}', levels)
        if best < 0:
            logger.info(f'No gear in storage for slot {index}')
            self.gear_depot_leave()
            return None
        level = levels[best]
        if current is not None and (level < GEARED_LEVEL or level <= current):
            logger.info(f'Storage has no better gear for slot {index}, +{level} at best for +{current}')
            self.gear_depot_leave()
            return None

        click.clear()
        for _ in self.loop(timeout=GEAR_TIMEOUT):
            if self.appear(EQUIP_CONFIRM, offset=(20, 20)):
                break
            if self.is_in_depot() and (not click.started() or click.reached()):
                self.device.click(DEPOT_CARDS[best])
                click.start()
                click.reset()
        else:
            logger.warning(f'Gear {best} of the depot did not open')
            self.gear_depot_leave()
            return None

        for _ in self.loop(timeout=GEAR_TIMEOUT):
            if self.appear(SHIP_INFO_EQUIPMENT_CHECK, offset=(20, 20)) \
                    and not self.appear(EQUIP_CONFIRM, offset=(20, 20)):
                logger.info(f'Gear slot {index} takes gear at +{level}')
                return level
            if self.appear_then_click(EQUIP_CONFIRM, offset=(20, 20), interval=2):
                continue
            if self.handle_popup_confirm('GEAR_EQUIP'):
                continue
        logger.warning(f'Gear slot {index} not confirmed')
        if self.is_in_depot():
            self.gear_depot_leave()
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
            list[int]: Enhancement of her 5 gear slots after it, 0 for an empty one

        Pages:
            in: detail page of a ship
            out: detail page of a ship, the Info tab
        """
        levels = self.gear_read()
        logger.attr('Gear', levels)
        slots = [index for index, level in enumerate(levels) if level < GEARED_LEVEL]
        if not slots:
            return levels

        self.ship_side_navbar_ensure(bottom=2)
        for _ in self.loop(timeout=GEAR_TIMEOUT):
            if self.appear(SHIP_INFO_EQUIPMENT_CHECK, offset=(20, 20)):
                break
        for index in slots:
            self.gear_slot_fill(index, current=None if levels[index] < 0 else levels[index])
        levels = self.gear_read()
        logger.attr('Gear', levels)
        return [max(level, 0) for level in levels]

    def gear_ship(self, target):
        """
        Find a ship in the dock and give her gear from storage, see gear_fill().

        Args:
            target (Target):

        Returns:
            list[int]: Enhancement of her 5 gear slots after it, None if she was not found

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
        logger.info(f'{target.ship} has gear at {levels}')
        self.ui_back(check_button=DOCK_CHECK)
        return levels
