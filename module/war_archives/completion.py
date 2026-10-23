"""Resumable archive routes and guaranteed prizes through the normal scheduler."""
from datetime import datetime

from module.base.base import ModuleBase
from module.campaign.campaign_stars import StageGoal
from module.config.utils import get_server_last_update
from module.exception import HardNotSatisfied
from module.logger import logger
from module.war_archives.catalog import get_archive
from module.war_archives.execution import WarArchivesExecution
from module.war_archives.planner import build_plan, next_prize, next_story, prize_key, story_key
from module.war_archives.progress import ArchiveProgress
from module.war_archives.state import ArchiveState


def verified_prize(value, prize):
    """Ownership or a local run count never establishes a guaranteed claim."""
    if not isinstance(value, dict) or value.get('returned_stage') is not True:
        return False
    if (isinstance(value, dict) and value.get('verified') is True
            and value.get('claimed') is True and value.get('count') is None
            and value.get('proof_kind') == 'source_received_hidden_button'
            and value.get('reason') == 'prize_already_claimed'
            and value.get('archive') == 'war_archives_20190321_en'
            and value.get('prize_id') == prize.id == 307061
            and value.get('returned_stage') is True
            and isinstance(value.get('diagnostics'), list) and value['diagnostics']):
        # The client hides the sole reward after receipt. This exact, settled
        # Visitors fixture proves receipt without inventing a 60/60 counter.
        return get_archive(value['archive']).prizes == (prize,)
    return (isinstance(value, dict) and value.get('verified') is True
            and type(value.get('count')) is int and type(value.get('total')) is int
            and value['total'] == prize.clears and 0 <= value['count'] <= value['total']
            and type(value.get('claimed')) is bool
            and (not value['claimed'] or value['count'] == value['total']))


