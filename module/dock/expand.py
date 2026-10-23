"""
DockExpand: buy room in the dock with gems, when the dock is nearly full.

The user, 2026-10-22: "Since you'll be getting 5-10 gems per day as a f2p player, i think spending them on dock
expansion makes sense", and the 3-star pass of the campaign now earns the gems of its missions. In the game, the "+"
before "Dock capacity" of the dock opens the Items of the shop, where a Dock Expansion gives 10 slots for 200 gems
(the F2P account, 2026-10-22: 150 slots, 200 gems).

A run reads the dock. With fewer free slots than DockExpand.FreeBelow, it opens the shop from the "+", reads the gems
and the price of the expansion, and buys one if the price is DockExpand.PriceMax at most and the gems cover it with
DockExpand.GemsReserve left. Then the dock must show 10 more slots. DockExpand.DryRun only logs what it would buy:
gems spent are not given back.

Pages:
    in: Any page
    out: page_dock
"""
import os
from datetime import datetime

import cv2

from module.base.timer import Timer
from module.base.utils import crop, load_image, save_image
from module.inventory.inventory import OCR_DOCK_AMOUNT
from module.leveling.fleet import area_button
from module.logger import logger
from module.ocr.ocr import Digit
from module.shop.shop_status import OCR_SHOP_GEMS
from module.ui.page import page_dock, page_main
from module.ui.ui import UI

# The "+" before "Dock capacity", on the dock page
DOCK_PLUS = './assets/dock/dock_plus.png'
DOCK_PLUS_AREA = (376, 5, 421, 51)
DOCK_PLUS_BUTTON = area_button((388, 17, 409, 39), 'DOCK_PLUS')
# The card of Dock Expansion among the Items of the shop, its price under its icon
EXPANSION_ICON = './assets/dock/expansion_icon.png'
SHOP_ITEMS_AREA = (205, 175, 1080, 660)
PRICE_OFFSET = (20, 128, 66, 160)
# The popup a tap on the card opens: "Dock Expansion", "Increase Dock capacity by 10 slots", Cancel and Purchase
POPUP_TITLE = './assets/dock/expansion_popup_title.png'
POPUP_TITLE_AREA = (514, 236, 714, 301)
PURCHASE = './assets/dock/expansion_purchase.png'
PURCHASE_AREA = (690, 470, 890, 552)
PURCHASE_BUTTON = area_button((715, 492, 865, 530), 'DOCK_EXPANSION_PURCHASE')
CANCEL_BUTTON = area_button((415, 492, 565, 530), 'DOCK_EXPANSION_CANCEL')
# The second confirmation, captured from the scheduled F2P task on 2026-10-22. Only this 200-gem offer is verified.
CONFIRM_SENTENCE = './assets/dock/expansion_confirm_sentence.png'
CONFIRM_SENTENCE_AREA = (378, 328, 904, 365)
CONFIRM_PRICE_AREA = (467, 331, 516, 361)
CONFIRM = './assets/dock/expansion_confirm.png'
CONFIRM_AREA = (698, 478, 882, 546)
CONFIRM_BUTTON = area_button((720, 492, 858, 532), 'DOCK_EXPANSION_CONFIRM')
CONFIRMED_PRICE = 200
SIMILARITY = 0.85
EXPANSION_SLOTS = 10
# Seconds for the shop to open, and for a purchase to go through
OPEN_WAIT = 10
PURCHASE_WAIT = 15


def template_match(image, template, area):
    """
    Returns:
        tuple[float, tuple[int, int]]: Best similarity of the template within the area, and the top left of the match
            on the image
    """
    region = crop(image, area, copy=False)
    _, score, _, point = cv2.minMaxLoc(cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED))
    return float(score), (area[0] + point[0], area[1] + point[1])


def price_area(icon):
    """
    Args:
        icon (tuple[int, int]): Top left of the icon of a card

    Returns:
        tuple[int, int, int, int]: Where the price of the card is
    """
    x1, y1, x2, y2 = PRICE_OFFSET
    return icon[0] + x1, icon[1] + y1, icon[0] + x2, icon[1] + y2


def purchase_verdict(free, gems, price, free_below, price_max, reserve):
    """
    Args:
        free (int): Free slots in the dock
        gems (int): Gems of the account, 0 if not read
        price (int): Price of a Dock Expansion, 0 if not read
        free_below (int): An expansion is bought below this many free slots
        price_max (int): Most gems an expansion may cost
        reserve (int): Gems kept

    Returns:
        str: '' to buy, else why not
    """
    if free >= free_below:
        return f'{free} free slots, {free_below} at least'
    if price <= 0:
        return 'price not read'
    if price > price_max:
        return f'it costs {price} gems, {price_max} at most'
    if gems <= 0:
        return 'gems not read'
    if gems < price + reserve:
        return f'{gems} gems, {price} to buy it and {reserve} kept'
    return ''


