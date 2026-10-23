"""Capture the observed DAY7, Rental Outfits and Packs entries without claiming."""
from datetime import datetime

from module.base.button import Button
from module.base.timer import Timer
from module.campaign.event_completion import (
    EventCompletion, load_reward_observations, save_reward_observations)
from module.eventstory.eventstory import ensure_event_page, ensure_event_running, save_event_diagnostic
from module.logger import logger
from module.ui.page import page_event_list, page_main, page_shop


def _button(name, area, color, click=None, label=None):
    return Button(area=area, color=color, button=click or area,
                  file=f'./assets/en/event_completion/MAIN_NOTIFICATION_{name}_20261023.png',
                  name='MAIN_NOTIFICATION_' + (label or name))


DAY7 = _button('DAY7', (969, 96, 1031, 154), (101, 121, 148), (978, 106, 1022, 147))
RENTAL_OUTFITS = _button('RENTAL_OUTFITS', (74, 684, 209, 700), (83, 96, 121),
                         (73, 635, 208, 676))
EVENTS_TITLE = _button('RENTAL_OUTFITS', (124, 15, 231, 39), (110, 123, 163), label='EVENTS_TITLE')
EVENTS_HOME = _button('RENTAL_OUTFITS', (1227, 11, 1255, 45), (107, 122, 149),
                      label='EVENTS_HOME')
PACKS = _button('PACKS', (59, 311, 128, 333), (127, 168, 195), (51, 311, 135, 333))
SHOP_TITLE = _button('PACKS', (148, 25, 204, 43), (126, 132, 143), label='SHOP_TITLE')
SHOP_HOME = _button('PACKS', (1208, 20, 1237, 48), (169, 168, 169), label='SHOP_HOME')

ENTRIES = {
    'day7': (page_main, None, DAY7),
    'rental_outfits': (page_event_list, EVENTS_TITLE, RENTAL_OUTFITS),
    'packs': (page_shop, SHOP_TITLE, PACKS),
}


class EventMainNotifications(EventCompletion):
    def _exact(self, button):
        button.clear_offset()
        return self.match_template_color(button, offset=(3, 3), similarity=.9)

    def _source(self, entry):
        page, title, button = ENTRIES[entry]
        return (self.ui_page_appear(page) and (title is None or self._exact(title))
                and self._exact(button))

    def _treasure_home(self):
        from module.campaign.event_treasure_free_pack_assets import (
            TREASURE_DATES, TREASURE_HEADER, TREASURE_HOME)
        if all(self._exact(button) for button in (TREASURE_HEADER, TREASURE_DATES, TREASURE_HOME)):
            return TREASURE_HOME
        return None

    def _notification_return_main(self, record, persist):
        """Use Home only on an exactly recognized destination captured by Purin."""
        settled = Timer(1, count=2).start()
        clicked = False
        for _ in self.loop(skip_first=False, timeout=8):
            if self.ui_page_appear(page_main):
                if settled.reached():
                    return True
                continue
            if clicked:
                settled.reset()
                continue
            home = None
            if self.ui_page_appear(page_event_list) and self._exact(EVENTS_TITLE) and self._exact(EVENTS_HOME):
                home = EVENTS_HOME
            elif self.ui_page_appear(page_shop) and self._exact(SHOP_TITLE) and self._exact(SHOP_HOME):
                home = SHOP_HOME
            else:
                home = self._treasure_home()
            if home is None:
                # Unknown destinations are evidence only, with no guessed exit.
                return False
            if not settled.reached():
                continue
            record['home_clicks'] = 1
            persist()
            self.device.click(home)
            clicked = True
            settled.reset()
        return False

    def observe(self, event, entry, folder='./config/stars'):
        account = self.config.config_name
        if event != 'event_20261022_cn' or account not in ('alas', 'f2p') or entry not in ENTRIES:
            return dict(status='unsupported', diagnostics=[], returned_main=True)
        ensure_event_running(self)
        state = load_reward_observations(account, folder=folder)
        key = event + ':main_notification:' + entry
        if key in state:
            return dict(state[key], reused=True)
        record = dict(status='observing', diagnostics=[], clicks=0, home_clicks=0,
                      returned_main=False, entry=entry,
                      started=datetime.now().isoformat(timespec='seconds'))
        state[key] = record

        def persist():
            save_reward_observations(account, state, folder=folder)

        # Reserve before navigation as well as before the single notification tap.
        persist()
        try:
            if not ensure_event_page(self, ENTRIES[entry][0], timeout=20, max_clicks=6):
                record.update(status='blocked', reason='notification_parent_unrecognized')
            else:
                settled = Timer(1, count=2).start()
                after_click = Timer(3, count=4)
                for _ in self.loop(skip_first=False, timeout=12):
                    if record['clicks']:
                        if after_click.reached():
                            record.update(status='observed', reason='notification_destination_capture_only')
                            break
                        continue
                    if not self._source(entry):
                        settled.reset()
                        continue
                    if not settled.reached():
                        continue
                    record['clicks'] = 1
                    persist()
                    self.device.click(ENTRIES[entry][2])
                    after_click.reset()
                else:
                    record.update(status='blocked', reason='notification_entry_unrecognized')
            path = save_event_diagnostic(self.device, f'{account}_{entry}_{record["status"]}')
            if path:
                record['diagnostics'].append(path)
            else:
                record.update(status='blocked', reason='diagnostic_not_saved')
            # The original destination is captured before any verified Home action.
            if record['status'] == 'observed':
                record['returned_main'] = self._notification_return_main(record, persist)
        except Exception as exc:
            record.update(status='failed', reason=type(exc).__name__)
            raise
        finally:
            persist()
            logger.attr('EventMainNotification', record)
        return record

    def observe_day7(self, event, folder='./config/stars'):
        return self.observe(event, 'day7', folder=folder)

    def observe_rental_outfits(self, event, folder='./config/stars'):
        return self.observe(event, 'rental_outfits', folder=folder)

    def observe_packs(self, event, folder='./config/stars'):
        return self.observe(event, 'packs', folder=folder)