class ArchiveCoordinator:
    """One bounded scheduler chunk, with injected UI and execution adapters."""

    def __init__(self, config, state, observer, runner_factory):
        self.config, self.state, self.observer = config, state, observer
        self.runner_factory = runner_factory
        self.period = get_server_last_update(config.Scheduler_ServerUpdate).isoformat()

    def sync_list(self, observation, plans):
        """Only an identity-bound 100% reading can skip an entire story route."""
        by_id = {plan.archive.id: plan.archive for plan in plans}
        for page in observation.get('pages', []):
            for card in page.get('cards', []):
                archive = by_id.get(card.get('archive'))
                if (archive is not None and card.get('completion_verified') is True
                        and type(card.get('completion')) is int and card['completion'] == 100):
                    self.state.update(story_key(archive), complete=True,
                                      evidence=dict(list_observation=observation.get('journal'), card=card),
                                      observed=datetime.now().isoformat())

    def _reserve(self, runner, goal, raw_keys, key_mode, attempt_kind):
        if key_mode != 'per_entry' or raw_keys != 5:
            return False
        return self.state.reserve(
            self.period, goal.key, oil=runner.get_oil(skip_first_screenshot=False),
            raw_keys=runner.proof.get('raw_keys'), key_cost=raw_keys,
            daily_tickets=self.config.Archive_DailyTickets,
            extra_reserve=self.config.Archive_ExtraOilReserve)

    def _safe_target_failure(self, value, goal):
        """Only a fresh exact stage page can permit trying another archive."""
        if (not isinstance(value, dict) or value.get('status') != 'blocked'
                or value.get('reason') not in ('target_locked_or_unreadable', 'hard_lineup')
                or value.get('reserved') is not False):
            return False
        proof = value.get('safe_target_return')
        return (isinstance(proof, dict) and proof.get('verified') is True
                and proof.get('proof_kind') == 'fresh_exact_archive_stage'
                and proof.get('account') == self.config.config_name
                and proof.get('event') == goal.event and proof.get('mode') == goal.mode
                and proof.get('stage') == goal.stage.lower()
                and type(proof.get('page')) is int and proof['page'] >= 0)

    @staticmethod
    def _prize_failure(value, fallback):
        """Keep an adapter's failed return visible and never act through it."""
        if isinstance(value, dict) and value.get('returned_stage') is not True:
            reason = value.get('reason')
            return dict(status='blocked', reason=(reason if isinstance(reason, str) and reason
                                                  else 'prize_return_unverified'), reserved=False)
        return dict(status='blocked', reason=fallback, reserved=False)

    def _attempt(self, goal, inspect_only=False):
        before = self.state.used(self.period)
        try:
            proof = self.observer.proof_for_goal(goal)
            if isinstance(proof, dict) and self._safe_target_failure(dict(proof, reserved=False), goal):
                return dict(status='blocked', reason=proof['reason'], reserved=False,
                            safe_target_return=proof['safe_target_return'])
            runner = self.runner_factory()
            kind = ('daily' if before < self.config.Archive_DailyTickets else 'extra')
            result = runner.run_once(
                goal, proof,
                lambda selected, **values: (False if inspect_only else self._reserve(runner, selected, **values)),
                self.observer.prepare_goal, key_mode='per_entry', key_cost=5,
                attempt_kind=kind, extra_oil_reserve=self.config.Archive_ExtraOilReserve,
                inspect_only=inspect_only)
        except HardNotSatisfied as error:
            reserved = self.state.used(self.period) > before
            if reserved:
                self.state.finish(self.period, goal.key, 'uncertain', reason='hard_lineup')
            result = dict(status='blocked', reason='hard_lineup', reserved=reserved,
                          diagnostic=str(error)[:500])
            logger.attr('WarArchivesStage', dict(goal=goal.key, **result))
            return result
        except Exception:
            if self.state.used(self.period) > before:
                self.state.finish(self.period, goal.key, 'uncertain', reason='exception')
            raise
        if result.get('reserved'):
            outcome = ('won' if result.get('boss_won') else
                       'lost' if result.get('status') == 'lost' else 'uncertain')
            self.state.finish(self.period, goal.key, outcome, result=result)
        if goal.goal == 'archive_clear' and result.get('stage_complete') is True:
            self.state.update(goal.key, complete=True, evidence=result,
                              observed=datetime.now().isoformat())
        logger.attr('WarArchivesStage', dict(goal=goal.key, **result))
        return result

    def _prize(self, plan, prize, observe_only):
        key = prize_key(plan.archive, prize)
        observed = self.observer.observe_prize(plan.archive, prize)
        if not verified_prize(observed, prize):
            return self._prize_failure(observed, 'prize_counter_unverified')
        self.state.update(key, **observed)
        if observed['claimed']:
            return dict(status='complete', reserved=False)
        if observed['count'] == observed['total']:
            if observe_only:
                return dict(status='observed', reason='prize_ready', reserved=False)
            result = self.observer.claim_prize(plan.archive, prize)
            if verified_prize(result, prize) and result['claimed']:
                self.state.update(key, **result)
                return dict(status='complete', reserved=False)
            return self._prize_failure(result, 'prize_claim_unverified')
        goal = StageGoal(plan.archive.id, prize.stage, prize.mode, 'archive_prize')
        result = self._attempt(goal, inspect_only=observe_only)
        if result.get('boss_won'):
            refreshed = self.observer.observe_prize(plan.archive, prize)
            if (not verified_prize(refreshed, prize)
                    or refreshed['count'] != min(prize.clears, observed['count'] + 1)):
                failure = self._prize_failure(refreshed, 'prize_increment_unverified')
                return dict(result, status='blocked', reason=failure['reason'])
            self.state.update(key, **refreshed)
        return result

    def run(self, plans, observe_only=False):
        # Inspections do not consume the daily budget, but remain bounded so a
        # mature account cannot monopolize the scheduler while skipping old maps.
        sortie_limit = max(1, min(int(self.config.Archive_StagesPerRun), 20))
        sorties, inspections = 0, 0
        deferred_archives = []
        for plan in plans:
            if plan.deferred_prizes:
                logger.info(f'{plan.archive.name}: guaranteed prizes need another difficulty: '
                            + ', '.join(item.name for item in plan.deferred_prizes))
            while inspections < 48 and sorties < sortie_limit:
                if self.config.task_switched():
                    return dict(status='deferred', sorties=sorties)
                goal = next_story(plan, self.state)
                target_goal = goal
                if goal is not None:
                    result = self._attempt(goal, inspect_only=observe_only)
                    if next_story(plan, self.state) is None:
                        self.state.update(story_key(plan.archive), complete=True,
                                          observed=datetime.now().isoformat())
                else:
                    prize = next_prize(plan, self.state)
                    if prize is None:
                        break
                    target_goal = StageGoal(plan.archive.id, prize.stage, prize.mode, 'archive_prize')
                    result = self._prize(plan, prize, observe_only)
                inspections += 1
                sorties += bool(result.get('reserved'))
                if not observe_only and self._safe_target_failure(result, target_goal):
                    deferred_archives.append(dict(archive=plan.archive.id, reason=result['reason'],
                                                  safe_target_return=result['safe_target_return']))
                    logger.warning(f'{plan.archive.name}: target unavailable on verified stage page; '
                                   'trying next archive')
                    break
                if observe_only:
                    return dict(result, status='observed' if result['status'] in
                                ('complete', 'incomplete', 'observed') else result['status'], sorties=sorties)
                if result['status'] not in ('complete', 'won'):
                    return dict(result, sorties=sorties)
            if inspections >= 48 or sorties >= sortie_limit:
                return dict(status='pending', sorties=sorties, **(
                    dict(deferred_archives=deferred_archives) if deferred_archives else {}))
        if deferred_archives:
            reason = ('hard_lineup' if all(item.get('reason', 'hard_lineup') == 'hard_lineup'
                                          for item in deferred_archives) else 'target_unavailable')
            return dict(status='blocked', reason=reason, sorties=sorties,
                        deferred_archives=deferred_archives)
        deferred_prizes = sum(len(plan.deferred_prizes) for plan in plans)
        if deferred_prizes:
            return dict(status='deferred_prizes', reason='prizes_require_other_difficulty',
                        sorties=sorties, deferred_prizes=deferred_prizes)
        return dict(status='complete', sorties=sorties,
                    deferred_prizes=0)


