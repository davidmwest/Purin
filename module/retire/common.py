"""Retire idle Lv1 common duplicates, keeping a positively read locked copy.

This deliberately does not use the rarity-only old-retire chooser or change the
game's limit-break reservations for other rarities. Both the keeper and each
selected duplicate must have an exact name read in a fresh scan.
"""
from collections import Counter
from datetime import datetime
import json
from pathlib import Path

from module.base.utils import save_image
from module.dock.census import card_level, card_rarity, card_status
from module.dock.policy import Card
from module.exception import RequestHumanTakeover
from module.handler.assets import POPUP_CANCEL
from module.inventory.inventory import CARD_LEFTS, OCR_DOCK_AMOUNT
from module.leveling.enhance import CENSUS_NAME_SHIFT
from module.leveling.fleet import CARD_NAME, area_button
from module.leveling.plan import RETROFIT, ShipMatcher, load_ship_data, name_key
from module.logger import logger
from module.ocr.ocr import Ocr
from module.retire.assets import DOCK_CHECK, IN_RETIREMENT_CHECK, SHIP_CONFIRM, SHIP_CONFIRM_2
from module.retire.dock_walk import DockWalker
from module.ui.page import page_build, page_dock, page_main

# The Selected number on the EN retirement page, not DOCK_SELECTED (a fleet picker).
SELECTED_NUMBER = area_button((530, 657, 570, 694), 'COMMON_RETIRE_SELECTED')
MAX_RETIRE = 50


def exact_common(card):
    return bool(card.ship and name_key(card.read) == name_key(card.ship)
                and card.rarity == 'Normal' and card.frame == 'Normal'
                and not card.bulin and 'Bulin' not in card.ship
                and not card.ship.endswith(RETROFIT))


def common_duplicate_targets(cards):
    """Counts of eligible duplicates. A locked copy remains for every name."""
    keepers = {card.ship for card in cards if exact_common(card) and card.locked and card.level >= 1}
    return Counter(card.ship for card in cards
                   if exact_common(card) and card.ship in keepers
                   and not card.locked and card.level == 1 and card.fleet == 0 and card.status == 'free')


def common_duplicate_fits(card, remaining):
    return (exact_common(card) and remaining[card.ship] > 0 and not card.locked
            and card.level == 1 and card.fleet == 0 and card.status == 'free')


