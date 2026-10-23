"""
Fill the empty slots of the fleets that the campaign uses with the best free ships of the dock.

A new account starts with a few ships in its first fleet, and the game does not add the ships that drop or
are built, so the fleet stays short of ships while the dock fills up.
"""
import os
from datetime import datetime

import cv2
import numpy as np

from module.base.button import Button
from module.base.decorator import cached_property
from module.base.timer import Timer
from module.base.utils import crop, load_image, save_image
from module.dock.census import GRID_SHIFT, card_level, card_rarity, card_status
from module.equipment.assets import FLEET_NEXT, FLEET_PREV, OCR_FLEET_INDEX
from module.exception import ScriptError
from module.inventory.inventory import CARD_HEIGHT, CARD_LEFTS, CARD_WIDTH
from module.logger import logger
from module.map_detection.utils import Points
from module.ocr.ocr import Digit
from module.retire.assets import DOCK_CHECK, SHIP_CONFIRM
from module.retire.dock import Dock, OCR_DOCK_SELECTED
from module.retire.dock_walk import DockWalker
from module.retire.scanner import Ship, ShipScanner, StatusScanner
from module.ui.assets import BACK_ARROW
from module.ui.page import page_fleet

FLEET_INDEX = Digit(OCR_FLEET_INDEX, letter=(90, 154, 255), threshold=128, alphabet='123456')
# The word "Add" over the next empty slot of each row of the fleet page, white letters
ADD_TEMPLATE = './assets/formation/add.png'
ADD_SIMILARITY = 0.7
# Rows of the fleet page are drawn in perspective, along a row y + 0.2 * x stays about the same:
# about 370 for the main fleet, about 540 for the vanguard
ROW_SLOPE = 0.2
ROW_BOUNDARY = 455
# Seconds to look for an "Add" on the fleet page, the bubbles show a moment after the page comes back from the dock
ADD_WAIT = 2
# A ship of a higher rarity grows stronger, she is worth a few levels more
RARITY_BONUS = {'common': 0, 'rare': 5, 'elite': 10, 'super_rare': 20, 'ultra': 25}
# The picker covers the bottom of the dock with Selected, Cancel and Confirm. Never read a hidden busy banner.
PICKER_LIST_BOTTOM = 630
CENSUS_RARITIES = {'Normal': 'common', 'Rare': 'rare', 'Elite': 'elite', 'Super Rare': 'super_rare'}
PICKER_WAIT = 12
PICKER_MAX_CLICKS = 3


def letters(image):
    """
    Args:
        image (np.ndarray): Screenshot

    Returns:
        np.ndarray: White letters, 255 on 0
    """
    image = image.astype(np.int16)
    white = (image.min(axis=2) > 200) & (image.max(axis=2) - image.min(axis=2) < 40)
    return white.astype(np.uint8) * 255


def row_of(point):
    """
    Args:
        point (tuple[int, int]): Middle of an "Add" on the fleet page

    Returns:
        str: 'main' or 'vanguard', as the dock filter names them
    """
    x, y = point
    return 'main' if y + ROW_SLOPE * x < ROW_BOUNDARY else 'vanguard'


