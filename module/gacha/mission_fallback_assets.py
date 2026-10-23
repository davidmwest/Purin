"""Ordinary build cost titles captured without submitting any orders."""
from module.base.button import Button

# The red xN and green total costs are deliberately outside these crops.
# Their changing numbers are verified separately on fresh frames.
LIGHT_BUILD_COST = Button(area=(450,338,765,363), color=(155,161,166),
    button=(450,338,765,363), file='./assets/en/gacha/LIGHT_BUILD_COUNT_20261023.png',
    name='MISSION_LIGHT_BUILD_COST')
HEAVY_BUILD_COST = Button(area=(450,338,765,363), color=(154,159,166),
    button=(450,338,765,363), file='./assets/en/gacha/HEAVY_BUILD_COUNT_20261023.png',
    name='MISSION_HEAVY_BUILD_COST')
SPECIAL_BUILD_COST = Button(area=(450,338,765,363), color=(157,161,168),
    button=(450,338,765,363), file='./assets/en/gacha/SPECIAL_BUILD_COUNT_20261023.png',
    name='MISSION_SPECIAL_BUILD_COST')

ORDINARY_COST_TITLES = {'light': LIGHT_BUILD_COST, 'heavy': HEAVY_BUILD_COST,
                        'special': SPECIAL_BUILD_COST}
