from datetime import datetime, timedelta

import pytest

from module.mood.plan import Job, fleet_roles, rest_plan, upcoming_jobs
from module.mood.ship import ShipMood, ShipMoods

NOW = datetime(2026, 10, 14, 8, 0, 0)
LATER = (NOW + timedelta(hours=3)).strftime('%Y-%m-%d %H:%M:%S')


class Config:
    def cross_get(self, keys, default=None):
        return default

    def cross_set(self, keys, value):
        pass


def make_moods(ships, lineups=None):
    """
    Args:
        ships (dict[str, tuple[int, int, str]]): {name: (mood, fleet, place)}
    """
    moods = ShipMoods(Config())
    for name, (mood, fleet, place) in ships.items():
        moods.ships[name] = ShipMood(name, mood=mood, time=NOW, place=place,
                                     speed={'rest': 50, 'train': 40}.get(place, 20), fleet=fleet)
    moods.lineups = lineups or {}
    return moods


def task(enable=True, fleet1=1, fleet2=2, order='fleet1_mob_fleet2_boss', next_run=LATER, mode='normal'):
    return {
        'Scheduler': {'Enable': enable, 'NextRun': next_run},
        'Campaign': {'Mode': mode},
        'Fleet': {'Fleet1': fleet1, 'Fleet2': fleet2, 'FleetOrder': order},
        'Emotion': {'Mode': 'calculate'},
    }


def test_roles_of_fleets():
    assert fleet_roles('fleet1_mob_fleet2_boss') == {1: 'mob', 2: 'boss'}
    assert fleet_roles('fleet1_all_fleet2_standby') == {1: 'all'}


def test_jobs_use_the_lineup_of_a_stage_or_the_common_fleets():
    moods = make_moods({
        'Victorious META': (150, 2, 'train'), 'London': (119, 2, 'dock'), 'Fusou META': (150, 1, 'rest'),
        'Hermes': (119, 5, 'dock'), 'Musashi': (119, 6, 'dock'),
    }, lineups={'Event2': {'1': ['Victorious META', 'London'], '2': ['Fusou META']}})
    data = {
        # D3 of an event has its own lineup, its mode is still normal
        'Event2': task(),
        'EventA': task(fleet1=5, fleet2=6, order='fleet1_all_fleet2_standby'),
        'Main': task(enable=False),
        # Its lineup is not seen yet, the common fleets are not its ships
        'Main2': task(mode='hard'),
        'Alas': {'Scheduler': {'Enable': True}},
    }

    jobs = {(job.task, tuple(job.names)): job.weight for job in upcoming_jobs(data, moods)}

    assert jobs == {
        # Farming: the mob fleet does most battles
        ('Event2', ('Victorious META', 'London')): 200,
        ('Event2', ('Fusou META',)): 50,
        # Dailies run once, the standby fleet does not fight
        ('EventA', ('Hermes',)): 5,
    }


def test_boss_fleet_of_farming_rests_before_the_fleet_of_dailies():
    """
    The fleet of daily stages is at 119 and runs in a few minutes, the boss fleet of farming is full.
    """
    moods = make_moods({'Fusou META': (150, 1, 'rest'), 'Hermes': (119, 5, 'dock')},
                       lineups={'Event2': {'1': [], '2': ['Fusou META']}})
    soon = (NOW + timedelta(minutes=10)).strftime('%Y-%m-%d %H:%M:%S')
    data = {
        'Event2': task(next_run=soon),
        'EventA': task(fleet1=5, fleet2=6, order='fleet1_all_fleet2_standby', next_run=soon),
        'EventB': task(fleet1=5, fleet2=6, order='fleet1_all_fleet2_standby', next_run=soon),
    }

    assert rest_plan(upcoming_jobs(data, moods), moods, ['Fusou META'], [], 1, NOW) == ['Fusou META']


def plan(ships, jobs, rest, train=(), capacity=6, lineups=None):
    moods = make_moods(ships, lineups)
    return rest_plan(jobs, moods, list(rest), list(train), capacity, NOW)


def job(names, weight, due=False):
    return Job(task='Task', names=names, weight=weight, next_run=NOW + timedelta(minutes=30 if due else 180))


def test_ships_that_lose_the_most_mood_rest_first():
    ships = {'Mob 1': (100, 0, 'dock'), 'Mob 2': (80, 0, 'dock'), 'Boss': (60, 0, 'dock'), 'Daily': (50, 0, 'dock')}
    jobs = [job(['Mob 1', 'Mob 2'], 40), job(['Boss'], 10), job(['Daily'], 5)]

    assert plan(ships, jobs, rest=[], capacity=3) == ['Mob 2', 'Mob 1', 'Boss']


