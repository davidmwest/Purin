import json
import os
import subprocess
import sys
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import cv2
import numpy as np
import pytest

import module.config.server as server
from module.exception import GameTooManyClickError
from module.inventory import inventory as inventory_module
from module.inventory.inventory import DockCard, DockReader, DockRow, Inventory, LIST_TOP, ROW_DELTA, \
    ROW_TOP_MAX, find_row, save_inventory, signature, signature_still

DATA = os.path.join(os.path.dirname(__file__), 'data')


@pytest.fixture(autouse=True)
def en():
    previous = server.server
    server.set_server('en')
    yield
    server.set_server(previous)


def load(name):
    """
    Screenshots of the dock of the EN client at 1280x720.
    """
    return cv2.cvtColor(cv2.imread(os.path.join(DATA, name)), cv2.COLOR_BGR2RGB)


def view(rows):
    return [(row.top, ''.join('L' if card.locked else '.' for card in row.cards)) for row in rows]


def test_rows_of_locked_ships():
    rows = DockReader().rows(load('dock_top.jpg'))

    assert view(rows) == [(75, 'LLLLLLL'), (304, 'LLLLLLL'), (530, 'LLLLLLL')]


def test_rows_at_the_end_of_the_dock():
    rows = DockReader().rows(load('dock_bottom.jpg'))

    # Ships at level 1, not locked, and the last row has 6 of them
    assert view(rows) == [(173, '.......'), (399, '......')]
    assert [card.column for card in rows[-1].cards] == [0, 1, 2, 3, 4, 5]


def test_row_found_again_after_a_drag():
    top = DockReader().rows(load('dock_top.jpg'))

    # The last row, read at 530, after a drag of 2 rows that went a bit further
    assert find_row(load('dock_dragged.jpg'), top[-1].signature) == 49
    # The first row is above the screen
    assert find_row(load('dock_dragged.jpg'), top[0].signature) is None


def test_card_button_stays_on_the_screen():
    x1, y1, x2, y2 = DockCard(top=566, column=6, locked=True).button.button

    assert y1 < y2 <= 710 and x1 < x2


def test_page_still_drawing_is_not_read():
    image = np.full((720, 1280, 3), 10, dtype=np.uint8)
    counting = image.copy()
    # Power counting up when the ship arrives
    counting[230:270, 1160:1200] = 250
    assert not signature_still(signature(image), signature(counting))
    assert signature_still(signature(image), signature(image.copy()))


def test_inventory_is_written_at_once(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'config').mkdir()

    save_inventory('alas', {'ships': [{'name': 'Tiger'}]})

    folder = tmp_path / 'config' / 'inventory'
    assert json.loads((folder / 'alas.json').read_text(encoding='utf-8')) == {'ships': [{'name': 'Tiger'}]}
    assert not (folder / 'alas.json.tmp').exists()
    # Not a file that the GUI takes as the config of a mod, like alas.maa.json
    assert [file.name for file in (tmp_path / 'config').iterdir()] == ['inventory']


class FakeDock:
    """
    Rows of the dock, each a list of booleans of whether the ship is locked, that drags scroll through.

    Args:
        rows (list[list[bool]]):
        further (int): Pixels a drag goes further than asked
        dropped (tuple[int]): Drags that do nothing, counted from 1
    """

    first_top = 75

    def __init__(self, rows, further=0, dropped=()):
        self.rows = rows
        self.offset = 0
        self.further = further
        self.dropped = set(dropped)
        self.drags = 0
        self.opened = []

    @property
    def max_offset(self):
        # The list stops with its last row at the bottom of the screen
        return max(0, self.first_top + (len(self.rows) - 1) * ROW_DELTA + 204 - 700)

    def drag(self, distance):
        self.drags += 1
        if self.drags in self.dropped:
            return
        if distance > 0:
            distance += self.further
        self.offset = min(max(self.offset + distance, 0), self.max_offset)

    def top(self, index):
        return self.first_top + index * ROW_DELTA - self.offset

    def screen_rows(self):
        out = []
        for index, cards in enumerate(self.rows):
            top = self.top(index)
            # Rows under the bar of the dock have no gap above them on the screen
            if LIST_TOP + 6 <= top <= ROW_TOP_MAX:
                row = DockRow(top=top, signature=index)
                row.cards = [DockCard(top=top, column=column, locked=locked) for column, locked in enumerate(cards)]
                out.append(row)
        return out

    def find(self, index):
        top = self.top(index)
        return top if top + 4 >= 0 and top + 140 <= 720 else None

    def open(self, card):
        index = (card.top + self.offset - self.first_top) // ROW_DELTA
        self.opened.append((index, card.column))
        # The "screenshot" of the page is the card it is of
        return (index, card.column), None, 4


