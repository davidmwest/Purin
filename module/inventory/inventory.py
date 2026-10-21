import gc
import json
import os
import re
import typing as t
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher

import cv2
import numpy as np

from module.base.button import Button
from module.base.decorator import del_cached_property
from module.base.timer import Timer
from module.base.utils import crop, load_image
from module.equipment.assets import EQUIPMENT_OPEN
from module.inventory.info import InfoReader, NAME_WINDOW, NameScroll
from module.logger import logger
from module.ocr.ocr import OCR_MODEL, DigitCounter
from module.retire.assets import DOCK_AMOUNT, DOCK_CHECK, SHIP_DETAIL_CHECK
from module.retire.dock import Dock
from module.ui.assets import BACK_ARROW
from module.ui.page import page_dock

OCR_DOCK_AMOUNT = DigitCounter(DOCK_AMOUNT, letter=(255, 255, 255), threshold=192, name='INVENTORY_DOCK_AMOUNT')

# Cards of the dock, 7 a row. Rows are found by the dark gap between them, the list scrolls.
CARD_LEFTS = [93 + round((164 + 2 / 3) * column) for column in range(7)]
CARD_WIDTH = 138
CARD_HEIGHT = 204
ROW_DELTA = 227
# The list is below the bar of the dock
LIST_TOP = 60
LIST_BOTTOM = 720
# A line of the gap between rows is dark and plain across the list
GAP_MEAN = 60
GAP_STD = 18
GAP_HEIGHT = 4
# The top of a card is a few pixels below the end of its gap
GAP_TO_CARD = 4
# Rows read on a screen show the upper part of their cards, that tells a row from an other
ROW_TOP_MAX = LIST_BOTTOM - 140
# A card has a portrait, an empty slot at the end of the last row is plain
CARD_STD = 25
# Lock icon on the upper right of the card of a locked ship, the body of the padlock with its keyhole
LOCK_TEMPLATE = './assets/inventory/dock/lock.png'
LOCK_AREA = (106, 30, 138, 60)
LOCK_SIMILARITY = 0.72
# Where a row is drawn, to find it again after a drag: the upper part of the cards
ROW_SIGNATURE = (4, 140)
ROW_SIMILARITY = 0.9
# Where the last row read is dragged to, a slow drag still goes about 25 pixels further
ROW_AFTER_DRAG = LIST_TOP + 60
# A drag slow enough not to fling the list
DRAG_X = 640
DRAG_SPEED = 380
# Docks are not larger than this
DOCK_LIMIT = 5000
# Ships read between two saves of a run not done yet
CHECKPOINT = 50
# A ship of the last full scan that a scan reaching the end of the dock did not read is kept, this many scans in a
# row at most: a card a drag skipped does not drop her, a ship gone from the dock is forgotten after that
MISSED_SCANS_MAX = 1
# A name read differently on two scans, like "Friedrich der GroBe" and "GroSe" (0.94), is this similar at least.
# "Kawakaze" and "Kawakaze META" are 0.80. "Ning Hai" and "Ping Hai" are 0.93, a name read the same way is matched
# first and takes its ship.
NAME_SIMILARITY = 0.9

# Still parts of the Info tab: gear, power, level and stats.
# Not the name, which META ships animate, nor the rest of the row of the level, whose "Awakening" and
# "Boost EXP" are animated, nor the skills, whose icons shine.
SIGNATURE_AREAS = [
    (696, 127, 1112, 205),
    (1145, 226, 1250, 272),
    (752, 286, 798, 318),
    (721, 329, 1240, 501),
]
# A pixel has changed if a channel differs this much
SIGNATURE_PIXEL_DIFF = 60
# Share of changed pixels in each area that a page drawn to its end does not go over,
# the power counts up when a ship arrives
SIGNATURE_STILL = 0.001
# Seconds on a detail page without the Info tab before opening it again
INFO_TAB_LOST = 3
# Seconds for a long name to scroll to its end
NAME_SCROLL_TIMEOUT = 12


def signature(image):
    """
    Returns:
        list[np.ndarray]: Pixels of each still area of the Info tab, shape (n, 3)
    """
    return [crop(image, area).astype(np.int16).reshape(-1, 3) for area in SIGNATURE_AREAS]