class WarArchivesComplete(ModuleBase):
    def run(self, prize_only=False):
        plans, unsupported = build_plan(
            self.config.Archive_Target, self.config.Archive_Difficulty,
            self.config.Archive_Goal, self.config.Archive_Prize, prize_only)
        if unsupported:
            logger.warning('Archives awaiting map support: ' + ', '.join(unsupported))
        if not plans:
            # An unsupported-only selection has no executable route. Empty
            # iteration must not turn that into a completion claim.
            logger.attr('WarArchivesResult', dict(status='unsupported', sorties=0,
                                                 unsupported_archives=unsupported))
            self.config.task_delay(server_update=True)
            return
        observer = ArchiveProgress(config=self.config, device=self.device)
        state = ArchiveState(self.config.config_name)
        coordinator = ArchiveCoordinator(
            self.config, state, observer,
            lambda: WarArchivesExecution(config=self.config, device=self.device))
        if self.config.Archive_ObserveOnly and plans:
            # Probe the selected goal directly; a full list census can take
            # several minutes and is not needed for identity-bound navigation.
            # The runner inspects preparation without Fleet Go or a reservation.
            proof = observer.proof_for_goal(plans[0].stages[0])
            logger.attr('WarArchivesNavigationProbe', proof)
            if proof.get('verified_for_navigation') is True:
                goal = plans[0].stages[0]
                runner = WarArchivesExecution(config=self.config, device=self.device)
                result = runner.run_once(
                    goal, proof, lambda *args, **kwargs: False,
                    observer.prepare_goal, key_mode='per_entry', key_cost=5,
                    inspect_only=goal.mode != 'hard', inspect_fleet_only=goal.mode == 'hard')
                logger.attr('WarArchivesPreparationProbe', dict(goal=goal.key, **result))
                if (result.get('status') in ('complete', 'incomplete', 'observed')
                        and self.config.Archive_Goal == 'items_and_stories'
                        and plans[0].archive.prizes):
                    prize = plans[0].archive.prizes[0]
                    logger.attr('WarArchivesPrizeProbe', observer.observe_prize(plans[0].archive, prize))
            self.config.task_delay(success=False)
            return
        if state.record('_list_scan').get('period') != coordinator.period:
            observation = observer.run_observe()
            coordinator.sync_list(observation, plans)
            if observation.get('status') != 'observed':
                logger.warning('Archive list scan incomplete; requiring fresh per-target proof: '
                               + str(observation.get('reason')))
            else:
                state.update('_list_scan', period=coordinator.period, journal=observation.get('journal'))
        result = coordinator.run(plans, observe_only=self.config.Archive_ObserveOnly)
        if unsupported:
            result['unsupported_archives'] = unsupported
        logger.attr('WarArchivesResult', result)
        if result['status'] == 'deferred':
            # Morale/preemption owns its already scheduled retry.
            return
        if result['status'] in ('complete', 'deferred_prizes'):
            self.config.task_delay(server_update=True)
        elif result['status'] == 'pending':
            self.config.task_delay(minute=1)
        elif result['status'] in ('oil_exhausted', 'lost', 'interrupted'):
            self.config.task_delay(minute=120)
        else:
            self.config.task_delay(success=False)
