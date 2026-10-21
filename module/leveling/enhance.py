"""
Enhance the ships of the fleets that the campaign uses, with ships no limit break needs.

The user's rule for the F2P account, 2026-10-21: "It's urgent to enhance ships and limit break them in the f2p
account because its fleet strength is so low. Just make sure not to enhance using ships that are meant for limit
breaking." The game's Fill took two copies of one ship and one of two Aobas, its pick is not used.

A material is a ship whose name no other card of the dock has: no limit break needs her, she needs none. She is at
level 3 at most (ships levelled for commissions stay), common or rare, not a Bulin, without a retrofit (retrofits
are the long-term core of the F2P account), in no fleet and on no commission. The names of every card of the dock are
read first, ships in fleets and locked ones too. The picker of materials is set to commons and rares, lowest level
first, and only its first screen is looked at: a tap on a card picked before would put it back. A ship gets what that
screen has, the next run gives more.
"""
from collections import Counter

import cv2

from module.base.timer import Timer
from module.base.utils import crop, load_image
from module.equipment.assets import FLEET_DETAIL_CHECK
from module.handler.assets import POPUP_CANCEL, POPUP_CONFIRM
from module.inventory.inventory import CARD_LEFTS
from module.leveling.fleet import CARD_NAME, LevelingFleet, area_button, card_name_area
from module.leveling.plan import RETROFIT, name_key
from module.logger import logger
from module.ocr.ocr import Ocr
from module.retire.dock_walk import DockWalker
from module.retire.scanner import ShipScanner
from module.ui.page import page_dock, page_fleet, page_main

# The enhance tab of the detail page: Fill between Empty and Enhance, 12 material slots above
ENHANCE_FILL = './assets/leveling/enhance_fill.png'
ENHANCE_FILL_AREA = (958, 590, 1112, 656)
ENHANCE_BUTTON = area_button((1150, 605, 1255, 642), 'ENHANCE_START')
# The first material slot, a "+" while empty: 1.0 empty, 0.32 with a ship in it
ENHANCE_SLOT_EMPTY = './assets/leveling/enhance_slot_empty.png'
ENHANCE_SLOT_AREA = (723, 378, 807, 462)
ENHANCE_SLOT = area_button((735, 390, 795, 450), 'ENHANCE_SLOT')
# The picker of materials, the dock with "Select characters to Enhance:" and Cancel, Confirm at its bottom
ENHANCE_PICKER = './assets/leveling/enhance_picker.png'
ENHANCE_PICKER_AREA = (28, 640, 382, 705)
PICKER_CONFIRM = area_button((950, 648, 1095, 690), 'ENHANCE_PICKER_CONFIRM')
PICKER_CANCEL = area_button((735, 648, 880, 690), 'ENHANCE_PICKER_CANCEL')
# Her name on top of the detail page, white letters
DETAIL_NAME = area_button((230, 86, 420, 118), 'DETAIL_NAME')
# The ship drawn on the left of the tab, no button there: a tap goes through the screens an enhancement shows
ENHANCE_SAFE_TAP = area_button((330, 380, 370, 420), 'ENHANCE_SAFE')
SIMILARITY = 0.85
# Confirm of a popup told by its color: 12 from it on "You've selected a Elite and above ship", 34 and more on the
# limit break and enhance tabs, the picker and the result of a limit break
POPUP_CONFIRM_COLOR = 20
# Materials, see the docstring
MATERIAL_LEVEL_MAX = 3
MATERIAL_RARITY = ('common', 'rare')
MATERIAL_SLOTS = 12
# Seconds for each step
STEP_TIMEOUT = 20


def template_score(image, template, area):
    region = crop(image, area, copy=False)
    return float(cv2.minMaxLoc(cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED))[1])


def material_fits(read, ship, census, matcher):
    """
    Args:
        read (str): Name read on the card of the picker
        ship (Ship): The card, with its level, rarity, fleet and status
        census (Counter): Cards of each name in the whole dock, by name_key()
        matcher (ShipMatcher):

    Returns:
        str: Why she is no material, '' if she is one
    """
    key = name_key(read)
    if not key:
        return 'name not read'
    if census[key] != 1:
        return f'{census[key]} cards of her name in the dock'
    if ship.level > MATERIAL_LEVEL_MAX:
        return f'level {ship.level}'
    if ship.rarity not in MATERIAL_RARITY:
        return f'rarity {ship.rarity}'
    if ship.fleet:
        return f'in fleet {ship.fleet}'
    if ship.status not in ('free', None):
        return f'status {ship.status}'
    data = matcher.match(read)
    if data is None:
        return 'no ship known by that name'
    if data.is_bulin:
        return 'a Bulin'
    if data.name.endswith(RETROFIT) or f'{data.name}{RETROFIT}' in matcher.ships:
        return 'has a retrofit'
    return ''


