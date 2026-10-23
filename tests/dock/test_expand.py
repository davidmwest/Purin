from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import module.config.server as server
from module.base.utils import load_image
from module.dock.expand import DockExpand, purchase_verdict

DATA = 'tests/dock/data'


@pytest.fixture(autouse=True)
def en():
    # The dock capacity of the EN server, the F2P account plays there
    previous = server.server
    server.set_server('en')
    yield
    server.set_server(previous)


@pytest.mark.parametrize('free, gems, price, verdict', [
    # The F2P account, 2026-10-22: 148/150 and 200 gems, an expansion for 200
    (2, 200, 200, ''),
    (9, 400, 200, ''),
    (10, 400, 200, '10 free slots, 10 at least'),
    (2, 199, 200, '199 gems, 200 to buy it and 0 kept'),
    (2, 400, 250, 'it costs 250 gems, 200 at most'),
    (2, 400, 0, 'price not read'),
    (2, 0, 200, 'gems not read'),
])
def test_an_expansion_is_bought_on_a_full_dock_with_the_gems_for_it(free, gems, price, verdict):
    assert purchase_verdict(free, gems, price, free_below=10, price_max=200, reserve=0) == verdict


def test_gems_kept_are_not_spent():
    assert purchase_verdict(2, 300, 200, free_below=10, price_max=200, reserve=150) \
           == '300 gems, 200 to buy it and 150 kept'
    assert purchase_verdict(2, 350, 200, free_below=10, price_max=200, reserve=150) == ''


def make_expand(image, **config):
    expand = DockExpand.__new__(DockExpand)
    expand._templates = {}
    expand.device = SimpleNamespace(image=load_image(f'{DATA}/{image}.png'), screenshot=Mock(), sleep=Mock(),
                                    click=Mock())
    expand.config = SimpleNamespace(**config)
    return expand


def test_the_shop_shows_a_dock_expansion_for_200_gems():
    card, price = make_expand('shop_items_f2p').expansion_offer()

    assert price == 200
    assert card.area == (932, 454, 996, 518)


@pytest.mark.parametrize('image', ['dock_148_f2p', 'main_f2p', 'dock_expansion_popup_f2p'])
def test_no_dock_expansion_elsewhere(image):
    assert make_expand(image).expansion_offer() is None


def test_the_dock_reads_its_capacity():
    assert make_expand('dock_148_f2p').dock_amount() == (148, 150)


def run_expand(monkeypatch, dry_run, free_below=10, gems=200):
    expand = make_expand('dock_148_f2p', DockExpand_DryRun=dry_run, DockExpand_FreeBelow=free_below,
                         DockExpand_PriceMax=200, DockExpand_GemsReserve=0, task_delay=Mock())
    expand.ui_ensure = Mock()
    shop = load_image(f'{DATA}/shop_items_f2p.png')

    def enter():
        expand.device.image = shop
        return True

    expand.expansion_shop_enter = enter
    expand.expansion_buy = Mock(return_value=True)
    monkeypatch.setattr('module.dock.expand.OCR_SHOP_GEMS', SimpleNamespace(ocr=lambda image: gems))
    expand.run()
    return expand


def test_a_dry_run_buys_nothing(monkeypatch):
    expand = run_expand(monkeypatch, dry_run=True)

    expand.expansion_buy.assert_not_called()
    expand.config.task_delay.assert_called_once_with(success=True)


def test_a_dock_with_room_does_not_open_the_shop(monkeypatch):
    expand = run_expand(monkeypatch, dry_run=False, free_below=2)

    expand.expansion_buy.assert_not_called()
    expand.config.task_delay.assert_called_once_with(success=True)


def test_too_few_gems_buy_nothing(monkeypatch):
    expand = run_expand(monkeypatch, dry_run=False, gems=150)

    expand.expansion_buy.assert_not_called()


def test_a_purchase_that_did_not_add_10_slots_is_a_failure(monkeypatch):
    # The dock read after the purchase is still 148/150
    expand = run_expand(monkeypatch, dry_run=False)

    expand.expansion_buy.assert_called_once()
    expand.config.task_delay.assert_called_once_with(success=False)


