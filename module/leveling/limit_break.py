"""
Limit break the ships of the fleets that the campaign uses, with the material the game's Quick Select picks.

The user's rules for the F2P account, 2026-10-21: "Remember to limit break ships as needed!", "It's urgent to enhance
ships and limit break them in the f2p account because its fleet strength is so low." Spending Bulins is fine.

A limit break raises the level cap by 10, from 70 with none to 100 with three: Long Island stopped at 70.
The game asks for levels 10, 30 and 70, and coins. Quick Select puts a copy of the ship in the material slot, or a
Bulin, and its pick is confirmed as it is, not checked. The copies of a ship in the dock are how many tries she gets:
a ship without a copy is not tried, and the material used up may still be a Bulin.
An Elite or higher material asks "You've selected a Elite and above ship. Continue?", which is confirmed.

Pages:
    in: Any page
    out: page_fleet, its 3D view
"""
import cv2

from module.base.timer import Timer
from module.base.utils import crop, load_image
from module.equipment.assets import FLEET_DETAIL_CHECK
from module.leveling.enhance import FleetEnhance
from module.leveling.fleet import area_button, card_name_area, name_fits, search_term
from module.leveling.plan import ShipMatcher, load_ship_data
from module.logger import logger
from module.ocr.ocr import Ocr
from module.retire.scanner import ShipScanner
from module.ui.page import page_dock, page_fleet, page_main

# The limit break tab of the detail page of a ship: Quick Select and Confirm under its material slot
QUICK_SELECT = './assets/leveling/limit_break_quick_select.png'
QUICK_SELECT_AREA = (806, 625, 966, 700)
QUICK_SELECT_BUTTON = area_button((826, 645, 946, 680), 'LIMIT_BREAK_QUICK_SELECT')
CONFIRM = './assets/leveling/limit_break_confirm.png'
CONFIRM_AREA = (1120, 625, 1272, 698)
CONFIRM_BUTTON = area_button((1140, 645, 1252, 678), 'LIMIT_BREAK_CONFIRM')
# The "+" of the material slot, 1.0 empty and 0.35 with a copy in it
SLOT_EMPTY = './assets/leveling/limit_break_slot_empty.png'
SLOT_AREA = (835, 508, 937, 610)
SIMILARITY = 0.85
# Confirm is orange when the game takes it
CONFIRM_ORANGE = 60
# Stats of the tab, no button under them: a tap there goes through the screens a limit break shows
SAFE_TAP = area_button((990, 292, 1010, 308), 'LIMIT_BREAK_SAFE')
# After Confirm: "LIMIT BREAK" over the stats before and after, a tap closes it. It has no back arrow.
# The slot of the tab empties the moment Confirm is taken, before this shows.
RESULT = './assets/leveling/limit_break_result.png'
RESULT_AREA = (660, 110, 1095, 265)
# Seconds on the tab with the slot emptied and no result shown, before the limit break is taken as done
RESULT_WAIT = 5
# A ship is limit broken 3 times at most
LIMIT_BREAK_MAX = 3
# Seconds for Quick Select to fill the slot, and for a limit break to come back to the tab
SELECT_WAIT = 3
BREAK_TIMEOUT = 30


# The Bulin that stands in for a copy of a ship, by her rarity before a retrofit (wiki: Dockyard)
BULINS = {
    'Normal': 'Universal Bulin',
    'Rare': 'Universal Bulin',
    'Elite': 'Universal Bulin',
    'Super Rare': 'Prototype Bulin MKII',
    'Ultra Rare': 'Specialized Bulin Custom MKIII',
}


def bulin_for(data):
    """
    Args:
        data (ShipData): A ship, None if not known

    Returns:
        str: Name of the Bulin her limit breaks can use, '' for none: a research or META ship, or a Bulin
    """
    if data is None or 'Bulin' in data.name:
        return ''
    if data.name.endswith(' (Retrofit)'):
        return ''
    return BULINS.get(data.rarity, '')


def template_score(image, template, area):
    """
    Returns:
        float: Best similarity of the template within the area
    """
    region = crop(image, area, copy=False)
    return float(cv2.minMaxLoc(cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED))[1])


