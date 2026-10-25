"""Fresh, read-only EXP-cap checks before filling the dorm with owned food."""
from types import SimpleNamespace
from functools import lru_cache

from module.inventory.info import InfoReader, parse_exp
from module.inventory.inventory import Inventory
from module.leveling.fleet import LevelingFleet, area_button, card_name_area
from module.leveling.plan import load_ship_data
from module.logger import logger
from module.mood.dorm_rest import (DIALOG_SLOTS, DormReadError, DormRest, OCR_DORM_SHIP_COUNT,
                                   read_dorm_floor)
from module.mood.ship import name_key
from module.ocr.ocr import Ocr
from module.retire.scanner import ShipScanner
from module.ui.page import page_dock, page_dorm

CENSUS_READS = 4
AWAKENING_LEVELS = (100, 105, 110, 115, 120)


@lru_cache(maxsize=1)
def short_ship_names():
    return frozenset(name_key(name) for name in load_ship_data() if len(name_key(name)) == 2)


def valid_name(key):
    return len(key) >= 3 or key in short_ship_names()


def census_key(ships, count):
    """Accept a complete occupied-card read, including an explicitly empty floor."""
    if not isinstance(count, (tuple, list)) or len(count) != 3:
        return None
    current, vacant, total = count
    if (any(type(value) is not int for value in count)
            or not 0 <= current <= total <= DIALOG_SLOTS or total <= 0
            or vacant < 0 or current + vacant != total or len(ships) != current):
        return None
    if any(type(ship.level) is not int or not 1 <= ship.level <= 125
           or not valid_name(name_key(ship.name)) for ship in ships):
        return None
    return tuple((name_key(ship.name), ship.level) for ship in ships), tuple(count)


def needs_exp_read(level):
    return level in AWAKENING_LEVELS


def level_can_gain_exp(level):
    """Prove awakening and room for EXP without any saved inventory data.

    A dorm level can lose its leading 1 (101 reads as 11), so a low read never
    authorizes food. It either is unawakened or cannot be trusted for this gate.
    """
    return type(level) is int and 101 <= level < 125 and not needs_exp_read(level)


