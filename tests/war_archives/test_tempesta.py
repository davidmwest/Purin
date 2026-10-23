"""Ordinary archived Tempesta maps retain the handwritten event combat logic."""
import importlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from module.base.template import Template
from module.base.utils import load_image
from module.campaign.stage import load_stage, stage_names
from module.war_archives.catalog import CLIENT_DATA_REVISION, get_archive
from module.war_archives import progress


DATA = Path(__file__).parent / 'data'
REMAP = json.loads((DATA / 'tempesta_chapter_remap.json').read_text())
ARCHIVES = ('war_archives_20241024_cn', 'war_archives_20251023_cn')


@pytest.mark.parametrize('proof', REMAP['stages'], ids=lambda proof: f"{proof['archive']}/{proof['stage']}")
def test_pinned_ordinary_remap_preserves_grid_boss_and_spawn_inputs(proof):
    assert REMAP['revision'] == CLIENT_DATA_REVISION
    assert REMAP['source'] == 'EN/sharecfgdata/chapter_template.lua'
    assert proof['unchanged_fields_sha256'] == proof['remapped_fields_sha256']
    assert set(proof['unchanged_fields_sha256']) == {
        'chapter_name', 'name', 'type', 'grids', 'boss_expedition_id',
        'enemy_refresh', 'ai_refresh', 'boss_refresh', 'is_ai', 'pos_x', 'pos_y',
    }
    assert proof['type'] == 1 and proof['chapter_name'].lower() == proof['stage']
    number = int(proof['stage'][1:])
    expected_map = (2200180 if proof['archive'] == ARCHIVES[0] else 2200190) + (number > 3)
    assert proof['map'] == proof['formation'] == expected_map
    stage = load_stage(proof['archive'], proof['stage'])
    assert list(stage.MAP.shape) == proof['shape']
    assert next(spawn['battle'] for spawn in stage.MAP.spawn_data if spawn.get('boss')) == proof['boss_refresh']


@pytest.mark.parametrize('archive', ARCHIVES)
def test_only_six_proven_normal_stages_are_exposed(archive):
    definition = get_archive(archive)
    assert definition.supported
    assert definition.normal_stages == tuple(f't{n}' for n in range(1, 7))
    assert definition.hard_stages == ()
    assert set(stage_names(archive)) == set(definition.normal_stages)
    assert definition.prizes[0].stage == 't6'
    assert definition.prizes[0].mode == 'normal'
    assert definition.prizes[0].clears == 60


@pytest.mark.parametrize('archive', ARCHIVES)
@pytest.mark.parametrize('name', [f't{n}' for n in range(1, 7)])
def test_configs_keep_original_detection_and_combat_settings(archive, name):
    original = load_stage(archive.replace('war_archives_', 'event_'), name)
    archived = load_stage(archive, name)
    for attr in dir(original.Config):
        if attr.isupper() and attr not in {'STAGE_ENTRANCE', 'MAP_CHAPTER_SWITCH_20241219'}:
            assert getattr(archived.Config, attr) == getattr(original.Config, attr), attr
    assert archived.Config.STAGE_ENTRANCE == ['normal']
    assert archived.Config.MAP_CHAPTER_SWITCH_20241219 is False
    owner = archived.Campaign.__new__(archived.Campaign)
    owner.ui_goto_event = Mock(side_effect=AssertionError('active event navigation'))
    assert owner.campaign_set_chapter_20241219('t', name[1:]) is False
    assert owner._campaign_separate_name(name) == ('t1' if int(name[1:]) <= 3 else 't2', name[1:])
    assert owner._campaign_get_chapter_index('t1' if int(name[1:]) <= 3 else 't2') == (1 if int(name[1:]) <= 3 else 2)


@pytest.mark.parametrize('name', ['t1', 't3', 't4', 't5'])
def test_sleeping_sea_handwritten_methods_and_custom_grids_are_not_copied_or_replaced(name):
    original = load_stage('event_20241024_cn', name)
    archived = load_stage(ARCHIVES[0], name)
    assert archived.MAP is original.MAP
    assert original.Campaign in archived.Campaign.__mro__
    assert archived.Campaign.ENEMY_FILTER == original.Campaign.ENEMY_FILTER
    for attr in dir(original.Campaign):
        if attr.startswith('battle_') or attr in {'grid_class', 'predict_enemy_genre', 'predict_boss'}:
            assert getattr(archived.Campaign, attr) is getattr(original.Campaign, attr), attr
    if name != 't1':
        assert archived.Campaign.grid_class is original.Campaign.grid_class


@pytest.mark.parametrize('archive,names', [
    (ARCHIVES[0], ['t2', 't6']), (ARCHIVES[1], [f't{n}' for n in range(1, 7)]),
])
def test_compact_maps_and_battle_plans_borrow_original_objects(archive, names):
    original = importlib.import_module(f"campaign.{archive.replace('war_archives_', 'event_')}.stages").STAGES
    archived = importlib.import_module(f'campaign.{archive}.stages').STAGES
    assert set(archived) == set(names)
    for name in names:
        before, after = original[name], archived[name]
        assert after.map is before.map
        assert after.battle is before.battle
        assert after.attrs is before.attrs
        assert after.config_base == before.config_base
        assert after.enemy_filter == before.enemy_filter
        assert after.config == {**before.config, 'STAGE_ENTRANCE': ['normal'], 'MAP_CHAPTER_SWITCH_20241219': False}
        assert after.base == '.campaign_base:CampaignBase'


def card_reader(image, monkeypatch):
    # Real exact EN identity templates; no device or game navigation is constructed.
    source = importlib.import_module('module.war_archives.dictionary').dic_archives_template
    templates = {name: Template(template.raw_file['en']) for name, template in source.items()}
    monkeypatch.setattr(progress, 'dic_archives_template', templates)
    owner = progress.ArchiveProgress.__new__(progress.ArchiveProgress)
    owner.device = SimpleNamespace(image=image)
    owner._list_page = Mock(return_value=True)
    return owner


def test_actual_current_archive_cards_are_exact_identities_with_clipped_progress_unknown(monkeypatch):
    image = load_image(str(DATA / 'alas_tempesta_archive_cards.png'))
    owner = card_reader(image, monkeypatch)
    observed = {card['archive']: card for card in owner.read_cards()}
    assert set(observed) == set(ARCHIVES)
    for name, area in [(ARCHIVES[0], [218, 508, 535, 624]),
                       (ARCHIVES[1], [736, 542, 1080, 610])]:
        card = observed[name]
        assert card['identity_area'] == area
        assert card['completion'] is None and card['card_area'] is None
        assert not card['fully_observed']
        assert card['guaranteed_prize_claimed'] is None


@pytest.mark.parametrize('damage', ['missing', 'occluded'])
def test_missing_or_occluded_identity_cannot_be_a_supported_card(monkeypatch, damage):
    image = load_image(str(DATA / 'alas_tempesta_archive_cards.png'))
    if damage == 'missing':
        image[508:624, 218:535] = 0
    else:
        image[508:624, 218:535] = 240
    observed = card_reader(image, monkeypatch).read_cards()
    assert ARCHIVES[0] not in {card['archive'] for card in observed}
    assert {card['archive'] for card in observed} == {ARCHIVES[1]}
