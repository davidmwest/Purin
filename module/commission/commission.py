import copy
import re
from datetime import datetime, timedelta

from scipy import signal

from module.base.button import Button
from module.base.timer import Timer
from module.base.utils import *
from module.combat.assets import *
from module.commission.assets import *
from module.commission.preset import DICT_FILTER_PRESET, SHORTEST_FILTER
from module.commission.project import COMMISSION_FILTER, Commission
from module.config.config_generated import GeneratedConfig
from module.config.utils import get_server_last_update, get_server_next_update
from module.dorm.dorm import RewardDorm
from module.exception import GameStuckError, OilMaxed, RequestHumanTakeover
from module.handler.info_handler import InfoHandler
from module.logger import logger
from module.map.map_grids import SelectedGrids
from module.ocr.ocr import DigitCounter, Ocr
from module.retire.assets import DOCK_CHECK
from module.ui.assets import BACK_ARROW, REWARD_GOTO_COMMISSION
from module.ui.page import page_commission, page_reward
from module.ui.scroll import Scroll
from module.ui.switch import Switch
from module.ui.ui import UI
from module.ui_white.assets import REWARD_1_WHITE, REWARD_GOTO_COMMISSION_WHITE

COMMISSION_SWITCH = Switch('Commission_switch', is_selector=True)
COMMISSION_SWITCH.add_state('daily', COMMISSION_DAILY)
COMMISSION_SWITCH.add_state('urgent', COMMISSION_URGENT)
COMMISSION_SCROLL = Scroll(COMMISSION_SCROLL_AREA, color=(247, 211, 66), name='COMMISSION_SCROLL')


def lines_detect(image):
    """
    Args:
        image:

    Returns:
        np.ndarray: Coordinate Y of the white lines under each commission.
    """
    # Find white lines under each commission to locate them.
    # (597, 0, 619, 720) is somewhere with white lines only.
    color_height = np.mean(rgb2gray(crop(image, (597, 0, 619, 720), copy=False)), axis=1)
    parameters = {'height': 200, 'distance': 100}
    peaks, _ = signal.find_peaks(color_height, **parameters)
    # 67 is the height of commission list header
    # 117 is the height of one commission card.
    peaks = [y for y in peaks if y > 67 + 117]
    return np.array(peaks)


# A commission unfolded, its requirements between its title and its team, from the top of COMMISSION_ADVICE
REQUIREMENT_AREA = (310, -109, 1170, -64)
TEAM_AREA = (225, -37, 825, 53)
# Pixels of a requirement in red, like "Have at least 1 lv. 10 ship." on a new account: 945 of them.
# Met requirements are yellow with a green tick.
REQUIREMENT_RED = 200
# Portraits make the team colorful. The game bug of ships disappearing after "Recommend" leaves it empty.
TEAM_STD = 40


def requirement_unmet(image, top):
    """
    Args:
        image (np.ndarray): Screenshot of a commission unfolded
        top (int): Top of COMMISSION_ADVICE

    Returns:
        bool: If a requirement is red while the team has ships
    """
    x1, y1, x2, y2 = REQUIREMENT_AREA
    requirement = crop(image, (x1, top + y1, x2, top + y2), copy=False).astype(np.int16)
    red = (requirement[:, :, 0] > 180) & (requirement[:, :, 1] < 110) & (requirement[:, :, 2] < 110)
    if np.sum(red) < REQUIREMENT_RED:
        return False
    x1, y1, x2, y2 = TEAM_AREA
    team = crop(image, (x1, top + y1, x2, top + y2), copy=False)
    return float(np.std(team)) > TEAM_STD


# The first requirement, like "Have at least 1 lv. 30 ship.", from the top of COMMISSION_ADVICE. It scrolls in its
# box, its numbers green and its words red, and is drawn a moment after the commission unfolds.
REQUIREMENT_LEVEL_AREA = (318, -86, 594, -60)
REQUIREMENT_WORDS = (230, 86, 68)
REQUIREMENT_DIGIT = (137, 233, 96)
# Rows searched for the line of the first requirement, from the top of COMMISSION_ADVICE. Recommend may match 11 px
# lower than its usual place (Research Mission II of the F2P account, 2026-10-22, its team filled with Lv.1 ships):
# the box at a fixed offset then cut the top half of the letters, read "vea`'e>v.3VS'P", and no fleet ship was taken.
REQUIREMENT_SEARCH = (-100, -45)
REQUIREMENT_LINE_MARGIN = 3
REQUIREMENT_LEVEL = re.compile(r'[l1I]v[.,]?\s*(\d{1,3})', re.IGNORECASE)
LEVEL_MAX = 125
REQUIREMENT_READS = 4
REQUIREMENT_READ_INTERVAL = 0.4


def requirement_mask(area):
    """
    Returns:
        np.ndarray: Pixels of the red words and the green numbers of a requirement
    """
    area = area.astype(np.int16)
    text = np.zeros(area.shape[:2], dtype=bool)
    for color in (REQUIREMENT_WORDS, REQUIREMENT_DIGIT):
        text |= np.abs(area - np.array(color)).max(axis=2) < 60
    return text


