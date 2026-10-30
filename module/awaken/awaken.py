from module.awaken.assets import *
from module.base.timer import Timer
from module.exception import ScriptError
from module.leveling.plan import campaign_fleets
from module.logger import logger
from module.ocr.ocr import Digit
from module.retire.assets import DOCK_CHECK, SHIP_DETAIL_CHECK
from module.retire.dock import CARD_GRIDS, DOCK_EMPTY, Dock
from module.retire.dock_walk import DockWalker
from module.retire.scanner import ShipScanner
from module.ui.assets import BACK_ARROW
from module.ui.page import page_dock, page_main

# Guard against awakening the same ship over and over, should it stay in dock after an awakening failed
AWAKEN_SHIPS_PER_RUN = 60


class ShipLevel(Digit):
    def after_process(self, result):
        result = super().after_process(result)
        if result < 100 or result > 125:
            logger.warning('Unexpected ship level')
            result = 0
        return result


class Awaken(Dock):
    def _get_button_state(self, button: Button):
        """
        Args:
            button: COST_COIN or COST_CHIP or COST_ARRAY

        Returns:
            bool: True if having sufficient resource, False if not
                or None if such resource is not required
        """
        # If COST_ARRAY is absent, COST_COIN and COST_CHIP are right moved 54px
        if button.match(self.device.image, offset=(75, 20)):
            # Look down, see if there are red letters
            area = button.button
            area = (area[0], area[3], area[2], area[3] + 60)
            if self.image_color_count(area, color=(214, 53, 33), threshold=75, count=16):
                return False
            else:
                return True
        else:
            return None

    def _get_awaken_cost(self, use_array=False):
        """
        Args:
            use_array: True to awaken to 125, False to 120

        Returns:
            bool or str:
                True if all required resource is sufficient,
                False if any is insufficient,
                'unexpected_array' if not going to use array but array presents,
                'invalid' if result valid,
        """
        coin = self._get_button_state(COST_COIN)
        chip = self._get_button_state(COST_CHIP)
        array = self._get_button_state(COST_ARRAY)

        logger.attr('AwakenCost', {'coin': coin, 'chip': chip, 'array': array})

        def is_right_moved(button):
            # If COST_ARRAY is absent, COST_COIN and COST_CHIP are right moved 54px
            return button.button[0] - button.area[0] > 20

        # Check if result are valid
        if array is not None:
            if not use_array:
                logger.warning('Not going to use array but array presents')
                return 'unexpected_array'
            # If array is needed, coin and chip should present
            if coin is not None and not is_right_moved(COST_COIN) \
                    and chip is not None and not is_right_moved(COST_CHIP):
                result = coin and chip and array
                logger.attr('AwakenSufficient', result)
                return result
        else:
            # If array is not needed, coin and chip should both present and right moved
            if coin is not None and is_right_moved(COST_COIN) \
                    and chip is not None and is_right_moved(COST_CHIP):
                result = coin and chip
                logger.attr('AwakenSufficient', result)
                return result

        logger.warning('Invalid awaken cost')
        return 'invalid'

    def _get_chip_amount(self):
        """
        Read the numbers below the cognitive chip, like "2389/240".
        Call this after _get_awaken_cost(), which finds where the chip is.

        Returns:
            tuple[int, int]: Cognitive chips owned, cognitive chips this awakening costs.

        Pages:
            in: Awaken popup, with enough chips. What is owned is written in green then, in red otherwise.
        """
        x1, y1, x2, y2 = COST_CHIP.button
        area = (x1 - 14, y2 + 12, x2 + 14, y2 + 34)
        owned = Digit(area, letter=(165, 236, 75), threshold=128, name='AwakenChipOwned')
        cost = Digit(area, letter=(255, 255, 255), threshold=128, name='AwakenChipCost')
        return owned.ocr(self.device.image), cost.ocr(self.device.image)

    def is_chip_reserve_kept(self):
        """
        Returns:
            bool: If this awakening leaves at least Awaken_ChipReserve cognitive chips.
                False also if the numbers can not be read, chips are not spent on a guess.

        Pages:
            in: Awaken popup, with enough chips
        """
        reserve = self.config.Awaken_ChipReserve
        if reserve <= 0:
            return True

        owned, cost = self._get_chip_amount()
        if cost <= 0 or owned < cost:
            logger.warning(f'Unexpected cognitive chips: owned={owned}, cost={cost}')
            return False
        logger.attr('AwakenChip', f'{owned} - {cost} = {owned - cost}, reserve {reserve}')
        return owned - cost >= reserve

    def handle_awaken_finish(self):
        return self.appear_then_click(AWAKEN_FINISH, offset=(20, 20), interval=1)

    def is_in_awaken(self):
        return SHIP_LEVEL_CHECK.match_luma(self.device.image, similarity=0.7)

    def awaken_popup_close(self, skip_first_screenshot=True):
        logger.info('Awaken popup close')
        self.interval_clear(AWAKEN_CANCEL)
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if self.is_in_awaken():
                break
            if self.appear_then_click(AWAKEN_CANCEL, offset=(20, 20), interval=3):
                continue
            if self.handle_awaken_finish():
                continue

    def awaken_once(self, use_array=False, skip_first_screenshot=True):
        """
        Args:
            use_array:
            skip_first_screenshot:

        Returns:
            str: Result state, 'no_exp', 'unexpected_array', 'insufficient', 'reserve', 'timeout', 'success'

        Pages:
            in: is_in_awaken
            out: is_in_awaken
        """
        logger.hr('Awaken once', level=2)
        interval = Timer(3, count=6)
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if self.appear(AWAKEN_CONFIRM):
                break
            if LEVEL_UP.match_luma(self.device.image):
                logger.info(f'awaken_once ended at {LEVEL_UP}')
                return 'no_exp'
            # Lower similarity due to random background
            if interval.reached() and AWAKENING.match_luma(self.device.image, similarity=0.7):
                self.device.click(AWAKENING)
                interval.reset()
                continue

        logger.info('Get awaken cost')
        timeout = Timer(2, count=6).start()
        skip_first_screenshot = True
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            result = self._get_awaken_cost(use_array)
            if result == 'unexpected_array':
                # This shouldn't happen
                self.awaken_popup_close()
                return result
            elif result is False:
                logger.info('Insufficient resources to awaken')
                self.awaken_popup_close()
                return 'insufficient'
            elif result is True:
                # Sufficient resources
                break
            elif result == 'invalid':
                # Retry, and check timeout also
                pass
            else:
                raise ScriptError(f'Unexpected _get_awaken_cost result: {result}')
            if timeout.reached():
                logger.warning('Get awaken cost timeout')
                self.awaken_popup_close()
                return 'timeout'

        # sufficient is True
        if not self.is_chip_reserve_kept():
            logger.info('Awakening would leave less cognitive chips than the reserve')
            self.awaken_popup_close()
            return 'reserve'

        logger.info('Awaken confirm')
        self.interval_clear(AWAKEN_CONFIRM)
        # Awaken popup takes 10s to appear if you have enough EXP to reach next awaken limit
        # and 2s to dismiss it by clicking
        # Timeout here is very long
        timeout = Timer(30, count=30).start()
        finished = False
        skip_first_screenshot = True
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # End
            if timeout.reached():
                logger.warning('Awaken confirm timeout')
                self.awaken_popup_close()
                break
            if finished and self.is_in_awaken():
                logger.info('Awaken finished')
                break
            # Click
            if self.appear_then_click(AWAKEN_CONFIRM, offset=(20, 20), interval=3):
                continue
            if self.handle_popup_confirm('AWAKEN'):
                continue
            if self.handle_awaken_finish():
                finished = True
                continue

        self.device.click_record_clear()
        return 'success'

    def get_ship_level(self, skip_first_screenshot=True):
        """
        Args:
            skip_first_screenshot:

        Returns:
            int: 100~125, or 0 if error
        """
        ocr = ShipLevel(OCR_SHIP_LEVEL, letter=(255, 255, 255), threshold=128, name='ShipLevel')
        timeout = Timer(2, count=4).start()
        level = 0
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if self.is_in_awaken():
                level = ocr.ocr(self.device.image)
                if level > 0:
                    return level
            if timeout.reached():
                logger.warning('get_ship_level timeout')
                return level

    def awaken_ship(self, use_array=False, skip_first_screenshot=True):
        """
        Awaken one ship til EXP not enough or reached stop level

        Args:
            use_array: True to awaken to level 125, False to 120
            skip_first_screenshot:

        Returns:
            str: 'level_max', 'insufficient', 'reserve', 'no_exp', 'timeout'

        Pages:
            in: is_in_awaken
            out: is_in_awaken
        """
        logger.hr('Awaken ship', level=1)
        logger.info(f'Awaken ship, use_array={use_array}')

        if use_array:
            stop_level = 125
        else:
            stop_level = 120

        if not skip_first_screenshot:
            self.device.screenshot()

        for _ in range(7):
            level = self.get_ship_level()
            if level > 0:
                if level >= stop_level:
                    logger.info(f'Awaken ship ended at stop_level')
                    return 'level_max'
                else:
                    result = self.awaken_once(use_array)
                    # 'no_exp', 'unexpected_array', 'insufficient', 'timeout', 'success'
                    if result == 'success':
                        continue
                    if result in ['insufficient', 'reserve', 'no_exp']:
                        # Return as it is
                        return result
                    if result == 'unexpected_array':
                        # Maybe just accidentally entered awaken confirm
                        # Re-run awaken_once should recheck it
                        continue
                    if result == 'timeout':
                        # Timeout getting resources, retry should fix it
                        continue
                    raise ScriptError(f'Unexpected awaken_once result: {result}')
            else:
                # Get level timeout, request exit
                return 'timeout'

        # Error, request exit
        logger.warning('Too many awaken trial on one ship')
        return 'timeout'

    def awaken_exit(self, skip_first_screenshot=True):
        """
        Pages:
            in: is_in_awaken
            out: DOCK_CHECK
        """
        logger.info('Awaken exit')
        interval = Timer(3)
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if self.ui_page_appear(page_dock):
                logger.info(f'Awaken exit at {page_dock}')
                break
            if interval.reached() and self.is_in_awaken():
                logger.info(f'is_in_awaken -> {BACK_ARROW}')
                self.device.click(BACK_ARROW)
                interval.reset()
                continue
            if self.handle_awaken_finish():
                continue
            if self.appear_then_click(AWAKEN_CANCEL, offset=(20, 20), interval=3):
                continue
            if self.is_in_main(interval=5):
                self.device.click(page_main.links[page_dock])
                continue

    def awaken_fleets(self):
        """
        Returns:
            list[int]: Fleets whose ships are awakened with Awaken_OnlyFleets: both fleets of Main, the one that
                fights the mobs and the one that fights the boss, and its submarine fleet. None for any ship of the dock.
        """
        if not self.config.Awaken_OnlyFleets:
            return None
        # The fleets that farm hold ships under 120 since 2026-10-21, the boss fleet too, its ships from level 100.
        # One at a cap of 100, 105, 110 or 115 levels on only once awakened: the boss fleet was left out and its
        # capped ships stayed there (code review).
        mob, boss = campaign_fleets(
            self.config.cross_get('Main.Fleet.Fleet1', default=1),
            self.config.cross_get('Main.Fleet.Fleet2', default=0),
            self.config.cross_get('Main.Fleet.FleetOrder', default='fleet1_mob_fleet2_boss'),
        )
        # Cards of the dock show a submarine fleet like the common fleet of its number
        fleets = mob + boss + [self.config.cross_get('Main.Submarine.Fleet', default=0)]
        return sorted({fleet for fleet in fleets if fleet})

    def awaken_run(self, use_array=False, favourite=False, rarity=None):
        """
        Awaken all ships in dock until resources exhausted

        Args:
            use_array: True to awaken to level 125, False to 120
            favourite: True to awaken favourite ships only, False to awaken all ships
            rarity (str): Rarity of the dock filter, Awaken_Rarity if None

        Returns:
            str: 'insufficient', 'finish', 'timeout'

        Pages:
            in: Any
            out: page_dock
        """
        logger.hr('Awaken run', level=1)
        self.ui_ensure(page_dock)
        self.dock_favourite_set(enable=favourite, wait_loading=False)
        # Sorted by level, so this is also from the most expensive awakening to the cheapest, or the other way
        self.dock_sort_method_dsc_set(self.config.Awaken_Order == 'highest_level_first', wait_loading=False)
        if use_array:
            extra = ['can_awaken_plus']
        else:
            extra = ['can_awaken']
        self.dock_filter_set(
            index=self.config.Awaken_Index,
            faction=self.config.Awaken_Faction,
            rarity=rarity if rarity is not None else self.config.Awaken_Rarity,
            extra=extra,
        )
        fleets = self.awaken_fleets()
        if fleets is not None:
            logger.info(f'Awaken ships of fleets {fleets} only')

        # Ships passed over to keep the chip reserve. They stay in dock, in front of the ships not tried yet.
        skipped = 0
        result = 'finish'
        for _ in range(AWAKEN_SHIPS_PER_RUN):
            # page_dock
            if self.appear(DOCK_EMPTY, offset=(20, 20)):
                logger.info('awaken_run finished, no ships to awaken')
                result = 'finish'
                break

            # page_dock -> SHIP_DETAIL_CHECK
            if fleets is None:
                entered = self.awaken_enter_ship(skipped)
            else:
                entered = self.awaken_enter_fleet_ship(fleets, skipped)
            if not entered:
                logger.info(f'awaken_run finished, no more ships to awaken, {skipped} left for the chip reserve')
                result = 'finish'
                break

            # is_in_awaken
            result = self.awaken_ship(use_array)
            self.awaken_exit()
            # 'insufficient', 'reserve', 'no_exp', 'timeout'
            if result in ['no_exp', 'level_max']:
                # Awaken next ship
                continue
            if result == 'reserve':
                # Another ship may be cheaper
                skipped += 1
                continue
            if result == 'insufficient':
                logger.info('awaken_run finished, resources exhausted')
                break
            if result == 'timeout':
                logger.info(f'awaken_run finished, result={result}')
                break
            raise ScriptError(f'Unexpected awaken_ship result: {result}')
        else:
            logger.warning('awaken_run finished, too many ships in one run')

        return result

    def awaken_enter_ship(self, index, skip_first_screenshot=True):
        """
        Enter a ship in dock, counted from the first one.

        Args:
            index (int): 0 for the first ship
            skip_first_screenshot:

        Returns:
            bool: True if success to enter
                False if there is no ship at that place, the end of the dock

        Pages:
            in: page_dock
            out: SHIP_DETAIL_CHECK
        """
        self.awaken_walk_back()
        if index == 0:
            return self.dock_enter_first()

        columns, rows = CARD_GRIDS.grid_shape
        if index >= columns * rows:
            # Ships passed over for the chip reserve stay in front, a cheaper one can be further down
            return self.awaken_walk_enter(index)

        logger.info(f'Dock enter ship {index}')
        button = CARD_GRIDS[(index % columns, index // columns)]
        return self.awaken_click_card(button, index, skip_first_screenshot=skip_first_screenshot)

    def awaken_click_card(self, button, index, skip_first_screenshot=True):
        """
        Args:
            button (Button): A card of the dock
            index (int): Of the ship, for the log

        Returns:
            bool: True if success to enter

        Pages:
            in: page_dock
            out: SHIP_DETAIL_CHECK
        """
        self.interval_clear(DOCK_CHECK, interval=5)
        clicked = 0
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # End
            if self.appear(SHIP_DETAIL_CHECK, offset=(20, 20)):
                return True

            # Click
            if self.appear(DOCK_CHECK, offset=(20, 20), interval=5):
                if clicked >= 2:
                    logger.info(f'No ship {index} in dock')
                    return False
                self.device.click(button)
                clicked += 1

    def awaken_walk_back(self):
        """
        The cards of CARD_GRIDS are those of the first screen: back to it after a walk down the dock.
        """
        if getattr(self, 'dock_walked', False):
            DockWalker(self).list_top()
            self.dock_walked = False

    def awaken_walk_enter(self, index, fleets=None):
        """
        Enter a ship past the first screen of the dock, counted from its first card.

        Args:
            index (int): 0 for the first ship, of the fleets if any
            fleets (list[int]): Fleets whose ships are counted, None for any ship

        Returns:
            bool: True if success to enter, False at the end of the dock

        Pages:
            in: page_dock
            out: SHIP_DETAIL_CHECK
        """
        of = f' of fleets {fleets}' if fleets is not None else ''
        count = 0
        for card, fleet in DockWalker(self).cards():
            self.dock_walked = True
            if fleets is not None and fleet not in fleets:
                continue
            if count < index:
                count += 1
                continue
            logger.info(f'Dock enter ship {index}{of}, row of the card at y={card.top}')
            return self.awaken_click_card(card.button, index, skip_first_screenshot=False)
        logger.info(f'No ship {index}{of} in dock, {count} seen')
        return False

    def awaken_enter_fleet_ship(self, fleets, index, skip_first_screenshot=True):
        """
        Enter a ship of the fleets, counted from the first one of them in the dock.

        Args:
            fleets (list[int]):
            index (int): 0 for the first ship of the fleets
            skip_first_screenshot:

        Returns:
            bool: True if success to enter
                False if there is no such ship in the dock

        Pages:
            in: page_dock
            out: SHIP_DETAIL_CHECK
        """
        if getattr(self, 'dock_walked', False):
            self.awaken_walk_back()
            skip_first_screenshot = False
        if not skip_first_screenshot:
            self.device.screenshot()
        scanner = ShipScanner(fleet=fleets, status='any')
        scanner.disable('emotion', 'rarity')
        ships = scanner.scan(self.device.image, output=False)
        if index >= len(ships):
            # The ships of the fleets may be further down, past ships of other fleets that can awaken too
            return self.awaken_walk_enter(index, fleets=fleets)

        button = ships[index].button
        logger.info(f'Dock enter a ship of fleet {ships[index].fleet} at level {ships[index].level}')
        self.interval_clear(DOCK_CHECK, interval=5)
        clicked = 0
        for _ in self.loop():
            if self.appear(SHIP_DETAIL_CHECK, offset=(20, 20)):
                return True
            if self.appear(DOCK_CHECK, offset=(20, 20), interval=5):
                if clicked >= 2:
                    logger.info('The ship of the fleets did not open')
                    return False
                self.device.click(button)
                clicked += 1

    def run(self):
        # Run Awakening+ first
        favourite = self.config.Awaken_Favourite
        if self.config.Awaken_LevelCap == 'level125':
            # Use Cognitive Arrays
            result = self.awaken_run(use_array=True, favourite=favourite)
            # Use Cognitive Chips
            if result != 'timeout':
                self.awaken_run(favourite=favourite)
        elif self.config.Awaken_LevelCap == 'ultra_level125':
            # Cognitive Arrays for ultra rares only, the other ships stop at 120
            result = self.awaken_run(use_array=True, favourite=favourite, rarity='ultra')
            if result != 'timeout':
                self.awaken_run(favourite=favourite)
        elif self.config.Awaken_LevelCap == 'level120':
            # Use Cognitive Chips
            self.awaken_run(favourite=favourite)
        else:
            raise ScriptError(f'Unknown Awaken_LevelCap={self.config.Awaken_LevelCap}')

        # Reset dock filters
        logger.hr('Awaken run exit', level=1)
        if favourite:
            self.dock_favourite_set(wait_loading=False)
        self.dock_filter_set(wait_loading=False)

        # Scheduler
        interval = self.config.Awaken_CheckInterval
        if interval > 0:
            # Ships reach their level cap while farming, not once a day
            self.config.task_delay(minute=interval, server_update=True)
        else:
            self.config.task_delay(server_update=True)
