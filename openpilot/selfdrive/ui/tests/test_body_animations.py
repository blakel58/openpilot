import time
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
        # a dot is (row, col) or (row, col, size)
        assert all(0 <= d[0] <= GRID_ROWS - 1 and 0 <= d[1] <= GRID_COLS - 1 for d in frame), name
        assert all(0 < d[2] <= 2 for d in frame if len(d) > 2), name
        # the live indicator must never be mistaken for part of a face
        # (scenes roll along the bottom row; they only play asleep, and the red dot draws on top anyway)
        assert anim in OFFROAD_SCENES or LIVE_DOT not in frame, name
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

  def test_held_scene_plays_every_frame(self):
    # the layout keeps asking for the scene while it plays; asking for the sleeping face mid-play would rewind it
    for scene in OFFROAD_SCENES:
      animator = FaceAnimator(ASLEEP)
      animator.set_animation(scene)
      animator.get_dots()
      for i in range(len(scene.frames)):
        animator._start_time = time.monotonic() - (i + 0.5) * scene.frame_duration
        animator.set_animation(scene)
        assert animator.get_dots() == scene.frames[i]
        assert animator._animation is scene and not animator._rewinding

  def test_reactions_start_right_away(self):
    # a looping face resting on its first frame hands over immediately, even before its first blink
    from openpilot.selfdrive.ui.body.animations import HAPPY
    animator = FaceAnimator(NORMAL)
    animator._start_time = time.monotonic() - 2.0   # resting between blinks, has blinked zero times as far as it knows
    animator._seen_nonzero = False
    animator.set_animation(HAPPY)
    animator.get_dots()
    assert animator._animation is HAPPY

  def test_battery_meter(self):
    from openpilot.selfdrive.ui.body.animations import METER_EMPTY, battery_meter, meter_color
    # just plugged in: nothing has filled in yet
    assert all(color == METER_EMPTY for _, color in battery_meter(0.6, 0., 0.))
    # settled: 4 of 8 filled, 4 empty, plus the next dot swelling
    meter = battery_meter(0.5, 10., 10.)
    assert sum(color == METER_EMPTY for _, color in meter) == 4
    assert sum(color == meter_color(0.5) for _, color in meter) == 5
    # full: every dot filled, nothing left to swell
    assert len(battery_meter(1.0, 10., 10.)) == len(BATTERY_METER)
    assert meter_color(0.05)[0] > meter_color(0.05)[1] and meter_color(0.95)[1] > meter_color(0.95)[0]
