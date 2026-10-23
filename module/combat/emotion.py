from datetime import datetime, timedelta
from time import sleep

import numpy as np

from module.base.decorator import cached_property
from module.base.utils import random_normal_distribution_int
from module.config.config import AzurLaneConfig
from module.exception import ScriptEnd, ScriptError, RequestHumanTakeover
from module.logger import logger
from module.mood.ship import ShipMoods

DIC_LIMIT = {
    'keep_exp_bonus': 120,
    'prevent_green_face': 40,
    'prevent_yellow_face': 30,
    'prevent_red_face': 2,
}
DIC_RECOVER = {
    'not_in_dormitory': 20,
    'dormitory_floor_1': 40,
    'dormitory_floor_2': 50,
}
DIC_RECOVER_MAX = {
    'not_in_dormitory': 119,
    'dormitory_floor_1': 150,
    'dormitory_floor_2': 150,
}
OATH_RECOVER = 10
# The game warns about low mood when a ship of the fleet is below this
LOW_MOOD = 30


class FleetEmotion:
    def __init__(self, config, fleet):
        """
        Args:
            config (AzurLaneConfig):
            fleet (int): Fleet index
        """
        self.config = config
        self.fleet = fleet
        self.current = 0

    @property
    def value(self):
        """
        Returns:
            int: 0 to 150
        """
        return getattr(self.config, f'Emotion_Fleet{self.fleet}Value')

    @property
    def value_name(self):
        """
        Returns:
            str:
        """
        return f'Emotion_Fleet{self.fleet}Value'

    @property
    def record(self):
        """
        Returns:
            datetime.datetime:
        """
        return getattr(self.config, f'Emotion_Fleet{self.fleet}Record')

    @property
    def recover(self):
        """
        Returns:
            str: not_in_dormitory, dormitory_floor_1, dormitory_floor_2
        """
        return getattr(self.config, f'Emotion_Fleet{self.fleet}Recover')

    @property
    def control(self):
        """
        Returns:
            str: keep_exp_bonus, prevent_green_face, prevent_yellow_face, prevent_red_face
        """
        return getattr(self.config, f'Emotion_Fleet{self.fleet}Control')

    @property
    def oath(self):
        """
        Returns:
            bool: If all ships oath.
        """
        return getattr(self.config, f'Emotion_Fleet{self.fleet}Oath')

    @property
    def speed(self):
        """
        Returns:
            int: Recover speed per 6 min.
        """
        speed = DIC_RECOVER[self.recover]
        if self.oath:
            speed += OATH_RECOVER
        return speed // 10

    @property
    def limit(self):
        """
        Returns:
            int: Minimum emotion value to control
        """
        return DIC_LIMIT[self.control]

    @property
    def max(self):
        """
        Returns:
            int: Maximum emotion value
        """
        return DIC_RECOVER_MAX[self.recover]

    def update(self):
        recover_count = int(int(datetime.now().timestamp()) // 360 - int(self.record.timestamp()) // 360)
        recover_count = max(recover_count, 0)
        self.current = min(max(self.value, 0) + self.speed * recover_count, self.max)

    def get_recovered(self, expected_reduce=0):
        """
        Args:
            expected_reduce (int):

        Returns:
            datetime.datetime: When will emotion >= control limit.
                If already recovered, return time in the past.
        """
        if self.control == 'keep_exp_bonus' and self.recover == 'not_in_dormitory':
            logger.critical(f'Fleet {self.fleet} Emotion Control=\"Keep Happy Bonus\" and '
                            f'Fleet {self.fleet} Recover Location=\"Docks\" can not be used together, '
                            'please check your emotion settings')
            raise RequestHumanTakeover
        # In 14-4 with 2X book, expected emotion reduce is 32, can't keep happy bonus (>120),
        # otherwise will infinite task delay
        if self.control == 'keep_exp_bonus' and expected_reduce >= 29:
            expected_reduce = 29
            logger.info(f'Fleet {self.fleet} expected_reduce is limited to 29 '
                        f'when Emotion Control=\"Keep Happy Bonus\"')

        recover_count = (self.limit + expected_reduce - self.current) // self.speed
        recovered = (int(datetime.now().timestamp()) // 360 + recover_count + 1) * 360
        return datetime.fromtimestamp(recovered)

    def show(self):
        logger.attr(f'Emotion fleet_{self.fleet}', self.value)


class ShipFleetEmotion(FleetEmotion):
    """
    Mood of a fleet whose ships are followed one by one, see module/mood/ship.py.
    It is the mood of the ship that has the lowest.
    """

    def __init__(self, config, fleet, moods, names):
        """
        Args:
            config (AzurLaneConfig):
            fleet (int): Fleet index, 1 or 2
            moods (ShipMoods):
            names (list[str]): Ships of the fleet
        """
        super().__init__(config, fleet)
        self.moods = moods
        self.names = names

    @property
    def ships(self):
        return self.moods.ships_of(self.names)

    @property
    def value(self):
        return self.current

    def update(self):
        now = datetime.now()
        self.current = min(ship.at(now) for ship in self.ships)

    def get_recovered(self, expected_reduce=0):
        """
        Args:
            expected_reduce (int):

        Returns:
            datetime.datetime: When every ship of the fleet will be at the control limit and `expected_reduce` above.
                If already recovered, return time in the past.
        """
        if self.control == 'keep_exp_bonus' and expected_reduce >= 29:
            expected_reduce = 29
        target = self.limit + expected_reduce
        now = datetime.now()
        ships = self.ships
        times = [ship.when(target, now) for ship in ships]
        stuck = [ship.name for ship, time in zip(ships, times) if time is None]
        if stuck:
            # The task DormRest may bring them into the dorm
            logger.warning(f'Out of the dorm, mood of {stuck} stops at 119, it is not enough for {target}. '
                           f'Check again in an hour')
            return now + timedelta(hours=1)
        return max(times)

    def fight(self, cost):
        """
        A battle of the fleet, every ship of it loses `cost`.
        """
        now = datetime.now()
        for ship in self.ships:
            ship.fight(now, cost)
        self.moods.save()
        self.update()

    def exhausted(self):
        """
        The game warns about low mood, whatever has been calculated.
        Which ship is low is not known, they are counted as low until read in the dorm again.
        """
        now = datetime.now().replace(microsecond=0)
        for ship in self.ships:
            ship.mood = min(ship.at(now), LOW_MOOD)
            ship.time = now
        self.moods.save()
        self.update()

    def show(self):
        now = datetime.now()
        moods = ', '.join(f'{ship.name} {ship.at(now)}' for ship in self.ships)
        logger.attr(f'Emotion fleet_{self.fleet}', f'{self.current} ({moods})')


class Emotion:
    total_reduced = 0
    map_is_2x_book = False

    def __init__(self, config):
        """
        Args:
            config (AzurLaneConfig):
        """
        self.config = config
        self.fleet_1 = FleetEmotion(self.config, fleet=1)
        self.fleet_2 = FleetEmotion(self.config, fleet=2)
        # {(fleet index, ship names): ShipFleetEmotion}
        self._ship_fleets = {}

    @cached_property
    def moods(self):
        return ShipMoods(self.config)

    def ship_names(self, index):
        """
        Args:
            index (int): Fleet index, 1 or 2.

        Returns:
            list[str]: Ships of the fleet if they are followed one by one, by the task DormRest.
                Empty to follow the fleet as a whole.
        """
        if not self.config.is_task_enabled('DormRest'):
            return []
        # A stage with its own lineup, like hard mode and the hard stages of events,
        # the ships there are not the common fleets. The lineup is seen on the first sortie, see record_lineup()
        task = getattr(self.config.task, 'command', '')
        if task in self.moods.lineups:
            return self.moods.lineup(task, index)
        if self.config.Campaign_Mode == 'hard':
            return []
        number = getattr(self.config, f'Fleet_Fleet{index}', 0)
        if not isinstance(number, int) or not 1 <= number <= 6:
            return []
        return self.moods.fleet(number)

    @property
    def fleets(self):
        """
        Returns:
            list[FleetEmotion]: Fleet 1 and 2.
        """
        fleets = []
        for index, fleet in [(1, self.fleet_1), (2, self.fleet_2)]:
            names = self.ship_names(index)
            if names:
                key = (index, tuple(names))
                if key not in self._ship_fleets:
                    self._ship_fleets[key] = ShipFleetEmotion(self.config, fleet=index, moods=self.moods, names=names)
                fleet = self._ship_fleets[key]
            fleets.append(fleet)
        return fleets

    @property
    def is_calculate(self):
        return 'calculate' in self.config.Emotion_Mode

    @property
    def is_ignore(self):
        return 'ignore' in self.config.Emotion_Mode

    def update(self):
        """
        Update emotion value. This should be called before doing anything.
        """
        for fleet in self.fleets:
            fleet.update()

    def record(self):
        """
        Save current emotion value to config.
        """
        value = {}
        for fleet in self.fleets:
            value[fleet.value_name] = fleet.current

        self.config.set_record(**value)

    def show(self):
        for fleet in self.fleets:
            fleet.show()

    @property
    def reduce_per_battle(self):
        if self.map_is_2x_book:
            return 4
        else:
            return 2

    @property
    def reduce_per_battle_before_entering(self):
        if self.map_is_2x_book:
            return 4
        elif self.config.Campaign_Use2xBook:
            return 4
        else:
            return 2

    def check_reduce(self, battle, fleet_order=None):
        """
        Check emotion before entering a campaign.

        Args:
            battle (int): Battles in this campaign
            fleet_order (str | None): Optional allocation for this preflight only.

        Raise:
            ScriptEnd: Delay current task to prevent emotion control in the future.
        """
        if not self.is_calculate:
            return

        method = self.config.Fleet_FleetOrder if fleet_order is None else fleet_order

        if method == 'fleet1_mob_fleet2_boss':
            battle = (battle - 1, 1)
        elif method == 'fleet1_boss_fleet2_mob':
            battle = (1, battle - 1)
        elif method == 'fleet1_all_fleet2_standby':
            battle = (battle, 0)
        elif method == 'fleet1_standby_fleet2_all':
            battle = (0, battle)
        else:
            raise ScriptError(f'Unknown fleet order: {method}')

        battle = tuple(np.array(battle) * self.reduce_per_battle_before_entering)
        logger.info(f'Expect emotion reduce: {battle}')

        self.update()
        self.record()
        self.show()
        fleets = self.fleets
        recovered = max([f.get_recovered(b) for f, b in zip(fleets, battle)])
        if recovered > datetime.now():
            logger.info('Delay current task to prevent emotion control in the future')
            self.config.task_delay(target=recovered)
            if any(isinstance(fleet, ShipFleetEmotion) for fleet in fleets):
                # Read the moods in the dorm, and rest the ships that need it
                self.config.task_call('DormRest', force_call=False)
            raise ScriptEnd('Emotion control')

    def wait(self, fleet_index):
        """
        Wait emotion of specific fleet.
        Should be called before entering any battles.

        Args:
            fleet_index (int): 1 or 2.
        """
        self.update()
        self.record()
        self.show()
        fleet = self.fleets[fleet_index - 1]
        recovered = fleet.get_recovered(expected_reduce=self.reduce_per_battle)
        if recovered > datetime.now():
            logger.hr('Emotion wait')
            logger.info(f'Emotion of fleet {fleet_index} will recover to {fleet.limit} at {recovered}')

            while 1:
                if datetime.now() > recovered:
                    break

                logger.attr('Wait until', recovered)
                sleep(60)

    def set_exhausted(self, fleet_index):
        """
        The game says the mood of a fleet is too low to attack, whatever has been calculated.
        Count it as 0, so it is given the time to recover.

        Args:
            fleet_index (int): 1 or 2.
        """
        logger.hr('Emotion exhausted')
        self.update()
        fleet = self.fleets[fleet_index - 1]
        if isinstance(fleet, ShipFleetEmotion):
            fleet.exhausted()
            # The dorm shows who is low
            self.config.task_call('DormRest', force_call=False)
        else:
            fleet.current = 0
        self.record()
        self.show()

    def reduce(self, fleet_index):
        """
        Reduce emotion of specific fleet.
        Should be called after battle executing.
        On server side, emotion is reduced once battle loading finished.

        Args:
            fleet_index (int): 1 or 2.
        """
        logger.hr('Emotion reduce')
        self.update()

        fleet = self.fleets[fleet_index - 1]
        if isinstance(fleet, ShipFleetEmotion):
            fleet.fight(self.reduce_per_battle)
        else:
            fleet.current -= self.reduce_per_battle
        self.total_reduced += self.reduce_per_battle
        self.record()
        self.show()

    @cached_property
    def bug_threshold(self):
        """
        Returns:
            int:
        """
        return random_normal_distribution_int(55, 105, n=2)

    def bug_threshold_reset(self):
        """ Call this method after emotion bug triggered. """
        del self.__dict__['bug_threshold']

    def triggered_bug(self):
        """
        Azur Lane client does not calculate emotion correctly, which is a bug.
        After a long run, we have to restart game client and let the client update it.
        """
        logger.attr('Emotion_bug', f'{self.total_reduced}/{self.bug_threshold}')
        if self.total_reduced >= self.bug_threshold:
            logger.info('Azur Lane client does not calculate emotion correctly, which is a bug. '
                        'After a long run, we have to restart game client and let the client update it.')
            self.total_reduced = 0
            self.bug_threshold_reset()
            return True
        else:
            return False
