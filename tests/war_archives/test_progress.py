from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from module.base.button import Button
from module.base.template import Template
from module.exception import GameNotRunningError, ScriptError
from module.ui.page import page_archives, page_main
from module.war_archives import progress as p


class ReadyTimer:
    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        return self

    def reset(self):
        return self

    def reached(self):
        return True


@pytest.fixture
def owner(monkeypatch):
    monkeypatch.setattr(p, 'Timer', ReadyTimer)
    result = p.ArchiveProgress.__new__(p.ArchiveProgress)
    result.config = SimpleNamespace(config_name='alas')
    result.device = SimpleNamespace(image=np.zeros((720, 1280, 3), dtype=np.uint8),
                                    click=Mock(), swipe=Mock(), screenshot=Mock(),
                                    stuck_record_clear=Mock(), app_is_running=Mock(return_value=True))
    result.appear = Mock(return_value=False)
    result.loop = lambda **kwargs: iter(range(5))
    return result


def card(name='war_archives_20190321_en'):
    return dict(archive=name, identity_area=[150, 200, 234, 223])


def record():
    return dict(selector_clicks=0, scroll_drags=0, diagnostics=[], pages=[])


@pytest.mark.parametrize('text,expected', [('0/60', 0), ('60/60', 60), ('25/60', 25),
                                         (' 5/60 ', 5), ('', None), ('560', None),
                                         ('1/6', None), ('61/60', None), ('05/60', None),
                                         ('O/60', None), ('-1/60', None), ('250/300', 250),
                                         ('300/300', 300), ('301/300', None)])
def test_strict_raw_key_reader_does_not_turn_unknown_into_zero(text, expected):
    assert p.parse_raw_keys(text) == expected


def test_key_denominator_is_preserved_without_inventing_sortie_conversion():
    assert p.parse_key_counter('250/300') == dict(raw_keys=250, key_capacity=300)
    assert p.parse_key_counter('50/60') == dict(raw_keys=50, key_capacity=60)
    assert p.parse_key_counter('5/999') is None


def test_known_identity_template_requires_visible_anchor_and_leaves_progress_unknown(owner, monkeypatch):
    # Synthetic placement of an existing real identity template, not a UI fixture.
    template = Template('./assets/en/war_archives/TEMPLATE_VISITORS_DYED_IN_RED.png')
    height, width = template.image.shape[:2]
    owner.device.image[200:200 + height, 150:150 + width] = template.image
    owner._list_page = Mock(return_value=True)
    monkeypatch.setattr(p, 'dic_archives_template', {'war_archives_20190321_en': template})
    observed = owner.read_cards()
    assert len(observed) == 1
    assert observed[0]['identity_area'] == [150, 200, 150 + width, 200 + height]
    assert observed[0]['completion'] is None
    assert observed[0]['guaranteed_prize_counter'] is None
    assert observed[0]['guaranteed_prize_claimed'] is None
    assert observed[0]['unlocked'] is None and not observed[0]['fully_observed']
    owner.device.image[:] = 0
    owner.device.image[85:85 + height, 150:150 + width] = template.image
    assert owner.read_cards() == []  # Header/cutoff anchors cannot be treated as cards.
    owner._list_page.return_value = False
    assert owner.read_cards() == []


@pytest.mark.parametrize('overlay', [p.IN_MAP, p.MAP_PREPARATION, p.FLEET_PREPARATION,
                                     p.BATTLE_PREPARATION, p.GET_ITEMS_1, p.GET_SHIP,
                                     p.POPUP_CONFIRM, p.POPUP_CANCEL_WHITE])
def test_list_header_beneath_overlay_does_not_authorize_inputs(owner, overlay):
    owner.appear = lambda button, **kwargs: button in (p.WAR_ARCHIVES_CHECK, overlay)
    assert not owner._list_page()


def test_category_selection_never_clicks_unknown_or_ambiguous_state(owner):
    owner._list_page = Mock(return_value=True)
    owner._category = Mock(return_value=None)
    assert not owner._select_category('sp', record(), Mock())
    owner.device.click.assert_not_called()


