from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest
from PIL import Image

from module.base.button import Button
from module.campaign import event_sp_observation as sp
from module.campaign.event_completion_assets import TEMPESTA_CAMPAIGN
from module.campaign.event_sp_observation import (
    EventSPObservation, load_sp_observations, save_sp_observations)
from module.config import server
from module.exception import GameNotRunningError, ScriptError
from module.handler.assets import IN_MAP
from module.map.assets import FLEET_PREPARATION, MAP_PREPARATION, MAP_PREPARATION_CANCEL
from module.combat.assets import BATTLE_PREPARATION
from module.ui.assets import GOTO_MAIN
from module.ui.page import page_main, page_sp

EVENT = 'event_20261022_cn'
KEY = f'{EVENT}:v1'


class ReadyTimer:
    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        return self

    def reset(self):
        return self

    def reached(self):
        return True


@pytest.fixture(autouse=True)
def fast_timers(monkeypatch):
    monkeypatch.setattr(sp, 'Timer', ReadyTimer)


def controller(monkeypatch):
    owner = EventSPObservation.__new__(EventSPObservation)
    owner.config = SimpleNamespace(config_name='alas', SERVER='en', STAGE_ENTRANCE=['old'], override=Mock())
    owner.device = SimpleNamespace(screenshot=Mock(), click=Mock(), image='screen',
                                   app_is_running=Mock(return_value=True))
    owner.appear = Mock(return_value=False)
    owner.ui_page_appear = Mock(return_value=False)
    owner.match_template_color = Mock(return_value=True)
    owner._capture = Mock(return_value=True)
    monkeypatch.setattr(sp, 'ensure_event_page', Mock(return_value=True))
    monkeypatch.setattr(sp, 'ensure_event_switch', Mock(return_value=True))
    return owner


def record():
    return dict(diagnostics=[], entrance_clicks=0, cancel_clicks=0, home_clicks=0)


def entrance():
    return Button(area=(500, 250, 550, 300), color=(0, 0, 0),
                  button=(500, 250, 550, 300), name='sp')


def configure_inspection(owner):
    owner._wait_campaign = Mock(return_value=True)
    owner._sp_page = Mock(return_value=True)
    owner.stage_entrance = {'sp': entrance()}
    owner.campaign_chapter = 'ex_sp'
    owner._get_stage_name = Mock()
    owner._preparation = Mock(return_value=('map', MAP_PREPARATION_CANCEL))
    owner.loop = Mock(side_effect=lambda **kwargs: iter(range(4)))


def test_real_current_campaign_title_and_page_reject_missing_title_or_page(monkeypatch):
    previous = server.server
    server.set_server('en')
    try:
        owner = controller(monkeypatch)
        image = np.asarray(Image.open('tests/campaign/data/sp_campaign_red_dot.png').convert('RGB')).copy()
        owner.device.image = image
        owner.match_template_color = lambda button, **kwargs: button.match_template_color(owner.device.image, **kwargs)
        owner.ui_page_appear = Mock(return_value=True)
        assert owner._campaign_page()
        image[TEMPESTA_CAMPAIGN.area[1]:TEMPESTA_CAMPAIGN.area[3],
              TEMPESTA_CAMPAIGN.area[0]:TEMPESTA_CAMPAIGN.area[2]] = 0
        assert not owner._campaign_page()
        owner.device.image = np.asarray(Image.open('tests/campaign/data/sp_campaign_red_dot.png').convert('RGB'))
        owner.ui_page_appear.return_value = False
        assert not owner._campaign_page()
    finally:
        server.set_server(previous)


@pytest.mark.parametrize('overlay', [MAP_PREPARATION, FLEET_PREPARATION, BATTLE_PREPARATION, IN_MAP])
def test_background_campaign_title_never_authorizes_selector_through_preparation_or_map(monkeypatch, overlay):
    owner = controller(monkeypatch)
    owner.ui_page_appear.return_value = True
    owner.appear = lambda button, **kwargs: button is overlay
    assert not owner._campaign_page()


def test_fresh_sp_proof_requires_both_exact_selected_states(monkeypatch):
    owner = controller(monkeypatch)
    owner._campaign_page = Mock(return_value=True)
    combat = Mock(return_value='combat')
    aside = Mock(return_value='sp')
    monkeypatch.setattr(sp.MODE_SWITCH_20241219, 'get', combat)
    monkeypatch.setattr(sp.ASIDE_SWITCH_20241219, 'get', aside)
    assert owner._sp_page()
    for mode, chapter in [('story', 'sp'), ('combat', 'part2'), ('unknown', 'sp')]:
        combat.return_value = mode
        aside.return_value = chapter
        assert not owner._sp_page()


