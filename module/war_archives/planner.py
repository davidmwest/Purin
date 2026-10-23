"""Deterministic archive goals; catalog entries never establish live progress."""
from dataclasses import dataclass

from module.campaign.campaign_stars import StageGoal
from module.campaign.stage import stage_names
from module.war_archives.catalog import ARCHIVES, get_archive


@dataclass(frozen=True)
class ArchivePlan:
    archive: object
    mode: str
    stages: tuple
    prizes: tuple
    deferred_prizes: tuple = ()


def build_plan(target='all', difficulty='hard', goal='stories', prize='auto', prize_only=False):
    if difficulty not in ('easy', 'hard') or goal not in ('stories', 'items_and_stories'):
        raise ValueError('Invalid archive goal or difficulty')
    selected = ARCHIVES if str(target).strip().lower() == 'all' else (get_archive(str(target).strip()),)
    result, unsupported = [], []
    for archive in selected:
        if not archive.supported:
            unsupported.append(archive.name)
            continue
        # Some SP archives have only one route. Their E.X./S.P. list category
        # does not establish which difficulties their chapters offer.
        mode = 'hard' if difficulty == 'hard' and archive.hard_stages else 'normal'
        stages = archive.stages(mode)
        if not stages:
            raise ValueError(f'Archive has no {mode} stages: {archive.name}')
        available = set(stage_names(archive.id))
        missing = set(stages) - available
        if missing:
            raise ValueError(f'Archive maps missing for {archive.name}: {sorted(missing)}')
        prizes = archive.prizes if prize_only or goal == 'items_and_stories' else ()
        if any(item.stage not in archive.stages(item.mode) or item.stage not in available for item in prizes):
            raise ValueError(f'Archive prize stage missing for {archive.name}')
        if str(prize).strip().lower() != 'auto':
            key = str(prize).strip().casefold()
            prizes = tuple(item for item in prizes if key in (str(item.id), item.name.casefold()))
        accessible = tuple(item for item in prizes if item.mode == mode)
        deferred = tuple(item for item in prizes if item.mode != mode)
        if prize_only and not prizes:
            continue
        result.append(ArchivePlan(archive, mode,
                                  tuple(StageGoal(archive.id, stage, mode, 'archive_clear') for stage in stages),
                                  accessible, deferred))
    if str(prize).strip().lower() != 'auto' and not any(item.prizes or item.deferred_prizes for item in result):
        raise ValueError(f'No guaranteed archive prize matches {prize!r}')
    return tuple(result), tuple(unsupported)


def story_key(archive):
    return f'{archive.id}/stories'


def prize_key(archive, prize):
    return f'{archive.id}/prize/{prize.id}'


def story_complete(plan, state):
    """Either verified campaign route satisfies the story goal."""
    return (state.record(story_key(plan.archive)).get('complete') is True
            or all(state.record(stage.key).get('complete') is True for stage in plan.stages))


def next_story(plan, state):
    if story_complete(plan, state):
        return None
    return next((stage for stage in plan.stages if state.record(stage.key).get('complete') is not True), None)


def next_prize(plan, state):
    return next((prize for prize in plan.prizes
                 if state.record(prize_key(plan.archive, prize)).get('claimed') is not True), None)
