import ast
from dataclasses import FrozenInstanceError
from pathlib import Path
import re

import pytest

from module.war_archives.catalog import (
    ARCHIVES, ARCHIVES_BY_GAME_ID, ARCHIVES_BY_ID, CATALOG_AS_OF,
    CLIENT_DATA_REVISION, CLIENT_DATA_URL, DAILY_SORTIES, RAW_KEYS_CAPACITY,
    RAW_KEYS_DAILY, RAW_KEYS_PER_ENTRY, get_archive, guaranteed_prizes,
)


ROOT = Path(__file__).resolve().parents[2]


def test_complete_current_en_list_and_explicit_unsupported_entries():
    assert len(ARCHIVES) == 52
    assert set(ARCHIVES_BY_GAME_ID) == {1, 2, *range(4, 54)}
    assert len(ARCHIVES_BY_ID) == 50
    assert {a.name for a in ARCHIVES if not a.supported} == {
        'Frostfall', 'Welcome to Little Academy',
    }
    assert all(a.id is None for a in ARCHIVES if not a.supported)
    assert all(a.id and a.normal_stages for a in ARCHIVES if a.supported)


def test_local_support_is_exactly_the_existing_archive_navigation_dictionary():
    # Reading the AST avoids importing assets or initializing a game client.
    tree = ast.parse((ROOT / 'module/war_archives/dictionary.py').read_text())
    dictionary = next(node.value for node in tree.body if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id == 'dic_archives_template'
                              for target in node.targets))
    assert set(ARCHIVES_BY_ID) == {ast.literal_eval(key) for key in dictionary.keys}


@pytest.mark.parametrize('archive', [a for a in ARCHIVES if a.supported], ids=lambda a: a.name)
def test_every_supported_stage_has_a_real_local_campaign(archive):
    folder = ROOT / 'campaign' / archive.id
    names = {path.stem for path in folder.glob('*.py')}
    # Compact archive implementations generate ordinary stages from stages.py.
    compact = folder / 'stages.py'
    if compact.exists():
        names.update(re.findall(r"['\"]([a-z]+\d+)['\"]", compact.read_text()))
    assert set(archive.normal_stages + archive.hard_stages) <= names


def test_counter_guarantees_are_60_clears_on_a_real_route_and_not_generic_drops():
    prizes = [p for a in ARCHIVES for p in a.prizes]
    assert len(prizes) == 38
    assert all(p.clears == 60 and p.reward_kind in {'ship', 'equipment'} for p in prizes)
    for archive in ARCHIVES:
        assert len({p.id for p in archive.prizes}) == len(archive.prizes)
        assert len(set(archive.normal_stages)) == len(archive.normal_stages)
        assert len(set(archive.hard_stages)) == len(archive.hard_stages)
        assert not set(archive.normal_stages) & set(archive.hard_stages)
        for prize in archive.prizes:
            assert prize.stage in archive.stages(prize.mode)
    assert not guaranteed_prizes('Strive, Wish and Strategize')
    assert not guaranteed_prizes('The Pursuit of Graf Spee')
    assert not guaranteed_prizes('Anthem of Remembrance')


@pytest.mark.parametrize('archive_name,prize_name,stage', [
    ('Fallen Wings', 'North Carolina', 'd3'),
    ("Khorovod of Dawn's Rime", 'Tallinn', 'd3'),
    ('Tower of Transcendence', 'Magdeburg', 'd3'),
    ("Dreamwaker's Butterfly", 'Kii', 'ht6'),
    ('Confluence of Nothingness', 'U-556 META', 'ht6'),
    ('Divergent Chessboard', 'Z46', 'd4'),
    ('Ink-Stained Steel Sakura', 'Kawakaze', 'd2'),
])
def test_specific_guarantees_do_not_use_other_banner_or_random_drop_ships(archive_name, prize_name, stage):
    prize, = guaranteed_prizes(archive_name)
    assert (prize.name, prize.stage, prize.mode, prize.reward_kind) == (prize_name, stage, 'hard', 'ship')


