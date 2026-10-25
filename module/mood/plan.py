"""
Which ships rest on the second floor of the dorm: those the upcoming tasks need the most.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List

from module.config.deep import deep_get
from module.mood.ship import MOOD_MAX, ShipMoods, parse_time

# Battles of a fleet in one run of a stage
ROLE_BATTLES = {'mob': 4, 'boss': 1, 'all': 5}
# Tasks that clear a few stages a day, the others farm a stage as long as they can.
# A farming fleet loses far more in a day than a daily one, even its boss fleet.
DAILY_TASKS = ['EventA', 'EventB', 'EventC', 'EventD', 'EventSp', 'CoalitionSp', 'RaidDaily']
ARCHIVE_TASKS = {'WarArchivesComplete', 'WarArchivesPrize'}
FARMING_RUNS = 50
# A ship with full mood needs no rest, unless her task is about to run
DUE_SOON = timedelta(hours=1)
# A resting ship stays for a few points of mood less than another one, not to swap ships back and forth
KEEP_RESTING = 10


@dataclass
class Job:
    task: str
    names: List[str]
    # Mood lost per run of the task times the runs in a day, roughly
    weight: int
    next_run: datetime
    # Dock recovery stops at 119, so a fleet keeping the EXP bonus needs a dorm slot.
    keep_exp_bonus: bool = False


def fleet_roles(order):
    """
    Args:
        order (str): Fleet_FleetOrder

    Returns:
        dict[int, str]: Role of each fleet that fights, by fleet index.
    """
    return {
        'fleet1_mob_fleet2_boss': {1: 'mob', 2: 'boss'},
        'fleet1_boss_fleet2_mob': {1: 'boss', 2: 'mob'},
        'fleet1_all_fleet2_standby': {1: 'all'},
        'fleet1_standby_fleet2_all': {2: 'all'},
    }.get(order, {1: 'all'})


def upcoming_jobs(data, moods):
    """
    Args:
        data (dict): The whole user config.
        moods (ShipMoods):

    Returns:
        list[Job]: Enabled tasks that sortie, with the ships of each fleet that fights.
            A stage with its own lineup uses the lineup seen last, the others the common fleets.
    """
    jobs = []
    for task, config in data.items():
        if not isinstance(config, dict) or not deep_get(config, 'Scheduler.Enable', False):
            continue
        if 'Emotion' not in config or 'Fleet' not in config:
            continue
        if task in ARCHIVE_TASKS:
            if deep_get(config, 'Archive.ObserveOnly', False):
                continue
            # Archive jobs run a bounded batch, not fifty consecutive farming sorties.
            runs = deep_get(config, 'Archive.StagesPerRun', 4)
            runs = max(1, min(runs, 20)) if type(runs) is int else 4
        else:
            runs = 1 if task in DAILY_TASKS else FARMING_RUNS
        next_run = parse_time(deep_get(config, 'Scheduler.NextRun', '')) or datetime.now()
        for index, role in fleet_roles(deep_get(config, 'Fleet.FleetOrder', '')).items():
            if task in moods.lineups:
                names = moods.lineup(task, index)
            elif deep_get(config, 'Campaign.Mode', '') == 'hard':
                # Its lineup is not seen yet
                names = []
            else:
                number = deep_get(config, f'Fleet.Fleet{index}', 0)
                names = moods.fleet(number) if isinstance(number, int) and 1 <= number <= 6 else []
            if names:
                bonus = deep_get(config, 'Emotion.Mode', '') in ('calculate', 'calculate_ignore') and \
                    deep_get(config, f'Emotion.Fleet{index}Control', '') == 'keep_exp_bonus'
                jobs.append(Job(task=task, names=names, weight=ROLE_BATTLES[role] * runs,
                                next_run=next_run, keep_exp_bonus=bonus))
    return jobs


def rest_plan(jobs, moods, rest, train, capacity, now):
    """
    Args:
        jobs (list[Job]):
        moods (ShipMoods):
        rest (list[str]): Ships resting on the second floor now.
        train (list[str]): Ships on the first floor, they stay there.
        capacity (int): Slots on the second floor.
        now (datetime):

    Returns:
        list[str]: Ships to rest on the second floor. Ships that need it first: below full mood,
            or with full mood and a task about to run. Near-due EXP-bonus fleets come first, since the
            dock cannot recover them to their required mood. Then those that lose the most mood in
            their tasks, the ones with the lowest mood among them first. Free slots then keep who is there,
            then take the other ships of the tasks.
    """
    weight, due, bonus_due = {}, {}, set()
    for job in jobs:
        for name in job.names:
            weight[name] = weight.get(name, 0) + job.weight
            # Include the one-hour retry used when a docked ship cannot reach the bonus floor.
            if job.next_run - now <= DUE_SOON:
                due[name] = True
                if job.keep_exp_bonus:
                    bonus_due.add(name)

    def needs(name):
        return moods.ships[name].at(now) < MOOD_MAX or due.get(name, False)

    def key(name):
        mood = moods.ships[name].at(now)
        if name in rest:
            mood -= KEEP_RESTING
        return name not in bonus_due, name not in due, -weight[name], mood

    candidates = [name for name in weight if name not in train and name in moods.ships]
    chosen = sorted([name for name in candidates if needs(name)], key=key)[:capacity]
    others = sorted([name for name in candidates if not needs(name)], key=key)
    for name in list(rest) + others:
        if len(chosen) >= capacity:
            break
        if name not in chosen:
            chosen.append(name)
    return chosen
