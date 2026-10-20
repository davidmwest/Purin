"""
Gear from storage for a ship that comes into a fleet without it.

On the detail page of a ship, the Info tab shows her 5 gear slots. On the Gear tab, a tap on a slot opens the depot
with the gear that fits it, the rarest first. Its EQUIPPED switch lists the gear other ships wear too: it is turned
off, so only gear in storage is listed and no ship loses hers. The first item goes on after a confirm.
"""
import cv2
import numpy as np

from module.base.decorator import cached_property
from module.base.timer import Timer
from module.base.utils import crop, load_image
from module.equipment.assets import (EQUIP_CONFIRM, EQUIP_TAKE_ON_0, EQUIP_TAKE_ON_1, EQUIP_TAKE_ON_2,
                                     EQUIP_TAKE_ON_3, EQUIP_TAKE_ON_4, EQUIPMENT_OPEN, SHIP_INFO_EQUIPMENT_CHECK)
from module.equipment.equipment_change import EQUIPMENT_GRID
from module.leveling.fleet import LevelingFleet, area_button
from module.leveling.plan import GEARED_SLOTS
from module.logger import logger
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
# First item of the depot, the rarest. A card is bright and busy, the background without one dark and plain.
FIRST_ITEM = area_button((143, 90, 261, 210), 'DEPOT_FIRST_ITEM')
ITEM_MEAN = 100
ITEM_STD = 30
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


def depot_has_item(image):
    """
    Returns:
        bool: If the depot lists an item first, False when storage has nothing for the slot
    """
    area = crop(image, FIRST_ITEM.area, copy=False)
    return bool(area.mean() > ITEM_MEAN and area.std() > ITEM_STD)


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

    def gear_slot_fill(self, index):
        """
        Put the rarest gear of storage that fits in an empty slot.

        Args:
            index (int): 0 to 4

        Returns:
            bool: If a gear went on. False if storage has none for the slot.

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
            return False

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
            return False
        # The list is drawn again
        self.device.sleep(0.5)
        self.device.screenshot()
        if not depot_has_item(self.device.image):
            logger.info(f'No gear in storage for slot {index}')
            self.gear_depot_leave()
            return False

        click.clear()
        for _ in self.loop(timeout=GEAR_TIMEOUT):
            if self.appear(EQUIP_CONFIRM, offset=(20, 20)):
                break
            if self.is_in_depot() and (not click.started() or click.reached()):
                self.device.click(FIRST_ITEM)
                click.start()
                click.reset()
        else:
            logger.warning(f'The first gear of the depot did not open')
            self.gear_depot_leave()
            return False

        for _ in self.loop(timeout=GEAR_TIMEOUT):
            if self.appear(SHIP_INFO_EQUIPMENT_CHECK, offset=(20, 20)) \
                    and not self.appear(EQUIP_CONFIRM, offset=(20, 20)):
                logger.info(f'Gear slot {index} filled')
                return True
            if self.appear_then_click(EQUIP_CONFIRM, offset=(20, 20), interval=2):
                continue
            if self.handle_popup_confirm('GEAR_EQUIP'):
                continue
        logger.warning(f'Gear slot {index} not confirmed')
        if self.is_in_depot():
            self.gear_depot_leave()
        return False

    def gear_fill(self):
        """
        Fill the empty gear slots of the ship shown with gear from storage.

        Returns:
            int: Slots with gear after it

        Pages:
            in: detail page of a ship, the Info tab
            out: detail page of a ship, the Info tab
        """
        self.ship_side_navbar_ensure(bottom=1)
        for _ in self.loop(timeout=GEAR_TIMEOUT):
            if self.appear(EQUIPMENT_OPEN, offset=(20, 20)):
                break
        empty = empty_gear_slots(self.device.image)
        filled = len(GEAR_SLOTS) - len(empty)
        logger.attr('Empty gear slots', empty)
        if not empty:
            return filled

        self.ship_side_navbar_ensure(bottom=2)
        for _ in self.loop(timeout=GEAR_TIMEOUT):
            if self.appear(SHIP_INFO_EQUIPMENT_CHECK, offset=(20, 20)):
                break
        for index in empty:
            if self.gear_slot_fill(index):
                filled += 1
        self.ship_side_navbar_ensure(bottom=1)
        return filled

    def gear_ship(self, target):
        """
        Find a ship in the dock and fill her empty gear slots from storage.

        Args:
            target (Target):

        Returns:
            bool: If she has gear in GEARED_SLOTS slots or more after it. False if she was not found.

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
            return False
        self.ship_info_enter(click_button=ship.button, check_button=EQUIPMENT_OPEN, long_click=False,
                             skip_first_screenshot=False)
        filled = self.gear_fill()
        logger.info(f'{target.ship} has gear in {filled} slots')
        self.ui_back(check_button=DOCK_CHECK)
        return filled >= GEARED_SLOTS
