"""
EXP Data Packs raise the level of a ship, to keep the average level of the mob fleet at its minimum.

A T1 EXP Data Pack, made by the Lecture Hall, gives 3,000 EXP to a ship (storage holds 3,000 of them).
They are used on the Info tab of the detail page of a ship: its "Boost EXP" button, beside her level, opens a dialog
with a row of buttons for each tier of pack, -10, -, +, +10, and Clear, Auto, Confirm. Above them the dialog shows
her level and the levels the packs chosen add, "LEVEL: 70+3", and their EXP, "9511(+30000)/12100".
Auto chooses the packs to her level cap, 274 of them from 70 to 100. Past 100 a ship waits for an awakening.

The packs added are counted by the levels they add, read in green: OCR takes the plus for "4-", "4-30" for +30.
Packs go in 10 at a time until she reaches the level wanted, then come back one at a time while she still does.

The Lecture Hall makes packs at a steady rate, a few an hour, and the Reward task collects them. Storage was at 2,811
of its 3,000 on 2026-10-20, packs past it are lost. So a ship coming into the fleet goes to BOOST_CAP with Auto when
storage keeps PACK_RESERVE after it, for the ships after her, and only to the level the fleet needs if not.
"""
import re

from module.base.decorator import cached_property
from module.base.utils import load_image
from module.equipment.assets import EQUIPMENT_OPEN
from module.leveling.fleet import area_button
from module.leveling.gear import LevelingGear, gray, template_score
from module.leveling.plan import BOOST_CAP, gear_record, is_geared
from module.logger import logger
from module.ocr.ocr import Digit, Ocr
from module.retire.assets import DOCK_CHECK
from module.ui.page import page_dock

# "Boost EXP" beside her level, on the Info tab
BOOST_ENTER = './assets/leveling/boost_enter.png'
BOOST_ENTER_AREA = (780, 270, 950, 320)
BOOST_ENTER_BUTTON = area_button((804, 286, 920, 304), 'BOOST_ENTER')
# Title of the dialog
BOOST_TITLE = './assets/leveling/boost_title.png'
BOOST_TITLE_AREA = (280, 100, 490, 165)
TEMPLATE_SIMILARITY = 0.85
# Her level on the Info tab, "Level: 70"
INFO_TAB_LEVEL = area_button((690, 282, 800, 316), 'INFO_TAB_LEVEL')
# Her level in the dialog, white, and the levels the packs chosen add, green
BOOST_LEVEL = area_button((338, 193, 420, 218), 'BOOST_LEVEL')
BOOST_GAIN = area_button((395, 193, 475, 218), 'BOOST_GAIN')
GAIN_GREEN = (145, 239, 106)
# Buttons of the T1 packs, and of the dialog
BOOST_PLUS10 = area_button((852, 331, 926, 358), 'BOOST_T1_PLUS10')
BOOST_PLUS = area_button((794, 331, 823, 358), 'BOOST_T1_PLUS')
BOOST_MINUS = area_button((587, 331, 616, 358), 'BOOST_T1_MINUS')
BOOST_CLEAR = area_button((352, 532, 508, 573), 'BOOST_CLEAR')
BOOST_CONFIRM = area_button((808, 532, 966, 573), 'BOOST_CONFIRM')
BOOST_CLOSE = area_button((960, 117, 1010, 146), 'BOOST_CLOSE')
BOOST_AUTO = area_button((580, 532, 738, 573), 'BOOST_AUTO')
# T1 packs in storage, under their icon, and the packs chosen
BOOST_STOCK = area_button((396, 352, 428, 368), 'BOOST_STOCK')
BOOST_COUNT = area_button((630, 330, 780, 360), 'BOOST_COUNT')
# Packs kept in storage for the levels the fleet needs, each ship coming in takes 60 or so for 15 levels
PACK_RESERVE = 300
# Taps of +10 at most, 274 packs take her from 70 to 100
PLUS10_TAPS = 40
# Taps of - at most, coming back from the last +10
MINUS_TAPS = 10
# Seconds for each step
BOOST_TIMEOUT = 10


def parse_level(text):
    """
    Args:
        text (str): "LEVEL:70" as OCR reads it, or "LCVBI:70"

    Returns:
        int: 70, 0 if not read
    """
    found = re.findall(r'\d+', str(text))
    return int(found[-1]) if found else 0


def parse_gain(text):
    """
    Args:
        text (str): The green "+3" after her level, as OCR reads it: "4-3", "4-30", "+3", or "" for none

    Returns:
        int: Levels the packs chosen add
    """
    text = str(text).strip()
    signed = re.search(r'[+\-]\s*(\d+)\s*\D*$', text)
    if signed:
        return int(signed.group(1))
    digits = re.sub(r'\D', '', text)
    if not digits:
        return 0
    # The plus taken for a 4
    if digits.startswith('4') and len(digits) > 1:
        return int(digits[1:])
    return int(digits)


