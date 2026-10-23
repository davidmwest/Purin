"""Claim the exact earned Build 3 mission, without submitting any builds."""
import json
import os
import re
import tempfile

import cv2

from module.base.button import Button
from module.base.timer import Timer
from module.combat.assets import GET_ITEMS_1, GET_ITEMS_2, GET_ITEMS_3, GET_SHIP
from module.config.utils import get_server_last_update
from module.eventstory.eventstory import ensure_event_page, ensure_event_running, save_event_diagnostic
from module.exception import ScriptError
from module.gacha.mission_claim_assets import BUILD_MISSION_CLAIM, BUILD_MISSION_COMPLETE
from module.logger import logger
from module.reward.reward import Reward
from module.ui.page import page_main, page_mission

CLAIM_VERSION = 'build-three-claim-v1'
DIALOGS_MAX = 8


def claim_state_path(account, folder='./config/gacha'):
    if not re.fullmatch(r'[A-Za-z0-9_-]+', account):
        raise ScriptError('Invalid account name for Gacha claim state')
    return os.path.join(folder, f'{account}-mission-claims.json')


def load_claim_state(account, folder='./config/gacha'):
    try:
        with open(claim_state_path(account, folder), encoding='utf-8') as stream:
            state = json.load(stream)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise ScriptError(f'Cannot safely read Gacha claim state: {exc}') from exc
    if not isinstance(state, dict) or any(not isinstance(item, dict) for item in state.values()):
        raise ScriptError('Invalid Gacha claim state')
    return state


