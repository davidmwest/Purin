from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import module.config.server as server
from module.config.config_updater import ConfigUpdater
from module.exception import ScriptError
from module.combat.assets import GET_ITEMS_1
from module.minigame.arcade import ArcadeTickets, next_week


@pytest.fixture(autouse=True)
def en_server():
    previous = server.server
    server.set_server('en')
    yield
    server.set_server(previous)


def test_current_scaled_arcade_room_controls_reject_dimmed_background():
    from module.base.utils import load_image
    from module.minigame.arcade import ARCADE_START, ARCADE_TITLE
    image = load_image('assets/en/minigame/arcade_room.png')
    for control in (ARCADE_TITLE, ARCADE_START):
        assert control.match_template_color(image, offset=(2, 2), similarity=.95, threshold=10)
        assert not control.match_template_color((image * .5).astype('uint8'), offset=(2, 2), similarity=.95, threshold=10)


def test_captured_credit_guide_is_distinct_from_the_room_and_does_not_cover_start_input():
    from module.base.utils import load_image
    from module.minigame.arcade import ARCADE_CREDIT_TUTORIAL, ARCADE_ROOM_TUTORIAL, ARCADE_PRIZE_TUTORIAL, ARCADE_TITLE
    room = load_image('assets/en/minigame/arcade_room_tutorial.png')
    credits = load_image('assets/en/minigame/arcade_credit_tutorial.png')
    assert ARCADE_ROOM_TUTORIAL.match(room, offset=(2, 2), similarity=.97)
    assert ARCADE_CREDIT_TUTORIAL.match(credits, offset=(2, 2), similarity=.97)
    assert not ARCADE_CREDIT_TUTORIAL.match(room, offset=(2, 2), similarity=.97)
    assert not ARCADE_TITLE.match_template_color(room, offset=(2, 2), similarity=.95, threshold=10)
    prize = load_image('assets/en/minigame/arcade_prize_tutorial.png')
    assert ARCADE_PRIZE_TUTORIAL.match(prize, offset=(2, 2), similarity=.97)
    assert not ARCADE_PRIZE_TUTORIAL.match(room, offset=(2, 2), similarity=.97)
    assert ARCADE_CREDIT_TUTORIAL.button[3] < 300  # The game's Start button is below y380.


def test_arcade_defaults_disabled_and_keeps_farming_and_other_minigame_settings():
    old = {'Main': {'Campaign': {'Name': '12-4'}, 'StopCondition': {'OilLimit': 5000}},
           'Minigame': {'Scheduler': {'Enable': False}}}
    new = ConfigUpdater().config_update(old)
    assert new['ArcadeTickets']['Scheduler']['Enable'] is False
    assert new['ArcadeTickets']['Arcade'] == {'ObserveOnly': False, 'CreditsPerRound': 5}
    assert new['Main']['Campaign']['Name'] == '12-4'
    assert new['Main']['StopCondition']['OilLimit'] == 5000
    assert not new['Minigame']['Scheduler']['Enable']


@pytest.mark.parametrize('now,expected', [
    ('2026-11-04T12:00:00-08:00', '2026-11-09T00:00:00-07:00'),
    ('2026-11-08T22:59:59-08:00', '2026-11-09T00:00:00-07:00'),
    ('2026-11-08T23:00:00-08:00', '2026-11-16T00:00:00-07:00'),
    ('2026-10-31T12:00:00-07:00', '2026-11-02T00:00:00-07:00'),
])
def test_weekly_schedule_uses_fixed_en_server_reset_across_local_dst(now, expected):
    previous = server.server
    server.set_server('en')
    try:
        result = next_week(datetime.fromisoformat(now))
        assert result.astimezone(timezone.utc) == datetime.fromisoformat(expected).astimezone(timezone.utc)
    finally:
        server.set_server(previous)


def task(observe=True):
    job = ArcadeTickets.__new__(ArcadeTickets)
    job.config = SimpleNamespace(config_name='arcade-test', Arcade_ObserveOnly=observe, cross_set=Mock(),
                                 task_call=Mock(), task_delay=Mock())
    for method in ('enter_arcade', 'open_games', 'capture', 'ui_goto_main', 'inspect_shooting', 'to_room'):
        setattr(job, method, Mock())
    return job


def test_observation_does_not_leave_a_repeat_job_enabled():
    job = task()
    job.run()
    job.config.cross_set.assert_called_once_with('ArcadeTickets.Scheduler.Enable', False)
    job.ui_goto_main.assert_called_once()
    job.config.task_call.assert_not_called()


def test_unknown_arcade_layout_defers_and_queues_normal_game_restart():
    job = task()
    job.open_games.side_effect = ScriptError('unknown game list')
    job.to_room.side_effect = ScriptError('return not recognized')
    job.run()
    job.config.task_call.assert_called_once_with('Restart')
    job.config.task_delay.assert_called_once_with(minute=60)


