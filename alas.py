import os

# OpenMP starts a thread a core for the OCR, each one spinning while it waits for work.
# On a Mac of 24 cores shared with emulators, the OCR of the inventory took 10 cores and ran 20 times slower.
# Set before anything loads OpenMP. Also for alas.py run by itself.
os.environ.setdefault('OMP_NUM_THREADS', '1')

import atexit
import re
import threading
import time
from datetime import datetime, timedelta

import inflection
from cached_property import cached_property

from module.base.decorator import del_cached_property
from module.config.config import AzurLaneConfig, TaskEnd
from module.config.deep import deep_get, deep_set
from module.config.scheduler_state import remove_scheduler_state, write_scheduler_state
from module.config.utils import filepath_pause
from module.exception import *
from module.logger import logger
from module.notify import handle_notify


def failed_task_delay(previous=None):
    """
    Args:
        previous (int): Minutes the task was put off for the last time it kept failing,
            None if it has succeeded since.

    Returns:
        int: Minutes to put it off for now. Longer every time, a task that is broken should not
            cost a restart of the game every few minutes.
    """
    if not previous:
        return 20
    return min(previous * 2, 240)


# A game that can't log in, in a maintenance or with its emulator gone, is tried again after 1, 2, 4, 8 minutes,
# then every 10 (the user, 2026-10-22: keep checking for the end of maintenance, but every 10 minutes or so)
LOGIN_RETRY_MAX = 10


def login_retry_delay(failures):
    """
    Args:
        failures (int): Logins failed in a row, 1 for the first

    Returns:
        int: Minutes before the next try to log in
    """
    return min(2 ** max(failures - 1, 0), LOGIN_RETRY_MAX)


# Optimization_WhenTaskQueueEmpty=close_emulator stops the game and the emulator only for a wait this long or longer,
# starting them again takes about 2 minutes. A shorter wait is spent at the main page.
CLOSE_EMULATOR_MIN_WAIT = timedelta(minutes=10)


