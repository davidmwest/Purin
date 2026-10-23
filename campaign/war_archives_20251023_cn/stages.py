"""Original Islas maps and battle plans on ordinary archive navigation."""
from dataclasses import replace
from campaign.event_20251023_cn.stages import STAGES as ORIGINAL


STAGES = {
    name: replace(ORIGINAL[name], base='.campaign_base:CampaignBase',
                  config={**ORIGINAL[name].config,
                          'MAP_CHAPTER_SWITCH_20241219': False,
                          'STAGE_ENTRANCE': ['normal']})
    for name in ['t1', 't2', 't3', 't4', 't5', 't6']
}
