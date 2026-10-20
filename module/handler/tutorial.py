"""
Tutorials of a new account. When a feature unlocks, the game takes the player to it: it points at the button to tap
with a blue hand in a pulsing ring, says what it is in a dialog of its guide, and takes no other tap until
the tutorial is done. A step with a dialog alone goes on with a tap.
"""
import cv2
import numpy as np

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
# The voice wave at the lower left of a dialog of the guide
TUTORIAL_DIALOG_TEMPLATE = './assets/tutorial/dialog_voice.png'
TUTORIAL_DIALOG_SIMILARITY = 0.85


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


def tutorial_dialog(image, template):
    """
    Args:
        image (np.ndarray): Screenshot, RGB
        template (np.ndarray): TUTORIAL_DIALOG_TEMPLATE

    Returns:
        tuple[int, int]: A point in a dialog of the guide, or None
    """
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    result = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)
    _, similarity, _, location = cv2.minMaxLoc(result)
    if similarity < TUTORIAL_DIALOG_SIMILARITY:
        return None
    height, width = template.shape
    return location[0] + width // 2, location[1] + height // 2


def is_same_step(point, previous):
    """
    Returns:
        bool: If the hand points where it did, the step is the same
    """
    if previous is None:
        return False
    return abs(point[0] - previous[0]) <= TUTORIAL_SAME_STEP and abs(point[1] - previous[1]) <= TUTORIAL_SAME_STEP
