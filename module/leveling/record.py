"""
The trial of the average level of the mob fleet, the user's rule of 2026-10-20: the mob fleet of Main keeps an
average level of at least AVERAGE_STEPS[0], raised to the next step once a battle of the mob fleet is lost below it.
Which average holds is kept in ./config/leveling/<config name>.json:

    minimum: the average the mob fleet keeps now
    average: the average of the mob fleet as Leveling left it, and when
    trials: for each minimum and stage, "95@12-4", runs of Main and battles of the mob fleet lost, since when, and
        when it held for TRIAL_RUNS runs in a row without a battle lost, the averages measured then. The last runs
        with the fleets as Leveling last saw them. A run on an other stage goes into its own trial: 19 runs of 2-1
        and one of 12-4 are not 20 runs of 12-4, the Codex review of 2026-10-21. A trial of before, keyed by the
        minimum alone, is kept as it is.
    losses: battles of the mob fleet lost, with the average measured and the fleets then
    failed: averages tried before, AVERAGES_FAILED
    assessment: the fleets of Main as Leveling last left them, see module.leveling.readiness

Leveling writes the average and the assessment, Main writes each run it ends.
"""
import json
import os
from datetime import datetime

from module.leveling.plan import AVERAGES_FAILED, AVERAGE_STEPS
from module.logger import logger

TIME_FORMAT = '%Y-%m-%d %H:%M:%S'
# An average that ends this many runs in a row without a battle of the mob fleet lost holds
TRIAL_RUNS = 20
# Battles lost kept in the file
LOSSES_KEPT = 50
# Runs kept in a trial, with their fleets
RECENT_KEPT = TRIAL_RUNS


def filepath_record(config_name):
    return os.path.join('./config/leveling', f'{config_name}.json')


def new_record():
    return {'minimum': AVERAGE_STEPS[0], 'average': None, 'average_time': None, 'trials': {}, 'losses': [],
            'failed': [dict(failed) for failed in AVERAGES_FAILED]}


def load_record(config_name):
    """
    Returns:
        dict: The record, a new one if there is none or it can not be read
    """
    try:
        with open(filepath_record(config_name), encoding='utf-8') as f:
            record = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return new_record()
    if not isinstance(record, dict):
        return new_record()
    for key, value in new_record().items():
        record.setdefault(key, value)
    return record


def save_record(config_name, record):
    file = filepath_record(config_name)
    os.makedirs(os.path.dirname(file), exist_ok=True)
    tmp = f'{file}.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(record, f, indent=1, ensure_ascii=False)
    os.replace(tmp, file)


def trial_key(minimum, stage):
    return f'{minimum}@{stage}'


def trial_of(record, now, stage):
    """
    Returns:
        dict: Trial of the minimum of the record on a stage, started now if it is new
    """
    return record['trials'].setdefault(trial_key(record['minimum'], stage), {
        'minimum': record['minimum'], 'stage': stage, 'runs': 0, 'lost': 0, 'since': now.strftime(TIME_FORMAT),
        'clean': 0, 'held': None, 'low': None, 'high': None, 'recent': []})


def fleet_text(assessment):
    """
    Args:
        assessment (dict): Of module.leveling.readiness, None for none

    Returns:
        str: The fleets as Leveling last saw them, "Hakuhou 95, Gangut 99 / Hiei 109", '' if not known
    """
    if not assessment or not assessment.get('ships'):
        return ''
    fleets = {}
    for ship in assessment['ships']:
        fleet, name, level = ship[:3]
        text = f'{name} {level}' + ('' if len(ship) < 4 or ship[3] is None or ship[3] else ' no gear')
        fleets.setdefault(fleet, []).append(text)
    return ' / '.join(', '.join(ships) for _, ships in sorted(fleets.items()))


