from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np

from module.map.camera import Camera


def test_a_swipe_before_any_view_waits_for_the_grid_center():
    # 1-1 on the F2P account, 2026-10-22: its first view was outside the map, the swipe that followed had no view
    # before it to compare with
    camera = Camera.__new__(Camera)
    camera.config = SimpleNamespace(MAP_GRID_CENTER_TOLERANCE=0.1)
    camera.device = SimpleNamespace(screenshot=Mock(), _screenshot_interval=SimpleNamespace(clear=Mock()))
    offsets = iter([[0.43, 0.11], [0.5, 0.5]])

    def update_view():
        camera.view = SimpleNamespace(center_offset=np.array(next(offsets)))
        return True

    camera._update_view = update_view
    camera._update_view_data = Mock()

    camera.update(wait_swipe=True)

    camera._update_view_data.assert_called_once_with()
    assert np.allclose(camera.view.center_offset, [0.5, 0.5])