class CommonDuplicates:
    def _common_retire_recover(self):
        """Only recognized Cancel controls are used after a failed verification."""
        for _ in range(3):
            self.device.screenshot()
            if self.appear(POPUP_CANCEL, offset=(30, 100)):
                self.device.click(POPUP_CANCEL)
                self.device.sleep(0.3)
                continue
            if self.appear(SHIP_CONFIRM_2, offset=(30, 30)):
                return False
            if self.appear(IN_RETIREMENT_CHECK, offset=(20, 20)):
                if self._common_retire_count() != 0:
                    self.device.click(IN_RETIREMENT_CHECK)
                    self.device.sleep(0.3)
                    continue
                self.dock_filter_set()
                self.dock_sort_method_dsc_set(True, wait_loading=False)
                self._retirement_quit()
                return True
            if self.appear(DOCK_CHECK, offset=(20, 20)):
                self.dock_filter_set()
                self.dock_sort_method_dsc_set(True, wait_loading=False)
                return True
            return False
        return False

    def _common_retire_census(self, matcher):
        self.ui_ensure(page_main)
        self.ui_ensure(page_dock)
        self.dock_favourite_set(False, wait_loading=False)
        self.dock_sort_method_dsc_set(True, wait_loading=False)
        self.dock_filter_set(sort='level', index='all', faction='all', rarity='all', extra='no_limit')
        cards = []
        for index, (card, fleet) in enumerate(DockWalker(self, max_screens=512).cards()):
            if index >= 5000:
                raise RequestHumanTakeover('Common retirement census exceeded its bound')
            cards.append(self._common_retire_card(card, fleet, matcher))
        return cards

    def _common_retire_count(self):
        """An unread counter is different from a verified zero selection."""
        read = str(Ocr(SELECTED_NUMBER, lang='azur_lane', letter=(255, 255, 255), threshold=128,
                       alphabet='0123456789', name='COMMON_RETIRE_SELECTED').ocr(self.device.image)).strip()
        return int(read) if read.isdigit() and len(read) <= 2 else None

    def _common_retire_amount(self):
        current, _, total = OCR_DOCK_AMOUNT.ocr(self.device.image)
        if 0 < total <= 4030 and 0 <= current <= total:
            return current, total
        raise RequestHumanTakeover('Common retirement could not verify dock capacity')

    def _common_retire_card(self, card, fleet, matcher):
        image = self.device.image
        x1, y1, x2, y2 = CARD_NAME
        left, top = CARD_LEFTS[card.column], card.top + CENSUS_NAME_SHIFT
        button = area_button((left + x1, top + y1, left + x2, top + y2), 'COMMON_RETIRE_NAME')
        read = str(Ocr(button, lang='cnocr', letter=(255, 255, 255), threshold=128,
                       name='COMMON_RETIRE_NAME').ocr(image))
        data, score = matcher.match_score(read)
        # Fuzzy names are adequate for inventory; irreversible retirement requires an exact read.
        ship = data.name if data is not None and score == 1 and name_key(read) == name_key(data.name) else ''
        return Card(index=0, read=read, ship=ship, rarity=data.rarity if ship else '',
                    frame=card_rarity(image, card.column, card.top), level=card_level(image, card.column, card.top),
                    locked=bool(card.locked), fleet=int(fleet), status=card_status(image, card.column, card.top),
                    bulin=bool(data.is_bulin) if ship else False, retrofit=False)

    def _common_retire_candidate(self, matcher, remaining):
        walker = DockWalker(self, list_bottom=620, card_read_bottom=204, max_screens=512)
        for index, (card, fleet) in enumerate(walker.cards()):
            if index >= 5000:
                raise RequestHumanTakeover('Common retirement dock scan exceeded its bound')
            read = self._common_retire_card(card, fleet, matcher)
            if common_duplicate_fits(read, remaining):
                # A fresh screenshot must still show this exact eligible card at this position.
                self.device.screenshot()
                reader = walker.reader
                card.locked = reader.is_locked(self.device.image, CARD_LEFTS[card.column], card.top)
                fleet = walker.fleet_of(self.device.image, card)
                check = self._common_retire_card(card, fleet, matcher)
                if check != read or not common_duplicate_fits(check, remaining):
                    raise RequestHumanTakeover('Common retirement card changed while it was being verified')
                return card, check
        return None

    def retire_common_duplicates(self):
        """A fresh census then one verified card per confirmation, bounded by that census."""
        if self.config.SERVER != 'en':
            raise RequestHumanTakeover('Common duplicate retirement currently requires the verified EN layout')
        matcher = ShipMatcher(load_ship_data())
        cards = self._common_retire_census(matcher)
        remaining = common_duplicate_targets(cards)
        logger.attr('Common_duplicate_targets', dict(remaining))
        report = dict(time=datetime.now().isoformat(), dry_run=bool(getattr(self.config, 'Retirement_DryRun', False)),
                      targets=dict(remaining), retired=[], complete=False)
        directory = Path('config') / 'retire' / self.config.config_name / datetime.now().strftime('%Y%m%d-%H%M%S-%f-common')
        # If audit storage cannot be created, stop before selecting any ship.
        directory.mkdir(parents=True, exist_ok=False)
        try:
            if not remaining or report['dry_run']:
                logger.info('Common duplicate retirement: census only, no cards selected')
                report['complete'] = True
                return 0

            # Capacity is read only on page_dock, its verified layout. Return there after every single retirement.
            self.ui_ensure(page_dock)
            self.device.screenshot()
            before = self._common_retire_amount()
            report['dock_before'] = before
            for _ in range(min(sum(remaining.values()), MAX_RETIRE)):
                self.ui_ensure(page_build)
                self.retire_page_enter()
                self.handle_dock_cards_loading()
                self.dock_favourite_set(False, wait_loading=False)
                self.dock_sort_method_dsc_set(False, wait_loading=False)
                self.dock_filter_set(sort='level', index='all', faction='all', rarity='common', extra='no_limit')
                self.device.screenshot()
                save_image(self.device.image, str(directory / f'before-{len(report["retired"]) + 1:02}.png'))
                if not self.appear(IN_RETIREMENT_CHECK, offset=(20, 20)) or self._common_retire_count() != 0:
                    raise RequestHumanTakeover('Common retirement requires a verified empty selection')
                candidate = self._common_retire_candidate(matcher, remaining)
                if candidate is None:
                    self._retirement_quit()
                    self.ui_ensure(page_dock)
                    break
                card, read = candidate
                if self._common_retire_count() != 0:
                    raise RequestHumanTakeover('Common retirement selection was not empty before picking a card')
                logger.info(f'Common duplicate retire: {read.ship}, Lv1, unlocked, idle; locked copy retained')
                self.device.click(card.button)
                self.device.sleep(0.3)
                self.device.screenshot()
                save_image(self.device.image, str(directory / f'selected-{len(report["retired"]) + 1:02}.png'))
                if self._common_retire_count() != 1 \
                        or not self.match_template_color(SHIP_CONFIRM, offset=(30, 30)) \
                        or self.appear(SHIP_CONFIRM_2, offset=(30, 30)):
                    raise RequestHumanTakeover('Common retirement could not verify exactly one selected card')
                # The existing central DryRun guard remains authoritative. No quick-retire selection is invoked.
                self._retirement_confirm()
                self.handle_dock_cards_loading()
                self.device.screenshot()
                if not self.appear(IN_RETIREMENT_CHECK, offset=(20, 20)) or self._common_retire_count() != 0:
                    raise RequestHumanTakeover('Common retirement did not return to an empty selection')
                self._retirement_quit()
                self.ui_ensure(page_dock)
                self.handle_dock_cards_loading()
                self.device.screenshot()
                after = self._common_retire_amount()
                report['retired'].append(dict(ship=read.ship, level=1, dock_before=before, dock_after=after))
                if after != (before[0] - 1, before[1]):
                    raise RequestHumanTakeover('Common retirement did not verify exactly one ship removed')
                before = after
                remaining[read.ship] -= 1
            self.dock_sort_method_dsc_set(True, wait_loading=False)
            self.dock_filter_set()
            report['dock_after'] = before
            report['remaining'] = dict(remaining)
            report['complete'] = True
            logger.info(f'Common duplicates retired: {len(report["retired"])}')
            return len(report['retired'])
        except Exception as error:
            report['error'] = str(error)
            try:
                report['recovered'] = self._common_retire_recover()
            except Exception as recovery_error:
                logger.warning(f'Common retirement recovery could not verify a safe page: {recovery_error}')
                report['recovered'] = False
            raise
        finally:
            (directory / 'common.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
            logger.info(f'Common duplicate retirement audit saved to {directory}')