class LevelingBoost(LevelingGear):
    @cached_property
    def _boost_enter(self):
        return gray(load_image(BOOST_ENTER))

    @cached_property
    def _boost_title(self):
        return gray(load_image(BOOST_TITLE))

    def is_boost_enter(self):
        """
        Returns:
            bool: If the Info tab of the detail page of a ship is shown, with its Boost EXP button
        """
        return template_score(self.device.image, self._boost_enter, BOOST_ENTER_AREA) > TEMPLATE_SIMILARITY

    def is_boost_dialog(self):
        return template_score(self.device.image, self._boost_title, BOOST_TITLE_AREA) > TEMPLATE_SIMILARITY

    def info_read_level(self):
        """
        Returns:
            int: Her level on the Info tab, 0 if not read
        """
        return parse_level(Ocr([INFO_TAB_LEVEL], lang='azur_lane', letter=(255, 255, 255), threshold=128,
                               name='INFO_TAB_LEVEL').ocr(self.device.image))

    def boost_read_level(self):
        return parse_level(Ocr([BOOST_LEVEL], lang='azur_lane', letter=(255, 255, 255), threshold=128,
                               name='BOOST_LEVEL').ocr(self.device.image))

    def boost_read_stock(self):
        """
        Returns:
            int: T1 EXP Data Packs in storage
        """
        return Digit([BOOST_STOCK], letter=(255, 255, 255), threshold=128, name='BOOST_STOCK').ocr(self.device.image)

    def boost_read_count(self):
        """
        Returns:
            int: T1 EXP Data Packs chosen
        """
        return Digit([BOOST_COUNT], letter=(255, 255, 255), threshold=128, name='BOOST_COUNT').ocr(self.device.image)

    def boost_read_gain(self):
        return parse_gain(Ocr([BOOST_GAIN], lang='azur_lane', letter=GAIN_GREEN, threshold=128,
                              name='BOOST_GAIN').ocr(self.device.image))

    def boost_tap(self, button):
        """
        Tap a button of the dialog and read the levels the packs add after it.

        Returns:
            int:
        """
        self.device.click(button)
        self.device.sleep((0.3, 0.4))
        self.device.screenshot()
        return self.boost_read_gain()

    def boost_dialog_enter(self):
        """
        Returns:
            bool: If the dialog opened

        Pages:
            in: detail page of a ship, the Info tab
            out: the Boost EXP dialog
        """
        self.interval_clear(BOOST_ENTER_BUTTON)
        for _ in self.loop(timeout=BOOST_TIMEOUT):
            if self.is_boost_dialog():
                return True
            if self.is_boost_enter():
                self.gear_click(BOOST_ENTER_BUTTON)
        logger.warning('Boost EXP did not open')
        return False

    def boost_dialog_close(self):
        """
        Pages:
            in: the Boost EXP dialog, or the Info tab
            out: detail page of a ship, the Info tab
        """
        self.interval_clear(BOOST_CLOSE)
        for _ in self.loop(timeout=BOOST_TIMEOUT):
            if self.is_boost_enter() and not self.is_boost_dialog():
                return True
            if self.is_boost_dialog():
                self.gear_click(BOOST_CLOSE)
                continue
            if self.handle_popup_cancel('BOOST_EXP'):
                continue
        logger.warning('Boost EXP did not close')
        return False

    def boost_choose(self, wanted):
        """
        Choose the fewest T1 packs that take her up a number of levels.

        Args:
            wanted (int): Levels up

        Returns:
            int: Levels up of the packs chosen, None if they can not take her there, nothing chosen then

        Pages:
            in: the Boost EXP dialog, nothing chosen
            out: the Boost EXP dialog
        """
        gain = self.boost_read_gain()
        for _ in range(PLUS10_TAPS):
            if gain >= wanted:
                break
            after = self.boost_tap(BOOST_PLUS10)
            if after <= gain:
                # Read again, the preview may lag
                self.device.sleep(0.5)
                self.device.screenshot()
                after = self.boost_read_gain()
                if after <= gain:
                    break
            gain = after
        if gain < wanted:
            logger.warning(f'EXP Data Packs take her {gain} levels up, not {wanted}: none used')
            self.boost_tap(BOOST_CLEAR)
            return None
        # Fewer packs may do, the last +10 may go past the level wanted
        for _ in range(MINUS_TAPS):
            after = self.boost_tap(BOOST_MINUS)
            if after < wanted:
                gain = self.boost_tap(BOOST_PLUS)
                break
            gain = after
        if gain < wanted:
            logger.warning(f'EXP Data Packs chosen take her {gain} levels up, not {wanted}: none used')
            self.boost_tap(BOOST_CLEAR)
            return None
        return gain

    def boost_choose_spare(self):
        """
        Choose packs to her level cap with Auto, if storage keeps PACK_RESERVE after it.

        Returns:
            int: Levels up of the packs chosen, None if storage can not spare them, nothing chosen then

        Pages:
            in: the Boost EXP dialog, nothing chosen
            out: the Boost EXP dialog
        """
        stock = self.boost_read_stock()
        gain = self.boost_tap(BOOST_AUTO)
        count = self.boost_read_count()
        if gain > 0 and 0 < count and stock - count >= PACK_RESERVE:
            logger.info(f'Spare EXP Data Packs: {count} of {stock} take her {gain} levels up')
            return gain
        logger.info(f'EXP Data Packs to her level cap: {count} of {stock}, keeping {PACK_RESERVE}: not spared')
        self.boost_tap(BOOST_CLEAR)
        return None

    def boost_to(self, level=None, spare=False):
        """
        Raise the ship shown with T1 EXP Data Packs: to her level cap with spare packs, see boost_choose_spare(),
        else to a level with as few packs as it takes. Nothing is used if she can not get there.

        Args:
            level (int): Level she needs, None for none
            spare (bool): If spare packs take her to her level cap, BOOST_CAP

        Returns:
            int: Her level after, None if no pack was used

        Pages:
            in: detail page of a ship, the Info tab
            out: detail page of a ship, the Info tab
        """
        if level is None and not spare:
            return None
        if not self.boost_dialog_enter():
            return None
        current = self.boost_read_level()
        logger.info(f'Boost EXP: level {current}, needs {level}, spare packs {spare}')
        if not 0 < current < BOOST_CAP:
            self.boost_dialog_close()
            return None
        gain = self.boost_choose_spare() if spare else None
        if gain is None:
            wanted = min(level, BOOST_CAP) - current if level is not None else 0
            if wanted <= 0:
                self.boost_dialog_close()
                return None
            gain = self.boost_choose(wanted)
            if gain is None:
                self.boost_dialog_close()
                return None

        logger.info(f'Boost EXP: {current} + {gain}')
        # The dialog may close once the packs are used, or stay with her new level
        self.interval_clear(BOOST_CONFIRM)
        confirmed = False
        for _ in self.loop(timeout=BOOST_TIMEOUT):
            if self.handle_popup_confirm('BOOST_EXP'):
                confirmed = True
                continue
            if self.is_boost_dialog():
                if confirmed and self.boost_read_level() >= current + gain:
                    break
                if self.gear_click(BOOST_CONFIRM):
                    confirmed = True
                continue
            if self.is_boost_enter():
                break
        self.boost_dialog_close()
        # Her level tells if the packs were used
        after = self.info_read_level()
        if after > current:
            logger.info(f'Boosted from level {current} to {after}')
            return after
        logger.warning(f'Boost EXP: level {after} after it, the packs were not used')
        return None

    def ship_prepare(self, target, gear=True, level=None, spare=False):
        """
        Find a ship in the dock, give her gear from storage and raise her level with EXP Data Packs.

        Args:
            target (Target):
            gear (bool): If she gets gear, see gear_fill()
            level (int): Level she needs, None for none
            spare (bool): If spare packs take her to her level cap, see boost_to()

        Returns:
            tuple[list[int], int]: Enhancement of her 5 gear slots and of her augment after it, None if not geared or
                the steps did not end well. Her level after, None if not raised.

        Pages:
            in: any
            out: page_dock
        """
        logger.hr(f'Prepare {target.ship}', level=2)
        self.ui_ensure(page_dock)
        ship = None
        if self.dock_search(target.ship):
            ship = self.dock_find(target)
        if ship is None:
            return None, None
        self.ship_info_enter(click_button=ship.button, check_button=EQUIPMENT_OPEN, long_click=False,
                             skip_first_screenshot=False)
        levels = None
        if gear:
            levels = self.gear_fill()
            if levels is None:
                logger.warning(f'Gear of {target.ship} left unfinished')
                self.ui_ensure(page_dock)
                return None, None
            logger.info(f'{target.ship} has gear at {levels}')
            if not is_geared(gear_record(levels)):
                # She does not come in, packs would be lost on her
                self.ui_back(check_button=DOCK_CHECK)
                return levels, None
        boosted = None
        if level is not None or spare:
            boosted = self.boost_to(level, spare=spare)
        self.ui_back(check_button=DOCK_CHECK)
        return levels, boosted