def signature_still(before, after):
    for a, b in zip(after, before):
        if (np.abs(a - b).max(axis=1) > SIGNATURE_PIXEL_DIFF).mean() > SIGNATURE_STILL:
            return False
    return True


@dataclass
class DockCard:
    top: int
    column: int
    locked: bool

    @property
    def button(self):
        """
        Returns:
            Button: Area to tap, the part of the card shown on the screen
        """
        left = CARD_LEFTS[self.column]
        area = (left + 20, self.top + 60, left + CARD_WIDTH - 20, min(self.top + CARD_HEIGHT - 20, LIST_BOTTOM - 10))
        return Button(area=area, color=(), button=area, name=f'INVENTORY_CARD_{self.column}')


@dataclass
class DockRow:
    top: int
    cards: t.List[DockCard] = field(default_factory=list)
    signature: np.ndarray = None


def list_area():
    return CARD_LEFTS[0], 0, CARD_LEFTS[-1] + CARD_WIDTH, LIST_BOTTOM


def gap_ends(image):
    """
    Returns:
        list[int]: Last line of each gap between rows of cards, from the top of the list
    """
    gray = cv2.cvtColor(crop(image, list_area()), cv2.COLOR_RGB2GRAY).astype(np.float32)
    gap = (gray.mean(axis=1) < GAP_MEAN) & (gray.std(axis=1) < GAP_STD)
    out = []
    start = None
    for y in range(LIST_TOP, LIST_BOTTOM):
        if gap[y] and start is None:
            start = y
        elif not gap[y] and start is not None:
            if y - start >= GAP_HEIGHT:
                out.append(y - 1)
            start = None
    return out


def row_signature(image, top):
    left, _, right, _ = list_area()
    return cv2.cvtColor(crop(image, (left, top + ROW_SIGNATURE[0], right, top + ROW_SIGNATURE[1])),
                        cv2.COLOR_RGB2GRAY)


def find_row(image, signature):
    """
    Args:
        image (np.ndarray): Screenshot of the dock
        signature (np.ndarray): Of a row read before

    Returns:
        int: Top of the row on the screen, or None if not shown
    """
    gray = cv2.cvtColor(crop(image, list_area()), cv2.COLOR_RGB2GRAY)
    result = cv2.matchTemplate(gray, signature, cv2.TM_CCOEFF_NORMED)
    _, similarity, _, location = cv2.minMaxLoc(result)
    if similarity < ROW_SIMILARITY:
        return None
    return location[1] - ROW_SIGNATURE[0]


class DockReader:
    """
    Reads the cards of the dock shown on the screen.
    """

    def __init__(self):
        self.lock = cv2.cvtColor(load_image(LOCK_TEMPLATE), cv2.COLOR_RGB2GRAY)

    def is_locked(self, image, left, top):
        area = (left + LOCK_AREA[0], top + LOCK_AREA[1], left + LOCK_AREA[2], top + LOCK_AREA[3])
        area = cv2.cvtColor(crop(image, area), cv2.COLOR_RGB2GRAY)
        _, similarity, _, _ = cv2.minMaxLoc(cv2.matchTemplate(area, self.lock, cv2.TM_CCOEFF_NORMED))
        return similarity > LOCK_SIMILARITY

    def rows(self, image):
        """
        Args:
            image (np.ndarray): Screenshot of the dock

        Returns:
            list[DockRow]: Rows with cards shown enough to be read, from the top
        """
        out = []
        for end in gap_ends(image):
            top = end + 1 + GAP_TO_CARD
            if top > ROW_TOP_MAX:
                continue
            row = DockRow(top=top, signature=row_signature(image, top))
            for column, left in enumerate(CARD_LEFTS):
                head = cv2.cvtColor(crop(image, (left, top, left + CARD_WIDTH, top + 30)), cv2.COLOR_RGB2GRAY)
                if head.std() < CARD_STD:
                    continue
                row.cards.append(DockCard(top=top, column=column, locked=self.is_locked(image, left, top)))
            if row.cards:
                out.append(row)
        return out


def filepath_inventory(config_name, partial=False):
    """
    Args:
        config_name (str):
        partial (bool): The file of a run that did not reach the end of the dock

    Returns:
        str: Not ./config/<config name>.<name>.json, the GUI would take it as the config of a mod
    """
    name = f'{config_name}.partial' if partial else config_name
    return f'./config/inventory/{name}.json'