def test_unreadable_arcade_counter_can_return_home_without_restarting_the_game():
    job = task()
    job.open_games.side_effect = ScriptError('unreadable list')
    job.run()
    job.config.task_call.assert_not_called()
    job.ui_goto_main.assert_called_once()
    job.config.task_delay.assert_called_once_with(minute=60)


def test_captured_preparation_reads_the_whole_owned_credit_fraction():
    from module.base.utils import load_image
    from module.minigame.arcade import SHOOTING_START, SHOOTING_TITLE, SHOOTING_PLUS, SELECTED_CREDITS
    from module.minigame.arcade_state import parse_selection
    image = load_image('assets/en/minigame/arcade_shooting_prepare.png')
    for control in (SHOOTING_TITLE, SHOOTING_START, SHOOTING_PLUS):
        assert control.match_template_color(image, offset=(3, 3), similarity=.92, threshold=18)
    assert parse_selection(SELECTED_CREDITS.ocr(image)) == (1, 10)


def test_room_scrollbar_like_pixels_do_not_establish_the_game_selection_page():
    from module.base.utils import load_image
    from module.minigame.arcade import ARCADE_LIST_TITLE
    assert ARCADE_LIST_TITLE.match_template_color(load_image('assets/en/minigame/arcade_games.png'),
                                                 offset=(3, 3), similarity=.92, threshold=18)
    assert not ARCADE_LIST_TITLE.match_template_color(load_image('assets/en/minigame/arcade_room.png'),
                                                     offset=(3, 3), similarity=.92, threshold=18)


def test_returned_list_with_arcade_title_is_a_list_only_with_the_shooting_card():
    from module.base.utils import load_image
    job = task()
    for name in ('arcade_games.png', 'arcade_return_list.png'):
        job.device = SimpleNamespace(image=load_image('assets/en/minigame/' + name))
        assert job.game_list()
    job.device.image = load_image('assets/en/minigame/arcade_room.png')
    assert not job.game_list()
    job.device.image = load_image('assets/en/minigame/arcade_return_list.png')
    job.device.image[439:474, 242:502] = 0
    assert not job.game_list()


def test_actual_idle_round_award_is_nine_hundred_tickets_not_the_credit_grant():
    from module.base.utils import load_image
    from module.minigame.arcade import ticket_receipt
    assert ticket_receipt(load_image('assets/en/minigame/arcade_ticket_receipt.png')) == 900
    assert ticket_receipt(load_image('assets/en/minigame/arcade_credit_receipt.png')) is None


def test_missing_ticket_amount_or_wrong_currency_icon_cannot_verify_a_round():
    from module.base.utils import load_image
    from module.minigame.arcade import ticket_receipt
    image = load_image('assets/en/minigame/arcade_ticket_receipt.png')
    image[371:393, 641:687] = 0
    assert ticket_receipt(image) is None
    image = load_image('assets/en/minigame/arcade_ticket_receipt.png')
    credit = load_image('assets/en/minigame/arcade_credit_receipt.png')
    image[304:368, 596:684] = credit[304:368, 596:684]
    assert ticket_receipt(image) is None


@pytest.mark.parametrize('text,kind', [
    ("You've reached your monthly limit of Game Tickets, and will not be able to earn any more. Continue playing the minigame?", 'monthly'),
    ("You've reached the maximum number of Game Tickets, and will not be able to earn any more. Continue playing the minigame?", 'storage'),
    ('Monthly limit reached. Buy more Credits?', None),
    ('Continue playing the minigame?', None),
    ('You have enough coins. Continue?', None),
])
def test_only_the_full_source_verified_ticket_cap_notice_can_be_acknowledged(text, kind):
    from module.minigame.arcade import cap_kind
    assert cap_kind(text) == kind


def test_uncertain_round_receipt_durably_blocks_a_second_start(tmp_path):
    import numpy as np
    from PIL import Image
    from module.minigame.arcade import SHOOTING_START
    from module.minigame.arcade_state import ArcadeJournal
    job = task(False)
    job.journal = ArcadeJournal('arcade-test', tmp_path / 'state')
    job.device = SimpleNamespace(image=np.zeros((720, 1280, 3), dtype=np.uint8), click=Mock())
    job.select_credits = Mock(return_value=5)
    job.stable_credits = Mock(return_value=(5, 10))
    job.preparing = Mock(return_value=True)
    job.loop = lambda **kwargs: iter([None])
    job.credit_receipt = Mock(return_value=True)
    job.dismiss_receipt = Mock()
    def capture(label):
        path = tmp_path / (label + '.png')
        Image.fromarray(job.device.image).save(str(path))
        return str(path)
    job.capture = capture
    with pytest.raises(ScriptError):
        job.idle_round(10)
    with pytest.raises(ScriptError):
        job.idle_round(10)
    job.device.click.assert_called_once_with(SHOOTING_START)
    job.dismiss_receipt.assert_not_called()
    pending = ArcadeJournal('arcade-test', tmp_path / 'state').pending()
    assert pending['start_clicks'] == 1 and pending['receipt_candidate']['sha256']


