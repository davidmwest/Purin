"""Submit only the remaining exact Build 3 mission orders, then claim its reward."""
import json
import os
import re
import tempfile

from module.base.timer import Timer
from module.config.utils import get_server_last_update
from module.eventstory.eventstory import ensure_event_page
from module.exception import ScriptError
from module.gacha.mission import (CURRENT_EVENT, GachaMissionOrderObserver,
                                  read_build_mission, remaining_mission_builds)
from module.gacha.mission_claim import GachaMissionClaim, earned_build_mission
from module.gacha.mission_submit_assets import (COUNT_WITNESSES, COUNT_MINUS, COUNT_PLUS,
                                                 COUNT_CANCEL, COUNT_CONFIRM)
from module.logger import logger
from module.ocr.ocr import Ocr
from module.ui.page import page_main, page_mission

ORDER_VERSION = 'build-three-orders-v1'
COIN_AREA = (815, 23, 922, 51)
# The legacy crop ends at925 and clips the final digit of the actual405.
CUBE_AREA = (845, 106, 935, 137)


def parse_resource(text):
    if not isinstance(text, str):
        return None
    text = text.translate(str.maketrans({'I': '1', 'D': '0', 'S': '5', 'B': '8'}))
    return int(text) if re.fullmatch(r'\s*[0-9]{1,7}\s*', text) else None


def read_order_resources(image):
    """Strict raw numbers; callers must first prove the unobscured build pool."""
    values = []
    for name, area, letter in (('MISSION_COINS', COIN_AREA, (239, 239, 239)),
                               ('MISSION_CUBES', CUBE_AREA, (255, 247, 247))):
        raw = Ocr(area, alphabet='0123456789IDSB', letter=letter,
                  threshold=128, name=name).ocr(image)
        value = parse_resource(raw)
        if value is None:
            return None
        values.append(value)
    return dict(coins=values[0], cubes=values[1])


def resources_cover_order(resources, count, pool='event'):
    if (not isinstance(resources, dict) or type(count) is not int or not 1 <= count <= 3
            or pool not in ('event', 'light', 'heavy', 'special')):
        return False
    if any(type(resources.get(name)) is not int or resources[name] < 0
           for name in ('coins', 'cubes')):
        return False
    coins, cubes = (600, 1) if pool == 'light' else (1500, 2)
    return resources['coins'] >= coins * count and resources['cubes'] >= cubes * count


def read_count_cost(image):
    """Raw count/cost fields; exact dialog witnesses are required by callers."""
    values = []
    for name, area, color in (
            ('BUILD_COUNT', (616,257,744,292), (255,255,255)),
            ('BUILD_COIN_COST', (430,362,507,391), (146,235,97)),
            ('BUILD_CUBE_COST', (633,362,652,391), (146,235,97))):
        value = parse_resource(Ocr(area, alphabet='0123456789IDSB', letter=color,
                                   threshold=128, name=name).ocr(image))
        if value is None:
            return None
        values.append(value)
    count, coins, cubes = values
    if not 1 <= count <= 3 or coins != count * 1500 or cubes != count * 2:
        return None
    return dict(count=count, coins=coins, cubes=cubes)


def order_state_path(account, folder='./config/gacha'):
    if not re.fullmatch(r'[A-Za-z0-9_-]+', account):
        raise ScriptError('Invalid account name for Gacha order state')
    return os.path.join(folder, f'{account}-mission-orders.json')


def load_order_state(account, folder='./config/gacha'):
    try:
        with open(order_state_path(account, folder), encoding='utf-8') as stream:
            state = json.load(stream)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise ScriptError(f'Cannot safely read Gacha order state: {exc}') from exc
    if not isinstance(state, dict) or any(not isinstance(item, dict) for item in state.values()):
        raise ScriptError('Invalid Gacha order state')
    return state