def load_inventory(config_name, partial=False):
    """
    Returns:
        dict: The inventory saved, None if there is none
    """
    try:
        with open(filepath_inventory(config_name, partial=partial), encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def ship_key(name):
    """
    'Ning Hai (Retrofit)' -> 'ninghairetrofit'
    """
    return re.sub(r'[^0-9a-z]', '', str(name).lower())


def carry_missed_ships(ships, previous):
    """
    Ships of the last full scan that a scan reaching the end of the dock did not read, as a drag can skip a card:
    833 cards of 913 were seen once.

    A ship is read again under the same name, the copy closest in level for copies. Then under a name read a little
    differently, the most similar pairs first, with the same hull and a level not lower.

    Args:
        ships (list[dict]): Ships read by the scan
        previous (list[dict]): Ships of the last full scan

    Returns:
        list[dict]: The ships of the last full scan not read, with `missed` the scans in a row that missed them.
            A ship missed more than MISSED_SCANS_MAX times in a row is left out, she most likely left the dock.
    """
    by_key = defaultdict(list)
    for index, ship in enumerate(ships):
        by_key[ship_key(ship.get('name'))].append(index)
    claimed = set()
    unmatched = []
    for old in previous:
        level = old.get('level') or 0
        same = [index for index in by_key.get(ship_key(old.get('name')), []) if index not in claimed]
        if same:
            claimed.add(min(same, key=lambda index: abs((ships[index].get('level') or 0) - level)))
        else:
            unmatched.append(old)

    pairs = []
    for number, old in enumerate(unmatched):
        key = ship_key(old.get('name'))
        for index, ship in enumerate(ships):
            if index in claimed:
                continue
            if old.get('hull') and ship.get('hull') and old['hull'] != ship['hull']:
                continue
            # OCR may read a level one off
            if (ship.get('level') or 0) < (old.get('level') or 0) - 2:
                continue
            similarity = SequenceMatcher(None, key, ship_key(ship.get('name'))).ratio()
            if similarity >= NAME_SIMILARITY:
                pairs.append((similarity, number, index))
    found = set()
    for _, number, index in sorted(pairs, reverse=True):
        if number in found or index in claimed:
            continue
        found.add(number)
        claimed.add(index)

    carried = []
    for number, old in enumerate(unmatched):
        if number in found:
            continue
        missed = int(old.get('missed') or 0) + 1
        if missed > MISSED_SCANS_MAX:
            continue
        carried.append(dict(old, missed=missed))
    return carried


def save_inventory(config_name, data, partial=False):
    """
    Write the inventory at once. A run that did not reach the end of the dock goes to <config name>.partial.json
    and leaves the last whole one as it was.
    """
    file = filepath_inventory(config_name, partial=partial)
    os.makedirs(os.path.dirname(file), exist_ok=True)
    tmp = f'{file}.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
    os.replace(tmp, file)
    if not partial:
        try:
            os.remove(filepath_inventory(config_name, partial=True))
        except FileNotFoundError:
            pass
    logger.info(f'Inventory saved: {file}')


# OCR models cached in module.ocr.models
OCR_MODELS = ['azur_lane', 'azur_lane_jp', 'cnocr', 'jp', 'tw']


def release_ocr_models():
    for model in OCR_MODELS:
        del_cached_property(OCR_MODEL, model)
    # Models in a reference cycle too
    gc.collect()


def ocr_pool_shutdown(pool):
    """
    MXNet aborts the process freeing an OCR model after a thread that used it has ended,
    "mutex lock failed: Invalid argument", and release_resources() frees models before the next task.
    The models are freed on the thread of the OCR while it lives, the next task loads them again.

    Args:
        pool (ThreadPoolExecutor):
    """
    pool.submit(release_ocr_models).result()
    pool.shutdown()


class Inventory(Dock):
    """
    Goes through the cards of the dock, opens the locked ones and keeps what their Info tab shows,
    in ./config/inventory/<config name>.json, for an algorithm to tell which ship to level next.
    """

    def inventory_dock_amount(self):
        """
        Pages:
            in: page_dock

        Returns:
            int: Ships in the dock, or DOCK_LIMIT if unreadable
        """
        current, _, total = OCR_DOCK_AMOUNT.ocr(self.device.image)
        if 0 < current <= total:
            return current
        logger.warning(f'Unexpected dock amount {current}/{total}')
        return DOCK_LIMIT

    def inventory_info_shown(self, lost_timer):
        """
        Args:
            lost_timer (Timer): Started when the detail page is without the Info tab

        Returns:
            bool: If the Info tab of a ship is shown.
                Opens it if the page stays on an other tab.
        """
        if not self.appear(SHIP_DETAIL_CHECK, offset=(20, 20)):
            lost_timer.clear()
            return False
        if self.appear(EQUIPMENT_OPEN, offset=(30, 30)):
            lost_timer.clear()
            return True
        if not lost_timer.started():
            lost_timer.start()
        elif lost_timer.reached():
            logger.info('Ship detail page without the Info tab, open it')
            self.ship_side_navbar_ensure(bottom=1)
            lost_timer.clear()
        return False

    def inventory_wait_info(self, scroll, skip_first_screenshot=True):
        """
        Wait until the Info tab is drawn and stays still.

        Args:
            scroll (NameScroll): Frames of the name, added to
            skip_first_screenshot (bool):

        Returns:
            bool: If drawn, False on timeout

        Pages:
            in: SHIP_DETAIL_CHECK
            out: SHIP_DETAIL_CHECK, Info tab
        """
        timeout = Timer(10, count=20).start()
        lost = Timer(INFO_TAB_LOST)
        previous = None
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                logger.warning('Wait Info tab timeout')
                return False
            if not self.inventory_info_shown(lost):
                previous = None
                continue
            scroll.add(crop(self.device.image, NAME_WINDOW))
            current = signature(self.device.image)
            if previous is not None and signature_still(previous, current):
                return True
            previous = current

    def inventory_name_scroll(self, scroll):
        """
        A long name scrolls in its window, take frames until it is known in full.

        Args:
            scroll (NameScroll): Frames of the name, added to

        Pages:
            in: SHIP_DETAIL_CHECK
        """
        timeout = Timer(NAME_SCROLL_TIMEOUT, count=NAME_SCROLL_TIMEOUT * 2).start()
        while not scroll.done:
            if timeout.reached():
                logger.warning('Name did not scroll to its end')
                return
            self.device.screenshot()
            if not self.appear(SHIP_DETAIL_CHECK, offset=(20, 20)):
                return
            scroll.add(crop(self.device.image, NAME_WINDOW))

    def inventory_open_card(self, card):
        """
        Open the detail page of a card, take its Info tab and come back to the dock, that stays where it was.

        Args:
            card (DockCard):

        Returns:
            tuple[np.ndarray, NameScroll, int]: Screenshot of the Info tab, the name put together from its frames,
                and the tabs of the page, or None if the page could not be taken

        Pages:
            in: page_dock
            out: page_dock
        """
        timeout = Timer(10, count=20).start()
        click_timer = Timer(3)
        while 1:
            self.device.screenshot()
            if self.appear(SHIP_DETAIL_CHECK, offset=(20, 20)):
                break
            if timeout.reached():
                logger.warning(f'Card in column {card.column} at {card.top} did not open')
                return None
            if self.handle_popup_confirm('INVENTORY_CARD'):
                continue
            if self.appear(DOCK_CHECK, offset=(20, 20)) and (not click_timer.started() or click_timer.reached()):
                self.device.click_record_clear()
                self.device.click(card.button)
                click_timer.start()
                click_timer.reset()

        taken = None
        # A long name starts to scroll a moment after the page arrives
        scroll = NameScroll()
        scroll.add(crop(self.device.image, NAME_WINDOW))
        # The power counts up when the page arrives, take frames of the name meanwhile
        arrive = Timer(0.6).start()
        while not arrive.reached():
            self.device.screenshot()
            scroll.add(crop(self.device.image, NAME_WINDOW))
        if self.inventory_wait_info(scroll, skip_first_screenshot=False):
            image, tabs = self.device.image, self._ship_side_navbar.get_total(main=self)
            self.inventory_name_scroll(scroll)
            taken = image, scroll, tabs
        self.device.click(BACK_ARROW)
        if not self.inventory_back_to_dock():
            self.ui_ensure(page_dock)
        return taken

    def inventory_back_to_dock(self):
        """
        Wait for the dock after BACK_ARROW is clicked once on the detail page.
        Clicking again only on the detail page, a click on the dock would leave it.

        Returns:
            bool: If back at the dock

        Pages:
            in: SHIP_DETAIL_CHECK
            out: page_dock
        """
        timeout = Timer(10, count=20).start()
        click_timer = Timer(4).start()
        while 1:
            self.device.screenshot()
            if self.appear(DOCK_CHECK, offset=(20, 20)):
                return True
            if timeout.reached():
                logger.warning('Did not come back to the dock')
                return False
            if click_timer.reached() and self.appear(SHIP_DETAIL_CHECK, offset=(20, 20)):
                self.device.click(BACK_ARROW)
                click_timer.reset()

    @staticmethod
    def inventory_collect(reading, ships, amount, wait=False):
        """
        Add the ships whose OCR is done, in their order.

        Args:
            reading (list[tuple[int, int, Future]]): (index, tabs, OCR), taken out once done
            ships (list[dict]):
            amount (int): Ships in the dock
            wait (bool): Wait for every OCR

        Returns:
            int: Ships that could not be read
        """
        failed = 0
        while reading and (wait or reading[0][2].done()):
            index, tabs, future = reading.pop(0)
            try:
                ship = future.result()
            except Exception as e:
                logger.exception(e)
                failed += 1
                continue
            ship['index'] = index
            ship['tabs'] = tabs
            ships.append(ship)
            logger.info(f'Inventory {index}/{amount}: {ship["hull"]} {ship["name"]}, Lv.{ship["level"]}, '
                        f'power {ship["power"]}, {ship["stars"]} stars, {ship["affinity"]}')
        return failed

    def inventory_drag(self, distance):
        """
        Drag the list up by a distance, slow enough not to fling it.

        Args:
            distance (int): Pixels
        """
        distance = int(min(max(distance, 20), LIST_BOTTOM - LIST_TOP - 120))
        start_y = LIST_BOTTOM - 60
        duration = max(distance / DRAG_SPEED, 0.5)
        # Drags follow one another through the ships not locked, that are not opened
        self.device.click_record_clear()
        self.device.swipe((DRAG_X, start_y), (DRAG_X, start_y - distance), duration=(duration, duration + 0.05),
                          name='INVENTORY_DRAG')
        # The list settles
        self.device.sleep(0.8)

    def inventory_drag_back(self):
        self.device.click_record_clear()
        self.device.swipe((DRAG_X, LIST_TOP + 80), (DRAG_X, LIST_TOP + 80 + ROW_DELTA),
                          duration=(0.8, 0.85), name='INVENTORY_DRAG_BACK')
        self.device.sleep(0.8)

    def inventory_list_top(self):
        """
        Scroll the dock back to its first row, with its scroll bar.

        Pages:
            in: page_dock
        """
        for _ in range(3):
            self.device.click_record_clear()
            self.device.swipe((1243, 400), (1243, 60), duration=(0.5, 0.55), name='INVENTORY_SCROLL_TOP')
            self.device.sleep(1)
            self.device.screenshot()
            ends = gap_ends(self.device.image)
            # The first row is right below the bar of the dock
            if ends and ends[0] < LIST_TOP + 20:
                return
        logger.warning('Dock may not be at its first row')

    def inventory_scan(self):
        """
        Returns:
            dict: The inventory

        Pages:
            in: Any page
            out: page_dock
        """
        start = datetime.now().replace(microsecond=0)
        self.ui_ensure(page_dock)
        # Every ship, the ones in fleets first, then by level
        self.dock_favourite_set(False)
        self.dock_filter_set(sort='level', index='all', faction='all', rarity='all', extra='no_limit')
        self.dock_sort_method_dsc_set(True)
        self.device.screenshot()
        amount = self.inventory_dock_amount()
        logger.attr('Dock amount', amount)
        self.inventory_list_top()

        dock_reader = DockReader()
        reader = InfoReader()
        # The OCR of a ship runs while the game opens the next one. A single thread does every OCR of the scan.
        pool = ThreadPoolExecutor(max_workers=1)
        reading = []
        ships = []
        seen = 0
        rows_done = 0
        unread = 0
        last = None
        lost = 0
        end = 'dock'
        checkpoint = CHECKPOINT
        try:
            while rows_done * 7 < DOCK_LIMIT:
                self.device.screenshot()
                image = self.device.image
                rows = dock_reader.rows(image)
                if last is not None:
                    last_top = find_row(image, last)
                    if last_top is None:
                        # Dragged too far, the last row read is above the screen
                        lost += 1
                        if lost > 3:
                            logger.warning('Last row read is not found, stop')
                            end = 'lost'
                            break
                        logger.warning('Last row read is not on the screen, drag back')
                        self.inventory_drag_back()
                        continue
                    rows = [row for row in rows if row.top > last_top + ROW_DELTA // 2]
                if not rows:
                    # The end of the dock, or a drag that did nothing
                    lost += 1
                    if seen >= amount or lost > 3:
                        break
                    self.inventory_drag(ROW_DELTA * 2)
                    continue
                lost = 0

                for row in rows:
                    rows_done += 1
                    for card in row.cards:
                        seen += 1
                        if not card.locked:
                            continue
                        self.device.stuck_record_clear()
                        taken = self.inventory_open_card(card)
                        if taken is None:
                            unread += 1
                            continue
                        image, name, tabs = taken
                        index = (rows_done - 1) * 7 + card.column + 1
                        reading.append((index, tabs, pool.submit(reader.read, image, name)))
                        unread += self.inventory_collect(reading, ships, amount)
                    last = row.signature
                if len(ships) >= checkpoint:
                    checkpoint += CHECKPOINT
                    save_inventory(self.config.config_name,
                                   self.inventory_result(start, amount, seen, 'running', unread, ships), partial=True)
                # The last row read up below the bar of the dock, with room for a drag that goes a bit further
                self.inventory_drag(rows[-1].top - ROW_AFTER_DRAG)
        except Exception:
            # Keep what was read, the next run starts again from the first ship
            unread += self.inventory_collect(reading, ships, amount, wait=True)
            ocr_pool_shutdown(pool)
            save_inventory(self.config.config_name,
                           self.inventory_result(start, amount, seen, 'error', unread, ships), partial=True)
            raise

        unread += self.inventory_collect(reading, ships, amount, wait=True)
        ocr_pool_shutdown(pool)
        logger.info(f'Inventory ended at {end}, {seen} ships seen of {amount}')
        if unread:
            logger.warning(f'{unread} ships could not be read')
        self.inventory_list_top()
        return self.inventory_result(start, amount, seen, end, unread, ships)

    @staticmethod
    def inventory_result(start, amount, seen, end, unread, ships):
        """
        Args:
            start (datetime): When the scan started
            amount (int): Ships in the dock
            seen (int): Cards gone through
            end (str): Why the scan ended, "dock" at the end of the dock, "lost", or "running" and "error" if not
            unread (int): Locked ships that could not be read
            ships (list[dict]):

        Returns:
            dict: The inventory
        """
        return {
            'time': start.isoformat(sep=' '),
            'duration': int((datetime.now() - start).total_seconds()),
            'dock_amount': amount,
            'seen': seen,
            'end': end,
            'complete': end == 'dock' and seen >= amount and not unread,
            'unread': unread,
            'locked': len(ships),
            'ships': ships,
        }

    def run(self):
        """
        Pages:
            in: Any page
            out: page_dock
        """
        data = self.inventory_scan()
        logger.info(f'Inventory: {data["locked"]} locked ships of {data["seen"]} seen, in {data["duration"]}s')
        # A scan that got to the end of the dock is whole, though a drag may have skipped a card or the count of the
        # dock was read wrong. The ships it did not read keep what the last full scan read of them.
        reached = data['end'] == 'dock'
        if reached:
            previous = load_inventory(self.config.config_name) or {}
            carried = carry_missed_ships(data['ships'], previous.get('ships') or [])
            if data['seen'] < data['dock_amount']:
                logger.info(f'{data["seen"]} cards seen of {data["dock_amount"]}, the scan is taken as whole')
            if carried:
                logger.info(f'Ships of the last full scan not read this time are kept: '
                            f'{[ship.get("name") for ship in carried]}')
            data['ships'] = data['ships'] + carried
            data['carried'] = len(carried)
        if data['seen']:
            save_inventory(self.config.config_name, data, partial=not reached)
        if reached:
            self.config.task_delay(success=True)
        else:
            logger.warning('Inventory did not reach the end of the dock, saved as partial and tried again later')
            self.config.task_delay(success=False)
