"""
Gear from storage for a ship that comes into a fleet without gear worth the name.

On the detail page of a ship, the Info tab shows her 5 gear slots and her augment, their enhancement and the rarity
of her gear by its color. On the Gear tab, a tap on an empty slot opens the depot with the gear that fits it, the
rarest first. A tap on gear opens its popup instead, its name and stats, whose Change button opens the depot with a
REMOVE card first. The EQUIPPED switch of the depot lists the gear other ships wear too: it is turned off, so only
gear in storage is listed and no ship loses hers. Gear picked there shows its popup beside the gear it replaces,
with Cancel and Confirm.

What goes in a slot is the choice of module.leveling.policy: gold weapons at +10, auxiliaries for her hull type,
nothing against submarines. Gear she wears stays if the policy keeps it, else the depot is looked through: the
popups of its likely best cards are read, MAX_LOOKS of them at most, and the best one goes on if it beats what she
has. Her enhancements are read again at the end.
The augment is the sixth slot, the user's rule wants it at +10 too, any that fits. A tap on it empty opens the depot
of augments: a "Craft" section first, the augments in storage under a "Depot" header, with the same EQUIPPED switch,
tags on the cards and Confirm as the depot of gear.
A step that does not go as planned comes back to the Gear tab, closing what it opened.
"""
import cv2
import numpy as np

from module.base.decorator import cached_property
from module.base.utils import crop, load_image
from module.equipment.assets import (EQUIP_TAKE_ON_0, EQUIP_TAKE_ON_1, EQUIP_TAKE_ON_2, EQUIP_TAKE_ON_3,
                                     EQUIP_TAKE_ON_4, EQUIPMENT_OPEN, SHIP_INFO_EQUIPMENT_CHECK)
from module.equipment.equipment_change import EQUIPMENT_GRID
from module.inventory.info import AUGMENT_LEVEL_AREA, GEAR_LEVEL_AREAS, parse_trailing_number
from module.leveling.fleet import LevelingFleet, area_button
from module.leveling.plan import GEARED_LEVEL, gear_record
from module.leveling.policy import AUX_SLOTS, MAX_LOOKS, WEAPON_SLOTS, card_item, card_order, describe, gear_good, \
    gear_ideal, gear_item, gear_passes, gear_score, is_better, parse_stats, rarity_of
from module.logger import logger
from module.ocr.ocr import Digit, Ocr
from module.retire.assets import DOCK_CHECK
from module.ui.assets import BACK_ARROW
from module.ui.page import page_dock

DEPOT_TITLE = './assets/leveling/depot_title.png'
DEPOT_TITLE_AREA = (90, 0, 240, 60)
DEPOT_SIMILARITY = 0.85
# The EQUIPPED switch of the depot, on or off
EQUIPPED_ON = './assets/leveling/equipped_on.png'
EQUIPPED_OFF = './assets/leveling/equipped_off.png'
EQUIPPED_AREA = (0, 480, 110, 600)
EQUIPPED_SWITCH = area_button((14, 510, 76, 566), 'EQUIPPED_SWITCH')
# Cards of the depot, 7 a row and 3 rows on screen, the rarest first. A card is bright and busy, the background
# without one dark and plain. The enhancement of a gear is on a tag at the middle of the left side of its card.
DEPOT_CARD_LEFTS = [137 + round(157.9 * column) for column in range(7)]
DEPOT_CARD_TOPS = [84 + 178 * row for row in range(3)]
DEPOT_CARDS = [area_button((x + 6, y + 6, x + 124, y + 126), f'DEPOT_CARD_{row}_{column}')
               for row, y in enumerate(DEPOT_CARD_TOPS) for column, x in enumerate(DEPOT_CARD_LEFTS)]
DEPOT_TAGS = [area_button((x + 2, y + 80, x + 48, y + 102), f'DEPOT_TAG_{row}_{column}')
              for row, y in enumerate(DEPOT_CARD_TOPS) for column, x in enumerate(DEPOT_CARD_LEFTS)]
