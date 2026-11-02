"""Rotate a training resident at the leveling goal, before checking food eligibility."""
import re
from datetime import datetime
from pathlib import Path

from module.base.button import Button
from module.base.utils import save_image
from module.dorm.awakening import (DormAwakening, boundary_identity, level_can_gain_exp,
                                   same_name, same_residents)
from module.logger import logger
from module.mood.assets import DORM_PICKER_CANCEL, DORM_PICKER_CONFIRM
from module.mood.dorm_rest import (CARD_DELTA_X, CARD_WIDTH, CARD_X, ROW_TOP_MAX, ROW_TOP_MIN,
                                   DormReadError, picker_drawn, picker_row_tops, read_picker_row)
from module.ui.page import page_dorm

TRAINING_GOAL = 120


def resident_card(resident, cards):
    matches = [card for card in cards if not card.remove and card.level == resident.level
               and same_name(card.name, resident.name)]
    if len(matches) != 1:
        raise DormReadError(f'Training: {resident.name} Lv.{resident.level} has '
                            f'{len(matches)} matching picker cards')
    return matches[0]


def training_replacement(train, rest, cards, removed):
    """Choose a unique fleet ship below 120, preserving both floor rosters."""
    if not picker_drawn(cards):
        raise DormReadError('Training picker has not finished drawing')
    occupied = [resident_card(ship, cards) for ship in train + rest]
    if len({card.index for card in occupied}) != len(occupied):
        raise DormReadError('Training: resident identities overlap')
    expected = {resident_card(ship, cards).index for ship in train if ship is not removed}
    if {card.index for card in cards if card.selected and not card.remove} != expected:
        raise DormReadError('Training: selected cards do not match the unchanged residents')
    occupied_indices = {card.index for card in occupied}
    candidates = []
    for card in cards:
        if (card.index in occupied_indices or card.remove or card.selected or card.submarine
                or not card.fleet or not 1 <= card.level < TRAINING_GOAL):
            continue
        identity = boundary_identity(card.name)
        if identity is None:
            continue
        # A same-name duplicate at any level makes a later name lookup unsafe.
        copies = [other for other in cards if not other.remove
                  and (boundary_identity(other.name) == identity or same_name(other.name, card.name))]
        if len(copies) != 1:
            continue
        candidates.append(card)
    # Prefer an active farming-fleet member with directly proved EXP room.
    candidates.sort(key=lambda card: (card.fleet not in (1, 2), not level_can_gain_exp(card.level),
                                      card.mood, card.index))
    return candidates[0] if candidates else None


