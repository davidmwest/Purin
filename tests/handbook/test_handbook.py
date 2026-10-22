import os
from types import SimpleNamespace
from unittest.mock import Mock

import cv2
import pytest

import module.config.server as server
from module.handbook.handbook import Handbook, find_badges, plan_taps

DATA = os.path.join(os.path.dirname(__file__), 'data')


@pytest.fixture(autouse=True)
def en():
    previous = server.server
    server.set_server('en')
    yield
    server.set_server(previous)


def load(name):
    # The F2P account, 2560x1440 taken down to 1280x720, 2026-10-22
    return cv2.cvtColor(cv2.imread(os.path.join(DATA, f'{name}.png')), cv2.COLOR_BGR2RGB)


def near(found, expected, tolerance=3):
    return len(found) == len(expected) and all(
        abs(a[0] - b[0]) <= tolerance and abs(a[1] - b[1]) <= tolerance for a, b in zip(found, expected))


@pytest.mark.parametrize('name, badges', [
    # Tabs, Phase 1, the Collect of mission 1
    ('rookie', [(872, 50), (997, 50), (1122, 50), (154, 145), (1185, 157)]),
    # Tabs, Series 1, the Collect of missions 1 and 2
    ('dev', [(872, 50), (997, 49), (1122, 50), (154, 145), (1185, 157), (1185, 257)]),
    # Tabs, the arrow under the tutorials, the reward of the tutorial at the bottom
    ('mechanic', [(872, 50), (997, 50), (1122, 49), (229, 614), (1174, 619)]),
])
def test_badges_of_the_handbook(name, badges):
    assert near(find_badges(load(name)), sorted(badges, key=lambda c: (c[1], c[0])))


def test_what_to_tap_first():
    plan = plan_taps(find_badges(load('dev')))

    # The Collect buttons of the missions shown, top first
    assert [kind for kind, _, _ in plan[:2]] == ['mission', 'mission']
    assert abs(plan[0][2][0] - 1108) <= 3 and abs(plan[0][2][1] - 175) <= 3
    # Then the page opened, then the tabs
    assert [kind for kind, _, _ in plan[2:]] == ['page', 'tab', 'tab', 'tab']


def test_the_reward_of_a_tutorial_comes_before_other_pages():
    plan = plan_taps(find_badges(load('mechanic')))

    assert plan[0][0] == 'page_reward'
    # The big button at the bottom
    assert abs(plan[0][2][0] - 1058) <= 3 and abs(plan[0][2][1] - 641) <= 3
    assert plan[1][0] == 'pages_more'


def handbook(image):
    hb = Handbook.__new__(Handbook)
    hb._templates = {}
    hb.device = SimpleNamespace(image=image)
    return hb


@pytest.mark.parametrize('name, title, entrance', [
    ('main', False, True),
    ('rookie', True, False),
    ('dev', True, False),
    ('mechanic', True, False),
])
def test_pages(name, title, entrance):
    hb = handbook(load(name))

    assert hb.is_handbook() is title
    assert hb.has_entrance() is entrance


def without_badges(image, badges):
    """
    The same screen with these badges gone, painted with what is left of them.
    """
    image = image.copy()
    for x, y in badges:
        image[y - 12:y + 13, x - 12:x + 13] = image[y - 12, x - 14]
    return image


def test_collect_taps_every_mission_then_stops():
    dev = load('dev')
    screens = {'now': dev}
    clicks = []

    def click(button):
        x1, y1, x2, y2 = button.area
        point = ((x1 + x2) // 2, (y1 + y2) // 2)
        clicks.append(point)
        # A Collect taken: its badge goes, and the page and tabs lose theirs once nothing is left
        if abs(point[0] - 1108) <= 3:
            left = [b for b in find_badges(screens['now']) if not (b[0] > 1150 and abs(b[1] - (point[1] - 18)) <= 4)]
            if not any(b[0] > 1150 for b in left):
                left = []
            screens['now'] = without_badges(dev, [b for b in find_badges(dev) if b not in left])

    device = SimpleNamespace(screenshot=lambda: screens['now'], click=click, sleep=Mock(), swipe=Mock())
    hb = handbook(dev)
    hb.device = device
    device.image = dev

    def screenshot():
        device.image = screens['now']
        return device.image

    device.screenshot = screenshot
    hb.appear = Mock(return_value=False)

    assert hb.handbook_collect() == 2
    collects = [c for c in clicks if abs(c[0] - 1108) <= 3]
    assert [c[1] for c in collects] == [175, 275]
