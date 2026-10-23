"""Ordinary archived Tempesta pages; never use the active-event mode switch."""
from campaign.war_archives_20231026_cn.campaign_base import CampaignBase as ArchiveBase


class CampaignBase(ArchiveBase):
    def campaign_set_chapter_20241219(self, chapter, stage, mode='combat'):
        return False
