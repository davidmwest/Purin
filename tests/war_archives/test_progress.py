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
                                     p.BATTLE_PREPARATION, p.GET_ITEMS_1, p.GET_SHIP])
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
