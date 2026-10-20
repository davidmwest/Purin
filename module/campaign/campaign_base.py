from module.base.decorator import Config, cached_property
from module.campaign.campaign_ui import CampaignUI
from module.combat.assets import BATTLE_PREPARATION
from module.combat.auto_search_combat import AutoSearchCombat
from module.exception import CampaignEnd, MapEnemyMoved, ScriptError
from module.logger import logger
from module.map.assets import FLEET_PREPARATION, MAP_PREPARATION, MAP_PREPARATION_HARD
from module.map.map import Map
from module.map.map_base import CampaignMap
from module.ui.assets import BACK_ARROW
from module.ui.page import page_main


class CampaignBase(CampaignUI, Map, AutoSearchCombat):
    FUNCTION_NAME_BASE = 'battle_'
    MAP: CampaignMap
    # If a battle of the last run of the map was lost
    run_lost = False

    def combat(self, *args, **kwargs):
        """
        A battle lost ends the run of the map. The fleet may have sunk, which leaves the map without a fleet,
        and a stage too hard for the fleet is not worth going on with.

        Raises:
            CampaignEnd: After leaving the map, if the battle was lost.
        """
        super().combat(*args, **kwargs)
        if self.combat_lost:
            logger.warning('Battle lost, leave the map')
            self.run_lost = True
            self.withdraw()

    def handle_in_stage(self):
        """
        A commander level that unlocks a feature takes the game to the main page after a battle, to show it,
        with the stage still in progress. Like the list of stages, the main page ends the run, the next one
        withdraws from the stage and enters it again.

        A battle lost with ships left brings the fleet back to the map, beside the enemy. After a boss the task
        waits for the list of stages, which does not come, so the map is left from here.

        Raises:
            CampaignEnd: On the main page, on the list of stages, or after leaving the map once a battle is lost.
        """
        if self.ui_page_appear(page_main):
            logger.info('Left the map for page_main')
            raise CampaignEnd('Main page')
        if self.combat_lost and self.is_in_map():
            logger.warning('Battle lost, back on the map, leave it')
            # withdraw() looks for the list of stages through here too
            self.combat_lost = False
            self.run_lost = True
            self.withdraw()
        return super().handle_in_stage()

    def handle_combat_low_emotion(self):
        """
        The game warns that the mood of a ship is so low she loses affinity if she attacks.

        With emotion ignored, attack anyway. With emotion calculated, the calculation is off,
        the ships fought where Alas did not count it, in another task or by hand.
        Cancel, count the fleet as exhausted so the task waits for it to recover, and leave the map.

        A story plays the first time a stage is entered, and skipping it asks to confirm in a popup
        like this warning. That one is for the story to handle.

        Returns:
            bool: If handled, with emotion ignored.

        Raises:
            CampaignEnd: After leaving the map, with emotion calculated.
        """
        if self.story_popup_timeout.started() and not self.story_popup_timeout.reached():
            return False
        if self.emotion.is_ignore:
            return super().handle_combat_low_emotion()
        if not self.handle_popup_cancel('LOW_EMOTION'):
            return False

        fleet = getattr(self, 'fleet_current_index', 1)
        logger.warning(f'Mood of fleet {fleet} is lower than calculated')
        self.emotion.set_exhausted(fleet)
        self.emotion_withdraw()
        raise CampaignEnd('Emotion withdraw')

    def emotion_withdraw(self):
        """
        Leave the map, or the preparation of a battle or of the map.
        """
        logger.hr('Emotion withdraw')
        for _ in self.loop(skip_first=False):
            if self.handle_story_skip():
                continue
            if self.handle_popup_cancel('LOW_EMOTION'):
                continue
            if self.appear(BATTLE_PREPARATION, offset=(20, 20), interval=2):
                self.device.click(BACK_ARROW)
                continue
            if self.handle_auto_search_exit():
                continue
            if self.is_in_stage():
                break
            if self.is_in_map():
                self.withdraw()
                break
            if self.appear(FLEET_PREPARATION, offset=(20, 50), interval=2) \
                    or self.appear(MAP_PREPARATION, offset=(20, 20), interval=2) \
                    or self.appear(MAP_PREPARATION_HARD, offset=(20, 20), interval=2):
                self.enter_map_cancel()
                break

    def battle_default(self):
        if self.clear_enemy():
            return True

        logger.warning('No battle executed.')
        return False

    def battle_boss(self):
        if self.brute_clear_boss():
            return True

        logger.warning('No battle executed.')
        return False

    @Config.when(POOR_MAP_DATA=True, MAP_CLEAR_ALL_THIS_TIME=False)
    def battle_function(self):
        logger.info('Using function: battle_with_poor_map_data')
        if self.fleet_2_break_siren_caught():
            return True
        self.clear_all_mystery()

        if self.battle_count >= 3:
            self.pick_up_ammo()

        if self.map.select(is_boss=True):
            if self.brute_clear_boss():
                return True
        else:
            if self.clear_siren():
                return True
            return self.clear_enemy()

        return False

    @Config.when(MAP_CLEAR_ALL_THIS_TIME=True)
    def battle_function(self):
        logger.info('Using function: clear_all')
        if self.fleet_2_break_siren_caught():
            return True
        self.clear_all_mystery()

        if self.battle_count >= 3:
            self.pick_up_ammo()

        remain = self.map.select(is_enemy=True) \
            .add(self.map.select(is_siren=True)) \
            .add(self.map.select(is_fortress=True)) \
            .delete(self.map.select(is_boss=True))
        logger.info(f'Enemy remain: {remain}')
        if remain.count > 0:
            if self.config.MAP_HAS_MOVABLE_NORMAL_ENEMY:
                if self.clear_any_enemy(sort=('cost_2',)):
                    return True
                return self.battle_default()
            else:
                if self.clear_bouncing_enemy():
                    return True
                if self.clear_siren():
                    return True
                self.clear_mechanism()
                return self.battle_default()
        else:
            result = self.battle_boss()
            return result

    @Config.when(MAP_CLEAR_ALL_THIS_TIME=False, POOR_MAP_DATA=False)
    def battle_function(self):
        func = self.FUNCTION_NAME_BASE + 'default'
        for extra_battle in range(10):
            if hasattr(self, self.FUNCTION_NAME_BASE + str(self.battle_count - extra_battle)):
                func = self.FUNCTION_NAME_BASE + str(self.battle_count - extra_battle)
                break

        logger.info(f'Using function: {func}')
        func = self.__getattribute__(func)

        result = func()

        return result

    def execute_a_battle(self):
        logger.hr(f'{self.FUNCTION_NAME_BASE}{self.battle_count}', level=2)
        prev = self.battle_count
        result = False
        for _ in range(10):
            try:
                result = self.battle_function()
                break
            except MapEnemyMoved:
                if self.battle_count > prev:
                    result = True
                    break
                else:
                    continue

        if not result:
            logger.warning('ScriptError, No combat executed.')
            if self.config.Error_HandleError:
                logger.warning('ScriptError, No combat executed, Withdrawing')
                self.withdraw()
            else:
                raise ScriptError('No combat executed.')

        return result

    def run(self):
        logger.hr(self.ENTRANCE, level=2)
        self.run_lost = False
        # The battle lost in the run before, its map is not this one
        self.combat_lost = False

        # Enter map
        self.emotion.check_reduce(self._map_battle)
        self.ENTRANCE.area = self.ENTRANCE.button
        self.enter_map(self.ENTRANCE, mode=self.config.Campaign_Mode)

        # Map init
        if not self.map_is_auto_search:
            self.handle_map_fleet_lock()
            self.map_init(self.MAP)
        else:
            self.map = self.MAP
            self.battle_count = 0
            self.lv_reset()
            self.lv_get()

        # Run
        for _ in range(20):
            try:
                if not self.map_is_auto_search:
                    self.execute_a_battle()
                else:
                    self.auto_search_execute_a_battle()
            except CampaignEnd:
                logger.hr('Campaign end')
                return True

        # Exception
        logger.warning('Battle function exhausted.')
        if self.config.Error_HandleError:
            logger.warning('ScriptError, Battle function exhausted, Withdrawing')
            try:
                self.withdraw()
            except CampaignEnd:
                pass
        else:
            raise ScriptError('Battle function exhausted.')

    @cached_property
    def _map_battle(self):
        """
        Returns:
            int: Battle on this map.
        """
        for data in self.MAP.spawn_data:
            if 'boss' in data:
                if 'battle' in data:
                    return data['battle'] + 1
                else:
                    logger.warning('No battle count in spawn_data')

        logger.warning('No boss data found in spawn_data')
        return 0

    def auto_search_execute_a_battle(self):
        logger.hr(f'{self.FUNCTION_NAME_BASE}{self.battle_count}', level=2)
        self.auto_search_moving()
        self.auto_search_combat(fleet_index=self.fleet_show_index)
        self.battle_count += 1