def test_full_mood_needs_no_rest_unless_its_task_is_about_to_run():
    ships = {'Mob': (150, 0, 'rest'), 'Daily': (119, 0, 'dock'), 'Other': (150, 0, 'rest')}

    # The farming task waits for oil for hours, the daily fleet can use the slot
    assert plan(ships, [job(['Mob'], 40), job(['Daily'], 5)], rest=['Mob', 'Other'], capacity=2) == \
        ['Daily', 'Mob']
    # The farming task is about to run
    assert plan(ships, [job(['Mob'], 40, due=True), job(['Daily'], 5)], rest=['Mob', 'Other'], capacity=2) == \
        ['Mob', 'Daily']


def test_ships_on_the_first_floor_stay_there():
    ships = {'Mob 1': (90, 0, 'train'), 'Mob 2': (100, 0, 'dock')}

    assert plan(ships, [job(['Mob 1', 'Mob 2'], 40)], rest=[], train=['Mob 1']) == ['Mob 2']


def test_free_slots_keep_who_rests_there():
    ships = {'Mob': (100, 0, 'dock'), 'Dmitri': (150, 0, 'rest'), 'Hatakaze': (150, 0, 'rest')}

    assert plan(ships, [job(['Mob'], 40)], rest=['Dmitri', 'Hatakaze'], capacity=3) == ['Mob', 'Dmitri', 'Hatakaze']
    assert plan(ships, [job(['Mob'], 40)], rest=['Dmitri', 'Hatakaze'], capacity=2) == ['Mob', 'Dmitri']


def test_resting_ship_is_not_swapped_for_a_few_points():
    ships = {'Resting': (105, 0, 'rest'), 'Docked': (100, 0, 'dock')}

    assert plan(ships, [job(['Resting', 'Docked'], 40)], rest=['Resting'], capacity=1) == ['Resting']


def archive(stages=4, observe=False, **kwargs):
    config = task(fleet1=6, order='fleet1_all_fleet2_standby', **kwargs)
    config['Archive'] = {'StagesPerRun': stages, 'ObserveOnly': observe}
    return config


def test_archive_weight_uses_the_bounded_batch_and_observers_need_no_rest():
    moods = make_moods({'Archive ship': (150, 6, 'rest')})
    data = {'WarArchivesComplete': archive(), 'WarArchivesPrize': archive(stages=2)}

    assert [(entry.task, entry.weight) for entry in upcoming_jobs(data, moods)] == [
        ('WarArchivesComplete', 20), ('WarArchivesPrize', 10)]
    data['WarArchivesPrize']['Archive']['ObserveOnly'] = True
    assert [(entry.task, entry.weight) for entry in upcoming_jobs(data, moods)] == [('WarArchivesComplete', 20)]


def test_active_farming_recovers_the_fleet_that_archives_kept_out_of_dorm():
    archive_names = [f'Archive {index}' for index in range(6)]
    moods = make_moods({**{name: (150, 6, 'rest') for name in archive_names},
                        'Hakuhou': (140, 1, 'dock'), 'Tirpitz': (119, 1, 'dock')})
    event = task(next_run=NOW, order='fleet1_all_fleet2_standby')
    event['Emotion']['Fleet1Control'] = 'keep_exp_bonus'
    data = {'WarArchivesComplete': archive(next_run=NOW), 'Event': event}

    chosen = rest_plan(upcoming_jobs(data, moods), moods, archive_names, [], 6, NOW)

    assert chosen[:2] == ['Tirpitz', 'Hakuhou']
    assert len(set(chosen) & set(archive_names)) == 4


def test_hourly_bonus_wait_has_explicit_priority_even_over_more_combined_work():
    ships = {'Farming': (119, 1, 'dock'), 'Archive': (150, 6, 'rest')}
    jobs = [Job('Event', ['Farming'], 50, NOW + timedelta(hours=1), keep_exp_bonus=True),
            Job('WarArchivesComplete', ['Archive'], 600, NOW)]

    assert plan(ships, jobs, rest=['Archive'], capacity=1) == ['Farming']


def test_archives_can_use_slots_while_bonus_farming_waits_for_oil():
    ships = {'Farming': (119, 1, 'rest'), 'Archive': (100, 6, 'dock')}
    jobs = [Job('Event', ['Farming'], 200, NOW + timedelta(hours=3), keep_exp_bonus=True),
            Job('WarArchivesComplete', ['Archive'], 20, NOW)]

    assert plan(ships, jobs, rest=['Farming'], capacity=1) == ['Archive']


@pytest.mark.parametrize('mode', ['calculate', 'calculate_ignore'])
def test_bonus_priority_follows_the_fleet_control_and_calculation_mode(mode):
    moods = make_moods({'Mob': (119, 1, 'dock'), 'Boss': (150, 2, 'train')})
    config = task()
    config['Emotion'].update(Mode=mode, Fleet1Control='keep_exp_bonus', Fleet2Control='prevent_yellow_face')

    assert [entry.keep_exp_bonus for entry in upcoming_jobs({'Event': config}, moods)] == [True, False]
    config['Emotion']['Mode'] = 'ignore'
    assert not any(entry.keep_exp_bonus for entry in upcoming_jobs({'Event': config}, moods))