def find_adds(image, template):
    """
    Args:
        image (np.ndarray): Screenshot of the fleet page
        template (np.ndarray): White letters of "Add"

    Returns:
        list[tuple[int, int]]: Middle of each "Add", the main fleet first
    """
    result = cv2.matchTemplate(letters(image), template, cv2.TM_CCOEFF_NORMED)
    points = np.array(np.where(result > ADD_SIMILARITY)).T[:, ::-1]
    if not len(points):
        return []
    height, width = template.shape
    points = Points(points).group(threshold=10)
    middles = [(int(x + width // 2), int(y + height // 2)) for x, y in points]
    return sorted(middles, key=lambda point: (row_of(point) != 'main', point[0]))


def ship_score(ship):
    """
    Args:
        ship (Ship): Of the dock

    Returns:
        int: The higher, the better a ship to come in
    """
    return ship.level + RARITY_BONUS.get(ship.rarity, 0)


class Formation(Dock):
    """
    Pages:
        in: Any page
        out: page_fleet
    """

    @cached_property
    def add_template(self):
        image = load_image(ADD_TEMPLATE)
        if image.ndim == 3:
            image = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
        return image

    def formation_fleets(self):
        """
        Returns:
            list[int]: Fleets that the campaign uses, those of the task Main
        """
        fleets = [self.config.cross_get('Main.Fleet.Fleet1', default=1),
                  self.config.cross_get('Main.Fleet.Fleet2', default=0)]
        return [fleet for fleet in dict.fromkeys(fleets) if fleet]

    def formation_goto_fleet(self, fleet):
        """
        Args:
            fleet (int): 1 to 6

        Pages:
            in: page_fleet
            out: page_fleet, showing the fleet
        """
        retry = Timer(1, count=2)
        for _ in self.loop():
            current = FLEET_INDEX.ocr(self.device.image)
            if current == 0:
                continue
            diff = fleet - current
            if diff == 0:
                break
            if retry.reached():
                button = FLEET_NEXT if diff > 0 else FLEET_PREV
                self.device.multi_click(button, n=abs(diff), interval=(0.2, 0.3))
                retry.reset()

    def formation_candidates(self, row):
        """
        Args:
            row (str): 'main' or 'vanguard'

        Returns:
            list[Ship]: Ships free to come in

        Pages:
            in: DOCK_CHECK, choosing a ship for a slot
        """
        self.dock_favourite_set(False, wait_loading=False)
        self.dock_sort_method_dsc_set(True, wait_loading=False)
        self.dock_filter_set(sort='level', index=row)
        scanner = ShipScanner(fleet=0, status='free')
        scanner.disable('emotion')
        candidates = scanner.scan(self.device.image, output=False)
        free = []
        for ship in candidates:
            if ship.button.area[3] > PICKER_LIST_BOTTOM:
                continue
            # On scaled 1440p screenshots the status template misses "In action". Keep the scanner's fleet and
            # hard/event exclusions, and also reject the green commission and red battle banners by colour.
            column = CARD_LEFTS.index(ship.button.area[0])
            status = card_status(self.device.image, column, ship.button.area[1])
            if status == 'free':
                free.append(ship)
            else:
                logger.info(f'Skip {ship.button}: {status}')
        if free:
            return free
        return self.formation_more_candidates()

    def formation_more_candidates(self):
        """Find a free ship below the first page, in descending level order, with her whole card visible."""
        logger.info('No free ship on the first page, look further down the dock')
        walker = DockWalker(self, list_bottom=PICKER_LIST_BOTTOM, card_read_bottom=CARD_HEIGHT)
        status_scanner = StatusScanner()
        for card, fleet in walker.cards():
            image = self.device.image
            if fleet or card_status(image, card.column, card.top) != 'free':
                continue
            left, top = CARD_LEFTS[card.column], card.top + GRID_SHIFT
            # Keep the hard-mode and event-lineup exclusions used by the first-page scanner.
            area = (left, top, left + CARD_WIDTH, top + CARD_HEIGHT)
            if status_scanner._match(crop(image, area)) != 'free':
                continue
            level = card_level(image, card.column, card.top)
            if not level:
                continue
            rarity = CENSUS_RARITIES.get(card_rarity(image, card.column, card.top), 'unknown')
            # Stop the walk before it scrolls: this card's button is valid only on the current screen.
            return [Ship(rarity=rarity, level=level, fleet=0, status='free', button=card.button)]
        return []

    def formation_save_failure(self, tag):
        logger.warning(f'Formation picker failed: {tag}')
        try:
            directory = './log/formation'
            os.makedirs(directory, exist_ok=True)
            path = os.path.join(directory, f'{datetime.now().strftime("%Y-%m-%d_%H-%M-%S-%f")}_{tag}.png')
            save_image(self.device.image, path)
            logger.info(f'Screen saved: {path}')
            for old in sorted(name for name in os.listdir(directory) if name.endswith('.png'))[:-20]:
                os.remove(os.path.join(directory, old))
        except Exception as e:
            logger.warning(f'Formation screen not saved: {e}')

    def formation_picker_click(self, button, selecting):
        """Bound selection and confirmation; never approve an unexpected fleet-transfer popup."""
        click = Timer(3)
        clicks = 0
        for _ in self.loop(timeout=PICKER_WAIT):
            if not selecting and self.ui_process_check_button(page_fleet.check_button):
                return True
            if self.handle_popup_cancel('FORMATION_PICKER'):
                self.formation_save_failure('unexpected_popup')
                return False
            current, _, total = OCR_DOCK_SELECTED.ocr(self.device.image)
            if selecting and current == total == 1:
                return True
            # Unreadable counters cannot authorize either another card tap or confirmation.
            ready = (current == 0 and total == 1) if selecting else (current == total == 1)
            check = DOCK_CHECK if selecting else SHIP_CONFIRM
            offset = (20, 20) if selecting else (200, 50)
            if ready and clicks < PICKER_MAX_CLICKS and self.appear(check, offset=offset) and click.reached():
                self.device.click(button)
                clicks += 1
                click.reset()
        self.formation_save_failure('selection_timeout' if selecting else 'confirmation_timeout')
        return False

    def formation_cancel_picker(self):
        click = Timer(3)
        clicks = 0
        for _ in self.loop(timeout=PICKER_WAIT):
            if self.ui_process_check_button(page_fleet.check_button):
                return
            if self.handle_popup_cancel('FORMATION_CANCEL'):
                continue
            if clicks < PICKER_MAX_CLICKS and self.appear(DOCK_CHECK, offset=(20, 20)) and click.reached():
                self.device.click(BACK_ARROW)
                clicks += 1
                click.reset()
        self.formation_save_failure('cancel_timeout')
        raise ScriptError('Formation could not leave the ship picker safely')

    def formation_add(self, point):
        """
        Fill the empty slot under an "Add".

        Args:
            point (tuple[int, int]): Middle of the "Add"

        Returns:
            bool: If a ship came in, False if no ship is free for that row

        Pages:
            in: page_fleet
            out: page_fleet
        """
        row = row_of(point)
        x, y = point
        button = Button(area=(x - 20, y - 12, x + 20, y + 12), color=(), button=(x - 20, y - 12, x + 20, y + 12),
                        name=f'FORMATION_ADD_{row.upper()}')
        self.ship_info_enter(click_button=button, check_button=DOCK_CHECK, long_click=False,
                             skip_first_screenshot=False)
        candidates = self.formation_candidates(row)
        if not candidates:
            logger.info(f'No free ship for the {row}')
            self.dock_reset()
            self.formation_cancel_picker()
            return False

        ship = max(candidates, key=ship_score)
        logger.info(f'Add a {ship.rarity} ship at level {ship.level} to the {row}')
        if not self.formation_picker_click(ship.button, selecting=True):
            self.formation_cancel_picker()
            return False
        self.dock_reset()
        if self.formation_picker_click(SHIP_CONFIRM, selecting=False):
            return True
        self.formation_cancel_picker()
        return False

    def formation_adds(self, full):
        """
        Args:
            full (set[str]): Rows that no free ship can go to

        Returns:
            list[tuple[int, int]]: Middle of each "Add" of the other rows

        Pages:
            in: page_fleet
        """
        timeout = Timer(ADD_WAIT, count=4).start()
        for _ in self.loop(skip_first=False):
            adds = [point for point in find_adds(self.device.image, self.add_template) if row_of(point) not in full]
            if adds or timeout.reached():
                return adds

    def formation_fill(self):
        """
        Fill the empty slots of the fleet shown.

        Returns:
            int: Ships that came in

        Pages:
            in: page_fleet
            out: page_fleet
        """
        added = 0
        full = set()
        # A fleet has 6 slots
        for _ in range(6):
            adds = self.formation_adds(full)
            logger.attr('Empty slots', [row_of(point) for point in adds])
            if not adds:
                break
            point = adds[0]
            if self.formation_add(point):
                added += 1
            else:
                full.add(row_of(point))
        return added

    def run(self):
        self.ui_ensure(page_fleet)
        added = 0
        for fleet in self.formation_fleets():
            logger.hr(f'Formation fleet {fleet}', level=1)
            self.formation_goto_fleet(fleet)
            added += self.formation_fill()
        logger.info(f'Formation: {added} ships added')
        self.config.task_delay(success=True)
