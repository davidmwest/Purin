"""
Collect the rewards of the Handbook, the guide of young accounts: EXP packs, Wisdom Cubes, books.

The user, 2026-10-22: "there needs to be a job to collect handbook rewards".

The Handbook is opened from its button on the left of the main page. It has three tabs, Rookie Missions,
Dev. Missions and Mechanic Tutorials, each with its pages on the left (phases, series, tutorials) and its missions on
the right. A mission done shows Collect (orange, blue or yellow by tab), the reward of a whole page is the big button at
the bottom. Whatever can be collected wears the same red badge with a "!": the Collect button, its page on the left,
its tab on top, and the arrow under the pages when the page is further down. So the badges tell where to tap.

Pages:
    in: Any page
    out: page_main
"""
import cv2
import numpy as np

from module.base.timer import Timer
from module.base.utils import crop, load_image
from module.combat.assets import GET_ITEMS_1, GET_ITEMS_2, GET_ITEMS_3
from module.leveling.fleet import area_button
from module.logger import logger
from module.ui.page import page_main
from module.ui.ui import UI

ENTRANCE = './assets/handbook/entrance.png'
ENTRANCE_AREA = (0, 90, 260, 170)
ENTRANCE_BUTTON = area_button((20, 115, 125, 140), 'HANDBOOK_ENTRANCE')
TITLE = './assets/handbook/title.png'
TITLE_AREA = (100, 0, 330, 70)
SIMILARITY = 0.85
BACK = area_button((20, 25, 90, 65), 'HANDBOOK_BACK')
# Background over the tabs, a tap there closes a reward shown and does nothing on the Handbook
SAFE = area_button((630, 10, 650, 30), 'HANDBOOK_SAFE')

# Where a badge is, and where to tap for it, from the badge's center
TAB_ROW = (760, 30, 1230, 70)
MISSIONS = (1100, 110, 1230, 545)
PAGE_REWARD = (1100, 575, 1230, 660)
PAGES = (120, 110, 320, 590)
PAGES_MORE = (120, 590, 320, 670)
TAP_TAB = (-5, 24)
TAP_MISSION = (-77, 18)
TAP_PAGE_REWARD = (-116, 22)
TAP_PAGE = (45, 20)
# Lists scrolled up, to the missions or pages further down
MISSIONS_SWIPE = ((740, 450), (740, 200))
PAGES_SWIPE = ((230, 520), (230, 220))

# A badge: a red disc about 20 px wide with a white "!" in it
BADGE_SIZE = (15, 25)
BADGE_AREA = (150, 380)
# A badge tapped this many times and still there leads nowhere, like the page already opened
TAPS_MAX = 2
SWIPES_MAX = 2
ROUNDS_MAX = 60
REWARD_TIMEOUT = 10


def find_badges(image):
    """
    Args:
        image (np.ndarray): Screenshot, RGB

    Returns:
        list[tuple[int, int]]: Centers of the red "!" badges, top to bottom
    """
    r, g, b = [image[:, :, i].astype(np.int16) for i in range(3)]
    mask = ((r > 200) & (g < 90) & (b < 90)).astype(np.uint8)
    count, _, stats, centers = cv2.connectedComponentsWithStats(mask)
    badges = []
    for i in range(1, count):
        x, y, w, h, area = stats[i]
        if not (BADGE_SIZE[0] <= w <= BADGE_SIZE[1] and BADGE_SIZE[0] <= h <= BADGE_SIZE[1]):
            continue
        if not BADGE_AREA[0] <= area <= BADGE_AREA[1]:
            continue
        # The "!" in it
        inside = image[y:y + h, x:x + w]
        white = np.sum(np.all(inside > 200, axis=2))
        if white < 8:
            continue
        badges.append((int(centers[i][0]), int(centers[i][1])))
    return sorted(badges, key=lambda c: (c[1], c[0]))


def in_area(point, area):
    x, y = point
    return area[0] <= x <= area[2] and area[1] <= y <= area[3]


def tap_point(badge, offset):
    return badge[0] + offset[0], badge[1] + offset[1]


def plan_taps(badges):
    """
    What to do next on the Handbook, from its badges.

    Args:
        badges (list[tuple[int, int]]):

    Returns:
        list[tuple[str, tuple[int, int], tuple[int, int]]]: (kind, badge, point to tap), the first one first:
            the missions of the page shown, its page reward, then the other pages, then the other tabs
    """
    plan = []
    for kind, area, offset in [
        ('mission', MISSIONS, TAP_MISSION),
        ('page_reward', PAGE_REWARD, TAP_PAGE_REWARD),
        ('page', PAGES, TAP_PAGE),
        ('pages_more', PAGES_MORE, None),
        ('tab', TAB_ROW, TAP_TAB),
    ]:
        for badge in badges:
            if in_area(badge, area):
                point = tap_point(badge, offset) if offset else None
                plan.append((kind, badge, point))
    return plan


def badge_key(kind, badge):
    # Badges move a pixel or two between screenshots
    return kind, badge[0] // 8, badge[1] // 8