FIRST_ITEM = DEPOT_CARDS[0]
# What Digit reads for the plus of a tag, or for the point at its right
TAG_MISREAD = '41'
ITEM_MEAN = 100
ITEM_STD = 30
# Gear slots of the Info tab, their enhancement
INFO_GEAR_LEVELS = [area_button(area, f'INFO_GEAR_LEVEL_{index}') for index, area in enumerate(GEAR_LEVEL_AREAS)]
# A gear slot of the Info tab is empty below this sharpness, gear at +0 is about 30
EMPTY_SLOT_EDGE = 10
GEAR_SLOTS = [EQUIP_TAKE_ON_0, EQUIP_TAKE_ON_1, EQUIP_TAKE_ON_2, EQUIP_TAKE_ON_3, EQUIP_TAKE_ON_4]
# The augment, index 5 of the slots, below the ship on the Gear tab, and its enhancement on the Info tab
AUGMENT_INDEX = 5
AUGMENT_SLOT = area_button((428, 595, 498, 660), 'AUGMENT_SLOT')
INFO_AUGMENT_LEVEL = area_button(AUGMENT_LEVEL_AREA, 'INFO_AUGMENT_LEVEL')
# The "Depot" header of the depot of augments, its cards start this far below its top, a row every 178 pixels.
# The list ends above the bottom bar.
AUGMENT_HEADER = './assets/leveling/augment_depot_header.png'
AUGMENT_HEADER_AREA = (128, 60, 300, 640)
AUGMENT_CARD_LEFTS = [131 + round(156.5 * column) for column in range(7)]
AUGMENT_ROW_OFFSET = 53
AUGMENT_LIST_BOTTOM = 640
# The popup of gear in a slot: Unequip, Change, Enhance. A tap below it, out of it, closes it.
GEAR_CHANGE = './assets/leveling/gear_change.png'
GEAR_CHANGE_AREA = (540, 600, 740, 700)
GEAR_CHANGE_BUTTON = area_button((562, 628, 717, 672), 'GEAR_CHANGE')
GEAR_POPUP_CLOSE = area_button((180, 630, 230, 670), 'GEAR_POPUP_CLOSE')
# Gear picked in the depot, beside what the slot has: Cancel, Confirm
GEAR_CONFIRM = './assets/leveling/gear_confirm.png'
GEAR_CONFIRM_AREA = (770, 600, 980, 700)
GEAR_CONFIRM_BUTTON = area_button((800, 630, 952, 670), 'GEAR_CONFIRM')
GEAR_CANCEL_BUTTON = area_button((328, 630, 482, 670), 'GEAR_CANCEL')
TEMPLATE_SIMILARITY = 0.85
# Seconds for each step
GEAR_TIMEOUT = 10
# Icons of her gear on the Info tab, their background is the color of their rarity
INFO_GEAR_ICON_LEFTS = [696 + 85 * index for index in range(5)]
INFO_GEAR_ICON_TOP = 129
# Popups of gear: gear picked in the depot is shown on the right, beside what the slot has, gear in a slot in the
# middle. Its name is on top, a long one scrolls and can be cut at both ends. Its stats are rows under its picture,
# the label on the left and the amount on the right, then come its skills. Gear picked to replace other gear lists
# the stats of the other gear first, at 0.
POPUP_PANELS = {'confirm': (698, 1052), 'worn': (462, 818)}
POPUP_TITLES = {'confirm': (692, 92, 1058, 122), 'worn': (482, 92, 818, 122)}
POPUP_ROWS = 7
POPUP_ROW_TOP = 278
POPUP_ROW_HEIGHT = 36
POPUP_VALUE_COLOR = (200, 175, 50)
# Seconds for a popup to fade in
POPUP_SETTLE = 0.4
# Seconds for the list of a depot to show, after its title
AUGMENT_WAIT = 3
DEPOT_LIST_WAIT = 2


def gray(image):
    return cv2.cvtColor(image, cv2.COLOR_RGB2GRAY) if image.ndim == 3 else image


def template_score(image, template, area):
    """
    Returns:
        float: Best similarity of the template within the area
    """
    region = gray(crop(image, area, copy=False))
    return float(cv2.minMaxLoc(cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED))[1])


def empty_gear_slots(image):
    """
    Args:
        image (np.ndarray): Info tab of the detail page of a ship

    Returns:
        list[int]: Gear slots without gear, 0 to 4
    """
    empty = []
    for index, button in enumerate(EQUIPMENT_GRID.buttons):
        area = crop(image, button.area, copy=False)
        if np.mean(np.abs(cv2.Sobel(area, 3, 1, 1))) < EMPTY_SLOT_EDGE:
            empty.append(index)
    return empty


def is_card(image, button):
    """
    Returns:
        bool: If a card of a gear is there, bright, not dimmed nor missing
    """
    area = crop(image, button.area, copy=False)
    return bool(area.mean() > ITEM_MEAN and area.std() > ITEM_STD)


