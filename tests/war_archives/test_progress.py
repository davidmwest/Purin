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
                                    stuck_record_clear=Mock(), click_record_remove=Mock(),
                                    app_is_running=Mock(return_value=True))
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


@pytest.mark.parametrize('recognized', ['ex', 'sp'])
def test_slow_category_recognition_cannot_click_or_succeed_after_wall_deadline(owner, monkeypatch, recognized):
    clock = [0.0]
    monkeypatch.setattr(p, 'monotonic', lambda: clock[0])
    owner._list_page = Mock(return_value=True)
    def slow_category():
        clock[0] += 11
        return recognized
    owner._category = Mock(side_effect=slow_category)
    persist = Mock()
    assert not owner._select_category('sp', record(), persist)
    owner._category.assert_called_once()
    persist.assert_not_called()
    owner.device.click.assert_not_called()


def test_category_wall_deadline_stops_unknown_pages_before_minimum_loop_frames(owner, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(p, 'monotonic', lambda: clock[0])
    def slow_unknown_page():
        clock[0] += 11
        return False
    owner._list_page = Mock(side_effect=slow_unknown_page)
    owner._category = Mock()
    assert not owner._select_category('sp', record(), Mock())
    owner._list_page.assert_called_once()
    owner._category.assert_not_called()
    owner.device.click.assert_not_called()


def test_scroll_is_one_bounded_observed_thumb_drag_with_preinput_journal(owner):
    info = dict(top=100, bottom=200, position=.2)
    owner._list_page = Mock(return_value=True)
    owner._category = Mock(return_value='ex')
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
    owner._list_geometry = Mock(return_value=((88, 126, 148, 145),))
    owner.read_cards = Mock(return_value=[])
    assert owner._settled_list('ex') is None
    owner.device.stuck_record_clear.assert_not_called()
    owner.read_cards.return_value = [card()]
    assert owner._settled_list('sp') is None
    assert owner._settled_list('ex') == ([card()], None)
    owner.device.stuck_record_clear.assert_called_once()


@pytest.mark.parametrize('missing', ['headers', 'category', 'scroll', 'list'])
def test_unknown_card_page_requires_exact_list_category_geometry_and_scroll(owner, missing):
    owner._list_page = Mock(return_value=missing != 'list')
    owner._category = Mock(return_value=None if missing == 'category' else 'sp')
    owner._list_geometry = Mock(return_value=() if missing == 'headers' else ((88, 156, 148, 175),))
    owner._scroll_info = Mock(return_value=None if missing == 'scroll' else dict(top=454, bottom=565))
    owner.read_cards = Mock(return_value=[])
    assert owner._settled_list('sp') is None
    owner.device.stuck_record_clear.assert_not_called()
    owner.device.click_record_remove.assert_not_called()


def test_unknown_card_pages_do_not_stop_identity_search_before_later_known_page(owner):
    owner._select_category = Mock(return_value=True)
    owner._settled_list = Mock(side_effect=[
        ([], dict(top=0, bottom=100)),
        ([], dict(top=50, bottom=150)),
        ([full_card()], dict(top=100, bottom=200)),
    ])
    owner._drag_scroll = Mock(return_value=True)
    assert owner._find_archive(VISITORS, 'sp', record(), Mock(), float('inf')) == full_card()
    assert owner._drag_scroll.call_count == 2
    owner.device.click.assert_not_called()


def test_unknown_identity_bottom_can_reset_to_top_without_authorizing_an_entrance(owner):
    bottom = dict(top=454, bottom=565, position=1)
    top = dict(top=0, bottom=111, position=0)
    owner._select_category = Mock(return_value=True)
    owner._category = Mock(return_value='sp')
    owner._settled_list = Mock(side_effect=[([], bottom), ([full_card()], top)])
    owner._drag_scroll = Mock(return_value=True)
    current, persist = record(), Mock()
    assert owner._find_archive(VISITORS, 'sp', current, persist, float('inf')) == full_card()
    owner._drag_scroll.assert_called_once_with(bottom, True, current, persist,
                                               expected_category='sp', deadline=float('inf'))
    owner.device.click.assert_not_called()


def test_identity_search_finishes_shortened_top_reset_before_accepting_card(owner):
    bottom = dict(top=454, bottom=565)
    short = dict(top=16, bottom=126)
    top = dict(top=0, bottom=111)
    owner._select_category = Mock(return_value=True)
    owner._category = Mock(return_value='sp')
    owner._settled_list = Mock(side_effect=[([], bottom), ([full_card()], short), ([full_card()], top)])
    owner._drag_scroll = Mock(return_value=True)
    current, persist = record(), Mock()
    assert owner._find_archive(VISITORS, 'sp', current, persist, float('inf')) == full_card()
    assert [call.args[0] for call in owner._drag_scroll.call_args_list] == [bottom, short]
    assert all(call.args[1] is True for call in owner._drag_scroll.call_args_list)
    owner.device.click.assert_not_called()


@pytest.mark.parametrize('after', [dict(top=16, bottom=126), dict(top=20, bottom=130),
                                 dict(top=0, bottom=180), None])
def test_top_reset_rejects_stagnant_reverse_changed_thumb_or_unknown_scroll(owner, after):
    before = dict(top=16, bottom=126)
    owner._category = Mock(return_value='sp')
    owner._drag_scroll = Mock(return_value=True)
    owner._settled_list = Mock(return_value=([], after))
    result, reason = owner._reset_scroll_top('sp', ([], before), record(), Mock(), float('inf'))
    assert result is None
    assert reason == ('scroll_top_unrecognized' if after is None else 'scroll_top_not_reached')
    owner._drag_scroll.assert_called_once()


def test_top_reset_has_three_input_cap_even_while_progressing(owner):
    owner._category = Mock(return_value='sp')
    owner._drag_scroll = Mock(return_value=True)
    owner._settled_list = Mock(side_effect=[([], dict(top=top, bottom=top + 110))
                                           for top in (300, 200, 100)])
    result, reason = owner._reset_scroll_top('sp', ([], dict(top=454, bottom=564)),
                                           record(), Mock(), float('inf'))
    assert result is None and reason == 'scroll_top_not_reached'
    assert owner._drag_scroll.call_count == 3


@pytest.mark.parametrize('category', [None, 'ex'])
def test_top_reset_never_drags_different_or_unknown_category(owner, category):
    owner._category = Mock(return_value=category)
    owner._drag_scroll = Mock(return_value=True)
    result, reason = owner._reset_scroll_top('sp', ([], dict(top=16, bottom=126)),
                                           record(), Mock(), float('inf'))
    assert result is None and reason == 'scroll_top_unverified'
    owner._drag_scroll.assert_not_called()


def test_top_reset_preserves_shared_deadline_and_stops_before_second_input(owner, monkeypatch):
    clock = [170.0]
    monkeypatch.setattr(p, 'monotonic', lambda: clock[0])
    owner._category = Mock(return_value='sp')
    owner._drag_scroll = Mock(return_value=True)
    def settled(category, timeout):
        assert category == 'sp' and timeout == 8
        clock[0] = 181
        return [], dict(top=16, bottom=126)
    owner._settled_list = Mock(side_effect=settled)
    result, reason = owner._reset_scroll_top('sp', ([], dict(top=454, bottom=565)),
                                           record(), Mock(), 180)
    assert result is None and reason == 'observation_deadline'
    owner._drag_scroll.assert_called_once()
    assert owner._archive_scroll_pending is None


def test_top_reset_bounds_settling_by_remaining_shared_deadline(owner, monkeypatch):
    monkeypatch.setattr(p, 'monotonic', lambda: 178)
    owner._category = Mock(return_value='sp')
    owner._drag_scroll = Mock(return_value=True)
    owner._settled_list = Mock(return_value=([], dict(top=0, bottom=110)))
    result, reason = owner._reset_scroll_top('sp', ([], dict(top=16, bottom=126)),
                                           record(), Mock(), 180)
    assert result == ([], dict(top=0, bottom=110)) and reason is None
    owner._settled_list.assert_called_once_with('sp', timeout=2)


def test_top_reset_fresh_progress_retries_preserve_pending_swipe_proof_and_journal(owner):
    state = dict(top=454, bottom=565)
    owner._list_page = Mock(return_value=True)
    owner._category = Mock(return_value='sp')
    owner._list_geometry = Mock(return_value=((88, 156, 148, 175),))
    owner._scroll_info = lambda: dict(state)
    owner.read_cards = Mock(return_value=[])
    current, snapshots = record(), []
    def swipe(*args, **kwargs):
        snapshots.append(('swiped', current['scroll_drags']))
        state.update(top=16 if current['scroll_drags'] == 1 else 0,
                     bottom=126 if current['scroll_drags'] == 1 else 110)
    owner.device.swipe.side_effect = swipe
    result, reason = owner._reset_scroll_top('sp', ([], dict(state)), current,
                                            lambda: snapshots.append(current['scroll_drags']), float('inf'))
    assert result == ([], dict(top=0, bottom=110)) and reason is None
    assert snapshots == [1, ('swiped', 1), 2, ('swiped', 2)]
    assert owner.device.click_record_remove.call_count == 2
    assert all(call.args == ('ARCHIVE_OBSERVE_SCROLL',)
               for call in owner.device.click_record_remove.call_args_list)
    assert owner._archive_scroll_pending is None
    owner.device.click.assert_not_called()


def test_top_reset_rechecks_category_and_deadline_immediately_before_input(owner, monkeypatch):
    clock = [170]
    monkeypatch.setattr(p, 'monotonic', lambda: clock[0])
    before = dict(top=16, bottom=126)
    owner._list_page = Mock(return_value=True)
    owner._scroll_info = Mock(return_value=before)
    owner._category = Mock(side_effect=['sp', 'ex'])
    current, persist = record(), Mock()
    result, reason = owner._reset_scroll_top('sp', ([], before), current, persist, 180)
    assert result is None and reason == 'scroll_top_unverified'
    owner.device.swipe.assert_not_called()
    persist.assert_not_called()
    def slow_category():
        clock[0] = 181
        return 'sp'
    clock[0] = 170
    # Recognition inside _drag_scroll may itself consume the remaining time.
    owner._category = Mock(side_effect=lambda: slow_category() if owner._category.call_count == 2 else 'sp')
    result, reason = owner._reset_scroll_top('sp', ([], before), current, persist, 180)
    assert result is None and reason == 'observation_deadline'
    owner.device.swipe.assert_not_called()
    persist.assert_not_called()


def test_progressive_census_exceeding_twelve_swipes_removes_only_own_verified_history(owner,
                                                                                     monkeypatch, tmp_path):
    state = dict(top=0)
    history = ['UNRELATED_BUTTON']
    owner._select_category = Mock(return_value=True)
    owner._list_page = Mock(return_value=True)
    owner._category = Mock(return_value='ex')
    owner._list_geometry = Mock(return_value=((88, 126, 148, 145),))
    owner._scroll_info = lambda: dict(top=state['top'], bottom=state['top'] + 70)
    owner.read_cards = lambda: [card(f"archive-{state['top']}")]
    owner._capture = Mock(return_value='list.png')
    monkeypatch.setattr(p.ARCHIVE_KEYS, 'ocr', Mock(return_value='80/300'))
    def swipe(start, end, **kwargs):
        history.append(kwargs['name'])
        assert history.count('ARCHIVE_OBSERVE_SCROLL') < 12
        state['top'] += end[1] - start[1]
    def remove(name):
        history[:] = [entry for entry in history if entry != name]
    owner.device.swipe.side_effect = swipe
    owner.device.click_record_remove.side_effect = remove
    current = record()
    assert owner._observe_category('ex', tmp_path, current, Mock(), 20, float('inf')) == 'bottom_observed'
    assert owner.device.swipe.call_count >= 12
    assert owner.device.click_record_remove.call_count == owner.device.swipe.call_count
    assert history == ['UNRELATED_BUTTON']


@pytest.mark.parametrize('after,category', [
    (dict(top=0, bottom=100), 'ex'),  # Stagnant.
    (dict(top=50, bottom=150), 'sp'),  # Different category.
    (dict(top=50, bottom=200), 'ex'),  # Different scrollbar/page geometry.
    (dict(top=-50, bottom=50), 'ex'),  # Wrong direction.
])
def test_unproved_scroll_never_erases_click_history(owner, after, category):
    owner._archive_scroll_pending = ('ex', 0, 100, False)
    owner._list_page = Mock(return_value=True)
    owner._category = Mock(return_value=category)
    owner._list_geometry = Mock(return_value=((88, 126, 148, 145),))
    owner._scroll_info = Mock(return_value=after)
    owner.read_cards = Mock(return_value=[card()])
    assert owner._settled_list(category) == ([card()], after)
    owner.device.click_record_remove.assert_not_called()
    assert owner._archive_scroll_pending is None


def test_stagnant_scroll_stops_census_without_erasing_history(owner, monkeypatch, tmp_path):
    info = dict(top=0, bottom=100)
    owner._select_category = Mock(return_value=True)
    owner._list_page = Mock(return_value=True)
    owner._category = Mock(return_value='ex')
    owner._list_geometry = Mock(return_value=((88, 126, 148, 145),))
    owner._scroll_info = Mock(return_value=info)
    owner.read_cards = Mock(return_value=[card()])
    owner._capture = Mock(return_value='list.png')
    monkeypatch.setattr(p.ARCHIVE_KEYS, 'ocr', Mock(return_value='80/300'))
    assert owner._observe_category('ex', tmp_path, record(), Mock(), 20, float('inf')) == 'repeated_page'
    owner.device.swipe.assert_called_once()
    owner.device.click_record_remove.assert_not_called()


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
    assert owner._drag_scroll.call_count == 2
    assert owner._settled_list.call_count == 3


def test_settling_reads_identities_once_on_the_final_fresh_frame(owner, monkeypatch):
    class ThreeReadsTimer(ReadyTimer):
        def reset(self):
            self.reads = 0
            return self

        def reached(self):
            self.reads += 1
            return self.reads == 3

    monkeypatch.setattr(p, 'Timer', ThreeReadsTimer)
    owner._list_page = Mock(return_value=True)
    owner._category = Mock(return_value='ex')
    owner._list_geometry = Mock(return_value=((88, 126, 148, 145),))
    owner._scroll_info = Mock(return_value=None)
    def frames(**kwargs):
        for frame in range(5):
            owner.device.image[0, 0, 0] = frame
            yield
    owner.loop = frames
    owner.read_cards = Mock(side_effect=lambda: [dict(card(), frame=int(owner.device.image[0, 0, 0]))])
    cards, scroll = owner._settled_list('ex')
    assert cards[0]['frame'] == 3 and scroll is None
    assert owner._list_geometry.call_count == 4
    owner.read_cards.assert_called_once()


def test_header_or_scroll_motion_resets_settling_before_identity_read(owner):
    owner._list_page = Mock(return_value=True)
    owner._category = Mock(return_value='ex')
    first, moved = ((88, 126, 148, 145),), ((88, 120, 148, 139),)
    owner._list_geometry = Mock(side_effect=[first, moved, moved, moved])
    owner._scroll_info = Mock(side_effect=[None, None, dict(top=1, bottom=82), dict(top=1, bottom=82)])
    owner.read_cards = Mock(return_value=[card()])
    assert owner._settled_list('ex') == ([card()], dict(top=1, bottom=82))
    assert owner._list_geometry.call_count == 4
    owner.read_cards.assert_called_once()


def test_missing_full_card_headers_does_not_scan_identities_or_refresh_watchdog(owner):
    owner._list_page = Mock(return_value=True)
    owner._category = Mock(return_value='ex')
    owner._list_geometry = Mock(return_value=())
    owner._scroll_info = Mock(return_value=None)
    owner.read_cards = Mock()
    assert owner._settled_list('ex') is None
    owner.read_cards.assert_not_called()
    owner.device.stuck_record_clear.assert_not_called()


def test_list_settle_has_a_wall_clock_deadline_even_when_loop_timer_has_frames_left(owner, monkeypatch):
    owner._list_page = Mock(return_value=True)
    owner._category = Mock(return_value='ex')
    owner._list_geometry = Mock(return_value=((88, 126, 148, 145),))
    owner._scroll_info = Mock(return_value=None)
    owner.read_cards = Mock(return_value=[card()])
    monkeypatch.setattr(p, 'monotonic', Mock(side_effect=[0, 0, 9]))
    assert owner._settled_list('ex', timeout=8) is None
    owner.read_cards.assert_not_called()


def test_census_reaches_actual_bottom_instead_of_stopping_five_percent_early(owner, monkeypatch, tmp_path):
    owner._select_category = Mock(return_value=True)
    owner._settled_list = Mock(side_effect=[
        ([card('first')], dict(top=0, bottom=100, position=0)),
        ([card('near_bottom')], dict(top=450, bottom=550, position=.968)),
        ([card('last')], dict(top=465, bottom=565, position=1)),
    ])
    owner._drag_scroll = Mock(return_value=True)
    owner._capture = Mock(return_value='list.png')
    monkeypatch.setattr(p.ARCHIVE_KEYS, 'ocr', Mock(return_value='80/300'))
    current = record()
    assert owner._observe_category('ex', tmp_path, current, Mock(), 20, float('inf')) == 'bottom_observed'
    assert current['pages'][-1]['cards'][0]['archive'] == 'last'
    assert owner._drag_scroll.call_count == 2


def test_census_first_returns_to_actual_top_when_within_five_percent(owner, monkeypatch, tmp_path):
    owner._select_category = Mock(return_value=True)
    owner._category = Mock(return_value='ex')
    almost_top = dict(top=20, bottom=120, position=.043)
    owner._settled_list = Mock(side_effect=[([card()], almost_top),
                                          ([card('first')], dict(top=0, bottom=100, position=0))])
    owner._drag_scroll = Mock(return_value=True)
    owner._capture = Mock(return_value='list.png')
    monkeypatch.setattr(p.ARCHIVE_KEYS, 'ocr', Mock(return_value='80/300'))
    current, persist = record(), Mock()
    assert owner._observe_category('ex', tmp_path, current, persist, 1, float('inf')) == 'page_limit'
    owner._drag_scroll.assert_called_once_with(almost_top, True, current, persist,
                                               expected_category='ex', deadline=float('inf'))
    assert current['pages'][0]['cards'][0]['archive'] == 'first'


def test_census_finishes_shortened_top_reset_before_capturing_first_page(owner, monkeypatch, tmp_path):
    owner._select_category = Mock(return_value=True)
    owner._category = Mock(return_value='sp')
    owner._settled_list = Mock(side_effect=[
        ([], dict(top=454, bottom=565)), ([card('short')], dict(top=16, bottom=126)),
        ([card('first')], dict(top=0, bottom=110)),
    ])
    owner._drag_scroll = Mock(return_value=True)
    owner._capture = Mock(return_value='top.png')
    monkeypatch.setattr(p.ARCHIVE_KEYS, 'ocr', Mock(return_value='80/300'))
    current = record()
    assert owner._observe_category('sp', tmp_path, current, Mock(), 1, float('inf')) == 'page_limit'
    assert owner._drag_scroll.call_count == 2
    assert current['pages'][0]['cards'][0]['archive'] == 'first'
    assert current['pages'][0]['scroll']['top'] == 0


def test_full_catalog_half_steps_cover_every_whole_card_within_shared_deadline(owner, monkeypatch, tmp_path):
    # A physical scrollbar model with the captured card height/296px row pitch.
    # Identity/OCR and settling cost a conservative 4.5s per final fresh page.
    clock = [0.0]
    state = {}
    monkeypatch.setattr(p, 'monotonic', lambda: clock[0])
    owner._list_page = Mock(return_value=True)
    def select(category, *args):
        rows = 15 if category == 'ex' else 11  # 30 EX + 22 SP catalog cards.
        extent = 126 + (rows - 1) * 296 + 249 - 675
        state.update(category=category, rows=rows, extent=extent,
                     thumb=round(565 * 565 / (565 + extent)), top=0)
        return True
    owner._select_category = select
    owner._category = lambda: state['category']
    def info():
        return dict(top=state['top'], bottom=state['top'] + state['thumb'],
                    position=state['top'] / (565 - state['thumb']))
    owner._scroll_info = info
    def settled(category):
        clock[0] += 4.5
        offset = info()['position'] * state['extent']
        cards = []
        for row in range(state['rows']):
            top = round(126 + row * 296 - offset)
            if 110 <= top and top + 249 <= 675:
                for column in range(2):
                    cards.append(dict(archive=f'{category}-{row}-{column}',
                                      identity_area=[88 + column * 634, top + 20, 200 + column * 634, top + 40]))
        assert cards  # Each fresh page has at least one whole row to identify.
        return cards, info()
    owner._settled_list = settled
    def swipe(start, end, **kwargs):
        state['top'] += end[1] - start[1]
    owner.device.swipe.side_effect = swipe
    owner._capture = Mock(return_value='page.png')
    monkeypatch.setattr(p.ARCHIVE_KEYS, 'ocr', Mock(return_value='80/300'))
    current = record()
    for category in ('ex', 'sp'):
        assert owner._observe_category(category, tmp_path, current, Mock(), 20, 180) == 'bottom_observed'
    observed = {card['archive'] for page in current['pages'] for card in page['cards']}
    assert len(observed) == 52
    assert clock[0] < 180
    assert current['scroll_drags'] == len(current['pages']) - 2


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


def use_en_buttons(owner):
    clones = {}
    def en_button(original):
        if id(original) not in clones:
            def en(value):
                return value['en'] if isinstance(value, dict) else value
            clones[id(original)] = Button(area=en(original.raw_area), color=en(original.raw_color),
                                          button=en(original.raw_button), file=en(original.raw_file),
                                          name=original.name)
        return clones[id(original)]
    owner.appear = lambda button, **kwargs: en_button(button).match(owner.device.image, **kwargs)
    owner.match_template_color = lambda button, **kwargs: en_button(button).match_template_color(
        owner.device.image, **kwargs)


def moonlit_list(owner, monkeypatch):
    from module.base.utils import load_image
    owner.device.image = load_image('tests/war_archives/data/alas_archive_moonlit.png')
    use_en_identities(monkeypatch)
    use_en_buttons(owner)


def test_actual_moonlit_card_uses_current_existing_portrait_and_reads_completion(owner, monkeypatch):
    moonlit_list(owner, monkeypatch)
    archive = 'war_archives_20190314_en'
    old_similarity, _ = p.dic_archives_template[archive].match_result(owner.device.image)
    assert old_similarity < .85  # The formerly selected Prelude crop caused the live miss.
    assert owner._list_page() and owner._category() == 'sp'
    cards = {item['archive']: item for item in owner.read_cards()}
    moonlit = cards[archive]
    assert moonlit['identity_similarity'] > .97
    assert moonlit['identity_area'] == [418, 474, 502, 506]
    assert moonlit['card_area'] == [88, 422, 558, 651]
    assert moonlit['completion'] == 100 and moonlit['completion_verified']
    owner.device.click.assert_not_called()


@pytest.mark.parametrize('missing', ['identity', 'header', 'completion'])
def test_actual_moonlit_missing_witness_cannot_authorize_card_entry(owner, monkeypatch, missing):
    moonlit_list(owner, monkeypatch)
    area = {'identity': (418, 474, 502, 506), 'header': (88, 402, 148, 421),
            'completion': (168, 606, 231, 636)}[missing]
    left, top, right, bottom = area
    owner.device.image[top:bottom, left:right] = 0
    matches = [item for item in owner.read_cards()
               if item['archive'] == 'war_archives_20190314_en'
               and item['card_area'] is not None and item['completion'] is not None]
    assert matches == []
    owner.device.click.assert_not_called()


def test_actual_moonlit_search_finds_fresh_card_after_one_progressive_scroll(owner, monkeypatch):
    moonlit_list(owner, monkeypatch)
    cards, scroll = owner._settled_list('sp')
    owner._select_category = Mock(return_value=True)
    owner._settled_list = Mock(side_effect=[([], dict(top=0, bottom=111)), (cards, scroll)])
    owner._drag_scroll = Mock(return_value=True)
    found = owner._find_archive('war_archives_20190314_en', 'sp', record(), Mock(), float('inf'))
    assert found['identity_area'] == [418, 474, 502, 506]
    assert found['completion'] == 100 and found['completion_verified']
    owner._drag_scroll.assert_called_once()
    owner.device.click.assert_not_called()


def test_actual_sp_short_reset_still_requires_another_drag_to_physical_top(owner):
    from module.base.utils import load_image
    owner.device.image = load_image('tests/war_archives/data/f2p_archive_sp_short_reset.png')
    use_en_buttons(owner)
    assert owner._list_page() and owner._category() == 'sp'
    assert owner._list_geometry()
    info = owner._scroll_info()
    assert info == dict(top=16, bottom=126, position=.035)
    assert not owner._scroll_at_top(info)
    current = record()
    assert owner._drag_scroll(info, True, current, Mock(), expected_category='sp')
    assert current['scroll_drags'] == 1
    owner.device.click.assert_not_called()


@pytest.mark.parametrize('known_identities', [False, True])
def test_actual_sp_bottom_clipped_cards_remain_scrollable_without_entry_proof(owner, monkeypatch,
                                                                            known_identities):
    from module.base.utils import load_image
    owner.device.image = load_image('tests/war_archives/data/f2p_archive_sp_bottom.png')
    use_en_identities(monkeypatch)
    if not known_identities:
        monkeypatch.setattr(p, 'dic_archives_template', {
            archive: template for archive, template in p.dic_archives_template.items()
            if archive not in ('war_archives_20241024_cn', 'war_archives_20251023_cn')})
    use_en_buttons(owner)
    assert owner._list_page() and owner._category() == 'sp'
    assert owner._list_geometry() == ((88, 156, 148, 175), (722, 156, 782, 175))
    cards, scroll = owner._settled_list('sp')
    if known_identities:
        assert {card['archive'] for card in cards} == {'war_archives_20241024_cn',
                                                      'war_archives_20251023_cn'}
        assert all(card['card_area'] is None and card['completion'] is None
                   and not card['completion_verified'] for card in cards)
    else:
        assert cards == []
    assert scroll == dict(top=454, bottom=565, position=1.0)
    assert owner.device.stuck_record_clear.call_count == int(known_identities)
    owner.device.click_record_remove.assert_not_called()
    current, persist = record(), Mock()
    assert owner._drag_scroll(scroll, True, current, persist)
    assert current['scroll_drags'] == 1
    owner.device.click.assert_not_called()


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


@pytest.mark.parametrize('archive', sorted(p.BALL_PAGE_ARCHIVES))
def test_ball_control_reuses_original_archive_geometry(owner, archive):
    from importlib import import_module
    original = import_module(f'campaign.{archive}.campaign_base').BALL
    assert owner._archive_ball_control(archive) is original
    assert owner._archive_ball_control(VISITORS) is None


@pytest.mark.parametrize('archive', sorted(p.BALL_PAGE_ARCHIVES))
@pytest.mark.parametrize('state', ['blue', 'red'])
def test_original_ball_color_requires_known_undimmed_area(owner, archive, state):
    # Source-documented color placed in its original observation rectangle;
    # this is a control regression, not a fabricated live archive fixture.
    left, top, right, bottom = owner._archive_ball_control(archive).area
    owner.device.image[top:bottom, left:right] = p.BALL_PAGE_COLORS[state]
    assert owner._archive_ball_state(archive) == state
    owner.device.image[top:bottom, left:right] //= 2
    assert owner._archive_ball_state(archive) is None
    owner.device.image[top:bottom, left:right] = (30, 220, 40)
    assert owner._archive_ball_state(archive) is None
    owner.device.image[:] = 0
    assert owner._archive_ball_state(archive) is None
    owner.device.click.assert_not_called()


@pytest.mark.parametrize('archive', sorted(p.BALL_PAGE_ARCHIVES))
def test_ball_routes_match_all_original_selection_rules(owner, archive):
    from module.war_archives.catalog import get_archive
    definition = get_archive(archive)
    for stage in definition.normal_stages + definition.hard_stages:
        prefix = stage.rstrip('0123456789')
        number = int(stage[len(prefix):])
        if archive == 'war_archives_20200917_cn':
            expected = 'blue' if number in (1, 6) else 'red'
        else:
            expected = 'blue' if number <= (1 if prefix in ('ts', 'hts') else 3) else 'red'
        assert owner._archive_goal_ball(archive, stage) == expected
        assert owner._archive_stage_page_index(archive, stage) == 1
    assert owner._archive_goal_ball(archive, 'sp1') is None
    assert owner._archive_goal_ball(VISITORS, 't1') is None


@pytest.mark.parametrize('archive,first_action', [
    ('war_archives_20200917_cn', 'ball'), ('war_archives_20230525_cn', 'mode')])
def test_ball_navigation_preserves_original_order_and_reserves_before_each_input(owner, archive, first_action):
    from module.campaign.campaign_stars import StageGoal
    target = 'ht2' if first_action == 'ball' else 'ht4'
    start = dict(stage_screen(stage='t1'), event=archive, ball='blue')
    if first_action == 'ball':
        middle = dict(stage_screen(stage='t2'), event=archive, ball='red')
    else:
        middle = dict(stage_screen(mode='hard', stage='ht1'), event=archive, ball='blue')
    final = dict(stage_screen(mode='hard', stage=target), event=archive, ball='red')
    owner.loop = lambda **kwargs: iter(range(8))
    owner._read_stage_screen = Mock(side_effect=[start, start, middle, middle, final, final])
    owner.appear.return_value = True
    current, events = stage_record(), []
    owner.device.click.side_effect = lambda button: events.append(('click', button, list(current['navigation_inputs'])))
    owner._navigate_goal(StageGoal(archive, target, 'hard', 'archive_clear'), current,
                         lambda: events.append(('save', list(current['navigation_inputs']))))
    ball = owner._archive_ball_control(archive)
    expected = [ball, p.SWITCH_1_HARD] if first_action == 'ball' else [p.SWITCH_1_HARD, ball]
    assert [item[1] for item in events if item[0] == 'click'] == expected
    assert [item[0] for item in events] == ['save', 'click', 'save', 'click']
    assert events[0][1] == events[1][2] and events[2][1] == events[3][2]
    assert current['verified_for_navigation'] and current['verified_for_execution']


def test_ball_transition_waits_for_three_seconds_and_fresh_matching_page(owner, monkeypatch):
    from module.campaign.campaign_stars import StageGoal
    timers = []
    class TrackingTimer(ReadyTimer):
        def __init__(self, limit, count):
            timers.append((limit, count))
    monkeypatch.setattr(p, 'Timer', TrackingTimer)
    archive = 'war_archives_20200917_cn'
    first = dict(stage_screen(stage='t1'), event=archive, ball='blue')
    last = dict(stage_screen(stage='t2'), event=archive, ball='red')
    owner._read_stage_screen = Mock(side_effect=[first, first, None, last, last])
    current = stage_record()
    owner._navigate_goal(StageGoal(archive, 't2', 'normal', 'archive_clear'), current, Mock())
    assert timers == [(1, 2), (3, 2)]
    assert current['verified_for_navigation']
    owner.device.click.assert_called_once_with(owner._archive_ball_control(archive))


def test_stale_or_reserved_ball_transition_never_replays_and_fresh_target_can_resume(owner):
    from module.campaign.campaign_stars import StageGoal
    archive = 'war_archives_20200917_cn'
    goal = StageGoal(archive, 't2', 'normal', 'archive_clear')
    owner._read_stage_screen = Mock(return_value=dict(stage_screen(stage='t1'), event=archive, ball='blue'))
    current = stage_record()
    owner._navigate_goal(goal, current, Mock())
    assert current['navigation_inputs'] == ['ball:normal:blue:red']
    assert not current['verified_for_navigation']
    owner.device.click.assert_called_once()
    owner.device.click.reset_mock()
    owner._navigate_goal(goal, current, Mock())
    owner.device.click.assert_not_called()
    owner._read_stage_screen.return_value = dict(stage_screen(stage='t2'), event=archive, ball='red')
    owner._navigate_goal(goal, current, Mock())
    assert current['verified_for_navigation']
    owner.device.click.assert_not_called()


def test_unknown_ball_does_not_authorize_color_toggle_or_stage_proof(owner):
    from module.campaign.campaign_stars import StageGoal
    archive = 'war_archives_20200917_cn'
    owner._read_stage_screen = Mock(return_value=dict(stage_screen(stage='t2'), event=archive, ball=None))
    current = stage_record()
    owner._navigate_goal(StageGoal(archive, 't2', 'normal', 'archive_clear'), current, Mock())
    assert not current['verified_for_navigation'] and 'safe_target_return' not in current
    owner.device.click.assert_not_called()


@pytest.mark.parametrize('names,archive,valid', [
    (['T1', 'HT1'], p.INTERLUDE_ARCHIVE, True),
    (['HT2'], p.INTERLUDE_ARCHIVE, True),
    (['T1', 'HT1'], 'war_archives_20230525_cn', False),
    (['T1', 'HT4'], p.INTERLUDE_ARCHIVE, False),
    (['T1', 'T1'], p.INTERLUDE_ARCHIVE, False),
])
def test_interlude_reader_allows_only_its_known_shared_normal_hard_nodes(owner, monkeypatch, names, archive, valid):
    owner._archive_identity = Mock(return_value=True)
    buttons = [Button(area=(200 + i * 100, 200, 220 + i * 100, 220), color=(),
                      button=(200 + i * 100, 200, 220 + i * 100, 220)) for i in range(len(names))]
    owner.campaign_match_multi = Mock(side_effect=[buttons, []])
    monkeypatch.setattr(p, 'Ocr', lambda *args, **kwargs: SimpleNamespace(ocr=lambda image: names))
    monkeypatch.setattr(p.STAGE_KEYS, 'ocr', Mock(return_value='300/300'))
    observed = owner._read_stage_screen(archive)
    if valid:
        assert observed['mode'] == 'combined' and observed['page'] == 1
        assert set(observed['stages']) == {name.lower() for name in names}
    else:
        assert observed is None
    owner.device.click.assert_not_called()


@pytest.mark.parametrize('mode,target', [('normal', 't1'), ('hard', 'ht1')])
def test_interlude_proves_target_mode_from_known_node_without_switch_controls(owner, mode, target):
    from module.campaign.campaign_stars import StageGoal
    screen = dict(stage_screen(mode='combined', stage='t1'), event=p.INTERLUDE_ARCHIVE)
    screen['stages']['ht1'] = dict(entrance_area=[600, 200, 620, 220], complete=False)
    owner._read_stage_screen = Mock(return_value=screen)
    current = stage_record()
    owner._navigate_goal(StageGoal(p.INTERLUDE_ARCHIVE, target, mode, 'archive_clear'), current, Mock())
    assert current['verified_for_navigation'] and current['verified_for_execution']
    assert current['entrance_area'] == screen['stages'][target]['entrance_area']
    owner.device.click.assert_not_called()
    owner.appear.assert_not_called()


def test_interlude_missing_hard_node_is_safely_locked_without_inventing_mode_control(owner):
    from module.campaign.campaign_stars import StageGoal
    owner._read_stage_screen = Mock(return_value=dict(stage_screen(mode='combined', stage='t1'),
                                                    event=p.INTERLUDE_ARCHIVE))
    current = stage_record()
    owner._navigate_goal(StageGoal(p.INTERLUDE_ARCHIVE, 'ht1', 'hard', 'archive_clear'), current, Mock())
    assert current['reason'] == 'target_locked_or_unreadable'
    assert current['safe_target_return']['mode'] == 'hard'
    owner.device.click.assert_not_called()


@pytest.mark.parametrize('phase', ['unknown_read', 'slow_read', 'before_input'])
def test_stage_navigation_obeys_wall_deadline_even_with_unbounded_legacy_loop(owner, monkeypatch, phase):
    owner.loop = lambda **kwargs: iter(range(100))
    ticks = {'unknown_read': [0, 1, 46], 'slow_read': [0, 1, 46],
             'before_input': [0, 1, 2, 3, 4, 46]}[phase]
    monkeypatch.setattr(p, 'monotonic', Mock(side_effect=ticks))
    owner._read_stage_screen = Mock(return_value=None if phase == 'unknown_read' else stage_screen())
    owner.appear.return_value = True
    current = stage_record()
    owner._navigate_goal(stage_goal('c1', 'hard'), current, Mock())
    assert current['reason'] == 'stage_navigation_unverified'
    assert not current['verified_for_navigation'] and not current['navigation_inputs']
    owner.device.click.assert_not_called()


TEMPESTA_ARCHIVES = ('war_archives_20231026_cn', 'war_archives_20241024_cn',
                     'war_archives_20251023_cn')


@pytest.mark.parametrize('archive', TEMPESTA_ARCHIVES)
@pytest.mark.parametrize('stage,page', [('t1', 1), ('t2', 1), ('t3', 1),
                                       ('t4', 2), ('t5', 2), ('t6', 2)])
def test_tempesta_stage_pages_keep_t_prefix_across_two_pages(owner, archive, stage, page):
    assert owner._archive_stage_page_index(archive, stage) == page


@pytest.mark.parametrize('archive,stage,page', [
    (VISITORS, 'a1', 1), (VISITORS, 'b1', 2), (VISITORS, 'c1', 1), (VISITORS, 'd1', 2),
    ('war_archives_20230525_cn', 't4', 1), ('war_archives_20230525_cn', 'ht4', 1),
    ('war_archives_20240725_cn', 't3', 1), ('war_archives_20240725_cn', 'ht3', 1),
    ('war_archives_20231026_cn', 'ht4', 1), ('event_20241024_cn', 't4', 1),
])
def test_archive_page_override_preserves_other_stage_mappings(owner, archive, stage, page):
    assert owner._archive_stage_page_index(archive, stage) == page


@pytest.mark.parametrize('archive', TEMPESTA_ARCHIVES)
@pytest.mark.parametrize('names,page', [(['T1', 'T3'], 1), (['T4', 'T6'], 2), (['T3', 'T4'], None)])
def test_tempesta_stage_reader_uses_archive_page_and_rejects_mixed_pages(owner, monkeypatch, archive, names, page):
    from module.war_archives import catalog
    definition = SimpleNamespace(normal_stages=('t1', 't2', 't3', 't4', 't5', 't6'), hard_stages=())
    definition.stages = lambda mode: definition.normal_stages if mode == 'normal' else ()
    monkeypatch.setattr(catalog, 'get_archive', lambda identity: definition if identity == archive else None)
    owner._archive_identity = Mock(return_value=True)
    buttons = [Button(area=(200 + i * 100, 200, 220 + i * 100, 220), color=(),
                      button=(200 + i * 100, 200, 220 + i * 100, 220)) for i in range(len(names))]
    owner.campaign_match_multi = Mock(side_effect=[buttons, []])
    monkeypatch.setattr(p, 'Ocr', lambda *args, **kwargs: SimpleNamespace(ocr=lambda image: names))
    monkeypatch.setattr(p.STAGE_KEYS, 'ocr', Mock(return_value='300/300'))
    result = owner._read_stage_screen(archive)
    if page is None:
        assert result is None
    else:
        assert result['page'] == page and result['event'] == archive
        assert result['mode'] == 'normal' and set(result['stages']) == {name.lower() for name in names}
    owner.device.click.assert_not_called()


@pytest.mark.parametrize('archive', TEMPESTA_ARCHIVES)
@pytest.mark.parametrize('origin,target,first_page,target_page,action', [
    ('t3', 't4', 1, 2, p.ARCHIVE_NEXT), ('t6', 't1', 2, 1, p.CHAPTER_PREV),
])
def test_tempesta_navigation_moves_once_between_pages_then_requires_fresh_target(
        owner, archive, origin, target, first_page, target_page, action):
    from module.campaign.campaign_stars import StageGoal
    first = dict(stage_screen(stage=origin, page=first_page), event=archive)
    second = dict(stage_screen(stage=target, page=target_page), event=archive)
    owner._read_stage_screen = Mock(side_effect=[first, first, second, second])
    owner.appear.return_value = True
    current, saved = stage_record(), []
    owner.device.click.side_effect = lambda button: saved.append(('click', list(current['navigation_inputs'])))
    owner._navigate_goal(StageGoal(archive, target, 'normal', 'archive_clear'), current,
                         lambda: saved.append(('save', list(current['navigation_inputs']))))
    transition = f'page:normal:{first_page}:{target_page}'
    assert saved == [('save', [transition]), ('click', [transition])]
    owner.device.click.assert_called_once_with(action)
    assert current['verified_for_navigation'] and current['verified_for_execution']
    assert current['navigation_inputs'] == [transition]


@pytest.mark.parametrize('archive', TEMPESTA_ARCHIVES)
def test_tempesta_missing_target_on_current_second_page_does_not_click_next(owner, archive):
    from module.campaign.campaign_stars import StageGoal
    owner._read_stage_screen = Mock(return_value=dict(stage_screen(stage='t4', page=2), event=archive))
    owner.appear.return_value = True
    current = stage_record()
    owner._navigate_goal(StageGoal(archive, 't5', 'normal', 'archive_clear'), current, Mock())
    assert current['reason'] == 'target_locked_or_unreadable'
    assert current['safe_target_return']['page'] == 2
    assert not current['verified_for_navigation']
    owner.device.click.assert_not_called()


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


def test_actual_enigma_title_bar_excludes_clouds_and_reads_the_complete_title(owner):
    from module.base.utils import load_image
    owner.device.image = load_image('tests/war_archives/data/alas_enigma_stages.png')
    use_actual_en_page_detectors(owner)
    assert p.stage_title_area(owner.device.image) == (210, 70, 494, 100)
    result = owner._read_stage_screen('war_archives_20200806_cn')
    assert result['event'] == 'war_archives_20200806_cn'
    assert result['mode'] == 'normal' and result['page'] == 1
    assert set(result['stages']) == {'sp1'}
    assert result['stages']['sp1']['complete'] is False
    assert result['raw_keys'] == 300 and result['key_capacity'] == 300
    owner.device.click.assert_not_called()


@pytest.mark.parametrize('negative', ['wrong_archive', 'dimmed', 'occluded_title',
                                      'missing_edge', 'different_band_edges'])
def test_actual_enigma_title_requires_full_title_and_undimmed_bar(owner, negative):
    from module.base.utils import load_image
    owner.device.image = load_image('tests/war_archives/data/alas_enigma_stages.png')
    use_actual_en_page_detectors(owner)
    archive = 'war_archives_20200806_cn'
    if negative == 'wrong_archive':
        archive = 'war_archives_20190314_en'
    elif negative == 'dimmed':
        owner.device.image = (owner.device.image * .5).astype(np.uint8)
    elif negative == 'occluded_title':
        owner.device.image[77:97, 375:485] = 0
    elif negative == 'missing_edge':
        owner.device.image[70:104, 494:1100] = 80
    elif negative == 'different_band_edges':
        owner.device.image[98:104, 494:550] = 80
    assert not owner._archive_identity(archive)
    owner.device.click.assert_not_called()


def test_title_crop_does_not_discard_extra_ocr_letters(owner, monkeypatch):
    from module.base.utils import load_image
    owner.device.image = load_image('tests/war_archives/data/alas_enigma_stages.png')
    use_actual_en_page_detectors(owner)
    monkeypatch.setattr(p.STAGE_TITLE, 'ocr', Mock(return_value='The Enigma and the Shark extra'))
    assert not owner._archive_identity('war_archives_20200806_cn')
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
