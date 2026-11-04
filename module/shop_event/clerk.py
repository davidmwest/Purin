import cv2

from module.base.button import ButtonGrid
from module.base.decorator import cached_property
from module.base.timer import Timer
from module.base.utils import color_mask, crop
from module.combat.assets import GET_SHIP, GET_ITEMS_1, GET_ITEMS_3
from module.logger import logger
from module.map_detection.utils import Points
from module.shop.assets import AMOUNT_PLUS, AMOUNT_MINUS, AMOUNT_MAX, SHOP_BUY_CONFIRM_AMOUNT, SHOP_BUY_CONFIRM, \
    SHOP_CLICK_SAFE_AREA
from module.shop.clerk import OCR_SHOP_AMOUNT
from module.shop_event.assets import *
from module.shop_event.item import ITEM_SHAPE, EventShopItemGrid, DELTA_PRICE_BACKGROUND, DELTA_ITEM, GRID_SHAPE, \
    PRICE_BACKGROUND_COLOR, PRICE_THRESHOLD
from module.shop_event.ui import EventShopUI, EVENT_SHOP_SCROLL
from module.ui_white.assets import BACK_ARROW_WHITE

DETECT_AREA = (221, 194, 1049, 632)
# How far to scroll between two screens of a scan, in screens. A row of items is about half a screen.
EVENT_SHOP_SCAN_PAGE = 0.3


class ItemNotFoundError(Exception):
    pass


