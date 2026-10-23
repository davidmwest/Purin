"""Navigation witnesses captured after actual mission construction orders.

Finish All is a recognition witness only. The only authorized action is Home.
"""
from module.base.button import Button

_FILE = './assets/en/gacha/ORDERS_RESULT_20261023.png'

ORDERS_BUILD_HEADER = Button(
    area=(111, 15, 188, 45), color=(105, 118, 159),
    button=(111, 15, 188, 45), file=_FILE, name='ORDERS_BUILD_HEADER')
ORDERS_ACTIVE = Button(
    area=(18, 265, 91, 311), color=(141, 115, 82),
    button=(18, 265, 91, 311), file=_FILE, name='ORDERS_ACTIVE')
ORDERS_BATCH_FINISH = Button(
    area=(1133, 100, 1251, 139), color=(182, 138, 83),
    button=(1133, 100, 1251, 139), file=_FILE, name='ORDERS_BATCH_FINISH')
ORDERS_HOME = Button(
    area=(1213, 16, 1247, 52), color=(97, 111, 137),
    button=(1215, 18, 1245, 49), file=_FILE, name='ORDERS_HOME')

ORDERS_WITNESSES = (ORDERS_BUILD_HEADER, ORDERS_ACTIVE, ORDERS_BATCH_FINISH, ORDERS_HOME)
