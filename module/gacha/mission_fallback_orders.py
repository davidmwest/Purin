"""Place the remaining mission orders in a positively authorized ordinary pool.

The caller owns the daily journal and verifies mission credit. This helper
shares that journal's one opener/one submission budgets; it never claims a
receipt or touches the construction queue.
"""
from module.base.timer import Timer
from module.eventstory.eventstory import ensure_event_running
from module.exception import ScriptError
from module.gacha.banner import resolve_mission_pool
from module.gacha.banner_assets import EVENT_BUILD_ORDERS
from module.gacha.mission_fallback import GachaFallbackObserver, POOL_RIGHT, SHARED_COUNT_WITNESSES
from module.gacha.mission_fallback_assets import ORDINARY_COST_TITLES
from module.gacha.mission_order_assets import UR_CAP_CONFIRM
from module.gacha.mission_submit_assets import COUNT_CANCEL, COUNT_CONFIRM, COUNT_MINUS, COUNT_PLUS
from module.ocr.ocr import Ocr


def read_ordinary_count_cost(image, pool):
    """Strict totals, including the actual Light 600 coin/one cube unit cost."""
    from module.gacha.mission_orders import parse_resource

    if pool not in POOL_RIGHT:
        return None
    count = parse_resource(Ocr((616,257,744,292), alphabet='0123456789IDSB',
        letter=(255,255,255), threshold=128, name='ORDINARY_BUILD_COUNT').ocr(image))
    if type(count) is not int or not 1 <= count <= 3:
        return None
    # The centered cost line shifts when Light's three-digit 600 becomes a
    # four-digit total. All other supported totals already have four digits.
    cube_area = (628,365,643,389) if pool == 'light' and count == 1 else (633,362,652,391)
    values = []
    for name, area in (('ORDINARY_COIN_COST', (430,362,507,391)),
                       ('ORDINARY_CUBE_COST', cube_area)):
        value = parse_resource(Ocr(area, alphabet='0123456789IDSB', letter=(146,235,97),
                                   threshold=128, name=name).ocr(image))
        if value is None:
            return None
        values.append(value)
    coins, cubes = values
    unit_coins, unit_cubes = (600,1) if pool == 'light' else (1500,2)
    if coins != count * unit_coins or cubes != count * unit_cubes:
        return None
    return dict(count=count, coins=coins, cubes=cubes)


