import re

from module.base.timer import Timer
from module.base.utils import color_bar_percentage
from module.campaign.stage import stage_names
from module.campaign.star_pass import clear_star_runs, star_stage_key
from module.handler.assets import *
from module.handler.auto_search import AutoSearchHandler
from module.logger import logger
from module.ui.switch import Switch

FLEET_LOCK = Switch('Fleet_Lock', offset=(5, 20))
FLEET_LOCK.add_state('on', check_button=FLEET_LOCKED)
FLEET_LOCK.add_state('off', check_button=FLEET_UNLOCKED)


class SwitchClearMode(Switch):
    def get(self, main):
        title = main.appear(CLEAR_MODE_TITLE, offset=(20, 20))
        if not title:
            return 'unknown'
        # find check area to the right of title
        CLEAR_MODE_CHECK.load_offset(CLEAR_MODE_TITLE)
        # cyan letter is `on`
        if main.image_color_count(CLEAR_MODE_CHECK.button, color=(130, 229, 255), threshold=30, count=50):
            return 'on'
        # white button is `off`
        if main.image_color_count(CLEAR_MODE_CHECK.button, color=(255, 255, 255), threshold=30, count=200):
            return 'off'
        return 'unknown'


CLEAR_MODE = SwitchClearMode('Clear_Mode')
CLEAR_MODE.add_state('on', check_button=CLEAR_MODE_TITLE, click_button=CLEAR_MODE_CHECK)
CLEAR_MODE.add_state('off', check_button=CLEAR_MODE_TITLE, click_button=CLEAR_MODE_CHECK)


class SwitchAutoSearch(Switch):
    def get(self, main):
        title = None
        if main.appear(AUTO_SEARCH_TITLE, offset=(20, 20)):
            title = AUTO_SEARCH_TITLE
        # [JP] has different character spacing in hard mode and normal mode
        if not title:
            if main.appear(AUTO_SEARCH_TITLE2, offset=(20, 20)):
                title = AUTO_SEARCH_TITLE2
        if not title:
            if main.appear(AUTO_SEARCH_TITLE3, offset=(20, 20)):
                title = AUTO_SEARCH_TITLE3
        if not title:
            return 'unknown'
        # find check area to the right of title
        AUTO_SEARCH_CHECK.load_offset(title)
        # green square is `on`
        if main.image_color_count(AUTO_SEARCH_CHECK.button, color=(158, 234, 94), threshold=30, count=50):
            return 'on'
        # no way to detect `off`
        # return `off` if title appears and it's not `on`
        return 'off'


AUTO_SEARCH = SwitchAutoSearch('Auto_Search')
AUTO_SEARCH.add_state('on', check_button=AUTO_SEARCH_TITLE, click_button=AUTO_SEARCH_CHECK)
AUTO_SEARCH.add_state('off', check_button=AUTO_SEARCH_TITLE, click_button=AUTO_SEARCH_CHECK)


def to_map_input_name(name: str) -> str:
    """
    Convert to user input names.

    7-2 -> 7-2
    campaign_7_2 -> 7-2
    d3 -> D3
    """
    # Remove whitespaces
    name = re.sub('[ \t\n]', '', name).lower()
    # B-1 -> B1
    res = re.match(r'([a-zA-Z])+[- ]+(\d+)', name)
    if res:
        name = f'{res.group(1)}{res.group(2)}'
    # Change back to upper case for campaign removal
    name = str(name).upper()
    # campaign_7_2 -> 7-2
    name = name.replace('CAMPAIGN_', '').replace('_', '-')
    return name


def to_map_file_name(name: str) -> str:
    """
    Convert to the name of map files.

    7-2 -> campaign_7_2
    campaign_7_2 -> campaign_7_2
    D3 -> d3
    """
    name = str(name).lower()
    # Remove whitespaces
    name = re.sub('[ \t\n]', '', name).lower()
    # B-1 -> B1
    res = re.match(r'([a-zA-Z])+[- ]+(\d+)', name)
    if res:
        name = f'{res.group(1)}{res.group(2)}'
    # 7-2 to campaign_7_2
    if name and name[0].isdigit():
        name = 'campaign_' + name.replace('-', '_')
    return name