def test_category_selection_reserves_before_each_of_at_most_two_inputs(owner):
    owner._list_page = Mock(return_value=True)
    owner._category = Mock(return_value='ex')
    current = record()
    snapshots = []
    persist = lambda: snapshots.append(current['selector_clicks'])
    owner.device.click.side_effect = lambda button: snapshots.append(('clicked', current['selector_clicks']))
    assert not owner._select_category('sp', current, persist)
    assert snapshots == [1, ('clicked', 1), 2, ('clicked', 2)]
    assert owner.device.click.call_count == 2


def test_scroll_is_one_bounded_observed_thumb_drag_with_preinput_journal(owner):
    info = dict(top=100, bottom=200, position=.2)
    owner._list_page = Mock(return_value=True)
    owner._scroll_info = Mock(return_value=info)
    current = record()
    saved = []
    owner.device.swipe.side_effect = lambda *args, **kwargs: saved.append(('swiped', current['scroll_drags']))
    assert owner._drag_scroll(info, False, current, lambda: saved.append(current['scroll_drags']))
    assert saved == [1, ('swiped', 1)]
    start, end = owner.device.swipe.call_args.args
    assert start[0] == end[0] and start[1] < end[1] < 695
    owner._scroll_info.return_value = dict(top=101, bottom=201, position=.2)
    assert not owner._drag_scroll(info, False, current, Mock())
    assert owner.device.swipe.call_count == 1


def test_absent_or_fragmented_scrollbar_is_not_a_drag_target(owner, monkeypatch):
    monkeypatch.setattr(p.ARCHIVE_SCROLL, 'match_color', Mock(return_value=np.zeros(565, dtype=bool)))
    assert owner._scroll_info() is None
    fragmented = np.zeros(565, dtype=bool)
    fragmented[[0, 10, 20, 30]] = True
    p.ARCHIVE_SCROLL.match_color.return_value = fragmented
    assert owner._scroll_info() is None


def test_settled_list_requires_known_cards_and_matching_category(owner):
    owner._list_page = Mock(return_value=True)
    owner._category = Mock(return_value='ex')
    owner._scroll_info = Mock(return_value=None)
    owner.read_cards = Mock(return_value=[])
    assert owner._settled_list('ex') is None
    owner.device.stuck_record_clear.assert_not_called()
    owner.read_cards.return_value = [card()]
    assert owner._settled_list('sp') is None
    assert owner._settled_list('ex') == ([card()], None)
    owner.device.stuck_record_clear.assert_called_once()


def test_repeated_page_stops_after_one_drag_and_does_not_loop(owner, monkeypatch, tmp_path):
    info = dict(top=0, bottom=100, position=0)
    owner._select_category = Mock(return_value=True)
    owner._settled_list = Mock(return_value=([card()], info))
    owner._drag_scroll = Mock(return_value=True)
    owner._capture = Mock(return_value='list.png')
    monkeypatch.setattr(p.ARCHIVE_KEYS, 'ocr', Mock(return_value='7/60'))
    current = record()
    assert owner._observe_category('ex', tmp_path, current, Mock(), 20, float('inf')) == 'repeated_page'
    assert len(current['pages']) == 1 and current['pages'][0]['raw_keys'] == 7
    owner._drag_scroll.assert_called_once()


def test_bottom_page_is_captured_without_any_further_scroll(owner, monkeypatch, tmp_path):
    owner._select_category = Mock(return_value=True)
    owner._settled_list = Mock(side_effect=[
        ([card()], dict(top=0, bottom=100, position=0)),
        ([card('war_archives_20190307_en')], dict(top=465, bottom=565, position=1)),
    ])
    owner._drag_scroll = Mock(return_value=True)
    owner._capture = Mock(return_value='bottom.png')
    monkeypatch.setattr(p.ARCHIVE_KEYS, 'ocr', Mock(return_value=''))
    current = record()
    assert owner._observe_category('ex', tmp_path, current, Mock(), 20, float('inf')) == 'bottom_observed'
    assert len(current['pages']) == 2 and current['pages'][1]['raw_keys'] is None
    owner._drag_scroll.assert_called_once()


def test_page_limit_is_bounded(owner, monkeypatch, tmp_path):
    owner._select_category = Mock(return_value=True)
    owner._settled_list = Mock(side_effect=[([card()], dict(top=index, bottom=index + 100, position=0))
                                            for index in range(4)])
    owner._drag_scroll = Mock(return_value=True)
    owner._capture = Mock(return_value='page.png')
    monkeypatch.setattr(p.ARCHIVE_KEYS, 'ocr', Mock(return_value='10/60'))
    current = record()
    assert owner._observe_category('ex', tmp_path, current, Mock(), 3, float('inf')) == 'page_limit'
    assert len(current['pages']) == 3
    assert owner._drag_scroll.call_count == 3