def depot_has_item(image):
    """
    Returns:
        bool: If the depot lists an item first, False when storage has nothing for the slot
    """
    return is_card(image, FIRST_ITEM)


def gear_levels(image):
    """
    Args:
        image (np.ndarray): Info tab of the detail page of a ship

    Returns:
        list[int]: Enhancement of her 5 gear slots, 0 for gear at +0 and for an empty slot
    """
    texts = Ocr(INFO_GEAR_LEVELS, letter=(255, 255, 255), threshold=128, name='GEAR_LEVEL').ocr(image)
    return [parse_trailing_number(str(text), maximum=13) for text in texts]


def tag_level(digit):
    """
    Args:
        digit (int): What Digit read on the tag of a card, "+10"

    Returns:
        int: Enhancement, 0 to 13. Digit can take the plus of the tag for a 4 or a 1, and the point at its right
            too: "+11" reads 411, "+10" 410, 104 or 101, "+6" 464. A tag read as none of these is taken as +0,
            rather than as the most enhanced gear.
    """
    if digit <= 13:
        return digit
    text = str(digit)
    point = text[-1] in TAG_MISREAD
    plus = text[0] in TAG_MISREAD
    candidates = []
    if point:
        candidates.append(text[:-1])
    if plus:
        candidates.append(text[1:])
    if point and plus:
        candidates.append(text[1:-1])
    for candidate in candidates:
        if candidate and int(candidate) <= 13:
            return int(candidate)
    return 0


def augment_depot_layout(top):
    """
    Args:
        top (int): Top of the "Depot" header of the depot of augments

    Returns:
        tuple[list[Button], list[Button]]: Cards of the augments in storage whole on screen, and their tags
    """
    cards, tags = [], []
    row, y = 0, top + AUGMENT_ROW_OFFSET
    while y + 130 <= AUGMENT_LIST_BOTTOM:
        for column, x in enumerate(AUGMENT_CARD_LEFTS):
            cards.append(area_button((x + 6, y + 6, x + 124, y + 126), f'AUGMENT_CARD_{row}_{column}'))
            tags.append(area_button((x + 2, y + 80, x + 48, y + 102), f'AUGMENT_TAG_{row}_{column}'))
        row, y = row + 1, y + 178
    return cards, tags


def depot_card_levels(image, cards=None, tags=None):
    """
    Args:
        image (np.ndarray): The depot of a slot
        cards (list[Button]): Cards on screen, DEPOT_CARDS of the depot of gear if None
        tags (list[Button]): Their tags, DEPOT_TAGS if None

    Returns:
        list[int]: Enhancement of each card on screen, -1 where there is no card, a dimmed one or the REMOVE card.
            A digit read where a card has no tag is dropped, a tag reads "+10".
    """
    if cards is None:
        cards, tags = DEPOT_CARDS, DEPOT_TAGS
    if not cards:
        return []
    digits = Digit(tags, letter=(255, 255, 255), threshold=128, name='DEPOT_TAG').ocr(image)
    texts = Ocr(tags, lang='cnocr', letter=(255, 255, 255), threshold=128, name='DEPOT_TAG_TEXT').ocr(image)
    # OCR of a single button gives its result alone
    if not isinstance(digits, list):
        digits, texts = [digits], [texts]
    levels = []
    for card, digit, text in zip(cards, digits, texts):
        if not is_card(image, card):
            levels.append(-1)
        elif '+' in str(text):
            levels.append(tag_level(digit))
        else:
            levels.append(0)
    return levels


def card_names(cards):
    """
    Args:
        cards (list[Button]): Cards of a depot, DEPOT_CARDS or augment_depot_layout()

    Returns:
        list[Button]: Where the name of each card is, under it, cut by the game, "Repair To.."
    """
    return [area_button((card.area[0] - 8, card.area[1] + 130, card.area[0] + 130, card.area[1] + 157),
                        f'{card.name}_NAME') for card in cards]


def card_rarity(image, card):
    """
    Args:
        image (np.ndarray):
        card (Button): A card of a depot

    Returns:
        str: Rarity by the color of its top left corner, see module.leveling.policy
    """
    x, y = card.area[0], card.area[1]
    color = crop(image, (x + 4, y + 4, x + 24, y + 24), copy=False).reshape(-1, 3).mean(axis=0)
    return rarity_of(tuple(float(value) for value in color))