class FastForwardHandler(AutoSearchHandler):
    map_clear_percentage = 0.
    map_achieved_star_1 = False
    map_achieved_star_2 = False
    map_achieved_star_3 = False
    map_is_100_percent_clear = False
    map_is_3_stars = False
    map_is_threat_safe = False
    map_has_clear_mode = False
    map_is_clear_mode = False  # Clear mode == fast forward
    map_is_auto_search = False
    map_is_2x_book = False

    STAGE_INCREASE = [
        """
        1-1 > 1-2 > 1-3 > 1-4
        > 2-1 > 2-2 > 2-3 > 2-4
        > 3-1 > 3-2 > 3-3 > 3-4
        > 4-1 > 4-2 > 4-3 > 4-4
        > 5-1 > 5-2 > 5-3 > 5-4
        > 6-1 > 6-2 > 6-3 > 6-4
        > 7-1 > 7-2 > 7-3 > 7-4
        > 8-1 > 8-2 > 8-3 > 8-4
        > 9-1 > 9-2 > 9-3 > 9-4
        > 10-1 > 10-2 > 10-3 > 10-4
        > 11-1 > 11-2 > 11-3 > 11-4
        > 12-1 > 12-2 > 12-3 > 12-4
        > 13-1 > 13-2 > 13-3 > 13-4
        > 14-1 > 14-2 > 14-3 > 14-4
        > 15-1 > 15-2 > 15-3 > 15-4
        > 16-1 > 16-2 > 16-3 > 16-4
        """,
        'A1 > A2 > A3',
        'B1 > B2 > B3',
        'C1 > C2 > C3',
        'D1 > D2 > D3',
        'SP1 > SP2 > SP3 > SP4 > SP5',
        'T1 > T2 > T3 > T4 > T5 > T6',
        'HT1 > HT2 > HT3 > HT4 > HT5 > HT6',
    ]
    map_fleet_checked = False

    def map_get_info(self):
        """
        Logs:
            | INFO | [Map_info] 98%, star_1, star_2, star_3, clear, 3_star, green, fast_forward
        """
        self.map_clear_percentage = self.get_map_clear_percentage()
        self.map_achieved_star_1, self.map_achieved_star_2, self.map_achieved_star_3 = self.map_stars_settled()
        self.map_is_100_percent_clear = self.map_clear_percentage > 0.95
        self.map_is_3_stars = self.map_achieved_star_1 and self.map_achieved_star_2 and self.map_achieved_star_3
        self.map_is_threat_safe = self.appear(MAP_GREEN, offset=(20, 20))
        if self.config.Campaign_Name.lower() == 'sp':
            # Minor issue here
            # Using auto_search option because clear mode cannot be detected whether on SP
            # If user manually turn off auto search, alas can't enable it again
            self.map_has_clear_mode = AUTO_SEARCH.appear(main=self)
        else:
            self.map_has_clear_mode = self.map_is_100_percent_clear and CLEAR_MODE.appear(main=self)

        # Override config
        if self.map_achieved_star_1:
            # Story before boss spawn, Attribute "story_refresh_boss" in chapter_template.lua
            self.config.MAP_HAS_MAP_STORY = False
        # STAR_REQUIRE_3 is the star of clearing every enemy. STAR_REQUIRE_2, defeating a number of escort fleets,
        # counts them over every run of the stage: a run that clears them all is the fewest runs to it. 1-1 of the
        # F2P account, 2026-10-22, had stars 1 and 3 and never got 2 from runs that went for the boss.
        self.config.MAP_CLEAR_ALL_THIS_TIME = bool(self.map_stars_missing()) \
            and (self.config.StopCondition_MapAchievement in ['map_3_stars', 'threat_safe'])

        self.map_show_info()

    def map_stars_missing(self):
        """
        Returns:
            list[int]: Stars of escort fleets and of a full clear the stage does not have yet
        """
        stars = [self.config.STAR_REQUIRE_2, self.config.STAR_REQUIRE_3]
        return [star for star in stars if star and not self.__getattribute__(f'map_achieved_star_{star}')]

    def map_show_info(self):
        # Log
        logger.attr('MAP_CLEAR_ALL_THIS_TIME', self.config.MAP_CLEAR_ALL_THIS_TIME)
        names = ['map_achieved_star_1', 'map_achieved_star_2', 'map_achieved_star_3',
                 'map_is_100_percent_clear', 'map_is_3_stars',
                 'map_is_threat_safe', 'map_has_clear_mode']
        strip = ['map', 'achieved', 'is', 'has']
        log_names = ['_'.join([x for x in name.split('_') if x not in strip]) for name in names]
        text = ', '.join([l for l, n in zip(log_names, names) if self.__getattribute__(n)])
        text = f'{int(self.map_clear_percentage * 100)}%, ' + text
        logger.attr('Map_info', text)
        logger.attr('StopCondition_MapAchievement', self.config.StopCondition_MapAchievement)

    def handle_fast_forward(self):
        if not self.map_has_clear_mode:
            self.map_is_clear_mode = False
            self.map_is_auto_search = False
            self.map_is_2x_book = False
            return False

        use_clear_mode = self.config.Campaign_UseClearMode
        if use_clear_mode and self.config.MAP_CLEAR_ALL_THIS_TIME:
            # In clear mode the game may search by itself: on 1-1 of the F2P account, 2026-10-22, a battle started
            # as the map opened, Purin found no map and the task ended. Its auto search switch is a template that
            # scores too low at 2560x1440, so Purin cannot tell it is on. A pass for stars plays the map itself.
            logger.info('Stars to get by clearing the map, clear mode off for this run')
            use_clear_mode = False

        if use_clear_mode:
            self.config.MAP_HAS_AMBUSH = False
            self.config.MAP_HAS_FLEET_STEP = False
            self.config.MAP_HAS_MOVABLE_ENEMY = False
            self.config.MAP_HAS_MOVABLE_NORMAL_ENEMY = False
            self.config.MAP_HAS_PORTAL = False
            self.config.MAP_HAS_LAND_BASED = False
            self.config.MAP_HAS_MAZE = False
            self.config.MAP_HAS_FORTRESS = False
            self.config.MAP_HAS_BOUNCING_ENEMY = False
            self.config.MAP_HAS_DECOY_ENEMY = False
            self.map_is_clear_mode = True
            self.map_is_auto_search = self.config.Campaign_UseAutoSearch
            self.map_is_2x_book = self.config.Campaign_Use2xBook
        else:
            # When disable fast forward, MAP_HAS_AMBUSH depends on map settings.
            # self.config.MAP_HAS_AMBUSH = True
            self.map_is_clear_mode = False
            self.map_is_auto_search = False
            self.map_is_2x_book = False
            pass

        state = 'on' if use_clear_mode else 'off'
        changed = CLEAR_MODE.set(state, main=self)
        if changed:
            self.map_wait_auto_search()
        return changed

    def _is_map_star_active(self, button):
        return self.image_color_count(button, color=(250, 232, 140), threshold=75, count=35)

    def map_read_stars(self):
        """
        Returns:
            tuple[bool, bool, bool]: Stars 1 to 3 lit on the screenshot
        """
        return tuple(bool(self._is_map_star_active(star)) for star in (MAP_STAR_1, MAP_STAR_2, MAP_STAR_3))

    def map_stars_settled(self):
        """
        The stars light up as the map information opens. 1-4 of the F2P account read star 3 alone, 20 ms after the
        clear percentage reached 99% (2026-10-22): a pass for stars would replay a stage that has them, or leave one
        that does not. They are read again until two screenshots in a row agree, a second at most.

        Returns:
            tuple[bool, bool, bool]: Stars 1 to 3

        Pages:
            in: MAP_PREPARATION
        """
        stars = self.map_read_stars()
        timeout = Timer(1, count=3).start()
        while 1:
            self.device.screenshot()
            again = self.map_read_stars()
            if again == stars:
                return stars
            logger.info(f'Stars read {stars}, then {again}')
            stars = again
            if timeout.reached():
                return stars

    def handle_map_fleet_lock(self, enable=None):
        """
        Args:
            enable (bool): Default to None, use Campaign_UseFleetLock.

        Returns:
            bool: If switched.
        """
        # Fleet lock depends on if it appear on map, not depends on map status.
        # Because if already in map, there's no map status,
        if not FLEET_LOCK.appear(main=self):
            logger.info('No fleet lock option.')
            return False

        if enable is None:
            enable = self.config.Campaign_UseFleetLock
        state = 'on' if enable else 'off'
        changed = FLEET_LOCK.set(state, main=self)

        return changed

    def map_wait_auto_search(self):
        """
        When enabling clear mode (FAST_FORWARD), AUTO_SEARCH has an animation to appear
        wait until it fully appeared

        Returns:
            bool: If waited
        """
        timeout = Timer(1, count=3).start()
        for _ in self.loop():
            state = AUTO_SEARCH.get(main=self)
            logger.attr('AUTO_SEARCH', state)
            if state != 'unknown':
                return True
            if timeout.reached():
                # some maps may have clear mode but don't have auto search
                logger.info('map wait auto search timeout')
                return False

    def handle_auto_search(self):
        """
        Returns:
            bool: If changed

        Pages:
            in: MAP_PREPARATION
        """
        # if not self.map_is_clear_mode:
        #     return False

        if not AUTO_SEARCH.appear(main=self):
            logger.info('No auto search option.')
            self.map_is_auto_search = False
            return False

        state = 'on' if self.map_is_auto_search else 'off'
        changed = AUTO_SEARCH.set(state, main=self)

        return changed

    def handle_auto_search_setting(self):
        """
        Returns:
            bool: If changed

        Pages:
            in: FLEET_PREPARATION
        """
        if not self.map_is_auto_search:
            return False

        logger.info('Auto search setting')
        self.fleet_preparation_sidebar_ensure(3)
        self.auto_search_setting_ensure(self.config.Fleet_FleetOrder)
        if self.config.SUBMARINE:
            self.auto_search_setting_ensure(self.config.Submarine_AutoSearchMode)
        return True

    @property
    def is_call_submarine_at_boss(self):
        return self.config.SUBMARINE and self.config.Submarine_Mode in ['boss_only', 'hunt_and_boss']

    def handle_auto_submarine_call_disable(self):
        """
        Returns:
            bool: If changed

        Pages:
            in: FLEET_PREPARATION
        """
        if self.map_fleet_checked:
            return False
        if not self.is_call_submarine_at_boss:
            return False
        # 2025.09.22, correct that fleet role settings is unlocked after clear mode
        if not self.map_is_clear_mode:
            logger.warning('Can not set submarine call because auto search not available, assuming disabled')
            logger.warning('Please do the followings: '
                           'goto any stage -> auto search role -> set submarine role to standby')
            logger.warning('If you already did, ignore this warning')
            return False

        logger.info('Disable auto submarine call')
        self.fleet_preparation_sidebar_ensure(3)
        self.auto_search_setting_ensure('sub_standby')
        return True

    def handle_auto_search_continue(self):
        """
        Override AutoSearchHandler definition
        for 2x book handling if needed
        """
        if self.appear(AUTO_SEARCH_MENU_CONTINUE, offset=self._auto_search_menu_offset, interval=2):
            self.map_is_2x_book = self.config.Campaign_Use2xBook
            self.handle_2x_book_setting(mode='auto')
            if self.appear_then_click(AUTO_SEARCH_MENU_CONTINUE, offset=self._auto_search_menu_offset):
                self.interval_reset(AUTO_SEARCH_MENU_CONTINUE)
            else:
                # AUTO_SEARCH_MENU_CONTINUE disappeared after handle_2x_book_setting()
                pass
            return True
        return False

    def get_map_clear_percentage(self):
        """
        Returns:
            float: 0 to 1.

        Pages:
            in: MAP_PREPARATION
        """
        percent = color_bar_percentage(self.device.image, area=MAP_CLEAR_PERCENTAGE.area, prev_color=(231, 170, 82))
        if self.config.MAP_CLEAR_PERCENTAGE_SHORT:
            percent *= 1.4
        return percent

    def campaign_name_increase(self, name):
        """
        Increase name to its next stage.

        Args:
            name (str): Such as `6-1`, `a1`, `campaign_6_1`

        Returns:
            str: Name of next stage in upper case,
                or origin name if unable to increase.
        """
        # Copy STAGE_INCREASE to avoid potential duplicate inserting
        stage_increase = [r for r in self.STAGE_INCREASE]
        # Insert custom increase logic
        if self.config.STAGE_INCREASE_AB:
            # Hard mode has the same two parts, C for A and D for B
            stage_increase = [
                'A1 > A2 > A3 > B1 > B2 > B3',
                'C1 > C2 > C3 > D1 > D2 > D3',
            ] + stage_increase
        custom = self.config.STAGE_INCREASE_CUSTOM
        if custom:
            if isinstance(custom, str):
                custom = [custom]
            stage_increase = custom + stage_increase

        # Increase stage
        name = to_map_input_name(name)
        for increase in stage_increase:
            increase = [i.strip(' \t\r\n') for i in increase.split('>')]
            if name in increase:
                index = increase.index(name) + 1
                if index < len(increase):
                    new = increase[index]
                    # Don't check main stages, assume all exist
                    # Main stages are named like campaign_7_2, but user inputs 7-2
                    if self.config.Campaign_Event == 'campaign_main':
                        return new
                    # Check if the stage exists
                    existing = stage_names(self.config.Campaign_Event)
                    logger.info(f'Existing stages: {existing}')
                    if new.lower() in existing:
                        return new
                    else:
                        logger.info(f'Stage increase reach end, new map {new} does not exist')
                        return name
                else:
                    logger.info('Stage increase reach end')
                    return name

        return name

    def campaign_name_decrease(self, name):
        """
        Decrease name to its previous stage.

        Args:
            name (str): Such as `6-1`, `a1`, `campaign_6_1`

        Returns:
            str: Name of the previous stage in upper case, or origin name if it is the first one.
        """
        name = to_map_input_name(name)
        for increase in self.STAGE_INCREASE:
            increase = [i.strip(' \t\r\n') for i in increase.split('>')]
            if name in increase:
                index = increase.index(name)
                if index > 0:
                    return increase[index - 1]
                return name
        return name

    def triggered_map_stop(self):
        """
        Returns:
            bool:
        """
        # Runs of the stage before the one lost, to level the fleet
        if self.config.StopCondition_RetreatRemain > 0:
            return False

        if self.config.StopCondition_MapAchievement == '100_percent_clear':
            if self.map_is_100_percent_clear:
                return True

        if self.config.StopCondition_MapAchievement == 'map_3_stars':
            if self.map_is_100_percent_clear and self.map_is_3_stars:
                return True

        if self.config.StopCondition_MapAchievement == 'threat_safe_without_3_stars':
            if self.map_is_100_percent_clear and self.map_is_threat_safe:
                return True

        if self.config.StopCondition_MapAchievement == 'threat_safe':
            if self.map_is_100_percent_clear and self.map_is_3_stars and self.map_is_threat_safe:
                return True

        return False

    def handle_map_stop(self):
        """
        Modify configs after reaching a stop condition.
        Disable current task or increase stage.
        """
        self.star_runs_clear()
        if self.config.StopCondition_StageIncrease:
            prev_stage = to_map_input_name(self.config.Campaign_Name)
            next_stage = self.campaign_name_increase(prev_stage)
            if next_stage != prev_stage:
                logger.info(f'Stage {prev_stage} increases to {next_stage}')
                self.config.Campaign_Name = next_stage
            elif self.campaign_name_last(prev_stage) and not self.config.MAP_IS_ONE_TIME_STAGE:
                # The user's rule (2026-10-22): oil goes into the hardest stage the fleet clears, so the last stage
                # of an event is farmed on
                logger.info(f'Stage {prev_stage} is the last stage, farm it on')
                self.config.StopCondition_MapAchievement = 'non_stop'
            else:
                logger.info(f'Stage {prev_stage} cannot increase, stop at current stage')
                self.config.Scheduler_Enable = False
                self.call_then_task()
        else:
            self.config.Scheduler_Enable = False
            self.call_then_task()

    def star_runs_clear(self):
        """
        The stage is left: the runs a pass for 3 stars counted on it are dropped, see module/campaign/star_pass.py.
        """
        config_name = getattr(self.config, 'config_name', None)
        if not config_name:
            return
        try:
            key = star_stage_key(self.config.Campaign_Event, self.config.Campaign_Mode,
                                 to_map_input_name(self.config.Campaign_Name))
            clear_star_runs(config_name, key)
        except Exception as e:
            logger.warning(f'Runs of the star pass not cleared: {e}')

    def call_then_task(self):
        """
        The task that ends calls StopCondition.ThenTask, like the event that a pass over the stages held.
        """
        task = getattr(self.config, 'StopCondition_ThenTask', '')
        if task:
            logger.info(f'{self.config.task.command} ends, {task} runs again')
            self.config.task_call(task, force_call=True)

    def campaign_name_last(self, name):
        """
        Args:
            name (str): Such as `6-1`, `t6`, `campaign_6_1`

        Returns:
            bool: If the stage ends one of the chains of stage increase, the last one that exists of its event
        """
        name = to_map_input_name(name)
        for increase in self.STAGE_INCREASE:
            increase = [i.strip(' \t\r\n') for i in increase.split('>')]
            if name in increase:
                return self.campaign_name_increase(name) == name
        return False

    def _set_2x_book_status(self, status, check_button, box_button, skip_first_screenshot=True):
        """
        Set appropriate 2x book setting
        with corresponding status and buttons
        Built with retry mechanism that limits to 3
        attempts that span 3 second intervals each

        Args:
            status (string):
                on or off
            check_button (Button):
                button to check before attempting to click
            box_button (Button):
                button to click and image color count against
            skip_first_screenshot (bool):
                namesake

        Returns:
            bool:
                True if detected having set correctly
                False can occur for 2 reasons either
                assets insufficient to detect properly
                or 2x book setting is absent

        """
        confirm_timer = Timer(0.3, count=1).start()
        clicked_threshold = 0
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if clicked_threshold > 3:
                break

            if self.appear(check_button, offset=self._auto_search_menu_offset, interval=3):
                box_button.load_offset(check_button)
                enabled = self.image_color_count(box_button.button, color=(156, 255, 82), threshold=30, count=20)
                if (status == 'on' and enabled) or (status == 'off' and not enabled):
                    return True
                if (status == 'on' and not enabled) or (status == 'off' and enabled):
                    self.device.click(box_button)

                clicked_threshold += 1

            if not clicked_threshold and confirm_timer.reached():
                logger.info('Map do not have 2x book setting')
                return False

        logger.warning(f'Wait time has expired; Cannot set 2x book setting')
        return False

    def handle_2x_book_setting(self, mode='prep'):
        """
        Handles 2x book setting if applicable

        Args:
            mode (string):
                prep or auto, assume auto if not prep

        Returns:
            bool:
                If handled to completion
        """
        if not self.map_is_clear_mode:
            return False
        if not hasattr(self, 'emotion'):
            logger.info('Emotion instance not loaded, cannot handle 2x book setting')
            return False

        logger.info(f'Handling 2x book setting, mode={mode}.')
        if mode == 'prep':
            book_check = BOOK_CHECK_PREP
            book_box = BOOK_BOX_PREP
        else:
            book_check = BOOK_CHECK_AUTO
            book_box = BOOK_BOX_AUTO

        state = 'on' if self.map_is_2x_book else 'off'
        if self._set_2x_book_status(state, book_check, book_box):
            self.emotion.map_is_2x_book = self.map_is_2x_book
        else:
            self.map_is_2x_book = False
            self.emotion.map_is_2x_book = self.map_is_2x_book

        self.handle_info_bar()
        return True

    def handle_2x_book_popup(self):
        if self.appear(BOOK_POPUP_CHECK, offset=(20, 20)):
            if self.handle_popup_confirm('2X_BOOK'):
                return True

        return False

    def handle_submarine_support_popup(self):
        """
        Should be rewritten in W16 submarine base class
        """
        return False

    def handle_map_walk_speedup(self, skip_first_screenshot=True):
        """
        Turn on walk speedup, no reason to turn it off
        """
        if not self.config.MAP_HAS_WALK_SPEEDUP:
            return False

        timeout = Timer(2, count=4).start()
        interval = Timer(1, count=2)
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if self.image_color_count(MAP_WALK_SPEEDUP, color=(132, 255, 148), threshold=75, count=50):
                logger.attr('Walk_Speedup', 'on')
                return True
            if timeout.reached():
                logger.warning(f'Wait time has expired; Cannot set map walk speedup')
                return False

            if interval.reached():
                self.device.click(MAP_WALK_SPEEDUP)
                interval.reset()
                continue