def test_observation_is_durable_before_navigation_and_same_request_never_replays(owner, monkeypatch, tmp_path):
    def navigate(main, page, **kwargs):
        import json
        state = json.loads((tmp_path / 'alas' / 'request-1' / 'observation.json').read_text())
        assert state['account'] == 'alas' and state['verified_for_execution'] is False
        return True

    navigation = Mock(side_effect=navigate)
    monkeypatch.setattr(p, 'ensure_event_page', navigation)
    owner._observe_category = Mock(return_value='bottom_observed')
    owner._list_page = Mock(return_value=True)
    first = owner.run_observe(folder=tmp_path, request_id='request-1')
    second = owner.run_observe(folder=tmp_path, request_id='request-1')
    assert first['status'] == 'observed' and first['returned_main']
    assert not first['combat_attempted'] and not first['verified_for_execution']
    assert second['reused'] is True
    assert owner._observe_category.call_count == 2
    assert [call.args[1] for call in navigation.call_args_list] == [page_archives, page_main]
    owner.device.click.assert_not_called()
    owner.device.swipe.assert_not_called()


def test_unknown_navigation_is_captured_without_further_controls(owner, monkeypatch, tmp_path):
    monkeypatch.setattr(p, 'ensure_event_page', Mock(return_value=False))
    owner._capture = Mock(return_value='unknown.png')
    owner._observe_category = Mock()
    result = owner.run_observe(folder=tmp_path, request_id='unknown')
    assert result['status'] == 'blocked' and result['reason'] == 'archive_navigation_unrecognized'
    owner._capture.assert_called_once()
    owner._observe_category.assert_not_called()
    assert p.ensure_event_page.call_count == 1


def test_closed_game_spends_no_observation_budget(owner, tmp_path):
    owner.device.app_is_running.return_value = False
    with pytest.raises(GameNotRunningError):
        owner.run_observe(folder=tmp_path, request_id='closed')
    assert not list(tmp_path.rglob('*'))


@pytest.mark.parametrize('request_id', ['../live', '/etc', 'a' * 81])
def test_invalid_explicit_request_cannot_escape_audit_folder(owner, tmp_path, request_id):
    with pytest.raises(ScriptError):
        owner.run_observe(folder=tmp_path, request_id=request_id)
    owner.device.app_is_running.assert_not_called()


def test_corrupt_or_cross_account_request_fails_closed_before_device(owner, tmp_path):
    path = tmp_path / 'alas' / 'same' / 'observation.json'
    path.parent.mkdir(parents=True)
    path.write_text('{bad')
    with pytest.raises(ScriptError):
        owner.run_observe(folder=tmp_path, request_id='same')
    path.write_text('{"account":"f2p","request_id":"same","version":1}')
    with pytest.raises(ScriptError):
        owner.run_observe(folder=tmp_path, request_id='same')
    owner.device.app_is_running.assert_not_called()


def actual_image(account):
    from module.base.utils import load_image
    return load_image(f'tests/war_archives/data/{account}_archive_list.png')


def use_en_identities(monkeypatch):
    monkeypatch.setattr(p, 'dic_archives_template', {
        name: Template(original.raw_file['en']) for name, original in p.dic_archives_template.items()})


@pytest.mark.parametrize('text,expected', [('0%', 0), ('100%', 100), ('53%', 53),
                                         ('', None), ('0', None), ('101%', None),
                                         ('00%', None), ('O%', None), ('-1%', None)])
def test_strict_completion_keeps_unknown_distinct(text, expected):
    assert p.parse_completion(text) == expected