class EventShopClerk(EventShopUI):
    pt_image = None
    urpt_image = None

    def _get_event_shop_grid(self):
        # PRICE_THRESHOLD is a color similarity, color_mask takes a color tolerance
        mask = color_mask(self.device.image, PRICE_BACKGROUND_COLOR, threshold=255 - PRICE_THRESHOLD)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=8)
        mask = crop(mask,
                    (DETECT_AREA[0], DETECT_AREA[1] + DELTA_PRICE_BACKGROUND[1], DETECT_AREA[2], DETECT_AREA[3]),
                    copy=False)
        buttons = TEMPLATE_COST_PRICE_BACKGROUND.match_multi(mask, similarity=0.6)
        points = Points([(0., p.area[1]) for p in buttons]).group(threshold=5)

        row = len(points)
        if row == 2:
            y = min(points[0][1], points[1][1]) + DETECT_AREA[1]
            delta_y = abs(points[0][1] - points[1][1])
        elif row == 1:
            y = points[0][1] + DETECT_AREA[1]
            delta_y = 215
        else:
            logger.warning(f"Unexpected number of rows: {row}, assuming scroll at top.")
            y = 1 + DETECT_AREA[1]  # Start position is 1 pixel lower than detect area
            delta_y = 215

        shop_grid = ButtonGrid(
            origin=(DETECT_AREA[0] + DELTA_ITEM[0], y + DELTA_ITEM[1]),
            delta=(169, delta_y),
            button_shape=ITEM_SHAPE,
            grid_shape=(5, row),
            name="EVENT_SHOP_GRID",
        )
        return shop_grid

    @cached_property
    def event_shop_items(self):
        event_shop_items = EventShopItemGrid(grids=None, templates={})
        event_shop_items.load_template_folder('./assets/shop/event')
        return event_shop_items

    def event_shop_get_items(self, scroll_pos=None):
        self.ensure_no_info_bar()
        for attempt in range(3):
            self.event_shop_items.grids = self._get_event_shop_grid()
            if self.config.SHOP_EXTRACT_TEMPLATE:
                self.event_shop_items.extract_template(self.device.image, './assets/shop/event')
            self.event_shop_items.predict(self.device.image, name=True, amount=True, cost=False,
                                          price=True, tag=True, counter=True, scroll_pos=scroll_pos)
            shop_items = self.event_shop_items.items
            # Invalid OCR uses 0/0; a valid sold-out counter is 0/N.
            invalid = [item for item in shop_items if item.count == 0 and item.total_count == 0]
            if not invalid:
                break
            if attempt >= 2:
                message = f'Invalid event shop counter after {attempt + 1} scans: {[str(item) for item in invalid]}'
                logger.error(message)
                raise ItemNotFoundError(message)
            logger.warning(f'Invalid event shop counter, retrying: {[str(item) for item in invalid]}')
            self.device.screenshot()

        if len(shop_items):
            min_row = self.event_shop_items.grids[0, 0].area[1]
            row = [str(item) for item in shop_items if item.button[1] == min_row]
            logger.info(f'Shop row 1: {row}')
            row = [str(item) for item in shop_items if item.button[1] != min_row]
            logger.info(f'Shop row 2: {row}')
            return shop_items
        else:
            logger.info('No shop items found')
            return []

    @staticmethod
    def merge_scanned_rows(rows, new_items, scroll_pixels=None):
        """
        Add the items of a screen to what has been scanned so far.
        The rows at the top of a screen are usually the last rows of the screen before.

        A row is the same row if it has the same prices and stocks, and if it is where the scrolling
        since should have put it. Names are left out, the same icon is not matched to the same name every time.

        Args:
            rows (list[list[EventShopItem]]): Rows of items scanned so far, top to bottom.
            new_items (list[EventShopItem]): Items on the current screen.
            scroll_pixels (int | float): How far the items move while the scroll goes from top to bottom.
                None to go by prices and stocks only.

        Returns:
            list[list[EventShopItem]]:
        """
        new_rows = {}
        for item in new_items:
            new_rows.setdefault(item.button[1], []).append(item)
        new_rows = [new_rows[y] for y in sorted(new_rows)]

        def same(old_row, new_row):
            if [(item.price, item.count, item.total_count) for item in old_row] \
                    != [(item.price, item.count, item.total_count) for item in new_row]:
                return False
            old, new = old_row[0], new_row[0]
            if scroll_pixels is None or old.scroll_pos is None or new.scroll_pos is None:
                return True
            moved_to = old.button[1] - (new.scroll_pos - old.scroll_pos) * scroll_pixels
            return abs(new.button[1] - moved_to) < GRID_SHAPE[1] / 2

        for seen in range(min(len(rows), len(new_rows)), 0, -1):
            if all(same(old, new) for old, new in zip(rows[-seen:], new_rows[:seen])):
                logger.info('Ignore duplicated items')
                new_rows = new_rows[seen:]
                break
        return rows + new_rows

    @staticmethod
    def event_shop_scroll_pixels():
        """
        The button of the scroll is as long, compared to the scroll, as the part of the items that is shown.

        Returns:
            float | None: How far the items move while the scroll goes from top to bottom.
                None if the length of the button is not known.
        """
        button, total = EVENT_SHOP_SCROLL.length, EVENT_SHOP_SCROLL.total
        if not 0 < button < total:
            return None
        return (DETECT_AREA[3] - DETECT_AREA[1]) * (total - button) / button

    def scan_all(self, max_pages=None, on_page=None):
        rows = []

        logger.hr('Event Shop Scan', level=2)
        # Each event shop has its own amount of items
        EVENT_SHOP_SCROLL.button_length = None
        EVENT_SHOP_SCROLL.set_top(main=self)
        at_end = False
        pages = 0
        while 1:
            if max_pages is not None and pages >= max_pages:
                raise ItemNotFoundError(f'Event shop scan did not finish within {max_pages} pages')
            # Swipes of a scan going well are not a button clicked over and over
            self.device.click_record_clear()
            new_items = self.event_shop_get_items(scroll_pos=EVENT_SHOP_SCROLL.cal_position(main=self))
            if on_page is not None:
                on_page(pages, new_items)
            pages += 1
            rows = self.merge_scanned_rows(rows, new_items, scroll_pixels=self.event_shop_scroll_pixels())
            if at_end:
                logger.info('Event shop reach bottom')
                break
            if EVENT_SHOP_SCROLL.at_bottom(main=self):
                # The scroll counts as at bottom a bit before the end, and the last row is only shown at the end
                EVENT_SHOP_SCROLL.drag_to_end(main=self)
                at_end = True
            else:
                # Less than a row at a time. A row is only read while all of it is shown,
                # and the position of the scroll is not known well enough to go by whole pages.
                EVENT_SHOP_SCROLL.next_page(main=self, page=EVENT_SHOP_SCAN_PAGE)
        return [item for row in rows for item in row]

    def event_shop_buy_item(self, item_to_buy, amount=None):
        scroll_pos = item_to_buy.scroll_pos
        EVENT_SHOP_SCROLL.set(scroll_pos, main=self)
        items = self.event_shop_get_items()
        items = [item for item in items if item.name == item_to_buy.name
                 and item.count == item_to_buy.count and item.price == item_to_buy.price]
        if len(items) == 0:
            logger.error(f'Item {item_to_buy} not found at scroll position {scroll_pos}')
            logger.warning(f'Will try to rerun the task.')
            raise ItemNotFoundError(f'Item {item_to_buy} not found at scroll position {scroll_pos}')
        elif len(items) > 1:
            logger.warning(f'Multiple items found for {item_to_buy} at scroll position {scroll_pos}, '
                           f'buying the first one')
        item = items[0]
        # For ship items, while it may have multiple stock, can only buy one at a time.
        if getattr(item, 'is_ship', False):
            buy_times = item.count if amount is None else min(amount, item.count)
            for _ in range(buy_times):
                self.event_shop_buy_item_execute(item, amount=1)
        else:
            self.event_shop_buy_item_execute(item, amount=amount)

    def event_shop_buy_item_execute(self, item, amount):
        self.event_shop_handle_obstruct()
        executed = False
        amount_handled = False
        timer = Timer(2, count=4).start()
        for _ in self.loop():
            if self.appear(AMOUNT_MAX, offset=(20, 20)):
                if not amount_handled:
                    self.device.click(AMOUNT_MAX)
                    if amount is not None:
                        self.ui_ensure_index(amount, letter=OCR_SHOP_AMOUNT, prev_button=AMOUNT_MINUS,
                                             next_button=AMOUNT_PLUS,
                                             skip_first_screenshot=False)
                    amount_handled = True
                    timer.reset()
                    continue
                else:
                    self.device.click(SHOP_BUY_CONFIRM_AMOUNT)
                    executed = True
                    timer.reset()
                    continue
            elif self.appear(SHOP_BUY_CONFIRM, offset=(20, 40)):
                self.device.click(SHOP_BUY_CONFIRM)
                executed = True
                timer.reset()
                continue
            elif self.handle_popup_confirm("meta_buy_confirm"):
                timer.reset()
                continue
            elif self.appear(BACK_ARROW_WHITE, offset=(20, 20)):
                if not executed:
                    if timer.reached():
                        self.device.click(item)
                        timer.reset()
                    continue
                elif timer.reached():
                    break
            elif timer.reached() and self.event_shop_handle_obstruct():
                timer.reset()
                continue

    def event_shop_handle_obstruct(self):
        if self.handle_info_bar():
            return True
        if self.handle_get_meowfficer():
            return True
        if self.appear(GET_SHIP, offset=(20, 20), interval=2):
            logger.info(f'Shop obstruct: {GET_SHIP} -> {SHOP_CLICK_SAFE_AREA}')
            self.device.click(SHOP_CLICK_SAFE_AREA)
            return True
        if self.appear(GET_ITEMS_1, offset=(20, 20), interval=2):
            logger.info(f'Shop obstruct: {GET_ITEMS_1} -> {SHOP_CLICK_SAFE_AREA}')
            self.device.click(SHOP_CLICK_SAFE_AREA)
            return True
        if self.appear(GET_ITEMS_3, offset=(20, 20), interval=2):
            logger.info(f'Shop obstruct: {GET_ITEMS_3} -> {SHOP_CLICK_SAFE_AREA}')
            self.device.click(SHOP_CLICK_SAFE_AREA)
            return True
        return False
