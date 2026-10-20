"""
Read the Info tab of the ship detail page, the one with Gear, Stats and Skills.
Areas are of a 1280x720 screenshot.
"""
import os
import re
import time

import cv2
import numpy as np

from module.base.decorator import cached_property
from module.base.utils import crop, load_image
from module.ocr.ocr import Digit, Ocr

# Stats grid: 3 columns of 5 rows, a cell is a stat, like "HP 5415 +560".
# Submarines have OXY and AMO instead of ASW and SPD, and their SPD moves to the third column.
STAT_ROW_TOPS = [329, 367, 404, 442, 479]
STAT_ROW_HEIGHT = 22
# (label left, value left, value right) of each column, the icon of the stat is on the left of its label
STAT_COLUMNS = [(721, 772, 866), (907, 962, 1052), (1094, 1146, 1240)]
STAT_NAMES = {
    'HP': 'hp', 'FP': 'fp', 'AA': 'aa', 'ASW': 'asw', 'OXY': 'oxy', 'LCK': 'lck',
    'TRP': 'trp', 'AVI': 'avi', 'SPD': 'spd', 'AMO': 'amo', 'COST': 'cost',
    'RLD': 'rld', 'EVA': 'eva', 'ACC': 'acc',
}
ARMORS = ['light', 'medium', 'heavy']
# Stat of each cell of the grid, row by row, "armor" is a word like "Light"
STAT_LAYOUT = [
    'hp', 'armor', 'rld',
    'fp', 'trp', 'eva',
    'aa', 'avi', 'acc',
    'asw', 'spd', None,
    'lck', 'cost', None,
]
STAT_LAYOUT_SUBMARINE = [
    'hp', 'armor', 'rld',
    'fp', 'trp', 'eva',
    'aa', 'avi', 'acc',
    'oxy', 'amo', 'spd',
    'lck', 'cost', None,
]
SUBMARINE_HULLS = ['SS', 'SSV', 'IX']
# Green number after a stat, what gear adds to it
BONUS_COLOR = (148, 255, 99)

# Oathed ships have a pencil to rename them on the left of the name
NAME_AREA = (220, 88, 420, 116)
NAME_AREA_OATH = (248, 88, 420, 116)
# The name is drawn in a window, in its middle if it fits.
# A longer one starts at the left of the window, stays there about half a second, scrolls to the left
# at about 13 pixels a second until its end is 5 pixels from the right of the window, stays there
# about half a second, and starts again.
NAME_WINDOW = (246, 88, 409, 116)
# Columns at the edges of the window, a name with letters there is cut
NAME_EDGE = 2
# Empty columns at the right of the window once a long name has scrolled to its end, more than between 2 letters
NAME_END_GAP = 4
# Pixels a name scrolls from a frame to the next one
NAME_SHIFT_MAX = 40
# Two frames of a name scrolling are this similar where they overlap
NAME_SHIFT_SIMILARITY = 0.6
# A name back at its start is this similar to its first frame
NAME_START_SIMILARITY = 0.8
# Seconds a name stays still before it is taken as one that fits, longer than a long name stays at its start
NAME_STILL_TIME = 1.5
# Black margin around a name put together from frames, the OCR model needs some
NAME_MARGIN = 10
# Words of names in lower case
NAME_PARTICLES = ['da', 'de', 'degli', 'der', 'di', 'von']
HULL_AREA = (152, 86, 212, 118)
HULL_COLOR = (255, 215, 80)
HULLS = ['DD', 'CL', 'CA', 'CB', 'BB', 'BC', 'BM', 'BBV', 'CV', 'CVL', 'SS', 'SSV', 'AR', 'AE', 'IX']
STARS_AREA = (262, 62, 384, 86)
# Word of the affinity icon, like "Love", and where to look for it
AFFINITY_TEMPLATE_FOLDER = './assets/inventory/affinity'
AFFINITY_SEARCH = (560, 115, 670, 168)
AFFINITY_SIMILARITY = 0.55
LEVEL_AREA = (752, 286, 805, 318)
EXP_AREA = (1040, 282, 1248, 304)
# Wide enough for the OCR to keep every digit, "5070" reads as "570" in a tight area
POWER_AREA = (1145, 226, 1250, 272)
# Enhancement level of the 5 gear slots, like "+10", and of the augment
GEAR_LEVEL_AREAS = [(696 + 85 * index, 169, 724 + 85 * index, 188) for index in range(5)]
AUGMENT_LEVEL_AREA = (1137, 178, 1165, 199)
# "LEVEL: 10" of the 3 skills shown, more skills are behind a scroll
SKILL_LEVEL_AREAS = [(820 + 189 * index, 568, 870 + 189 * index, 597) for index in range(3)]
SKILL_LEVEL_COLOR = (110, 110, 110)
# "UN" of the button "UNLOCK" that a locked ship has, an unlocked one has "LOCK"
LOCK_TEMPLATE_FOLDER = './assets/inventory/lock'
LOCK_SEARCH = (874, 664, 914, 697)
LOCK_SIMILARITY = 0.6
FAVORITE_AREA = (1160, 630, 1200, 645)