class Handbook(UI):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._templates = {}

    def _score(self, file, area):
        if file not in self._templates:
            self._templates[file] = load_image(file)
        region = crop(self.device.image, area, copy=False)
        return float(cv2.minMaxLoc(cv2.matchTemplate(region, self._templates[file], cv2.TM_CCOEFF_NORMED))[1])

    def is_handbook(self):
        return self._score(TITLE, TITLE_AREA) > SIMILARITY

    def has_entrance(self):
        return self._score(ENTRANCE, ENTRANCE_AREA) > SIMILARITY

    def click_point(self, point, name):
        x, y = point
        self.device.click(area_button((x - 3, y - 3, x + 3, y + 3), name))

    def handbook_enter(self):
        """
        Returns:
            bool: If the Handbook opened. False if the main page has no Handbook, an old account.

        Pages:
            in: page_main
            out: Handbook
        """
        self.device.screenshot()
        if not self.has_entrance():
            # Popups of the main page may hide it for a moment
            self.device.sleep(1)
            self.device.screenshot()
            if not self.has_entrance():
                return False
        timeout = Timer(15).start()
        click = Timer(3)
        while 1:
            if self.is_handbook():
                return True
            if timeout.reached():
                logger.warning('Handbook did not open')
                return False
            if click.reached() and self.has_entrance():
                self.device.click(ENTRANCE_BUTTON)
                click.reset()
            self.device.sleep(0.5)
            self.device.screenshot()

    def handbook_close_reward(self):
        """
        After a Collect: the items got, closed by a tap, until the Handbook is back.

        Returns:
            bool: If the Handbook came back
        """
        timeout = Timer(REWARD_TIMEOUT).start()
        tap = Timer(1.5)
        while 1:
            self.device.screenshot()
            if self.is_handbook() and not any(self.appear(b, offset=(30, 30)) for b in (GET_ITEMS_1, GET_ITEMS_2,
                                                                                         GET_ITEMS_3)):
                return True
            if timeout.reached():
                logger.warning('Handbook not back after a reward')
                return False
            for button in (GET_ITEMS_1, GET_ITEMS_2, GET_ITEMS_3):
                if self.appear(button, offset=(30, 30), interval=1):
                    self.device.click(SAFE)
                    tap.reset()
                    break
            else:
                if tap.reached():
                    self.device.click(SAFE)
                    tap.reset()
            self.device.sleep(0.5)

    def handbook_collect(self):
        """
        Returns:
            int: Rewards collected, missions and pages

        Pages:
            in: Handbook
            out: Handbook
        """
        collected = 0
        taps = {}
        swipes = {'missions': 0, 'pages': 0}
        for _ in range(ROUNDS_MAX):
            self.device.screenshot()
            if not self.is_handbook():
                if not self.handbook_close_reward():
                    break
                continue
            badges = find_badges(self.device.image)
            plan = [(kind, badge, point) for kind, badge, point in plan_taps(badges)
                    if taps.get(badge_key(kind, badge), 0) < TAPS_MAX]
            logger.attr('Handbook_badges', [(kind, badge) for kind, badge, _ in plan_taps(badges)])
            if not plan:
                # The page opened keeps its badge while a mission further down can be collected
                stale_page = any(kind == 'page' for kind, _, _ in plan_taps(badges))
                if stale_page and swipes['missions'] < SWIPES_MAX:
                    swipes['missions'] += 1
                    logger.info('Missions further down, swipe')
                    self.device.swipe(*MISSIONS_SWIPE, name='HANDBOOK_MISSIONS_SWIPE')
                    self.device.sleep(1)
                    taps = {k: v for k, v in taps.items() if k[0] != 'page'}
                    continue
                break
            kind, badge, point = plan[0]
            key = badge_key(kind, badge)
            taps[key] = taps.get(key, 0) + 1
            if kind == 'pages_more':
                if swipes['pages'] >= SWIPES_MAX:
                    taps[key] = TAPS_MAX
                    continue
                swipes['pages'] += 1
                logger.info('Pages further down, swipe')
                self.device.swipe(*PAGES_SWIPE, name='HANDBOOK_PAGES_SWIPE')
                self.device.sleep(1)
                continue
            logger.info(f'Handbook {kind} at {badge}, tap {point}')
            self.click_point(point, f'HANDBOOK_{kind.upper()}')
            if kind in ('mission', 'page_reward'):
                collected += 1
                self.device.sleep(1)
                self.handbook_close_reward()
            else:
                # A page or a tab opened: its lists start from their top, and its badges are new ones even where
                # badges were before (2026-10-22, a third page with a Collect where two pages had one was left)
                if kind == 'tab':
                    swipes = {'missions': 0, 'pages': 0}
                    taps = {k: v for k, v in taps.items() if k[0] == 'tab'}
                else:
                    swipes = {**swipes, 'missions': 0}
                    taps = {k: v for k, v in taps.items() if k[0] in ('tab', 'page')}
                self.device.sleep(1.5)
        return collected

    def handbook_leave(self):
        """
        Pages:
            in: Handbook
            out: page_main
        """
        timeout = Timer(20).start()
        click = Timer(2)
        while 1:
            self.device.screenshot()
            if self.is_in_main():
                return True
            if timeout.reached():
                break
            if click.reached() and self.is_handbook():
                self.device.click(BACK)
                click.reset()
            self.device.sleep(0.5)
        self.ui_ensure(page_main)
        return True

    def run(self):
        self.ui_ensure(page_main)
        if not self.handbook_enter():
            logger.info('No Handbook on the main page, nothing to collect')
            self.config.task_delay(server_update=True)
            return
        collected = self.handbook_collect()
        logger.info(f'Handbook rewards collected: {collected}')
        self.handbook_leave()
        self.config.task_delay(success=True)
