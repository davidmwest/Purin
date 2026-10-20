from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from module.base.timer import Timer
from module.campaign.campaign_base import CampaignBase
from module.combat.emotion import Emotion
from module.exception import CampaignEnd


def make_campaign(mode, popup):
    """
    Args:
        mode (str): Emotion mode, 'calculate' or 'ignore'.
        popup (bool): If the warning about a low mood is on screen.
    """
    campaign = CampaignBase.__new__(CampaignBase)
    campaign.emotion = SimpleNamespace(is_ignore='ignore' in mode, set_exhausted=Mock())
    campaign.handle_popup_cancel = Mock(return_value=popup)
    campaign.handle_popup_confirm = Mock(return_value=popup)
    campaign.interval_reset = Mock()
    campaign.emotion_withdraw = Mock()
    campaign.fleet_current_index = 2
    campaign.story_popup_timeout = Timer(10)
    return campaign


def test_low_mood_with_emotion_calculated_waits_for_the_fleet_to_recover():
    campaign = make_campaign('calculate', popup=True)

    with pytest.raises(CampaignEnd):
        campaign.handle_combat_low_emotion()

    campaign.handle_popup_cancel.assert_called_once()
    campaign.handle_popup_confirm.assert_not_called()
    campaign.emotion.set_exhausted.assert_called_once_with(2)
    campaign.emotion_withdraw.assert_called_once_with()


def test_no_low_mood_warning_nothing_to_do():
    campaign = make_campaign('calculate', popup=False)

    assert campaign.handle_combat_low_emotion() is False

    campaign.emotion.set_exhausted.assert_not_called()
    campaign.emotion_withdraw.assert_not_called()


@pytest.mark.parametrize('mode', ['calculate', 'ignore'])
def test_story_skip_popup_is_not_a_low_mood(mode):
    # A story skipped at the first entry of a stage asks to confirm in the same kind of popup
    campaign = make_campaign(mode, popup=True)
    campaign.story_popup_timeout.start()

    assert campaign.handle_combat_low_emotion() is False

    campaign.handle_popup_cancel.assert_not_called()
    campaign.handle_popup_confirm.assert_not_called()
    campaign.emotion.set_exhausted.assert_not_called()


def test_low_mood_with_emotion_ignored_attacks_anyway():
    campaign = make_campaign('ignore', popup=True)

    assert campaign.handle_combat_low_emotion() is True

    campaign.handle_popup_confirm.assert_called_once()
    campaign.handle_popup_cancel.assert_not_called()
    campaign.emotion.set_exhausted.assert_not_called()


def test_exhausted_fleet_is_counted_as_zero():
    record = datetime(2026, 10, 13, 22, 0)
    config = SimpleNamespace(
        Emotion_Mode='calculate',
        Emotion_Fleet1Value=113, Emotion_Fleet1Record=record, Emotion_Fleet1Control='prevent_green_face',
        Emotion_Fleet1Recover='not_in_dormitory', Emotion_Fleet1Oath=False,
        Emotion_Fleet2Value=110, Emotion_Fleet2Record=record, Emotion_Fleet2Control='prevent_green_face',
        Emotion_Fleet2Recover='not_in_dormitory', Emotion_Fleet2Oath=False,
        set_record=Mock(),
        # Without the task DormRest, fleets are followed as a whole
        is_task_enabled=lambda task: False,
    )
    emotion = Emotion(config)

    emotion.set_exhausted(1)

    recorded = config.set_record.call_args[1]
    assert recorded['Emotion_Fleet1Value'] == 0
    # The other fleet keeps what it had, and what it recovered since
    assert recorded['Emotion_Fleet2Value'] >= 110
