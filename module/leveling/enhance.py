"""
Enhance the ships of the fleets that the campaign uses, with ships no limit break needs.

The user's rule for the F2P account, 2026-10-21: "It's urgent to enhance ships and limit break them in the f2p
account because its fleet strength is so low. Just make sure not to enhance using ships that are meant for limit
breaking." The game's Fill took two copies of one ship and one of two Aobas, its pick is not used.

Then the user's rule of 2026-10-21: "keep enough to MLB any locked ship, any extras count as enhancement material".
The cards of the whole dock are read first, by name: a locked one keeps 4 copies, what it takes to limit break her
fully from none (1, 1, then 2, a card does not tell how many she had), and so does one in a fleet, locked or not, as
the limit breaks of this task use their copies: Long Island and Laffey of the F2P fleet are not locked. The other
cards of a name, past those kept, are extras, a single unlocked ship too. An extra is material at level 3 at most
(ships levelled for commissions stay), common, rare or elite, not a Bulin, in no fleet and on no commission.
The picker of materials is set to those rarities, lowest level first, and only its first screen is looked at: a tap on
a card picked before would put it back. A ship gets what that screen has, the next run gives more.
"""
from collections import Counter
from datetime import datetime
import os

import cv2

from module.base.timer import Timer
from module.base.utils import crop, load_image, save_image
from module.combat.assets import GET_ITEMS_1
from module.equipment.assets import FLEET_DETAIL_CHECK
from module.exception import ScriptError
from module.handler.assets import POPUP_CANCEL, POPUP_CONFIRM
from module.inventory.inventory import CARD_LEFTS
from module.leveling.fleet import CARD_NAME, LevelingFleet, area_button, card_name_area
from module.leveling.plan import name_key
from module.logger import logger
from module.ocr.ocr import Ocr
from module.retire.dock_walk import DockWalker
from module.retire.assets import EQUIP_CONFIRM, EQUIP_CONFIRM_2, GET_ITEMS_1_RETIREMENT_SAVE
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
# Enhancement materials with gear open this exact disassembly dialog before the tab returns.
# Its default selection and confirmation follow the existing retirement enhancement path.
ENHANCE_EQUIPMENT_MESSAGE = './assets/leveling/enhance_equipment_message.png'
ENHANCE_EQUIPMENT_AREA = (330, 518, 1090, 558)
# Disassembly then shows the returned items under "Will get:". This is a second
# confirmation, not an Item Found receipt or an unrelated blue Confirm.
ENHANCE_EQUIPMENT_RETURN = './assets/leveling/enhance_equipment_return.png'
ENHANCE_EQUIPMENT_RETURN_AREA = (578, 125, 707, 165)
SIMILARITY = 0.85
# Confirm of a popup told by its color: 12 from it on "You've selected a Elite and above ship", 34 and more on the
# limit break and enhance tabs, the picker and the result of a limit break
POPUP_CONFIRM_COLOR = 20
# Materials, see the docstring
MATERIAL_LEVEL_MAX = 3
MATERIAL_RARITY = ('common', 'rare', 'elite')
MATERIAL_SLOTS = 12
# Copies a ship needs to be limit broken fully from none: 1, 1, then 2
MLB_COPIES = 4
# Rows found by their gap start 2 below the cards of CARD_GRIDS: the name read there took a letter of the frame,
# ".Beaglem" for the Beagle the picker read
CENSUS_NAME_SHIFT = -2
# Seconds for each step
STEP_TIMEOUT = 20
ENHANCE_RETRIES = 2
EQUIPMENT_RETRIES = 2
RECEIPT_RETRIES = 3
POPUP_RETRIES = 3
ENHANCE_DIAGNOSTICS = './log/enhance'
ENHANCE_DIAGNOSTICS_KEEP = 20


def template_score(image, template, area):
    region = crop(image, area, copy=False)
    return float(cv2.minMaxLoc(cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED))[1])


