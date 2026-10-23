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
    assert result['proof_kind'] == 'source_received_hidden_button'
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
    monkeypatch.setattr(reader, '_panel_frame', Mock(return_value=False))
    monkeypatch.setattr(reader, '_panel', Mock(return_value=None))


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
    reader._panel_frame.return_value = True
    result = reader.observe(p.VISITORS, prize(), tmp_path, 'cancel')
    assert observer.device.click.call_args_list[0].args == (p.PRIZE_ZUIKAKU,)
    assert [call.args[0] for call in observer.device.click.call_args_list[1:]] == [p.PRIZE_PANEL_CLOSE] * 2
    assert result['cancel_clicks'] == 2 and result['claim_clicks'] == 0


def test_recognized_stage_return_is_verified_after_cancel(observer, monkeypatch, tmp_path):
    reader = p.ArchivePrizeProgress(observer)
    mock_open(reader, monkeypatch)
    reader._panel_frame.return_value = True
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


def test_other_prize_requires_its_own_fresh_identity(observer, tmp_path):
    observer._archive_identity = Mock(return_value=False)
    archive = get_archive('war_archives_20190221_en')
    result = p.ArchivePrizeProgress(observer).observe(archive.id, archive.prizes[0], tmp_path, 'unsupported')
    assert result['reason'] == 'prize_source_unrecognized' and not result['verified']
    observer.device.click.assert_not_called()


def test_actual_prize_popup_parses_independent_catalog_fields(observer):
    observer.device.image = fixture('archive_prize_panel.png')
    result = p.ArchivePrizeProgress(observer)._panel(prize())
    assert result == dict(count=0, action='sortie', name='zuikaku', stage='D3',
                          proof_kind='catalog_bound_reward_panel')


@pytest.mark.parametrize('witness', [p.PRIZE_PANEL_INFO, p.PRIZE_PANEL_CLOSE,
                                    p.PRIZE_PANEL_FRAME, p.PRIZE_PANEL_CAPTION])
def test_actual_popup_missing_witness_is_unknown(observer, witness):
    observer.device.image = fixture('archive_prize_panel.png')
    left, top, right, bottom = witness.area
    observer.device.image[top:bottom, left:right] = 0
    assert p.ArchivePrizeProgress(observer)._panel(prize()) is None


@pytest.mark.parametrize('field,value', [('name', 'Duke of York'), ('stage', 'C3'),
                                       ('count', '6060'), ('count', '61/60'),
                                       ('count', '60/60'), ('action', 'Accept')])
def test_popup_mismatched_reward_stage_counter_or_action_never_authorizes(observer, monkeypatch,
                                                                         field, value):
    observer.device.image = fixture('archive_prize_panel.png')
    original = p.Ocr.ocr

    def raw(ocr, image, **kwargs):
        key = {'ARCHIVE_PRIZE_STAGE': 'stage',
               'ARCHIVE_PRIZE_PANEL_COUNT': 'count'}.get(ocr.name)
        return value if key == field else original(ocr, image, **kwargs)

    monkeypatch.setattr(p.Ocr, 'ocr', raw)
    if field == 'name':
        monkeypatch.setattr(p.PRIZE_PANEL_NAME, 'ocr', Mock(return_value=value))
    if field == 'action':
        monkeypatch.setattr(p.PRIZE_PANEL_ACTION_TEXT, 'ocr', Mock(return_value=value))
    assert p.ArchivePrizeProgress(observer)._panel(prize()) is None
    observer.device.click.assert_not_called()


def test_actual_popup_closes_with_its_red_x_and_returns_exact_source(observer, tmp_path):
    reader = p.ArchivePrizeProgress(observer)

    def click(button):
        if button is p.PRIZE_ZUIKAKU:
            observer.device.image = fixture('archive_prize_panel.png')
        else:
            assert button is p.PRIZE_PANEL_CLOSE
            observer.device.image = fixture()

    observer.device.click.side_effect = click
    result = reader.observe(p.VISITORS, prize(), tmp_path, 'actual-popup')
    assert result['returned_stage'] and result['verified'] and result['count'] == 0
    assert result['panel']['action'] == 'sortie'
    assert result['cancel_clicks'] == 1 and result['claim_clicks'] == 0
    assert observer.device.click.call_count == 2


def test_generic_prize_uses_exact_catalog_identity_stage_and_shared_counter(observer):
    archive = get_archive('war_archives_20190221_en')
    reader = p.ArchivePrizeProgress(observer)
    reader.definition, reader.prize = archive, archive.prizes[0]
    observer._archive_identity = Mock(return_value=True)
    observer._read_stage_screen = Mock(return_value=dict(event=archive.id, mode='normal',
                                                       stages={'a1': {}}, raw_keys=80))
    assert reader._source() and reader._count() == 0
    assert not reader._claimed_source()  # No invented received proof for unknown background.
    observer._archive_identity.assert_called_with(archive.id)
    observer._read_stage_screen.return_value['event'] = p.VISITORS
    assert not reader._source()


def claim_mock(reader, observer, monkeypatch):
    monkeypatch.setattr(reader, '_count', Mock(return_value=60))
    monkeypatch.setattr(reader, '_panel_frame', Mock(return_value=False))
    monkeypatch.setattr(reader, '_source', Mock(return_value=False))
    monkeypatch.setattr(reader, '_claimed_source',
                        Mock(side_effect=lambda: observer.device.click.call_count >= 3))
    monkeypatch.setattr(reader, '_panel', Mock(return_value=dict(count=60, action='accept')))
    monkeypatch.setattr(reader, '_receipt', Mock(side_effect=lambda:
                        p.GET_ITEMS_1 if observer.device.click.call_count == 2 else None))
    observer.loop = lambda **kwargs: iter(range(15))


