import json

import pytest

from module.exception import ScriptError
from module.minigame.arcade_state import ArcadeJournal, parse_count, parse_selection


@pytest.mark.parametrize('text,value', [('0', 0), ('40', 40), ('41', None), ('', None), ('MAX:40', None),
                                      ('3/40', None), ('-1', None), ('1O', None), ('True', None)])
def test_credit_count_requires_the_whole_numeric_value(text, value):
    assert parse_count(text) == value


@pytest.mark.parametrize('text,value', [('0/0', (0, 0)), ('5/40', (5, 40)), ('3/3', (3, 3)),
                                      ('6/40', None), ('5/4', None), ('1/41', None), ('5', None),
                                      ('5/40 cost 10', None), ('-1/40', None)])
def test_selected_credit_counter_does_not_accept_partial_or_inconsistent_ocr(text, value):
    assert parse_selection(text) == value


def test_restart_does_not_replay_an_uncertain_free_credit_round(tmp_path):
    first = ArcadeJournal('f2p', tmp_path)
    first.reserve_round(10, 5, 'before.png')
    resumed = ArcadeJournal('f2p', tmp_path)
    assert resumed.pending()['start_clicks'] == 1
    with pytest.raises(ScriptError):
        resumed.reserve_round(10, 5, 'another.png')
    with pytest.raises(ScriptError):
        resumed.reserve_claim('2026-11-02', 10, 'claim.png')
    assert len(resumed.data['rounds']) == 1


@pytest.mark.parametrize('credits,tickets,evidence', [(10, 900, 'receipt.png'), (5, 0, 'receipt.png'),
                                                     (5, 901, 'receipt.png'), (5, 900, '')])
def test_receipt_needs_both_exact_credit_debit_and_ticket_proof(tmp_path, credits, tickets, evidence):
    journal = ArcadeJournal('alas', tmp_path)
    journal.reserve_round(10, 5, 'before.png')
    with pytest.raises(ScriptError):
        journal.finish_round(credits, tickets, evidence)
    assert journal.pending() is not None


def test_two_distinct_idle_rounds_and_a_partial_monthly_cap_are_recorded(tmp_path):
    journal = ArcadeJournal('alas', tmp_path)
    first = journal.reserve_round(10, 5, 'before1.png')
    journal.finish_round(5, 900, 'tickets1.png')
    second = journal.reserve_round(5, 5, 'before2.png')
    journal.finish_round(0, 620, 'tickets2.png')
    assert first['id'] != second['id'] and journal.pending() is None
    assert sum(row['tickets'] for row in ArcadeJournal('alas', tmp_path).data['rounds']) == 1520


def test_claim_requires_space_and_cannot_be_repeated_after_restart(tmp_path):
    journal = ArcadeJournal('f2p', tmp_path)
    with pytest.raises(ScriptError):
        journal.reserve_claim('2026-11-02', 31, 'full.png')
    journal.reserve_claim('2026-11-02', 0, 'before.png')
    with pytest.raises(ScriptError):
        ArcadeJournal('f2p', tmp_path).reserve_round(10, 5, 'unsettled.png')
    journal.finish_claim('2026-11-02', 10, 'credit-receipt.png')
    with pytest.raises(ScriptError):
        ArcadeJournal('f2p', tmp_path).reserve_claim('2026-11-02', 10, 'duplicate.png')


def test_corrupt_or_foreign_journal_cannot_authorize_another_start(tmp_path):
    (tmp_path / 'alas.json').write_text('{"version": 1, "account": "f2p", "rounds": [], "claims": {}}')
    with pytest.raises(ScriptError):
        ArcadeJournal('alas', tmp_path)
    (tmp_path / 'alas.json').write_text('{bad json')
    with pytest.raises(ScriptError):
        ArcadeJournal('alas', tmp_path)


def test_unknown_round_status_cannot_silently_drop_an_uncertain_start(tmp_path):
    journal = ArcadeJournal('alas', tmp_path)
    journal.reserve_round(10, 5, 'before.png')
    journal.data['rounds'][0]['status'] = 'unknown'
    journal.save()
    with pytest.raises(ScriptError):
        ArcadeJournal('alas', tmp_path)
