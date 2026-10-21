import os

import cv2
import numpy as np

from module.commission.commission import TEAM_AREA, requirement_unmet

DATA = os.path.join(os.path.dirname(__file__), 'data')
# Top of COMMISSION_ADVICE in these screenshots
TOP = 339


def screen(file, left=220, top=225):
    """
    A screenshot of the EN client at 1280x720, black but for a commission unfolded.
    """
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    part = cv2.cvtColor(cv2.imread(os.path.join(DATA, file)), cv2.COLOR_BGR2RGB)
    height, width = part.shape[:2]
    image[top:top + height, left:left + width] = part
    return image


def test_level_requirement_no_ship_meets():
    # "Daily Resource Extraction IV" on the F2P account: "Have at least 1 lv. 10 ship." in red after "Recommend",
    # with 6 ships of level 1 in the team
    assert requirement_unmet(screen('commission_lv10_unmet.png'), TOP) is True


def test_requirements_met():
    # "Fleet Combat Exercise" on the main account: yellow and green requirements with green ticks
    assert requirement_unmet(screen('commission_requirements_met.png'), TOP) is False


def test_team_emptied_by_the_game_bug_is_left_to_the_restart():
    # After "Recommend", the ships may disappear while the commission flashes, a restart of the game fixes it
    image = screen('commission_lv10_unmet.png')
    x1, y1, x2, y2 = TEAM_AREA
    image[TOP + y1:TOP + y2, x1:x2] = (40, 50, 80)

    assert requirement_unmet(image, TOP) is False


def test_nothing_unfolded():
    assert requirement_unmet(np.zeros((720, 1280, 3), dtype=np.uint8), TOP) is False


def test_commissions_that_can_not_start_give_way_to_the_next_ones():
    # The F2P account could do none of the 3 daily ones chosen, its slots stayed empty while others it could do were
    # listed. The chosen ones come first, then the other commissions of the filter, once each.
    from module.commission.commission import commission_candidates
    from module.map.map_grids import SelectedGrids

    class Comm:
        def __init__(self, name):
            self.name = name

        def __eq__(self, other):
            return isinstance(other, Comm) and other.name == self.name

        def __repr__(self):
            return self.name

    iii, ii, i, gear = Comm('Daily Resource Extraction III'), Comm('Daily Resource Extraction II'), \
        Comm('Daily Resource Extraction I'), Comm('Gear Transport')
    daily_choose = SelectedGrids([iii, ii])
    urgent_choose = SelectedGrids([gear])
    comm_choose = SelectedGrids([iii, ii, gear, 'shortest', Comm('Daily Resource Extraction I')])

    assert commission_candidates(daily_choose, urgent_choose, comm_choose) == [iii, ii, gear, i]
