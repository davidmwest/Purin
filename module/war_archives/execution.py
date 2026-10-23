"""One observed War Archives sortie; the caller owns the shared attempt ledger.

This deliberately does not use CampaignWarArchives.run(): that wrapper enables
the old timed data-key confirmation. A navigation provider must first establish
the exact archive/stage from captured UI. Unknown observations never authorize
navigation or combat. The provider is responsible for bounding its own controls.
"""
from contextlib import contextmanager
from time import monotonic

from module.base.timer import Timer
from module.campaign.run import CampaignRun
from module.campaign.stage import stage_names
from module.config.config import TaskEnd
from module.combat.assets import GET_ITEMS_1, GET_ITEMS_2, GET_ITEMS_3, GET_SHIP
from module.exception import CampaignEnd, OilExhausted, ScriptEnd
from module.handler.assets import POPUP_CANCEL, POPUP_CONFIRM, USE_DATA_KEY
from module.handler.fast_forward import to_map_file_name
from module.logger import logger
from module.map.assets import FLEET_PREPARATION, MAP_PREPARATION, MAP_PREPARATION_CANCEL, MAP_PREPARATION_HARD
from module.war_archives.catalog import get_archive


class ArchiveBlocked(ScriptEnd):
    pass


class ArchiveInspected(ScriptEnd):
    pass


@contextmanager
def archive_overrides(config, goal, oil_floor):
    values = dict(Campaign_Name=goal.stage, Campaign_Event=goal.event, Campaign_Mode=goal.mode,
                  Campaign_UseClearMode=False, Campaign_UseAutoSearch=False, Campaign_Use2xBook=False,
                  USE_DATA_KEY=False, ShipRotation_Enable=False,
                  StopCondition_MapAchievement='100_percent_clear', StopCondition_RunCount=0,
                  StopCondition_StageIncrease=False, StopCondition_StageRetreat=0,
                  StopCondition_RetreatRemain=0, StopCondition_ThenTask='',
                  StopCondition_GetNewShip=False, StopCondition_ReachLevel=0,
                  StopCondition_OilLimit=oil_floor, EventGeneral_PtLimit=0)
    previous = {key: getattr(config, key) for key in values}
    overridden = dict(config.overridden)
    try:
        config.override(**values)
        yield
    finally:
        config.overridden.clear()
        config.overridden.update(overridden)
        for key, value in previous.items():
            object.__setattr__(config, key, value)


