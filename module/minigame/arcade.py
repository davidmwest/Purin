"""Weekly free arcade credits and the idle shooting game; no paid-credit exchange."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from module.base.utils import save_image
from module.base.button import Button
from module.config.utils import server_timezone
from module.exception import ScriptError
from module.logger import logger
from module.minigame.assets import GOTO_CHOOSE_GAME, MINIGAME_SCROLL_AREA
from module.minigame.minigame import MINIGAME_SCROLL
from module.ui.assets import ACADEMY_GOTO_GAME_ROOM, GAME_ROOM_CHECK
from module.ui.page import page_academy, page_game_room
from module.ui.ui import UI

ARCADE_TITLE = Button(area=(123, 12, 272, 44), color=(112, 110, 140), button=(123, 12, 272, 44),
                      file='./assets/en/minigame/arcade_room.png', name='ARCADE_TITLE')
ARCADE_START = Button(area=(1050, 604, 1198, 661), color=(123, 129, 203), button=(1050, 604, 1198, 661),
                      file='./assets/en/minigame/arcade_room.png', name='ARCADE_GAME_LIST')


def next_week(now=None):
    """Next Monday midnight on the account's server, converted at that date for DST."""
    now = now or datetime.now().astimezone()
    current = now.astimezone(timezone(server_timezone()))
    days = 7 - current.weekday()
    return (current + timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0) \
        .astimezone().replace(tzinfo=None)


class ArcadeTickets(UI):
    def capture(self, label):
        folder = Path('log/arcade')
        folder.mkdir(parents=True, exist_ok=True)
        name = f'{datetime.now():%Y-%m-%d_%H-%M-%S_%f}_{self.config.config_name}_{label}.png'
        save_image(self.device.image, str(folder / name))
        logger.info(f'Arcade screen: {folder / name}')

    def enter_arcade(self):
        self.ui_ensure(page_academy)
        for _ in self.loop(skip_first=False, timeout=30):
            if self.ui_page_appear(page_game_room):
                return
            if self.ui_page_appear(page_academy, interval=5):
                self.device.click(ACADEMY_GOTO_GAME_ROOM)
        raise ScriptError('Arcade entrance not recognized')

    def open_games(self):
        for _ in self.loop(skip_first=False, timeout=20):
            if self.appear(GAME_ROOM_CHECK, offset=(5, 5)) and MINIGAME_SCROLL.appear(main=self):
                return
            if self.match_template_color(ARCADE_TITLE, offset=(2, 2), similarity=.95, threshold=10) \
                    and self.match_template_color(ARCADE_START, offset=(2, 2), similarity=.95, threshold=10, interval=4):
                self.device.click(ARCADE_START)
                continue
            if self.appear_then_click(GOTO_CHOOSE_GAME, offset=(5, 5), interval=4):
                continue
        raise ScriptError('Arcade game list not recognized')

    def run(self):
        try:
            self.enter_arcade()
            self.capture('room')
            self.open_games()
            self.capture('games')
            if self.config.Arcade_ObserveOnly:
                # A one-shot, purchase-free screen inspection used to validate a client layout.
                self.config.cross_set('ArcadeTickets.Scheduler.Enable', False)
                self.ui_goto_main()
                logger.info('Arcade inspection complete; no credits claimed or spent')
                return
            raise ScriptError('Shooting-game layout has not been verified')
        except ScriptError as exc:
            self.capture('blocked')
            logger.warning(f'Arcade deferred: {exc}')
            self.config.task_call('Restart')
            self.config.task_delay(minute=60)
