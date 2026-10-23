"""Retain the original Sleeping Sea map, grid detection and battle methods."""
from campaign.event_20241024_cn.t1 import MAP, Config as OriginalConfig, Campaign as OriginalCampaign
from .campaign_base import CampaignBase


class Config(OriginalConfig):
    MAP_CHAPTER_SWITCH_20241219 = False
    STAGE_ENTRANCE = ['normal']


class Campaign(CampaignBase, OriginalCampaign):
    MAP = MAP
    ENEMY_FILTER = OriginalCampaign.ENEMY_FILTER
