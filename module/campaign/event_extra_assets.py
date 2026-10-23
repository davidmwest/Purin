"""Stage-owned EX lineup controls observed on 2026-10-22 at 23:42:26.

These disabled slots are a positive witness that EX permits only Fleet 1.
They must never authorize clearing a secondary or submarine fleet.
"""
from module.base.button import Button

_EX_PREPARATION = './assets/en/event_completion/TEMPESTA_EX_PREPARATION_20261022.png'

EX_DISABLED_FLEET_2 = Button(
    area=(373, 303, 974, 367), color=(55, 63, 71), button=(373, 303, 974, 367),
    file=_EX_PREPARATION, name='EX_DISABLED_FLEET_2')
EX_DISABLED_SUBMARINE = Button(
    area=(435, 438, 738, 499), color=(44, 60, 78), button=(435, 438, 738, 499),
    file=_EX_PREPARATION, name='EX_DISABLED_SUBMARINE')