def stat_cells():
    """
    Yields:
        tuple: (label area, value area) of each cell of the stats grid
    """
    for top in STAT_ROW_TOPS:
        for label, value, right in STAT_COLUMNS:
            yield (label, top, value, top + STAT_ROW_HEIGHT), (value, top, right, top + STAT_ROW_HEIGHT)


def parse_exp(text):
    """
    Args:
        text (str): Like "139714/145000", or "236428/MAX" at the maximum level

    Returns:
        tuple[int, int]: EXP and the EXP of the next level.
            (None, 0) at the maximum level, where EXP does not matter and "/" may read as "7".
            (None, None) if unreadable.
    """
    text = text.replace(' ', '').upper()
    # "MAX" may read as "M8X"
    if 'M' in text:
        return None, 0
    res = re.search(r'(\d+)/(\d+)$', text)
    if not res:
        return None, None
    current, total = int(res.group(1)), int(res.group(2))
    if current > total:
        return None, None
    return current, total


def parse_bonus(text):
    """
    Args:
        text (str): A gear bonus like "+560", which the OCR model reads as "4-560"

    Returns:
        int:
    """
    text = text.replace('D', '0').replace('O', '0')
    res = re.search(r'[-+](\d+)$', text)
    if res:
        return int(res.group(1))
    res = re.search(r'(\d+)$', text)
    return int(res.group(1)) if res else 0


def parse_trailing_number(text, maximum=None):
    """
    Args:
        text (str): Like "-13" for "+13", "LEVEL:10"
        maximum (int): Drop leading digits until the number is not above it

    Returns:
        int:
    """
    text = text.replace('D', '0').replace('O', '0').replace('I', '1').replace('l', '1')
    res = re.search(r'(\d+)$', text)
    if not res:
        return 0
    digits = res.group(1)
    if maximum is not None:
        while len(digits) > 1 and int(digits) > maximum:
            digits = digits[1:]
    return int(digits)


def parse_stat_label(text):
    """
    Args:
        text (str): Like "HP", "Cost", "Light"

    Returns:
        str: Key of the stat, like "hp", or the armor like "armor:light", or '' if unknown
    """
    word = re.sub(r'[^A-Za-z]', '', text).upper()
    if not word:
        return ''
    for armor in ARMORS:
        if word.lower().startswith(armor[:4]):
            return f'armor:{armor}'
    if word in STAT_NAMES:
        return STAT_NAMES[word]
    # OCR may add a letter of the icon on the left
    for name, key in STAT_NAMES.items():
        if word.endswith(name):
            return key
    return ''


def parse_hull(text):
    """
    Args:
        text (str): Like "CL", "1A" for "IX"

    Returns:
        str: Hull type, or the text as it is if unknown
    """
    text = re.sub(r'[^A-Z0-9]', '', text.upper())
    # The OCR model of digits reads "DD" as "22", "BC" as "3C", "AR" as "4R"
    for digit, letter in [('2', 'D'), ('3', 'B'), ('8', 'B'), ('5', 'S'), ('1', 'I'), ('0', 'O'), ('4', 'A')]:
        text = text.replace(digit, letter)
    # "M" of "BM" reads as "IV"
    text = text.replace('IV', 'M')
    if text in HULLS:
        return text
    if text.startswith('I') and len(text) == 2:
        return 'IX'
    # A "D" of "DD" missed
    if text == 'D':
        return 'DD'
    return text


def tidy_name(text):
    """
    The OCR of the big font of the name cuts words, like "T ige r", "Shim ak aze", "U-55 7".
    A part that starts in lower case or with a digit belongs to the word before it,
    words of a name start in upper case, like "Dmitri Donskoi", "Ning Hai (Retrofit)",
    but for particles, like "Conte di Cavour", "Friedrich der Grosse".

    Args:
        text (str):

    Returns:
        str:
    """
    words = []
    for word in text.split():
        # "II" of "Yorktown II" reads as "lI"
        if re.fullmatch(r'[lI1|]{2,3}', word):
            word = 'I' * len(word)
        if words and (word[0].islower() or word[0].isdigit()) and word not in NAME_PARTICLES:
            words[-1] += word
        else:
            words.append(word)
    name = ' '.join(words)
    # Leftover of a letter cut at the edge of the name box, like ".ondon (Retrofit"
    name = name.lstrip('.,\'`-_~ ')
    # "I" of submarines like "I-404" reads as "1" or "l"
    return re.sub(r'^[1lI|]-(?=\d)', 'I-', name)


