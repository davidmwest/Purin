"""One owned snack and its exact Daily mission reward; never buys food."""
import json
import os
import re
import tempfile

import cv2

from module.base.button import Button
from module.base.timer import Timer
from module.combat.assets import GET_ITEMS_1, GET_ITEMS_2, GET_ITEMS_3, GET_SHIP
from module.config.utils import get_server_last_update
from module.dorm.assets import DORM_BUY_FOOD_CHECK, DORM_FEED_CHECK, DORM_FEED_ENTER
from module.dorm.daily_assets import RESTOCK_DAILY, RESTOCK_TEXT, RESTOCK_ZERO
from module.dorm.dorm import FOOD_FEED_AMOUNT, OCR_FILL, RewardDorm
from module.eventstory.eventstory import ensure_event_page, ensure_event_running, save_event_diagnostic
from module.exception import ScriptError
from module.gacha.mission_claim import _match_control
from module.gacha.mission_claim_assets import BUILD_MISSION_CLAIM, BUILD_MISSION_COMPLETE
from module.logger import logger
from module.ocr.ocr import Ocr
from module.reward.reward import Reward
from module.ui.assets import DORM_CHECK, DORM_INFO
from module.ui.page import page_dorm, page_main, page_mission

VERSION = 'restock-one-v1'
DIALOGS_MAX = 8


def state_path(account, folder):
    if not re.fullmatch(r'[A-Za-z0-9_-]+', account):
        raise ScriptError('Invalid DormDaily account')
    return os.path.join(folder, f'{account}-dorm-daily.json')


def load_state(account, folder):
    try:
        with open(state_path(account, folder), encoding='utf-8') as stream:
            state = json.load(stream)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise ScriptError(f'Cannot safely read DormDaily journal: {exc}') from exc
    if not isinstance(state, dict) or any(not isinstance(value, dict) for value in state.values()):
        raise ScriptError('Invalid DormDaily journal')
    return state


