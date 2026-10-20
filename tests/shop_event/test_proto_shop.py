import os
from types import SimpleNamespace
from unittest.mock import Mock

import cv2
import pytest

import module.config.server as server
from module.base.utils import crop
from module.shop.shop_proto import (OCR_PROTO_BALANCE, PROTO_CARDS, PROTO_ICONS, ProtoShop_250814,
                                    TEMPLATE_SPECIALIZED_BULIN)

DATA = os.path.join(os.path.dirname(__file__), 'data')


@pytest.fixture(autouse=True)
def en():
    previous = server.server
    server.set_server('en')
    yield
    server.set_server(previous)


def load(name):
    """
    The Proto tab as Alas sees it, black except the icons, prices and balance.
    """
    return cv2.cvtColor(cv2.imread(os.path.join(DATA, name)), cv2.COLOR_BGR2RGB)


def proto_shop(enabled=True, image='proto_tab.png'):
    shop = ProtoShop_250814.__new__(ProtoShop_250814)
    shop.config = SimpleNamespace(ProtoShop_BuySpecializedBulin=enabled)
    shop.device = SimpleNamespace(image=load(image))
    return shop


def test_only_the_specialized_bulin_has_its_icon():
    """
    Its name can't tell it, it scrolls on the card and other items have 'Specialized Bulin Custom MKIII' in theirs.
    """
    image = load('proto_tab.png')

    found = [TEMPLATE_SPECIALIZED_BULIN.match(crop(image, icon.area)) for icon in PROTO_ICONS]

    assert found == [False] * 4 + [True] + [False] * 5


def test_specialized_bulin_is_found_on_the_proto_tab():
    shop = proto_shop()

    card, price = shop.proto_find_bulin()

    assert card is PROTO_CARDS[4]
    assert price == 4000
    assert OCR_PROTO_BALANCE.ocr(shop.device.image) == 34254


def test_specialized_bulin_is_found_on_any_card():
    shop = proto_shop()
    image = shop.device.image
    bulin, other = PROTO_ICONS[4].area, PROTO_ICONS[7].area
    icon = image[bulin[1]:bulin[3], bulin[0]:bulin[2]].copy()
    image[bulin[1]:bulin[3], bulin[0]:bulin[2]] = image[other[1]:other[3], other[0]:other[2]]
    image[other[1]:other[3], other[0]:other[2]] = icon

    card, price = shop.proto_find_bulin()

    assert card is PROTO_CARDS[7]
    assert price == 2000


def test_nothing_found_without_the_specialized_bulin():
    shop = proto_shop()
    x1, y1, x2, y2 = PROTO_ICONS[4].area
    shop.device.image[y1:y2, x1:x2] = 0

    assert shop.proto_find_bulin() == (None, 0)


def test_buy_popup_is_confirmed():
    """
    Its buttons sit a few pixels aside of the other popups.
    """
    shop = proto_shop()
    # A screenshot at 1280x720
    shop.device = SimpleNamespace(image=load('proto_buy_popup.png'), click=Mock(), stuck_record_add=Mock(),
                                  display_scale=1.0)
    shop.interval_timer = {}

    assert not ProtoShop_250814.handle_popup_confirm(shop, 'DEFAULT_OFFSET')
    assert shop.shop_buy_handle(PROTO_CARDS[4])
    assert shop.device.click.call_count == 1


def test_nothing_is_bought_by_default():
    shop = proto_shop(enabled=False)
    shop.shop_buy_execute = Mock()
    shop.proto_wait_shop = Mock()

    shop.run()

    shop.shop_buy_execute.assert_not_called()