def test_sp_category_does_not_erase_interlude_hard_difficulty():
    archive = get_archive('Interlude of Illusions')
    assert archive.category == 'sp'
    assert archive.normal_stages == ('t1', 't2', 't3')
    assert archive.hard_stages == ('ht1', 'ht2', 'ht3')
    assert guaranteed_prizes(archive.id, 'normal') == ()
    prize, = guaranteed_prizes(archive.id, 'hard')
    assert (prize.name, prize.stage) == ("L'Audacieux", 'ht3')


@pytest.mark.parametrize('title,name,stage,kind', [
    ('Passionate Polaris', 'Cosmic Kicks', 'sp5', 'equipment'),
    ('Universe in Unison', 'Celestial Body', 'sp5', 'equipment'),
    ('Virtual Tower', 'Pompeo Magno', 'sp4', 'ship'),
    ('Operation Convergence', 'Blücher', 'sp4', 'ship'),
    ('Tempesta and the Fountain of Youth', 'Mary Celeste', 't6', 'ship'),
    ('Tempesta and the Sleeping Sea', 'Amity', 't6', 'ship'),
    ('Tempesta and Islas de Libertád', 'Pearl', 't6', 'ship'),
])
def test_single_route_guarantees_remain_normal_without_fabricated_hard_mode(title, name, stage, kind):
    archive = get_archive(title)
    prize, = guaranteed_prizes(title, 'normal')
    assert (prize.name, prize.stage, prize.reward_kind) == (name, stage, kind)
    assert archive.hard_stages == ()
    assert guaranteed_prizes(title, 'hard') == ()


def test_side_stage_order_is_preserved_and_not_classified_as_cutscene_only():
    assert get_archive('Empyreal Tragicomedy').normal_stages == (
        'a1', 'a2', 'a3', 'as1', 'as2', 'b1', 'b2', 'b3')
    assert get_archive('Tower of Transcendence').hard_stages == (
        'c1', 'c2', 'c3', 'd1', 'd2', 'ds1', 'd3')
    assert get_archive('Stars of the Shimmering Fjord').normal_stages[0] == 'sp0'
    assert get_archive("Dreamwaker's Butterfly").hard_stages[-2:] == ('hts1', 'hts2')


def test_lookup_aliases_are_explicit_and_unsupported_folders_are_never_invented():
    assert get_archive('  crescendo of polaris  ') is get_archive('Passionate Polaris')
    assert get_archive('Encircling Graf Spee') is get_archive('The Pursuit of Graf Spee')
    assert get_archive('Prelude Under the Moon') is get_archive('Moonlit Overture')
    assert get_archive('The Way Home in the Night') is get_archive('The Way Home in the Dark')
    assert get_archive('war_archives_20190321_en').game_id == 1
    assert not get_archive('Frostfall').supported
    for identifier in (None, 50, 'war_archives_20260709_cn', 'Unknown archive'):
        with pytest.raises(ValueError):
            get_archive(identifier)
    with pytest.raises(ValueError):
        guaranteed_prizes('Frostfall', 'easy')


def test_catalog_and_indexes_are_immutable():
    with pytest.raises(FrozenInstanceError):
        ARCHIVES[0].supported = False
    with pytest.raises(FrozenInstanceError):
        ARCHIVES[0].prizes[0].clears = 1
    with pytest.raises(TypeError):
        ARCHIVES_BY_ID['fabricated'] = ARCHIVES[0]
    with pytest.raises(TypeError):
        ARCHIVES_BY_GAME_ID[3] = ARCHIVES[0]


def test_current_raw_key_units_are_per_entry_and_not_the_legacy_timed_permit():
    assert (RAW_KEYS_DAILY, RAW_KEYS_CAPACITY, RAW_KEYS_PER_ENTRY, DAILY_SORTIES) == (20, 300, 5, 4)
    assert CATALOG_AS_OF == '2026-10-23'
    assert re.fullmatch('[0-9a-f]{40}', CLIENT_DATA_REVISION)
    assert f'/blob/{CLIENT_DATA_REVISION}/EN/sharecfg/re_map_template.lua' in CLIENT_DATA_URL


def test_documentation_lists_every_archive_and_explains_unknown_support():
    document = (ROOT / 'docs/WAR_ARCHIVES.md').read_text()
    assert all(archive.name in document for archive in ARCHIVES)
    assert all(archive.id in document for archive in ARCHIVES if archive.supported)
    assert '5 keys per fresh stage entry' in document
    assert 'not a timed access permit' in document