def run_context(record):
    """
    Returns:
        dict: The fleets of Main at a run, as Leveling last saw them: the average measured, the fleets
    """
    assessment = record.get('assessment') if isinstance(record.get('assessment'), dict) else None
    average = assessment.get('average') if assessment else None
    if average is None:
        average = record.get('average')
    return {'average': average, 'fleet': fleet_text(assessment)}


def record_average(record, average, now=None):
    """
    Args:
        record (dict): load_record()
        average (float): Average level of the mob fleet, as Leveling leaves it
        now (datetime):
    """
    now = now or datetime.now()
    record['average'] = round(average, 1)
    record['average_time'] = now.strftime(TIME_FORMAT)


def record_run(record, stage, mob_lost, now=None):
    """
    A run of Main has ended.

    Args:
        record (dict): load_record()
        stage (str): Stage of Main, like '12-4'
        mob_lost (bool): If a battle of the mob fleet was lost in the run
        now (datetime):

    Returns:
        bool: If the minimum went up
    """
    now = now or datetime.now()
    minimum = record['minimum']
    trial = trial_of(record, now, stage)
    context = run_context(record)
    average = context['average']
    trial['runs'] += 1
    trial['recent'] = (trial.get('recent') or []) + [
        {'time': now.strftime(TIME_FORMAT), 'average': average, 'fleet': context['fleet'], 'lost': bool(mob_lost)}]
    trial['recent'] = trial['recent'][-RECENT_KEPT:]
    if not mob_lost:
        trial['clean'] += 1
        # The averages measured over the runs in a row without a battle lost
        if average is not None:
            trial['low'] = average if trial.get('low') is None else min(trial['low'], average)
            trial['high'] = average if trial.get('high') is None else max(trial['high'], average)
        if trial['clean'] >= TRIAL_RUNS and not trial['held']:
            trial['held'] = now.strftime(TIME_FORMAT)
            logger.info(f'The mob fleet held {TRIAL_RUNS} runs of {stage} in a row with a minimum average of '
                        f'{minimum}, at average levels {trial["low"]} to {trial["high"]}')
        return False

    trial['lost'] += 1
    trial['clean'] = 0
    trial['low'] = trial['high'] = None
    record['losses'].append({'time': now.strftime(TIME_FORMAT), 'stage': stage, 'average': average,
                             'minimum': minimum, 'fleet': context['fleet']})
    record['losses'] = record['losses'][-LOSSES_KEPT:]
    higher = [step for step in AVERAGE_STEPS if step > minimum]
    if higher and (average is None or average < higher[0]):
        record['minimum'] = higher[0]
        trial_of(record, now, stage)
        logger.warning(f'A battle of the mob fleet was lost on {stage} at average level {average}: '
                       f'the minimum goes from {minimum} to {higher[0]}')
        return True
    logger.warning(f'A battle of the mob fleet was lost on {stage} at average level {average}, '
                   f'with a minimum of {minimum}')
    return False


def record_leveling_run(config, campaign):
    """
    Called by Main after each run, when Leveling is enabled: the run goes into the trial of the average.

    Args:
        config (AzurLaneConfig):
        campaign: The campaign of the run, its run_lost and run_lost_fleet tell how it went.
            run_lost_fleet is 1 for the mob fleet, 2 for the boss fleet, None if not known.
    """
    if not config.cross_get('Leveling.Scheduler.Enable', default=False):
        return
    lost = getattr(campaign, 'run_lost', False)
    fleet = getattr(campaign, 'run_lost_fleet', None)
    # A battle lost by a fleet not known is taken as one of the mob fleet, the minimum rather goes up
    mob_lost = bool(lost) and fleet != 2
    record = load_record(config.config_name)
    record_run(record, config.Campaign_Name, mob_lost)
    # The fleets were looked at a run ago more, see module.leveling.readiness
    assessment = record.get('assessment')
    if isinstance(assessment, dict):
        assessment['runs'] = (assessment.get('runs') or 0) + 1
    save_record(config.config_name, record)
