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
from dataclasses import replace

import numpy as np

from module.base.button import Button
from module.base.timer import Timer
from module.equipment.assets import EQUIPMENT_OPEN, FLEET_DETAIL, FLEET_DETAIL_CHECK
from module.formation.formation import Formation
from module.leveling.plan import FARM_LEVEL_MAX, LEVEL_MAX, RETROFIT, ShipMatcher, Slot, load_ship_data, name_key
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
# A tap on a card opens the detail page of her ship
INFO_CARDS = [area_button((x + 30, 230, x + 125, 400), f'INFO_CARD_{i}') for i, x in enumerate(INFO_CARD_LEFTS)]
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
DOCK_TITLE = area_button((125, 15, 180, 40), 'DOCK_TITLE')
# The magnifier is blue, about (89, 100, 125), and gold, about (186, 143, 92), with the search bar open
SEARCH_ICON = (654, 12, 686, 44)
# OK of the input bar of Android. The bar is white and covers the bottom of the screen while typing.
# Its size goes by the density of the screen: y 600 to 720 at 1280x720, the bottom 60 only at 2560x1440 brought to
# 1280x720, with OK at about (1200, 657) and (1240, 688). The F2P account never saw the bar come up.
INPUT_OK = area_button((1165, 635, 1235, 680), 'INPUT_OK')
INPUT_BAR = (300, 662, 1100, 718)
# The dark letters of OK, at the right end of the bar
INPUT_OK_SEARCH = (1100, 600, 1280, 720)
INPUT_OK_DARK = 100
# Name on a card of the dock, from the top left of the card
CARD_NAME = (4, 163, 134, 186)
# Seconds for each step of a search
SEARCH_TIMEOUT = 10
# Names of the Info view are read this many times at most, this far apart, long ones scroll
NAME_READS = 3
NAME_READ_INTERVAL = 0.7


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


def input_ok(image):
    """
    Args:
        image (np.ndarray): Screenshot with the input bar of Android up

    Returns:
        Button: OK of the input bar, where its letters are
    """
    x1, y1, x2, y2 = INPUT_OK_SEARCH
    # The bar is the white rows at the bottom, the dock above it is dark too
    bar_x1, _, bar_x2, _ = INPUT_BAR
    rows = image[y1:y2, bar_x1:bar_x2].mean(axis=(1, 2))
    top = y2
    for y in range(y2 - y1 - 1, -1, -1):
        if rows[y] <= 240:
            break
        top = y1 + y
    gray = image[top:y2, x1:x2].mean(axis=2)
    ys, xs = np.where(gray < INPUT_OK_DARK)
    if not len(xs):
        return INPUT_OK
    x, y = int(xs.mean()) + x1, int(ys.mean()) + top
    return area_button((x - 15, y - 8, x + 15, y + 8), 'INPUT_OK')


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


# What the OCR reads for the ".." of a name cut on a card: "..", ".", ".x", ".u", ":", "…"
CUT_MARK = re.compile(r'\s*[.:\u2026]+[A-Za-z]?$')


def name_fits(read, name):
    """
    A dock card cuts a long name, "London (Ret..", its dots read as ".x" at times: "Vittorio Cu.x", 2026-10-21.

    Args:
        read (str): Name read on a card
        name (str): Name of the ship looked for

    Returns:
        bool:
    """
    read = CUT_MARK.sub('', str(read).strip())
    read, name = name_key(read), name_key(name)
    if len(read) < 3:
        return False
    # "Vampire MET.." is not Vampire
    return name.startswith(read)


def card_level(level):
    """
    Args:
        level (int): Level read on a dock card

    Returns:
        int: The level. A read over LEVEL_MAX is the level and a stray digit: 1011 for Dmitri Donskoi at 101,
            2026-10-21, its first digits that make a level are taken. 0 if it can not be read.
    """
    if not level or level < 1:
        return 0
    text = str(int(level))
    while len(text) > 1 and int(text) > LEVEL_MAX:
        text = text[:-1]
    level = int(text)
    return level if 1 <= level <= LEVEL_MAX else 0