def white_popup_on(image):
    """
    The shop with a white popup of the F2P account over it, "Cancel" and "Confirm" scaled down from 2560x1440.
    """
    import cv2
    image = image.copy()
    part = cv2.cvtColor(cv2.imread('tests/base/data/popup_no_mail_scaled.png'), cv2.COLOR_BGR2RGB)
    height, width = part.shape[:2]
    image[150:150 + height, 335:335 + width] = part
    return image


def test_a_purchase_cancels_the_popup_after_it_and_never_confirms(monkeypatch):
    from module.ui_white.assets import POPUP_CANCEL_WHITE, POPUP_CONFIRM_WHITE
    from module.dock.expand import PURCHASE_BUTTON
    shop = load_image(f'{DATA}/shop_items_f2p.png')
    frames = {
        'shop': shop,
        'popup': load_image(f'{DATA}/dock_expansion_popup_f2p.png'),
        'white': white_popup_on(shop),
    }
    state = {'now': 'shop'}
    clicks = []

    def click(button):
        clicks.append(button.name)
        if state['now'] == 'shop' and button.name == 'DOCK_EXPANSION':
            state['now'] = 'popup'
        elif state['now'] == 'popup' and button is PURCHASE_BUTTON:
            state['now'] = 'white'
        elif state['now'] == 'white' and button is POPUP_CANCEL_WHITE:
            state['now'] = 'shop'

    expand = make_expand('shop_items_f2p')
    expand.device = SimpleNamespace(image=shop, click=click, stuck_record_add=Mock(), display_scale=2.0,
                                    screenshot=Mock(), sleep=Mock())
    expand.interval_timer = {}
    save = Mock()
    monkeypatch.setattr('module.dock.expand.save_image', save)
    monkeypatch.setattr('module.dock.expand.os.makedirs', Mock())

    def loop():
        while 1:
            expand.device.image = frames[state['now']]
            yield expand.device.image

    expand.loop = loop

    assert expand.expansion_buy(price=200, gems=200) is True

    assert clicks.count('DOCK_EXPANSION') == 1
    assert clicks.count(PURCHASE_BUTTON.name) == 1
    assert any(name.startswith(POPUP_CANCEL_WHITE.name) for name in clicks)
    assert not any(name.startswith(POPUP_CONFIRM_WHITE.name) for name in clicks)
    assert state['now'] == 'shop'
    save.assert_called_once()
    assert (save.call_args[0][0] == frames['white']).all()
    assert save.call_args[0][1].startswith('log/dock_expand/')
    assert save.call_args[0][1].endswith('_unverified_popup.png')


def test_no_diagnostic_is_written_without_a_popup(monkeypatch):
    expand = make_expand('shop_items_f2p')
    expand.handle_popup_cancel = Mock(return_value=False)
    save = Mock()
    monkeypatch.setattr('module.dock.expand.save_image', save)

    assert expand._cancel_purchase_popup() is False

    save.assert_not_called()


def test_popup_capture_keeps_the_frame_before_the_cancel_handler(monkeypatch):
    expand = make_expand('dock_expansion_popup_f2p')
    before = expand.device.image.copy()

    def cancel(name):
        expand.device.image[:] = 0
        return True

    expand.handle_popup_cancel = cancel
    save = Mock()
    monkeypatch.setattr('module.dock.expand.save_image', save)
    monkeypatch.setattr('module.dock.expand.os.makedirs', Mock())

    assert expand._cancel_purchase_popup() is True

    assert (save.call_args[0][0] == before).all()


def test_the_captured_second_confirmation_matches_the_checked_offer():
    assert make_expand('dock_expansion_confirm_f2p').expansion_confirmation(price=200, gems=250)


def test_a_different_price_in_the_actual_confirmation_is_rejected():
    expand = make_expand('dock_expansion_confirm_f2p')
    # Copy the captured "2" over the next digit: the screen now offers 220 gems. The full sentence still
    # matches its template, so rejection must come from reading the amount independently.
    expand.device.image[331:361, 483:499] = expand.device.image[331:361, 468:484].copy()
    from module.dock.expand import CONFIRM_SENTENCE, CONFIRM_SENTENCE_AREA
    assert expand._appear(CONFIRM_SENTENCE, CONFIRM_SENTENCE_AREA)

    assert not expand.expansion_confirmation(price=200, gems=250)


