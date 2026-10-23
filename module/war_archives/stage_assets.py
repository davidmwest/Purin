"""EN stage controls witnessed on both Visitors archive captures, 2026-10-23."""
from module.base.button import Button
from module.base.template import Template

FILE = './assets/en/war_archives/ARCHIVE_STAGE_CONTROLS_20261023.png'
VISITORS_TITLE = Button(area=(214, 74, 418, 99), color=(), button=(214, 74, 418, 99), file=FILE,
                        name='ARCHIVE_VISITORS_TITLE')
ARCHIVE_NEXT = Button(area=(1197, 428, 1264, 457), color=(),
                      button=(1197, 350, 1256, 456), file=FILE, name='ARCHIVE_STAGE_NEXT')
# Explicit EN resources keep stage reading independent of process-global server
# caches. These are the same Clear/% witnesses used by CampaignOcr.
STAGE_CLEAR = Template('./assets/en/template/TEMPLATE_STAGE_CLEAR.png')
STAGE_PERCENT = Template('./assets/en/template/TEMPLATE_STAGE_PERCENT.png')