class FakeReader:
    def read(self, image, name=None):
        index, column = image
        return {'name': f'{index}-{column}', 'hull': 'DD', 'level': 100, 'power': 4000, 'stars': 5,
                'affinity': 'love'}


class SyncPool:
    """
    Does the OCR at once, tests do not depend on when a thread runs
    """

    def __init__(self, max_workers=1):
        pass

    @staticmethod
    def submit(func, *args, **kwargs):
        future = Future()
        try:
            future.set_result(func(*args, **kwargs))
        except Exception as e:
            future.set_exception(e)
        return future

    def shutdown(self, wait=True):
        pass


def make_inventory(monkeypatch, dock):
    monkeypatch.setattr(inventory_module, 'ThreadPoolExecutor', SyncPool)
    monkeypatch.setattr(inventory_module, 'DockReader', lambda: SimpleNamespace(rows=lambda image: dock.screen_rows()))
    monkeypatch.setattr(inventory_module, 'InfoReader', FakeReader)
    monkeypatch.setattr(inventory_module, 'find_row', lambda image, index: dock.find(index))
    inv = object.__new__(Inventory)
    inv.device = SimpleNamespace(image=None, screenshot=Mock(), stuck_record_clear=Mock(), click_record_clear=Mock())
    inv.ui_ensure = Mock()
    inv.dock_favourite_set = Mock()
    inv.dock_filter_set = Mock()
    inv.dock_sort_method_dsc_set = Mock()
    inv.inventory_dock_amount = lambda: sum(len(row) for row in dock.rows)
    inv.inventory_list_top = lambda: setattr(dock, 'offset', 0)
    inv.inventory_drag = dock.drag
    inv.inventory_drag_back = lambda: dock.drag(-ROW_DELTA)
    inv.inventory_open_card = dock.open
    return inv


def dock_rows(count=12, last=4):
    """
    Rows of 7 ships, every third locked, and a last row of fewer ships.
    """
    rows = [[(row * 7 + column) % 3 == 0 for column in range(7)] for row in range(count - 1)]
    rows.append([column % 2 == 0 for column in range(last)])
    return rows


def locked_cards(rows):
    return [(index, column) for index, cards in enumerate(rows) for column, locked in enumerate(cards) if locked]


@pytest.mark.parametrize('further', [0, 25, 60])
def test_scan_opens_every_locked_ship_once(monkeypatch, further):
    rows = dock_rows()
    dock = FakeDock(rows, further=further)

    data = make_inventory(monkeypatch, dock).inventory_scan()

    assert dock.opened == locked_cards(rows)
    assert [ship['name'] for ship in data['ships']] == [f'{row}-{column}' for row, column in locked_cards(rows)]
    assert (data['seen'], data['dock_amount'], data['complete']) == (81, 81, True)
    assert [ship['index'] for ship in data['ships']] == [row * 7 + column + 1 for row, column in locked_cards(rows)]


def test_scan_with_a_drag_that_does_nothing(monkeypatch):
    rows = dock_rows()
    dock = FakeDock(rows, dropped=(2,))

    data = make_inventory(monkeypatch, dock).inventory_scan()

    assert dock.opened == locked_cards(rows)
    assert data['complete'] is True


def test_scan_with_a_drag_that_goes_too_far(monkeypatch):
    rows = dock_rows()
    # A drag that flings the list past the last row read, which is found again by dragging back
    dock = FakeDock(rows)
    drag = dock.drag

    def fling(distance):
        drag(distance + (300 if dock.drags == 1 and distance > 0 else 0))

    inv = make_inventory(monkeypatch, dock)
    inv.inventory_drag = fling

    data = inv.inventory_scan()

    assert dock.opened == locked_cards(rows)
    assert data['complete'] is True


def test_dock_that_fits_on_a_screen(monkeypatch):
    rows = dock_rows(count=2, last=3)
    dock = FakeDock(rows)

    data = make_inventory(monkeypatch, dock).inventory_scan()

    assert dock.opened == locked_cards(rows)
    assert (data['seen'], data['complete']) == (10, True)


def test_ship_not_read_leaves_the_inventory_not_complete(monkeypatch):
    rows = dock_rows(count=3)
    dock = FakeDock(rows)
    inv = make_inventory(monkeypatch, dock)
    inv.inventory_open_card = lambda card: None if card.column == 0 else dock.open(card)

    data = inv.inventory_scan()

    assert data['unread'] > 0
    assert data['complete'] is False


