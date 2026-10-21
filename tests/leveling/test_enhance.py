import os
from types import SimpleNamespace

import numpy as np
import pytest

from module.base.utils import load_image
from module.leveling.enhance import (ENHANCE_FILL, ENHANCE_FILL_AREA, ENHANCE_PICKER, ENHANCE_PICKER_AREA,
                                     ENHANCE_SLOT_AREA, ENHANCE_SLOT_EMPTY, SIMILARITY, Census, material_fits,
                                     template_score)
from module.leveling.plan import ShipMatcher, load_ship_data

DATA = os.path.join(os.path.dirname(__file__), 'data')


@pytest.fixture(scope='module')
def matcher():
    return ShipMatcher(load_ship_data())


def card(level=1, rarity='common', fleet=0, status='free'):
    return SimpleNamespace(level=level, rarity=rarity, fleet=fleet, status=status)


def census(*cards, matcher=None):
    """
    Args:
        cards: Names, or (name, locked, fleet)
    """
    out = Census(matcher)
    for item in cards:
        name, locked, fleet = (item, False, 0) if isinstance(item, str) else item
        out.add(name, locked=locked, fleet=fleet)
    return out


def test_a_single_unlocked_ship_is_material(matcher):
    # "any extras count as enhancement material"
    assert material_fits('McCall', card(), census('McCall'), matcher) == ''


def test_copies_of_a_ship_nobody_keeps_are_material(matcher):
    # Four Cravens, none locked, none in a fleet
    dock = census('Craven', 'Craven', 'Craven', 'Craven')
    for _ in range(4):
        assert material_fits('Craven', card(), dock, matcher) == ''
        dock.take('Craven')
    assert material_fits('Craven', card(), dock, matcher)


def test_a_locked_ship_keeps_enough_copies_to_limit_break_her_fully(matcher):
    # McCall locked at level 12 for commissions, 3 copies: she needs 4, none is extra
    dock = census(('McCall', True, 0), 'McCall', 'McCall', 'McCall')
    assert material_fits('McCall', card(), dock, matcher).startswith('kept to limit break')
    # A 4th copy is the last one she needs, a 5th is an extra
    dock.add('McCall')
    assert material_fits('McCall', card(), dock, matcher).startswith('kept to limit break')
    dock.add('McCall')
    assert material_fits('McCall', card(), dock, matcher) == ''


def test_a_ship_of_a_fleet_keeps_her_copies_locked_or_not(matcher):
    # Long Island and Laffey of the F2P fleet are not locked, the limit breaks of the task use their copies
    dock = census(('Laffey', False, 1), 'Laffey')
    assert material_fits('Laffey', card(rarity='elite'), dock, matcher)


@pytest.mark.parametrize('name, ship', [
    # Levelled for commissions
    ('Craven', card(level=12)),
    ('Craven', card(rarity='super_rare')),
    ('Craven', card(fleet=2)),
    ('Craven', card(status='commission')),
])
def test_other_ships_kept(matcher, name, ship):
    assert material_fits(name, ship, census(name), matcher)


def test_an_elite_extra_is_material(matcher):
    assert material_fits('Laffey', card(rarity='elite'), census('Laffey'), matcher) == ''


def test_a_bulin_is_kept(matcher):
    assert material_fits('Universal Bulin', card(rarity='rare'), census('Universal Bulin'), matcher) == 'a Bulin'


def test_a_name_not_read_is_kept(matcher):
    assert material_fits('', card(), census(''), matcher)


def test_a_name_the_census_did_not_read_is_kept(matcher):
    # "B og ue" in the picker, the census read her otherwise: no card of that name, no extra
    assert material_fits('Bogue', card(), census('Boque'), matcher)


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


def test_names_read_their_own_way_are_the_same_ship(matcher):
    # The census read ".Beaglem" four times, the picker "Beagle": no card of that name, no extra
    dock = census('.Beaglem', '.Beaglem', '.Beaglem', '.Beaglem', matcher=matcher)
    assert dock.extras('Beagle') == 4
    assert material_fits('Beagle', card(), dock, matcher) == ''
    dock = census('_Raleighm', matcher=matcher)
    assert material_fits('R aleigh', card(), dock, matcher) == ''
    # Kept the same way: a locked McCall read "__McCall", her copies "McC all"
    dock = census(('__McCall', True, 0), 'McC all', 'McCall', matcher=matcher)
    assert material_fits('McC all', card(), dock, matcher).startswith('kept to limit break')
