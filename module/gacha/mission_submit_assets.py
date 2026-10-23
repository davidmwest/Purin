"""Exact Limited Build count and cost controls captured by Purin."""
from module.base.button import Button

_FILE = './assets/en/gacha/EVENT_BUILD_COUNT_20261023.png'
COUNT_HEADER = Button(area=(325,164,505,197), color=(105,135,184), button=(325,164,505,197), file=_FILE, name='MISSION_COUNT_HEADER')
BATCH_BUILD_TITLE = Button(area=(383,260,539,291), color=(147,153,162), button=(383,260,539,291), file=_FILE, name='MISSION_BATCH_BUILD_TITLE')
LIMITED_BUILD_COST = Button(area=(450,338,786,364), color=(157,162,168), button=(450,338,786,364), file=_FILE, name='MISSION_LIMITED_BUILD_COST')
COUNT_MINUS = Button(area=(567,253,614,296), color=(85,133,197), button=(567,253,614,296), file=_FILE, name='MISSION_COUNT_MINUS')
COUNT_PLUS = Button(area=(748,253,794,296), color=(87,134,200), button=(748,253,794,296), file=_FILE, name='MISSION_COUNT_PLUS')
COUNT_CANCEL = Button(area=(397,453,573,512), color=(196,119,110), button=(397,453,573,512), file=_FILE, name='MISSION_COUNT_CANCEL')
COUNT_CONFIRM = Button(area=(700,451,875,510), color=(108,152,205), button=(700,451,875,510), file=_FILE, name='MISSION_COUNT_CONFIRM')
COUNT_WITNESSES = (COUNT_HEADER, BATCH_BUILD_TITLE, LIMITED_BUILD_COST, COUNT_MINUS, COUNT_PLUS, COUNT_CANCEL, COUNT_CONFIRM)