def confirm_orange(image):
    """
    Returns:
        bool: If Confirm of the limit break tab is lit
    """
    r, g, b = cv2.mean(crop(image, CONFIRM_BUTTON.area, copy=False))[:3]
    return r - b > CONFIRM_ORANGE


class LimitBreak(FleetEnhance):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._templates = {}

    def _template(self, file):
        if file not in self._templates:
            self._templates[file] = load_image(file)
        return self._templates[file]

    def is_limit_break_tab(self):
        return template_score(self.device.image, self._template(QUICK_SELECT), QUICK_SELECT_AREA) > SIMILARITY

    def is_slot_empty(self):
        return template_score(self.device.image, self._template(SLOT_EMPTY), SLOT_AREA) > SIMILARITY

    def is_limit_break_result(self):
        return template_score(self.device.image, self._template(RESULT), RESULT_AREA) > SIMILARITY

    def limit_break_cards(self, name):
        """
        Args:
            name (str): Name of a ship on the wiki

        Returns:
            list[Ship]: Her cards in the dock, highest level first, those in fleets too

        Pages:
            in: page_dock
            out: page_dock, the ships found
        """
        if not self.dock_search(name):
            return []
        scanner = ShipScanner(fleet=(0, 6))
        scanner.disable('emotion', 'rarity')
        ships = scanner.scan(self.device.image, output=False)
        if not ships:
            # The dock may not have drawn the ships found yet
            self.device.sleep(1)
            self.device.screenshot()
            ships = scanner.scan(self.device.image, output=False)
        if not ships:
            return []
        buttons = [area_button(card_name_area(ship.button), f'CARD_NAME_{i}') for i, ship in enumerate(ships)]
        names = Ocr(buttons, lang='cnocr', letter=(255, 255, 255), threshold=128, name='CARD_NAME').ocr(
            self.device.image)
        if isinstance(names, str):
            names = [names]
        fits = [ship for ship, read in zip(ships, names) if name_fits(read, name)]
        logger.info(f'{name}: {len(fits)} in the dock, levels {[ship.level for ship in fits]}')
        return sorted(fits, key=lambda ship: -ship.level)

    def limit_break_once(self):
        """
        Returns:
            bool: If the material in the slot was used up, a copy or a Bulin

        Pages:
            in: limit break tab
            out: limit break tab
        """
        # Quick Select puts a copy or a Bulin in the slot. An Elite one asks to continue first, and its popup
        # covers the slot and Confirm: Laffey's copy was taken as no material, Confirm as not lit.
        timeout = Timer(SELECT_WAIT, count=6).start()
        clicked = False
        for _ in self.loop():
            if timeout.reached():
                logger.info('No material for a limit break')
                return False
            if self.handle_popup_confirm_loose('LIMIT_BREAK_MATERIAL'):
                timeout.reset()
                continue
            if not self.is_limit_break_tab():
                continue
            if not self.is_slot_empty():
                break
            if not clicked:
                self.device.click(QUICK_SELECT_BUTTON)
                clicked = True
        if not confirm_orange(self.device.image):
            logger.info('Confirm is not lit, the level or the coins are short')
            return False
        self.device.click(CONFIRM_BUTTON)
        # A popup to confirm maybe, the result, a tap closes it, then the tab with its slot empty
        timeout = Timer(BREAK_TIMEOUT, count=60).start()
        tap = Timer(2)
        retry = Timer(3, count=6).start()
        emptied = Timer(RESULT_WAIT, count=10)
        result = False
        for _ in self.loop():
            if timeout.reached():
                logger.warning('The limit break did not come back to its tab')
                return result
            if self.is_limit_break_result():
                result = True
                if tap.reached():
                    self.device.click(SAFE_TAP)
                    tap.reset()
                continue
            if self.handle_popup_confirm_loose('LIMIT_BREAK'):
                continue
            if self.is_limit_break_tab():
                if self.is_slot_empty():
                    if result:
                        return True
                    # The material is taken, the result comes after it
                    if not emptied.started():
                        emptied.start()
                    if emptied.reached():
                        logger.info('The material was taken, no result shown')
                        return True
                    continue
                emptied.clear()
                if result and tap.reached():
                    logger.warning('Back on the tab with the material still in the slot')
                    return True
                if retry.reached():
                    # The tap on Confirm did not get through
                    self.device.click(CONFIRM_BUTTON)
                    retry.reset()
                continue
            # Another screen of the limit break
            if tap.reached():
                self.device.click(SAFE_TAP)
                tap.reset()

    def limit_break_ship(self, slot, name, matcher):
        """
        Args:
            slot (Slot): Of a fleet, her ship is the one limit broken
            name (str): Her name on the wiki
            matcher (ShipMatcher):

        Returns:
            int: Limit breaks done

        Pages:
            in: Any page
            out: page_fleet, its Info view
        """
        logger.hr(f'Limit break {name}', level=2)
        # The search bar keeps the name typed before, "Nevada" went after "Long Island" and found nothing:
        # the dock is entered again for each search
        self.ui_ensure(page_main)
        self.ui_ensure(page_dock)
        cards = self.limit_break_cards(name)
        copies = len(cards) - 1
        bulins = 0
        bulin = bulin_for(matcher.ships.get(name))
        if bulin:
            # The user, 2026-10-21: "Spending bulins is fine". Quick Select takes a copy first, then a Bulin.
            # A Bulin in a fleet is not offered, like Fleet 2's Universal Bulin of the F2P account.
            self.ui_ensure(page_main)
            self.ui_ensure(page_dock)
            bulins = len([card for card in self.limit_break_cards(bulin) if not card.fleet])
        if copies + bulins < 1:
            logger.info(f'{name} has no copy and no {bulin or "Bulin"} free in the dock, not tried')
            return 0
        copies += bulins
        # The ship of the slot is opened from the fleet: the highest card of her name can be an other copy of her,
        # in an other fleet or in the dock (code review, Codex)
        self.ui_ensure(page_fleet)
        self.formation_goto_fleet(slot.fleet)
        self.fleet_ship_enter(slot)
        # Detail tab 1, gear 2, limit break 3 from the bottom, with or without a retrofit tab on top
        self.ship_side_navbar_ensure(bottom=3)
        self.device.sleep(1)
        self.device.screenshot()
        done = 0
        if not self.detail_name_fits(name, matcher):
            pass
        elif self.is_limit_break_tab():
            for _ in range(min(copies, LIMIT_BREAK_MAX)):
                if not self.limit_break_once():
                    break
                done += 1
                logger.info(f'{name} limit broken, {done} time(s)')
        else:
            logger.info(f'{name} has no limit break tab, fully limit broken or a research ship')
        # A popup left open takes the tap on the back arrow
        for _ in range(3):
            self.device.screenshot()
            if not self.handle_popup_cancel_loose('LIMIT_BREAK_LEAVE'):
                break
            self.device.sleep(1)
        self.ui_back(check_button=FLEET_DETAIL_CHECK)
        self.fleet_formation_ensure()
        return done

    def run(self):
        matcher = ShipMatcher(load_ship_data())
        self.ui_ensure(page_fleet)
        members = []
        for fleet in self.formation_fleets():
            self.formation_goto_fleet(fleet)
            for slot in self.fleet_read_slots(fleet):
                data = matcher.match(slot.name) if slot.name else None
                if data is None:
                    if slot.name:
                        logger.warning(f'Fleet {fleet}: {slot.name} is no ship known')
                    continue
                if not search_term(data.name):
                    continue
                members.append((slot, data.name))
        self.fleet_formation_ensure()
        total = 0
        if members:
            for slot, name in members:
                total += self.limit_break_ship(slot, name, matcher)
        logger.info(f'Limit breaks done: {total}')
        # Then enhancement, with ships no limit break needs: the copies just used are gone from the census
        self.enhance_fleet(members, matcher)
        self.ui_ensure(page_fleet)
        self.fleet_formation_ensure()
        self.config.task_delay(success=True)