@pytest.mark.parametrize('result_fixture', ['arcade_shooting_result.png', 'arcade_shooting_result_native.png'])
def test_real_ticket_then_zero_score_result_acknowledges_once_before_finishing(tmp_path, result_fixture):
    from pathlib import Path
    from PIL import Image
    from module.base.utils import load_image
    from module.minigame.arcade import SHOOTING_START, SHOOTING_RESULT_OK
    from module.minigame.arcade_state import ArcadeJournal
    prepare = load_image('assets/en/minigame/arcade_shooting_prepare.png')
    award = load_image('assets/en/minigame/arcade_ticket_receipt.png')
    result = load_image('assets/en/minigame/' + result_fixture)
    job = task(False)
    job.device = SimpleNamespace(image=prepare, click=Mock())
    job.journal = ArcadeJournal('arcade-test', tmp_path / 'state')
    job.select_credits = Mock(return_value=5)
    job.stable_credits = Mock(side_effect=[(5, 15), (5, 10)])
    job.dismiss_receipt = Mock()
    job.credit_receipt = lambda: GET_ITEMS_1.match(job.device.image, offset=(20, 20), similarity=.85)
    def frames(**kwargs):
        for frame in (award, award, result, result, result, prepare):
            job.device.image = frame
            yield frame
    def capture(label):
        path = tmp_path / (label + '.png')
        Image.fromarray(job.device.image).save(str(path))
        return str(path)
    job.loop, job.capture = frames, capture
    assert job.idle_round(15) == 10
    assert [call.args[0] for call in job.device.click.call_args_list] == [SHOOTING_START, SHOOTING_RESULT_OK]
    job.dismiss_receipt.assert_called_once()
    row = ArcadeJournal('arcade-test', tmp_path / 'state').data['rounds'][0]
    assert row['status'] == 'complete' and row['result_ack_clicks'] == 1 and row['tickets'] == 900


@pytest.mark.parametrize('result_fixture', ['arcade_shooting_result.png', 'arcade_shooting_result_native.png'])
def test_result_requires_the_zero_score_and_visible_acknowledgement(result_fixture):
    from module.base.utils import load_image
    image = load_image('assets/en/minigame/' + result_fixture)
    job = task(False)
    job.device = SimpleNamespace(image=image)
    assert job.shooting_result()
    job.device.image = (image * .5).astype('uint8')
    assert not job.shooting_result()
    for box in ((642, 312, 835, 343), (371, 279, 588, 386), (568, 442, 712, 477)):
        job.device.image = image.copy()
        x, y, x2, y2 = box
        job.device.image[y:y2, x:x2] = 0
        assert not job.shooting_result()


def test_higher_priority_work_yields_after_one_verified_round():
    job = task(False)
    job.rounds, job.yielded, job.capped = 0, False, False
    job.open_shooting = Mock()
    job.idle_round = Mock(return_value=5)
    job.stable_credits = Mock(return_value=5)
    job.config.task_switched = Mock(return_value=True)
    assert job.play_to(10, 0) == 5
    assert job.yielded and job.rounds == 1
    job.idle_round.assert_called_once_with(10)


def test_unknown_or_blank_modal_cannot_be_acknowledged_as_a_cap(monkeypatch):
    import numpy as np
    job = task(False)
    job.device = SimpleNamespace(image=np.zeros((720, 1280, 3), dtype=np.uint8), display_scale=2)
    job.appear = Mock(return_value=True)
    job.handle_popup_confirm = Mock()
    assert not job.handle_cap_warning()
    job.handle_popup_confirm.assert_not_called()


def test_hitting_ticket_cap_while_freeing_credit_space_still_claims_weekly_grant():
    job = task(False)
    job.stable_credits = Mock(return_value=35)
    job.matches = Mock(return_value=True)
    job.finish_pending = Mock()
    def make_space(credits, target):
        assert (credits, target) == (35, 30)
        job.capped = True
        return 30
    job.play_to = Mock(side_effect=make_space)
    job.collect_weekly = Mock(return_value=40)
    job.run()
    job.collect_weekly.assert_called_once_with(30)
    job.play_to.assert_called_once_with(35, 30)
    job.config.task_call.assert_not_called()
