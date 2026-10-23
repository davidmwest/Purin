from unittest.mock import Mock

import numpy as np
from PIL import Image

from module.campaign.event_strict_maid import STRICT_MAID_TAB, StrictMaidObserver


def test_exact_sidebar_template_recognizes_label_and_rejects_blank():
    image = np.asarray(Image.open(STRICT_MAID_TAB.file).convert('RGB'))
    assert STRICT_MAID_TAB.match_template_color(image, offset=(3, 3), similarity=.85)
    assert not STRICT_MAID_TAB.match_template_color(image * 0, offset=(3, 3), similarity=.85)


def test_observer_uses_the_shared_durable_budget_without_a_destination_action(tmp_path):
    helper = StrictMaidObserver.__new__(StrictMaidObserver)
    record = {'status': 'observed', 'returned_main': False}
    helper._observe_event_tab = Mock(return_value=record)
    assert helper.run('event_20261022_cn', folder=tmp_path) is record
    helper._observe_event_tab.assert_called_once_with(
        'event_20261022_cn', STRICT_MAID_TAB, 'strict_maid', folder=tmp_path)