class DockExpand(UI):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._templates = {}

    def _template(self, file):
        if file not in self._templates:
            self._templates[file] = load_image(file)
        return self._templates[file]

    def _appear(self, file, area):
        return template_match(self.device.image, self._template(file), area)[0] > SIMILARITY

    def dock_amount(self):
        """
        Returns:
            tuple[int, int]: Cards in the dock and its capacity, (0, 0) if not read

        Pages:
            in: page_dock
        """
        for _ in range(3):
            current, _, total = OCR_DOCK_AMOUNT.ocr(self.device.image)
            if 0 < total <= 4030 and 0 <= current <= total:
                return current, total
            self.device.sleep(0.5)
            self.device.screenshot()
        return 0, 0

    def expansion_offer(self):
        """
        Returns:
            tuple[Button, int] | None: The card of Dock Expansion and its price, 0 if not read. None if the Items of
                the shop do not show it.

        Pages:
            in: the Items of the shop
        """
        score, icon = template_match(self.device.image, self._template(EXPANSION_ICON), SHOP_ITEMS_AREA)
        if score < SIMILARITY:
            return None
        h, w = self._template(EXPANSION_ICON).shape[:2]
        card = area_button((icon[0], icon[1], icon[0] + w, icon[1] + h), 'DOCK_EXPANSION')
        price = Digit(area_button(price_area(icon), 'DOCK_EXPANSION_PRICE'), letter=(255, 255, 255),
                      threshold=128, name='DOCK_EXPANSION_PRICE').ocr(self.device.image)
        return card, int(price or 0)

    def expansion_shop_enter(self):
        """
        Returns:
            bool: If the Items of the shop show a Dock Expansion

        Pages:
            in: page_dock
            out: the Items of the shop, or page_dock
        """
        timeout = Timer(OPEN_WAIT, count=20).start()
        click = Timer(3)
        for _ in self.loop():
            if self.expansion_offer() is not None:
                return True
            if timeout.reached():
                logger.warning('The shop did not show a Dock Expansion')
                return False
            if self._appear(DOCK_PLUS, DOCK_PLUS_AREA) and click.reached():
                self.device.click(DOCK_PLUS_BUTTON)
                click.reset()

    def expansion_confirmation(self, price, gems):
        """Recognize only the reviewed 200-gem Dock Expansion confirmation.

        Matching the sentence alone is insufficient: changing one digit could still give a high template score.
        Read its green price separately and require the offer already checked on the shop, with enough gems.
        """
        if price != CONFIRMED_PRICE or gems < price:
            return False
        if not self._appear(CONFIRM_SENTENCE, CONFIRM_SENTENCE_AREA):
            return False
        if not self._appear(CONFIRM, CONFIRM_AREA):
            return False
        amount = Digit(area_button(CONFIRM_PRICE_AREA, 'DOCK_EXPANSION_CONFIRM_PRICE'),
                       letter=(173, 255, 74), threshold=96, name='DOCK_EXPANSION_CONFIRM_PRICE').ocr(self.device.image)
        return int(amount or 0) == price

    def expansion_buy(self, price, gems):
        """
        Purchase is tapped once only, a second tap could buy a second expansion. Only the reviewed second
        confirmation for the already checked 200-gem offer is confirmed, once. Other popups are cancelled and
        captured: an insufficient-gems popup could open the paid gems. It ends on the shop with no popup for 2 s,
        or at the timeout with any popup cancelled.

        Returns:
            bool: If Purchase was tapped

        Pages:
            in: the Items of the shop
            out: the Items of the shop
        """
        if price != CONFIRMED_PRICE or gems < price:
            logger.warning(f'Dock Expansion purchase not verified: price {price}, gems {gems}')
            return False
        timeout = Timer(PURCHASE_WAIT, count=30).start()
        click = Timer(3)
        settled = Timer(2, count=4)
        purchased = False
        confirmed = False
        for _ in self.loop():
            if timeout.reached():
                if self._appear(POPUP_TITLE, POPUP_TITLE_AREA):
                    logger.warning('The popup of the Dock Expansion is still open, cancelled')
                    self.device.click(CANCEL_BUTTON)
                elif self._cancel_purchase_popup():
                    logger.warning('A popup is still open, cancelled')
                else:
                    logger.warning('The purchase of a Dock Expansion did not end on the shop')
                return purchased
            if self._appear(POPUP_TITLE, POPUP_TITLE_AREA):
                settled.clear()
                if not purchased and self._appear(PURCHASE, PURCHASE_AREA):
                    self.device.click(PURCHASE_BUTTON)
                    purchased = True
                    timeout.reset()
                continue
            if purchased:
                if self.expansion_confirmation(price, gems):
                    settled.clear()
                    if not confirmed:
                        # This captured button differs from the generic white popup template. Its own template
                        # and the exact sentence/price above must all match before this fixed button is tapped.
                        self.device.click(CONFIRM_BUTTON)
                        confirmed = True
                    # The dialog can linger after the tap. Do not confirm it again or cancel during its transition.
                    continue
                if self._cancel_purchase_popup():
                    logger.warning('A popup came after Purchase, cancelled')
                    settled.clear()
                    continue
                if self.expansion_offer() is not None:
                    if not settled.started():
                        settled.start()
                    if settled.reached():
                        return True
                else:
                    settled.clear()
                continue
            # The card where it is now, not where the shop first drew it
            offer = self.expansion_offer()
            if offer is not None and click.reached():
                self.device.click(offer[0])
                click.reset()

    def _cancel_purchase_popup(self):
        """Cancel an unverified popup and retain its pre-click frame for review.

        A popup might be an offer to buy gems or another purchase. Keep the frame before the cancel handler taps,
        since a later screenshot is the shop. Only expansion_confirmation permits a second confirmation.
        """
        image = self.device.image.copy()
        if not self.handle_popup_cancel('DOCK_EXPANSION'):
            return False
        folder = os.path.join('log', 'dock_expand')
        file = os.path.join(folder, f'{datetime.now():%Y-%m-%d_%H-%M-%S-%f}_unverified_popup.png')
        try:
            os.makedirs(folder, exist_ok=True)
            save_image(image, file)
            logger.warning(f'Unverified Dock Expansion popup cancelled; pre-click screenshot saved to {file}')
        except OSError as exc:
            logger.warning(f'Unverified Dock Expansion popup cancelled; screenshot could not be saved: {exc}')
        return True

    def run(self):
        self.ui_ensure(page_main)
        self.ui_ensure(page_dock)
        current, total = self.dock_amount()
        if not total:
            logger.warning('Dock capacity not read')
            self.config.task_delay(success=False)
            return
        free = total - current
        logger.attr('Dock', f'{current}/{total}, {free} free')
        free_below = self.config.DockExpand_FreeBelow
        if free >= free_below:
            logger.info(f'Room in the dock: {free} free slots, {free_below} at least, nothing bought')
            self.config.task_delay(success=True)
            return

        if not self.expansion_shop_enter():
            self.ui_ensure(page_dock)
            self.config.task_delay(success=False)
            return
        gems = int(OCR_SHOP_GEMS.ocr(self.device.image) or 0)
        _, price = self.expansion_offer()
        logger.attr('Gems', gems)
        logger.attr('Dock_expansion_price', price)
        verdict = purchase_verdict(free, gems, price, free_below=free_below,
                                   price_max=self.config.DockExpand_PriceMax,
                                   reserve=self.config.DockExpand_GemsReserve)
        if verdict:
            logger.info(f'No Dock Expansion bought: {verdict}')
            self.ui_ensure(page_dock)
            self.config.task_delay(success=True)
            return
        if self.config.DockExpand_DryRun:
            logger.info(f'DockExpand.DryRun is on: a Dock Expansion for {price} gems would be bought, '
                        f'{gems} gems, dock {current}/{total}')
            self.ui_ensure(page_dock)
            self.config.task_delay(success=True)
            return

        logger.hr('Dock expansion', level=2)
        logger.info(f'Buying a Dock Expansion for {price} gems of {gems}, dock {current}/{total}')
        self.expansion_buy(price=price, gems=gems)
        if self.expansion_offer() is not None:
            gems_after = int(OCR_SHOP_GEMS.ocr(self.device.image) or 0)
            logger.attr('Gems_after_dock_expansion', gems_after)
            logger.info(f'Dock Expansion gems: {gems} -> {gems_after}')
        self.ui_ensure(page_main)
        self.ui_ensure(page_dock)
        after, total_after = self.dock_amount()
        if total_after == total + EXPANSION_SLOTS:
            logger.info(f'Dock expanded: {after}/{total_after}')
            self.config.task_delay(success=True)
        else:
            logger.warning(f'Dock capacity {total_after} after the purchase, {total + EXPANSION_SLOTS} expected')
            self.config.task_delay(success=False)
