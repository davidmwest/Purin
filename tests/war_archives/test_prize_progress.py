import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
from PIL import Image
import pytest

from module.exception import ScriptError
from module.war_archives.catalog import get_archive
from module.war_archives import prize_progress as p


class ReadyTimer:
    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        return self

    def reset(self):
        return self

    def reached(self):
        return True


def fixture(name='archive_visitors_prize.png'):
    return np.array(Image.open(Path(__file__).parent / 'data' / name).convert('RGB'))


@pytest.fixture
def observer(monkeypatch):
    monkeypatch.setattr(p, 'Timer', ReadyTimer)
    return SimpleNamespace(config=SimpleNamespace(config_name='f2p'),
                           device=SimpleNamespace(image=fixture(), click=Mock()),
                           _archive_stage_page=Mock(return_value=True),
                           loop=lambda **kwargs: iter(range(5)))


def prize():
    return get_archive(p.VISITORS).prizes[0]


@pytest.mark.parametrize('text,value', [('0/60', 0), ('1/60', 1), ('59/60', 59),
                                      ('60/60', 60), (' 7/60 ', 7), ('', None),
                                      ('O/60', None), ('05/60', None), ('61/60', None),
                                      ('060', None), ('1/600', None), ('-1/60', None),
                                      ('1/6', None), ('100/60', None)])
def test_prize_count_requires_complete_raw_counter(text, value):
    assert p.parse_prize_count(text) == value


def test_actual_f2p_fixture_reads_zero_of_sixty_without_digit_defaults(observer):
    reader = p.ArchivePrizeProgress(observer)
    assert reader._source()
    assert not reader._claimed_source()
    assert p.PRIZE_COUNT.ocr(observer.device.image) == '0/60'
    assert reader._count() == 0


@pytest.mark.parametrize('witness', [p.PRIZE_ARCHIVE_TITLE, p.PRIZE_PROGRESS_FRAME,
                                    p.PRIZE_ZUIKAKU])
def test_each_exact_source_witness_is_required(observer, witness):
    left, top, right, bottom = witness.area
    observer.device.image[top:bottom, left:right] = 0
    assert not p.ArchivePrizeProgress(observer)._source()


def test_dimmed_stage_under_unknown_modal_cannot_authorize_input(observer, tmp_path):
    observer.device.image = (observer.device.image * .45).astype(np.uint8)
    reader = p.ArchivePrizeProgress(observer)
    assert not reader._source()
    assert not reader._claimed_source()
    result = reader.observe(p.VISITORS, prize(), tmp_path, 'dimmed')
    assert result['open_clicks'] == 0 and result['claimed'] is None
    observer.device.click.assert_not_called()


def test_actual_main_already_claimed_fixture_needs_three_fresh_stage_frames(observer, tmp_path):
    observer.config.config_name = 'alas'
    observer.device.image = fixture('archive_visitors_prize_claimed.png')
    reader = p.ArchivePrizeProgress(observer)
    assert reader._claimed_source() and not reader._source()
    calls = []

    def frames(**kwargs):
        for index in range(5):
            calls.append(index)
            yield index

    observer.loop = frames
    result = reader.observe(p.VISITORS, prize(), tmp_path, 'claimed')
    assert result['claimed'] is True and result['verified'] is True
    assert result['count'] is None  # No invented prior clear count.
    assert result['open_clicks'] == result['claim_clicks'] == 0
    assert result['returned_stage'] and len(calls) == 3
    observer.device.click.assert_not_called()


def test_absence_without_exact_stage_or_background_remains_unknown(observer, tmp_path):
    observer.device.image = fixture('archive_visitors_prize_claimed.png')
    observer._archive_stage_page.return_value = False
    result = p.ArchivePrizeProgress(observer).observe(p.VISITORS, prize(), tmp_path, 'unknown')
    assert result['claimed'] is None and result['open_clicks'] == 0
    observer.device.click.assert_not_called()


def mock_open(reader, monkeypatch):
    monkeypatch.setattr(reader, '_claimed_source', Mock(return_value=False))
    monkeypatch.setattr(reader, '_count', Mock(return_value=0))
    monkeypatch.setattr(reader, '_source', Mock(return_value=False))
    monkeypatch.setattr(p.POPUP_CANCEL, 'match', Mock(return_value=False))


