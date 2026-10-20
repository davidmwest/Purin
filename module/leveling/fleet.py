"""
Ships of the fleets on the fleet page, and putting a ship of the dock in a slot of a fleet.

The Info view of the fleet page shows a card for each ship, with her name and level. It lists the main fleet
from the flagship on, while the 3D view has the flagship in the middle, the second ship on the right and the third
on the left. The vanguard goes from left to right in both.

Tapping a ship of the 3D view opens the dock to replace her. The search bar of the dock finds a ship by her name:
it opens on the magnifier, a tap on its field brings up the input bar of Android, and adb types the name in it.

The fleet page is known by its lit Formation button, the 3D view. Other tasks go to the fleet page and expect it,
so this leaves the page in the 3D view.
"""
import re

from module.base.button import Button
from module.base.timer import Timer
from module.equipment.assets import FLEET_DETAIL, FLEET_DETAIL_CHECK
from module.formation.formation import Formation
from module.leveling.plan import RETROFIT, Slot, name_key
from module.logger import logger
from module.ocr.ocr import Digit, Ocr
from module.retire.assets import DOCK_CHECK
from module.retire.scanner import ShipScanner
from module.ui.assets import FLEET_CHECK


def area_button(area, name):
    return Button(area=area, color=(), button=area, name=name)


# Cards of the Info view, the main fleet then the vanguard
INFO_CARD_LEFTS = (62, 248, 434, 687, 873, 1059)
INFO_ROWS = ('main', 'main', 'main', 'vanguard', 'vanguard', 'vanguard')
INFO_NAMES = [area_button((x + 4, 463, x + 152, 486), f'INFO_NAME_{i}') for i, x in enumerate(INFO_CARD_LEFTS)]
# Levels are right aligned, read without the "Lv." in front of them
INFO_LEVELS = [area_button((x + 116, 156, x + 145, 180), f'INFO_LEVEL_{i}') for i, x in enumerate(INFO_CARD_LEFTS)]
# Where to tap a ship on the 3D view: on her, above the label of her level. By the order of the Info view.
SLOT_TAPS = {
    ('main', 0): (591, 284), ('main', 1): (815, 255), ('main', 2): (370, 325),
    ('vanguard', 0): (509, 481), ('vanguard', 1): (758, 430), ('vanguard', 2): (978, 383),
}
FLEET_FORMATION = area_button((1045, 645, 1240, 690), 'FLEET_FORMATION')

DOCK_SEARCH = area_button((652, 10, 688, 46), 'DOCK_SEARCH')
DOCK_SEARCH_FIELD = area_button((712, 14, 968, 42), 'DOCK_SEARCH_FIELD')
# The magnifier is blue, about (89, 100, 125), and gold, about (186, 143, 92), with the search bar open
SEARCH_ICON = (654, 12, 686, 44)
# OK of the input bar of Android. The bar is white and covers the bottom of the screen while typing.
INPUT_OK = area_button((1165, 635, 1235, 680), 'INPUT_OK')
INPUT_BAR = (300, 600, 1100, 720)
# Name on a card of the dock, from the top left of the card
CARD_NAME = (4, 163, 134, 186)
# Seconds for each step of a search
SEARCH_TIMEOUT = 10


def crop_mean(image, area):
    x1, y1, x2, y2 = area
    return image[y1:y2, x1:x2].reshape(-1, image.shape[-1]).mean(axis=0)


def is_search_open(image):
    """
    Args:
        image (np.ndarray): Screenshot of the dock

    Returns:
        bool: If the search bar of the dock is open, its magnifier lit
    """
    r, g, b = crop_mean(image, SEARCH_ICON)[:3]
    return bool(r > 150 and r - b > 50)


def is_input_shown(image):
    """
    Returns:
        bool: If the input bar of Android is up
    """
    return bool(crop_mean(image, INPUT_BAR).mean() > 240)