def depot_cards_read(image, cards=None, tags=None):
    """
    Args:
        image (np.ndarray): The depot of a slot
        cards (list[Button]): Cards on screen, DEPOT_CARDS if None
        tags (list[Button]): Their tags

    Returns:
        list[dict]: Each card that is there: index, level, rarity, name as cut by the game
    """
    if cards is None:
        cards, tags = DEPOT_CARDS, DEPOT_TAGS
    levels = depot_card_levels(image, cards, tags)
    present = [index for index, level in enumerate(levels) if level >= 0]
    if not present:
        return []
    buttons = card_names([cards[index] for index in present])
    names = Ocr(buttons, lang='cnocr', letter=(255, 255, 255), threshold=128, name='CARD_NAME').ocr(image)
    if not isinstance(names, list):
        names = [names]
    return [{'index': index, 'level': levels[index], 'rarity': card_rarity(image, cards[index]),
             'name': str(name).strip()} for index, name in zip(present, names)]


def best_card(levels):
    """
    Args:
        levels (list[int]): depot_card_levels()

    Returns:
        int: Index of the most enhanced card, the rarest of them, -1 if there is none
    """
    best = -1
    for index, level in enumerate(levels):
        if level >= 0 and (best < 0 or level > levels[best]):
            best = index
    return best


def gear_rarities(image, empty=()):
    """
    Args:
        image (np.ndarray): Info tab of the detail page of a ship
        empty (iterable[int]): Empty slots, empty_gear_slots()

    Returns:
        list[str]: Rarity of the gear in her 5 slots, by the background of its icon, None for an empty slot
    """
    rarities = []
    for index, x in enumerate(INFO_GEAR_ICON_LEFTS):
        if index in empty:
            rarities.append(None)
            continue
        area = (x + 4, INFO_GEAR_ICON_TOP + 3, x + 20, INFO_GEAR_ICON_TOP + 13)
        color = crop(image, area, copy=False).reshape(-1, 3).mean(axis=0)
        rarities.append(rarity_of(tuple(float(value) for value in color)))
    return rarities


def popup_buttons(panel):
    """
    Args:
        panel (str): 'confirm' for gear picked in the depot, 'worn' for gear in a slot

    Returns:
        tuple[Button, list[Button], list[Button]]: Its name, the labels and the amounts of its rows
    """
    left, right = POPUP_PANELS[panel]
    title = area_button(POPUP_TITLES[panel], f'POPUP_TITLE_{panel}')
    labels, values = [], []
    for row in range(POPUP_ROWS):
        top = POPUP_ROW_TOP + POPUP_ROW_HEIGHT * row
        labels.append(area_button((left + 6, top, left + 190, top + 28), f'POPUP_LABEL_{row}'))
        values.append(area_button((right - 110, top, right - 8, top + 28), f'POPUP_VALUE_{row}'))
    return title, labels, values


def popup_read(image, panel):
    """
    Args:
        image (np.ndarray):
        panel (str): 'confirm' or 'worn', see popup_buttons()

    Returns:
        dict: Gear of the popup, its name and stats, see module.leveling.policy
    """
    title, labels, values = popup_buttons(panel)
    name = Ocr([title], lang='cnocr', letter=(255, 255, 255), threshold=128, name='POPUP_TITLE').ocr(image)
    texts = Ocr(labels, lang='cnocr', letter=(255, 255, 255), threshold=128, name='POPUP_LABEL').ocr(image)
    amounts = Digit(values, letter=POPUP_VALUE_COLOR, threshold=128, name='POPUP_VALUE').ocr(image)
    return gear_item(name=str(name).strip(), stats=parse_stats(texts, amounts))


