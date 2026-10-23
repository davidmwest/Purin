"""Captured EN single-item archive reward panel; no Sortie template is clickable."""
from module.base.button import Button
from module.ocr.ocr import Ocr, OcrYuv

FILE = './assets/en/war_archives/ARCHIVE_PRIZE_PANEL_20261023.png'


def _button(area, name):
    return Button(area=area, button=area, color=(), file=FILE, name=name)


PRIZE_PANEL_INFO = _button((354, 181, 423, 214), 'ARCHIVE_PRIZE_PANEL_INFO')
PRIZE_PANEL_CLOSE = _button((858, 172, 929, 221), 'ARCHIVE_PRIZE_PANEL_CLOSE')
PRIZE_PANEL_FRAME = _button((338, 544, 943, 559), 'ARCHIVE_PRIZE_PANEL_FRAME')
PRIZE_PANEL_CAPTION = _button((391, 498, 574, 524), 'ARCHIVE_PRIZE_PANEL_CAPTION')
PRIZE_PANEL_ACTION = _button((745, 481, 917, 540), 'ARCHIVE_PRIZE_PANEL_ACTION')
# Coordinates are shared by Sortie and Accept. The caller must freshly read
# literal Accept and the complete catalog-bound 60/60 panel before this click.
PRIZE_PANEL_ACCEPT = _button((744, 480, 918, 541), 'ARCHIVE_PRIZE_PANEL_ACCEPT')
PRIZE_PANEL_NAME = OcrYuv((570, 249, 925, 283), lang='cnocr', threshold=96,
                         name='ARCHIVE_PRIZE_PANEL_NAME')
PRIZE_PANEL_ACTION_TEXT = Ocr((743, 485, 918, 537), lang='cnocr', threshold=96,
                             name='ARCHIVE_PRIZE_PANEL_ACTION')