def requirement_box(image, top):
    """
    Args:
        image (np.ndarray): Screenshot of a commission unfolded
        top (int): Top of COMMISSION_ADVICE

    Returns:
        tuple[int, int, int, int]: Area of the line of the first requirement, found by its colors around where it
            is drawn, REQUIREMENT_LEVEL_AREA if no line is found
    """
    x1, y1, x2, y2 = REQUIREMENT_LEVEL_AREA
    fixed = (x1, top + y1, x2, top + y2)
    s1 = max(top + REQUIREMENT_SEARCH[0], 0)
    s2 = min(top + REQUIREMENT_SEARCH[1], image.shape[0])
    if s2 - s1 < 5:
        return fixed
    rows = requirement_mask(crop(image, (x1, s1, x2, s2), copy=False)).sum(axis=1)
    lit = rows >= 3
    # The longest run of rows with letters is the line
    best, start = None, None
    for y, on in enumerate(list(lit) + [False]):
        if on and start is None:
            start = y
        elif not on and start is not None:
            if best is None or rows[start:y].sum() > rows[best[0]:best[1]].sum():
                best = (start, y)
            start = None
    if best is None:
        return fixed
    margin = REQUIREMENT_LINE_MARGIN
    return x1, max(s1 + best[0] - margin, 0), x2, min(s1 + best[1] + margin, image.shape[0])


def requirement_text(image, top):
    """
    Args:
        image (np.ndarray): Screenshot of a commission unfolded
        top (int): Top of COMMISSION_ADVICE

    Returns:
        str: The first requirement as read, its red words and green numbers
    """
    box = requirement_box(image, top)
    text = requirement_mask(crop(image, box, copy=False))
    if np.sum(text) < 50:
        return ''
    # White letters on black, the colors of the OCR below
    letters = np.zeros_like(image)
    region = letters[box[1]:box[3], box[0]:box[2]]
    region[text] = 255
    button = Button(area=box, color=(), button=box, name='REQUIREMENT')
    return str(Ocr(button, lang='cnocr', letter=(255, 255, 255), threshold=128, name='REQUIREMENT').ocr(letters))


def requirement_level(image, top):
    """
    Args:
        image (np.ndarray): Screenshot of a commission unfolded
        top (int): Top of COMMISSION_ADVICE

    Returns:
        int: Level the first requirement asks a ship for, 0 if it asks for no level or is not read
    """
    match = REQUIREMENT_LEVEL.search(requirement_text(image, top))
    if not match:
        return 0
    level = int(match.group(1))
    return level if 1 <= level <= LEVEL_MAX else 0


# A ship of a fleet goes in the team of a commission from its team picker, by her name. Recommend never offers a
# fleet ship, nor one just taken out of her fleet: 2026-10-22, Laffey Lv.75 out of fleet 1 and free, Recommend said
# "No suitable shipgirls found" for a commission asking for a Lv.30 ship. The picker shows the fleet ships, and taking
# one asks "This ship is assigned to another fleet. Remove ship from that fleet?"
TEAM_SLOT_EMPTY = './assets/commission/team_slot_empty.png'
# The line of the team, from the top of COMMISSION_ADVICE
TEAM_ROW = (220, -45, 830, 65)
TEAM_SLOT_SIMILARITY = 0.85
PICK_OPEN_TIMEOUT = 10
PICK_TIMEOUT = 20