def save_state(account, state, folder):
    os.makedirs(folder, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.dorm-daily-', suffix='.json', dir=folder)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(state, stream, indent=2, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, state_path(account, folder))
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def choose_food(amounts, capacity):
    """Prefer one Secret Coolant (2000), then the smallest fitting owned food."""
    if (not isinstance(amounts, (tuple, list)) or len(amounts) != 6
            or type(capacity) is not int or not 0 <= capacity <= 90000
            or any(type(amount) is not int or not 0 <= amount <= 999999 for amount in amounts)):
        return None
    # The local tutorial's second Snacks2000 canister matches the mission's
    # Secret Coolant reward icon. Snacks1000 is the tutorial's Oxy-cola.
    return next((index for index in (1, 0, 2, 3, 4, 5)
                 if amounts[index] > 0 and FOOD_FEED_AMOUNT[index] <= capacity), None)


def locate_restock(image):
    """One fully visible exact title plus its own DAILY marker."""
    if image.shape[:2] != (720, 1280):
        return None
    RESTOCK_TEXT.ensure_template()
    region = image[87:692, 253:610]
    scores = cv2.matchTemplate(cv2.cvtColor(region, cv2.COLOR_RGB2GRAY),
                               cv2.cvtColor(RESTOCK_TEXT.image, cv2.COLOR_RGB2GRAY), cv2.TM_CCOEFF_NORMED)
    matches = []
    while scores.size:
        _, score, _, (x, y) = cv2.minMaxLoc(scores)
        if score < .90:
            break
        matches.append((x + 253, y + 87))
        scores[max(0, y - 35):y + 36, :] = -1
    if len(matches) != 1:
        return None
    x, y = matches[0]
    if y - 42 < 87 or y + 98 > 692:
        return None
    if _match_control(image, RESTOCK_DAILY, (173, y - 43, 198, y + 37)) is None:
        return None
    return dict(title_y=y, counter=(616, y + 31, 660, y + 49))


def read_restock(image):
    row = locate_restock(image)
    if row is None:
        return None
    y = row['title_y']
    complete = _match_control(image, BUILD_MISSION_COMPLETE, (519, y + 24, 665, y + 62))
    claim = _match_control(image, BUILD_MISSION_CLAIM, (1111, y, 1230, y + 48))
    if complete is not None and claim is not None:
        return dict(kind='earned', button=Button(claim, (0, 0, 0), claim, name='RESTOCK_CLAIM'))
    # The observed square zero is read as a slash/one by the OCR model. Only
    # accept its exact captured 0/1 appearance, bound to this title's row.
    zero = _match_control(image, RESTOCK_ZERO, (613, y + 28, 663, y + 56))
    if zero is not None:
        sample = image[zero[1]:zero[3], zero[0]:zero[2]].astype('float32')
        if abs(sample - RESTOCK_ZERO.image.astype('float32')).mean() < 3:
            return dict(kind='unfinished', current=0)
    raw = Ocr(row['counter'], alphabet='01/', letter=(255, 255, 255), threshold=128,
              name='RESTOCK_DAILY_RAW').ocr(image)
    counter = re.fullmatch(r'\s*([01])\s*/\s*1\s*', raw)
    return dict(kind='unfinished', current=int(counter[1])) if counter else dict(kind='unknown')


class DormDaily(RewardDorm, Reward):
    def _period(self):
        return get_server_last_update(self.config.Scheduler_ServerUpdate).isoformat()

    def _capture(self, record, tag):
        path = save_event_diagnostic(self.device, f'{self.config.config_name}_dorm_daily_{tag}')
        if path:
            record['diagnostics'].append(path)

    def _daily_list(self):
        return self.ui_page_appear(page_mission) and self._reward_side_navbar.get_info(self) == (3, 0, 5)

    def _receipt(self):
        for button in (GET_ITEMS_1, GET_ITEMS_2, GET_ITEMS_3, GET_SHIP):
            if self.appear(button, offset=(20, 20)):
                return GET_ITEMS_1 if button in (GET_ITEMS_2, GET_ITEMS_3) else button
        return None

    def _claim(self, record, persist):
        # Receipt handling precedes background page recognition on every frame.
        settled, click_wait = Timer(1, count=2).start(), Timer(1, count=2)
        previous, frames = None, 0
        for _ in self.loop(skip_first=False, timeout=25):
            dialog = self._receipt()
            if dialog is not None:
                previous, frames = None, 0
                settled.reset()
                if record['claim_clicks'] and record['dialogs'] < DIALOGS_MAX and click_wait.reached():
                    record.update(receipt_seen=True, dialogs=record['dialogs'] + 1)
                    persist()
                    self.device.click(dialog)
                    click_wait.reset()
                continue
            if not self._daily_list():
                previous, frames = None, 0
                settled.reset()
                continue
            row = read_restock(self.device.image)
            kind = row['kind'] if row else 'absent'
            if kind != previous:
                previous, frames = kind, 0
                settled.reset()
            frames += 1
            if frames < 3 or not settled.reached():
                continue
            if (kind == 'absent' and record['claim_clicks'] and record['receipt_seen']
                    and self._reward_get_state() is not None):
                record.update(status='complete', mission_reward_claimed=True, reason='restock_reward_verified')
                return
            if kind == 'earned' and not record['claim_clicks']:
                if self._period() != record['period']:
                    return
                record['claim_clicks'] = 1
                persist()
                if self._period() != record['period']:
                    return
                self.device.click(row['button'])
                click_wait.reset()
                previous, frames = None, 0
                settled.reset()
        record.update(reason='restock_claim_unverified')

    def _read_mission_view(self):
        timer = Timer(1, count=2).start()
        previous, frames = None, 0
        for _ in self.loop(skip_first=False, timeout=4):
            if self._receipt() is not None or not self._daily_list():
                previous, frames = None, 0
                timer.reset()
                continue
            row = read_restock(self.device.image)
            value = None if row is None else (row['kind'], row.get('current'))
            if value != previous:
                previous, frames = value, 0
                timer.reset()
            frames += 1
            if frames >= 3 and timer.reached():
                return row
        return None

    def _mission(self):
        self.device.screenshot()
        # An unrelated task may own a reward overlay. Do not let generic page
        # or sidebar navigation dismiss it before this job owns a claim.
        if self._receipt() is not None:
            return None
        if not ensure_event_page(self, page_mission) or not self.reward_side_navbar_ensure(upper=4):
            return None
        row = self._read_mission_view()
        if row is not None:
            return row
        # The Daily tab can retain an earlier scroll position. Search earlier
        # rows, then later rows with overlap. Never infer absence/completion
        # from hitting this fixed observation budget.
        for earlier in (True, True, True, False, False, False, False, False, False):
            if self._receipt() is not None or not self._daily_list():
                return None
            p1, p2 = ((910, 235), (910, 565)) if earlier else ((910, 565), (910, 235))
            self.device.swipe(p1, p2, duration=(.35, .45), name='DORM_DAILY_MISSION_SCROLL')
            row = self._read_mission_view()
            if row is not None:
                return row
        return None

    def _feed_panel(self, opened):
        clicks, info_clicks, settle = 0, 0, Timer(2, count=2)
        for _ in self.loop(skip_first=False, timeout=12):
            if self._receipt() is not None:
                return False
            if self.appear(DORM_BUY_FOOD_CHECK, offset=(20, 20)):
                return False
            # Entering Dorm can show its XP summary over a recognizable
            # background. Dismiss only this established exact control before
            # deciding whether the food panel is open or clicking its toggle.
            if self.appear(DORM_INFO, offset=(30, 30)):
                if info_clicks < 2 and settle.reached():
                    self.device.click(DORM_INFO)
                    info_clicks += 1
                    settle.reset()
                continue
            panel = self.appear(DORM_FEED_CHECK, offset=(20, 20))
            if (panel if opened else not panel and self.appear(DORM_CHECK, offset=(20, 20))):
                return True
            if clicks < 2 and settle.reached() and (panel or self.appear(DORM_CHECK, offset=(20, 20))):
                self.device.click(DORM_FEED_ENTER)
                clicks += 1
                settle.reset()
        return False

    def _feed_one(self, record, persist):
        if record['food_clicks']:
            return
        if not ensure_event_page(self, page_dorm) or not self._feed_panel(True):
            record['reason'] = 'dorm_feed_page_unrecognized'
            return
        self._capture(record, 'food_before')
        timer = Timer(1, count=2).start()
        previous, frames = None, 0
        for _ in self.loop(skip_first=False, timeout=10):
            if (not self.appear(DORM_FEED_CHECK, offset=(20, 20))
                    or self.appear(DORM_BUY_FOOD_CHECK, offset=(20, 20))):
                return
            snapshot = self._food_values()
            if snapshot is None:
                previous, frames = None, 0
                timer.reset()
                continue
            amounts, capacity = snapshot
            if snapshot != previous:
                previous, frames = snapshot, 0
                timer.reset()
            frames += 1
            if frames < 3 or not timer.reached():
                continue
            index = choose_food(*snapshot)
            if index is None:
                record['reason'] = 'no_owned_food_fits_free_capacity'
                return
            if self._period() != record['period']:
                return
            record.update(food_clicks=1, food_index=index, food_feed=FOOD_FEED_AMOUNT[index],
                          food_identity='Secret Coolant' if index == 1 else f'Food_{FOOD_FEED_AMOUNT[index]}',
                          food_before=list(snapshot[0]), capacity_before=capacity, reason='food_use_reserved')
            persist()
            if self._period() != record['period']:
                return
            # Exactly one raw click. Never call the bulk/long-press dorm feeder.
            self.device.click(self._dorm_food.buttons[index])
            self.device.screenshot()
            self._capture(record, 'food_clicked')
            return
        record['reason'] = 'food_read_unstable'

    def _food_values(self):
        """Use existing food OCR, rejecting unknown/zero capacity or bad counts."""
        amounts = self._dorm_food_ocr.ocr(self.device.image)
        fill = OCR_FILL.ocr(self.device.image)
        if (not isinstance(amounts, list) or len(amounts) != 6
                or any(type(value) is not int or not 0 <= value <= 999999 for value in amounts)
                or not isinstance(fill, (tuple, list)) or len(fill) != 3
                or any(type(value) is not int for value in fill)):
            return None
        current, capacity, total = fill
        if not (0 <= current <= total <= 90000 and total > 0
                and 0 <= capacity <= total and current + capacity == total):
            return None
        amounts = tuple(value if self._dorm_has_food(button) else 0
                        for value, button in zip(amounts, self._dorm_food.buttons))
        return amounts, capacity

    def run(self, folder='./config/dorm'):
        account, period = self.config.config_name, self._period()
        state = load_state(account, folder)
        key = f'{period}:{VERSION}'
        record = state.get(key, dict(period=period, version=VERSION, food_clicks=0, claim_clicks=0,
                                    dialogs=0, receipt_seen=False, mission_reward_claimed=False,
                                    diagnostics=[], status='blocked', reason='mission_not_verified'))
        for name, maximum in (('food_clicks', 1), ('claim_clicks', 1), ('dialogs', DIALOGS_MAX)):
            if type(record.get(name)) is not int or not 0 <= record[name] <= maximum:
                raise ScriptError('Invalid DormDaily action budget')
        if (record.get('period') != period or record.get('version') != VERSION
                or any(type(record.get(name)) is not bool for name in ('receipt_seen', 'mission_reward_claimed'))
                or not isinstance(record.get('diagnostics'), list)
                or (record['receipt_seen'] and not record['claim_clicks'])
                or (record['mission_reward_claimed'] and not record['receipt_seen'])):
            raise ScriptError('Invalid DormDaily proof')
        if record['mission_reward_claimed']:
            self.config.task_delay(server_update=True)
            return record
        ensure_event_running(self)
        state[key] = record
        def persist():
            save_state(account, state, folder)
        persist()
        try:
            if record['claim_clicks']:
                # Preserve an owned receipt before any generic UI discovery.
                self._claim(record, persist)
                if not record['mission_reward_claimed'] and self._receipt() is None:
                    self._mission()
                    self._claim(record, persist)
            else:
                row = self._mission()
                if row and row['kind'] == 'unfinished' and row['current'] == 0 and not record['food_clicks']:
                    self._feed_one(record, persist)
                    if not self._feed_panel(False):
                        record['reason'] = 'dorm_feed_return_unrecognized'
                        self._capture(record, 'return_blocked')
                        return record
                    row = self._mission()
                if row and row['kind'] == 'earned':
                    record['mission_credited'] = True
                    persist()
                    self._claim(record, persist)
            self._capture(record, record['status'])
            if self._receipt() is None:
                record['returned_main'] = ensure_event_page(self, page_main)
            logger.attr('DormDaily', record)
            return record
        finally:
            persist()
            self.config.task_delay(server_update=True) if record['mission_reward_claimed'] else self.config.task_delay(minute=30)
