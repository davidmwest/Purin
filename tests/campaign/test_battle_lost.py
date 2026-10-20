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