class AzurLaneAutoScript:
    stop_event: threading.Event = None
    # Logged out during a task, most likely by a log in on another device: no log in before this time
    logged_out_until = None

    def __init__(self, config_name='alas'):
        logger.hr('Start', level=0)
        self.config_name = config_name
        # Skip first restart
        self.is_first_task = True
        # Failure count of tasks
        # Key: str, task name, value: int, failure count
        self.failure_record = {}
        # Minutes a task was last put off for after failing over and over, {task: minutes}
        self.failure_delay = {}
        # Logins failed in a row, and when to try again. While it is set, no task runs but Restart: any other would
        # only fail on the login page and call Restart again, every 30 s all night (2026-10-22, a maintenance).
        self.login_failures = 0
        self.login_retry_at = None

    @cached_property
    def config(self):
        try:
            config = AzurLaneConfig(config_name=self.config_name)
            return config
        except RequestHumanTakeover:
            logger.critical('Request human takeover')
            exit(1)
        except Exception as e:
            logger.exception(e)
            exit(1)

    @cached_property
    def device(self):
        try:
            from module.device.device import Device
            # Starts the emulator if it is not running
            device = Device(config=self.config)
            return device
        except EmulatorBusy as e:
            logger.critical(f'{e}, this scheduler does not tap over it')
            exit(1)
        except RequestHumanTakeover:
            if self.config.Error_KeepRunning:
                # The emulator offline for now, the loop tries again later
                raise
            logger.critical('Request human takeover')
            exit(1)
        except Exception as e:
            logger.exception(e)
            exit(1)

    def login_failed(self, reason):
        """
        The game could not log in, or its emulator could not be reached: no task runs until the next try.

        Args:
            reason (str):

        Returns:
            int: Minutes before the next try
        """
        self.login_failures += 1
        minutes = login_retry_delay(self.login_failures)
        self.login_retry_at = datetime.now().replace(microsecond=0) + timedelta(minutes=minutes)
        # Restart is never put off for hours or given up on, the backoff here is its own
        deep_set(self.failure_record, keys='Restart', value=0)
        logger.warning(f'{reason}, {self.login_failures} time(s) in a row. '
                       f'Try to log in again in {minutes} minutes, at {self.login_retry_at}')
        return minutes

    def login_succeeded(self):
        if self.login_failures:
            logger.info(f'Logged in again after {self.login_failures} failed tries')
        self.login_failures = 0
        self.login_retry_at = None

    def drop_device(self):
        """
        Forget a device that can't be reached, the next task connects again.
        The lock of the emulator stays with this process, see module/device/owner.py.
        """
        device = self.__dict__.pop('device', None)
        if device is not None:
            try:
                device.user_input_stop()
            except Exception as e:
                logger.warning(f'Device not stopped: {e}')

    @cached_property
    def checker(self):
        try:
            from module.server_checker import ServerChecker
            checker = ServerChecker(server=self.config.Emulator_ServerName)
            return checker
        except Exception as e:
            logger.exception(e)
            exit(1)

    def run(self, command, skip_first_screenshot=False):
        try:
            if not skip_first_screenshot:
                self.device.screenshot()
            self.__getattribute__(command)()
            return True
        except TaskEnd:
            return True
        except UserPlaying as e:
            # Not a failure, the task runs again once nobody is playing
            logger.warning(e)
            return None
        except GameLoggedOutError as e:
            logger.warning(e)
            self.handle_logged_out()
            return False
        except GameNotRunningError as e:
            logger.warning(e)
            self.config.task_call('Restart')
            return False
        except (GameStuckError, GameTooManyClickError) as e:
            logger.error(e)
            self.save_error_log()
            if self.logged_out_on_screen():
                self.handle_logged_out()
                return False
            logger.warning(f'Game stuck, {self.device.package} will be restarted in 10 seconds')
            logger.warning('If you are playing by hand, please stop Alas')
            self.config.task_call('Restart')
            self.device.sleep(10)
            return False
        except GameBugError as e:
            logger.warning(e)
            self.save_error_log()
            if self.logged_out_on_screen():
                self.handle_logged_out()
                return False
            logger.warning('An error has occurred in Azur Lane game client, Alas is unable to handle')
            logger.warning(f'Restarting {self.device.package} to fix it')
            self.config.task_call('Restart')
            self.device.sleep(10)
            return False
        except GamePageUnknownError:
            if self.logged_out_on_screen():
                self.handle_logged_out()
                return False
            logger.info('Game server may be under maintenance or network may be broken, check server status now')
            self.checker.check_now()
            if self.checker.is_available():
                logger.critical('Game page unknown')
                self.save_error_log()
                if self.config.Error_KeepRunning:
                    # A frozen game or a popup nobody is there to close, a restart gets out of both
                    logger.warning(f'{self.device.package} will be restarted in 10 seconds')
                    logger.warning('If you are playing by hand, please stop Alas')
                    self.config.task_call('Restart')
                    self.device.sleep(10)
                    return False
                handle_notify(
                    self.config.Error_OnePushConfig,
                    title=f"Alas <{self.config_name}> crashed",
                    content=f"<{self.config_name}> GamePageUnknownError",
                )
                exit(1)
            else:
                self.checker.wait_until_available()
                return False
        except ScriptError as e:
            logger.exception(e)
            logger.critical('This is likely to be a mistake of developers, but sometimes just random issues')
            handle_notify(
                self.config.Error_OnePushConfig,
                title=f"Alas <{self.config_name}> crashed",
                content=f"<{self.config_name}> ScriptError",
            )
            exit(1)
        except RequestHumanTakeover:
            logger.critical('Request human takeover')
            if self.config.Error_KeepRunning:
                # Most likely the emulator offline for a while (2026-10-22 00:35, the main account stopped for the
                # night on 13 s of "device offline"): connect again and log in later
                handle_notify(
                    self.config.Error_OnePushConfig,
                    title=f"Alas <{self.config_name}> lost its emulator",
                    content=f"<{self.config_name}> RequestHumanTakeover, trying again later",
                )
                self.drop_device()
                self.config.task_call('Restart')
                self.login_failed('Emulator not reached')
                return False
            handle_notify(
                self.config.Error_OnePushConfig,
                title=f"Alas <{self.config_name}> crashed",
                content=f"<{self.config_name}> RequestHumanTakeover",
            )
            exit(1)
        except Exception as e:
            logger.exception(e)
            self.save_error_log()
            if self.logged_out_on_screen():
                self.handle_logged_out()
                return False
            if self.config.Error_KeepRunning:
                # Whatever it was, a restart of the game is where every task can start from.
                # A task that keeps failing like this is put off by the scheduler.
                logger.warning(f'{self.device.package} will be restarted in 10 seconds')
                logger.warning('If you are playing by hand, please stop Alas')
                self.config.task_call('Restart')
                self.device.sleep(10)
                return False
            handle_notify(
                self.config.Error_OnePushConfig,
                title=f"Alas <{self.config_name}> crashed",
                content=f"<{self.config_name}> Exception occured",
            )
            exit(1)

    def save_error_log(self):
        """
        Save last 60 screenshots in ./log/error/<timestamp>
        Save logs to ./log/error/<timestamp>/log.txt
        """
        from module.base.utils import save_image
        from module.handler.sensitive_info import (handle_sensitive_image,
                                                   handle_sensitive_logs)
        if self.config.Error_SaveError:
            if not os.path.exists('./log/error'):
                os.mkdir('./log/error')
            folder = f'./log/error/{int(time.time() * 1000)}'
            logger.warning(f'Saving error: {folder}')
            os.mkdir(folder)
            for data in self.device.screenshot_deque:
                image_time = datetime.strftime(data['time'], '%Y-%m-%d_%H-%M-%S-%f')
                image = handle_sensitive_image(data['image'])
                save_image(image, f'{folder}/{image_time}.png')
            with open(logger.log_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                start = 0
                for index, line in enumerate(lines):
                    line = line.strip(' \r\t\n')
                    if re.match('^═{15,}$', line):
                        start = index
                lines = lines[start - 2:]
                lines = handle_sensitive_logs(lines)
            with open(f'{folder}/log.txt', 'w', encoding='utf-8') as f:
                f.writelines(lines)

    def restart(self):
        from module.handler.login import LoginHandler
        LoginHandler(self.config, device=self.device).app_restart()

    def start(self):
        from module.handler.login import LoginHandler
        LoginHandler(self.config, device=self.device).app_start()

    def log_flood_check(self):
        from module.handler.login import LoginHandler
        LoginHandler(self.config, device=self.device).app_restart_on_log_flood()

    def goto_main(self):
        from module.handler.login import LoginHandler
        from module.ui.ui import UI
        if self.device.app_is_running():
            logger.info('App is already running, goto main page')
            UI(self.config, device=self.device).ui_goto_main()
        else:
            logger.info('App is not running, start app and goto main page')
            LoginHandler(self.config, device=self.device).app_start()
            UI(self.config, device=self.device).ui_goto_main()

    def research(self):
        from module.research.research import RewardResearch
        RewardResearch(config=self.config, device=self.device).run()

    def commission(self):
        from module.commission.commission import RewardCommission
        RewardCommission(config=self.config, device=self.device).run()

    def tactical(self):
        from module.tactical.tactical_class import RewardTacticalClass
        RewardTacticalClass(config=self.config, device=self.device).run()

    def dorm(self):
        from module.dorm.dorm import RewardDorm
        RewardDorm(config=self.config, device=self.device).run()

    def dorm_rest(self):
        from module.mood.dorm_rest import DormRest
        DormRest(config=self.config, device=self.device).run()

    def inventory(self):
        from module.inventory.inventory import Inventory
        Inventory(config=self.config, device=self.device).run()

    def formation(self):
        from module.formation.formation import Formation
        Formation(config=self.config, device=self.device).run()

    def dock_care(self):
        from module.dock.care import DockCare
        DockCare(config=self.config, device=self.device).run()

    def handbook(self):
        from module.handbook.handbook import Handbook
        Handbook(config=self.config, device=self.device).run()

    def limit_break(self):
        from module.leveling.limit_break import LimitBreak
        LimitBreak(config=self.config, device=self.device).run()

    def leveling(self):
        from module.leveling.leveling import Leveling
        Leveling(config=self.config, device=self.device).run()

    def meowfficer(self):
        from module.meowfficer.meowfficer import RewardMeowfficer
        RewardMeowfficer(config=self.config, device=self.device).run()

    def guild(self):
        from module.guild.guild_reward import RewardGuild
        RewardGuild(config=self.config, device=self.device).run()

    def reward(self):
        from module.reward.reward import Reward
        Reward(config=self.config, device=self.device).run()

    def awaken(self):
        from module.awaken.awaken import Awaken
        Awaken(config=self.config, device=self.device).run()

    def enhance_ships(self):
        from module.retire.scheduled import EnhanceShips
        EnhanceShips(config=self.config, device=self.device).run()

    def retire_ships(self):
        from module.retire.scheduled import RetireShips
        RetireShips(config=self.config, device=self.device).run()

    def shop_frequent(self):
        from module.shop.shop_reward import RewardShop
        RewardShop(config=self.config, device=self.device).run_frequent()

    def shop_guild(self):
        from module.shop.shop_reward import RewardShop
        RewardShop(config=self.config, device=self.device).run_guild()

    def shop_monthly(self):
        from module.shop.shop_reward import RewardShop
        RewardShop(config=self.config, device=self.device).run_monthly()

    def shop_once(self):
        from module.shop.shop_reward import RewardShop
        RewardShop(config=self.config, device=self.device).run_once()

    def event_shop(self):
        from module.shop_event.shop_event import EventShop
        EventShop(config=self.config, device=self.device).run()

    def shipyard(self):
        from module.shipyard.shipyard_reward import RewardShipyard
        RewardShipyard(config=self.config, device=self.device).run()

    def gacha(self):
        from module.gacha.gacha_reward import RewardGacha
        RewardGacha(config=self.config, device=self.device).run()

    def freebies(self):
        from module.freebies.freebies import Freebies
        Freebies(config=self.config, device=self.device).run()

    def minigame(self):
        from module.minigame.minigame import Minigame
        Minigame(config=self.config, device=self.device).run()

    def private_quarters(self):
        from module.private_quarters.private_quarters import PrivateQuarters
        PrivateQuarters(config=self.config, device=self.device).run()

    def daily(self):
        from module.daily.daily import Daily
        Daily(config=self.config, device=self.device).run()

    def hard(self):
        from module.hard.hard import CampaignHard
        CampaignHard(config=self.config, device=self.device).run()

    def exercise(self):
        from module.exercise.exercise import Exercise
        Exercise(config=self.config, device=self.device).run()

    def sos(self):
        from module.sos.sos import CampaignSos
        CampaignSos(config=self.config, device=self.device).run()

    def war_archives(self):
        from module.war_archives.war_archives import CampaignWarArchives
        CampaignWarArchives(config=self.config, device=self.device).run(
            name=self.config.Campaign_Name, folder=self.config.Campaign_Event, mode=self.config.Campaign_Mode)

    def raid_daily(self):
        from module.raid.daily import RaidDaily
        RaidDaily(config=self.config, device=self.device).run()

    def event_a(self):
        from module.event.campaign_abcd import CampaignABCD
        CampaignABCD(config=self.config, device=self.device).run()

    def event_b(self):
        from module.event.campaign_abcd import CampaignABCD
        CampaignABCD(config=self.config, device=self.device).run()

    def event_c(self):
        from module.event.campaign_abcd import CampaignABCD
        CampaignABCD(config=self.config, device=self.device).run()

    def event_d(self):
        from module.event.campaign_abcd import CampaignABCD
        CampaignABCD(config=self.config, device=self.device).run()

    def event_sp(self):
        from module.event.campaign_sp import CampaignSP
        CampaignSP(config=self.config, device=self.device).run()

    def maritime_escort(self):
        from module.event.maritime_escort import MaritimeEscort
        MaritimeEscort(config=self.config, device=self.device).run()

    def opsi_ash_assist(self):
        from module.os_ash.meta import AshBeaconAssist
        AshBeaconAssist(config=self.config, device=self.device).run()

    def opsi_ash_beacon(self):
        from module.os_ash.meta import OpsiAshBeacon
        OpsiAshBeacon(config=self.config, device=self.device).run()

    def opsi_explore(self):
        from module.campaign.os_run import OSCampaignRun
        OSCampaignRun(config=self.config, device=self.device).opsi_explore()

    def opsi_shop(self):
        from module.campaign.os_run import OSCampaignRun
        OSCampaignRun(config=self.config, device=self.device).opsi_shop()

    def opsi_voucher(self):
        from module.campaign.os_run import OSCampaignRun
        OSCampaignRun(config=self.config, device=self.device).opsi_voucher()

    def opsi_daily(self):
        from module.campaign.os_run import OSCampaignRun
        OSCampaignRun(config=self.config, device=self.device).opsi_daily()

    def opsi_obscure(self):
        from module.campaign.os_run import OSCampaignRun
        OSCampaignRun(config=self.config, device=self.device).opsi_obscure()

    def opsi_month_boss(self):
        from module.campaign.os_run import OSCampaignRun
        OSCampaignRun(config=self.config, device=self.device).opsi_month_boss()

    def opsi_abyssal(self):
        from module.campaign.os_run import OSCampaignRun
        OSCampaignRun(config=self.config, device=self.device).opsi_abyssal()

    def opsi_archive(self):
        from module.campaign.os_run import OSCampaignRun
        OSCampaignRun(config=self.config, device=self.device).opsi_archive()

    def opsi_stronghold(self):
        from module.campaign.os_run import OSCampaignRun
        OSCampaignRun(config=self.config, device=self.device).opsi_stronghold()

    def opsi_meowfficer_farming(self):
        from module.campaign.os_run import OSCampaignRun
        OSCampaignRun(config=self.config, device=self.device).opsi_meowfficer_farming()

    def opsi_hazard1_leveling(self):
        from module.campaign.os_run import OSCampaignRun
        OSCampaignRun(config=self.config, device=self.device).opsi_hazard1_leveling()

    def opsi_cross_month(self):
        from module.campaign.os_run import OSCampaignRun
        OSCampaignRun(config=self.config, device=self.device).opsi_cross_month()

    def main(self):
        from module.campaign.run import CampaignRun
        CampaignRun(config=self.config, device=self.device).run(
            name=self.config.Campaign_Name, folder=self.config.Campaign_Event, mode=self.config.Campaign_Mode)

    def main2(self):
        from module.campaign.run import CampaignRun
        CampaignRun(config=self.config, device=self.device).run(
            name=self.config.Campaign_Name, folder=self.config.Campaign_Event, mode=self.config.Campaign_Mode)

    def main3(self):
        from module.campaign.run import CampaignRun
        CampaignRun(config=self.config, device=self.device).run(
            name=self.config.Campaign_Name, folder=self.config.Campaign_Event, mode=self.config.Campaign_Mode)

    def event(self):
        from module.campaign.run import CampaignRun
        CampaignRun(config=self.config, device=self.device).run(
            name=self.config.Campaign_Name, folder=self.config.Campaign_Event, mode=self.config.Campaign_Mode)

    def event2(self):
        from module.campaign.run import CampaignRun
        CampaignRun(config=self.config, device=self.device).run(
            name=self.config.Campaign_Name, folder=self.config.Campaign_Event, mode=self.config.Campaign_Mode)

    def raid(self):
        from module.raid.run import RaidRun
        RaidRun(config=self.config, device=self.device).run()

    def hospital(self):
        from module.event_hospital.hospital import Hospital
        Hospital(config=self.config, device=self.device).run()

    def coalition(self):
        from module.coalition.coalition import Coalition
        Coalition(config=self.config, device=self.device).run()

    def coalition_sp(self):
        from module.coalition.coalition_sp import CoalitionSP
        CoalitionSP(config=self.config, device=self.device).run()

    def c72_mystery_farming(self):
        from module.campaign.run import CampaignRun
        CampaignRun(config=self.config, device=self.device).run(
            name=self.config.Campaign_Name, folder=self.config.Campaign_Event, mode=self.config.Campaign_Mode)

    def c122_medium_leveling(self):
        from module.campaign.run import CampaignRun
        CampaignRun(config=self.config, device=self.device).run(
            name=self.config.Campaign_Name, folder=self.config.Campaign_Event, mode=self.config.Campaign_Mode)

    def c124_large_leveling(self):
        from module.campaign.run import CampaignRun
        CampaignRun(config=self.config, device=self.device).run(
            name=self.config.Campaign_Name, folder=self.config.Campaign_Event, mode=self.config.Campaign_Mode)

    def gems_farming(self):
        from module.campaign.gems_farming import GemsFarming
        GemsFarming(config=self.config, device=self.device).run(
            name=self.config.Campaign_Name, folder=self.config.Campaign_Event, mode=self.config.Campaign_Mode)

    def island_production(self):
        from module.island.production import IslandProduction
        IslandProduction(config=self.config, device=self.device).run()

    def island_order(self):
        from module.island.order import IslandOrder
        IslandOrder(config=self.config, device=self.device).run()

    def island_freebie(self):
        from module.island.freebie import IslandFreebie
        IslandFreebie(config=self.config, device=self.device).run()

    def island_collect(self):
        from module.island.collect import IslandCollect
        IslandCollect(config=self.config, device=self.device).run()

    def island_season_task(self):
        from module.island.season_task import IslandSeasonTask
        IslandSeasonTask(config=self.config, device=self.device).run()

    def island_business(self):
        from module.island.business import IslandBusiness
        IslandBusiness(config=self.config, device=self.device).run()

    def daemon(self):
        from module.daemon.daemon import AzurLaneDaemon
        AzurLaneDaemon(config=self.config, device=self.device, task="Daemon").run()

    def opsi_daemon(self):
        from module.daemon.os_daemon import AzurLaneDaemon
        AzurLaneDaemon(config=self.config, device=self.device, task="OpsiDaemon").run()

    def event_story(self):
        from module.eventstory.eventstory import EventStory
        EventStory(config=self.config, device=self.device, task="EventStory").run()

    def island_production_planner(self):
        from module.island_handler.production_planner import IslandProductionPlanner
        IslandProductionPlanner(config=self.config, device=self.device, task="IslandProductionPlanner").run()

    def azur_lane_uncensored(self):
        from module.daemon.uncensored import AzurLaneUncensored
        AzurLaneUncensored(config=self.config, device=self.device, task="AzurLaneUncensored").run()

    def benchmark(self):
        from module.daemon.benchmark import run_benchmark
        run_benchmark(config=self.config)

    def game_manager(self):
        from module.daemon.game_manager import GameManager
        GameManager(config=self.config, device=self.device, task="GameManager").run()

    def close_emulator(self):
        """
        Stop the emulator during a long wait, with the device connected to it.
        The device of the next task is created again, which starts the emulator.

        Returns:
            bool: If stopped. False on platforms that can not stop emulators, the emulator stays as it is.
        """
        logger.info('Close emulator during wait')
        self.device.user_input_stop()
        if not self.device.emulator_stop():
            logger.warning('Emulator is not closed, close the game only')
            return False
        del_cached_property(self, 'device')
        return True

    def wait_until(self, future):
        """
        Wait until a specific time.

        Args:
            future (datetime):

        Returns:
            bool: True if wait finished, False if config changed or a pause is requested.
        """
        future = future + timedelta(seconds=1)
        self.config.start_watching()
        while 1:
            if datetime.now() > future:
                return True
            if self.stop_event is not None:
                if self.stop_event.is_set():
                    logger.info("Update event detected")
                    logger.info(f"[{self.config_name}] exited. Reason: Update")
                    exit(0)

            time.sleep(5)

            if self.config.should_reload():
                return False
            # Hold now, not when the next task is due, someone may be playing already
            if self.config.pause_requested:
                return False

    def put_off_failed_task(self, task):
        """
        A task failed 3 times in a row. Every failure ended with a restart of the game,
        so the game is fine again: let the other tasks run, and try this one again later.

        Args:
            task (str):

        Returns:
            int: Minutes the task is put off for.
        """
        minutes = failed_task_delay(self.failure_delay.get(task))
        self.failure_delay[task] = minutes
        deep_set(self.failure_record, keys=task, value=0)
        logger.critical(f'Task `{task}` failed 3 or more times, try it again in {minutes} minutes')
        handle_notify(
            self.config.Error_OnePushConfig,
            title=f"Alas <{self.config_name}> task failed",
            content=f"<{self.config_name}> Task `{task}` failed 3 or more times, "
                    f"trying it again in {minutes} minutes",
        )
        # The task that just ran may have left the config bound to another task
        del_cached_property(self, 'config')
        self.config.task_delay(minute=minutes, task=task)
        del_cached_property(self, 'config')
        return minutes

    def logged_out_on_screen(self):
        """
        Returns:
            bool: If the game shows its login page, or a popup that asks to log in again.
                Always False if Error.LoggedOutWait is 0.
        """
        if self.config.Error_LoggedOutWait <= 0:
            return False
        # A login page is expected until the game is logged in, like after an update
        if not getattr(self.device, 'logged_in', False):
            return False
        try:
            from module.handler.logged_out import logged_out
            self.device.screenshot()
            return logged_out(self.device.image)
        except Exception as e:
            logger.warning(f'Can not check if the account was logged out: {e}')
            return False

    def handle_logged_out(self):
        """
        The account was logged out during a task, most likely because it logged in on another device.
        Logging in again at once would log that device out: stop the game and log in again later.
        """
        minutes = max(int(self.config.Error_LoggedOutWait), 0)
        self.logged_out_until = datetime.now() + timedelta(minutes=minutes)
        logger.warning(f'The account was logged out, maybe it logged in on another device. '
                       f'Logging in again at {self.logged_out_until.strftime("%H:%M")}')
        try:
            self.device.app_stop()
        except Exception as e:
            logger.warning(f'Can not stop the game: {e}')
        self.config.task_call('Restart')

    def wait_while_logged_out(self):
        """
        Hold between tasks until the time to log in again, see handle_logged_out().

        Returns:
            bool: If waited.
        """
        if self.logged_out_until is None or datetime.now() >= self.logged_out_until:
            self.logged_out_until = None
            return False

        logger.hr('Logged out', level=1)
        logger.info(f'Waiting until {self.logged_out_until.strftime("%H:%M")} before logging in again')
        write_scheduler_state(self.config_name, task='LoggedOut', paused=True)
        while datetime.now() < self.logged_out_until:
            if self.stop_event is not None and self.stop_event.is_set():
                break
            time.sleep(5)
        self.logged_out_until = None
        logger.info('Logging in again')
        write_scheduler_state(self.config_name)
        del_cached_property(self, 'config')
        return True

    def wait_while_paused(self):
        """
        Hold between tasks while ./config/<config_name>.pause exists,
        so the game can be used by hand or by another script without killing Alas in the middle of something.
        Tasks that check for a task switch, like the farming ones, end at their next check.

        Returns:
            bool: If paused.
        """
        if not self.config.pause_requested:
            return False

        logger.hr('Paused', level=1)
        logger.info(f'Scheduler is paused, delete {filepath_pause(self.config_name)} to continue')
        write_scheduler_state(self.config_name, paused=True)
        while self.config.pause_requested:
            if self.stop_event is not None and self.stop_event.is_set():
                break
            time.sleep(2)
        logger.info('Scheduler continues')
        write_scheduler_state(self.config_name)
        # Config may have been edited during the pause
        del_cached_property(self, 'config')
        return True

    def wait_while_user_plays(self):
        """
        Hold while someone plays on the emulator, until nobody has touched it for Optimization.UserInputWait seconds.
        The task that was running starts again after, from wherever the game was left.
        """
        wait = self.device.user_input_wait
        logger.hr('Someone playing', level=1)
        logger.info(f'Purin waits until nobody has touched the emulator for {wait}s')
        write_scheduler_state(self.config_name, task='UserPlaying', paused=True)
        while self.device.user_playing():
            if self.stop_event is not None and self.stop_event.is_set():
                break
            time.sleep(1)
        logger.info('Nobody touched the emulator for a while, Purin continues')
        write_scheduler_state(self.config_name)

    def get_next_task(self):
        """
        Returns:
            str: Name of the next task.
        """
        while 1:
            # A pause may come while waiting for the next task
            self.wait_while_paused()
            # Someone may play on another device
            self.wait_while_logged_out()
            task = self.config.get_next()
            self.config.task = task
            self.config.bind(task)

            from module.base.resource import release_resources
            if self.config.task.command != 'Alas':
                release_resources(next_task=task.command)

            if task.next_run > datetime.now():
                logger.info(f'Wait until {task.next_run} for task `{task.command}`')
                self.is_first_task = False
                method = self.config.Optimization_WhenTaskQueueEmpty
                # Without a device, the emulator was closed, or nothing connected to it since the scheduler started.
                # It is not started only to be closed, the device of the next task starts it.
                emulator_closed = method == 'close_emulator' and 'device' not in self.__dict__
                if method != 'stay_there' and 'device' in self.__dict__ and self.device.user_playing():
                    # The game is not closed nor taken to its main page under someone's fingers
                    logger.info('Someone is playing on the emulator, stay there during wait')
                    method = 'stay_there'
                if method == 'close_emulator' and not emulator_closed \
                        and task.next_run - datetime.now() < CLOSE_EMULATOR_MIN_WAIT:
                    # Not worth a restart of the game either, it takes half a minute every time
                    logger.info(f'Wait is shorter than {CLOSE_EMULATOR_MIN_WAIT}, goto main page only')
                    method = 'goto_main'
                if method in ['close_game', 'close_emulator']:
                    if not emulator_closed:
                        logger.info('Close game during wait')
                        self.device.app_stop()
                        release_resources()
                        self.device.release_during_wait()
                        if method == 'close_emulator':
                            self.close_emulator()
                    if not self.wait_until(task.next_run):
                        del_cached_property(self, 'config')
                        if self.config.pause_requested and task.command != 'Restart':
                            # The game is closed, start it again once the pause is over
                            self.config.task_call('Restart')
                            del_cached_property(self, 'config')
                        continue
                    if task.command != 'Restart':
                        self.config.task_call('Restart')
                        del_cached_property(self, 'config')
                        continue
                elif method == 'goto_main':
                    logger.info('Goto main page during wait')
                    self.run('goto_main')
                    release_resources()
                    self.device.release_during_wait()
                    if not self.wait_until(task.next_run):
                        del_cached_property(self, 'config')
                        continue
                elif method == 'stay_there':
                    logger.info('Stay there during wait')
                    release_resources()
                    self.device.release_during_wait()
                    if not self.wait_until(task.next_run):
                        del_cached_property(self, 'config')
                        continue
                else:
                    logger.warning(f'Invalid Optimization_WhenTaskQueueEmpty: {method}, fallback to stay_there')
                    release_resources()
                    self.device.release_during_wait()
                    if not self.wait_until(task.next_run):
                        del_cached_property(self, 'config')
                        continue
            break

        AzurLaneConfig.is_hoarding_task = False
        return task.command

    def loop(self):
        logger.set_file_logger(self.config_name)
        logger.info(f'Start scheduler loop: {self.config_name}')
        # One controller per emulator: a scheduler started on an emulator held by an other process stops here, before
        # it writes over the status of the one that holds it. An auto serial is taken once the device resolves it.
        serial = str(self.config.Emulator_Serial or '')
        if serial and serial != 'auto':
            from module.device.owner import acquire_device
            try:
                acquire_device(serial, self.config_name)
            except EmulatorBusy as e:
                logger.critical(f'{e}, this scheduler does not start')
                exit(1)
        # Let the GUI know, wherever this scheduler was started from
        write_scheduler_state(self.config_name)
        atexit.register(remove_scheduler_state, self.config_name, owner=os.getpid())

        while 1:
            # Check update event from GUI
            if self.stop_event is not None:
                if self.stop_event.is_set():
                    logger.info("Update event detected")
                    logger.info(f"Alas [{self.config_name}] exited.")
                    break
            # Hold while the user asks for a pause
            self.wait_while_paused()
            # Check game server maintenance
            self.checker.wait_until_available()
            if self.checker.is_recovered():
                # There is an accidental bug hard to reproduce
                # Sometimes, config won't be updated due to blocking
                # even though it has been changed
                # So update it once recovered
                del_cached_property(self, 'config')
                logger.info('Server or network is recovered. Restart game client')
                self.config.task_call('Restart')
            # Get task
            task = self.get_next_task()
            # Not logged in: only Restart runs, at its next try
            if self.login_retry_at is not None:
                if datetime.now() < self.login_retry_at:
                    logger.info(f'Game not logged in, no task runs before the next try to log in, '
                                f'at {self.login_retry_at}')
                    if not self.wait_until(self.login_retry_at):
                        del_cached_property(self, 'config')
                        continue
                # Taken like any task, the config bound to it: Restart puts itself off once logged in
                self.config.task_call('Restart')
                del_cached_property(self, 'config')
                task = self.get_next_task()
                if task != 'Restart':
                    logger.warning(f'Restart is not the next task but `{task}`, it runs first')
            # Init device and change server
            try:
                _ = self.device
            except RequestHumanTakeover:
                logger.critical('Request human takeover')
                self.login_failed('Emulator not reached')
                continue
            self.device.config = self.config
            # Skip first restart
            if self.is_first_task and task == 'Restart' and self.login_retry_at is None:
                logger.info('Skip task `Restart` at scheduler start')
                self.config.task_delay(server_update=True)
                del_cached_property(self, 'config')
                continue
            # A game slowed down by a flood of logs is started again first. If that fails, run() has called Restart.
            if task != 'Restart':
                checked = self.run('log_flood_check', skip_first_screenshot=True)
                if checked is None:
                    self.wait_while_user_plays()
                    del_cached_property(self, 'config')
                    continue
                if not checked:
                    continue

            # Run
            logger.info(f'Scheduler: Start task `{task}`')
            write_scheduler_state(self.config_name, task=task)
            self.device.stuck_record_clear()
            self.device.click_record_clear()
            logger.hr(task, level=0)
            success = self.run(inflection.underscore(task))
            logger.info(f'Scheduler: End task `{task}`')
            write_scheduler_state(self.config_name)
            self.is_first_task = False
            if success is None:
                # Someone played on the emulator, the task is not counted as failed and runs again
                self.wait_while_user_plays()
                del_cached_property(self, 'config')
                continue

            # A login that failed is tried again later, any task that went well was logged in
            if success:
                self.login_succeeded()
            elif task == 'Restart':
                self.login_failed('Log in failed')
                del_cached_property(self, 'config')
                continue
            elif self.login_retry_at is not None:
                # The emulator was lost during the task
                del_cached_property(self, 'config')
                continue

            # Check failures
            failed = deep_get(self.failure_record, keys=task, default=0)
            failed = 0 if success else failed + 1
            deep_set(self.failure_record, keys=task, value=failed)
            if success:
                self.failure_delay.pop(task, None)
            if failed >= 3 and self.config.Error_KeepRunning:
                self.put_off_failed_task(task)
                self.checker.check_now()
                continue
            if failed >= 3:
                logger.critical(f"Task `{task}` failed 3 or more times.")
                logger.critical("Possible reason #1: You haven't used it correctly. "
                                "Please read the help text of the options.")
                logger.critical("Possible reason #2: There is a problem with this task. "
                                "Please contact developers or try to fix it yourself.")
                logger.critical('Request human takeover')
                handle_notify(
                    self.config.Error_OnePushConfig,
                    title=f"Alas <{self.config_name}> crashed",
                    content=f"<{self.config_name}> RequestHumanTakeover\nTask `{task}` failed 3 or more times.",
                )
                exit(1)

            if success:
                del_cached_property(self, 'config')
                continue
            elif self.config.Error_HandleError:
                # self.config.task_delay(success=False)
                del_cached_property(self, 'config')
                self.checker.check_now()
                continue
            else:
                break


if __name__ == '__main__':
    alas = AzurLaneAutoScript()
    alas.loop()
