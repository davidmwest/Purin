"""
DockCare: keep room in the dock of a free-to-play account.

The user, 2026-10-22: "dock space is super precious for a f2p account", then "Build option d" of the F2P dock doc:
no copies held for common, rare and elite ships but what the fleet ships still need for their limit breaks, one copy
of each ship with a retrofit, super rares and up and Bulins kept, the rest used at once or retired.

A run walks the whole dock and reads every card (name, lock, fleet, level, rarity, commission), decides each one
with module/dock/policy.py, and logs the verdicts. DockCare.DryRun keeps it to that: retiring cannot be undone, so the
user reviews a run before Purin acts on one.

Pages:
    in: Any page
    out: page_dock
"""
import json
import os
from datetime import datetime

from module.dock.census import card_level, card_rarity, card_status
from module.dock.policy import Card, decide, summary, verdict_line
from module.inventory.inventory import CARD_LEFTS, OCR_DOCK_AMOUNT
from module.leveling.enhance import CENSUS_NAME_SHIFT, FleetEnhance
from module.leveling.fleet import CARD_NAME, area_button
from module.leveling.plan import RETROFIT, ShipMatcher, load_ship_data
from module.logger import logger
from module.ocr.ocr import Ocr
from module.retire.dock_walk import DockWalker
from module.ui.page import page_dock, page_main

# A name read this close to a ship is hers, below it the card is kept as not read
MATCH_SCORE = 0.8
# Level caps by limit breaks done: 70 with none, then 80, 90, 100. A ship above a cap has broken past it.
LEVEL_CAPS = (70, 80, 90)
# Copies a full limit break still asks for, by limit breaks done: 1 + 1 + 2
COPIES_LEFT = (4, 3, 2, 0)
CENSUS_DIR = './config/dock'


def breaks_at_least(level):
    """
    Args:
        level (int):

    Returns:
        int: Limit breaks a ship at that level has done at least
    """
    return sum(1 for cap in LEVEL_CAPS if level > cap)


def copies_needed(level):
    """
    The fewest breaks her level shows, so the most copies she may still need: a ship is never short of a copy kept.
    """
    return COPIES_LEFT[breaks_at_least(level)]


class DockCare(FleetEnhance):
    def dock_amount(self):
        """
        Returns:
            tuple[int, int]: Cards in the dock and its capacity, (0, 0) if not read

        Pages:
            in: page_dock
        """
        current, _, total = OCR_DOCK_AMOUNT.ocr(self.device.image)
        if not 0 < total <= 4030 or not 0 <= current <= total:
            return 0, 0
        return current, total

    def dock_census(self, matcher):
        """
        Returns:
            list[Card]: Every card of the dock, highest level first

        Pages:
            in: Any page
            out: page_dock
        """
        logger.hr('Dock census', level=2)
        self.ui_ensure(page_main)
        self.ui_ensure(page_dock)
        self.dock_favourite_set(False, wait_loading=False)
        # Highest level first: the policy tells a misread level by the order. The picker of enhancement sorts its
        # list the other way, and the dock keeps a sort until it is changed.
        self.dock_sort_method_dsc_set(True, wait_loading=False)
        self.dock_filter_set(sort='level', index='all', faction='all', rarity='all', extra='no_limit')
        retrofits = {name[:-len(RETROFIT)] for name in matcher.ships if name.endswith(RETROFIT)}
        cards = []
        phantoms = 0
        for card, fleet in DockWalker(self).cards():
            image = self.device.image
            x1, y1, x2, y2 = CARD_NAME
            left, top = CARD_LEFTS[card.column], card.top + CENSUS_NAME_SHIFT
            button = area_button((left + x1, top + y1, left + x2, top + y2), 'CENSUS_NAME')
            read = str(Ocr(button, lang='cnocr', letter=(255, 255, 255), threshold=128, name='CENSUS_NAME').ocr(image))
            data, score = matcher.match_score(read)
            ship = data.name if data is not None and score >= MATCH_SCORE else ''
            level = card_level(image, card.column, card.top)
            if not ship and not level:
                # 2026-10-22: the walk read a row of 7 that is no cards, "心<NN" and ">^" at level 0, between real
                # rows: 154 cards for a dock of 147. A card left out gets no verdict and nothing is done to it.
                phantoms += 1
                continue
            cards.append(Card(
                index=len(cards), read=read, ship=ship,
                rarity=matcher.ships[ship].rarity if ship else '',
                frame=card_rarity(image, card.column, card.top),
                level=level,
                locked=bool(card.locked), fleet=int(fleet),
                status=card_status(image, card.column, card.top),
                bulin='Bulin' in ship,
                retrofit=ship in retrofits,
            ))
        if phantoms:
            logger.info(f'{phantoms} cards read with no name nor level, left out')
        return cards

    def fleet_needs(self, cards):
        """
        Returns:
            dict[str, int]: Copies each ship of the fleets Purin levels may still need for her limit breaks
        """
        fleets = set(self.formation_fleets())
        needs = {}
        for card in cards:
            # A ship "Engaged" is in a fleet out on a map, the fleets that farm: her banner hides her FLEET badge
            if card.ship and (card.fleet in fleets or card.status == 'battle') and not card.bulin:
                needs[card.ship] = max(needs.get(card.ship, 0), copies_needed(card.level))
        return needs

    def save_census(self, cards, verdicts, amount):
        os.makedirs(CENSUS_DIR, exist_ok=True)
        path = os.path.join(CENSUS_DIR, f'{self.config.config_name}.json')
        data = {
            'time': datetime.now().isoformat(timespec='seconds'),
            'dock': {'current': amount[0], 'total': amount[1]},
            'summary': summary(verdicts),
            'cards': [dict(card.__dict__, action=verdict.action, reason=verdict.reason)
                      for card, verdict in zip(cards, verdicts)],
        }
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=1, ensure_ascii=False)
        logger.info(f'Dock census saved to {path}')

    def run(self):
        matcher = ShipMatcher(load_ship_data())
        self.ui_ensure(page_main)
        self.ui_ensure(page_dock)
        amount = self.dock_amount()
        logger.attr('Dock', f'{amount[0]}/{amount[1]}, {amount[1] - amount[0]} free')
        cards = self.dock_census(matcher)
        needs = self.fleet_needs(cards)
        logger.attr('Fleet_needs', needs)
        verdicts = decide(cards, needs, commission_pool=self.config.DockCare_CommissionPool)
        for verdict in verdicts:
            logger.info(verdict_line(verdict))
        counts = summary(verdicts)
        logger.info(f'Dock census: {len(cards)} cards of {amount[0]} counted, {counts}')
        self.save_census(cards, verdicts, amount)
        if self.config.DockCare_DryRun:
            logger.info('DockCare.DryRun is on: nothing retired or enhanced, the verdicts are for review')
        self.config.task_delay(success=True)
