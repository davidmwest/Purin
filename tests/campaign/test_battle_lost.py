from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from module.campaign.campaign_base import CampaignBase
from module.campaign.run import CampaignRun
from module.combat.combat import Combat
from module.exception import CampaignEnd
from module.ui.page import page_main


def make_campaign(monkeypatch, lost):
    def combat(self, *args, **kwargs):
        self.combat_lost = lost

    monkeypatch.setattr(Combat, 'combat', combat)
    campaign = CampaignBase.__new__(CampaignBase)
    campaign.withdraw = Mock(side_effect=CampaignEnd('Withdraw'))
    return campaign


def test_battle_lost_leaves_the_map(monkeypatch):
    # The only fleet may have sunk, the map would have no fleet to go on with
    campaign = make_campaign(monkeypatch, lost=True)

    with pytest.raises(CampaignEnd):
        campaign.combat()

    campaign.withdraw.assert_called_once_with()
    assert campaign.run_lost is True


def test_battle_won_goes_on(monkeypatch):
    campaign = make_campaign(monkeypatch, lost=False)

    campaign.combat()

    campaign.withdraw.assert_not_called()
    assert campaign.run_lost is False


def test_defeat_before_cleanup_still_withdraws_after_normal_map_return(monkeypatch):
    def cleanup(self, **kwargs):
        # Generic cleanup resets this flag; the result can already have disappeared.
        self.combat_lost = False

    def combat(self, *args, **kwargs):
        self.combat_lost = True
        self.combat_status()

    monkeypatch.setattr(Combat, 'combat', combat)
    monkeypatch.setattr(Combat, 'combat_status', cleanup)
    campaign = CampaignBase.__new__(CampaignBase)
    campaign.run_lost = False
    campaign.withdraw = Mock(side_effect=CampaignEnd('Withdraw'))

    with pytest.raises(CampaignEnd):
        campaign.combat()

    assert campaign.combat_lost is False
    assert campaign.run_lost is True
    campaign.withdraw.assert_called_once_with()


def make_lost_on_map(lost):
    campaign = CampaignBase.__new__(CampaignBase)
    campaign.combat_lost = lost
    campaign.ui_page_appear = Mock(return_value=False)
    campaign.is_in_map = Mock(return_value=True)
    campaign.is_in_stage = Mock(return_value=False)
    campaign.appear = Mock(return_value=False)
    campaign.withdraw = Mock(side_effect=CampaignEnd('Withdraw'))
    return campaign


def test_boss_lost_with_ships_left_leaves_the_map():
    # The fleet is back on the map beside the boss, the task waits for the list of stages
    campaign = make_lost_on_map(lost=True)

    with pytest.raises(CampaignEnd):
        campaign.handle_in_stage()

    campaign.withdraw.assert_called_once_with()
    assert campaign.run_lost is True
    # withdraw() calls handle_in_stage() as well
    assert campaign.combat_lost is False


def test_map_after_a_battle_won_is_left_alone():
    campaign = make_lost_on_map(lost=False)

    assert campaign.handle_in_stage() is False

    campaign.withdraw.assert_not_called()


def test_boss_defeat_survives_cleanup_reset_and_withdraws_without_recursing():
    # Boss cleanup waits for in_stage, but a surviving fleet returns to the map. The
    # earlier D must end that wait even when its result screen is already gone.
    campaign = make_lost_on_map(lost=True)
    campaign.run_lost = False
    campaign.device = SimpleNamespace(
        screenshot_interval_set=Mock(), stuck_record_clear=Mock(), click_record_clear=Mock())
    campaign.loop = lambda: iter([None])
    campaign.in_stage_timer = SimpleNamespace(reset=Mock())

    def withdraw():
        # The withdrawal's own stage check still sees the map; it must not withdraw again.
        assert campaign.combat_lost is False
        assert campaign.handle_in_stage() is False
        raise CampaignEnd('Withdraw')

    campaign.withdraw = Mock(side_effect=withdraw)

    with pytest.raises(CampaignEnd):
        campaign.combat_status(expected_end='in_stage')

    campaign.withdraw.assert_called_once_with()
    assert campaign.run_lost is True
    assert campaign._loss_withdraw_active is False


@pytest.mark.parametrize('detected_before_cleanup', [True, False])
@pytest.mark.parametrize('destination', ['stage', 'main'])
def test_direct_ui_return_preserves_defeat_for_end_on_loss_and_retreat(detected_before_cleanup, destination):
    # F2P T3, 2026-10-22: D was read, but the sunk fleet returned directly to the stage
    # list and raised CampaignEnd before combat() could record run_lost. Test real cleanup,
    # both when execute saw D first and when only cleanup sees it.
    campaign = CampaignBase.__new__(CampaignBase)
    campaign.combat_lost = detected_before_cleanup
    campaign.run_lost = False
    campaign.interval_timer = {}
    campaign.device = SimpleNamespace(
        screenshot_interval_set=Mock(), stuck_record_clear=Mock(), click_record_clear=Mock())
    frame = ['']

    def frames():
        for scene in ([destination] if detected_before_cleanup else ['result', destination]):
            frame[0] = scene
            yield

    campaign.loop = frames
    campaign.ui_page_appear = Mock(side_effect=lambda page: frame[0] == 'main' and page is page_main)
    campaign.is_in_map = Mock(return_value=False)
    campaign.is_in_stage = Mock(side_effect=lambda: frame[0] == 'stage')
    campaign.in_stage_timer = SimpleNamespace(reached=Mock(return_value=True), reset=Mock())
    campaign.ensure_no_info_bar = Mock()
    campaign.appear = Mock(return_value=False)
    campaign.withdraw = Mock()

    def result(**kwargs):
        if frame[0] != 'result':
            return False
        campaign.combat_lost = True
        return True

    campaign.handle_battle_status = result
    for name in ['handle_in_map_with_enemy_searching', 'handle_story_skip', 'handle_get_ship',
                 'handle_get_items', 'handle_popup_confirm', 'handle_exp_info', 'appear_then_click',
                 'handle_urgent_commission', 'handle_guild_popup_cancel', 'handle_vote_popup',
                 'handle_mission_popup_ack', 'handle_auto_search_exit', 'handle_combat_mis_click']:
        setattr(campaign, name, Mock(return_value=False))

    with pytest.raises(CampaignEnd):
        campaign.combat_status(expected_end='with_searching')

    assert campaign.run_lost is True
    campaign.withdraw.assert_not_called()

    run = CampaignRun.__new__(CampaignRun)
    run.campaign = campaign
    campaign.call_then_task = Mock()
    run.config = SimpleNamespace(
        StopCondition_EndOnLoss=True, Scheduler_Enable=True, Campaign_Name='T3',
        StopCondition_StageIncrease=True, StopCondition_StageRetreat=3,
        StopCondition_RetreatFrom='', StopCondition_RetreatRemain=0)
    assert run.handle_end_on_loss() is True
    assert run.config.Scheduler_Enable is False
    campaign.call_then_task.assert_called_once_with()
    assert run.handle_stage_retreat() is True
    assert run.config.Campaign_Name == 'T2'
    assert (run.config.StopCondition_RetreatFrom, run.config.StopCondition_RetreatRemain) == ('T3', 3)


def test_direct_winning_return_does_not_mark_run_lost(monkeypatch):
    campaign = CampaignBase.__new__(CampaignBase)
    campaign.run_lost = False
    campaign.combat_lost = False
    monkeypatch.setattr(Combat, 'combat_status', Mock(side_effect=CampaignEnd('In stage.')))

    with pytest.raises(CampaignEnd):
        campaign.combat_status()

    assert campaign.run_lost is False