def test_unknown_panel_is_only_captured_after_one_durable_open(observer, monkeypatch, tmp_path):
    reader = p.ArchivePrizeProgress(observer)
    mock_open(reader, monkeypatch)
    path = tmp_path / 'f2p' / 'once' / f'prize-{prize().id}.json'

    def click(button):
        saved = json.loads(path.read_text())
        assert button is p.PRIZE_ZUIKAKU
        assert saved['open_clicks'] == 1 and saved['claim_clicks'] == 0

    observer.device.click.side_effect = click
    result = reader.observe(p.VISITORS, prize(), tmp_path, 'once')
    assert result['status'] == 'observed' and result['count'] == 0
    assert result['reason'] == 'prize_panel_close_unrecognized'
    assert result['cancel_clicks'] == result['claim_clicks'] == 0
    assert any(name.endswith('prize_panel.png') for name in result['diagnostics'])
    observer.device.click.assert_called_once_with(p.PRIZE_ZUIKAKU)
    reused = reader.observe(p.VISITORS, prize(), tmp_path, 'once')
    assert reused['reused'] and observer.device.click.call_count == 1


def test_changed_count_before_open_blocks_click(observer, monkeypatch, tmp_path):
    reader = p.ArchivePrizeProgress(observer)
    mock_open(reader, monkeypatch)
    reader._count.side_effect = [0, 0, 1]
    result = reader.observe(p.VISITORS, prize(), tmp_path, 'changed')
    assert result['reason'] == 'prize_source_changed' and result['open_clicks'] == 0
    observer.device.click.assert_not_called()


def test_open_crash_reservation_prevents_any_replay(observer, monkeypatch, tmp_path):
    reader = p.ArchivePrizeProgress(observer)
    mock_open(reader, monkeypatch)
    observer.device.click.side_effect = RuntimeError('interrupted')
    with pytest.raises(RuntimeError, match='interrupted'):
        reader.observe(p.VISITORS, prize(), tmp_path, 'crash')
    observer.device.click.reset_mock()
    result = reader.observe(p.VISITORS, prize(), tmp_path, 'crash')
    assert result['reused'] and result['open_clicks'] == 1
    observer.device.click.assert_not_called()


def test_exact_cancel_is_bounded_and_never_becomes_claim(observer, monkeypatch, tmp_path):
    reader = p.ArchivePrizeProgress(observer)
    mock_open(reader, monkeypatch)
    p.POPUP_CANCEL.match.return_value = True
    result = reader.observe(p.VISITORS, prize(), tmp_path, 'cancel')
    assert observer.device.click.call_args_list[0].args == (p.PRIZE_ZUIKAKU,)
    assert [call.args[0] for call in observer.device.click.call_args_list[1:]] == [p.POPUP_CANCEL] * 2
    assert result['cancel_clicks'] == 2 and result['claim_clicks'] == 0


def test_recognized_stage_return_is_verified_after_cancel(observer, monkeypatch, tmp_path):
    reader = p.ArchivePrizeProgress(observer)
    mock_open(reader, monkeypatch)
    p.POPUP_CANCEL.match.return_value = True
    reader._source.side_effect = lambda: observer.device.click.call_count >= 2
    result = reader.observe(p.VISITORS, prize(), tmp_path, 'returned')
    assert result['returned_stage'] and result['reason'] == 'prize_panel_captured'
    assert result['cancel_clicks'] == 1 and observer.device.click.call_count == 2


@pytest.mark.parametrize('mutation', [dict(account='alas'), dict(open_clicks=True),
                                    dict(open_clicks=2), dict(cancel_clicks=3),
                                    dict(claim_clicks=True), dict(claim_clicks=1),
                                    dict(combat_attempted=True), dict(diagnostics={})])
def test_corrupt_journal_never_resets_budgets(observer, monkeypatch, tmp_path, mutation):
    reader = p.ArchivePrizeProgress(observer)
    mock_open(reader, monkeypatch)
    result = reader.observe(p.VISITORS, prize(), tmp_path, 'bad')
    path = Path(result['journal'])
    result.update(mutation)
    path.write_text(json.dumps(result))
    observer.device.click.reset_mock()
    with pytest.raises(ScriptError, match='journal'):
        reader.observe(p.VISITORS, prize(), tmp_path, 'bad')
    observer.device.click.assert_not_called()


def test_other_prize_remains_explicitly_unsupported(observer, tmp_path):
    archive = get_archive('war_archives_20190221_en')
    result = p.ArchivePrizeProgress(observer).observe(archive.id, archive.prizes[0], tmp_path, 'unsupported')
    assert result['reason'] == 'prize_fixture_unsupported' and not result['verified']
    observer.device.click.assert_not_called()
