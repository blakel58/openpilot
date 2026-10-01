import unittest

from openpilot.selfdrive.ui.body import animations
from openpilot.selfdrive.ui.body.animations import Animation, LIVE_DOT

GRID_ROWS, GRID_COLS = 8, 16


class TestBodyAnimations(unittest.TestCase):
  def test_frames_fit_the_grid(self):
    anims = {name: a for name, a in vars(animations).items() if isinstance(a, Animation)}
    assert {"NORMAL", "ASLEEP", "SLEEPY", "INQUISITIVE", "TIRED", "CONTENT", "WINK"} <= set(anims)
    for name, anim in anims.items():
      for frame in anim.frames + (anim.starting_frames or []):
        assert all(0 <= r < GRID_ROWS and 0 <= c < GRID_COLS for r, c in frame), name
        # the live indicator must never be mistaken for part of a face
        assert LIVE_DOT not in frame, name
