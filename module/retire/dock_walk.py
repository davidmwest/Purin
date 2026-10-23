"""
Walk the cards of the dock from its top, row by row, past its first screen.

The cards of the first screen line up with CARD_GRIDS, 7 by 2. Further down they are found where a drag left them,
by the dark gap above each row, as the inventory reads the dock: a row is told from the ones before it by its
signature, the strip of its card heads, so no card is counted twice.
"""
import cv2

from module.base.utils import crop
from module.exception import RequestHumanTakeover
from module.inventory.inventory import (DRAG_SPEED, DRAG_X, LIST_BOTTOM, LIST_TOP, ROW_AFTER_DRAG, ROW_DELTA,
                                        CARD_LEFTS, DockReader, find_row, gap_ends)
from module.logger import logger
from module.retire.scanner import FleetScanner

# The fleet badge of a card, from its top left: a card is read once the badge is on the screen
FLEET_AREA = (0, 117, 35, 162)
CARD_READ_BOTTOM = FLEET_AREA[3] + 3
# Drags that bring no new row before the dock is taken as ended
END_DRAGS = 3


class DockWalker:
    def __init__(self, main, list_bottom=LIST_BOTTOM, card_read_bottom=CARD_READ_BOTTOM, max_screens=None):
        """
        Args:
            main (ModuleBase): With its device on page_dock
            list_bottom (int): Bottom of the visible card list, above a picker's Confirm bar
            card_read_bottom (int): Height of a card that must be visible before yielding it
            max_screens (int | None): Optional loop budget, including screens that yield no cards
        """
        self.main = main
        self.list_bottom = list_bottom
        self.card_read_bottom = card_read_bottom
        self.max_screens = max_screens
        self.reader = DockReader()
        self.fleet_scanner = FleetScanner()

    @property
    def device(self):
        return self.main.device

    def list_top(self):
        """
        Scroll the dock back to its first row, with its scroll bar.
        """
        for _ in range(3):
            self.device.click_record_clear()
            self.device.swipe((1243, 400), (1243, 60), duration=(0.5, 0.55), name='DOCK_WALK_TOP')
            self.device.sleep(1)
            self.device.screenshot()
            ends = gap_ends(self.device.image)
            # The first row is right below the bar of the dock
            if ends and ends[0] < LIST_TOP + 20:
                return
        logger.warning('Dock may not be at its first row')

    def drag(self, distance):
        """
        Drag the list up by a distance, slow enough not to fling it.
        """
        distance = int(min(max(distance, 20), self.list_bottom - LIST_TOP - 120))
        start_y = self.list_bottom - 60
        duration = max(distance / DRAG_SPEED, 0.5)
        self.device.click_record_clear()
        self.device.swipe((DRAG_X, start_y), (DRAG_X, start_y - distance), duration=(duration, duration + 0.05),
                          name='DOCK_WALK_DRAG')
        self.device.sleep(0.8)

    def drag_back(self):
        self.device.click_record_clear()
        self.device.swipe((DRAG_X, LIST_TOP + 80), (DRAG_X, LIST_TOP + 80 + ROW_DELTA),
                          duration=(0.8, 0.85), name='DOCK_WALK_DRAG_BACK')
        self.device.sleep(0.8)

    def fleet_of(self, image, card):
        """
        Returns:
            int: Fleet of the card, 0 if in none
        """
        left = CARD_LEFTS[card.column]
        x1, y1, x2, y2 = FLEET_AREA
        badge = crop(image, (left + x1, card.top + y1, left + x2, card.top + y2))
        return self.fleet_scanner._match(self.fleet_scanner.pre_process(badge))

    def cards(self):
        """
        Yields:
            tuple[DockCard, int]: Each card of the dock from its top, with its fleet. Its button is on the screen
                until the walk goes on: a caller that taps it stops the walk.
        """
        self.list_top()
        last = None
        lost = 0
        screens = 0
        while 1:
            if self.max_screens is not None and screens >= self.max_screens:
                raise RequestHumanTakeover('Dock walk exceeded its screenshot budget')
            screens += 1
            self.device.screenshot()
            image = self.device.image
            rows = [row for row in self.reader.rows(image)
                    if row.top + self.card_read_bottom <= self.list_bottom]
            if last is not None:
                last_top = find_row(image, last)
                if last_top is None:
                    # Dragged too far, the last row read is above the screen
                    lost += 1
                    if lost > END_DRAGS:
                        logger.warning('Last row read is not found, the walk of the dock stops')
                        return
                    self.drag_back()
                    continue
                rows = [row for row in rows if row.top > last_top + ROW_DELTA // 2]
            if not rows:
                # The end of the dock, or a drag that did nothing
                lost += 1
                if lost > END_DRAGS:
                    return
                self.drag(ROW_DELTA * 2)
                continue
            lost = 0
            for row in rows:
                for card in row.cards:
                    yield card, self.fleet_of(image, card)
                last = row.signature
            # The last row read up below the bar of the dock
            self.drag(rows[-1].top - ROW_AFTER_DRAG)
