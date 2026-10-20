from unittest.mock import Mock

import pytest

from module.campaign.campaign_base import CampaignBase
from module.combat.combat import Combat
from module.exception import CampaignEnd


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