class Census:
    """
    The cards of the whole dock by name, see the docstring of this module.
    """

    def __init__(self, matcher=None):
        """
        Args:
            matcher (ShipMatcher): Names read are taken as the ship they are closest to, the census and the picker
                read a name each their way. None to keep them as read.
        """
        self.matcher = matcher
        self.total = Counter()
        # Locked, or in a fleet: each keeps MLB_COPIES copies
        self.keepers = Counter()
        # Neither
        self.free = Counter()
        # Picked as material during this run
        self.taken = Counter()

    def key(self, read):
        data = self.matcher.match(read) if self.matcher is not None and read else None
        return name_key(data.name) if data is not None else name_key(read)

    def add(self, read, locked=False, fleet=0):
        key = self.key(read)
        self.total[key] += 1
        if locked or fleet:
            self.keepers[key] += 1
        else:
            self.free[key] += 1

    def extras(self, read):
        """
        Returns:
            int: Cards of her name that can still be material
        """
        key = self.key(read)
        return max(0, self.free[key] - MLB_COPIES * self.keepers[key] - self.taken[key])

    def take(self, read):
        self.taken[self.key(read)] += 1

    def __len__(self):
        return sum(self.total.values())


def material_fits(read, ship, census, matcher):
    """
    Args:
        read (str): Name read on the card of the picker
        ship (Ship): The card, with its level, rarity, fleet and status
        census (Census): Cards of each name in the whole dock
        matcher (ShipMatcher):

    Returns:
        str: Why she is no material, '' if she is one
    """
    key = census.key(read)
    if not key:
        return 'name not read'
    if ship.level > MATERIAL_LEVEL_MAX:
        return f'level {ship.level}'
    if ship.rarity not in MATERIAL_RARITY:
        return f'rarity {ship.rarity}'
    if ship.fleet:
        return f'in fleet {ship.fleet}'
    if ship.status not in ('free', None):
        return f'status {ship.status}'
    if not census.extras(read):
        return (f'kept to limit break: {census.total[key]} in the dock, {census.keepers[key]} locked or in a fleet '
                f'keep {MLB_COPIES} each, {census.taken[key]} taken')
    data = matcher.match(read)
    if data is None:
        return 'no ship known by that name'
    if data.is_bulin:
        return 'a Bulin'
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

    def is_enhance_equipment_dialog(self):
        return (template_score(self.device.image, self._enhance_template(ENHANCE_EQUIPMENT_MESSAGE),
                               ENHANCE_EQUIPMENT_AREA) > SIMILARITY
                and self.match_template_color(EQUIP_CONFIRM, offset=(3, 3), similarity=SIMILARITY))

    def is_enhance_equipment_return_dialog(self):
        return (template_score(self.device.image, self._enhance_template(ENHANCE_EQUIPMENT_RETURN),
                               ENHANCE_EQUIPMENT_RETURN_AREA) > SIMILARITY
                and self.match_template_color(EQUIP_CONFIRM_2, offset=(3, 3), similarity=SIMILARITY))

    def save_enhance_stall(self, reason):
        try:
            os.makedirs(ENHANCE_DIAGNOSTICS, exist_ok=True)
            name = f'{datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")}_{reason}.png'
            path = os.path.join(ENHANCE_DIAGNOSTICS, name)
            save_image(self.device.image, path)
            logger.warning(f'Enhancement stalled, screen saved: {path}')
            for old in sorted(name for name in os.listdir(ENHANCE_DIAGNOSTICS) if name.endswith('.png'))[
                    :-ENHANCE_DIAGNOSTICS_KEEP]:
                os.remove(os.path.join(ENHANCE_DIAGNOSTICS, old))
        except Exception as error:
            logger.warning(f'Enhancement screen not saved: {error}')

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

    def enhance_census(self, matcher=None):
        """
        Returns:
            Census: Cards of each name in the whole dock

        Pages:
            in: Any page
            out: page_dock
        """
        logger.hr('Enhance census', level=2)
        self.ui_ensure(page_main)
        self.ui_ensure(page_dock)
        self.dock_favourite_set(False, wait_loading=False)
        self.dock_filter_set(sort='level', index='all', faction='all', rarity='all', extra='no_limit')
        census = Census(matcher)
        for card, fleet in DockWalker(self).cards():
            x1, y1, x2, y2 = CARD_NAME
            left, top = CARD_LEFTS[card.column], card.top + CENSUS_NAME_SHIFT
            button = area_button((left + x1, top + y1, left + x2, top + y2), 'CENSUS_NAME')
            read = Ocr(button, lang='cnocr', letter=(255, 255, 255), threshold=128, name='CENSUS_NAME').ocr(
                self.device.image)
            census.add(str(read), locked=card.locked, fleet=fleet)
        extras = sum(max(0, census.free[key] - MLB_COPIES * census.keepers[key]) for key in census.total)
        logger.info(f'Enhance census: {len(census)} cards, {sum(census.keepers.values())} locked or in a fleet, '
                    f'{extras} extras')
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
            census.take(str(read))
            self.device.click(ship.button)
            self.device.sleep(0.3)
            picked += 1
        return picked

    def enhance_ship(self, slot, name, census, matcher):
        """
        Args:
            slot (Slot): Of a fleet, her ship is the one enhanced
            name (str): Her name on the wiki
            census (Census): See enhance_census()
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
        return self.enhance_finish(picked)

    def enhance_finish(self, picked):
        """Complete only recognized gear/receipt pages and verify the materials emptied."""
        self.device.click(ENHANCE_BUTTON)
        click = Timer(2, count=3).start()
        retry = Timer(4, count=8).start()
        empty = Timer(1, count=2).start()
        empty_reads = 0
        retries = equipment_clicks = equipment_return_clicks = receipts = popups = 0
        for _ in self.loop(timeout=STEP_TIMEOUT * 2):
            if not click.reached():
                continue
            if self.is_enhance_equipment_dialog():
                empty_reads = 0
                empty.reset()
                if equipment_clicks >= EQUIPMENT_RETRIES:
                    self.save_enhance_stall('equipment_confirmation_rejected')
                    raise ScriptError('Enhancement equipment confirmation was not accepted')
                self.device.click(EQUIP_CONFIRM)
                equipment_clicks += 1
                click.reset()
                continue
            if self.is_enhance_equipment_return_dialog():
                empty_reads = 0
                empty.reset()
                # Only our preceding disassembly confirmation owns this panel.
                if not equipment_clicks:
                    continue
                if equipment_return_clicks >= EQUIPMENT_RETRIES:
                    self.save_enhance_stall('equipment_return_confirmation_rejected')
                    raise ScriptError('Enhancement equipment return confirmation was not accepted')
                self.device.click(EQUIP_CONFIRM_2)
                equipment_return_clicks += 1
                click.reset()
                continue
            if equipment_clicks and self.match_template_color(GET_ITEMS_1, offset=(30, 30), similarity=SIMILARITY):
                empty_reads = 0
                empty.reset()
                if receipts >= RECEIPT_RETRIES:
                    self.save_enhance_stall('receipt_stalled')
                    raise ScriptError('Enhancement equipment receipt did not close')
                self.device.click(GET_ITEMS_1_RETIREMENT_SAVE)
                receipts += 1
                click.reset()
                continue
            if popups >= POPUP_RETRIES:
                self.save_enhance_stall('popup_limit')
                raise ScriptError('Enhancement confirmation limit reached')
            if self.handle_popup_confirm_loose('ENHANCE'):
                empty_reads = 0
                empty.reset()
                popups += 1
                click.reset()
                continue
            if self.is_enhance_tab():
                if self.is_enhance_slot_empty():
                    empty_reads += 1
                    if empty_reads >= 2 and empty.reached():
                        logger.info(f'Enhanced with {picked} materials')
                        return picked
                    continue
                empty_reads = 0
                empty.reset()
                if retry.reached():
                    if retries >= ENHANCE_RETRIES or equipment_clicks:
                        self.save_enhance_stall('materials_not_consumed')
                        logger.warning('Enhancement did not consume its materials; no more retries')
                        return 0
                    self.device.click(ENHANCE_BUTTON)
                    retries += 1
                    retry.reset()
                    click.reset()
                continue
            empty_reads = 0
            empty.reset()
            # Unknown pages are captured, never tapped. An equipped material used to leave this loop
            # clicking the disassembly dialog's empty body until the global click watchdog fired.
        self.save_enhance_stall('completion_timeout')
        raise ScriptError('Enhancement did not return to a recognized empty tab')

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
        census = self.enhance_census(matcher)
        used = 0
        for slot, name in members:
            used += self.enhance_ship(slot, name, census, matcher)
        logger.info(f'Enhance: {used} materials used')
        return used