def save_order_state(account, state, folder='./config/gacha'):
    os.makedirs(folder, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.gacha-orders-', suffix='.json', dir=folder)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(state, stream, indent=2, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, order_state_path(account, folder))
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def mission_claim_recovery_due(config, folder='./config/gacha'):
    """Read durable credit before any navigator can consume an owned receipt."""
    period = get_server_last_update(config.Scheduler_ServerUpdate).isoformat()
    key = f'{period}:{CURRENT_EVENT}:{ORDER_VERSION}'
    previous = load_order_state(config.config_name, folder).get(key, {})
    return type(previous.get('mission_credit')) is int and previous['mission_credit'] == 3


class GachaMissionOrders(GachaMissionOrderObserver):
    def _read_authorization_tab(self, record, name, upper):
        if not self._observe_mission_tab(record, name, upper):
            return None
        previous, frames = None, 0
        settled = Timer(1, count=2).start()
        for _ in self.loop(skip_first=False, timeout=8):
            value = None
            if (self.ui_page_appear(page_mission)
                    and self._reward_side_navbar.get_info(self) == (upper - 1, 0, 5)):
                if earned_build_mission(self.device.image) is not None:
                    value = dict(current=3, total=3, earned=True, tab=name)
                else:
                    numeric = read_build_mission(self.device.image)
                    if numeric is not None:
                        value = dict(current=numeric['current'], total=3,
                                     earned=False, tab=name)
            if value is None or value != previous:
                previous, frames = value, 1 if value else 0
                settled.reset()
                continue
            frames += 1
            if frames >= 2 and settled.reached():
                self._capture_mission(record, f'authorized_{name}_credit')
                return value
        return None

    def _read_daily_authorization(self, record):
        return self._read_authorization_tab(record, 'daily', 4)

    def _authorization(self, record):
        daily = self._read_daily_authorization(record)
        event = self._read_authorization_tab(record, 'event', 6)
        credits = [item for item in (daily, event) if item is not None]
        if not credits or len({item['current'] for item in credits}) != 1:
            return None
        record['credit_evidence'] = credits
        record['claim_tabs'] = [4 if item['tab']=='daily' else 6 for item in credits]
        return dict(current=credits[0]['current'], earned=any(item['earned'] for item in credits))

    def _claim(self, record, folder):
        record['claim_status'] = 'resuming'
        result = GachaMissionClaim(config=self.config, device=self.device).run(folder=folder, tabs=tuple(record.get('claim_tabs', [4])))
        record.update(mission_reward_claimed=bool(result.get('mission_reward_claimed')),
                      claim_status=result.get('status'), returned_main=bool(result.get('returned_main')))
        record['diagnostics'].extend(result.get('diagnostics', []))
        if record['mission_reward_claimed']:
            record.update(status='complete', reason='three_builds_and_reward_verified')
        else:
            record.update(status='blocked', reason='credited_mission_reward_unverified')

    def _count_witness(self):
        return all(self.match_template_color(asset, offset=(3,3), similarity=.85)
                   for asset in COUNT_WITNESSES)

    def _stable_count(self):
        previous, frames = None, 0
        settled = Timer(1, count=2).start()
        for _ in self.loop(skip_first=False, timeout=8):
            value = read_count_cost(self.device.image) if self._count_witness() else None
            if value is None or value != previous:
                previous, frames = value, 1 if value else 0
                settled.reset()
                continue
            frames += 1
            if frames >= 2 and settled.reached():
                return value
        return None

    def _stable_resources(self, banner):
        previous, frames = None, 0
        settled = Timer(1, count=2).start()
        for _ in self.loop(skip_first=False, timeout=8):
            value = read_order_resources(self.device.image) if banner.on_event_pool() else None
            if value is None or value != previous:
                previous, frames = value, 1 if value else 0
                settled.reset()
                continue
            frames += 1
            if frames >= 2 and settled.reached():
                return value
        return None

    def _pool_after_dialog(self, banner):
        frames = 0
        settled = Timer(1, count=2).start()
        for _ in self.loop(skip_first=False, timeout=12):
            if not banner.on_event_pool():
                frames = 0
                settled.reset()
                continue
            frames += 1
            if frames >= 2 and settled.reached():
                return True
        return False

    def _cancel_order(self, record, persist, banner):
        # This exact Cancel is the observed count dialog, never a purchase or
        # generic popup. A failed click cannot be repeated in this job period.
        if (record.get('submit_clicks') or record.get('cancel_clicks')
                or not self._count_witness()):
            return
        record['cancel_clicks'] = 1
        persist()
        self.device.click(COUNT_CANCEL)
        if self._pool_after_dialog(banner):
            record['order_dialog_open'] = False

    def _submit_order(self, record, persist):
        from module.gacha.banner import EventBannerObserver, resolve_mission_pool
        record['preferred_pool'] = resolve_mission_pool(
            self.config.config_name, fallback=getattr(self.config, 'Gacha_FallbackPool', 'light'))
        # Current evidence proves the Limited Build dialog only. An owned-banner
        # fallback must never silently place duplicate Event orders.
        if record['preferred_pool'] != 'event':
            record.update(status='blocked', reason='fallback_pool_controls_unobserved')
            return
        banner = EventBannerObserver(config=self.config, device=self.device)
        if not banner.enter_event_pool():
            record.update(status='blocked', reason='current_event_pool_unrecognized')
            return
        resources = self._stable_resources(banner)
        record['resources'] = resources
        if not resources_cover_order(resources, record['remaining']):
            record.update(status='blocked', reason='insufficient_or_unreadable_build_resources')
            return
        if record.get('dialog_opens') or self._period() != record['period']:
            record.update(status='blocked', reason='dialog_budget_or_period_changed')
            return
        record['dialog_opens'] = 1
        persist()
        if self._period() != record['period']:
            record.update(status='blocked', reason='mission_period_changed')
            return
        self._open_order_dialog(record, persist)
        record['order_dialog_open'] = bool(record.get('build_open_clicked'))
        if record.get('reason') != 'order_count_dialog_requires_review':
            return
        value = self._stable_count()
        for _ in range(2):
            if value is None or value['count'] == record['remaining']:
                break
            if self._period() != record['period']:
                break
            expected = value['count'] + (1 if value['count'] < record['remaining'] else -1)
            record['adjust_clicks'] = record.get('adjust_clicks', 0) + 1
            persist()
            if self._period() != record['period']:
                break
            self.device.click(COUNT_PLUS if expected > value['count'] else COUNT_MINUS)
            value = self._stable_count()
            if value is None or value['count'] != expected:
                value = None
                break
        # Read once more immediately before the durable final input reservation.
        final = self._stable_count() if value is not None else None
        if (final is None or final['count'] != record['remaining']
                or not resources_cover_order(resources, final['count'])
                or self._period() != record['period']
                or resolve_mission_pool(self.config.config_name,
                    fallback=getattr(self.config, 'Gacha_FallbackPool', 'light')) != 'event'):
            record.update(status='blocked', reason='fresh_count_cost_or_period_unverified')
            self._capture_mission(record, 'count_unverified')
            self._cancel_order(record, persist, banner)
            return
        record.update(status='submitting', reason='submission_credit_pending',
                      submission_pending=True, submit_clicks=1,
                      orders_reserved=record['remaining'], final_count_cost=final)
        persist()
        if self._period() != record['period']:
            record.update(status='blocked', reason='mission_period_changed_before_submit')
            return
        self.device.click(COUNT_CONFIRM)
        record['submit_click_performed'] = True
        persist()
        # A return to Build is not proof that orders were accepted. Only fresh
        # mission credit below can prove submission and authorize reward claim.
        if self._pool_after_dialog(banner):
            record['order_dialog_open'] = False
        else:
            record.update(status='blocked', reason='submission_result_unrecognized')
        self._capture_mission(record, 'submission_result')

    def run(self, folder='./config/gacha'):
        account, period = self.config.config_name, self._period()
        key = f'{period}:{CURRENT_EVENT}:{ORDER_VERSION}'
        state = load_order_state(account, folder)
        previous = state.get(key, {})
        for name, maximum in (('submit_clicks', 1), ('orders_submitted', 3), ('dialog_opens', 1),
                              ('adjust_clicks', 2), ('cancel_clicks', 1), ('orders_reserved', 3),
                              ('warning_ack_clicks', 1)):
            if name in previous and (type(previous[name]) is not int or not 0 <= previous[name] <= maximum):
                raise ScriptError('Invalid durable Gacha order budget')
        for name in ('submission_pending', 'mission_reward_claimed', 'submit_click_performed'):
            if name in previous and type(previous[name]) is not bool:
                raise ScriptError('Invalid durable Gacha order proof')
        credit = previous.get('mission_credit')
        if credit is not None and (type(credit) is not int or not 0 <= credit <= 3):
            raise ScriptError('Invalid durable Gacha mission credit')
        if not isinstance(previous.get('diagnostics', []), list):
            raise ScriptError('Invalid durable Gacha order diagnostics')
        claim_tabs = previous.get('claim_tabs', [4])
        if (not isinstance(claim_tabs, list) or not claim_tabs
                or any(type(tab) is not int or tab not in (4,6) for tab in claim_tabs)
                or len(set(claim_tabs)) != len(claim_tabs)):
            raise ScriptError('Invalid durable Gacha claim scope')
        if previous.get('mission_reward_claimed'):
            if credit != 3:
                raise ScriptError('Claimed Gacha reward lacks verified mission credit')
            if not previous.get('returned_main'):
                previous.setdefault('diagnostics', [])
                self._claim(previous, folder)
                save_order_state(account, state, folder)
            return dict(previous, reused=True)
        # Generic page discovery may dismiss GET_ITEMS without recording this
        # claim's receipt. Resume verified credit directly through its journal.
        if credit != 3:
            self.ui_get_current_page()
        record = dict(status='reading', reason='fresh_build_mission_required', period=period,
                      event=CURRENT_EVENT, diagnostics=[], mission_tabs=[],
                      submit_clicks=0, orders_submitted=0, submission_pending=False,
                      mission_credit=None, mission_reward_claimed=False, returned_main=False,
                      preferred_pool='event', dialog_opens=0, adjust_clicks=0, cancel_clicks=0,
                      orders_reserved=0, warning_ack_clicks=0, submit_click_performed=False, claim_tabs=[4])
        record.update({name: previous[name] for name in (
            'submit_clicks', 'orders_submitted', 'submission_pending',
            'mission_credit', 'diagnostics', 'dialog_opens', 'adjust_clicks', 'cancel_clicks',
            'orders_reserved', 'warning_ack_clicks', 'submit_click_performed', 'claim_tabs') if name in previous})
        state[key] = record

        def persist():
            save_order_state(account, state, folder)

        persist()
        try:
            # Durable credit can outlive a claimed row that Reward removed.
            # The independent claim journal resumes receipts or verifies its
            # own completed claim; it cannot submit or open Build.
            if credit == 3:
                self._claim(record, folder)
                authorization = None
            else:
                authorization = self._authorization(record)
            if credit == 3:
                pass
            elif authorization is None:
                record.update(status='blocked', reason='build_mission_credit_unknown')
            elif self._period() != period:
                record.update(status='blocked', reason='mission_period_changed')
            else:
                if credit is not None and authorization['current'] < credit:
                    record.update(status='blocked', reason='build_mission_credit_regressed')
                    return record
                record['mission_credit'] = authorization['current']
                record['remaining'] = remaining_mission_builds(authorization['current'], 3)
                persist()
                if authorization['current'] == 3:
                    if record['submit_clicks']:
                        record['orders_submitted'] = record['orders_reserved']
                    record['submission_pending'] = False
                    self._claim(record, folder)
                elif record['submit_clicks'] or record['orders_submitted'] or record['submission_pending']:
                    # An interrupted input may have reached the game. Its
                    # ledger cannot authorize another order, even if the old
                    # counter is still visible or credit verification fails.
                    record.update(status='blocked', reason='reserved_order_credit_unverified')
                else:
                    self._submit_order(record, persist)
                    if record.get('submit_click_performed') and not record.get('order_dialog_open'):
                        fresh = self._authorization(record)
                        if fresh is not None and fresh['current'] == 3 and self._period() == period:
                            record.update(mission_credit=3, orders_submitted=record['orders_reserved'],
                                          submission_pending=False)
                            persist()
                            self._claim(record, folder)
                        else:
                            record.update(status='blocked', reason='reserved_order_credit_unverified')
        except Exception:
            record.update(status='blocked', reason='mission_order_exception')
            self._capture_mission(record, 'mission_order_exception')
            raise
        finally:
            # Never navigate behind an unknown or still-open submission dialog.
            if not record.get('order_dialog_open') and not record.get('claim_status'):
                record['returned_main'] = ensure_event_page(self, page_main)
            record['retry_required'] = record['status'] != 'complete'
            persist()
        logger.attr('GachaMissionOrders', record)
        return record