@pytest.mark.parametrize('account,expected,keys', [('alas', 100, 300), ('f2p', 0, 80)])
def test_actual_both_account_lists_identify_selected_ex_and_read_relative_completion(owner, monkeypatch,
                                                                                   account, expected, keys):
    owner.device.image = actual_image(account)
    use_en_identities(monkeypatch)
    # Local EN clones make this test independent of the global server cache.
    selected = {}
    for name, original in p.CATEGORY_BUTTONS.items():
        selected[name] = Button(area=original.raw_area['en'], color=original.raw_color['en'],
                                button=original.raw_button['en'], file=original.raw_file['en'], name=name)
    monkeypatch.setattr(p, 'CATEGORY_BUTTONS', selected)
    owner.match_template_color = lambda button, **kwargs: button.match_template_color(owner.device.image, **kwargs)
    assert all(button.match(owner.device.image, offset=(3, 3)) for button in selected.values())
    assert owner._category() == 'ex'  # Shape-only matching falsely recognized both.
    owner._list_page = Mock(return_value=True)
    observed = owner.read_cards()
    assert len(observed) == 4
    assert {item['completion'] for item in observed} == {expected}
    assert all(item['completion_verified'] and item['card_area'] for item in observed)
    assert not any(item['fully_observed'] for item in observed)  # Prize/lock status still unknown.
    # Real template matching produces NumPy coordinates; a durable pre-input
    # journal must encode them without a special JSON encoder.
    import json
    assert json.loads(json.dumps(observed)) == observed
    assert all(type(value) is int for item in observed
               for key in ('identity_area', 'card_area') for value in item[key])
    assert p.parse_key_counter(p.ARCHIVE_KEYS.ocr(owner.device.image)) == dict(raw_keys=keys, key_capacity=300)


def test_percentage_without_identity_or_visible_header_never_becomes_completion(owner, monkeypatch):
    owner.device.image = actual_image('f2p')
    use_en_identities(monkeypatch)
    owner._list_page = Mock(return_value=True)
    owner.device.image[126:145, 88:148] = 0
    observed = {item['archive']: item for item in owner.read_cards()}
    assert observed['war_archives_20190321_en']['completion'] is None
    assert not observed['war_archives_20190321_en']['completion_verified']
    monkeypatch.setattr(p, 'dic_archives_template', {})
    assert owner.read_cards() == []


def full_card():
    return dict(card(), card_area=[88, 146, 558, 375], completion=0, completion_verified=True)


def test_archive_card_enter_is_once_durable_and_never_stage_or_claim_input(owner, monkeypatch, tmp_path):
    import json
    monkeypatch.setattr(p, 'ensure_event_page', Mock(return_value=True))
    monkeypatch.setattr(p.ARCHIVE_KEYS, 'ocr', Mock(return_value='80/300'))
    owner._find_archive = Mock(return_value=full_card())
    owner.read_cards = Mock(return_value=[full_card()])
    owner._list_page = Mock(return_value=True)
    owner._archive_stage_page = Mock(return_value=True)
    owner._capture = Mock(return_value='stage.png')
    def click(button):
        journal = json.loads((tmp_path / 'alas' / 'one' / 'archive.json').read_text())
        assert journal['entrance_clicks'] == 1 and not journal['verified_for_execution']
        assert button.name == 'ARCHIVE_CARD_ENTRANCE'
    owner.device.click.side_effect = click
    first = owner.observe_archive('war_archives_20190321_en', folder=tmp_path, request_id='one')
    second = owner.observe_archive('war_archives_20190321_en', folder=tmp_path, request_id='one')
    assert first['status'] == 'observed' and first['raw_keys'] == 80 and first['key_capacity'] == 300
    assert not first['verified_for_navigation'] and not first['verified_for_execution']
    assert second['reused'] and owner.device.click.call_count == 1
    assert owner._capture.call_count == 2


def test_changed_card_and_unknown_entry_never_receive_repeat_click(owner, monkeypatch, tmp_path):
    monkeypatch.setattr(p, 'ensure_event_page', Mock(return_value=True))
    monkeypatch.setattr(p.ARCHIVE_KEYS, 'ocr', Mock(return_value='300/300'))
    owner._find_archive = Mock(return_value=full_card())
    owner.read_cards = Mock(return_value=[])
    owner._list_page = Mock(return_value=True)
    owner._capture = Mock(return_value='unknown.png')
    blocked = owner.observe_archive('war_archives_20190321_en', folder=tmp_path, request_id='changed')
    assert blocked['reason'] == 'archive_card_changed'
    owner.device.click.assert_not_called()
    owner.read_cards.return_value = [full_card()]
    owner._archive_stage_page = Mock(return_value=False)
    blocked = owner.observe_archive('war_archives_20190321_en', folder=tmp_path, request_id='unknown')
    assert blocked['reason'] == 'archive_entrance_unrecognized'
    owner.device.click.assert_called_once()