def name_letters(window):
    """
    Args:
        window (np.ndarray): NAME_WINDOW of a screenshot

    Returns:
        np.ndarray: Mask of the white letters
    """
    window = window.astype(np.int16)
    return (window.min(axis=2) > 200) & (window.max(axis=2) - window.min(axis=2) < 40)


def name_gaps(window):
    """
    Returns:
        tuple[int, int]: Empty columns between the name and the left of its window, the right of its window,
            or None if no name
    """
    columns = np.where(name_letters(window).any(axis=0))[0]
    if not len(columns):
        return None
    return int(columns[0]), int(window.shape[1] - 1 - columns[-1])


def name_shift(before, after):
    """
    Args:
        before (np.ndarray): NAME_WINDOW of a frame
        after (np.ndarray): NAME_WINDOW of a later frame

    Returns:
        tuple[int, float]: Pixels the name has scrolled to the left, and how similar the frames are where they overlap
    """
    a, b = name_letters(before), name_letters(after)
    width = a.shape[1]
    best = 0, 0.
    for shift in range(0, NAME_SHIFT_MAX + 1):
        x, y = a[:, shift:], b[:, :width - shift]
        union = (x | y).sum()
        if not union:
            continue
        similarity = (x & y).sum() / union
        if similarity > best[1]:
            best = shift, similarity
    return best


class NameScroll:
    """
    Puts the name of a ship together from its window in frames one after the other, from the page arriving,
    until the name is known in full. Frames are added until done.
    """

    def __init__(self):
        # (time, window) of the frame before
        self.previous = None
        # Frames added
        self.count = 0
        # Parts of the image of the name, from the first frame of the chain
        self.pieces = []
        # Time of the first frame of the chain
        self.since = 0.
        # If the chain began at the start of the name
        self.start = False
        self.moved = False
        self.done = False
        self.whole = False

    def begin(self, window, now):
        gaps = name_gaps(window)
        self.pieces = [window]
        self.since = now
        self.moved = False
        # A long name stays at its start a moment
        self.start = gaps is not None and gaps[0] >= NAME_EDGE

    def is_start(self, window):
        """
        Returns:
            bool: If the window shows the name at its start, as the first frame of the chain
        """
        shift, similarity = name_shift(self.pieces[0], window)
        return shift <= NAME_EDGE and similarity > NAME_START_SIMILARITY

    def finish(self, whole, window=None):
        if window is not None:
            # A name that fits, the latest frame is drawn to its end
            self.pieces = [window]
        self.done = True
        self.whole = whole

    def add(self, window, now=None):
        """
        Args:
            window (np.ndarray): NAME_WINDOW of a frame
            now (float): Time of the frame, now if None
        """
        if self.done:
            return
        now = time.time() if now is None else now
        self.count += 1
        previous, self.previous = self.previous, (now, window)
        if previous is None:
            return
        shift, similarity = name_shift(previous[1], window)
        if not self.pieces:
            # The first frame that the next one follows, earlier ones may be of the page arriving
            if similarity >= NAME_SHIFT_SIMILARITY:
                self.begin(previous[1], previous[0])
                # Only the first frames of the page show the start of a long name for sure
                self.start = self.start and self.count <= 3
            else:
                return
        elif similarity < NAME_SHIFT_SIMILARITY:
            if self.moved and self.start and self.is_start(window):
                # The name started again, the frame before showed its end
                self.finish(whole=True)
            else:
                # The page changed, the game skipped some scrolling, or the start of the name was missed
                # and it starts again now
                self.begin(window, now)
            return

        if shift:
            self.moved = True
            # Columns the window has scrolled to
            self.pieces.append(window[:, -shift:])
            return
        gaps = name_gaps(window)
        if gaps is None:
            return
        if self.moved:
            if gaps[0] < NAME_EDGE and gaps[1] >= NAME_END_GAP:
                # Stays at its end
                self.finish(whole=self.start)
        elif now - self.since >= NAME_STILL_TIME and gaps[0] >= NAME_EDGE and gaps[1] >= NAME_EDGE:
            # A name that fits, a long one would have scrolled by now
            self.finish(whole=True, window=window)

    @property
    def image(self):
        """
        Returns:
            np.ndarray: Image of the name, with a black margin, or None if no frame
        """
        if self.pieces:
            image = np.concatenate(self.pieces, axis=1)
        elif self.previous is not None:
            image = self.previous[1]
        else:
            return None
        margin = np.zeros((image.shape[0], NAME_MARGIN, 3), dtype=image.dtype)
        return np.concatenate([margin, image, margin], axis=1)


