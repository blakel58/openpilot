import unittest

from openpilot.selfdrive.ui.body import animations
from openpilot.selfdrive.ui.body.animations import ASLEEP, NORMAL, OFFROAD_SCENES, YAWN, Animation, AnimationMode, \
                                                     BATTERY_METER, FaceAnimator, LIVE_DOT, duration

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
        assert not set(BATTERY_METER) & set(frame), name

  def test_scenes_hand_back_to_the_sleeping_face(self):
    # each scene plays forward once and ends on a frame the sleeping face can cut to, without rewinding
    for scene in OFFROAD_SCENES:
      assert scene.mode == AnimationMode.ONCE_FORWARD
      animator = FaceAnimator(ASLEEP)
      animator.set_animation(scene)
      assert animator.get_dots() == scene.frames[0]
      animator.set_animation(ASLEEP)
      animator._start_time -= duration(scene) + 0.01   # jump to the end of the scene
      animator._seen_nonzero = True
      assert animator.get_dots() == ASLEEP.frames[0]
      assert animator._animation is ASLEEP and not animator._rewinding

  def test_yawn_ends_on_the_normal_face(self):
    assert YAWN.frames[-1] == NORMAL.frames[0]