class DormTraining(DormAwakening):
    def _training_diagnostic(self, phase):
        """Keep bounded actual frames for an unread census or picker transition."""
        try:
            folder = Path('./log/dorm_training')
            folder.mkdir(parents=True, exist_ok=True)
            account = re.sub(r'[^a-zA-Z0-9_-]', '_', str(self.config.config_name))
            path = folder / f'{datetime.now():%Y-%m-%d_%H-%M-%S_%f}_{account}_{phase}.png'
            save_image(self.device.image, str(path))
            logger.info(f'Training {phase} screen: {path}')
            for old in sorted(folder.glob(f'*_{account}_*.png'))[:-12]:
                old.unlink()
        except Exception as error:
            logger.warning(f'Training diagnostic not saved: {error}')

    def _training_find(self, target):
        """Find the bound name AND level on fresh frames; never select by fuzzy name alone."""
        identity = boundary_identity(target.name)
        self.picker_scroll_top()
        for _ in range(26):
            self.device.screenshot()
            found = []
            for top in picker_row_tops(self.device.image):
                if not ROW_TOP_MIN <= top <= ROW_TOP_MAX:
                    continue
                for card in read_picker_row(self.device.image, top, row=0):
                    if (not card.remove and card.level == target.level
                            and boundary_identity(card.name) == identity):
                        found.append((card, top))
            if len(found) > 1:
                raise DormReadError('Training: replacement identity became ambiguous')
            if found:
                card, top = found[0]
                if card.selected or card.submarine or card.fleet != target.fleet:
                    raise DormReadError('Training: replacement state changed')
                return int(CARD_X + CARD_DELTA_X * card.col), top
            self._picker_drag(.5)
        raise DormReadError('Training: replacement was not found again')

    def rotate_training(self):
        """Replace at most one >=120 resident per run, with complete before/after proof.

        This goal avoids leaving a finished leveling ship in the dorm. Whether a
        boundary ship can bank EXP is separately checked by can_feed(), using Info.
        """
        picker_open = False
        list_open = False
        try:
            self.ui_ensure(page_dorm)
            self.dorm_ship_list_enter()
            list_open = True
            train = self._food_floor('train')
            self._training_diagnostic('before')
            finished = [ship for ship in train if ship.level >= TRAINING_GOAL]
            if not finished:
                return True
            # Rotation needs a complete rest-floor census, so it never takes a
            # morale resident. This path is not needed on the young F2P floor.
            rest = self._food_floor('rest')
            removed = finished[0]
            slot = removed.slot
            if type(slot) is not int or not 0 <= slot < 6:
                raise DormReadError('Training: resident slot is not verified')
            self.dorm_ship_list_set_tab('train')
            if not same_residents(train, self._food_floor('train')):
                raise DormReadError('Training residents changed before opening the picker')
            self.picker_enter(place='train', slot=slot)
            picker_open = True
            cards = self.picker_scan([ship.name for ship in train + rest])
            self._training_diagnostic('picker')
            replacement = training_replacement(train, rest, cards, removed)
            if replacement is None:
                logger.info('Training: no unique eligible fleet replacement; roster stays unchanged')
                return True
            if self.picker_selected_count() != len(train) - 1:
                raise DormReadError('Training: unexpected selection count before replacement')
            x, top = self._training_find(replacement)
            if self.picker_selected_count() != len(train) - 1:
                raise DormReadError('Training: selection changed while finding replacement')
            area = (x + 30, top + 60, x + CARD_WIDTH - 30, top + 140)
            logger.info(f'Training: replace {removed.name} Lv.{removed.level} with '
                        f'{replacement.name} Lv.{replacement.level}, fleet {replacement.fleet}')
            self.device.click(Button(area=area, color=(), button=area, name='DORM_TRAIN_REPLACEMENT'))
            self.device.sleep(.8)
            self.device.screenshot()
            if self.handle_dorm_food_popup():
                self.device.sleep(.8)
                self.device.screenshot()
            if self.picker_selected_count() != len(train):
                raise DormReadError('Training: replacement did not produce the exact selection count')
            self.dock_filter_set(sort='level')
            self.dock_sort_method_dsc_set(True)
            self.picker_confirm(DORM_PICKER_CONFIRM)
            picker_open = False
            after = self._food_floor('train')
            self._training_diagnostic('after')
            unchanged = [ship for ship in train if ship is not removed]
            expected = unchanged + [replacement]
            if (len(after) != len(expected) or any(
                    len([ship for ship in after if ship.level == wanted.level
                         and same_name(ship.name, wanted.name)]) != 1 for wanted in expected)):
                raise DormReadError('Training: changed roster could not be verified; food stays unchanged')
            if not same_residents(rest, self._food_floor('rest')):
                raise DormReadError('Training: rest roster changed; food stays unchanged')
            logger.info('Training: replacement verified; rest-floor residents preserved')
            return True
        except DormReadError as exc:
            self._training_diagnostic('deferred')
            logger.warning(f'Training rotation deferred: {exc}')
            return False
        finally:
            if picker_open:
                self.picker_confirm(DORM_PICKER_CANCEL)
            if list_open:
                self.dorm_ship_list_quit()
            self.ui_ensure(page_dorm)