class WarArchivesExecution(CampaignRun):
    """``run_once`` returns a result without advancing or resetting any journal.

    ``prepare(campaign, goal)`` must use bounded known controls, set ENTRANCE,
    and return a *fresh* observation with matching account/event/stage/mode and
    ``verified_for_navigation=True``. Initial observation requires the same proof.
    ``verified_for_execution=True`` additionally authorizes combat, never an
    unknown key dialog. ``inspect_only`` needs no execution/key authorization.

    ``reserve(goal, raw_keys=cost, key_mode=mode, attempt_kind=kind)`` must persist
    the caller's shared daily/surplus budget and return literal True. No Start is
    allowed otherwise. A successful reservation is never refunded here, including
    losses, exceptions or interruptions. Raw-key pricing must come from observed
    game evidence; this module has no assumed five-key or timed-unlock default.
    """

    def _proof(self, value, execution=False):
        goal = self.goal
        return (isinstance(value, dict)
                and value.get('account') == self.config.config_name
                and value.get('event') == goal.event
                and str(value.get('stage', '')).upper() == goal.stage.upper()
                and value.get('mode') == goal.mode
                and value.get('verified_for_navigation') is True
                and (not execution or value.get('verified_for_execution') is True))

    def _check_switch(self):
        self.config.check_task_switch('War Archives safe boundary')

    def _check_oil(self):
        oil = self.get_oil(skip_first_screenshot=False)
        if type(oil) is not int or oil < self.oil_floor:
            self.defer_for_oil()
            self.oil_deferred = True
            raise ArchiveBlocked('oil_reserve')

    def _key_proof(self, value):
        return (self.key_mode == 'per_entry' and self.key_cost == 5
                and type(self.key_cost) is int
                and value.get('key_capacity') == 300
                and type(value.get('key_capacity')) is int
                and type(value.get('raw_keys')) is int
                and 5 <= value['raw_keys'] <= 300)

    def _prepare(self):
        self._check_switch()
        self.campaign.device.screenshot()
        if self.campaign.is_in_map():
            raise ArchiveBlocked('unowned_map_in_progress')
        proof = self.prepare(self.campaign, self.goal)
        if not self._proof(proof) or getattr(self.campaign, 'ENTRANCE', None) is None:
            raise ArchiveBlocked('stage_navigation_unverified')
        self.proof = proof

    def _cancel_preparation(self, campaign):
        clicks = 0
        settle = Timer(2, count=2).start()
        for _ in campaign.loop(skip_first=False, timeout=12):
            if campaign.is_in_stage():
                return True
            if (campaign.appear(POPUP_CONFIRM, offset=(20, 20))
                    or campaign.appear(POPUP_CANCEL, offset=(20, 20))):
                break
            if (clicks < 2 and settle.reached() and any(
                    campaign.appear(button, offset=(20, 50)) for button in
                    (MAP_PREPARATION, MAP_PREPARATION_HARD, FLEET_PREPARATION))):
                campaign.device.click(MAP_PREPARATION_CANCEL)
                clicks += 1
                settle.reset()
        raise ArchiveBlocked('preparation_return_unrecognized')

    def _install_guards(self):
        campaign = self.campaign
        original_enter = campaign.enter_map
        original_stop = campaign.triggered_map_stop
        original_status = campaign.combat_status
        original_oil_popup = campaign.handle_oil_purchase
        original_battle = campaign.execute_a_battle
        original_morale = campaign.emotion.check_reduce
        entry_deadline, entry_frames = None, 0

        def entry_guard():
            nonlocal entry_frames
            if entry_deadline is not None:
                entry_frames += 1
                if monotonic() >= entry_deadline or entry_frames > 120:
                    raise ArchiveBlocked('map_entry_timeout')
                if any(campaign.appear(button, offset=(20, 30)) for button in
                       (GET_ITEMS_1, GET_ITEMS_2, GET_ITEMS_3, GET_SHIP)):
                    raise ArchiveBlocked('unowned_receipt')
                if campaign.appear(USE_DATA_KEY, offset=(20, 20)):
                    raise ArchiveBlocked('unverified_data_key_dialog')
                # A generic confirm may buy keys, oil, or an unknown entitlement.
                # Let the exact oil guard cancel its known purchase dialog first.
                original_oil_popup()
                if (campaign.appear(POPUP_CONFIRM, offset=(20, 20))
                        or campaign.appear(POPUP_CANCEL, offset=(20, 20))):
                    raise ArchiveBlocked('unrecognized_entry_dialog')
                return False
            return original_oil_popup()

        def enter(*args, **kwargs):
            nonlocal entry_deadline, entry_frames
            if self.entry_calls >= 2 or (self.entry_calls and not self.inspection):
                raise ArchiveBlocked('second_sortie_forbidden')
            self.entry_calls += 1
            entry_deadline, entry_frames = monotonic() + 60, 0
            try:
                return original_enter(*args, **kwargs)
            finally:
                entry_deadline = None

        def stop():
            self.preparation_seen = True
            self.stage_complete = (self.proof.get('stage_complete') is True if self.one_clear
                                   else bool(original_stop()))
            if (self.stage_complete and self.goal.goal == 'archive_clear') or self.inspection:
                self._cancel_preparation(campaign)
                raise ArchiveInspected('complete' if self.stage_complete else 'incomplete')
            try:
                self._check_switch()
                self._check_oil()
                if not self._proof(self.proof, execution=True) or not self._key_proof(self.proof):
                    raise ArchiveBlocked('execution_unverified')
                if not self.reserved:
                    original_morale(campaign._map_battle)
                    # This callback is the last authorization before map Start.
                    if self.reserve(self.goal, raw_keys=self.key_cost, key_mode=self.key_mode,
                                    attempt_kind=self.attempt_kind) is not True:
                        raise ArchiveBlocked('reservation_rejected')
                    self.reserved = True
                    self._check_switch()
            except (ScriptEnd, TaskEnd) as error:
                try:
                    self._cancel_preparation(campaign)
                except ArchiveBlocked as cleanup_error:
                    # Failed cleanup must not replace scheduler preemption or
                    # the original morale/oil deferral with a different result.
                    error.archive_cleanup_error = str(cleanup_error)
                    logger.warning(f'Archive preparation cleanup failed: {cleanup_error}')
                raise
            return False

        def status(drop=None, expected_end=None):
            won_before, lost_before = campaign.combat_won, campaign.combat_lost
            try:
                return original_status(drop=drop, expected_end=expected_end)
            finally:
                if (expected_end == 'in_stage' and (won_before or campaign.combat_won)
                        and not (lost_before or campaign.combat_lost or campaign.run_lost)
                        and campaign.is_in_stage()):
                    self.boss_won = True

        def battle():
            if self.config.task_switched():
                self._withdraw_between_battles()
                raise TaskEnd('War Archives preempted between battles')
            if campaign.is_in_map():
                try:
                    self._check_oil()
                except ArchiveBlocked:
                    self._withdraw_between_battles()
                    raise
            return original_battle()

        def bounded_campaign_ui(name=None, mode=None, **kwargs):
            if ((name is not None and str(name).upper() != self.goal.stage.upper())
                    or (mode is not None and mode != self.goal.mode)):
                raise ArchiveBlocked('navigation_goal_changed')
            self._prepare()
            return True

        campaign.first_run = False
        campaign.ensure_campaign_ui = bounded_campaign_ui
        campaign.ui_goto_archives_campaign = lambda *args, **kwargs: bounded_campaign_ui()
        campaign.ui_goto_event = campaign.ui_goto_archives_campaign
        campaign.ui_goto_sp = campaign.ui_goto_archives_campaign
        campaign.enter_map = enter
        campaign.triggered_map_stop = stop
        campaign.combat_status = status
        campaign.handle_oil_purchase = entry_guard
        campaign.handle_use_data_key = lambda: False
        campaign.handle_map_stop = lambda: None
        campaign.handle_auto_search_continue = lambda: False
        campaign.handle_retirement = lambda: False
        campaign.execute_a_battle = battle
        campaign.enter_map_cancel = lambda *args, **kwargs: self._cancel_preparation(campaign)
        # Inspect completion before asking fleets to recover morale. The original
        # check still gates every first Start above, including DormRest deferral.
        campaign.emotion.check_reduce = lambda battle: None

    def _withdraw_between_battles(self):
        if self.campaign.is_in_map():
            try:
                self.campaign.withdraw(timeout=20)
            except CampaignEnd:
                pass

    def _result(self, status, reason=''):
        return dict(status=status, reason=reason, reserved=self.reserved,
                    boss_won=self.boss_won, stage_complete=self.stage_complete)

    def run_once(self, goal, observation, reserve, prepare, *, key_mode=None, key_cost=None,
                 attempt_kind='daily', extra_oil_reserve=5000, inspect_only=False):
        self.goal, self.reserve, self.prepare = goal, reserve, prepare
        self.reserved = self.boss_won = self.stage_complete = self.preparation_seen = False
        self.oil_deferred = False
        self.entry_calls, self.inspection = 0, inspect_only
        try:
            archive = get_archive(goal.event)
            valid_stage = (archive.supported and to_map_file_name(goal.stage) in archive.stages(goal.mode)
                           and to_map_file_name(goal.stage) in stage_names(goal.event))
        except ValueError:
            valid_stage = False
        if (self.config.config_name not in ('alas', 'f2p')
                or not goal.event.startswith('war_archives_')
                or goal.goal not in ('archive_clear', 'archive_prize')
                or goal.mode not in ('normal', 'hard')
                or not valid_stage
                or not self._proof(observation)):
            return self._result('blocked', 'navigation_unverified')
        if (attempt_kind not in ('daily', 'extra') or type(extra_oil_reserve) is not int
                or extra_oil_reserve < 0):
            return self._result('blocked', 'invalid_attempt_policy')
        if not inspect_only:
            self.key_mode, self.key_cost = key_mode, key_cost
            if not self._proof(observation, execution=True) or not self._key_proof(observation):
                return self._result('blocked', 'key_entitlement_unverified')
        self.key_mode, self.key_cost, self.attempt_kind = key_mode, key_cost, attempt_kind
        self.oil_floor = max(500, extra_oil_reserve if attempt_kind == 'extra' else 0)
        # CampaignRun's name-only cache must never reuse a stage from another archive.
        self.__dict__.pop('name', None)
        try:
            with archive_overrides(self.config, goal, self.oil_floor):
                self._check_switch()
                self.load_campaign(to_map_file_name(goal.stage), goal.event)
                self.one_clear = (not self.campaign.config.MAP_HAS_CLEAR_PERCENTAGE
                                  or self.campaign.config.MAP_IS_ONE_TIME_STAGE)
                self._install_guards()
                self._prepare()
                if self.campaign.is_in_map():
                    raise ArchiveBlocked('unowned_map_in_progress')
                # The guarded preparation boundary performs the established fleet
                # morale and oil preflight before Start. Loss still leaves the map.
                self.campaign.run()
                if self.campaign.run_lost:
                    return self._result('lost', 'battle_lost')
                if not self.reserved or not self.boss_won:
                    return self._result('blocked', 'boss_victory_unverified')
                if goal.goal == 'archive_prize':
                    return self._result('won', 'prize_counter_requires_observation')
                if self.one_clear:
                    self.stage_complete = True
                    return self._result('complete', 'one_clear_boss_victory')
                self.inspection = True
                self._prepare()
                self.campaign.enter_map(self.campaign.ENTRANCE, mode=goal.mode)
                return self._result('won', 'post_win_inspection_unverified')
        except ArchiveInspected:
            return self._result('complete' if self.stage_complete else ('won' if self.boss_won else 'incomplete'))
        except ArchiveBlocked as error:
            # defer_for_oil owns NextRun; the coordinator must not replace that
            # recovery time with its generic blocked/interrupted delay.
            return self._result('deferred' if self.oil_deferred else 'blocked', str(error))
        except OilExhausted:
            self.defer_for_oil()
            self.oil_deferred = True
            self._withdraw_between_battles()
            return self._result('deferred', 'oil_exhausted')
        except ScriptEnd as error:
            # Morale deferral preserves the existing scheduled recovery and no
            # reservation when it happens before entry. Never mark it a win.
            return self._result('deferred' if not self.reserved else 'interrupted', str(error))
        except Exception as error:
            # Preserve scheduler/recovery semantics. The caller can audit whether
            # its durable reservation was already consumed; never refund by guess.
            error.archive_reserved = self.reserved
            raise
