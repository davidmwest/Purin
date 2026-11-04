"""One-shot, purchase-free stock observations using the event-shop scheduler's normal navigation."""
import json
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from deploy.atomic import atomic_write
from module.base.utils import save_image
from module.exception import ScriptError
from module.logger import logger
from module.shop_event.clerk import ItemNotFoundError
from module.shop_event.shop_event import EventShop


def observation_request_path(account):
    if account not in ('alas', 'f2p'):
        raise ScriptError('Event-shop observations require a Purin account')
    return Path('./config/shop_observations') / (account + '.requested')


def observation_requested(account):
    return account in ('alas', 'f2p') and observation_request_path(account).exists()


class EventShopObservation(EventShop):
    def _save_observation(self):
        atomic_write(str(self.observation_directory / 'stock.json'), json.dumps(self.observation, indent=2))

    def _capture_observation(self, name):
        path = self.observation_directory / (name + '.png')
        save_image(self.device.image, str(path))
        return str(path)

    def event_shop_buy_item(self, *args, **kwargs):
        raise ScriptError('Purchases are forbidden during an event-shop observation')

    def event_shop_buy_item_execute(self, *args, **kwargs):
        raise ScriptError('Purchases are forbidden during an event-shop observation')

    def _run(self):
        """The parent run() calls this for each shop tab. No purchase handlers are entered."""
        index = len(self.observation['shops']) + 1
        shop = {'index': index, 'status': 'scanning', 'screens': [], 'items': []}
        self.observation['shops'].append(shop)
        self._save_observation()
        self.event_shop_load_ensure()
        shop['screens'].append(self._capture_observation(f'shop-{index}-header'))

        def capture(page, items):
            shop['screens'].append(self._capture_observation(f'shop-{index}-page-{page:02}'))
            self._save_observation()

        try:
            items = self.scan_all(max_pages=40, on_page=capture)
        except ItemNotFoundError as exc:
            # Parent purchasing jobs retry tabs; an incomplete audit stops after its one bounded attempt.
            raise ScriptError(f'Event-shop stock observation failed: {exc}') from exc
        if not items:
            raise ScriptError('No event-shop stock rows could be verified')
        self.get_current_pts()
        before = (int(self.pt), int(self.urpt) if self.event_shop_has_urpt else None)
        self.device.screenshot()
        self.get_current_pts()
        after = (int(self.pt), int(self.urpt) if self.event_shop_has_urpt else None)
        if before != after:
            raise ScriptError('Event-shop observation balance did not agree across fresh frames')
        shop.update(pt=after[0], urpt=after[1], status='complete')
        for item in items:
            shop['items'].append(dict(name=str(item.name), amount=int(item.amount), count=int(item.count),
                                      total_count=int(item.total_count), cost=str(item.cost), price=int(item.price),
                                      tag=item.tag, is_ship=bool(item.is_ship)))
        self._save_observation()
        logger.attr('EventShopStock', shop)
        return True

    def run(self, folder='./config/shop_observations'):
        account = self.config.config_name
        if account not in ('alas', 'f2p'):
            raise ScriptError('Event-shop observations require a Purin account')
        self.observation_directory = Path(folder).resolve() / account / (
            datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid4().hex[:8])
        self.observation_directory.mkdir(parents=True, exist_ok=False)
        self.observation = dict(account=account, status='reserved', shops=[], purchases=0,
                                started=datetime.now().isoformat())
        self._save_observation()
        # Durable before navigation: a crash/reboot must not turn this audit into the ordinary purchase job.
        self.config.cross_set('EventShop.Scheduler.Enable', False)
        try:
            available = super().run()
            self.ui_goto_main()
            self.observation.update(status='complete' if available else 'no_event_shop', returned_main=True,
                                    finished=datetime.now().isoformat())
            self._save_observation()
            return self.observation
        except Exception as exc:
            self.observation.update(status='blocked', reason=str(exc), finished=datetime.now().isoformat())
            self.observation['error_screen'] = self._capture_observation('blocked')
            self._save_observation()
            raise
