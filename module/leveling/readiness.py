"""
If Main may farm with its fleets, the rules Leveling keeps them to. The Codex review of 2026-10-21: Leveling
repaired what it could, then Main went on all the same, with a mob fleet below its minimum average, or a ship at 120
kept for want of an other.

Leveling ends each run with an assessment of the fleets of Main, kept under 'assessment' in the record of the trial
(module.leveling.record):
    time: when
    fleets: [the mob fleets, the boss fleets]
    stage: the stage of Main then
    version: READINESS_VERSION, of the rules
    minimum: the minimum average of the mob fleet then
    average: the average level of the mob fleet
    ok: if the fleets keep the rules: the mob fleet at its minimum average, the ships of the boss fleet at CARRY_LEVEL
        and above, no ship at FARM_LEVEL_MAX and above in either
    reasons: the rules broken, in words, then what kept Leveling from repairing them
    near_cap: if a ship is NEAR_CAP levels or less below FARM_LEVEL_MAX, a run can take her to it
    runs: runs of Main since, written as they end
    ships: the ships of the fleets, [fleet, name, level, geared], geared None if not known

Main asks readiness() before each run, see main_readiness(). An assessment missing, of other fleets, rules or
minimum, older than ASSESSMENT_MAX_AGE, or of too many runs ago is stale: Leveling runs first. One that is not ok
holds Main until the next run of Leveling, which comes in RECOVERY_MINUTES then.
"""
from datetime import datetime, timedelta

from module.leveling.plan import CARRY_LEVEL, FARM_LEVEL_MAX, campaign_fleets, fleet_average
from module.leveling.record import TIME_FORMAT, load_record

READINESS_VERSION = 1
# A ship this many levels below FARM_LEVEL_MAX or less can reach it in a run: the fleets are looked at after each run
NEAR_CAP = 2
# Runs of Main an assessment is good for, a ship may have been changed by hand
RUNS_PER_ASSESSMENT = 5
ASSESSMENT_MAX_AGE = timedelta(hours=4)
# Leveling comes back this soon when the fleets break the rules
RECOVERY_MINUTES = 30

READY = 'ready'
STALE = 'stale'
BLOCKED = 'blocked'


def assess_fleets(mob_slots, boss_slots, minimum, fleets=None, stage=None, problems=(), geared=None, now=None):
    """
    Args:
        mob_slots (list[Slot]): Slots of the mob fleet, as Leveling leaves them
        boss_slots (list[Slot]): Slots of the boss fleet
        minimum (float): Average level the mob fleet keeps
        fleets (tuple[list[int], list[int]]): The mob and the boss fleets, campaign_fleets()
        stage (str): Stage of Main
        problems (list[str]): What kept Leveling from repairing the fleets, said with the rules broken
        geared (dict[int, bool]): If the ship in a slot is geared, by id() of the slot
        now (datetime):

    Returns:
        dict: The assessment, see the docstring of this module
    """
    now = now or datetime.now()
    slots = list(mob_slots) + list(boss_slots)
    reasons = []
    average = fleet_average(mob_slots)
    if mob_slots and average < minimum:
        reasons.append(f'the mob fleet is at average level {average:.1f}, below its minimum {minimum}')
    for slot in slots:
        if slot.name and slot.level >= FARM_LEVEL_MAX:
            reasons.append(f'{slot.name} Lv.{slot.level} is in fleet {slot.fleet}, '
                           f'the fleets that farm hold ships under {FARM_LEVEL_MAX}')
    for slot in boss_slots:
        if slot.name and 0 < slot.level < CARRY_LEVEL:
            reasons.append(f'{slot.name} Lv.{slot.level} is in the boss fleet {slot.fleet}, below {CARRY_LEVEL}')
    ok = not reasons
    if not ok:
        reasons += [problem for problem in problems if problem not in reasons]
    near_cap = any(slot.name and FARM_LEVEL_MAX - NEAR_CAP <= slot.level < FARM_LEVEL_MAX for slot in slots)
    mob, boss = fleets if fleets is not None else (sorted({slot.fleet for slot in mob_slots}),
                                                   sorted({slot.fleet for slot in boss_slots}))
    return {
        'time': now.strftime(TIME_FORMAT),
        'fleets': [list(mob), list(boss)],
        'stage': stage,
        'version': READINESS_VERSION,
        'minimum': minimum,
        'average': round(average, 1),
        'ok': ok,
        'reasons': reasons,
        'near_cap': near_cap,
        'runs': 0,
        'ships': [[slot.fleet, slot.name, slot.level, (geared or {}).get(id(slot))] for slot in slots],
    }


def readiness(record, fleets, now=None):
    """
    Args:
        record (dict): The record of the trial, module.leveling.record.load_record()
        fleets (tuple[list[int], list[int]]): The mob and the boss fleets of Main, campaign_fleets()
        now (datetime):

    Returns:
        tuple[str, str]: READY; STALE, Leveling looks at the fleets first; or BLOCKED, Main holds. And why.
    """
    now = now or datetime.now()
    assessment = record.get('assessment')
    if not assessment:
        return STALE, 'the fleets were not looked at yet'
    if assessment.get('version') != READINESS_VERSION:
        return STALE, 'the fleets were looked at with other rules'
    if assessment.get('fleets') != [list(fleets[0]), list(fleets[1])]:
        return STALE, f'fleets {assessment.get("fleets")} were looked at, not {[list(fleets[0]), list(fleets[1])]}'
    if assessment.get('minimum') != record.get('minimum'):
        return STALE, f'the minimum average went from {assessment.get("minimum")} to {record.get("minimum")}'
    try:
        age = now - datetime.strptime(assessment.get('time') or '', TIME_FORMAT)
    except ValueError:
        return STALE, 'the time of the assessment can not be read'
    if age > ASSESSMENT_MAX_AGE:
        return STALE, f'the fleets were looked at {age} ago'
    runs = assessment.get('runs') or 0
    if assessment.get('near_cap') and runs >= 1:
        return STALE, f'a ship was near level {FARM_LEVEL_MAX}, {runs} runs ago'
    if runs >= RUNS_PER_ASSESSMENT:
        return STALE, f'the fleets were looked at {runs} runs ago'
    if not assessment.get('ok'):
        return BLOCKED, '; '.join(assessment.get('reasons') or ['the fleets break the rules'])
    return READY, ''


def main_fleets(config):
    """
    Returns:
        tuple[list[int], list[int]]: The mob and the boss fleets of Main
    """
    return campaign_fleets(
        config.cross_get('Main.Fleet.Fleet1', default=1),
        config.cross_get('Main.Fleet.Fleet2', default=0),
        config.cross_get('Main.Fleet.FleetOrder', default='fleet1_mob_fleet2_boss'),
    )


def main_readiness(config, now=None):
    """
    Args:
        config (AzurLaneConfig):
        now (datetime):

    Returns:
        tuple[str, str]: As readiness(), READY if Leveling is off
    """
    if not config.cross_get('Leveling.Scheduler.Enable', default=False):
        return READY, ''
    return readiness(load_record(config.config_name), main_fleets(config), now=now)


def leveling_next_run(config):
    """
    Returns:
        datetime: When Leveling runs next, None if not known
    """
    next_run = config.cross_get('Leveling.Scheduler.NextRun', default=None)
    if isinstance(next_run, datetime):
        return next_run
    try:
        return datetime.strptime(str(next_run), TIME_FORMAT)
    except ValueError:
        return None