def save_claim_state(account, state, folder='./config/gacha'):
    os.makedirs(folder, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.gacha-claim-', suffix='.json', dir=folder)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(state, stream, indent=2, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, claim_state_path(account, folder))
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def _match_control(image, asset, area):
    asset.ensure_template()
    x1, y1, x2, y2 = area
    region = image[y1:y2, x1:x2]
    template = asset.image
    if region.shape[0] < template.shape[0] or region.shape[1] < template.shape[1]:
        return None
    scores = cv2.matchTemplate(cv2.cvtColor(region, cv2.COLOR_RGB2GRAY),
                               cv2.cvtColor(template, cv2.COLOR_RGB2GRAY), cv2.TM_CCOEFF_NORMED)
    _, score, _, (x, y) = cv2.minMaxLoc(scores)
    if score < .90:
        return None
    # The exact blue earned control must not be accepted as the gray GO action.
    sample = region[y:y + template.shape[0], x:x + template.shape[1]]
    if abs(sample.astype('float32').mean(axis=(0, 1))
           - template.astype('float32').mean(axis=(0, 1))).max() > 25:
        return None
    return (x1 + x, y1 + y, x1 + x + template.shape[1], y1 + y + template.shape[0])


def earned_build_mission(image):
    """Recognize COMPLETE and CLAIM in the exact target row, never another row."""
    # Lazy import permits the order coordinator to call this helper without a
    # module import cycle. The locator requires the observed DAILY/EVENT marker.
    from module.gacha.mission import locate_build_mission

    row = locate_build_mission(image)
    if row is None:
        return None
    y = row['title_area'][1]
    complete = _match_control(image, BUILD_MISSION_COMPLETE, (519, y + 29, 665, y + 61))
    claim = _match_control(image, BUILD_MISSION_CLAIM, (1111, y + 5, 1230, y + 47))
    if complete is None or claim is None:
        return None
    return Button(area=claim, color=(0, 0, 0), button=claim, name='BUILD_THREE_CLAIM')


class GachaMissionClaim(Reward):
    def _period(self):
        return get_server_last_update(self.config.Scheduler_ServerUpdate).isoformat()

    def _capture(self, record, tag):
        path = save_event_diagnostic(self.device, f'{self.config.config_name}_gacha_claim_{tag}')
        if path:
            record['diagnostics'].append(path)

    def _daily_list(self):
        return (self.ui_page_appear(page_mission)
                and self._reward_side_navbar.get_info(self) == (3, 0, 5))

    def _reward_dialog(self):
        for button in (GET_ITEMS_1, GET_ITEMS_2, GET_ITEMS_3, GET_SHIP):
            if self.appear(button, offset=(20, 20)):
                return GET_ITEMS_1 if button in (GET_ITEMS_2, GET_ITEMS_3) else button
        return None

    def _dismiss_receipt(self, record, persist):
        dialog = self._reward_dialog() if record['claim_clicks'] else None
        if dialog is None or record['reward_dialogs'] >= DIALOGS_MAX:
            return False
        record.update(receipt_seen=True, reward_dialogs=record['reward_dialogs'] + 1)
        persist()
        self.device.click(dialog)
        return True

    def _resume_receipts(self, record, persist):
        wait = Timer(1, count=2)
        for _ in self.loop(skip_first=False, timeout=10):
            if self.ui_page_appear(page_mission) or self.ui_page_appear(page_main):
                return
            if wait.reached() and self._dismiss_receipt(record, persist):
                wait.reset()

    def _collect(self, record, persist):
        from module.gacha.mission import locate_build_mission

        settled = Timer(1, count=2).start()
        click_wait = Timer(1, count=2)
        prior = None
        frames = 0
        for _ in self.loop(skip_first=False, timeout=40):
            if not self._daily_list():
                prior, frames = None, 0
                settled.reset()
                if click_wait.reached() and self._dismiss_receipt(record, persist):
                    click_wait.reset()
                continue
            self.device.stuck_record_clear()
            button = earned_build_mission(self.device.image)
            row = locate_build_mission(self.device.image)
            state = ('earned', button.button) if button is not None else ('absent' if row is None else 'unknown')
            if state != prior:
                prior, frames = state, 0
                settled.reset()
            frames += 1
            if frames < 3 or not settled.reached():
                continue
            if button is not None and not record['claim_clicks']:
                if self._period() != record['period']:
                    record.update(status='blocked', reason='mission_period_changed')
                    return
                record.update(claim_clicks=1, pending_claim=True, earned_row=list(button.button))
                # A crash after input resumes receipt verification only. It can
                # never submit builds or repeat this claim on the same server day.
                persist()
                # Disk persistence can itself straddle the configured reset.
                # Keep the old reservation, but never click its stale row.
                if self._period() != record['period']:
                    record.update(status='blocked', reason='mission_period_changed', pending_claim=False)
                    return
                self.device.click(button)
                click_wait.reset()
                prior, frames = None, 0
                settled.reset()
                continue
            if (row is None and record['claim_clicks'] and record['receipt_seen']
                    and self._reward_get_state() is not None):
                record.update(status='complete', reason='build_mission_reward_verified',
                              mission_reward_claimed=True, pending_claim=False)
                return
        record.update(status='blocked', reason='build_mission_claim_unverified')

    def run(self, folder='./config/gacha'):
        from module.gacha.mission import CURRENT_EVENT

        ensure_event_running(self)
        account = self.config.config_name
        period = self._period()
        key = f'{period}:{CURRENT_EVENT}:{CLAIM_VERSION}'
        state = load_claim_state(account, folder)
        previous = state.get(key, {})
        for name, maximum in (('claim_clicks', 1), ('reward_dialogs', DIALOGS_MAX)):
            if name in previous and (type(previous[name]) is not int or not 0 <= previous[name] <= maximum):
                raise ScriptError('Invalid durable Gacha claim budget')
        for name in ('receipt_seen', 'pending_claim', 'mission_reward_claimed'):
            if name in previous and type(previous[name]) is not bool:
                raise ScriptError('Invalid durable Gacha claim proof')
        if previous.get('mission_reward_claimed'):
            return dict(previous, reused=True)
        record = dict(status='claiming', reason='earned_build_mission_requires_verification',
                      period=period, event=CURRENT_EVENT,
                      diagnostics=[], claim_clicks=0, reward_dialogs=0, receipt_seen=False,
                      pending_claim=False, mission_reward_claimed=False, returned_main=False)
        record.update({k: previous[k] for k in ('claim_clicks', 'reward_dialogs', 'receipt_seen',
                                               'pending_claim', 'diagnostics') if k in previous})
        state[key] = record

        def persist():
            save_claim_state(account, state, folder)

        persist()
        try:
            if record['claim_clicks']:
                self._resume_receipts(record, persist)
            if not ensure_event_page(self, page_mission) or not self.reward_side_navbar_ensure(upper=4):
                record.update(status='blocked', reason='daily_mission_page_unrecognized')
            else:
                self._collect(record, persist)
            self._capture(record, record['status'])
        except Exception:
            record.update(status='blocked', reason='build_mission_claim_exception')
            self._capture(record, 'exception')
            raise
        finally:
            try:
                record['returned_main'] = ensure_event_page(self, page_main)
                if not record['returned_main']:
                    record.update(status='blocked', reason='claim_main_return_unrecognized')
            finally:
                persist()
        logger.attr('GachaMissionClaim', record)
        return record