def count_stars(image):
    """
    Returns:
        int: Stars lit, the limit break of the ship
    """
    area = crop(image, STARS_AREA).astype(int)
    # The middle of a lit star is pale yellow, like (251, 235, 153)
    lit = (area[:, :, 0] > 235) & (area[:, :, 1] > 215) & (area[:, :, 2] > 120) & (area[:, :, 2] < 190)
    columns = lit.sum(axis=0) > 2
    # Count runs of lit columns, stars are about 19 pixels apart with a dark outline in between
    runs = 0
    width = 0
    for column in list(columns) + [False]:
        if column:
            width += 1
        else:
            if width >= 3:
                runs += 1
            width = 0
    return runs


def has_bonus(image):
    """
    Args:
        image (np.ndarray): Value area of a stat cell

    Returns:
        bool: If it has a green number of what gear adds
    """
    image = image.astype(int)
    green = (image[:, :, 1] > 200) & (image[:, :, 1] - image[:, :, 0] > 60) & (image[:, :, 2] < 170)
    return bool(green.sum() > 10)


def is_favorite(image):
    """
    "ON" is yellow, "OFF" is white
    """
    area = crop(image, FAVORITE_AREA).astype(int)
    yellow = (area[:, :, 0] > 200) & (area[:, :, 1] > 180) & (area[:, :, 2] < 120)
    return bool(yellow.sum() > 15)


def load_templates(folder):
    """
    Args:
        folder (str): Images named by what they show, a name may have several images, like "oath.png", "oath_2.png"

    Returns:
        list[tuple[str, np.ndarray]]: (name, image)
    """
    out = []
    for file in sorted(os.listdir(folder)):
        if file.endswith('.png'):
            out.append((file[:-4].split('_')[0], load_image(os.path.join(folder, file))))
    return out


def best_template(area, templates):
    """
    Args:
        area (np.ndarray): Image to look in, larger than the templates
        templates (list[tuple[str, np.ndarray]]):

    Returns:
        tuple[str, float]: Name of the best template and its similarity
    """
    best, similarity = '', 0.
    for name, template in templates:
        _, sim, _, _ = cv2.minMaxLoc(cv2.matchTemplate(area, template, cv2.TM_CCOEFF_NORMED))
        if sim > similarity:
            best, similarity = name, sim
    return best, similarity