def test_observation_has_own_durable_budget_and_preserves_actual_ledgers(monkeypatch, tmp_path):
    owner = controller(monkeypatch)
    sentinels = {'alas-ex.json': '{"combat":"untouched"}', 'alas-EventStars.json': '{"sp":"complete"}',
                 'alas-event-rewards.json': '{"story":"paid"}'}
    for name, contents in sentinels.items():
        (tmp_path / name).write_text(contents)

    def inspect(current, persist):
        saved = load_sp_observations('alas', tmp_path)[KEY]
        assert saved['status'] == 'observing' and saved['combat_attempted'] is False
        assert saved['account'] == 'alas'
        return 'observed', 'captured'

    owner._inspect_sp = Mock(side_effect=inspect)
    owner._return_main = Mock(return_value=True)
    first = owner.observe(EVENT, folder=tmp_path)
    second = owner.observe(EVENT, folder=tmp_path)
    assert first['status'] == 'observed' and second['reused'] is True
    assert first['combat_attempted'] is False and first['availability'] == 'unverified'
    owner._inspect_sp.assert_called_once()
    owner._return_main.assert_called_once()
    owner.device.click.assert_not_called()
    assert owner.config.override.call_args_list[-1].kwargs == {'STAGE_ENTRANCE': ['old']}
    assert {name: (tmp_path / name).read_text() for name in sentinels} == sentinels


def test_entrance_reserved_before_click_and_only_preparation_is_captured(monkeypatch, tmp_path):
    owner = controller(monkeypatch)
    configure_inspection(owner)
    owner._return_main = Mock(return_value=True)

    def click(button):
        saved = load_sp_observations('alas', tmp_path)[KEY]
        assert saved['entrance_clicks'] == 1 and saved['combat_attempted'] is False
        assert button.name == 'sp'

    owner.device.click.side_effect = click
    result = owner.observe(EVENT, folder=tmp_path)
    assert result['status'] == 'observed'
    assert result['preparation'] == 'map'
    assert result['entrance_clicks'] == 1
    assert owner.device.click.call_count == 1
    assert owner.device.click.call_args.args[0] not in (
        MAP_PREPARATION, FLEET_PREPARATION, BATTLE_PREPARATION)
    assert owner._capture.call_args_list[-1].args[1] == 'map_preparation'


def test_unknown_current_event_never_taps_generic_selectors(monkeypatch):
    owner = controller(monkeypatch)
    owner._wait_campaign = Mock(return_value=False)
    assert owner._inspect_sp(record(), Mock()) == ('blocked', 'current_event_unrecognized')
    sp.ensure_event_switch.assert_not_called()
    owner.device.click.assert_not_called()


@pytest.mark.parametrize('chapter, names, box', [
    ('ex_ex', ['sp'], (500, 250, 550, 300)),
    ('ex_sp', ['sp', 'extra'], (500, 250, 550, 300)),
    ('ex_sp', ['t6'], (500, 250, 550, 300)),
    ('ex_sp', ['sp'], (500, 650, 550, 700)),
])
def test_wrong_or_ambiguous_ocr_entrance_never_clicks(monkeypatch, chapter, names, box):
    owner = controller(monkeypatch)
    configure_inspection(owner)
    button = entrance()
    button.raw_button = box
    owner.stage_entrance = {name: button for name in names}
    owner.campaign_chapter = chapter
    assert owner._inspect_sp(record(), Mock()) == ('blocked', 'sp_entrance_unrecognized')
    owner.device.click.assert_not_called()


def test_unknown_destination_does_not_tap_start_or_retry_entrance(monkeypatch):
    owner = controller(monkeypatch)
    configure_inspection(owner)
    owner._preparation.return_value = (None, None)
    current = record()
    assert owner._inspect_sp(current, Mock()) == ('blocked', 'sp_entrance_destination_unrecognized')
    assert owner.device.click.call_count == 1
    assert current['entrance_clicks'] == 1


