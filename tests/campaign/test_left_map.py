from unittest.mock import Mock

import pytest

from module.campaign.campaign_base import CampaignBase
from module.exception import CampaignEnd
from module.ui.page import page_main


def make_campaign(main=False, stage=False):
    campaign = CampaignBase.__new__(CampaignBase)
    campaign.ui_page_appear = Mock(side_effect=lambda page, **kwargs: main and page is page_main)
    campaign.is_in_stage = Mock(return_value=stage)
    campaign.appear = Mock(return_value=False)
    return campaign


def test_main_page_after_a_battle_ends_the_run():
    # The commander level unlocked a feature, the game shows it from the main page
    campaign = make_campaign(main=True)

    with pytest.raises(CampaignEnd):
        campaign.handle_in_stage()


def test_map_goes_on():
    campaign = make_campaign()

    assert campaign.handle_in_stage() is False