class FleetEnhance(LevelingFleet):
    def _enhance_template(self, file):
        if not hasattr(self, '_enhance_templates'):
            self._enhance_templates = {}
        if file not in self._enhance_templates:
            self._enhance_templates[file] = load_image(file)
        return self._enhance_templates[file]

    def is_enhance_tab(self):
        return template_score(self.device.image, self._enhance_template(ENHANCE_FILL), ENHANCE_FILL_AREA) > SIMILARITY

    def is_enhance_slot_empty(self):
        return template_score(
            self.device.image, self._enhance_template(ENHANCE_SLOT_EMPTY), ENHANCE_SLOT_AREA) > SIMILARITY

    def is_enhance_picker(self):
        return template_score(
            self.device.image, self._enhance_template(ENHANCE_PICKER), ENHANCE_PICKER_AREA) > SIMILARITY

    def handle_popup_confirm_loose(self, name):
        """
        handle_popup_confirm(), and the popups whose Confirm scores under its template on a 2560x1440 screen brought to
        1280x720, 0.74 for "You've selected a Elite and above ship. Continue?" with Laffey's copy: Cancel is found by
        its template, Confirm by its color at its place.

        Returns:
            bool: If a Confirm was tapped
        """
        if self.handle_popup_confirm(name):
            return True
        if self.appear(POPUP_CANCEL, offset=self._popup_offset, interval=2) \
                and POPUP_CONFIRM.appear_on(self.device.image, threshold=POPUP_CONFIRM_COLOR):
            logger.info(f'Popup {name}, its Confirm told by its color')
            self.device.click(POPUP_CONFIRM)
            return True
        return False

    def handle_popup_cancel_loose(self, name):
        """
        Returns:
            bool: If a Cancel was tapped, see handle_popup_confirm_loose()
        """
        if self.handle_popup_cancel(name):
            return True
        if self.appear(POPUP_CANCEL, offset=self._popup_offset, interval=2) \
                and POPUP_CONFIRM.appear_on(self.device.image, threshold=POPUP_CONFIRM_COLOR):
            logger.info(f'Popup {name}, cancelled')
            self.device.click(POPUP_CANCEL)
            return True
        return False

    def detail_name_fits(self, name, matcher):
        """
        Args:
            name (str): Name of the ship of the slot, on the wiki
            matcher (ShipMatcher):

        Returns:
            bool: False if the detail page shows an other ship, True if hers or not read

        Pages:
            in: detail page of a ship
        """
        read = Ocr(DETAIL_NAME, lang='cnocr', letter=(255, 255, 255), threshold=128, name='DETAIL_NAME').ocr(
            self.device.image)
        data = matcher.match(str(read)) if read else None
        if data is not None and data.name != name:
            logger.warning(f'The detail page shows {data.name}, not {name}')
            return False
        return True

    def enhance_census(self):
        """
        Returns:
            Counter: Cards of each name in the whole dock, by name_key()

        Pages:
            in: Any page
            out: page_dock
        """
        logger.hr('Enhance census', level=2)
        self.ui_ensure(page_main)
        self.ui_ensure(page_dock)
        self.dock_favourite_set(False, wait_loading=False)
        self.dock_filter_set(sort='level', index='all', faction='all', rarity='all', extra='no_limit')
        census = Counter()
        cards = 0
        for card, _ in DockWalker(self).cards():
            x1, y1, x2, y2 = CARD_NAME
            left = CARD_LEFTS[card.column]
            button = area_button((left + x1, card.top + y1, left + x2, card.top + y2), 'CENSUS_NAME')
            read = Ocr(button, lang='cnocr', letter=(255, 255, 255), threshold=128, name='CENSUS_NAME').ocr(
                self.device.image)
            census[name_key(str(read))] += 1
            cards += 1
        logger.info(f'Enhance census: {cards} cards, {sum(1 for count in census.values() if count > 1)} names with '
                    f'copies')
        return census

    def enhance_wait(self, check, timeout=STEP_TIMEOUT):
        """
        Returns:
            bool: If check() came true in time
        """
        for _ in self.loop(timeout=timeout):
            if check():
                return True
        return False

    def enhance_pick(self, census, matcher):
        """
        Tap the materials of the first screen of the picker.

        Returns:
            int: Materials picked

        Pages:
            in: the picker of materials
        """
        self.dock_filter_set(sort='level', index='all', faction='all', rarity=list(MATERIAL_RARITY),
                             extra='no_limit')
        self.dock_sort_method_dsc_set(False)
        self.device.screenshot()
        scanner = ShipScanner()
        scanner.disable('emotion')
        ships = scanner.scan(self.device.image, output=False)
        if not ships:
            return 0
        buttons = [area_button(card_name_area(ship.button), f'PICKER_NAME_{i}') for i, ship in enumerate(ships)]
        names = Ocr(buttons, lang='cnocr', letter=(255, 255, 255), threshold=128, name='PICKER_NAME').ocr(
            self.device.image)
        if isinstance(names, str):
            names = [names]
        picked = 0
        for ship, read in zip(ships, names):
            if picked >= MATERIAL_SLOTS:
                break
            why = material_fits(str(read), ship, census, matcher)
            if why:
                logger.info(f'{read} (Lv.{ship.level}) is no material: {why}')
                continue
            logger.info(f'Material: {read} (Lv.{ship.level}, {ship.rarity})')
            self.device.click(ship.button)
            self.device.sleep(0.3)
            picked += 1
        return picked

    def enhance_ship(self, slot, name, census, matcher):
        """
        Args:
            slot (Slot): Of a fleet, her ship is the one enhanced
            name (str): Her name on the wiki
            census (Counter): See enhance_census()
            matcher (ShipMatcher):

        Returns:
            int: Materials used

        Pages:
            in: Any page
            out: page_fleet, its 3D view
        """
        logger.hr(f'Enhance {name}', level=2)
        self.ui_ensure(page_fleet)
        self.formation_goto_fleet(slot.fleet)
        self.fleet_ship_enter(slot)
        # Detail tab 1, gear 2, limit break 3, enhancement 4 from the bottom
        self.ship_side_navbar_ensure(bottom=4)
        used = 0
        if not self.enhance_wait(self.is_enhance_tab, timeout=5):
            logger.info(f'{name} has no enhance tab')
        elif not self.detail_name_fits(name, matcher):
            pass
        else:
            used = self.enhance_with_materials(census, matcher)
        self.ui_back(check_button=FLEET_DETAIL_CHECK)
        self.fleet_formation_ensure()
        return used

    def enhance_with_materials(self, census, matcher):
        """
        Returns:
            int: Materials used

        Pages:
            in: enhance tab
            out: enhance tab
        """
        # The picker opens from a material slot
        click = Timer(3)
        for _ in self.loop(timeout=STEP_TIMEOUT):
            if self.is_enhance_picker():
                break
            if self.handle_popup_confirm_loose('ENHANCE_PICKER'):
                continue
            if click.reached() and self.is_enhance_tab():
                self.device.click(ENHANCE_SLOT)
                click.reset()
        else:
            logger.warning('The picker of materials did not open')
            return 0
        picked = self.enhance_pick(census, matcher)
        # Back to the tab, with the materials or without
        leave = PICKER_CONFIRM if picked else PICKER_CANCEL
        click = Timer(3)
        for _ in self.loop(timeout=STEP_TIMEOUT):
            if self.is_enhance_tab():
                break
            if self.handle_popup_confirm_loose('ENHANCE_MATERIALS'):
                continue
            if click.reached() and self.is_enhance_picker():
                self.device.click(leave)
                click.reset()
        else:
            logger.warning('The picker of materials did not close')
            return 0
        if not picked:
            logger.info('No material in the picker')
            return 0
        if self.is_enhance_slot_empty():
            logger.info('The materials did not come in the slots')
            return 0
        # Enhance, a popup maybe, the screens of the enhancement, the tab back with its slots empty
        self.device.click(ENHANCE_BUTTON)
        tap = Timer(2)
        retry = Timer(4, count=8).start()
        for _ in self.loop(timeout=STEP_TIMEOUT * 2):
            if self.handle_popup_confirm_loose('ENHANCE'):
                continue
            if self.is_enhance_tab():
                if self.is_enhance_slot_empty():
                    logger.info(f'Enhanced with {picked} materials')
                    return picked
                if retry.reached():
                    self.device.click(ENHANCE_BUTTON)
                    retry.reset()
                continue
            if tap.reached():
                self.device.click(ENHANCE_SAFE_TAP)
                tap.reset()
        logger.warning('The enhancement did not come back to its tab')
        return 0

    def enhance_fleet(self, members, matcher):
        """
        Args:
            members (list[tuple[Slot, str]]): Ships of the fleets, with their names on the wiki
            matcher (ShipMatcher):

        Returns:
            int: Materials used
        """
        if not members:
            return 0
        census = self.enhance_census()
        used = 0
        for slot, name in members:
            used += self.enhance_ship(slot, name, census, matcher)
        logger.info(f'Enhance: {used} materials used')
        return used
