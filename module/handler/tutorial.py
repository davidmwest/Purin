"""
Tutorials of a new account. When a feature unlocks, the game takes the player to it: it dims the screen,
points at the button to tap with a blue hand in a pulsing ring, and takes no other tap until the tutorial is done.
"""
import cv2
import numpy as np

# The hand as its blue minus its red, the screen shows through it. The pulsing ring is left out.
TUTORIAL_HAND_TEMPLATE = './assets/tutorial/hand.png'
TUTORIAL_HAND_SIMILARITY = 0.7
# The tip of the finger is at the upper left of the hand
TUTORIAL_HAND_TIP = (3, 3)


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