def test_goal_unknowns_cannot_authorize_execution(owner):
    from module.campaign.campaign_stars import StageGoal
    with pytest.raises(ScriptError):
        owner.proof_for_goal(StageGoal('war_archives_20190321_en', 'd3', 'normal'))
    owner.device.click.assert_not_called()


def test_prize_claim_uses_identity_bound_adapter(owner, monkeypatch, tmp_path):
    from module.war_archives.catalog import get_archive
    from module.war_archives.prize_progress import ArchivePrizeProgress
    archive = get_archive('war_archives_20190321_en')
    expected = dict(verified=False, count=None, total=60, claimed=None, returned_stage=False)
    claim = Mock(return_value=expected)
    monkeypatch.setattr(ArchivePrizeProgress, 'claim', claim, raising=False)
    result = owner.claim_prize(archive, archive.prizes[0], folder=tmp_path, request_id='claim')
    assert result is expected
    claim.assert_called_once_with(archive.id, archive.prizes[0], folder=tmp_path, request_id='claim')
    owner.device.click.assert_not_called()


VISITORS = 'war_archives_20190321_en'


def stage_goal(stage='a1', mode='normal'):
    from module.campaign.campaign_stars import StageGoal
    return StageGoal(VISITORS, stage, mode, 'archive_clear')


def stage_screen(mode='normal', page=1, stage='a1', complete=False, raw_keys=80, key_capacity=300):
    return dict(event=VISITORS, mode=mode, page=page,
                stages={stage: dict(entrance_area=[427, 207, 442, 226], complete=complete)},
                raw_keys=raw_keys, key_capacity=key_capacity)


def stage_record():
    return dict(navigation_inputs=[], diagnostics=[], verified_for_navigation=False,
                verified_for_execution=False)


def use_actual_en_page_detectors(owner):
    # Read real page/overlay assets without relying on another test setting the
    # process-global server cache before this module is imported.
    clones = {}
    def appear(button, offset=(20, 20), **kwargs):
        if button not in clones:
            en = lambda raw: raw.get('en') if isinstance(raw, dict) else raw
            clones[button] = Button(area=en(button.raw_area), color=en(button.raw_color),
                                    button=en(button.raw_button), file=en(button.raw_file))
        return clones[button].match(owner.device.image, offset=offset)
    owner.appear = appear


@pytest.mark.parametrize('account,keys,complete,names', [
    ('f2p', 80, False, {'a1'}), ('alas', 300, True, {'a1', 'a2'})])
def test_actual_stage_pages_read_unlocked_entrances_and_clear_separately(owner, account, keys, complete, names):
    from module.base.utils import load_image
    owner.device.image = load_image(f'tests/war_archives/data/{account}_visitors_stages.png')
    use_actual_en_page_detectors(owner)
    assert owner._archive_stage_page()
    result = owner._read_stage_screen(VISITORS)
    assert result['event'] == VISITORS and result['mode'] == 'normal' and result['page'] == 1
    assert set(result['stages']) == names
    assert result['stages']['a1']['complete'] is complete
    assert result['raw_keys'] == keys and result['key_capacity'] == 300
    if account == 'alas':
        assert result['stages']['a2']['complete'] is False
    import json
    assert json.loads(json.dumps(result)) == result
    assert all(type(x) is int for stage in result['stages'].values() for x in stage['entrance_area'])
    owner.device.click.assert_not_called()


@pytest.mark.parametrize('negative', ['title', 'overlay', 'no_stages', 'dimmed'])
def test_actual_stage_page_missing_identity_or_overlay_cannot_authorize(owner, negative):
    from module.base.utils import load_image
    owner.device.image = load_image('tests/war_archives/data/f2p_visitors_stages.png')
    owner._archive_stage_page = Mock(return_value=negative != 'overlay')
    if negative == 'title':
        owner.device.image[70:110, 210:530] = 0
    elif negative == 'no_stages':
        owner.device.image[117:636, 87:1151] = 0
    elif negative == 'dimmed':
        owner.device.image = (owner.device.image * .5).astype(np.uint8)
    assert owner._read_stage_screen(VISITORS) is None
    owner.device.click.assert_not_called()