@pytest.mark.parametrize('image', ['shop_items_f2p', 'dock_expansion_popup_f2p', 'main_f2p'])
def test_no_second_confirmation_on_other_screens(image):
    assert not make_expand(image).expansion_confirmation(price=200, gems=250)


@pytest.mark.parametrize('price, gems', [(0, 250), (199, 250), (250, 300), (200, 199)])
def test_an_unverified_offer_or_insufficient_gems_never_start_a_purchase(price, gems):
    expand = make_expand('dock_expansion_confirm_f2p')

    assert not expand.expansion_confirmation(price=price, gems=gems)
    assert expand.expansion_buy(price=price, gems=gems) is False

    expand.device.click.assert_not_called()


@pytest.mark.parametrize('amount', [0, 20, 199, 250, 300])
def test_the_confirmation_price_must_be_read_and_match_the_shop(monkeypatch, amount):
    # The sentence still matches if one digit changes. Its price must independently agree with the checked offer.
    monkeypatch.setattr('module.dock.expand.Digit.ocr', lambda self, image: amount)

    assert not make_expand('dock_expansion_confirm_f2p').expansion_confirmation(price=200, gems=250)


@pytest.mark.parametrize('after_confirm', ['shop', 'unrelated'])
def test_only_the_reviewed_dock_confirmation_is_confirmed_once(monkeypatch, after_confirm):
    from module.ui_white.assets import POPUP_CANCEL_WHITE
    from module.dock.expand import CONFIRM_BUTTON, PURCHASE_BUTTON
    shop = load_image(f'{DATA}/shop_items_f2p.png')
    frames = {
        'shop': shop,
        'popup': load_image(f'{DATA}/dock_expansion_popup_f2p.png'),
        'confirm': load_image(f'{DATA}/dock_expansion_confirm_f2p.png'),
        'unrelated': white_popup_on(shop),
    }
    state = {'now': 'shop', 'linger': 0}
    clicks = []

    def click(button):
        clicks.append(button.name)
        if state['now'] == 'shop' and button.name == 'DOCK_EXPANSION':
            state['now'] = 'popup'
        elif state['now'] == 'popup' and button is PURCHASE_BUTTON:
            state['now'] = 'confirm'
        elif state['now'] == 'confirm' and button is CONFIRM_BUTTON:
            state['now'] = 'confirmed'
        elif state['now'] == 'unrelated' and button is POPUP_CANCEL_WHITE:
            state['now'] = 'shop'

    expand = make_expand('shop_items_f2p')
    expand.device = SimpleNamespace(image=shop, click=click, stuck_record_add=Mock(), display_scale=2.0,
                                    screenshot=Mock(), sleep=Mock())
    expand.interval_timer = {}
    save = Mock()
    monkeypatch.setattr('module.dock.expand.save_image', save)
    monkeypatch.setattr('module.dock.expand.os.makedirs', Mock())

    def loop():
        while 1:
            frame = state['now']
            if frame == 'confirmed':
                # A successful tap need not clear the dialog before the next screenshot.
                state['linger'] += 1
                if state['linger'] < 4:
                    frame = 'confirm'
                else:
                    state['now'] = frame = after_confirm
            expand.device.image = frames[frame]
            yield expand.device.image

    expand.loop = loop

    assert expand.expansion_buy(price=200, gems=250) is True

    assert clicks.count('DOCK_EXPANSION') == 1
    assert clicks.count(PURCHASE_BUTTON.name) == 1
    assert clicks.count(CONFIRM_BUTTON.name) == 1
    assert state['now'] == 'shop'
    if after_confirm == 'unrelated':
        assert sum(name.startswith(POPUP_CANCEL_WHITE.name) for name in clicks) == 1
        save.assert_called_once()
        assert (save.call_args[0][0] == frames['unrelated']).all()
    else:
        save.assert_not_called()