class LevelingGear(LevelingFleet):
    @cached_property
    def _depot_title(self):
        return gray(load_image(DEPOT_TITLE))

    @cached_property
    def _equipped_on(self):
        return gray(load_image(EQUIPPED_ON))

    @cached_property
    def _equipped_off(self):
        return gray(load_image(EQUIPPED_OFF))

    @cached_property
    def _augment_header(self):
        return gray(load_image(AUGMENT_HEADER))

    def augment_header_top(self):
        """
        Returns:
            int: Top of the "Depot" header of the depot of augments, None if it is not on screen
        """
        region = gray(crop(self.device.image, AUGMENT_HEADER_AREA, copy=False))
        result = cv2.matchTemplate(region, self._augment_header, cv2.TM_CCOEFF_NORMED)
        _, score, _, location = cv2.minMaxLoc(result)
        if score < TEMPLATE_SIMILARITY:
            return None
        return AUGMENT_HEADER_AREA[1] + location[1]

    @cached_property
    def _gear_change(self):
        return gray(load_image(GEAR_CHANGE))

    @cached_property
    def _gear_confirm(self):
        return gray(load_image(GEAR_CONFIRM))

    def is_in_depot(self):
        return template_score(self.device.image, self._depot_title, DEPOT_TITLE_AREA) > DEPOT_SIMILARITY

    def is_gear_popup(self):
        """
        Returns:
            bool: If the popup of gear in a slot is shown, with its Change button
        """
        return template_score(self.device.image, self._gear_change, GEAR_CHANGE_AREA) > TEMPLATE_SIMILARITY

    def is_gear_confirm(self):
        """
        Returns:
            bool: If gear picked in the depot asks to Confirm
        """
        return template_score(self.device.image, self._gear_confirm, GEAR_CONFIRM_AREA) > TEMPLATE_SIMILARITY

    def is_gear_tab(self):
        """
        Returns:
            bool: If on the Gear tab of the detail page of a ship, no popup over it
        """
        return self.appear(SHIP_INFO_EQUIPMENT_CHECK, offset=(20, 20)) \
            and not self.is_gear_popup() and not self.is_gear_confirm()

    def depot_lists_equipped(self):
        """
        Returns:
            bool: If the depot lists the gear other ships wear too
        """
        on = template_score(self.device.image, self._equipped_on, EQUIPPED_AREA)
        off = template_score(self.device.image, self._equipped_off, EQUIPPED_AREA)
        return on > off

    def gear_click(self, button, interval=2):
        """
        Click a button, once in an interval.

        Returns:
            bool: If clicked
        """
        timer = self.get_interval_timer(button, interval=interval, renew=True)
        if not timer.reached():
            return False
        self.device.click(button)
        timer.reset()
        return True

    def gear_tab_ensure(self):
        """
        Back to the Gear tab: closes the popup of gear, cancels gear picked, leaves the depot.

        Returns:
            bool: If on the Gear tab

        Pages:
            in: the Gear tab, the popup of gear, the depot of a slot, gear picked there
            out: SHIP_INFO_EQUIPMENT_CHECK, the Gear tab
        """
        self.interval_clear([GEAR_CANCEL_BUTTON, GEAR_POPUP_CLOSE, BACK_ARROW])
        for _ in self.loop(timeout=GEAR_TIMEOUT):
            if self.is_gear_tab():
                return True
            if self.is_gear_confirm():
                self.gear_click(GEAR_CANCEL_BUTTON)
                continue
            if self.is_gear_popup():
                self.gear_click(GEAR_POPUP_CLOSE)
                continue
            if self.is_in_depot():
                self.gear_click(BACK_ARROW)
                continue
        logger.warning('Gear tab not found')
        return False

    def gear_depot_enter(self, index):
        """
        Open the depot of a gear slot. A tap on an empty slot opens it, a tap on gear opens its popup and Change the
        depot.

        Args:
            index (int): 0 to 4

        Returns:
            bool: If the depot opened, else back on the Gear tab

        Pages:
            in: SHIP_INFO_EQUIPMENT_CHECK, the Gear tab
            out: the depot of the slot
        """
        slot = AUGMENT_SLOT if index == AUGMENT_INDEX else GEAR_SLOTS[index]
        self.interval_clear([slot, GEAR_CHANGE_BUTTON])
        for _ in self.loop(timeout=GEAR_TIMEOUT):
            if self.is_in_depot():
                return True
            if self.is_gear_popup():
                self.gear_click(GEAR_CHANGE_BUTTON)
                continue
            if self.is_gear_tab():
                self.gear_click(slot)
                continue
        logger.warning(f'Depot of gear slot {index} did not open')
        self.gear_tab_ensure()
        return False

    def depot_storage_only(self):
        """
        Turn off the EQUIPPED switch of the depot, so it lists the gear in storage only.

        Returns:
            bool: If done, else back on the Gear tab

        Pages:
            in: the depot of a slot
            out: the depot of a slot, gear in storage only
        """
        self.interval_clear(EQUIPPED_SWITCH)
        for _ in self.loop(timeout=GEAR_TIMEOUT):
            if not self.depot_lists_equipped():
                break
            self.gear_click(EQUIPPED_SWITCH)
        else:
            logger.warning('The depot keeps listing gear worn by other ships')
            self.gear_tab_ensure()
            return False
        # The list is drawn again
        self.device.sleep(0.5)
        self.device.screenshot()
        return True

    def depot_list_wait(self, cards):
        """
        Wait for the cards of the depot to stop changing, the list shows a moment after the title.

        Args:
            cards (list[Button]):

        Returns:
            bool: If cards show, the same in two screenshots

        Pages:
            in: the depot of a slot
        """
        previous = None
        for _ in self.loop(timeout=DEPOT_LIST_WAIT):
            present = tuple(is_card(self.device.image, card) for card in cards)
            if any(present) and present == previous:
                return True
            previous = present
        return False

    def gear_card_open(self, card):
        """
        Pick gear of the depot, its popup shows beside the gear of the slot.

        Args:
            card (Button):

        Returns:
            bool: If the popup shows

        Pages:
            in: the depot of a slot
            out: gear picked, Cancel and Confirm
        """
        self.interval_clear(card)
        for _ in self.loop(timeout=GEAR_TIMEOUT):
            if self.is_gear_confirm():
                self.device.sleep(POPUP_SETTLE)
                self.device.screenshot()
                return True
            if self.is_in_depot():
                self.gear_click(card)
        logger.warning(f'{card} of the depot did not open')
        return False

    def gear_card_cancel(self):
        """
        Returns:
            bool: If back in the depot

        Pages:
            in: gear picked, Cancel and Confirm
            out: the depot of a slot
        """
        self.interval_clear(GEAR_CANCEL_BUTTON)
        for _ in self.loop(timeout=GEAR_TIMEOUT):
            if self.is_gear_confirm():
                self.gear_click(GEAR_CANCEL_BUTTON)
                continue
            if self.is_in_depot():
                return True
        logger.warning('Gear picked was not cancelled')
        return False

    def gear_card_confirm(self, index):
        """
        Args:
            index (int): Slot, for the log

        Returns:
            bool: If the gear went on

        Pages:
            in: gear picked, Cancel and Confirm
            out: SHIP_INFO_EQUIPMENT_CHECK, the Gear tab
        """
        self.interval_clear(GEAR_CONFIRM_BUTTON)
        for _ in self.loop(timeout=GEAR_TIMEOUT):
            if self.is_gear_tab():
                return True
            if self.is_gear_confirm():
                self.gear_click(GEAR_CONFIRM_BUTTON)
                continue
            if self.handle_popup_confirm('GEAR_EQUIP'):
                continue
        logger.warning(f'Gear slot {index} not confirmed')
        self.gear_tab_ensure()
        return False

    def gear_worn_read(self, index):
        """
        Read the gear in a slot on its popup.

        Args:
            index (int): 0 to 4

        Returns:
            dict: Its name and stats, see module.leveling.policy. None if the popup did not open.

        Pages:
            in: SHIP_INFO_EQUIPMENT_CHECK, the Gear tab
            out: SHIP_INFO_EQUIPMENT_CHECK, the Gear tab
        """
        slot = GEAR_SLOTS[index]
        self.interval_clear(slot)
        for _ in self.loop(timeout=GEAR_TIMEOUT):
            if self.is_gear_popup():
                break
            if self.is_gear_tab():
                self.gear_click(slot)
        else:
            logger.warning(f'Gear of slot {index} did not show its popup')
            self.gear_tab_ensure()
            return None
        self.device.sleep(POPUP_SETTLE)
        self.device.screenshot()
        item = popup_read(self.device.image, 'worn')
        self.gear_tab_ensure()
        return item

    def augment_slot_fill(self, current=None):
        """
        Put the most enhanced augment of storage that fits on her.

        Args:
            current (int): Enhancement of her augment, None for none.
                It is only replaced by one at GEARED_LEVEL or above, more enhanced.

        Returns:
            int: Enhancement of the augment that went on, None if none did

        Pages:
            in: SHIP_INFO_EQUIPMENT_CHECK, the Gear tab
            out: SHIP_INFO_EQUIPMENT_CHECK, the Gear tab, unless a step could not come back to it
        """
        self.device.click_record_clear()
        if not self.gear_depot_enter(AUGMENT_INDEX):
            return None
        if not self.depot_storage_only():
            return None
        # The list shows a moment after the title, the EQUIPPED switch may already be off
        top = None
        for _ in self.loop(timeout=AUGMENT_WAIT):
            top = self.augment_header_top()
            if top is not None:
                break
        cards, tags = augment_depot_layout(top) if top is not None else ([], [])
        levels = depot_card_levels(self.device.image, cards, tags)
        best = best_card(levels)
        logger.attr('Depot of augments', levels)
        if best < 0:
            logger.info('No augment in storage')
            self.gear_tab_ensure()
            return None
        level = levels[best]
        if current is not None and (level < GEARED_LEVEL or level <= current):
            logger.info(f'Storage has no better augment, +{level} at best for +{current}')
            self.gear_tab_ensure()
            return None
        if not self.gear_card_open(cards[best]):
            self.gear_tab_ensure()
            return None
        if not self.gear_card_confirm(AUGMENT_INDEX):
            return None
        logger.info(f'Augment at +{level} goes on')
        return level

    def gear_slot_fill(self, index, hull=None, current=None, other=None):
        """
        Put the gear of storage the policy likes best in a slot, if it beats what she has.
        The popups of the likely best cards of the depot are read, MAX_LOOKS of them at most.

        Args:
            index (int): 0 to 4
            hull (str): Her hull type, 'DD'
            current (dict): Gear in the slot, None if empty, see module.leveling.policy
            other (dict): Gear in her other auxiliary slot, for slots 3 and 4

        Returns:
            dict: Gear that went on, None if none did

        Pages:
            in: SHIP_INFO_EQUIPMENT_CHECK, the Gear tab
            out: SHIP_INFO_EQUIPMENT_CHECK, the Gear tab, unless a step could not come back to it
        """
        # Taps on Cancel and on the cards of a depot add up
        self.device.click_record_clear()
        if not self.gear_depot_enter(index):
            return None
        if not self.depot_storage_only():
            return None
        self.depot_list_wait(DEPOT_CARDS)

        cards = depot_cards_read(self.device.image)
        logger.attr(f'Depot of slot {index}', [f'{card["name"]} {card["rarity"]} +{card["level"]}' for card in cards])
        current_score = gear_score(index, hull, current, other)
        order = card_order(index, hull, cards, other)
        if index in WEAPON_SLOTS:
            # The card of a weapon tells its score, but for gear against submarines
            order = [card for card in order if is_better(gear_score(index, hull, card_item(card)), current_score)]
        best, best_score, best_button, opened = None, None, None, None
        for card in order[:MAX_LOOKS]:
            button = DEPOT_CARDS[card['index']]
            if not self.gear_card_open(button):
                self.gear_tab_ensure()
                return None
            item = popup_read(self.device.image, 'confirm')
            item.update(rarity=card['rarity'], level=card['level'])
            score = gear_score(index, hull, item, other)
            logger.info(f'Slot {index}: {describe(item)}, ' + ('left out' if score is None else f'score {score}'))
            if is_better(score, best_score):
                best, best_score, best_button = item, score, button
            if gear_ideal(index, hull, score):
                opened = button
                break
            if not self.gear_card_cancel():
                self.gear_tab_ensure()
                return None

        if not is_better(best_score, current_score):
            logger.info(f'Storage has nothing better for slot {index} than {describe(current)}')
            self.gear_tab_ensure()
            return None
        if opened is not best_button:
            if opened is not None and not self.gear_card_cancel():
                self.gear_tab_ensure()
                return None
            if not self.gear_card_open(best_button):
                self.gear_tab_ensure()
                return None
        if not self.gear_card_confirm(index):
            return None
        logger.info(f'Gear slot {index}: {describe(current)} -> {describe(best)}')
        return best

    def gear_read_info(self):
        """
        Returns:
            tuple[list[int], list[str]]: Enhancement of the 5 gear slots, -1 for an empty one, and of the augment,
                0 for none. Rarity of the gear in the 5 slots, None for an empty one.

        Pages:
            in: detail page of a ship
            out: detail page of a ship, the Info tab
        """
        self.ship_side_navbar_ensure(bottom=1)
        for _ in self.loop(timeout=GEAR_TIMEOUT):
            if self.appear(EQUIPMENT_OPEN, offset=(20, 20)):
                break
        empty = empty_gear_slots(self.device.image)
        levels = gear_levels(self.device.image)
        augment = parse_trailing_number(str(Ocr([INFO_AUGMENT_LEVEL], letter=(255, 255, 255), threshold=128,
                                                name='AUGMENT_LEVEL').ocr(self.device.image)), maximum=13)
        levels = [-1 if index in empty else level for index, level in enumerate(levels)] + [augment]
        return levels, gear_rarities(self.device.image, empty)

    def gear_read(self):
        """
        Returns:
            list[int]: Enhancement of the 5 gear slots, -1 for an empty one, and of the augment, 0 for none

        Pages:
            in: detail page of a ship
            out: detail page of a ship, the Info tab
        """
        return self.gear_read_info()[0]

    def gear_fill(self, hull=None):
        """
        Gear from storage for the ship shown, by the choice of module.leveling.policy: weapons the policy does not
        keep, auxiliaries the worst first, and an augment below GEARED_LEVEL.

        Args:
            hull (str): Her hull type, 'DD'. None if unknown, auxiliaries go as for a hull of no list.

        Returns:
            dict: Her gear after it, as the inventory keeps it, see module.leveling.plan.gear_record(): the
                enhancement of her 5 gear slots and of her augment, and if her gear passed the policy, see
                gear_passes(). Gear it leaves out that storage could not replace stays on her and fails it.
                None if a step could not come back to the Gear tab.

        Pages:
            in: detail page of a ship
            out: detail page of a ship, the Info tab. Unknown if None.
        """
        levels, rarities = self.gear_read_info()
        logger.attr('Gear', levels)
        logger.attr('Rarity', rarities)
        worn = [None if levels[index] < 0 else gear_item(rarity=rarities[index], level=levels[index])
                for index in range(len(GEAR_SLOTS))]
        weapons = [index for index in WEAPON_SLOTS if not gear_good(index, hull, worn[index])]
        self.ship_side_navbar_ensure(bottom=2)
        if not self.gear_tab_ensure():
            return None
        # Auxiliaries are judged by their name and stats
        for index in AUX_SLOTS:
            if worn[index] is not None:
                read = self.gear_worn_read(index)
                if read is not None:
                    worn[index].update(name=read['name'], stats=read['stats'])
                logger.info(f'Slot {index}: {describe(worn[index])}')

        for index in weapons:
            put = self.gear_slot_fill(index, hull=hull, current=worn[index])
            if put is not None:
                worn[index] = put
            # A popup that stays open hides the side navbar
            if not self.gear_tab_ensure():
                return None

        def other_of(index):
            return worn[AUX_SLOTS[1] if index == AUX_SLOTS[0] else AUX_SLOTS[0]]

        def badness(index):
            score = gear_score(index, hull, worn[index], other_of(index))
            return score is None, score or ()

        # The worst first: a destroyer without a Repair Toolkit has it in place of her worse auxiliary
        for index in sorted(AUX_SLOTS, key=badness, reverse=True):
            if gear_good(index, hull, worn[index], other_of(index)):
                continue
            put = self.gear_slot_fill(index, hull=hull, current=worn[index], other=other_of(index))
            if put is not None:
                worn[index] = put
            if not self.gear_tab_ensure():
                return None

        if levels[AUGMENT_INDEX] < GEARED_LEVEL:
            self.augment_slot_fill(current=levels[AUGMENT_INDEX])
            if not self.gear_tab_ensure():
                return None

        levels = self.gear_read()
        logger.attr('Gear', levels)
        checked = gear_passes(hull, worn)
        if not checked:
            logger.warning('Gear the policy leaves out, or could not read, stays on her: '
                           + ', '.join(describe(item) for item in worn))
        return gear_record(levels, checked=checked)

    def gear_ship(self, target):
        """
        Find a ship in the dock and give her gear from storage, see gear_fill().

        Args:
            target (Target):

        Returns:
            dict: Her gear after it, see gear_fill(). None if she was not found or the gear steps did not end well

        Pages:
            in: any
            out: page_dock
        """
        logger.hr(f'Gear {target.ship}', level=2)
        self.ui_ensure(page_dock)
        ship = None
        if self.dock_search(target.ship):
            ship = self.dock_find(target)
        if ship is None:
            return None
        self.ship_info_enter(click_button=ship.button, check_button=EQUIPMENT_OPEN, long_click=False,
                             skip_first_screenshot=False)
        gear = self.gear_fill(hull=target.hull)
        if gear is None:
            logger.warning(f'Gear of {target.ship} left unfinished')
            self.ui_ensure(page_dock)
            return None
        logger.info(f'{target.ship} has gear {gear}')
        self.ui_back(check_button=DOCK_CHECK)
        return gear