@pytest.mark.parametrize('overlay', [p.GET_ITEMS_2, p.POPUP_CONFIRM, p.POPUP_CANCEL_WHITE])
def test_stage_header_under_known_popup_never_authorizes_stage_navigation(owner, overlay):
    owner._list_page = Mock(return_value=False)
    owner.appear = lambda button, **kwargs: button in (p.WAR_ARCHIVES_CAMPAIGN_CHECK, overlay)
    assert not owner._archive_stage_page()


def test_actual_stage_controls_match_only_the_visible_known_next_and_hard(owner):
    from module.base.utils import load_image
    owner.device.image = load_image('tests/war_archives/data/f2p_visitors_stages.png')
    hard = Button(area=p.SWITCH_1_HARD.raw_area['en'], color=(),
                  button=p.SWITCH_1_HARD.raw_button['en'], file=p.SWITCH_1_HARD.raw_file['en'])
    assert hard.match(owner.device.image, offset=(3, 3))
    assert p.ARCHIVE_NEXT.match(owner.device.image, offset=(3, 3), similarity=.90)
    owner.device.image[350:457, 1197:1264] = 0
    assert not p.ARCHIVE_NEXT.match(owner.device.image, offset=(3, 3), similarity=.90)


def test_mode_navigation_reserves_one_known_input_then_requires_fresh_target(owner):
    normal, hard = stage_screen(), stage_screen('hard', stage='c1')
    owner._read_stage_screen = Mock(side_effect=[normal, normal, hard, hard])
    owner.appear.return_value = True
    current, events = stage_record(), []
    owner.device.click.side_effect = lambda button: events.append(('click', button.name, list(current['navigation_inputs'])))
    owner._navigate_goal(stage_goal('c1', 'hard'), current,
                         lambda: events.append(('save', list(current['navigation_inputs']))))
    assert current['verified_for_navigation'] and current['verified_for_execution']
    assert current['stage_complete'] is False and current['unlocked'] is True
    assert events[0] == ('save', ['mode:normal:hard'])
    assert events[1][0] == 'click' and events[1][2] == ['mode:normal:hard']
    owner.device.click.assert_called_once_with(p.SWITCH_1_HARD)


def test_stale_mode_after_click_never_replays_and_unknown_controls_never_click(owner):
    owner._read_stage_screen = Mock(return_value=stage_screen())
    owner.appear.return_value = True
    current = stage_record()
    owner._navigate_goal(stage_goal('c1', 'hard'), current, Mock())
    assert not current['verified_for_navigation'] and current['reason'] == 'stage_navigation_unverified'
    owner.device.click.assert_called_once()
    owner.device.click.reset_mock()
    current['navigation_inputs'] = []
    owner.appear.return_value = False
    owner._navigate_goal(stage_goal('c1', 'hard'), current, Mock())
    owner.device.click.assert_not_called()


def test_page_navigation_uses_known_next_once_and_does_not_skip_locked_same_page(owner):
    first, second = stage_screen(), stage_screen(page=2, stage='b1')
    owner._read_stage_screen = Mock(side_effect=[first, first, second, second])
    owner.appear.return_value = True
    current = stage_record()
    owner._navigate_goal(stage_goal('b1'), current, Mock())
    assert current['verified_for_navigation']
    owner.device.click.assert_called_once_with(p.ARCHIVE_NEXT)
    assert current['navigation_inputs'] == ['page:normal:1:2']
    owner.device.click.reset_mock()
    owner._read_stage_screen = Mock(return_value=first)
    current = stage_record()
    owner._navigate_goal(stage_goal('a2'), current, Mock())
    assert current['reason'] == 'target_locked_or_unreadable'
    assert current['safe_target_return'] == dict(
        verified=True, proof_kind='fresh_exact_archive_stage', account='alas',
        event=VISITORS, stage='a2', mode='normal', page=1)
    assert not current['verified_for_navigation'] and not current['verified_for_execution']
    owner.device.click.assert_not_called()


def test_unknown_stage_page_has_no_safe_return_proof(owner):
    owner._read_stage_screen = Mock(return_value=None)
    current = stage_record()
    owner._navigate_goal(stage_goal('a2'), current, Mock())
    assert current['reason'] == 'stage_navigation_unverified'
    assert 'safe_target_return' not in current
    owner.device.click.assert_not_called()