class DormAwakening(DormRest, LevelingFleet, Inventory):
    """Inspects both floors and, at awakening boundaries, the actual ship Info tab.

    This helper never changes residents, awakens ships, buys food, or writes inventory.
    Its caller retains control over feeding and the separate one-snack daily mission.
    """

    def _food_floor(self, place):
        self.dorm_ship_list_set_tab(place)
        previous = None
        for attempt in range(CENSUS_READS):
            # Always take a new frame, even immediately after changing the tab.
            if attempt:
                self.device.sleep(.5)
            self.device.screenshot()
            ships, _ = read_dorm_floor(self.device.image, place)
            count = OCR_DORM_SHIP_COUNT.ocr(self.device.image)
            current = census_key(ships, count)
            if current is not None and current == previous:
                logger.info(f'Dorm food {place}: {[(ship.name, ship.level) for ship in ships]}, '
                            f'occupied {count[0]}/{count[2]}')
                return ships
            previous = current
        raise DormReadError(f'Dorm food {place}: no two complete agreeing resident reads')

    def _food_residents(self):
        self.ui_ensure(page_dorm)
        self.dorm_ship_list_enter()
        try:
            train = self._food_floor('train')
            rest = self._food_floor('rest')
        finally:
            # A denied census still needs to close this modal before ui_ensure.
            self.dorm_ship_list_quit()
        if not train:
            raise DormReadError('Dorm food: training floor is empty')
        return train + rest

    def _food_search_cards(self):
        # Retain all positions, including unreadable levels, so an unread duplicate
        # cannot disappear through the scanner's ordinary level/fleet filters.
        scanner = ShipScanner(fleet=None, status='any')
        scanner.disable('emotion', 'rarity', 'fleet', 'status')
        scanner.set_limitation(level=None)
        ships = scanner.scan(self.device.image, output=False)
        buttons = [area_button(card_name_area(ship.button), f'DORM_FOOD_CARD_NAME_{index}')
                   for index, ship in enumerate(ships)]
        names = Ocr(buttons, lang='cnocr', letter=(255, 255, 255), threshold=128,
                    name='DORM_FOOD_CARD_NAME').ocr(self.device.image)
        if isinstance(names, str):
            names = [names]
        if len(names) != len(ships):
            raise DormReadError('Dorm food: incomplete dock names')
        result = []
        for index, (ship, name) in enumerate(zip(ships, names)):
            key = name_key(name)
            if not ship.level and not key:
                continue
            if not 1 <= (ship.level or 0) <= 125 or not valid_name(key):
                raise DormReadError('Dorm food: unreadable search-result card')
            result.append((ship, key, index))
        # A full screen cannot prove there is no other same-level copy below it.
        if len(result) >= len(ships):
            raise DormReadError('Dorm food: search results may continue below this screen')
        return result

    def _food_boundary_card(self, resident):
        # Leaving the dock clears any earlier name search before Android types.
        self.ui_ensure(page_dorm)
        self.ui_ensure(page_dock)
        # Each search starts from a clean dock; Android input otherwise appends to
        # the preceding term. These are view filters only, never selection changes.
        self.dock_reset()
        if not self.dock_search(resident.name):
            raise DormReadError(f'Dorm food: could not search {resident.name}')
        previous = None
        for _ in range(3):
            self.device.sleep(.5)
            self.device.screenshot()
            cards = self._food_search_cards()
            current = tuple((name, ship.level, tuple(ship.button.area)) for ship, name, _ in cards)
            if current == previous:
                fits = [(ship, index) for ship, name, index in cards
                        if name == name_key(resident.name) and ship.level == resident.level]
                if len(fits) != 1:
                    raise DormReadError(f'Dorm food: {resident.name} Lv.{resident.level} '
                                        f'has {len(fits)} exact matching copies')
                ship, index = fits[0]
                return SimpleNamespace(button=ship.button, column=index % 7, top=ship.button.area[1])
            previous = current
        raise DormReadError(f'Dorm food: search for {resident.name} did not settle')

    def _food_boundary_exp(self, resident):
        card = self._food_boundary_card(resident)
        reader = InfoReader()
        previous = None
        for _ in range(2):
            # The inventory reader waits for the Info tab to settle, records the
            # scrolling name, and returns to the same dock without editing a ship.
            taken = self.inventory_open_card(card)
            if taken is None:
                raise DormReadError(f'Dorm food: no fresh Info page for {resident.name}')
            image, scroll, _ = taken
            name, _ = reader.read_name(image, reader.read_affinity(image), scroll)
            level = reader.ocr_level.ocr(image)
            exp, next_exp = parse_exp(reader.ocr_exp.ocr(image))
            if name_key(name) != name_key(resident.name) or level != resident.level:
                raise DormReadError(f'Dorm food: detail identity changed for {resident.name}')
            if (type(exp) is not int or type(next_exp) is not int
                    or next_exp <= 0 or not 0 <= exp < next_exp):
                logger.info(f'Dorm food: {resident.name} Lv.{level} is capped or EXP is unreadable')
                return False
            current = (name_key(name), level, exp, next_exp)
            if previous is not None and current != previous:
                raise DormReadError(f'Dorm food: EXP read for {resident.name} did not agree')
            previous = current
        logger.info(f'Dorm food: {resident.name} Lv.{resident.level} has room for EXP')
        return True

    def can_feed(self):
        """Permit food only with awakened, EXP-active residents on both floors."""
        try:
            residents = self._food_residents()
            # Read all residents before opening any details: one unawakened or
            # ambiguous low level, or one fully capped ship, already forbids food.
            if any(type(ship.level) is not int or not 100 <= ship.level < 125 for ship in residents):
                logger.info('Dorm food: a resident is unawakened, capped at 125, or has an uncertain low level')
                return False
            for resident in residents:
                if level_can_gain_exp(resident.level):
                    continue
                if not needs_exp_read(resident.level):
                    logger.info(f'Dorm food: {resident.name} Lv.{resident.level} cannot gain EXP')
                    return False
                if not self._food_boundary_exp(resident):
                    return False
            # Detail checks leave the dorm for the dock. A person may have moved
            # ships meanwhile; only the same fresh census may authorize food.
            final = self._food_residents()
            identity = lambda ships: [(ship.place, name_key(ship.name), ship.level) for ship in ships]
            if identity(final) != identity(residents):
                raise DormReadError('Dorm food: residents changed during awakening checks')
            logger.info('Dorm food: every resident is awakened with room for EXP; owned food may fill the store')
            return True
        except DormReadError as exc:
            logger.warning(f'Dorm food stays unchanged: {exc}')
            return False
        finally:
            self.ui_ensure(page_dorm)