def test_ship_whose_ocr_fails_is_counted(monkeypatch):
    rows = dock_rows(count=2, last=3)
    dock = FakeDock(rows)
    inv = make_inventory(monkeypatch, dock)
    read = FakeReader.read

    def fail_once(self, image, name=None):
        if image == (0, 3):
            raise ValueError('OCR failed')
        return read(self, image, name)

    monkeypatch.setattr(FakeReader, 'read', fail_once)

    data = inv.inventory_scan()

    assert data['unread'] == 1
    assert (0, 3) in dock.opened and '0-3' not in [ship['name'] for ship in data['ships']]


def test_drags_do_not_count_as_clicks_on_a_button():
    # At the end of the dock, many drags follow one another without a card opened
    inv = object.__new__(Inventory)
    calls = []
    inv.device = SimpleNamespace(click_record_clear=lambda: calls.append('clear'),
                                 swipe=lambda *args, **kwargs: calls.append('swipe'), sleep=lambda second: None)

    for _ in range(20):
        inv.inventory_drag(ROW_DELTA)
    inv.inventory_drag_back()

    assert calls == ['clear', 'swipe'] * 21


def test_failed_scan_keeps_what_was_read(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    dock = FakeDock(dock_rows())
    inv = make_inventory(monkeypatch, dock)
    inv.config = SimpleNamespace(config_name='alas')
    opened = dock.open

    def open_and_fail(card):
        if len(dock.opened) == 5:
            raise GameTooManyClickError('Too many click for a button: INVENTORY_DRAG')
        return opened(card)

    inv.inventory_open_card = open_and_fail

    with pytest.raises(GameTooManyClickError):
        inv.inventory_scan()

    folder = tmp_path / 'config' / 'inventory'
    data = json.loads((folder / 'alas.partial.json').read_text(encoding='utf-8'))
    assert (data['end'], data['locked'], data['complete']) == ('error', 5, False)
    # The last whole inventory is left as it was
    assert not (folder / 'alas.json').exists()


def test_whole_run_replaces_the_partial_one(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(inventory_module, 'CHECKPOINT', 5)
    dock = FakeDock(dock_rows())
    inv = make_inventory(monkeypatch, dock)
    inv.config = SimpleNamespace(config_name='alas', task_delay=Mock())
    saved = []
    save = inventory_module.save_inventory

    def save_and_record(name, data, partial=False):
        saved.append((data['end'], partial))
        save(name, data, partial=partial)

    monkeypatch.setattr(inventory_module, 'save_inventory', save_and_record)

    inv.run()

    # Saved while it goes, then whole
    assert ('running', True) in saved and saved[-1] == ('dock', False)
    folder = tmp_path / 'config' / 'inventory'
    assert [file.name for file in folder.iterdir()] == ['alas.json']
    inv.config.task_delay.assert_called_once_with(success=True)


def test_ocr_models_are_freed_on_the_thread_that_used_them(monkeypatch):
    freed = []
    monkeypatch.setattr(inventory_module, 'release_ocr_models', lambda: freed.append(threading.get_ident()))
    pool = ThreadPoolExecutor(max_workers=1)
    ocr_thread = pool.submit(threading.get_ident).result()

    inventory_module.ocr_pool_shutdown(pool)

    assert freed == [ocr_thread]
    assert ocr_thread != threading.get_ident()


def test_release_ocr_models_drops_the_cached_ones(monkeypatch):
    models = SimpleNamespace(cnocr=object(), azur_lane=object(), other='kept')
    monkeypatch.setattr(inventory_module, 'OCR_MODEL', models)

    inventory_module.release_ocr_models()

    assert vars(models) == {'other': 'kept'}


# The OCR of a scan on its thread, then the release of models before the next task, on the main thread
NEXT_TASK = """
import gc
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from module.base.resource import release_resources
from module.inventory.inventory import ocr_pool_shutdown
from module.ocr.ocr import Ocr

image = np.zeros((720, 1280, 3), np.uint8)
image[20:40, 20:120] = 255
pool = ThreadPoolExecutor(max_workers=1)
pool.submit(Ocr((10, 10, 300, 50), lang='cnocr', name='TEST').ocr, image).result()
ocr_pool_shutdown(pool)
release_resources(next_task='Commission')
gc.collect()
print('survived')
"""


def test_next_task_after_a_scan_does_not_abort():
    # With pool.shutdown() alone, MXNet aborted the process: "mutex lock failed: Invalid argument"
    root = os.path.join(os.path.dirname(__file__), '..', '..')
    env = dict(os.environ, OMP_NUM_THREADS='1')
    result = subprocess.run([sys.executable, '-c', NEXT_TASK], cwd=root, env=env, capture_output=True, text=True,
                            timeout=300)

    assert result.returncode == 0, result.stderr[-2000:]
    assert 'survived' in result.stdout