def test_return_cancels_own_preparation_then_exact_campaign_home(monkeypatch, tmp_path):
    owner = controller(monkeypatch)
    frames = iter(['preparation', 'campaign', 'main'])
    current = ['preparation']

    def loop(**kwargs):
        for frame in frames:
            current[0] = frame
            yield frame

    owner.loop = loop
    owner._preparation = lambda: (('map', MAP_PREPARATION_CANCEL) if current[0] == 'preparation'
                                  else (None, None))
    owner._campaign_page = lambda: current[0] == 'campaign'
    owner.ui_page_appear = lambda page: page is page_main and current[0] == 'main'
    owner.appear = lambda button, **kwargs: button is GOTO_MAIN and current[0] == 'campaign'
    audit = record()
    audit['entrance_clicks'] = 1
    saved = []

    def persist():
        saved.append(audit.copy())

    assert owner._return_main(audit, persist)
    assert [call.args[0] for call in owner.device.click.call_args_list] == [MAP_PREPARATION_CANCEL, GOTO_MAIN]
    assert saved[0]['cancel_clicks'] == 1 and saved[1]['home_clicks'] == 1


@pytest.mark.parametrize('own_entrance', [0, 1])
def test_stuck_preparation_has_at_most_one_cancel_and_no_go(monkeypatch, own_entrance):
    owner = controller(monkeypatch)
    owner.loop = lambda **kwargs: iter(range(8))
    owner._preparation = Mock(return_value=('map', MAP_PREPARATION_CANCEL))
    current = record()
    current['entrance_clicks'] = own_entrance
    assert not owner._return_main(current, Mock())
    assert owner.device.click.call_count == own_entrance
    if own_entrance:
        owner.device.click.assert_called_once_with(MAP_PREPARATION_CANCEL)


def test_unknown_return_has_no_inputs(monkeypatch):
    owner = controller(monkeypatch)
    owner.loop = lambda **kwargs: iter(range(8))
    owner._preparation = Mock(return_value=(None, None))
    owner._campaign_page = Mock(return_value=False)
    assert not owner._return_main(record(), Mock())
    owner.device.click.assert_not_called()


def test_interrupted_observation_is_not_retried(monkeypatch, tmp_path):
    owner = controller(monkeypatch)
    pending = dict(record(), account='alas', event=EVENT, version=1, status='observing', combat_attempted=False)
    save_sp_observations('alas', {KEY: pending}, folder=tmp_path)
    owner._inspect_sp = Mock()
    result = owner.observe(EVENT, folder=tmp_path)
    assert result['reused'] is True
    owner._inspect_sp.assert_not_called()
    owner.device.click.assert_not_called()


def test_exception_restores_config_and_spends_only_observation_budget(monkeypatch, tmp_path):
    owner = controller(monkeypatch)
    owner._inspect_sp = Mock(side_effect=RuntimeError('unknown'))
    with pytest.raises(RuntimeError, match='unknown'):
        owner.observe(EVENT, folder=tmp_path)
    saved = load_sp_observations('alas', tmp_path)[KEY]
    assert saved['status'] == 'blocked' and saved['combat_attempted'] is False
    assert owner.config.override.call_args_list[-1].kwargs == {'STAGE_ENTRANCE': ['old']}


@pytest.mark.parametrize('contents', ['not json', '[]', '{"event": 1}'])
def test_malformed_journal_cannot_authorize_navigation(monkeypatch, tmp_path, contents):
    owner = controller(monkeypatch)
    (tmp_path / 'alas-sp-observation.json').write_text(contents)
    owner._inspect_sp = Mock()
    with pytest.raises(ScriptError):
        owner.observe(EVENT, folder=tmp_path)
    owner._inspect_sp.assert_not_called()


@pytest.mark.parametrize('account,event', [('other', EVENT), ('alas', 'other')])
def test_unsupported_identity_has_no_budget_or_inputs(monkeypatch, tmp_path, account, event):
    owner = controller(monkeypatch)
    owner.config.config_name = account
    assert owner.observe(event, folder=tmp_path)['status'] == 'unsupported'
    assert list(tmp_path.iterdir()) == []
    owner.device.app_is_running.assert_not_called()
    owner.device.click.assert_not_called()


def test_closed_game_does_not_consume_observation(monkeypatch, tmp_path):
    owner = controller(monkeypatch)
    owner.device.app_is_running.return_value = False
    with pytest.raises(GameNotRunningError):
        owner.observe(EVENT, folder=tmp_path)
    assert list(tmp_path.iterdir()) == []
    owner.device.click.assert_not_called()