def test_claim_reserves_once_then_requires_own_receipt_and_fresh_return(observer, monkeypatch,
                                                                       tmp_path):
    reader = p.ArchivePrizeProgress(observer)
    claim_mock(reader, observer, monkeypatch)
    path = tmp_path / 'f2p' / 'prize-claims' / f'{p.VISITORS}-{prize().id}.json'

    def click(button):
        saved = json.loads(path.read_text())
        if button is p.PRIZE_PANEL_ACCEPT:
            assert saved['claim_clicks'] == 1 and saved['pending'] and saved['count'] == 60
        elif button is p.PRIZE_ZUIKAKU:
            assert saved['open_clicks'] == 1 and saved['claim_clicks'] == 0
        else:
            assert button is p.GET_ITEMS_1 and saved['receipt_seen']

    observer.device.click.side_effect = click
    result = reader.claim(p.VISITORS, prize(), tmp_path, 'first')
    assert result['verified'] and result['claimed'] and result['returned_stage']
    assert result['claim_clicks'] == 1 and result['receipt_seen'] and not result['pending']
    assert result['proof_kind'] == 'owned_claim_receipt'
    assert [call.args[0] for call in observer.device.click.call_args_list] == [
        p.PRIZE_ZUIKAKU, p.PRIZE_PANEL_ACCEPT, p.GET_ITEMS_1]
    # A later scheduler request is bound to the same irreversible claim budget.
    result = reader.claim(p.VISITORS, prize(), tmp_path, 'second')
    assert result['verified'] and observer.device.click.call_count == 3


def test_claim_crash_after_accept_reservation_never_replays(observer, monkeypatch, tmp_path):
    reader = p.ArchivePrizeProgress(observer)
    claim_mock(reader, observer, monkeypatch)

    def click(button):
        if button is p.PRIZE_PANEL_ACCEPT:
            raise RuntimeError('crash before delivery')

    observer.device.click.side_effect = click
    with pytest.raises(RuntimeError):
        reader.claim(p.VISITORS, prize(), tmp_path, 'before')
    observer.device.click.reset_mock(side_effect=True)
    reader._receipt.return_value = None
    reader._receipt.side_effect = None
    reader._claimed_source.side_effect = None
    reader._claimed_source.return_value = False
    result = reader.claim(p.VISITORS, prize(), tmp_path, 'after')
    assert result['claim_clicks'] == 1 and result['pending'] and not result['verified']
    observer.device.click.assert_not_called()


@pytest.mark.parametrize('panel', [None, dict(count=59, action='sortie'),
                                  dict(count=60, action='sortie')])
def test_claim_never_clicks_sortie_or_unknown_panel(observer, monkeypatch, tmp_path, panel):
    reader = p.ArchivePrizeProgress(observer)
    claim_mock(reader, observer, monkeypatch)
    reader._panel.return_value = panel
    reader._receipt.side_effect = None
    reader._receipt.return_value = None
    result = reader.claim(p.VISITORS, prize(), tmp_path)
    assert result['claim_clicks'] == 0 and not result['verified']
    observer.device.click.assert_called_once_with(p.PRIZE_ZUIKAKU)


def test_claim_below_sixty_never_opens_or_handles_unowned_receipt(observer, monkeypatch, tmp_path):
    reader = p.ArchivePrizeProgress(observer)
    claim_mock(reader, observer, monkeypatch)
    reader._count.return_value = 59
    result = reader.claim(p.VISITORS, prize(), tmp_path)
    assert result['reason'] == 'prize_not_ready' and result['claim_clicks'] == 0
    reader._receipt.assert_not_called()
    observer.device.click.assert_not_called()


@pytest.mark.parametrize('mutation', [dict(account='alas'), dict(open_clicks=True),
                                    dict(claim_clicks=2), dict(pending='true'),
                                    dict(receipt_seen=True), dict(claimed=True),
                                    dict(reward_dialogs=25), dict(stage='D4'),
                                    dict(prize_name='Duke of York'), dict(total=59),
                                    dict(count=True), dict(count=61),
                                    dict(open_clicks=1, claim_clicks=1, pending=True, count=59),
                                    dict(open_clicks=1, claim_clicks=1, count=60)])
def test_claim_corrupt_budget_never_resets_or_inputs(observer, monkeypatch, tmp_path, mutation):
    reader = p.ArchivePrizeProgress(observer)
    claim_mock(reader, observer, monkeypatch)
    reader._count.return_value = 0
    result = reader.claim(p.VISITORS, prize(), tmp_path)
    result.update(mutation)
    Path(result['journal']).write_text(json.dumps(result))
    with pytest.raises(ScriptError, match='journal'):
        reader.claim(p.VISITORS, prize(), tmp_path)
    observer.device.click.assert_not_called()


def test_unknown_owned_claim_panel_is_closed_but_never_accepted(observer, monkeypatch, tmp_path):
    reader = p.ArchivePrizeProgress(observer)
    claim_mock(reader, observer, monkeypatch)
    reader._panel.return_value = None
    reader._panel_frame.side_effect = lambda: observer.device.click.call_count == 1
    reader._source.side_effect = lambda: observer.device.click.call_count >= 2
    result = reader.claim(p.VISITORS, prize(), tmp_path)
    assert result['returned_stage'] and not result['verified']
    assert result['claim_clicks'] == 0 and result['cancel_clicks'] == 1
    assert [call.args[0] for call in observer.device.click.call_args_list] == [
        p.PRIZE_ZUIKAKU, p.PRIZE_PANEL_CLOSE]
