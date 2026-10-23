from dataclasses import replace

import pytest

from module.war_archives import planner
from module.war_archives.catalog import ARCHIVES, get_archive
from module.war_archives.state import ArchiveState


def single(target, **kwargs):
    plans, unsupported = planner.build_plan(target, **kwargs)
    assert unsupported == ()
    plan, = plans
    return plan


def test_easy_stories_keep_exact_normal_route_and_defer_hard_guarantees():
    plan = single('Visitors Dyed in Red', difficulty='easy', goal='items_and_stories')
    assert plan.mode == 'normal'
    assert [stage.stage for stage in plan.stages] == ['a1', 'a2', 'a3', 'b1', 'b2', 'b3']
    assert all(stage.mode == 'normal' and stage.goal == 'archive_clear' for stage in plan.stages)
    assert plan.prizes == ()
    prize, = plan.deferred_prizes
    assert (prize.name, prize.stage, prize.mode, prize.clears) == ('Zuikaku', 'd3', 'hard', 60)


def test_hard_route_preserves_hard_stages_and_accessible_counter_prize():
    plan = single('war_archives_20190321_en', difficulty='hard', goal='items_and_stories')
    assert [stage.stage for stage in plan.stages] == ['c1', 'c2', 'c3', 'd1', 'd2', 'd3']
    assert {stage.mode for stage in plan.stages} == {'hard'}
    assert [prize.name for prize in plan.prizes] == ['Zuikaku']
    assert plan.deferred_prizes == ()


def test_sp_category_interlude_has_real_hard_route_and_normal_prize_deferral():
    easy = single('Interlude of Illusions', difficulty='easy', goal='items_and_stories')
    hard = single('Interlude of Illusions', difficulty='hard', goal='items_and_stories')
    assert easy.archive.category == hard.archive.category == 'sp'
    assert [stage.stage for stage in easy.stages] == ['t1', 't2', 't3']
    assert [stage.stage for stage in hard.stages] == ['ht1', 'ht2', 'ht3']
    assert easy.prizes == () and [prize.name for prize in easy.deferred_prizes] == ["L'Audacieux"]
    assert hard.mode == 'hard' and hard.prizes == easy.deferred_prizes


def test_single_route_hard_preference_does_not_invent_a_hard_mode():
    plan = single('Crescendo of Polaris', difficulty='hard', goal='items_and_stories')
    assert plan.archive.name == 'Passionate Polaris' and plan.mode == 'normal'
    assert [stage.stage for stage in plan.stages] == ['sp1', 'sp2', 'sp3', 'sp4', 'sp5']
    assert [(prize.name, prize.stage, prize.mode) for prize in plan.prizes] == [('Cosmic Kicks', 'sp5', 'normal')]


def test_side_stage_order_is_not_removed_or_resorted_as_a_cutscene():
    plan = single('Empyreal Tragicomedy', difficulty='easy')
    assert [stage.stage for stage in plan.stages] == ['a1', 'a2', 'a3', 'as1', 'as2', 'b1', 'b2', 'b3']


def test_all_uses_supported_catalog_order_and_reports_every_missing_archive():
    plans, unsupported = planner.build_plan('ALL', difficulty='easy')
    assert [plan.archive for plan in plans] == [archive for archive in ARCHIVES if archive.supported]
    assert unsupported == tuple(archive.name for archive in ARCHIVES if not archive.supported)
    assert all(not plan.prizes and not plan.deferred_prizes for plan in plans)


def test_explicit_unsupported_title_never_becomes_a_guessed_folder():
    assert planner.build_plan('Frostfall') == ((), ('Frostfall',))


def test_prize_only_filters_no_prize_archives_and_keeps_easy_hard_deferred():
    plans, _ = planner.build_plan('all', difficulty='easy', prize='ZUikaku', prize_only=True)
    plan, = plans
    assert plan.archive.name == 'Visitors Dyed in Red'
    assert plan.prizes == () and plan.deferred_prizes[0].name == 'Zuikaku'
    by_id = single('Visitors Dyed in Red', difficulty='hard', prize='307061', prize_only=True)
    assert by_id.prizes == plan.deferred_prizes
    assert planner.build_plan('Strive, Wish and Strategize', prize_only=True) == ((), ())


@pytest.mark.parametrize('kwargs', [
    {'difficulty': 'normal'}, {'difficulty': 'easy', 'goal': 'items'},
    {'prize': 'random_drop', 'goal': 'items_and_stories'}, {'target': 'unknown'},
])
def test_invalid_route_or_non_guaranteed_prize_is_rejected(kwargs):
    with pytest.raises(ValueError):
        planner.build_plan(**kwargs)


def test_missing_local_stage_or_selected_prize_stage_fails_closed(monkeypatch):
    monkeypatch.setattr(planner, 'stage_names', lambda event: ['a1', 'a2', 'a3', 'b1', 'b2'])
    with pytest.raises(ValueError, match='Archive maps missing'):
        planner.build_plan('Visitors Dyed in Red', difficulty='easy')
    monkeypatch.setattr(planner, 'stage_names', lambda event: ['a1', 'a2', 'a3', 'b1', 'b2', 'b3'])
    with pytest.raises(ValueError, match='Archive prize stage missing'):
        planner.build_plan('Visitors Dyed in Red', difficulty='easy', goal='items_and_stories')


def test_empty_supported_route_cannot_vacuously_complete(monkeypatch):
    archive = replace(get_archive('Visitors Dyed in Red'), normal_stages=())
    monkeypatch.setattr(planner, 'get_archive', lambda target: archive)
    with pytest.raises(ValueError, match='has no normal stages'):
        planner.build_plan(archive.name, difficulty='easy')


def test_story_resume_uses_exact_mode_stage_proof_and_literal_completion(tmp_path):
    plan = single('Visitors Dyed in Red', difficulty='easy')
    state = ArchiveState('alas', str(tmp_path))
    first, second = plan.stages[:2]
    assert planner.next_story(plan, state) == first
    state.update(first.key.replace('/normal/', '/hard/'), complete=True)
    state.update(first.key, complete='yes')
    assert planner.next_story(plan, state) == first
    state.update(first.key, complete=True)
    assert planner.next_story(plan, ArchiveState('alas', str(tmp_path))) == second
    assert not planner.story_complete(plan, state)


def test_entire_verified_route_completes_stories_but_not_prizes(tmp_path):
    plan = single('Visitors Dyed in Red', difficulty='hard', goal='items_and_stories')
    state = ArchiveState('alas', str(tmp_path))
    for stage in plan.stages:
        state.update(stage.key, complete=True, boss_won=True)
    assert planner.story_complete(plan, state) and planner.next_story(plan, state) is None
    prize = plan.prizes[0]
    assert planner.next_prize(plan, state) == prize
    key = planner.prize_key(plan.archive, prize)
    state.update(key, clears=60, won=True)
    assert planner.next_prize(plan, state) == prize
    state.update(key, claimed=True)
    assert planner.next_prize(plan, ArchiveState('alas', str(tmp_path))) is None


def test_verified_story_satisfaction_is_shared_across_routes_but_not_accounts(tmp_path):
    normal = single('Visitors Dyed in Red', difficulty='easy')
    hard = single('Visitors Dyed in Red', difficulty='hard')
    main = ArchiveState('alas', str(tmp_path))
    main.update(planner.story_key(normal.archive), complete=True, verified_route='normal')
    assert planner.story_complete(hard, main)
    assert not planner.story_complete(hard, ArchiveState('f2p', str(tmp_path)))
