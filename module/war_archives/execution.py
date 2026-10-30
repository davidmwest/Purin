"""One observed War Archives sortie; the caller owns the shared attempt ledger.

This deliberately does not use CampaignWarArchives.run(): its legacy data-key
handler suppresses the daily reminder and retries confirmation without a bound.
A navigation provider must first establish
the exact archive/stage from captured UI. Unknown observations never authorize
navigation or combat. The provider is responsible for bounding its own controls.
"""
from contextlib import contextmanager
import re
from time import monotonic

import cv2
import numpy as np

from module.base.timer import Timer
from module.campaign.campaign_status import OilReadError
from module.campaign.run import CampaignRun
from module.campaign.stage import stage_names
from module.config.config import TaskEnd
from module.combat.assets import GET_ITEMS_1, GET_ITEMS_2, GET_ITEMS_3, GET_SHIP
from module.eventstory.eventstory import save_event_diagnostic
from module.exception import CampaignEnd, OilExhausted, ScriptEnd
from module.handler.assets import POPUP_CANCEL, POPUP_CONFIRM, USE_DATA_KEY
from module.handler.fast_forward import to_map_file_name
from module.logger import logger
from module.map.assets import FLEET_PREPARATION, MAP_PREPARATION, MAP_PREPARATION_CANCEL, MAP_PREPARATION_HARD
from module.map.assets import FLEET_1_ADVICE, FLEET_1_CLEAR, FLEET_2_ADVICE, FLEET_2_CLEAR
from module.ocr.ocr import Ocr
from module.ui_white.assets import POPUP_CANCEL_WHITE, POPUP_CONFIRM_WHITE
from module.war_archives.catalog import get_archive
from module.war_archives.execution_assets import (
    ARCHIVE_DISABLED_SUB, ARCHIVE_FLEET_TITLE, ARCHIVE_HARD_GO, ARCHIVE_HARD_SURFACE,
    ARCHIVE_EMPTY_HARD_ROWS, ARCHIVE_EMPTY_HARD_SUB,
    ARCHIVE_KEY_HEADER, ARCHIVE_KEY_MESSAGE, ARCHIVE_KEY_FIVE,
    ARCHIVE_KEY_CANCEL, ARCHIVE_KEY_CONFIRM, ARCHIVE_KEY_LAYOUTS)


_REQUIREMENT = re.compile(r'(AverageLevel|Total(?:HP|FP|TRP|AVI|AA|RLD|EVA|HIT|ASW))>(0|[1-9][0-9]{0,5})\((0|[1-9][0-9]{0,5})\)')


def parse_hard_requirements(lines):
    """Accept only fully read, satisfied known comparisons, never OCR defaults."""
    result = []
    for line in lines:
        text = re.sub(r'\s+', '', str(line)).replace('（', '(').replace('）', ')')
        cursor = 0
        for match in _REQUIREMENT.finditer(text):
            if match.start() != cursor:
                return None
            name, threshold, current = match.groups()
            if int(current) <= int(threshold):
                return None
            result.append((name, int(threshold), int(current)))
            cursor = match.end()
        if cursor != len(text):
            return None
    if not 1 <= len(result) <= 6 or len({row[0] for row in result}) != len(result):
        return None
    return tuple(result)


class ArchiveBlocked(ScriptEnd):
    pass


class ArchiveInspected(ScriptEnd):
    pass


class _ArchiveEntryDevice:
    """Observe the actual Go outside legacy fleet_preparation(), before input."""

    def __init__(self, device, before_go):
        self._device, self._before_go = device, before_go

    def __getattr__(self, name):
        return getattr(self._device, name)

    def click(self, button, *args, **kwargs):
        if button is FLEET_PREPARATION:
            self._before_go()
        elif button is POPUP_CONFIRM or button is POPUP_CONFIRM_WHITE:
            raise ArchiveBlocked('unrecognized_entry_confirm')
        return self._device.click(button, *args, **kwargs)


class _FleetPreparationDevice:
    """Bound legacy FleetOperator loops only while this archive prepares fleets."""

    def __init__(self, device, guard, *, deadline, max_frames=90, max_clicks=12):
        self._device, self._guard, self._deadline = device, guard, deadline
        self._max_frames, self._max_clicks = max_frames, max_clicks
        self._frames = self._clicks = 0
        self._checking = False

    def __getattr__(self, name):
        return getattr(self._device, name)

    def _check(self):
        if monotonic() >= self._deadline:
            raise ArchiveBlocked('fleet_preparation_timeout')

    def _check_dialog(self):
        # The exact oil handler may itself screenshot/cancel its known modal.
        # Its controls still consume the same budgets, without recursive guards.
        if not self._checking:
            self._checking = True
            try:
                self._guard()
            finally:
                self._checking = False

    def screenshot(self, *args, **kwargs):
        self._check()
        if self._frames >= self._max_frames:
            raise ArchiveBlocked('fleet_preparation_frame_limit')
        self._frames += 1
        result = self._device.screenshot(*args, **kwargs)
        self._check()
        self._check_dialog()
        return result

    def click(self, button, *args, **kwargs):
        self._check()
        if self._clicks >= self._max_clicks:
            raise ArchiveBlocked('fleet_preparation_click_limit')
        if any(button is forbidden for forbidden in (
                POPUP_CONFIRM, POPUP_CONFIRM_WHITE, POPUP_CANCEL, POPUP_CANCEL_WHITE,
                FLEET_PREPARATION)):
            raise ArchiveBlocked('unrecognized_fleet_control')
        self._check_dialog()
        self._check()
        self._clicks += 1
        return self._device.click(button, *args, **kwargs)


