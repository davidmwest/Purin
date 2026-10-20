"""
Tutorials of a new account. When a feature unlocks, the game takes the player to it: it points at the button to tap
with a blue hand in a pulsing ring, says what it is in a dialog of its guide, and takes no other tap until
the tutorial is done. A step with a dialog alone goes on with a tap.

The game starts them wherever the player is, like on the main page after a battle once the commander level
unlocks commissions, so Device.screenshot() follows them, whatever a task is waiting for.
"""
import cv2
import numpy as np

# Looking for a tutorial takes about 40ms, once a second is soon enough
TUTORIAL_CHECK_INTERVAL = 1
# Seconds without a tutorial on screen before the task gets the screen back, the next step may come after a moment
TUTORIAL_GONE = 2

# The hand as its blue minus its red, the screen shows through it. The pulsing ring is left out.
TUTORIAL_HAND_TEMPLATE = './assets/tutorial/hand.png'
TUTORIAL_HAND_SIMILARITY = 0.7
# The tip of the finger is at the upper left of the hand
TUTORIAL_HAND_TIP = (3, 3)
# The hand may point a bit beside what takes the tap, like right above the button "Supplies" of the dorm.
# Taps again at the same step go around the tip, below it first.
TUTORIAL_TAP_OFFSETS = [(0, 0), (0, 35), (-35, 35), (35, 35), (0, -35), (-40, 0), (40, 0)]
# Pixels the hand may move between two frames of the same step
TUTORIAL_SAME_STEP = 15
# The voice wave at a lower corner of a dialog of the guide, some dialogs are see-through
TUTORIAL_DIALOG_TEMPLATE = './assets/tutorial/dialog_voice.png'
# Dialogs score 0.93 to 1, the most like one on 205 screenshots of errors 0.54
TUTORIAL_DIALOG_SIMILARITY = 0.8
# The hand turns from a step to another, and the template of its blue misses it over a blue icon.
# With a dialog of the guide on screen, it is found by its color instead: light blue, hue 103 to 108 of 180
HAND_HUE = (100, 112)
HAND_SATURATION = (25, 120)
HAND_VALUE = 200
# Pixels of the hand of that color, and the share of its box they fill
HAND_AREA = (900, 4000)
HAND_SIZE = (30, 110)
HAND_FILL = (0.35, 0.8)


def blueness(image):
    """
    Args:
        image (np.ndarray): Screenshot, RGB

    Returns:
        np.ndarray: Blue minus red, 0 to 255
    """
    image = image.astype(np.int16)
    return np.clip(image[:, :, 2] - image[:, :, 0], 0, 255).astype(np.uint8)


def tutorial_hand(image, template):
    """
    Args:
        image (np.ndarray): Screenshot
        template (np.ndarray): TUTORIAL_HAND_TEMPLATE

    Returns:
        tuple[int, int]: Where the hand of a tutorial points, or None if no tutorial
    """
    result = cv2.matchTemplate(blueness(image), template, cv2.TM_CCOEFF_NORMED)
    _, similarity, _, location = cv2.minMaxLoc(result)
    if similarity < TUTORIAL_HAND_SIMILARITY:
        return None
    return location[0] + TUTORIAL_HAND_TIP[0], location[1] + TUTORIAL_HAND_TIP[1]


def hand_by_color(image):
    """
    Args:
        image (np.ndarray): Screenshot, RGB

    Returns:
        tuple[int, int]: The tip of the finger, the top of the hand, or None
    """
    hsv = cv2.cvtColor(image, cv2.COLOR_RGB2HSV)
    hue, saturation, value = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    mask = (hue >= HAND_HUE[0]) & (hue <= HAND_HUE[1]) \
        & (saturation >= HAND_SATURATION[0]) & (saturation <= HAND_SATURATION[1]) & (value >= HAND_VALUE)
    mask = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    # The outline of the fingers splits the hand
    merged = cv2.dilate(mask, np.ones((9, 9), np.uint8))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(merged, connectivity=8)
    width = mask.shape[1]
    best = None
    for label in range(1, count):
        x, y, w, h, _ = stats[label]
        # The sea and the sky of a page go to its sides and its top. A hand may point at a button at the bottom.
        if x == 0 or y == 0 or x + w >= width:
            continue
        if not (HAND_SIZE[0] <= w <= HAND_SIZE[1] and HAND_SIZE[0] <= h <= HAND_SIZE[1]):
            continue
        inside = (labels == label) & (mask > 0)
        area = int(inside.sum())
        if not (HAND_AREA[0] <= area <= HAND_AREA[1] and HAND_FILL[0] <= area / (w * h) <= HAND_FILL[1]):
            continue
        if best is None or area > best[0]:
            ys, xs = np.where(inside)
            best = area, (int(xs[np.argmin(ys)]), int(ys.min()))
    return best[1] if best else None


def tutorial_dialog(image, template):
    """
    Args:
        image (np.ndarray): Screenshot, RGB
        template (np.ndarray): TUTORIAL_DIALOG_TEMPLATE

    Returns:
        tuple[int, int]: A point in a dialog of the guide, or None
    """
    # The bars of the wave go up and down while the guide talks, a frame of it is no template for the others.
    # The darkest pixel of each column, whatever the height of its bar, is the same from a frame to another.
    height, width = template.shape
    gray = cv2.erode(cv2.cvtColor(image, cv2.COLOR_RGB2GRAY), np.ones((height, 1), np.uint8))
    result = cv2.matchTemplate(gray, template.min(axis=0, keepdims=True), cv2.TM_CCOEFF_NORMED)
    _, similarity, _, location = cv2.minMaxLoc(result)
    if similarity < TUTORIAL_DIALOG_SIMILARITY:
        return None
    return location[0] + width // 2, location[1]


def is_same_step(point, previous):
    """
    Returns:
        bool: If the hand points where it did, the step is the same
    """
    if previous is None:
        return False
    return abs(point[0] - previous[0]) <= TUTORIAL_SAME_STEP and abs(point[1] - previous[1]) <= TUTORIAL_SAME_STEP


def tutorial_target(image, hand_template, dialog_template):
    """
    Args:
        image (np.ndarray): Screenshot, RGB
        hand_template (np.ndarray): TUTORIAL_HAND_TEMPLATE
        dialog_template (np.ndarray): TUTORIAL_DIALOG_TEMPLATE

    Returns:
        tuple[str, tuple[int, int]]: ('hand', where it points) or ('dialog', a point in the dialog), or None
    """
    dialog = tutorial_dialog(image, dialog_template)
    if dialog is not None:
        hand = hand_by_color(image)
        if hand is not None:
            return 'hand', hand
        return 'dialog', dialog
    # A hand without a dialog, it has to look like the one of the template
    hand = tutorial_hand(image, hand_template)
    if hand is not None:
        return 'hand', hand
    return None
