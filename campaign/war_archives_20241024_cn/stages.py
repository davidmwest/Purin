"""Declarative Sleeping Sea stages; handwritten stages retain their own modules."""
from dataclasses import replace
from campaign.event_20241024_cn.stages import STAGES as ORIGINAL


STAGES = {
    name: replace(ORIGINAL[name], base='.campaign_base:CampaignBase',
                  config={**ORIGINAL[name].config,
                          'MAP_CHAPTER_SWITCH_20241219': False,
                          'STAGE_ENTRANCE': ['normal']})
    for name in ['t2', 't6']
}
