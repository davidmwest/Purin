import os
from collections import Counter
from types import SimpleNamespace

import numpy as np
import pytest

from module.base.utils import load_image
from module.leveling.enhance import (ENHANCE_FILL, ENHANCE_FILL_AREA, ENHANCE_PICKER, ENHANCE_PICKER_AREA,
                                     ENHANCE_SLOT_AREA, ENHANCE_SLOT_EMPTY, SIMILARITY, material_fits, template_score)
from module.leveling.plan import ShipMatcher, load_ship_data, name_key

DATA = os.path.join(os.path.dirname(__file__), 'data')


@pytest.fixture(scope='module')
def matcher():
    return ShipMatcher(load_ship_data())


def card(level=1, rarity='common', fleet=0, status='free'):
    return SimpleNamespace(level=level, rarity=rarity, fleet=fleet, status=status)


def census(*names):
    return Counter(name_key(name) for name in names)


def test_a_ship_alone_of_her_name_is_material(matcher):
    # McCall, common, no retrofit
    assert material_fits('McCall', card(), census('McCall', 'Aoba', 'Aoba'), matcher) == ''


def test_a_ship_with_a_copy_is_kept_for_limit_breaks(matcher):
    # Two Aobas: one limit breaks the other. Fill took one of them.
    assert material_fits('Aoba', card(), census('Aoba', 'Aoba'), matcher)


def test_the_copy_of_a_ship_of_a_fleet_is_kept(matcher):
    # Hammann of fleet 1 and her copy in the dock: the census has both
    assert material_fits('Hammann', card(), census('Hammann', 'Hammann'), matcher)


@pytest.mark.parametrize('name, ship', [
    # Levelled for commissions
    ('McCall', card(level=12)),
    ('McCall', card(rarity='elite')),
    ('McCall', card(fleet=2)),
    ('McCall', card(status='commission')),
])
def test_other_ships_kept(matcher, name, ship):
    assert material_fits(name, ship, census(name), matcher)


def test_a_ship_with_a_retrofit_is_kept(matcher):
    # Cassin has a retrofit, the long-term core of the account
    assert material_fits('Cassin', card(), census('Cassin'), matcher) == 'has a retrofit'


def test_a_bulin_is_kept(matcher):
    assert material_fits('Universal Bulin', card(rarity='rare'), census('Universal Bulin'), matcher) == 'a Bulin'


def test_a_name_not_read_is_kept(matcher):
    assert material_fits('', card(), census(''), matcher)


def screen(file, left, top):
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    part = load_image(os.path.join(DATA, file))
    image[top:top + part.shape[0], left:left + part.shape[1]] = part
    return image


def test_enhance_tab_and_its_first_slot():
    fill, slot = load_image(ENHANCE_FILL), load_image(ENHANCE_SLOT_EMPTY)
    empty = screen('enhance_tab_empty.png', 700, 370)
    assert template_score(empty, fill, ENHANCE_FILL_AREA) > SIMILARITY
    assert template_score(empty, slot, ENHANCE_SLOT_AREA) > SIMILARITY
    # Fill put 8 ships in the slots
    filled = screen('enhance_tab_filled.png', 700, 370)
    assert template_score(filled, fill, ENHANCE_FILL_AREA) > SIMILARITY
    assert template_score(filled, slot, ENHANCE_SLOT_AREA) < SIMILARITY


def test_the_picker_of_materials():
    picker = load_image(ENHANCE_PICKER)
    assert template_score(screen('enhance_picker_bar.png', 20, 630), picker, ENHANCE_PICKER_AREA) > SIMILARITY
    assert template_score(screen('enhance_tab_empty.png', 700, 370), picker, ENHANCE_PICKER_AREA) < SIMILARITY


# The EN Confirm of popups, the assets of tests are those of CN
POPUP_CONFIRM_EN = dict(area=(707, 487, 874, 537), color=(105, 151, 207), button=(707, 487, 874, 537),
                        name='POPUP_CONFIRM')


def popup_controller(image, cancel_found):
    from unittest.mock import Mock
    from module.handler.assets import POPUP_CANCEL
    from module.leveling.enhance import FleetEnhance
    controller = SimpleNamespace(
        device=SimpleNamespace(image=image, click=Mock()),
        _popup_offset=(3, 30),
        handle_popup_confirm=Mock(return_value=False),
        handle_popup_cancel=Mock(return_value=False),
        # The template of Cancel found it at 0.97 on the F2P account, its EN asset is not loaded in tests
        appear=lambda button, offset=0, interval=0: cancel_found and button is POPUP_CANCEL,
    )
    return controller, FleetEnhance


def test_the_elite_material_popup_is_confirmed():
    # Laffey's copy on the F2P account: "You've selected a Elite and above ship. Continue?", its Confirm scores 0.74
    # under its template at 2560x1440 brought to 1280x720, handle_popup_confirm() did not take it
    from unittest.mock import patch
    from module.base.button import Button
    confirm = Button(**POPUP_CONFIRM_EN)
    with patch('module.leveling.enhance.POPUP_CONFIRM', confirm):
        controller, FleetEnhance = popup_controller(screen('popup_elite_material.png', 300, 160), cancel_found=True)
        assert FleetEnhance.handle_popup_confirm_loose(controller, 'LIMIT_BREAK_MATERIAL') is True
        controller.device.click.assert_called_once_with(confirm)


def test_no_popup_no_tap():
    from unittest.mock import patch
    from module.base.button import Button
    with patch('module.leveling.enhance.POPUP_CONFIRM', Button(**POPUP_CONFIRM_EN)):
        # A Cancel found where Confirm is not blue: the material slots of the enhance tab
        controller, FleetEnhance = popup_controller(screen('enhance_tab_empty.png', 700, 370), cancel_found=True)
        assert FleetEnhance.handle_popup_confirm_loose(controller, 'LIMIT_BREAK_MATERIAL') is False
        controller.device.click.assert_not_called()
        # The popup, its Cancel not found
        controller, FleetEnhance = popup_controller(screen('popup_elite_material.png', 300, 160), cancel_found=False)
        assert FleetEnhance.handle_popup_confirm_loose(controller, 'LIMIT_BREAK_MATERIAL') is False
        controller.device.click.assert_not_called()
