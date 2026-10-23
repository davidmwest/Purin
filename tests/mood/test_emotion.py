from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from module.combat.emotion import Emotion, FleetEmotion, ShipFleetEmotion
from module.exception import ScriptEnd
from module.mood.lineup import forget_lineup
from module.mood.ship import ShipMood, ShipMoods


class Config(SimpleNamespace):
    """
    The config of a farming task, its fleets 1 and 2 control mood to stay above 40.
    """

    def __init__(self, dorm_rest=True, mode='normal', storage=None):
        now = datetime.now().replace(microsecond=0)
        super().__init__(
            Campaign_Mode=mode, Campaign_Use2xBook=False, task=SimpleNamespace(command='Event2'),
            Fleet_Fleet1=1, Fleet_Fleet2=2, Fleet_FleetOrder='fleet1_mob_fleet2_boss',
            Emotion_Mode='calculate',
            Emotion_Fleet1Value=119, Emotion_Fleet1Record=now, Emotion_Fleet1Control='prevent_green_face',
            Emotion_Fleet1Recover='not_in_dormitory', Emotion_Fleet1Oath=False,
            Emotion_Fleet2Value=119, Emotion_Fleet2Record=now, Emotion_Fleet2Control='prevent_green_face',
            Emotion_Fleet2Recover='not_in_dormitory', Emotion_Fleet2Oath=False,
            storage=storage or {}, dorm_rest=dorm_rest,
            task_delay=Mock(), task_call=Mock(),
        )

    def is_task_enabled(self, task):
        return task == 'DormRest' and self.dorm_rest

    def cross_get(self, keys, default=None):
        return self.storage or default

    def cross_set(self, keys, value):
        self.storage = value

    def set_record(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


def storage(ships, lineups=None):
    now = datetime.now().replace(microsecond=0)
    moods = ShipMoods(Config(storage={}))
    for name, (mood, fleet, place) in ships.items():
        moods.ships[name] = ShipMood(name, mood=mood, time=now, place=place,
                                     speed={'rest': 50, 'train': 40}.get(place, 20), fleet=fleet)
    moods.lineups = lineups or {}
    return moods.to_dict()


SHIPS = {'Hatakaze': (60, 1, 'rest'), 'Oite': (90, 1, 'dock'), 'Hiei': (140, 2, 'train')}


def test_fleets_are_followed_as_a_whole_without_dorm_rest():
    emotion = Emotion(Config(dorm_rest=False, storage=storage(SHIPS)))

    assert [type(fleet) for fleet in emotion.fleets] == [FleetEmotion, FleetEmotion]


def test_mood_of_a_fleet_is_the_lowest_of_its_ships():
    emotion = Emotion(Config(storage=storage(SHIPS)))
    emotion.update()

    fleet_1, fleet_2 = emotion.fleets
    assert isinstance(fleet_1, ShipFleetEmotion)
    assert fleet_1.current == 60
    assert fleet_2.current == 140


def test_hard_mode_follows_the_lineup_of_the_task():
    lineups = {'Event2': {'1': ['Hiei', 'Oite'], '2': ['Hatakaze']}}
    emotion = Emotion(Config(mode='hard', storage=storage(SHIPS, lineups)))
    emotion.update()

    assert [fleet.current for fleet in emotion.fleets] == [90, 60]


def test_hard_mode_without_a_lineup_follows_the_fleets_as_a_whole():
    emotion = Emotion(Config(mode='hard', storage=storage(SHIPS)))

    assert [type(fleet) for fleet in emotion.fleets] == [FleetEmotion, FleetEmotion]


def test_hard_stage_of_an_event_follows_its_lineup():
    """
    D3 of an event has a lineup of its own, the task is in normal mode.
    """
    lineups = {'Event2': {'1': ['Hiei', 'Oite'], '2': ['Hatakaze']}}
    emotion = Emotion(Config(mode='normal', storage=storage(SHIPS, lineups)))

    assert [fleet.names for fleet in emotion.fleets] == [['Hiei', 'Oite'], ['Hatakaze']]


def test_stage_with_the_common_fleets_forgets_the_lineup_of_its_task():
    lineups = {'Event2': {'1': ['Hiei', 'Oite'], '2': ['Hatakaze']}}
    config = Config(storage=storage(SHIPS, lineups))
    main = SimpleNamespace(config=config, emotion=Emotion(config))

    forget_lineup(main)

    assert ShipMoods(config).lineups == {}
    assert [fleet.names for fleet in main.emotion.fleets] == [['Hatakaze', 'Oite'], ['Hiei']]


def test_battle_costs_mood_to_every_ship_of_the_fleet_and_is_saved():
    config = Config(storage=storage(SHIPS))
    emotion = Emotion(config)

    emotion.reduce(1)

    saved = ShipMoods(config).ships
    assert (saved['Hatakaze'].mood, saved['Oite'].mood, saved['Hiei'].mood) == (58, 88, 140)
    # Shown in the config of the task too
    assert config.Emotion_Fleet1Value == 58


def test_low_mood_warning_marks_the_ships_low_and_reads_the_dorm():
    config = Config(storage=storage(SHIPS))
    emotion = Emotion(config)

    emotion.set_exhausted(1)

    saved = ShipMoods(config).ships
    assert (saved['Hatakaze'].mood, saved['Oite'].mood) == (30, 30)
    config.task_call.assert_called_once_with('DormRest', force_call=False)


def test_task_waits_for_the_lowest_ship_and_dorm_rest_runs():
    ships = {'Hatakaze': (40, 1, 'rest'), 'Oite': (90, 1, 'dock'), 'Hiei': (140, 2, 'train')}
    config = Config(storage=storage(ships))
    emotion = Emotion(config)

    with pytest.raises(ScriptEnd):
        # 4 battles, fleet 1 needs 40 + 6
        emotion.check_reduce(4)

    target = config.task_delay.call_args[1]['target']
    # Hatakaze recovers 5 every 6 minutes on the second floor, 2 ticks
    assert timedelta(minutes=6) < target - datetime.now() <= timedelta(minutes=12)
    config.task_call.assert_called_once_with('DormRest', force_call=False)


@pytest.mark.parametrize('order,expected', [
    (None, (22, 2)),
    ('fleet1_all_fleet2_standby', (24, 0)),
    ('fleet1_standby_fleet2_all', (0, 24)),
])
def test_preflight_can_allocate_all_twelve_fights_without_changing_farm_order(order, expected):
    config = Config(dorm_rest=False)
    emotion = Emotion(config)
    recovered = datetime.now() - timedelta(seconds=1)
    emotion.fleet_1.get_recovered = Mock(return_value=recovered)
    emotion.fleet_2.get_recovered = Mock(return_value=recovered)

    emotion.check_reduce(12, fleet_order=order)

    emotion.fleet_1.get_recovered.assert_called_once_with(expected[0])
    emotion.fleet_2.get_recovered.assert_called_once_with(expected[1])
    assert config.Fleet_FleetOrder == 'fleet1_mob_fleet2_boss'
    config.task_delay.assert_not_called()
