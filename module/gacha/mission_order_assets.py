"""Exact zero-cost UR-cap warning observed before the build count dialog."""
from module.base.button import Button

_FILE = './assets/en/gacha/UR_CAP_WARNING_20261023.png'
UR_CAP_TEXT = Button(area=(350, 309, 928, 383), color=(146, 152, 157),
                     button=(350, 309, 928, 383), file=_FILE, name='UR_CAP_TEXT')
UR_CAP_CANCEL = Button(area=(402, 482, 577, 541), color=(168, 169, 169),
                       button=(402, 482, 577, 541), file=_FILE, name='UR_CAP_CANCEL')
UR_CAP_CONFIRM = Button(area=(703, 482, 878, 541), color=(103, 148, 203),
                        button=(703, 482, 878, 541), file=_FILE, name='UR_CAP_CONFIRM')