@contextmanager
def archive_overrides(config, goal, oil_floor):
    values = dict(Campaign_Name=goal.stage, Campaign_Event=goal.event, Campaign_Mode=goal.mode,
                  Campaign_UseClearMode=False, Campaign_UseAutoSearch=False, Campaign_Use2xBook=False,
                  USE_DATA_KEY=False, ShipRotation_Enable=False,
                  StopCondition_MapAchievement='100_percent_clear', StopCondition_RunCount=0,
                  StopCondition_StageIncrease=False, StopCondition_StageRetreat=0,
                  StopCondition_RetreatRemain=0, StopCondition_ThenTask='',
                  StopCondition_GetNewShip=False, StopCondition_ReachLevel=0,
                  StopCondition_OilLimit=oil_floor, EventGeneral_PtLimit=0)
    previous = {key: getattr(config, key) for key in values}
    overridden = dict(config.overridden)
    try:
        config.override(**values)
        yield
    finally:
        config.overridden.clear()
        config.overridden.update(overridden)
        for key, value in previous.items():
            object.__setattr__(config, key, value)


class WarArchivesExecution(CampaignRun):
    """``run_once`` returns a result without advancing or resetting any journal.

    ``prepare(campaign, goal)`` must use bounded known controls, set ENTRANCE,
    and return a *fresh* observation with matching account/event/stage/mode and
    ``verified_for_navigation=True``. Initial observation requires the same proof.
    ``verified_for_execution=True`` additionally authorizes combat, never an
    unknown key dialog. ``inspect_only`` needs no execution/key authorization.
    ``inspect_fleet_only`` instead captures the next Fleet Preparation and
    cancels before any fleet setup or Fleet Go, including already-cleared stages.

    ``reserve(goal, raw_keys=cost, key_mode=mode, attempt_kind=kind)`` must persist
    the caller's shared daily/surplus budget and return literal True. No Start is
    allowed otherwise. A successful reservation is never refunded here, including
    losses, exceptions or interruptions. Raw-key pricing must come from observed
    game evidence. Current EN entry uses five keys per fresh tracking request;
    its optional daily reminder is confirmed only after this run's reserved Go.
    """

    def _proof(self, value, execution=False):
        goal = self.goal
        return (isinstance(value, dict)
                and value.get('account') == self.config.config_name
                and value.get('event') == goal.event
                and str(value.get('stage', '')).upper() == goal.stage.upper()
                and value.get('mode') == goal.mode
                and value.get('verified_for_navigation') is True
                and (not execution or value.get('verified_for_execution') is True))

    def _check_switch(self):
        self.config.check_task_switch('War Archives safe boundary')

    def _check_oil(self):
        oil = self.get_oil(skip_first_screenshot=False)
        if type(oil) is not int or oil < self.oil_floor:
            self.defer_for_oil()
            self.oil_deferred = True
            raise ArchiveBlocked('oil_reserve')

    def _key_proof(self, value):
        return (self.key_mode == 'per_entry' and self.key_cost == 5
                and type(self.key_cost) is int
                and value.get('key_capacity') == 300
                and type(value.get('key_capacity')) is int
                and type(value.get('raw_keys')) is int
                and 5 <= value['raw_keys'] <= 300)

    def _data_key_dialog_candidate(self, image):
        """Exact control shapes permit waiting through a fade, never confirmation."""
        if not isinstance(image, np.ndarray) or image.shape != (720, 1280, 3):
            return False
        luma = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)

        def visible_control(button):
            x1, y1, x2, y2 = button.area
            # Normalized correlation can report a match on constant pixels.
            # Require visible spatial contrast in every control during a fade.
            return (np.ptp(luma[y1:y2, x1:x2]) >= 20
                    and button.match_luma(image, offset=(0, 0), similarity=.9))

        return any(all(visible_control(button)
                       for button in (header, ARCHIVE_KEY_CANCEL, ARCHIVE_KEY_CONFIRM))
                   for header, _, _ in ARCHIVE_KEY_LAYOUTS)

    def _data_key_dialog_witness(self, image):
        """Exact captured Info dialog and complete five-key message, never a price guess."""
        if not isinstance(image, np.ndarray) or image.shape != (720, 1280, 3):
            return False
        if not any(all(button.match_template_color(image, offset=(0, 0), similarity=.9)
                       for button in (*layout, ARCHIVE_KEY_CANCEL, ARCHIVE_KEY_CONFIRM))
                   for layout in ARCHIVE_KEY_LAYOUTS):
            return False
        white = Ocr([(362, 319, 737, 347), (410, 345, 871, 373)], lang='cnocr',
                    letter=(255, 255, 255), threshold=128, name='ARCHIVE_KEY_MESSAGE')
        gold = Ocr([(737, 319, 908, 347), (736, 319, 756, 347)], lang='cnocr',
                   letter=(255, 220, 50), threshold=128, name='ARCHIVE_KEY_COST')
        normalize = lambda text: re.sub(r'\s+', '', text)
        return ([normalize(text) for text in white.ocr(image)] ==
                ['Unlockingthisstagerequires', 'Wouldyouliketounlockthisstage?']
                and [normalize(text) for text in gold.ocr(image)] == ['5DataKey(s)', '5'])

    def _story_unlock_candidate(self, image):
        return (isinstance(image, np.ndarray) and image.shape == (720, 1280, 3)
                and all(button.match_template_color(image, offset=(0, 0), similarity=.9)
                        for button in (ARCHIVE_KEY_HEADER, ARCHIVE_KEY_CANCEL)))

    def _story_unlock_witness(self, image):
        """The current archive's complete cutscene notice, including its Go label.

        The captured Info/Cancel controls share the tracking reminder's layout.
        They never authorize input without reading the entire distinct message.
        """
        if not self._story_unlock_candidate(image):
            return False
        text = image[240:453, 350:930].astype(np.int16)
        white = (text.min(axis=2) > 200) & (text.max(axis=2) - text.min(axis=2) < 35)
        rows = np.flatnonzero(white.sum(axis=1) > 20)
        lines = []
        for row in rows:
            if not lines or row - lines[-1][-1] > 1:
                lines.append([row])
            else:
                lines[-1].append(row)
        if not 2 <= len(lines) <= 8 or any(not 8 <= len(line) <= 30 for line in lines):
            return False
        crops = []
        for line in lines:
            padding = max(4, (29 - len(line)) // 2)
            top, bottom = line[0] - padding, line[-1] + padding + 1 + (len(line) < 20)
            crops.append(cv2.resize(image[240 + top:240 + bottom, 350:930],
                                    None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC))
        crops.append(cv2.resize(image[485:540, 705:876], None, fx=2, fy=2,
                                interpolation=cv2.INTER_CUBIC))
        reader = Ocr([(0, 0, 1, 1)] * len(crops), lang='cnocr', letter=(255, 255, 255),
                     threshold=128, name='ARCHIVE_STORY_UNLOCK')
        words = reader.ocr(crops, direct_ocr=True)
        normalize = lambda value: re.sub(r'[^a-z0-9]', '', value.lower())
        expected = ("You've unlocked the cutscenes of " + get_archive(self.goal.event).name
                    + '. They can be viewed through the Memories menu.')
        return (len(words) == len(crops) and normalize(''.join(words[:-1])) == normalize(expected)
                and normalize(words[-1]) == 'go')

    def _acknowledge_story_unlock(self, campaign, won_before, lost_before=False):
        """Cancel one owned post-boss notice; Go would navigate to Memories."""
        if (self.inspection or self.fleet_inspection or not self.reserved
                or not self.fleet_go_clicked or self.story_unlock_cancelled
                or not self._proof(self.proof, execution=True) or not self._key_proof(self.proof)
                or not (won_before or campaign.combat_won)
                or lost_before or campaign.combat_lost or campaign.run_lost):
            raise ArchiveBlocked('unowned_archive_story_unlock')
        stable = Timer(1, count=2).start()
        frames, returned = 0, 0
        deadline = monotonic() + 12
        for _ in range(20):
            if monotonic() >= deadline:
                break
            self._check_switch()
            campaign.device.screenshot()
            image = getattr(campaign.device, 'image', None)
            if not self.story_unlock_cancelled:
                if not self._story_unlock_witness(image):
                    raise ArchiveBlocked('archive_story_unlock_unverified')
                frames += 1
                if frames >= 2 and stable.reached():
                    self._check_switch()
                    # Consume before forwarding: an uncertain input is never retried.
                    self.story_unlock_cancelled = True
                    logger.info('Archive cutscenes unlocked; dismiss notice and stay on stage list')
                    campaign.device.click(ARCHIVE_KEY_CANCEL)
                continue
            if (self._story_unlock_candidate(image)
                    or any(campaign.appear(button, offset=(20, 30)) for button in (
                        GET_ITEMS_1, GET_ITEMS_2, GET_ITEMS_3, GET_SHIP, USE_DATA_KEY,
                        POPUP_CONFIRM, POPUP_CANCEL, POPUP_CONFIRM_WHITE, POPUP_CANCEL_WHITE))):
                returned = 0
                continue
            if campaign.is_in_stage() and not campaign.is_in_map():
                returned += 1
                if returned >= 2:
                    return
            else:
                returned = 0
        raise ArchiveBlocked('archive_story_unlock_return_unverified')

    def _confirm_data_key(self, campaign, check_budget, deadline):
        """Own fresh frames through one confirmation and positively recognized entry.

        enter_map ignores the oil-handler return value, so this must not return
        while a pending modal could fall through into legacy controls.
        """
        if (self.inspection or self.fleet_inspection or not self.reserved
                or not self.fleet_go_clicked or self.key_confirm_clicked
                or not self._proof(self.proof, execution=True) or not self._key_proof(self.proof)):
            raise ArchiveBlocked('unverified_data_key_dialog')
        stable = Timer(1, count=2).start()
        frames, entered_frames = 0, 0
        end = min(deadline, monotonic() + 15)
        for _ in range(30):
            if monotonic() >= end:
                break
            campaign.device.screenshot()
            check_budget()
            if any(campaign.appear(button, offset=(20, 30)) for button in
                   (GET_ITEMS_1, GET_ITEMS_2, GET_ITEMS_3, GET_SHIP)):
                raise ArchiveBlocked('unowned_receipt')
            image = getattr(campaign.device, 'image', None)
            witness = self._data_key_dialog_witness(image)
            if witness:
                entered_frames = 0
                if not self.key_confirm_clicked:
                    if frames == 0:
                        stable.reset()
                    frames += 1
                    if frames >= 2 and stable.reached():
                        self._check_switch()
                        # The durable shared reservation already exists. Consume
                        # this invocation's sole confirmation before forwarding.
                        self.key_confirm_clicked = True
                        campaign.device.click(ARCHIVE_KEY_CONFIRM)
                continue
            if not self.key_confirm_clicked:
                # A fade or game toast can hide the price/message just after
                # Go. The exact control shapes authorize only bounded waiting.
                # Once complete message proof starts, a changed message fails.
                if frames == 0 and self._data_key_dialog_candidate(image):
                    continue
                raise ArchiveBlocked('unverified_data_key_dialog')
            if any(campaign.appear(button, offset=(20, 20)) for button in (
                    USE_DATA_KEY, POPUP_CONFIRM, POPUP_CANCEL, POPUP_CONFIRM_WHITE, POPUP_CANCEL_WHITE)):
                raise ArchiveBlocked('unrecognized_entry_dialog')
            loading = getattr(campaign, 'is_combat_loading', lambda: False)
            if campaign.is_in_map() or loading():
                entered_frames += 1
                if entered_frames >= 2:
                    return
            else:
                entered_frames = 0
        raise ArchiveBlocked('data_key_entry_not_verified')

    def _hard_fleet_layout(self, campaign):
        if not all(campaign.match_template_color(button, offset=(3, 3), similarity=.85)
                   for button in (ARCHIVE_FLEET_TITLE, ARCHIVE_HARD_SURFACE,
                                  ARCHIVE_HARD_GO)):
            return False
        if not (campaign.match_template_color(ARCHIVE_DISABLED_SUB, offset=(3, 3), similarity=.85)
                or campaign.match_template_color(ARCHIVE_EMPTY_HARD_SUB, offset=(0, 0), similarity=.98)):
            return False
        return all(campaign.match_template_color(button, offset=(-20, -80, 20, 5), similarity=.85)
                   for button in (FLEET_1_ADVICE, FLEET_1_CLEAR, FLEET_2_ADVICE, FLEET_2_CLEAR))

    def _empty_hard_fleet_rows(self, campaign):
        if not self._hard_fleet_layout(campaign):
            return None
        return tuple(campaign.match_template_color(button, offset=(0, 0), similarity=.98)
                     for button in ARCHIVE_EMPTY_HARD_ROWS)

    def _hard_fleet_witness(self, campaign):
        """Only the captured two-surface-row layout, with every stat satisfied.

        Hard fleets belong to the stage. Common fleet numbers must never be
        applied to their rows. Unknown layouts/requirements need another capture.
        """
        if not self._hard_fleet_layout(campaign):
            return None
        image = campaign.device.image
        # Lower portrait strips avoid the empty slot's central plus sign. Each
        # surface row needs a backline and a frontline ship, independently of
        # the global stat comparisons or the enabled Go button.
        gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
        for y in (242, 356):
            occupied = [np.std(gray[y:y + 18, x:x + 72], ddof=1) > 27
                        for x in (377, 482, 587, 696, 800, 905)]
            if not occupied[0] or not any(occupied[3:]):
                return None
        area = image[559:607, 106:976]
        red, green, blue = [area[:, :, index].astype(float) for index in range(3)]
        bright = np.max(area, axis=2) > 180
        gold = ((red > 150) & (green > 130) & (blue < 180)
                & (red >= green * .85) & (green >= red * .7))
        # The OCR is gold-filtered. Reject other bright text first so an unmet
        # red/white comparison cannot disappear from OCR and falsely pass.
        if np.count_nonzero(bright & ~gold) or np.count_nonzero(bright & gold) < 100:
            return None
        crops = [cv2.resize(image[y1:y2, 106:976], None, fx=2, fy=2,
                            interpolation=cv2.INTER_CUBIC) for y1, y2 in ((559, 582), (582, 607))]
        reader = Ocr([(0, 0, 1, 1)] * 2, lang='cnocr', letter=(250, 240, 126),
                     threshold=128, name='ARCHIVE_HARD_REQUIREMENTS')
        return parse_hard_requirements(reader.ocr(crops, direct_ocr=True))

    def _prepare_hard_fleet(self, campaign):
        previous, frames = None, 0
        stable = Timer(1, count=2).start()
        empty_previous, empty_frames, advised = None, 0, set()
        empty_stable = Timer(1, count=2).start()
        for _ in campaign.loop(skip_first=False, timeout=12):
            self._check_switch()
            witness = self._hard_fleet_witness(campaign)
            if witness is None or witness != previous:
                previous, frames = witness, 0
                stable.reset()
            if witness is not None:
                frames += 1
                if frames >= 3 and stable.reached():
                    campaign.map_is_hard_mode = True
                    campaign.config.SUBMARINE = 0  # Exact disabled or wholly empty row above.
                    from module.mood.lineup import record_lineup
                    record_lineup(campaign)
                    campaign.device.screenshot()
                    if self._hard_fleet_witness(campaign) == witness:
                        logger.info('Archive Hard stage-owned fleets and every stat requirement verified')
                        return False
                    break
            # Recommend only a positively empty stage-owned row, once per row.
            # Populated fleets and unreadable requirements are never replaced.
            empty = self._empty_hard_fleet_rows(campaign) if witness is None else None
            if empty is None or empty != empty_previous:
                empty_previous, empty_frames = empty, 0
                empty_stable.reset()
            if empty is not None and any(empty):
                empty_frames += 1
                row = next((i for i, value in enumerate(empty) if value and i not in advised), None)
                if row is not None and empty_frames >= 3 and empty_stable.reached():
                    if (self.inspection or self.fleet_inspection or not self.reserved
                            or self.fleet_go_clicked or not self._proof(self.proof, execution=True)
                            or not self._key_proof(self.proof)):
                        raise ArchiveBlocked('unowned_hard_recommendation')
                    campaign.device.screenshot()
                    if self._empty_hard_fleet_rows(campaign) != empty:
                        break
                    self._check_switch()
                    advised.add(row)  # Consume before forwarding an uncertain input.
                    logger.info(f'Archive Hard fleet {row + 1} is empty; asking the game for advice once')
                    campaign.device.click((FLEET_1_ADVICE, FLEET_2_ADVICE)[row])
                    empty_previous, empty_frames = None, 0
                    empty_stable.reset()
        self.diagnostic = str(save_event_diagnostic(
            campaign.device, f'{self.config.config_name}_{self.goal.event}_{self.goal.stage}_archive_hard_unverified') or '')
        raise ArchiveBlocked('hard_lineup_or_requirements_unverified')

    def _prepare(self):
        self._check_switch()
        self.campaign.device.screenshot()
        if self.campaign.is_in_map():
            raise ArchiveBlocked('unowned_map_in_progress')
        proof = self.prepare(self.campaign, self.goal)
        if not self._proof(proof) or getattr(self.campaign, 'ENTRANCE', None) is None:
            raise ArchiveBlocked('stage_navigation_unverified')
        self.proof = proof

    def _cancel_preparation(self, campaign):
        clicks = 0
        settle = Timer(2, count=2).start()
        returned = Timer(1, count=2).start()
        for _ in campaign.loop(skip_first=False, timeout=12):
            # Background stage markers may still match beneath preparation or
            # a receipt. Never report a return, or tap through those overlays.
            if any(campaign.appear(button, offset=(20, 30)) for button in (
                    GET_ITEMS_1, GET_ITEMS_2, GET_ITEMS_3, GET_SHIP, USE_DATA_KEY,
                    POPUP_CONFIRM, POPUP_CANCEL, POPUP_CONFIRM_WHITE, POPUP_CANCEL_WHITE)):
                break
            preparation = any(campaign.appear(button, offset=(20, 50)) for button in
                              (MAP_PREPARATION, MAP_PREPARATION_HARD, FLEET_PREPARATION))
            if not preparation and campaign.is_in_stage():
                if returned.reached():
                    return True
                continue
            returned.reset()
            if preparation and clicks < 2 and settle.reached():
                campaign.device.click(MAP_PREPARATION_CANCEL)
                clicks += 1
                settle.reset()
        raise ArchiveBlocked('preparation_return_unrecognized')

    def _install_guards(self):
        campaign = self.campaign
        original_enter = campaign.enter_map
        original_stop = campaign.triggered_map_stop
        original_status = campaign.combat_status
        original_in_stage = getattr(campaign, 'handle_in_stage', None)
        original_oil_popup = campaign.handle_oil_purchase
        original_battle = campaign.execute_a_battle
        original_morale = campaign.emotion.check_reduce
        original_fleet = campaign.fleet_preparation
        entry_deadline, entry_frames = None, 0

        def check_entry_budget():
            nonlocal entry_frames
            entry_frames += 1
            if monotonic() >= entry_deadline or entry_frames > 120:
                raise ArchiveBlocked('map_entry_timeout')

        def entry_guard():
            if entry_deadline is not None:
                check_entry_budget()
                if any(campaign.appear(button, offset=(20, 30)) for button in
                       (GET_ITEMS_1, GET_ITEMS_2, GET_ITEMS_3, GET_SHIP)):
                    raise ArchiveBlocked('unowned_receipt')
                if (campaign.appear(USE_DATA_KEY, offset=(20, 20))
                        or self._data_key_dialog_candidate(getattr(campaign.device, 'image', None))):
                    try:
                        self._confirm_data_key(campaign, check_entry_budget, entry_deadline)
                        return False
                    except ArchiveBlocked:
                        self.diagnostic = str(save_event_diagnostic(
                            campaign.device,
                            f'{self.config.config_name}_{self.goal.event}_{self.goal.stage}_archive_data_key_dialog') or '')
                        # Do not hand a pending spending modal to the next job's
                        # generic UI confirmer. Preserve the reservation and
                        # recover the game before any ordinary job resumes.
                        self.config.task_call('Restart')
                        raise
                # A generic confirm may buy keys, oil, or an unknown entitlement.
                # Let the exact oil guard cancel its known purchase dialog first.
                original_oil_popup()
                if any(campaign.appear(button, offset=(20, 20)) for button in (
                        POPUP_CONFIRM, POPUP_CANCEL, POPUP_CONFIRM_WHITE, POPUP_CANCEL_WHITE)):
                    self.diagnostic = str(save_event_diagnostic(
                        campaign.device,
                        f'{self.config.config_name}_{self.goal.event}_{self.goal.stage}_archive_entry_dialog') or '')
                    self.config.task_call('Restart')
                    raise ArchiveBlocked('unrecognized_entry_dialog')
                return False
            return original_oil_popup()

        def enter(*args, **kwargs):
            nonlocal entry_deadline, entry_frames
            if self.entry_calls >= 2 or (self.entry_calls and not self.inspection):
                raise ArchiveBlocked('second_sortie_forbidden')
            self.entry_calls += 1
            entry_deadline, entry_frames = monotonic() + 60, 0
            original_device = campaign.device
            campaign.device = _ArchiveEntryDevice(original_device, before_go)
            try:
                return original_enter(*args, **kwargs)
            finally:
                campaign.device = original_device
                entry_deadline = None

        def before_go():
            check_entry_budget()
            if (self.inspection or self.fleet_inspection or not self.reserved
                    or not self.fleet_ready or not self._proof(self.proof, execution=True)
                    or not self._key_proof(self.proof)):
                raise ArchiveBlocked('unreserved_fleet_go')
            if self.fleet_go_clicked:
                raise ArchiveBlocked('second_fleet_go_forbidden')
            self._check_switch()
            self.fleet_go_clicked = True

        def stop():
            self.preparation_seen = True
            self.stage_complete = (self.proof.get('stage_complete') is True if self.one_clear
                                   else bool(original_stop()))
            if self.fleet_inspection:
                # Permit exactly one known Map Preparation -> Fleet Preparation
                # transition, even for a completed stage. Never authorize Go.
                if self.fleet_probe_advances:
                    self._cancel_preparation(campaign)
                    raise ArchiveBlocked('fleet_preparation_not_reached')
                self.fleet_probe_advances += 1
                return False
            if (self.stage_complete and self.goal.goal == 'archive_clear') or self.inspection:
                self._cancel_preparation(campaign)
                raise ArchiveInspected('complete' if self.stage_complete else 'incomplete')
            try:
                self._check_switch()
                self._check_oil()
                if not self._proof(self.proof, execution=True) or not self._key_proof(self.proof):
                    raise ArchiveBlocked('execution_unverified')
                if not self.reserved:
                    original_morale(campaign._map_battle)
                    # This callback is the last authorization before map Start.
                    if self.reserve(self.goal, raw_keys=self.key_cost, key_mode=self.key_mode,
                                    attempt_kind=self.attempt_kind) is not True:
                        raise ArchiveBlocked('reservation_rejected')
                    self.reserved = True
                    self._check_switch()
            except (ScriptEnd, TaskEnd, OilReadError) as error:
                try:
                    self._cancel_preparation(campaign)
                except ArchiveBlocked as cleanup_error:
                    # Failed cleanup must not replace scheduler preemption or
                    # the original morale/oil deferral with a different result.
                    error.archive_cleanup_error = str(cleanup_error)
                    logger.warning(f'Archive preparation cleanup failed: {cleanup_error}')
                raise
            return False

        def fleet_preparation(*args, **kwargs):
            original_device = campaign.device
            if self.fleet_inspection:
                campaign.device.screenshot()
                entry_guard()
                if (self.fleet_probe_advances != 1 or campaign.is_in_map()
                        or not campaign.appear(FLEET_PREPARATION, offset=(20, 50))):
                    raise ArchiveBlocked('fleet_preparation_unrecognized')
                self.diagnostic = str(save_event_diagnostic(
                    original_device,
                    f'{self.config.config_name}_{self.goal.event}_{self.goal.stage}_archive_fleet_probe') or '')
                self._cancel_preparation(campaign)
                if not self.diagnostic:
                    raise ArchiveBlocked('fleet_preparation_capture_failed')
                raise ArchiveInspected('fleet_preparation')
            deadline = min(entry_deadline, monotonic() + 45) if entry_deadline is not None else monotonic() + 45
            campaign.device = _FleetPreparationDevice(original_device, entry_guard, deadline=deadline)
            try:
                entry_guard()
                result = (self._prepare_hard_fleet(campaign) if self.goal.mode == 'hard'
                          else original_fleet(*args, **kwargs))
                campaign.device._check()
                entry_guard()
                self.fleet_ready = True
                return result
            finally:
                campaign.device = original_device

        def status(drop=None, expected_end=None):
            won_before, lost_before = campaign.combat_won, campaign.combat_lost
            # Combat cleanup asks handle_in_stage before its broad popup handlers.
            # The expected end is only a prediction: a multiple-spawn potential
            # boss search can predict no_searching even when the map really ends.
            # The notice handler still requires our owned victory and exact text.
            if callable(original_in_stage):
                def in_stage(*args, **kwargs):
                    if self._story_unlock_candidate(getattr(campaign.device, 'image', None)):
                        self._acknowledge_story_unlock(campaign, won_before, lost_before)
                    return original_in_stage(*args, **kwargs)
                campaign.handle_in_stage = in_stage
            try:
                return original_status(drop=drop, expected_end=expected_end)
            finally:
                if callable(original_in_stage):
                    campaign.handle_in_stage = original_in_stage
                if (self.reserved and not (self.inspection or self.fleet_inspection)
                        and self._proof(self.proof, execution=True) and self._key_proof(self.proof)
                        and (won_before or campaign.combat_won)
                        and not (lost_before or campaign.combat_lost or campaign.run_lost)
                        and campaign.is_in_stage() and not campaign.is_in_map()):
                    self.boss_won = True
                    logger.info(f'Archive victory and return to stage list verified (expected_end={expected_end})')

        def battle():
            if self.config.task_switched():
                self._withdraw_between_battles()
                raise TaskEnd('War Archives preempted between battles')
            if campaign.is_in_map():
                try:
                    self._check_oil()
                except ArchiveBlocked:
                    self._withdraw_between_battles()
                    raise
            return original_battle()

        def bounded_campaign_ui(name=None, mode=None, **kwargs):
            if ((name is not None and str(name).upper() != self.goal.stage.upper())
                    or (mode is not None and mode != self.goal.mode)):
                raise ArchiveBlocked('navigation_goal_changed')
            self._prepare()
            return True

        campaign.first_run = False
        campaign.ensure_campaign_ui = bounded_campaign_ui
        campaign.ui_goto_archives_campaign = lambda *args, **kwargs: bounded_campaign_ui()
        campaign.ui_goto_event = campaign.ui_goto_archives_campaign
        campaign.ui_goto_sp = campaign.ui_goto_archives_campaign
        campaign.enter_map = enter
        campaign.fleet_preparation = fleet_preparation
        campaign.triggered_map_stop = stop
        campaign.combat_status = status
        campaign.handle_oil_purchase = entry_guard
        campaign.handle_use_data_key = lambda: False
        campaign.handle_map_stop = lambda: None
        campaign.handle_auto_search_continue = lambda: False
        campaign.handle_retirement = lambda: False
        campaign.execute_a_battle = battle
        campaign.enter_map_cancel = lambda *args, **kwargs: self._cancel_preparation(campaign)
        # Inspect completion before asking fleets to recover morale. The original
        # check still gates every first Start above, including DormRest deferral.
        campaign.emotion.check_reduce = lambda battle: None

    def _withdraw_between_battles(self):
        if self.campaign.is_in_map():
            try:
                self.campaign.withdraw(timeout=20)
            except CampaignEnd:
                pass

    def _result(self, status, reason=''):
        result = dict(status=status, reason=reason, reserved=self.reserved,
                      boss_won=self.boss_won, stage_complete=self.stage_complete)
        if self.diagnostic:
            result['diagnostic'] = self.diagnostic
        return result

    def run_once(self, goal, observation, reserve, prepare, *, key_mode=None, key_cost=None,
                 attempt_kind='daily', extra_oil_reserve=5000, inspect_only=False,
                 inspect_fleet_only=False):
        self.goal, self.reserve, self.prepare = goal, reserve, prepare
        self.reserved = self.boss_won = self.stage_complete = self.preparation_seen = False
        self.fleet_ready = self.fleet_go_clicked = self.key_confirm_clicked = False
        self.story_unlock_cancelled = False
        self.oil_deferred = False
        self.diagnostic = ''
        self.fleet_inspection, self.fleet_probe_advances = inspect_fleet_only, 0
        self.entry_calls, self.inspection = 0, inspect_only
        if inspect_only and inspect_fleet_only:
            return self._result('blocked', 'conflicting_inspection_modes')
        try:
            archive = get_archive(goal.event)
            valid_stage = (archive.supported and to_map_file_name(goal.stage) in archive.stages(goal.mode)
                           and to_map_file_name(goal.stage) in stage_names(goal.event))
        except ValueError:
            valid_stage = False
        if (self.config.config_name not in ('alas', 'f2p')
                or not goal.event.startswith('war_archives_')
                or goal.goal not in ('archive_clear', 'archive_prize')
                or goal.mode not in ('normal', 'hard')
                or not valid_stage
                or not self._proof(observation)):
            return self._result('blocked', 'navigation_unverified')
        if (attempt_kind not in ('daily', 'extra') or type(extra_oil_reserve) is not int
                or extra_oil_reserve < 0):
            return self._result('blocked', 'invalid_attempt_policy')
        if not (inspect_only or inspect_fleet_only):
            self.key_mode, self.key_cost = key_mode, key_cost
            if not self._proof(observation, execution=True) or not self._key_proof(observation):
                return self._result('blocked', 'key_entitlement_unverified')
        self.key_mode, self.key_cost, self.attempt_kind = key_mode, key_cost, attempt_kind
        self.oil_floor = max(500, extra_oil_reserve if attempt_kind == 'extra' else 0)
        # CampaignRun's name-only cache must never reuse a stage from another archive.
        self.__dict__.pop('name', None)
        try:
            with archive_overrides(self.config, goal, self.oil_floor):
                self._check_switch()
                self.load_campaign(to_map_file_name(goal.stage), goal.event)
                self.one_clear = (not self.campaign.config.MAP_HAS_CLEAR_PERCENTAGE
                                  or self.campaign.config.MAP_IS_ONE_TIME_STAGE)
                self._install_guards()
                self._prepare()
                if self.campaign.is_in_map():
                    raise ArchiveBlocked('unowned_map_in_progress')
                # The guarded preparation boundary performs the established fleet
                # morale and oil preflight before Start. Loss still leaves the map.
                self.campaign.run()
                if self.campaign.run_lost:
                    return self._result('lost', 'battle_lost')
                if not self.reserved or not self.boss_won:
                    return self._result('blocked', 'boss_victory_unverified')
                if goal.goal == 'archive_prize':
                    return self._result('won', 'prize_counter_requires_observation')
                if self.one_clear:
                    self.stage_complete = True
                    return self._result('complete', 'one_clear_boss_victory')
                self.inspection = True
                self._prepare()
                self.campaign.enter_map(self.campaign.ENTRANCE, mode=goal.mode)
                return self._result('won', 'post_win_inspection_unverified')
        except ArchiveInspected as error:
            if self.fleet_inspection:
                return self._result('observed', str(error))
            return self._result('complete' if self.stage_complete else ('won' if self.boss_won else 'incomplete'))
        except OilReadError as error:
            # A hidden/unreadable counter cannot authorize entry or establish a
            # shortage. Before Start there is no reservation; after entry the
            # coordinator retains the spent reservation as an uncertain result.
            logger.warning(error)
            self.config.task_delay(minute=1)
            if self.reserved:
                self._withdraw_between_battles()
            return self._result('deferred', 'oil_unverified')
        except ArchiveBlocked as error:
            # defer_for_oil owns NextRun; the coordinator must not replace that
            # recovery time with its generic blocked/interrupted delay.
            return self._result('deferred' if self.oil_deferred else 'blocked', str(error))
        except OilExhausted:
            self.defer_for_oil()
            self.oil_deferred = True
            self._withdraw_between_battles()
            return self._result('deferred', 'oil_exhausted')
        except ScriptEnd as error:
            # Morale deferral preserves the existing scheduled recovery and no
            # reservation when it happens before entry. Never mark it a win.
            return self._result('deferred' if not self.reserved else 'interrupted', str(error))
        except Exception as error:
            # Preserve scheduler/recovery semantics. The caller can audit whether
            # its durable reservation was already consumed; never refund by guess.
            error.archive_reserved = self.reserved
            raise