class InfoReader:
    """
    Reads one Info page into a dict, OCR models are loaded once.
    """

    def __init__(self):
        self.cells = list(stat_cells())
        # Labels are read only if the hull is unknown, every hull has one of the 2 layouts
        self.ocr_labels = Ocr([label for label, _ in self.cells], lang='cnocr', letter=(255, 255, 255),
                              threshold=128, name='INFO_STAT_LABEL')
        # Given the crops of the cells to read
        self.ocr_values = Digit([value for _, value in self.cells], letter=(255, 255, 255), threshold=128,
                                name='INFO_STAT_VALUE')
        self.ocr_bonuses = Ocr([value for _, value in self.cells], letter=BONUS_COLOR, threshold=128,
                               name='INFO_STAT_BONUS')
        # The armor spans the label and the value of its cell
        label, value = self.cells[1]
        self.ocr_armor = Ocr((label[0], label[1], value[2], label[3]), lang='cnocr', letter=(255, 255, 255),
                             threshold=128, name='INFO_ARMOR')
        self.ocr_name = Ocr(NAME_AREA, lang='cnocr', letter=(255, 255, 255), threshold=128, name='INFO_NAME')
        self.ocr_name_oath = Ocr(NAME_AREA_OATH, lang='cnocr', letter=(255, 255, 255), threshold=128,
                                 name='INFO_NAME')
        self.ocr_hull = Ocr(HULL_AREA, letter=HULL_COLOR, threshold=160, name='INFO_HULL')
        self.ocr_level = Digit(LEVEL_AREA, letter=(255, 255, 255), threshold=128, name='INFO_LEVEL')
        self.ocr_exp = Ocr(EXP_AREA, letter=(255, 255, 255), threshold=128, name='INFO_EXP')
        self.ocr_power = Digit(POWER_AREA, letter=(255, 255, 255), threshold=128, name='INFO_POWER')
        self.ocr_gear = Ocr(GEAR_LEVEL_AREAS + [AUGMENT_LEVEL_AREA], letter=(255, 255, 255), threshold=128,
                            name='INFO_GEAR_LEVEL')
        self.ocr_skills = Ocr(SKILL_LEVEL_AREAS, letter=SKILL_LEVEL_COLOR, threshold=64, alphabet='0123456789',
                              name='INFO_SKILL_LEVEL')

    @cached_property
    def affinity_templates(self):
        return load_templates(AFFINITY_TEMPLATE_FOLDER)

    @cached_property
    def lock_templates(self):
        return load_templates(LOCK_TEMPLATE_FOLDER)

    def read_affinity(self, image):
        """
        Returns:
            str: Like "oath", "love", "crush", "friendly", "stranger", "synced" of META ships,
                or "" if none matches
        """
        name, similarity = best_template(crop(image, AFFINITY_SEARCH), self.affinity_templates)
        return name if similarity > AFFINITY_SIMILARITY else ''

    def read_locked(self, image):
        _, similarity = best_template(crop(image, LOCK_SEARCH), self.lock_templates)
        return similarity > LOCK_SIMILARITY

    def read_stats(self, image, hull):
        """
        Args:
            image (np.ndarray):
            hull (str):

        Returns:
            dict, str: {stat: [value, bonus of gear]}, armor
        """
        if hull in SUBMARINE_HULLS:
            keys = STAT_LAYOUT_SUBMARINE
        elif hull in HULLS:
            keys = STAT_LAYOUT
        else:
            keys = [parse_stat_label(label) or None for label in self.ocr_labels.ocr(image)]

        armor = ''
        if 'armor' in keys:
            armor = parse_stat_label(self.ocr_armor.ocr(image))
        else:
            armor = next((key for key in keys if key and key.startswith('armor:')), '')
        armor = armor[6:] if armor.startswith('armor:') else ''

        cells = [(key, crop(image, value)) for key, (_, value) in zip(keys, self.cells)
                 if key and key != 'armor' and not key.startswith('armor:')]
        if not cells:
            return {}, armor
        values = self.ocr_values.ocr([area for _, area in cells], direct_ocr=True)
        # Most cells have no green number, OCR only those that have
        green = [has_bonus(area) for _, area in cells]
        crops = [area for (_, area), g in zip(cells, green) if g]
        bonuses = iter(self.ocr_bonuses.ocr(crops, direct_ocr=True) if crops else [])
        stats = {}
        for (key, _), value, g in zip(cells, values, green):
            stats[key] = [value, parse_bonus(next(bonuses)) if g else 0]
        return stats, armor

    def read_name(self, image, affinity, name=None):
        """
        Args:
            image (np.ndarray): Screenshot of the Info tab
            affinity (str):
            name (NameScroll): The name put together from its frames, or None to read it on the screenshot

        Returns:
            tuple[str, bool]: Name, and if it is whole, None if not known
        """
        if name is not None and name.image is not None:
            whole = name.whole
            name = tidy_name(self.ocr_name.ocr([name.image], direct_ocr=True))
            # Names start in upper case, "C" may read as "c"
            if whole:
                name = name[:1].upper() + name[1:]
            return name, whole
        if affinity == 'oath':
            return tidy_name(self.ocr_name_oath.ocr(image)), None
        return tidy_name(self.ocr_name.ocr(image)), None

    def read(self, image, name=None):
        """
        Args:
            image (np.ndarray): Screenshot of the Info tab
            name (NameScroll): The name put together from its frames, or None to read it on the screenshot

        Returns:
            dict:
        """
        hull = parse_hull(self.ocr_hull.ocr(image))
        stats, armor = self.read_stats(image, hull)
        affinity = self.read_affinity(image)
        name, whole = self.read_name(image, affinity, name)
        exp, exp_next = parse_exp(self.ocr_exp.ocr(image))
        gear = [parse_trailing_number(text, maximum=13) for text in self.ocr_gear.ocr(image)]
        skills = [parse_trailing_number(text, maximum=10) for text in self.ocr_skills.ocr(image)]

        return {
            'name': name,
            'name_whole': whole,
            'hull': hull,
            'stars': count_stars(image),
            'affinity': affinity,
            'level': self.ocr_level.ocr(image),
            'exp': exp,
            'exp_next': exp_next,
            'power': self.ocr_power.ocr(image),
            'armor': armor,
            'stats': stats,
            'gear_levels': gear[:5],
            'augment_level': gear[5],
            'skill_levels': skills,
            'locked': self.read_locked(image),
            'favorite': is_favorite(image),
        }