def team_empty_slots(image, top, template):
    """
    Args:
        image (np.ndarray): Screenshot of a commission unfolded
        top (int): Top of COMMISSION_ADVICE
        template (np.ndarray): An empty slot of the team

    Returns:
        list[tuple[int, int, int, int]]: Areas of the empty slots of the team, left to right
    """
    x1, y1, x2, y2 = TEAM_ROW
    area = (x1, max(top + y1, 0), x2, min(top + y2, image.shape[0]))
    h, w = template.shape[:2]
    if area[3] - area[1] < h or area[2] - area[0] < w:
        return []
    result = cv2.matchTemplate(crop(image, area, copy=False), template, cv2.TM_CCOEFF_NORMED)
    slots = []
    while 1:
        _, score, _, (x, y) = cv2.minMaxLoc(result)
        if score < TEAM_SLOT_SIMILARITY:
            break
        slots.append((area[0] + x, area[1] + y, area[0] + x + w, area[1] + y + h))
        # Wipe the neighbourhood of this match, the next best is an other slot
        result[max(y - h // 2, 0):y + h // 2 + 1, max(x - w // 2, 0):x + w // 2 + 1] = -1
    return sorted(slots)


# Commissions tried in a run at most, those that can not start included
COMMISSION_TRIES = 10
# "Available Fleet 2/3" on top of the commission list: fleets free for a commission, of all. A young account has 3,
# not the 4 the slots are counted to, and started commissions over and over with its 3 busy.
COMMISSION_FLEET_AREA = (1040, 14, 1150, 44)
OCR_COMMISSION_FLEET = DigitCounter(
    Button(area=COMMISSION_FLEET_AREA, color=(), button=COMMISSION_FLEET_AREA, name='COMMISSION_FLEET'),
    letter=(255, 255, 255), threshold=128, name='COMMISSION_FLEET')
# Accounts have up to this many fleets for commissions, more is a misread
COMMISSION_FLEET_MAX = 6


def commission_fleet_free(image):
    """
    Args:
        image (np.ndarray): Commission list

    Returns:
        int | None: Fleets free for a commission, None if the counter is not read
    """
    free, _, total = OCR_COMMISSION_FLEET.ocr(image)
    if not 0 < total <= COMMISSION_FLEET_MAX or not 0 <= free <= total:
        logger.warning(f'Commission fleets not read: {free}/{total}')
        return None
    return free


def commission_kind(comm):
    """
    Returns:
        tuple: Genre and duration of a commission, those of a kind ask for the same ships
    """
    return getattr(comm, 'genre', None), str(getattr(comm, 'duration', None))


def commission_candidates(daily_choose, urgent_choose, comm_choose):
    """
    Args:
        daily_choose (SelectedGrids): Daily commissions chosen
        urgent_choose (SelectedGrids): Urgent commissions chosen
        comm_choose (SelectedGrids): Every commission the filter takes, in its order, with 'shortest' and the like

    Returns:
        list[Commission]: Commissions in the order of the filter, the chosen ones are its first.
            Daily ones went before urgent ones, an urgent cube commission waited behind daily resource ones.
    """
    out = []
    for comm in list(comm_choose) + list(daily_choose) + list(urgent_choose):
        if isinstance(comm, str) or any(comm == other for other in out):
            continue
        out.append(comm)
    return out


class RewardCommission(UI, InfoHandler):
    daily: SelectedGrids
    urgent: SelectedGrids
    daily_choose: SelectedGrids
    urgent_choose: SelectedGrids
    comm_choose: SelectedGrids
    max_commission = 4

    def _commission_detect(self, image):
        """
        Get all commissions from an image.

        Args:
            image (np.ndarray):

        Returns:
            SelectedGrids:
        """
        logger.hr('Commission detect')
        commission = []
        for y in lines_detect(image):
            comm = Commission(image, y=y, config=self.config)
            logger.attr('Commission', comm)
            repeat = len([c for c in commission if c == comm])
            comm.repeat_count += repeat
            commission.append(comm)

        return SelectedGrids(commission)

    def commission_detect(self, trial=1, area=None, skip_first_screenshot=True):
        """
        Args:
            trial (int): Retry if has one invalid commission,
                         usually because info_bar didn't disappear completely.
            area (tuple):
            skip_first_screenshot (bool):

        Returns:
            SelectedGrids:
        """
        commissions = SelectedGrids([])
        for _ in range(trial):
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            image = self.device.image
            if area is not None:
                image = crop(image, area, copy=False)
            commissions = self._commission_detect(image)

            if commissions.count >= 2 and commissions.select(valid=False).count == 1:
                logger.warning('Found 1 invalid commission, retry commission detect')
                continue
            else:
                return commissions

        logger.info('trials of commission detect exhausted, stop')
        return commissions

    def _commission_choose(self, daily, urgent):
        """
        Args:
            daily (SelectedGrids):
            urgent (SelectedGrids):

        Returns:
            SelectedGrids, SelectedGrids: Chosen daily commission, Chosen urgent commission
        """
        self.comm_choose = SelectedGrids([])
        # Count Commission
        total = daily.add_by_eq(urgent)
        # Commissions with higher suffix are always below those with smaller suffix
        # Reverse the commission list to choose commissions with higher suffix first
        total = total[::-1]
        self.max_commission = 4
        for comm in total:
            if comm.genre == 'daily_event':
                self.max_commission = 5
        running_count = int(
            np.sum([1 for c in total if c.status == 'running']))
        logger.attr('Running', f'{running_count}/{self.max_commission}')
        free = self.max_commission - running_count
        fleet_free = getattr(self, 'fleet_free', None)
        if fleet_free is not None and fleet_free < free:
            logger.info(f'Fleets free for commissions: {fleet_free}')
            free = fleet_free

        # Load filter string
        preset = self.config.Commission_PresetFilter
        if preset == 'custom':
            string = self.config.Commission_CustomFilter
        else:
            if f'{preset}_night' in DICT_FILTER_PRESET:
                start_time = get_server_last_update('02:00')
                end_time = get_server_last_update('21:00')
                if start_time < end_time:
                    preset = f'{preset}_night'
            if preset not in DICT_FILTER_PRESET:
                logger.warning(f'Preset not found: {preset}, use default preset')
                preset = GeneratedConfig.Commission_PresetFilter
            string = DICT_FILTER_PRESET[preset]
        logger.attr('Commission Filter', preset)

        # Filter
        COMMISSION_FILTER.load(string)
        run = COMMISSION_FILTER.apply(total.grids, func=self._commission_check)
        logger.attr('Filter_sort', ' > '.join([str(c) for c in run]))
        run = SelectedGrids(run)

        # Add shortest
        no_shortest = run.delete(SelectedGrids(['shortest']))
        if no_shortest.count < free:
            if daily.count:
                logger.info('Not enough commissions to run, add shortest daily commissions')
                COMMISSION_FILTER.load(SHORTEST_FILTER)
                shortest = COMMISSION_FILTER.apply(daily[::-1], func=self._commission_check)
                # Reverse the daily list to choose better commissions
                run = no_shortest.add_by_eq(SelectedGrids(shortest))
                logger.attr('Filter_sort', ' > '.join([str(c) for c in run]))
            else:
                logger.info('Not enough commissions to run')

        self.comm_choose = run
        if free <= 0:
            return SelectedGrids([]), SelectedGrids([])

        # Separate daily and urgent
        run = run[:free]
        daily_choose = run.intersect_by_eq(daily)
        urgent_choose = run.intersect_by_eq(urgent)
        if daily_choose:
            logger.info('Choose daily commission')
            for comm in daily_choose:
                logger.info(comm)
        if urgent_choose:
            logger.info('Choose urgent commission')
            for comm in urgent_choose:
                logger.info(comm)

        return daily_choose, urgent_choose

    def _commission_check(self, commission):
        """
        Args:
            commission (Commission):

        Returns:
            bool:
        """
        if not commission.valid or commission.status != 'pending':
            return False
        if not self.config.Commission_DoMajorCommission and commission.category_str == 'major':
            return False

        return True

    def _commission_ensure_mode(self, mode):
        if COMMISSION_SWITCH.set(mode, main=self):
            # If daily list has commissions > 4, usually to be 5, and 1 <= urgent <= 4
            # commission list will have an animation to scroll,
            # which causes the topmost one undetected.
            if not COMMISSION_SCROLL.appear(main=self) or COMMISSION_SCROLL.cal_position(main=self) < 0.05 or COMMISSION_SCROLL.length / COMMISSION_SCROLL.total > 0.98:
                pre_peaks = lines_detect(self.device.image)
                if not len(pre_peaks):
                    return True
                self.device.screenshot()
                while 1:
                    peaks = lines_detect(self.device.image)
                    if (not len(peaks) or peaks[0] > 67 + 117) and (not len(pre_peaks) or not len(peaks) or abs(peaks[0] - pre_peaks[0]) < 3):
                        break
                    pre_peaks = peaks
                    self.device.screenshot()

            return True
        else:
            return False

    def _commission_mode_reset(self):
        logger.hr('Commission mode reset')
        if self.appear(COMMISSION_DAILY):
            current, another = 'daily', 'urgent'
        elif self.appear(COMMISSION_URGENT):
            current, another = 'urgent', 'daily'
        else:
            logger.warning('Unknown Commission mode')
            return False

        self._commission_ensure_mode(another)
        self._commission_ensure_mode(current)

        return True

    def _commission_swipe(self):
        if COMMISSION_SCROLL.appear(main=self):
            if COMMISSION_SCROLL.at_bottom(main=self):
                return False
            else:
                COMMISSION_SCROLL.next_page(main=self)
                return True
        else:
            return False

    def _commission_swipe_to_top(self):
        if not COMMISSION_SCROLL.appear(main=self):
            return False
        COMMISSION_SCROLL.set_top(main=self, skip_first_screenshot=True)
        return True

    def _commission_scan_list(self):
        """
        Returns:
            SelectedGrids: SelectedGrids containing Commission objects
        """
        self.device.click_record_clear()
        commission = SelectedGrids([])
        for _ in range(15):
            new = self.commission_detect(trial=2)
            commission = commission.add_by_eq(new)

            # End
            if not self._commission_swipe():
                break

        self.device.click_record_clear()
        return commission

    def _commission_scan_all(self):
        """
        Pages:
            in: page_commission
            out: page_commission
        """
        logger.hr('Commission scan', level=1)
        # Urgent list is lazy loaded. Check it first for a force update.
        self._commission_ensure_mode('urgent')
        self.fleet_free = commission_fleet_free(self.device.image)

        logger.hr('Scan daily', level=2)
        self._commission_ensure_mode('daily')
        self._commission_swipe_to_top()
        daily = self._commission_scan_list()

        urgent = SelectedGrids([])
        for _ in range(2):
            logger.hr('Scan urgent', level=2)
            self._commission_ensure_mode('urgent')
            self._commission_swipe_to_top()
            urgent = self._commission_scan_list()
            # Convert extra commission to night
            urgent.call('convert_to_night')

            # Not in 21:00~03:00, but scanned night commissions
            # Probably some outdated commissions, a refresh should solve it
            if datetime.now() - get_server_next_update('21:00') > timedelta(hours=6):
                night = urgent.select(category_str='night')
                if night:
                    logger.warning('Not in 21:00~03:00, but scanned night commissions')
                    for comm in night:
                        logger.attr('Commission', comm)
                    logger.info('Re-scan urgent commission list')
                    # Poor sleep but acceptable in rare cases
                    self.device.sleep(2)
                    self._commission_ensure_mode('daily')
                    continue

            break

        logger.hr('Showing commission', level=2)
        logger.info('Daily commission')
        for comm in daily.sort('status', 'genre'):
            logger.attr('Commission', comm)
        if urgent.count:
            logger.info('Urgent commission')
            for comm in urgent.sort('status', 'genre'):
                logger.attr('Commission', comm)

        self.daily = daily
        self.urgent = urgent
        self.daily_choose, self.urgent_choose = self._commission_choose(self.daily, self.urgent)
        return daily, urgent

    def _commission_start_click(self, comm, is_urgent=False, skip_first_screenshot=True, pick=None):
        """
        Start a commission.

        Args:
            comm (Commission):
            is_urgent (bool):
            skip_first_screenshot:
            pick (Slot): A ship of a fleet put in the team before Recommend, see commission_pick(). Recommend then
                adds the rest, locked ships of level 1 if it offers them.

        Returns:
            bool: If success

        Pages:
            in: page_commission
            out: page_commission, info_bar, commission details unfold
        """
        logger.hr('Commission start')
        self.interval_clear(COMMISSION_ADVICE)
        self.interval_clear(COMMISSION_START)
        comm_timer = Timer(7)
        count = 0
        # START tapped, and if it was lit then
        start_clicked = False
        start_lit = False
        # Level the first requirement asks a ship for, read before Recommend fills the team.
        # Set when no free ship has it, see commission_start()
        need_level = 0
        self.commission_no_ship = 0
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # End
            if self.info_bar_count():
                if not start_clicked:
                    # An info bar before START is no start
                    continue
                if start_lit:
                    break
                # START tapped while it did not look lit: the game took it unless its requirements are still red
                self.device.sleep(1.5)
                self.device.screenshot()
                if self.appear(COMMISSION_ADVICE, offset=(5, 20)) \
                        and requirement_unmet(self.device.image, COMMISSION_ADVICE.button[1]):
                    logger.warning(f'Requirements of commission {comm} are not met, skip it')
                    return False
                break
            if count >= 3:
                # Restart game and handle commission recommend bug.
                # After you click "Recommend", your ships appear and then suddenly disappear.
                # At the same time, the icon of commission is flashing.
                logger.warning('Triggered commission list flashing bug')
                raise GameStuckError('Triggered commission list flashing bug')

            # Click
            if self.match_template_color(COMMISSION_START, offset=(5, 20), interval=7):
                self.device.click(COMMISSION_START)
                start_clicked = True
                start_lit = True
                self.interval_reset(COMMISSION_ADVICE)
                comm_timer.reset()
                continue
            if count and not start_clicked and need_level > 1 and pick is None \
                    and self.handle_popup_cancel('COMMISSION_NO_SHIP'):
                # After Recommend: "No suitable shipgirls found. Would you like to automatically add a locked Lv. 1
                # shipgirl?" Ships of level 1 would not meet "Have at least 1 lv. 30 ship." either
                logger.warning(f'No free ship at level {need_level} for commission {comm}')
                self.commission_no_ship = need_level
                self.device.sleep(1)
                return False
            if self.handle_popup_confirm('COMMISSION_START'):
                self.interval_reset(COMMISSION_ADVICE)
                comm_timer.reset()
                continue
            # Accidentally entered dock
            if self.appear(DOCK_CHECK, offset=(20, 20), interval=3):
                logger.info(f'equip_enter {DOCK_CHECK} -> {BACK_ARROW}')
                self.device.click(BACK_ARROW)
                comm_timer.reset()
                continue
            # Check if is the right commission
            if self.appear(COMMISSION_ADVICE, offset=(5, 20), interval=7):
                if count and not start_clicked:
                    # Recommended already and START not lit: a requirement no free ship meets, like "Have at least
                    # 1 lv. 10 ship." when Recommend could only add locked ships of level 1
                    if requirement_unmet(self.device.image, COMMISSION_ADVICE.button[1]):
                        logger.warning(f'Requirements of commission {comm} are not met, skip it')
                        return False
                    # Requirements met and START not recognized, the game may still take it: tapped once
                    logger.info(f'START of commission {comm} is not lit after Recommend, tapped anyway')
                    self.device.click(COMMISSION_START)
                    start_clicked = True
                    self.interval_reset(COMMISSION_ADVICE)
                    comm_timer.reset()
                    continue
                if count and start_clicked:
                    # START tapped and the panel still open, no info bar
                    logger.warning(f'Commission {comm} did not start, START was tapped')
                    return False
                area = (0, 0, image_size(self.device.image)[0], COMMISSION_ADVICE.button[1])
                current = self.commission_detect(area=area)
                if is_urgent:
                    current.call('convert_to_night')  # Convert extra commission to night
                if current.count >= 1:
                    current = current[0]
                    if current == comm:
                        logger.info('Selected to the correct commission')
                    else:
                        logger.warning('Selected to the wrong commission')
                        return False
                else:
                    logger.warning('No selected commission detected, assuming correct')
                if not count:
                    need_level = self.commission_need_level()
                    if pick is not None and not self.commission_pick(pick):
                        logger.warning(f'{pick.name} not put in the team of commission {comm}')
                        return False
                self.device.click(COMMISSION_ADVICE)
                count += 1
                self.interval_reset(COMMISSION_ADVICE)
                self.interval_clear(COMMISSION_START)
                comm_timer.reset()
                continue
            # Enter
            if comm_timer.reached():
                self.device.click(comm.button)
                self.device.sleep(0.3)
                comm_timer.reset()

        return True

    def commission_team_empty_slot(self):
        """
        Returns:
            Button | None: The first empty slot of the team of the commission unfolded

        Pages:
            in: commission details unfold, COMMISSION_ADVICE appeared
        """
        if not hasattr(self, '_team_slot_template'):
            self._team_slot_template = load_image(TEAM_SLOT_EMPTY)
        slots = team_empty_slots(self.device.image, COMMISSION_ADVICE.button[1], self._team_slot_template)
        if not slots:
            return None
        return Button(area=slots[0], color=(), button=slots[0], name='COMMISSION_TEAM_SLOT')

    def commission_pick_card(self, ship):
        """
        Args:
            ship (Slot): Her name, level and fleet

        Returns:
            Button | None: Her card among the ships the search found, her level first, then the highest

        Pages:
            in: the team picker, a dock search done
        """
        from module.leveling.fleet import area_button, card_name_area, name_fits
        from module.retire.scanner import ShipScanner
        for _ in range(2):
            scanner = ShipScanner(fleet=(0, 6))
            scanner.disable('emotion', 'rarity')
            cards = scanner.scan(self.device.image, output=False)
            if cards:
                break
            # The dock may not have drawn the ships found yet
            self.device.sleep(1)
            self.device.screenshot()
        else:
            return None
        buttons = [area_button(card_name_area(card.button), f'PICK_NAME_{i}') for i, card in enumerate(cards)]
        names = Ocr(buttons, lang='cnocr', letter=(255, 255, 255), threshold=128, name='PICK_NAME').ocr(
            self.device.image)
        if isinstance(names, str):
            names = [names]
        fits = [card for card, read in zip(cards, names) if name_fits(read, ship.name)]
        if not fits:
            logger.warning(f'{ship.name} not found in the team picker, read {names}')
            return None
        fits = sorted(fits, key=lambda card: (card.level != ship.level, -card.level))
        return fits[0].button

    def commission_pick(self, ship):
        """
        Put a ship of a fleet in the team of the commission unfolded: tap an empty slot, search her by name in the
        team picker, tap her, confirm "This ship is assigned to another fleet. Remove ship from that fleet?", and
        Confirm. The Formation task fills her fleet slot again.

        Args:
            ship (Slot):

        Returns:
            bool: If she is in the team

        Pages:
            in: commission details unfold, COMMISSION_ADVICE appeared
            out: commission details unfold
        """
        from module.leveling.fleet import LevelingFleet
        from module.retire.assets import SHIP_CONFIRM
        logger.hr(f'Commission pick {ship.name}', level=3)
        slot = self.commission_team_empty_slot()
        if slot is None:
            logger.warning('No empty slot in the team of the commission')
            return False
        empty_before = len(team_empty_slots(self.device.image, COMMISSION_ADVICE.button[1], self._team_slot_template))
        self.device.click(slot)
        for _ in self.loop(timeout=PICK_OPEN_TIMEOUT):
            if self.appear(DOCK_CHECK, offset=(20, 20)):
                break
        else:
            logger.warning('The team picker did not open')
            return False
        if not LevelingFleet(config=self.config, device=self.device).dock_search(ship.name):
            self.commission_pick_leave()
            return False
        self.device.screenshot()
        card = self.commission_pick_card(ship)
        if card is None:
            self.commission_pick_leave()
            return False
        self.device.click(card)
        timeout = Timer(PICK_TIMEOUT, count=40).start()
        # The card taken, or the popup of her fleet confirmed, shows "Selected: 1/6" a moment after
        confirm = Timer(1.5, count=3).start()
        for _ in self.loop():
            if timeout.reached():
                logger.warning(f'{ship.name} not taken in the team picker')
                self.commission_pick_leave()
                return False
            if self.handle_popup_confirm('COMMISSION_PICK'):
                confirm.reset()
                continue
            if self.appear(COMMISSION_ADVICE, offset=(5, 20)):
                break
            if self.appear(DOCK_CHECK, offset=(20, 20)) and confirm.reached():
                if self.appear_then_click(SHIP_CONFIRM, offset=(200, 50), interval=3):
                    confirm.reset()
        # Back on the commission, a slot fewer empty
        self.device.screenshot()
        if not self.appear(COMMISSION_ADVICE, offset=(5, 20)):
            logger.warning(f'Not back on the commission after picking {ship.name}')
            return False
        empty_after = len(team_empty_slots(self.device.image, COMMISSION_ADVICE.button[1], self._team_slot_template))
        if empty_after >= empty_before:
            logger.warning(f'{ship.name} is not in the team, {empty_after} slots empty')
            return False
        logger.info(f'{ship.name} (Lv.{ship.level}) of fleet {ship.fleet} put in the team')
        return True

    def commission_pick_leave(self):
        """
        Leave the team picker with nothing taken.

        Pages:
            in: the team picker
            out: commission details unfold
        """
        for _ in self.loop(timeout=PICK_OPEN_TIMEOUT):
            if self.appear(COMMISSION_ADVICE, offset=(5, 20)):
                return
            if self.handle_popup_cancel('COMMISSION_PICK'):
                continue
            if self.appear(DOCK_CHECK, offset=(20, 20), interval=3):
                self.device.click(BACK_ARROW)

    def commission_need_level(self):
        """
        Returns:
            int: Level the first requirement of the commission unfolded asks a ship for, 0 if none.
                Its requirements are drawn a moment after it unfolds: read a few times.

        Pages:
            in: commission details unfold, COMMISSION_ADVICE appeared
        """
        level = 0
        for read in range(REQUIREMENT_READS):
            if read:
                self.device.sleep(REQUIREMENT_READ_INTERVAL)
                self.device.screenshot()
                if not self.appear(COMMISSION_ADVICE, offset=(5, 20)):
                    continue
            level = requirement_level(self.device.image, COMMISSION_ADVICE.button[1])
            if level:
                break
        logger.attr('Commission_need_level', level)
        return level

    def _commission_find_and_start(self, comm, is_urgent=False, pick=None):
        """
        Args:
            comm (Commission):
            is_urgent (bool):
            pick (Slot): A ship of a fleet to put in its team, see commission_pick()
        """
        self.device.click_record_clear()
        comm = copy.deepcopy(comm)
        comm.repeat_count = 1
        for _ in range(3):
            logger.hr('Commission find and start', level=2)
            logger.info(f'Finding commission {comm}')

            failed = True

            for _ in range(15):
                new = self.commission_detect(trial=2)
                if is_urgent:
                    new.call('convert_to_night')  # Convert extra commission to night

                # Update commission position.
                # In different scans, they have the same information, but have different locations.
                current = None
                for new_comm in new:
                    if new_comm == comm:
                        current = new_comm
                if current is not None:
                    if self._commission_start_click(current, is_urgent=is_urgent, pick=pick):
                        self.device.click_record_clear()
                        return True
                    else:
                        self._commission_mode_reset()
                        self._commission_swipe_to_top()
                        failed = False
                        break

                # End
                if not self._commission_swipe():
                    break

            if failed:
                logger.warning(f'Failed to select commission: {comm}')
                self._commission_mode_reset()
                self._commission_swipe_to_top()
                self.device.click_record_clear()
                continue
            else:
                logger.warning(f'Commission not found: {comm}')
                self.device.click_record_clear()
                return False

        logger.warning(f'Failed to select commission after 3 trial')
        self.device.click_record_clear()
        return False

    def commission_start(self):
        """
        Scan and Start all chosen commissions.

        Pages:
            in: page_commission
            out: page_commission
        """
        self._commission_scan_all()

        logger.hr('Commission run', level=1)
        if not self.daily_choose and not self.urgent_choose:
            logger.info('No commission chose')
            return
        # The commissions chosen first, then the others the filter takes, in its order: a young account can not meet
        # the requirements of some, its slots stayed empty while others it could do were listed
        slots = self.daily_choose.count + self.urgent_choose.count
        started = 0
        tries = 0
        # Commissions of a kind and duration that did not start: the others of it ask for the same.
        # OCR reads a name a little differently from a scan to an other, the same commission was tried 3 times.
        failed = set()
        # Commissions no free ship had the level for, (commission, is_urgent, level), the first in the filter first
        no_ship = []
        for comm in commission_candidates(self.daily_choose, self.urgent_choose, self.comm_choose):
            if started >= slots or tries >= COMMISSION_TRIES:
                break
            kind = commission_kind(comm)
            if kind in failed:
                continue
            tries += 1
            is_urgent = bool(self.urgent.intersect_by_eq(SelectedGrids([comm])).count)
            self._commission_ensure_mode('urgent' if is_urgent else 'daily')
            self._commission_swipe_to_top()
            self.handle_info_bar()
            self.commission_no_ship = 0
            if self._commission_find_and_start(comm, is_urgent=is_urgent):
                comm.convert_to_running()
                started += 1
            else:
                logger.info(f'Commission {comm} did not start, the next one is tried')
                failed.add(kind)
                if self.commission_no_ship:
                    no_ship.append((comm, is_urgent, self.commission_no_ship))
            self._commission_mode_reset()
        logger.info(f'Commissions started: {started} of {slots}, {tries} tried')
        if started < slots and no_ship:
            started += self.commission_start_with_fleet_ships(no_ship, slots - started)

    def commission_start_with_fleet_ships(self, no_ship, free):
        """
        The user, 2026-10-22: "Commissions are higher priority than farming, so pull a ship from the main or boss
        fleet", "Number one priority is to keep 4/4 commissions running". A commission no free ship has the level
        for takes one from the fleets that farm, the Formation task fills her slot again.

        Args:
            no_ship (list[tuple[Commission, bool, int]]): (commission, is_urgent, level asked), first ones first
            free (int): Fleets free for commissions

        Returns:
            int: Commissions started

        Pages:
            in: page_commission
            out: page_commission
        """
        if not self.config.Commission_PullFleetShip:
            logger.info(f'{len(no_ship)} commission(s) wait for a free ship of their level, '
                        f'Commission.PullFleetShip is off')
            return 0
        from module.leveling.fleet import LevelingFleet
        started = 0
        # A ship taken out that the commission did not take, like one of a type it does not ask for: the next
        # commission tries her before an other ship leaves
        spare = None
        for comm, is_urgent, level in no_ship:
            if started >= free:
                break
            logger.hr(f'Fleet ship for {comm}', level=2)
            if spare is not None and spare.level >= level:
                ship = spare
                logger.info(f'{ship.name} (Lv.{ship.level}) is free already')
            else:
                fleet = LevelingFleet(config=self.config, device=self.device)
                ship = fleet.fleet_choose_ship(level)
                self.ui_ensure(page_commission)
                if ship is None:
                    logger.info(f'No ship of the fleets that farm can leave for a level {level} commission')
                    continue
            self._commission_ensure_mode('urgent' if is_urgent else 'daily')
            self._commission_swipe_to_top()
            self.handle_info_bar()
            if self._commission_find_and_start(comm, is_urgent=is_urgent, pick=ship):
                comm.convert_to_running()
                started += 1
                spare = None
                logger.info(f'Commission {comm} started with {ship.name} of the fleet {ship.fleet} free')
            else:
                spare = ship
                logger.warning(f'Commission {comm} did not start with {ship.name} free')
            self._commission_mode_reset()
        return started

    def _commission_receive(self, skip_first_screenshot=True):
        """
        Args:
            skip_first_screenshot:

        Returns:
            bool: If rewarded.

        Pages:
            in: page_reward
            out: page_commission
        """
        logger.hr('Reward receive')

        reward = False
        click_timer = Timer(1)
        with self.stat.new(
                'commission', method=self.config.DropRecord_CommissionRecord
        ) as drop:
            while 1:
                if skip_first_screenshot:
                    skip_first_screenshot = False
                else:
                    self.device.screenshot()

                # End
                if self.ui_page_appear(page_commission, offset=(20, 20)):
                    # Leaving at page_commission
                    # Commission rewards may appear too slow, causing stuck in UI switching
                    break

                for button in [EXP_INFO_S_REWARD, GET_ITEMS_1, GET_ITEMS_2, GET_ITEMS_3]:
                    if self.appear(button, interval=1):
                        if drop:
                            self.ensure_no_info_bar(timeout=1)
                            drop.add(self.device.image)

                        REWARD_SAVE_CLICK.name = button.name
                        self.device.click(REWARD_SAVE_CLICK)
                        click_timer.reset()
                        reward = True
                        continue
                if click_timer.reached() and self.appear_then_click(REWARD_1, offset=(20, 20), interval=1):
                    self.interval_reset(GET_SHIP)
                    click_timer.reset()
                    reward = True
                    continue
                if click_timer.reached() and self.appear_then_click(REWARD_1_WHITE, offset=(20, 20), interval=1):
                    self.interval_reset(GET_SHIP)
                    click_timer.reset()
                    reward = True
                    continue
                if click_timer.reached() and self.appear_then_click(REWARD_GOTO_COMMISSION, offset=(20, 20)):
                    self.interval_reset(GET_SHIP)
                    click_timer.reset()
                    continue
                if click_timer.reached() and self.appear_then_click(REWARD_GOTO_COMMISSION_WHITE, offset=(20, 20)):
                    self.interval_reset(GET_SHIP)
                    click_timer.reset()
                    continue
                if self.ui_main_appear_then_click(page_reward, interval=3):
                    self.interval_reset(GET_SHIP)
                    # no need to reset click_timer, just instant click REWARD_1
                    # click_timer.reset()
                    continue
                # handle oil maxed
                if self.config.SERVER in ['cn']:
                    if self.appear(OIL_MAXED, offset=(20, 20), interval=3):
                        raise OilMaxed
                # Check GET_SHIP at last to handle random white background at page_main
                for button in [GET_SHIP]:
                    if click_timer.reached() and self.appear(button, offset=(20, 20), interval=1):
                        self.ensure_no_info_bar(timeout=1)
                        drop.add(self.device.image)

                        REWARD_SAVE_CLICK.name = button.name
                        self.device.click(REWARD_SAVE_CLICK)
                        click_timer.reset()
                        reward = True
                        continue
                if click_timer.reached() and self.ui_additional():
                    click_timer.reset()
                    continue

        return reward

    def commission_receive(self):
        """
        Returns:
            bool: If rewarded.

        Pages:
            in: page_reward
            out: page_commission
        """
        for _ in range(3):
            try:
                reward = self._commission_receive()
                return reward
            except OilMaxed:
                logger.info("Oil maxed, buy food to consume oil")
                RewardDorm(self.config, self.device).dorm_food_run(amount=10)
                self.ui_ensure(page_reward)

        logger.critical(f'Failed to handle oil maxed after 3 trial')
        raise RequestHumanTakeover

    def run(self):
        """
        Pages:
            in: Any
            out: page_commission
        """
        self.ui_ensure(page_reward)
        self.commission_receive()

        # info_bar appears when get ship in Launch Ceremony commissions
        # This is a game bug, the info_bar shows get ship, will appear over and over again, until you click get_ship.
        self.handle_info_bar()
        self.commission_start()

        # Scheduler
        total = self.daily.add_by_eq(self.urgent)
        future_finish = sorted([f for f in total.get('finish_time') if f is not None])
        logger.info(f'Commission finish: {[str(f) for f in future_finish]}')
        if len(future_finish):
            self.config.task_delay(target=future_finish)
        else:
            logger.info('No commission running')
            self.config.task_delay(success=False)

        # Delay GemsFarming
        if self.config.cross_get(keys='GemsFarming.GemsFarming.CommissionLimit', default=False):
            daily = self.daily.select(category_str='daily', status='pending').count
            filtered_urgent = self.comm_choose.intersect_by_eq(self.urgent.select(status='pending')).count
            logger.info(f'Daily commission: {daily}, filtered_urgent: {filtered_urgent}')
            if daily > 0 and filtered_urgent >= 1:
                logger.info('Having daily commissions to do, delay task `GemsFarming`')
                self.config.task_delay(
                    minute=120, target=future_finish if len(future_finish) else None, task='GemsFarming')
            elif filtered_urgent >= 4:
                logger.info('Having too many urgent commissions, delay task `GemsFarming`')
                self.config.task_delay(
                    minute=120, target=future_finish if len(future_finish) else None, task='GemsFarming')
