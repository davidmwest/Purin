import os
from datetime import datetime
from time import monotonic

from module.base.timer import Timer
from module.base.utils import rgb2gray, save_image
from module.campaign.campaign_ui import CampaignUI, MODE_SWITCH_20241219
from module.combat.assets import BATTLE_PREPARATION
from module.combat.combat import Combat
from module.eventstory.assets import *
from module.exception import GameNotRunningError
from module.handler.login import LoginHandler
from module.logger import logger
from module.map.assets import FLEET_PREPARATION, MAP_PREPARATION, MAP_PREPARATION_CANCEL, MAP_PREPARATION_HARD
from module.ui.assets import BACK_ARROW, GOTO_MAIN
from module.ui.page import Page, page_event, page_main, page_sp

STORY_TIMEOUT = 90
STORY_PASS_TIMEOUT = 600
STORY_TRANSITIONS_MAX = 64
STORY_BATTLES_MAX = 8
EVENT_DIAGNOSTIC_DIR = './log/event_completion'
EVENT_DIAGNOSTIC_KEEP = 40


def ensure_event_running(main):
    """Let the scheduler start a closed game before spending a one-shot budget."""
    if not main.device.app_is_running():
        raise GameNotRunningError('Game not running')


def ensure_event_page(main, destination, timeout=30, max_clicks=12):
    """Use the normal page graph with a finite budget and recognized screens only."""
    from module.campaign.event_completion_assets import TEMPESTA_HUB, TEMPESTA_HUB_HOME, TEMPESTA_HUB_INACTIVE

    ensure_event_running(main)
    Page.init_connection(destination)
    main.interval_clear(list(Page.iter_check_buttons()))
    clicks = 0
    # Several source pages share the same home button. A second source-page
    # match during the transition can click the main screen's Settings gear.
    # Settle across fresh screenshots before allowing any further navigation.
    settled = Timer(2, count=2)
    try:
        for _ in main.loop(timeout=timeout):
            GOTO_MAIN.clear_offset()
            if main.ui_page_appear(destination):
                main.ui_current = destination
                return True
            if clicks >= max_clicks:
                break
            if not settled.reached():
                continue
            # The game's screensaver is outside the page graph. Reuse its
            # exact idle templates, counting the wake-up against this same
            # navigation budget and waiting for fresh frames before continuing.
            if main.handle_idle_page():
                clicks += 1
                settled.reset()
                continue
            # The observed featured-event hub is outside the normal page graph.
            # Its exact title and Home glyph must agree before using that exit.
            if ((main.match_template_color(TEMPESTA_HUB, offset=(3, 3), similarity=.85)
                 or main.match_template_color(TEMPESTA_HUB_INACTIVE, offset=(3, 3), similarity=.85))
                    and main.match_template_color(TEMPESTA_HUB_HOME, offset=(3, 3), similarity=.85)):
                main.device.click(TEMPESTA_HUB_HOME)
                clicks += 1
                settled.reset()
                continue
            for page in Page.iter_pages():
                if page.parent is None or page.check_button is None:
                    continue
                if main.appear(page.check_button, offset=(30, 30), interval=3):
                    button = page.links[page.parent]
                    main.device.click(button)
                    main.ui_button_interval_reset(button)
                    clicks += 1
                    settled.reset()
                    break
            else:
                # The established campaign withdrawal path backs out of this screen.
                if main.appear(BATTLE_PREPARATION, offset=(20, 20), interval=3):
                    main.device.click(BACK_ARROW)
                    clicks += 1
                    settled.reset()
                    continue
                # The common campaign runner may leave a recognized preparation screen.
                if any(main.appear(button, offset=(20, 50), interval=3) for button in (
                        MAP_PREPARATION, MAP_PREPARATION_HARD, FLEET_PREPARATION)):
                    main.device.click(MAP_PREPARATION_CANCEL)
                    clicks += 1
                    settled.reset()
        return False
    finally:
        Page.clear_connection()


def ensure_event_switch(main, switch, state, timeout=10, max_clicks=3):
    """Switch a recognized event selector; unknown screens never authorize a click."""
    clicks = 0
    click_timer = Timer(1, count=2)
    for _ in main.loop(timeout=timeout):
        current = switch.get(main=main)
        if current == state:
            return True
        if current == 'unknown':
            continue
        if click_timer.reached():
            if clicks >= max_clicks:
                return False
            switch.click(state, main=main)
            clicks += 1
            click_timer.reset()
    return False