def search_term(name):
    """
    What to type in the search bar of the dock to find a ship.
    adb types ASCII letters, a space as %s. A retrofit has the name of the ship she was.

    'Halsey Powell' -> 'Halsey%sPowell', 'London (Retrofit)' -> 'London', 'Königsberg' -> 'nigsberg'
    """
    if name.endswith(RETROFIT):
        name = name[:-len(RETROFIT)]
    parts = [part.strip() for part in re.findall(r'[A-Za-z0-9 .\-]+', name)]
    parts = [part for part in parts if part]
    if not parts:
        return ''
    return max(parts, key=len).replace(' ', '%s')


def card_name_area(button):
    x1, y1, _, _ = button.area
    dx1, dy1, dx2, dy2 = CARD_NAME
    return x1 + dx1, y1 + dy1, x1 + dx2, y1 + dy2


def name_fits(read, name):
    """
    A dock card cuts a long name, "London (Ret..".

    Args:
        read (str): Name read on a card
        name (str): Name of the ship looked for

    Returns:
        bool:
    """
    read, name = name_key(read), name_key(name)
    if len(read) < 3:
        return False
    return name.startswith(read) or read.startswith(name)


class LevelingFleet(Formation):
    def fleet_info_ensure(self, skip_first_screenshot=True):
        """
        Pages:
            in: page_fleet
            out: Info view of the fleet page
        """
        for _ in self.loop(skip_first=skip_first_screenshot):
            if self.appear(FLEET_DETAIL_CHECK, offset=(20, 20)):
                break
            if self.appear(FLEET_CHECK, offset=(20, 20), interval=2):
                self.device.click(FLEET_DETAIL)
                continue

    def fleet_formation_ensure(self, skip_first_screenshot=True):
        """
        Pages:
            in: page_fleet or its Info view
            out: page_fleet, the 3D view
        """
        for _ in self.loop(skip_first=skip_first_screenshot):
            if self.appear(FLEET_CHECK, offset=(20, 20)):
                break
            if self.appear(FLEET_DETAIL_CHECK, offset=(20, 20), interval=2):
                self.device.click(FLEET_FORMATION)
                continue

    def fleet_read_slots(self, fleet):
        """
        Args:
            fleet (int): Fleet shown

        Returns:
            list[Slot]: The 6 slots of the fleet, as the Info view lists them, an empty one without a name

        Pages:
            in: page_fleet
            out: Info view of the fleet page
        """
        self.fleet_info_ensure()
        # Cards are drawn a moment after the view changes, and long names scroll. Read until the levels agree.
        previous = None
        for _ in range(6):
            self.device.screenshot()
            levels = Digit(INFO_LEVELS, letter=(255, 255, 255), threshold=128, name='INFO_LEVEL').ocr(
                self.device.image)
            if levels == previous:
                break
            previous = levels
        names = Ocr(INFO_NAMES, lang='cnocr', letter=(255, 255, 255), threshold=128, name='INFO_NAME').ocr(
            self.device.image)
        slots = [
            Slot(fleet=fleet, row=row, index=index % 3, name=str(name).strip(), level=level)
            for index, (row, name, level) in enumerate(zip(INFO_ROWS, names, levels))
        ]
        logger.attr(f'Fleet {fleet}', ', '.join(f'{slot.name or "-"} Lv.{slot.level}' for slot in slots))
        return slots

    def fleet_slot_enter(self, slot):
        """
        Args:
            slot (Slot):

        Pages:
            in: page_fleet, the 3D view, showing the fleet of the slot
            out: DOCK_CHECK, choosing a ship for the slot
        """
        x, y = SLOT_TAPS[(slot.row, slot.index)]
        button = area_button((x - 10, y - 10, x + 10, y + 10), f'FLEET_SLOT_{slot.row.upper()}_{slot.index + 1}')
        self.ship_info_enter(click_button=button, check_button=DOCK_CHECK, long_click=False,
                             skip_first_screenshot=False)

    def dock_search(self, name):
        """
        Show the ships of a name in the dock.

        Args:
            name (str): Name of a ship

        Returns:
            bool: If typed

        Pages:
            in: DOCK_CHECK
            out: DOCK_CHECK, the ships found
        """
        term = search_term(name)
        if not term:
            logger.warning(f'No letters to search {name} with')
            return False
        logger.info(f'Dock search: {term}')
        click = Timer(2)
        for _ in self.loop(timeout=SEARCH_TIMEOUT):
            if is_search_open(self.device.image):
                break
            if self.appear(DOCK_CHECK, offset=(20, 20)) and (not click.started() or click.reached()):
                self.device.click(DOCK_SEARCH)
                click.start()
                click.reset()
        else:
            logger.warning('The search bar of the dock did not open')
            return False
        click.clear()
        for _ in self.loop(timeout=SEARCH_TIMEOUT):
            if is_input_shown(self.device.image):
                break
            if not click.started() or click.reached():
                self.device.click(DOCK_SEARCH_FIELD)
                click.start()
                click.reset()
        else:
            logger.warning('The input bar did not come up')
            return False
        self.device.adb_shell(['input', 'text', term])
        self.device.sleep(0.5)
        click.clear()
        for _ in self.loop(timeout=SEARCH_TIMEOUT):
            if not is_input_shown(self.device.image):
                if self.appear(DOCK_CHECK, offset=(20, 20)):
                    break
                continue
            if not click.started() or click.reached():
                self.device.click(INPUT_OK)
                click.start()
                click.reset()
        else:
            logger.warning('The input bar did not close')
            return False
        # The cards of the ships found are drawn
        self.device.sleep(0.5)
        self.device.screenshot()
        return True

    def dock_find(self, target):
        """
        Args:
            target (Target): Ship looked for

        Returns:
            Ship: Card of the ship, free, not in a fleet nor on a commission. None if not found.

        Pages:
            in: DOCK_CHECK, the ships found by a search
        """
        scanner = ShipScanner(fleet=0, status='free')
        scanner.disable('emotion', 'rarity')
        ships = scanner.scan(self.device.image, output=False)
        if not ships:
            logger.info(f'No free ship found for {target.ship}')
            return None
        buttons = [area_button(card_name_area(ship.button), f'CARD_NAME_{i}') for i, ship in enumerate(ships)]
        names = Ocr(buttons, lang='cnocr', letter=(255, 255, 255), threshold=128, name='CARD_NAME').ocr(
            self.device.image)
        # OCR of a single button gives its result alone
        if isinstance(names, str):
            names = [names]
        fits = [ship for ship, read in zip(ships, names) if name_fits(read, target.ship)]
        for ship in fits:
            if ship.level == target.level:
                return ship
        if len(fits) == 1:
            logger.info(f'{target.ship} found at level {fits[0].level}, the dock read had {target.level}')
            return fits[0]
        logger.info(f'{target.ship} at level {target.level} not found, cards: {list(zip(names, [s.level for s in ships]))}')
        return None

    def fleet_swap(self, slot, target):
        """
        Put a ship in a slot.

        Args:
            slot (Slot):
            target (Target):

        Returns:
            bool: If she came in. False if she is not free in the dock, the slot stays as it was.

        Pages:
            in: page_fleet, the 3D view, showing the fleet of the slot
            out: page_fleet, the 3D view
        """
        logger.hr(f'Fleet {slot.fleet} {slot.row} {slot.index + 1}', level=2)
        logger.info(f'{slot.name or "Empty slot"} (Lv.{slot.level}) -> {target.ship} (Lv.{target.level})')
        self.fleet_slot_enter(slot)
        ship = None
        if self.dock_search(target.ship):
            ship = self.dock_find(target)
            if ship is None:
                # The dock may not have drawn the ships found yet
                self.device.sleep(1)
                self.device.screenshot()
                ship = self.dock_find(target)
        if ship is None:
            self.ui_back(check_button=FLEET_CHECK)
            return False
        self.dock_select_one(ship.button)
        self.dock_select_confirm(check_button=FLEET_CHECK)
        return True