@pytest.mark.parametrize('raw,capacity', [(None, None), (4, 300), (60, 60), (True, 300)])
def test_navigation_proof_never_invents_sufficient_current_keys(owner, raw, capacity):
    owner._read_stage_screen = Mock(return_value=stage_screen(raw_keys=raw, key_capacity=capacity))
    current = stage_record()
    owner._navigate_goal(stage_goal(), current, Mock())
    assert current['verified_for_navigation'] and not current['verified_for_execution']
    owner.device.click.assert_not_called()


def test_goal_request_rereads_after_restart_and_does_not_replay_reserved_input(owner, monkeypatch, tmp_path):
    import json
    owner._read_stage_screen = Mock(return_value=stage_screen())
    owner.appear.return_value = True
    owner._capture = Mock()
    first = owner.proof_for_goal(stage_goal('c1', 'hard'), folder=tmp_path, request_id='restart')
    assert not first['verified_for_navigation'] and owner.device.click.call_count == 1
    state = json.loads((tmp_path / 'alas' / 'restart' / 'goal.json').read_text())
    assert state['navigation_inputs'] == ['mode:normal:hard']
    owner.device.click.reset_mock()
    again = owner.proof_for_goal(stage_goal('c1', 'hard'), folder=tmp_path, request_id='restart')
    assert not again['verified_for_navigation']
    owner.device.click.assert_not_called()
    owner._read_stage_screen.return_value = stage_screen('hard', stage='c1')
    fresh = owner.proof_for_goal(stage_goal('c1', 'hard'), folder=tmp_path, request_id='restart')
    assert fresh['verified_for_navigation'] and fresh['verified_for_execution']
    assert not fresh['combat_attempted']
    owner.device.click.assert_not_called()


def test_prepare_sets_only_fresh_entrance_and_clears_a_cached_one_on_failure(owner):
    campaign = SimpleNamespace(ENTRANCE=Button(area=(1, 2, 3, 4), color=(), button=(1, 2, 3, 4)))
    owner.proof_for_goal = Mock(return_value=dict(verified_for_navigation=True, entrance_area=[427, 207, 442, 226]))
    owner.prepare_goal(campaign, stage_goal())
    assert campaign.ENTRANCE.button == (427, 207, 442, 226) and campaign.ENTRANCE.name == 'a1'
    owner.proof_for_goal.return_value = dict(verified_for_navigation=False)
    owner.prepare_goal(campaign, stage_goal())
    assert campaign.ENTRANCE.button == ()


@pytest.mark.parametrize('account', ['alas', 'f2p'])
def test_prepared_actual_entrance_supports_real_enter_map_color_check(owner, account):
    from module.base.base import ModuleBase
    from module.base.utils import load_image
    owner.device.image = load_image(f'tests/war_archives/data/{account}_visitors_stages.png')
    use_actual_en_page_detectors(owner)
    observed = owner._read_stage_screen(VISITORS)
    entrance = observed['stages']['a1']['entrance_area']
    owner.proof_for_goal = Mock(return_value=dict(verified_for_navigation=True, entrance_area=entrance))
    campaign = ModuleBase.__new__(ModuleBase)
    campaign.device = owner.device
    campaign.device.stuck_record_add = Mock()
    owner.prepare_goal(campaign, stage_goal())
    assert len(campaign.ENTRANCE.color) == 3
    assert campaign.appear_then_click(campaign.ENTRANCE)
    campaign.device.click.assert_called_once_with(campaign.ENTRANCE)
    campaign.device.click.reset_mock()
    left, top, right, bottom = entrance
    campaign.device.image[top:bottom, left:right] = 0
    assert not campaign.appear_then_click(campaign.ENTRANCE)
    campaign.device.click.assert_not_called()


@pytest.mark.parametrize('archive_object', [False, True])
def test_prize_observer_delegates_request_without_adding_claim_inputs(owner, monkeypatch, tmp_path,
                                                                    archive_object):
    from module.war_archives import prize_progress
    from module.war_archives.catalog import get_archive
    adapter = Mock()
    monkeypatch.setattr(prize_progress, 'ArchivePrizeProgress', Mock(return_value=adapter))
    prize = SimpleNamespace(clears=60)
    archive = get_archive(VISITORS) if archive_object else VISITORS
    owner.observe_prize(archive, prize, folder=tmp_path, request_id='prize')
    adapter.observe.assert_called_once_with(VISITORS, prize, folder=tmp_path, request_id='prize')
    owner.device.click.assert_not_called()