class OrdinaryMissionOrders(GachaFallbackObserver):
    def _authorized(self, record, pool, folder):
        return (self._period() == record['period'] and resolve_mission_pool(
            self.config.config_name, fallback=getattr(self.config, 'Gacha_FallbackPool', 'light'),
            folder=folder) == pool)

    def _count_witness(self, pool):
        return all(self._exact(asset) for asset in (
            *SHARED_COUNT_WITNESSES, ORDINARY_COST_TITLES[pool]))

    def _stable_count(self, pool):
        previous, frames = None, 0
        settled = Timer(1, count=2).start()
        for _ in self.loop(skip_first=False, timeout=8):
            value = read_ordinary_count_cost(self.device.image, pool) if self._count_witness(pool) else None
            if value is None or value != previous:
                previous, frames = value, 1 if value else 0
                settled.reset()
                continue
            frames += 1
            if frames >= 2 and settled.reached():
                return value
        return None

    def _stable_resources(self, pool):
        from module.gacha.mission_orders import read_order_resources

        previous, frames = None, 0
        settled = Timer(1, count=2).start()
        for _ in self.loop(skip_first=False, timeout=8):
            value = read_order_resources(self.device.image) if self._on_pool(pool) else None
            if value is None or value != previous:
                previous, frames = value, 1 if value else 0
                settled.reset()
                continue
            frames += 1
            if frames >= 2 and settled.reached():
                return value
        return None

    def _open_count(self, record, persist, pool, folder):
        if not self._on_pool(pool) or not self._authorized(record, pool, folder):
            return False
        record.update(dialog_opens=1, build_open_clicked=True, order_dialog_open=True)
        persist()
        if not self._authorized(record, pool, folder):
            return False
        self.device.click(EVENT_BUILD_ORDERS)
        settled = Timer(1, count=2).start()
        previous, frames = None, 0
        for _ in self.loop(skip_first=False, timeout=12):
            state = 'warning' if self._ur_cap_warning() else 'count' if self._count_witness(pool) else None
            if state is None or state != previous:
                previous, frames = state, 1 if state else 0
                settled.reset()
                continue
            frames += 1
            if frames < 3 or not settled.reached():
                continue
            if state == 'count':
                self._capture(record, 'authorized_count_dialog')
                return True
            if record.get('warning_ack_clicks') or not self._authorized(record, pool, folder):
                return False
            record['warning_ack_clicks'] = 1
            persist()
            if not self._authorized(record, pool, folder):
                return False
            self.device.click(UR_CAP_CONFIRM)
            previous, frames = None, 0
            settled.reset()
        return False

    def _cancel(self, record, persist, pool):
        if (record.get('submit_clicks') or record.get('cancel_clicks')
                or not self._count_witness(pool)):
            return
        record['cancel_clicks'] = 1
        persist()
        self.device.click(COUNT_CANCEL)
        if self._wait_pool(pool):
            record['order_dialog_open'] = False

    def _return_after_submit(self):
        from module.gacha.mission_result import GachaSubmissionResult

        # This helper takes fresh frames and waits through count disappearance;
        # it never invokes generic popup/queue handling on an unknown image.
        return GachaSubmissionResult(config=self.config, device=self.device).return_to_main()

    def submit(self, record, persist, pool, folder='./config/gacha'):
        from module.gacha.mission_orders import resources_cover_order

        if pool not in POOL_RIGHT:
            raise ScriptError('Unsupported ordinary mission pool')
        if (type(record.get('remaining')) is not int or not 1 <= record['remaining'] <= 3
                or not isinstance(record.get('period'), str)):
            raise ScriptError('Invalid ordinary mission authorization')
        record['pool'] = pool
        if any(record.get(key) for key in (
                'submit_clicks', 'orders_reserved', 'orders_submitted', 'submission_pending',
                'dialog_opens', 'build_open_clicked', 'adjust_clicks', 'cancel_clicks', 'warning_ack_clicks')):
            record.update(status='blocked', reason='ordinary_order_budget_already_reserved')
            return False
        ensure_event_running(self)
        if not self._authorized(record, pool, folder):
            record.update(status='blocked', reason='ordinary_pool_ownership_or_period_unverified')
            return False
        if not self._enter_pool(pool):
            record.update(status='blocked', reason='ordinary_pool_navigation_unverified')
            self._capture(record, 'pool_blocked')
            return False
        resources = self._stable_resources(pool)
        record['resources'] = resources
        if not resources_cover_order(resources, record['remaining'], pool=pool):
            record.update(status='blocked', reason='insufficient_or_unreadable_build_resources')
            return False
        if not self._open_count(record, persist, pool, folder):
            record.update(status='blocked', reason='ordinary_count_dialog_unverified')
            self._capture(record, 'dialog_blocked')
            self._cancel(record, persist, pool)
            return False
        value = self._stable_count(pool)
        for _ in range(2):
            if value is None or value['count'] == record['remaining']:
                break
            if not self._authorized(record, pool, folder):
                value = None
                break
            expected = value['count'] + (1 if value['count'] < record['remaining'] else -1)
            record['adjust_clicks'] = record.get('adjust_clicks', 0) + 1
            persist()
            if not self._authorized(record, pool, folder):
                value = None
                break
            self.device.click(COUNT_PLUS if expected > value['count'] else COUNT_MINUS)
            value = self._stable_count(pool)
            if value is None or value['count'] != expected:
                value = None
                break
        final = self._stable_count(pool) if value is not None else None
        if (final is None or final['count'] != record['remaining']
                or not resources_cover_order(resources, final['count'], pool=pool)
                or not self._authorized(record, pool, folder)):
            record.update(status='blocked', reason='ordinary_count_cost_or_authorization_unverified')
            self._capture(record, 'count_unverified')
            self._cancel(record, persist, pool)
            return False
        record.update(status='submitting', reason='submission_credit_pending',
                      submission_pending=True, submit_clicks=1,
                      orders_reserved=record['remaining'], final_count_cost=final)
        persist()
        if not self._authorized(record, pool, folder):
            record.update(status='blocked', reason='ordinary_authorization_changed_before_submit')
            return False
        self.device.click(COUNT_CONFIRM)
        record['submit_click_performed'] = True
        persist()
        if self._return_after_submit():
            record['order_dialog_open'] = False
        else:
            record.update(status='blocked', reason='submission_result_unrecognized')
        self._capture(record, 'submission_result')
        # Only the caller's fresh mission credit can prove successful orders.
        return not record.get('order_dialog_open')