def choose_pull(slots, level):
    """
    The ship a commission takes from a fleet that farms, when no free ship has the level it asks for.

    Args:
        slots (list[Slot]): The 6 slots of the fleet
        level (int): Level the commission asks a ship for

    Returns:
        Slot: The highest ship at that level or above, she is the nearest to her level cap, or None.
            Never the flagship, never the last ship of the vanguard: the fleet still sorties.
    """
    ships = [slot for slot in slots if slot.name and slot.level]
    vanguard = [slot for slot in ships if slot.row == 'vanguard']
    candidates = []
    for slot in ships:
        if slot.level < level:
            continue
        if slot.row == 'main' and slot.index == 0:
            continue
        if slot.row == 'vanguard' and len(vanguard) <= 1:
            continue
        candidates.append(slot)
    if not candidates:
        return None
    return max(candidates, key=lambda slot: slot.level)


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
        # Cards are drawn a moment after the view changes. Read until the levels agree.
        previous = None
        for _ in range(6):
            self.device.screenshot()
            levels = Digit(INFO_LEVELS, letter=(255, 255, 255), threshold=128, name='INFO_LEVEL').ocr(
                self.device.image)
            if levels == previous:
                break
            previous = levels
        # A long name scrolls in its banner and reads cut, "ing Hai Retrofit". Keep the read closest to a ship.
        matcher = ShipMatcher(load_ship_data())
        names, scores = [''] * len(INFO_NAMES), [-1.] * len(INFO_NAMES)
        for read in range(NAME_READS):
            if read:
                self.device.sleep(NAME_READ_INTERVAL)
                self.device.screenshot()
            reads = Ocr(INFO_NAMES, lang='cnocr', letter=(255, 255, 255), threshold=128, name='INFO_NAME').ocr(
                self.device.image)
            for index, name in enumerate(reads):
                name = str(name).strip()
                score = matcher.match_score(name)[1] if name else 0.
                if score > scores[index]:
                    names[index], scores[index] = name, score
            if all(score == 1. or not name for name, score in zip(names, scores)):
                break
        slots = [
            Slot(fleet=fleet, row=row, index=index % 3, name=str(name).strip(), level=level)
            for index, (row, name, level) in enumerate(zip(INFO_ROWS, names, levels))
        ]
        logger.attr(f'Fleet {fleet}', ', '.join(f'{slot.name or "-"} Lv.{slot.level}' for slot in slots))
        return slots

    def fleet_ship_enter(self, slot):
        """
        Open the detail page of the ship of a slot, from the Info view. ui_back(check_button=FLEET_DETAIL_CHECK)
        comes back.

        Args:
            slot (Slot):

        Pages:
            in: page_fleet, showing the fleet of the slot
            out: detail page of her ship, the Info tab
        """
        self.fleet_info_ensure()
        index = (0 if slot.row == 'main' else 3) + slot.index
        self.ship_info_enter(click_button=INFO_CARDS[index], check_button=EQUIPMENT_OPEN, long_click=False,
                             skip_first_screenshot=False)

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
                self.device.click(input_ok(self.device.image))
                click.start()
                click.reset()
        else:
            logger.warning('The input bar did not close')
            return False
        # Searches typed before are listed under the bar and take the next tap, a tap on the title closes them
        self.device.click(DOCK_TITLE)
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
        # A level read out of range is kept, see card_level()
        scanner.set_limitation(level=None)
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
        fits = []
        for ship, read in zip(ships, names):
            if not name_fits(read, target.ship):
                continue
            level = card_level(ship.level)
            if level != ship.level:
                logger.info(f'Level of a card of {target.ship} read {ship.level}, '
                            + (f'taken as {level}' if level else 'not a level'))
                # Ship is frozen
                ship = replace(ship, level=level)
            fits.append(ship)
        for ship in fits:
            if ship.level == target.level:
                return ship
        if len(fits) == 1:
            # A level not read can not tell her from an other copy: not taken, the next ship is tried
            if not fits[0].level:
                logger.warning(f'{target.ship} found, her level can not be read: not taken')
                return None
            # A ship goes up in level after the dock read, never down. One below it is an other copy, a level 1
            # duplicate when the copy looked for is on a commission: taken, it came in as the geared level 110 one.
            if fits[0].level < target.level:
                logger.warning(f'{target.ship} found at level {fits[0].level} only, the dock read had '
                               f'{target.level}: an other copy, not taken')
                return None
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
        if ship is not None and ship.level >= FARM_LEVEL_MAX:
            # The fleets that farm hold ships under 120, one that went up to it since the dock read stays out
            logger.warning(f'{target.ship} is at level {ship.level} now, the fleets that farm hold ships '
                           f'under {FARM_LEVEL_MAX}')
            ship = None
        if ship is None:
            self.ui_back(check_button=FLEET_CHECK)
            return False
        self.dock_select_one(ship.button)
        self.dock_select_confirm(check_button=FLEET_CHECK)
        # She comes in at her level now, the fleet's average and the records follow it
        target.level = ship.level
        return True

    def fleet_remove(self, slot):
        """
        Take the ship of a slot out of her fleet: the dock of the slot starts with a REMOVE card.

        Args:
            slot (Slot):

        Returns:
            bool: If the slot is empty now

        Pages:
            in: page_fleet, the 3D view, showing the fleet of the slot
            out: page_fleet, the 3D view
        """
        from module.retire.dock import CARD_GRIDS
        logger.info(f'Remove {slot.name} (Lv.{slot.level}) from fleet {slot.fleet} {slot.row} {slot.index + 1}')
        self.fleet_slot_enter(slot)
        self.device.click(CARD_GRIDS[(0, 0)])
        timeout = Timer(15).start()
        for _ in self.loop():
            if self.appear(FLEET_CHECK, offset=(20, 20)):
                return True
            if timeout.reached():
                logger.warning(f'{slot.name} not removed')
                break
            if self.handle_popup_confirm('FLEET_REMOVE'):
                continue
            if self.appear(DOCK_CHECK, offset=(20, 20), interval=3):
                from module.retire.assets import SHIP_CONFIRM
                if self.appear_then_click(SHIP_CONFIRM, offset=(200, 50), interval=3):
                    continue
        self.ui_back(check_button=FLEET_CHECK)
        return False

    def fleet_pull_ship(self, level):
        """
        Free a ship of the fleets that farm for a commission.

        Args:
            level (int): Level the commission asks a ship for

        Returns:
            Slot: Her slot as it was, None if no ship of the fleets can leave

        Pages:
            in: Any page
            out: page_fleet, the 3D view
        """
        from module.ui.page import page_fleet
        for index in self.formation_fleets():
            self.ui_ensure(page_fleet)
            self.formation_goto_fleet(index)
            slots = self.fleet_read_slots(index)
            slot = choose_pull(slots, level)
            self.fleet_formation_ensure()
            if slot is None:
                continue
            if self.fleet_remove(slot):
                return slot
        return None
