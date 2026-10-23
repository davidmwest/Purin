"""Observed EN restock title, DAILY marker and uncredited 0/1 counter."""
from module.base.button import Button

_FILE = './assets/en/dorm/RESTOCK_DAILY_20261023.png'
RESTOCK_TEXT = Button(area=(258, 130, 598, 149), color=(180, 189, 202),
                      button=(258, 130, 598, 149), file=_FILE, name='RESTOCK_TEXT')
RESTOCK_DAILY = Button(area=(176, 89, 193, 151), color=(137, 178, 214),
                       button=(176, 89, 193, 151), file=_FILE, name='RESTOCK_DAILY')
RESTOCK_ZERO = Button(area=(616, 161, 660, 183), color=(117, 129, 152),
                      button=(616, 161, 660, 183), file=_FILE, name='RESTOCK_ZERO')