def save_event_diagnostic(device, tag):
    """Keep the screen that stopped a bounded event action, without masking its result."""
    try:
        os.makedirs(EVENT_DIAGNOSTIC_DIR, exist_ok=True)
        tag = ''.join(c if c.isalnum() or c in '-_' else '_' for c in tag)
        file = os.path.abspath(os.path.join(
            EVENT_DIAGNOSTIC_DIR, f'{datetime.now():%Y-%m-%d_%H-%M-%S_%f}_{tag}.png'))
        save_image(device.image, file)
        logger.info(f'Event screen saved: {file}')
        for old in sorted(name for name in os.listdir(EVENT_DIAGNOSTIC_DIR) if name.endswith('.png'))[:-EVENT_DIAGNOSTIC_KEEP]:
            os.remove(os.path.join(EVENT_DIAGNOSTIC_DIR, old))
        return file
    except Exception as exc:
        logger.warning(f'Event screen not saved: {exc}')
        return None


class EventStory(CampaignUI, Combat, LoginHandler):
    def ui_goto_event_story(self, event=None):
        """
        Returns:
            str: 'finish', 'story', 'unknown'
        """
        if event is None:
            event = self.config.cross_get('Event.Campaign.Event', '')
        if event in [
            'event_20251023_cn',
            'event_20261022_cn',
        ]:
            # SP event
            destination = page_sp
        else:
            # most events show as page_event
            destination = page_event
        if not ensure_event_page(self, destination):
            return 'unknown'
        if not ensure_event_switch(self, MODE_SWITCH_20241219, 'story'):
            return 'unknown'

        state = 'unknown'
        for _ in range(3):
            # wait any story state
            timeout = Timer(2, count=6).start()
            for _ in self.loop(timeout=8):
                state = self.get_event_story_state()
                logger.attr('EventStoryState', state)
                if state != 'unknown':
                    break
                if timeout.reached():
                    logger.warning('Wait EventStoryState timeout')
                    break
            if state == 'unknown':
                # Story page get swiped, can't find story entrance
                # Reset mode to reset swipe
                if not ensure_event_switch(self, MODE_SWITCH_20241219, 'combat'):
                    return 'unknown'
                if not ensure_event_switch(self, MODE_SWITCH_20241219, 'story'):
                    return 'unknown'
                continue
            else:
                break

        return state

    def get_event_20250724_button(self):
        """
        Returns:
            Button | None:
        """
        area = (0, 72, 1280, 560)
        image = self.image_crop(area, copy=False)
        image = rgb2gray(image)
        sim, button = TEMPLATE_ALCHEMIST_STORY.match_result(image)
        if sim >= 0.85:
            button = button.move(area[:2])
            # move down to click the text
            button = button.move((0, 44))
            return button
        sim, button = TEMPLATE_ALCHEMIST_BATTLE.match_result(image)
        if sim >= 0.85:
            button = button.move(area[:2])
            # move down to click the text
            button = button.move((0, 44))
            return button
        return None

    def handle_event_20250724(self, interval=2):
        """
        In Alchemist collab 2, story button just appear everywhere

        Returns:
            bool: If clicked
        """
        interval = self.get_interval_timer(TEMPLATE_ALCHEMIST_STORY, interval=interval)
        if not interval.reached():
            return False
        button = self.get_event_20250724_button()
        if button:
            self.device.click(button)
            interval.reset()
            return True
        else:
            return False

    def event_story(self, skip_first_screenshot=True):
        """
        Args:
            skip_first_screenshot:

        Returns:
            str: 'battle' or 'finish'
        """
        logger.hr('Event story', level=1)
        for _ in self.loop(skip_first=skip_first_screenshot, timeout=STORY_TIMEOUT):

            # End
            if self.is_combat_executing() or self.is_combat_loading():
                logger.info('run_story end at battle')
                return 'battle'
            if self.match_template_color(STORY_FINISHED, offset=(20, 20), interval=3):
                logger.info('run_story end at STORY_FINISHED')
                return 'finish'
            if self.appear(REWARD_GOT, offset=(50, 30)):
                logger.info('run_story end at REWARD_GOT')
                return 'finish'

            # Story skip
            if self.handle_story_skip():
                self.interval_clear([STORY_MIDDLE, BATTLE_MIDDLE])
                continue
            if self.handle_get_items():
                continue

            # Clicks
            if self.appear_then_click(STORY_FIRST, offset=(20, 20), interval=3):
                self.story_skip_interval_clear()
                self.popup_interval_clear()
                self.device.click_record_clear()
                continue
            if self.match_template_color(STORY_LAST, offset=(20, 20), interval=3):
                self.device.click(STORY_LAST)
                self.story_skip_interval_clear()
                self.popup_interval_clear()
                self.device.click_record_clear()
                continue
            if self.appear_then_click(STORY_MIDDLE, offset=(20, 200), interval=3):
                self.story_skip_interval_clear()
                self.popup_interval_clear()
                self.device.click_record_clear()
                continue
            if self.appear_then_click(BATTLE_MIDDLE, offset=(20, 200), interval=3):
                self.story_skip_interval_clear()
                self.popup_interval_clear()
                self.device.click_record_clear()
                continue
            if self.handle_event_20250724():
                self.story_skip_interval_clear()
                self.popup_interval_clear()
                self.device.click_record_clear()
                continue
            # Secrets of the Abyss (event_20250814_cn)
            # popup after all story finished
            if self.appear_then_click(POPUP_RPG_STATUS, offset=(20, 20), interval=3):
                continue

        logger.warning('Event story timed out without a recognized end state')
        save_event_diagnostic(self.device, 'story_timeout')
        return 'unknown'

    def run_event_story(self, event=None, skip_battle=False):
        """
        Loop until event story finished
        Handle story battles
        """
        deadline = monotonic() + STORY_PASS_TIMEOUT
        battles = 0
        for _ in range(STORY_TRANSITIONS_MAX):
            if monotonic() >= deadline:
                break
            state = self.ui_goto_event_story(event=event)
            if state == 'finish':
                logger.info('Event story completion verified')
                return True
            if state not in ('story', 'story_alchemist'):
                logger.warning(f'Event story stopped at unrecognized state: {state}')
                save_event_diagnostic(self.device, 'story_unknown')
                return False
            result = self.event_story()
            if result == 'battle':
                if not skip_battle or battles >= STORY_BATTLES_MAX:
                    logger.warning('Event story battle stopped: skip disabled or restart limit reached')
                    save_event_diagnostic(self.device, 'story_battle_blocked')
                    return False
                # Kill game is considered cleared battles
                # It's much faster than waiting event battles
                logger.hr('Event Story Battle', level=2)
                battles += 1
                logger.info(f'Skipping story battle by game restart ({battles}/{STORY_BATTLES_MAX})')
                self.config.override(Error_HandleError=True)
                self.app_stop()
                self.app_start()
                continue
            if result == 'finish':
                # Run after finished event story, in order to close GET_ITEMS
                logger.hr('Event story finish', level=2)
                if not ensure_event_page(self, page_main):
                    save_event_diagnostic(self.device, 'story_return_blocked')
                    return False
                continue
            logger.warning(f'Event story stopped without completion: {result}')
            return False

        logger.warning('Event story pass reached its time or transition limit')
        save_event_diagnostic(self.device, 'story_pass_limit')
        return False

    def get_event_story_state(self):
        """
        Returns:
            str: 'finish', 'story', 'unknown'
        """
        if self.match_template_color(STORY_FINISHED, offset=(20, 20)):
            return 'finish'
        if self.appear(REWARD_GOT, offset=(50, 30)):
            return 'finish'

        if self.appear_then_click(STORY_FIRST, offset=(20, 20)):
            return 'story'
        if self.match_template_color(STORY_LAST, offset=(20, 20)):
            return 'story'
        if self.appear_then_click(STORY_MIDDLE, offset=(20, 200)):
            return 'story'
        if self.appear_then_click(BATTLE_MIDDLE, offset=(20, 200)):
            return 'story'
        if self.get_event_20250724_button():
            return 'story_alchemist'

        return 'unknown'

    def run(self, event=None, skip_battle=None):
        if event is None:
            event = self.config.cross_get('Event.Campaign.Event', '')
        if skip_battle is None:
            skip_battle = getattr(self.config, 'EventStory_SkipBattle', False)
        if event in [
            # event story is in event minigame
            'event_20260226_cn',
        ]:
            logger.info(f'Current event ({event}) does not have event story, stopped')
            return False

        if not self.device.app_is_running():
            logger.warning('Game is not running, start it')
            self.app_start()

        return self.run_event_story(event=event, skip_battle=skip_battle)

        # Scheduler
        pass


if __name__ == '__main__':
    self = EventStory('alas')
    self.run()
