"""Exact completed Strict Maid milestone modal, observed by Purin on 2026-10-23."""
from module.base.button import Button


_FILE = './assets/en/event_completion/STRICT_MAID_MILESTONES_COMPLETED_20261023.png'


def _witness(name, area, color, button=None):
    return Button(area=area, color=color, button=button or area, file=_FILE, name=name)


MILESTONE_HEADER = _witness('MILESTONE_HEADER', (393, 131, 462, 165), (142, 164, 201))
MILESTONE_POSSESSION = _witness('MILESTONE_POSSESSION', (642, 581, 827, 609), (133, 134, 142))
MILESTONE_CLOSE = _witness('MILESTONE_CLOSE', (838, 127, 901, 168), (214, 100, 91),
                           button=(846, 131, 893, 164))
MILESTONE_GRAY_COLLECT = _witness('MILESTONE_GRAY_COLLECT', (394, 577, 548, 610), (143, 144, 144))
MILESTONE_FINAL_PHASES = (
    _witness('MILESTONE_PHASE12', (418, 247, 509, 271), (164, 166, 170)),
    _witness('MILESTONE_PHASE13', (418, 358, 509, 382), (165, 168, 171)),
    _witness('MILESTONE_PHASE14', (418, 469, 509, 493), (165, 167, 169)),
)
MILESTONE_COMPLETED = (
    _witness('MILESTONE_COMPLETED12', (793, 266, 864, 289), (91, 99, 66)),
    _witness('MILESTONE_COMPLETED13', (793, 377, 864, 400), (104, 100, 43)),
    _witness('MILESTONE_COMPLETED14', (793, 488, 864, 511), (99, 100, 71)),
)
