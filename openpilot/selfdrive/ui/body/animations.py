from dataclasses import dataclass
from enum import Enum
import math
import time


class AnimationMode(Enum):
  ONCE_FORWARD = 1
  ONCE_FORWARD_BACKWARD = 2
  REPEAT_FORWARD = 3
  REPEAT_FORWARD_BACKWARD = 4


@dataclass
class Animation:
  frames: list[list[tuple[int, int]]]
  starting_frames: list[list[tuple[int, int]]] | None = None  # played once before the main loop
  frame_duration: float = 0.15       # seconds each frame is shown
  mode: AnimationMode = AnimationMode.REPEAT_FORWARD_BACKWARD
  repeat_interval: float = 5.0      # seconds between animation restarts (only for REPEAT modes)
  hold_end: float = 0.0             # seconds to hold the last frame before playing backward (only for *_BACKWARD modes)
  left_turn_remove: list[tuple[int, int]] | None = None   # dots to remove from frame when turning left
  right_turn_remove: list[tuple[int, int]] | None = None   # dots to remove from frame when turning right


# --- Animation Helper Functions ---

def _mirror(dots: list[tuple[int, int]]) -> list[tuple[int, int]]:
  """Mirror a component from the left side of the face to the right"""
  return [(r, 15 - c) for r, c in dots]


def _mirror_no_flip(dots: list[tuple[int, int]]) -> list[tuple[int, int]]:
  """Move a component to the mirrored position on the right half without flipping its shape."""
  min_c = min(c for _, c in dots)
  max_c = max(c for _, c in dots)
  return [(r, 15 - max_c - min_c + c) for r, c in dots]


def _shift(dots: list[tuple], rc: tuple[float, float]) -> list[tuple]:
  # a dot is (row, col) or (row, col, size); size scales the dot's radius and defaults to 1
  dr, dc = rc
  return [(d[0] + dr, d[1] + dc, *d[2:]) for d in dots]


def _sized(dots: list[tuple], size: float) -> list[tuple]:
  return [(d[0], d[1], size * (d[2] if len(d) > 2 else 1.)) for d in dots]


def _make_frame(left_eye: list[tuple[int, int]], right_eye: list[tuple[int, int]],
                left_brow: list[tuple[int, int]], right_brow: list[tuple[int, int]],
                mouth: list[tuple[int, int]]) -> list[tuple[int, int]]:
  return left_eye + left_brow + right_eye + right_brow + mouth


# --- Animation Helper Components ---

# Eyes (left side)
EYE_OPEN = [
        (2, 2), (2, 3),
(3, 1), (3, 2), (3, 3), (3, 4),
(4, 1), (4, 2), (4, 3), (4, 4),
        (5, 2), (5, 3)
]
EYE_HALF = [
(4, 1), (4, 2), (4, 3), (4, 4),
        (5, 2), (5, 3)
]
EYE_CLOSED = [
(4, 1),                 (4, 4),
        (5, 2), (5, 3),
]
EYE_LEFT_LOOK = [
        (2, 2), (2, 3),
(3, 1), (3, 2),
(4, 1), (4, 2),
        (5, 2), (5, 3),
]
EYE_RIGHT_LOOK = [
        (2, 2), (2, 3),
                (3, 3), (3, 4),
                (4, 3), (4, 4),
        (5, 2), (5, 3),
]

# Eyebrows (left side)
BROW_HIGH = [
        (0, 1), (0, 2),
(1, 0),
]
BROW_LOWERED = [
        (1, 1), (1, 2),
(2, 0)
]
BROW_STRAIGHT = [(1, 0), (1, 1), (1, 2)]
# Mouths (centered, not mirrored)
MOUTH_SMILE = [
(6, 6),                 (6, 9),
        (7, 7), (7, 8),
]
MOUTH_NORMAL = [(7, 7), (7, 8)]

# --- Animations ---

NORMAL = Animation(
  frames=[
    _make_frame(EYE_OPEN, _mirror(EYE_OPEN), BROW_HIGH, _mirror(BROW_HIGH), MOUTH_SMILE),
    _make_frame(EYE_HALF, _mirror(EYE_HALF), BROW_HIGH, _mirror(BROW_HIGH), MOUTH_SMILE),
    _make_frame(EYE_CLOSED, _mirror(EYE_CLOSED), BROW_LOWERED, _mirror(BROW_LOWERED), MOUTH_SMILE),
  ],
  left_turn_remove=[
    (3, 3), (3, 4),
    (4, 3), (4, 4),
  ] + _mirror_no_flip([
    (3, 1), (3, 2),
    (4, 1), (4, 2),
  ]),
  right_turn_remove=[
    (3, 1), (3, 2),
    (4, 1), (4, 2),
  ] + _mirror_no_flip([
    (3, 3), (3, 4),
    (4, 3), (4, 4),
  ])
)

ASLEEP = Animation(
  frames=[
    _make_frame(EYE_CLOSED, _mirror(EYE_CLOSED), [], [], MOUTH_NORMAL),
  ],
)

SLEEPY = Animation(
  frames=[
    _make_frame(EYE_CLOSED, _mirror(EYE_CLOSED), _shift(BROW_STRAIGHT, (1, 0)), [], MOUTH_NORMAL),
    _make_frame(EYE_HALF, _mirror(EYE_CLOSED), BROW_LOWERED, [], MOUTH_NORMAL),
    _make_frame(EYE_OPEN, _mirror(EYE_CLOSED), BROW_HIGH, [], MOUTH_NORMAL)
  ],
  frame_duration=0.25,
  mode=AnimationMode.ONCE_FORWARD_BACKWARD,
  repeat_interval=10,
  hold_end=1.5,
)

INQUISITIVE = Animation(
  frames=[
    _make_frame(EYE_OPEN, _mirror(EYE_OPEN), BROW_HIGH, _mirror(BROW_HIGH), MOUTH_SMILE),

    _make_frame(EYE_LEFT_LOOK, _mirror(EYE_RIGHT_LOOK), BROW_HIGH, _mirror(BROW_HIGH), MOUTH_SMILE),
    _make_frame(_shift(EYE_LEFT_LOOK, (0, -1)), _shift(_mirror(EYE_RIGHT_LOOK), (0, -1)), BROW_HIGH, _mirror(BROW_HIGH), MOUTH_SMILE),
    _make_frame(_shift(EYE_LEFT_LOOK, (0, -1)), _shift(_mirror(EYE_RIGHT_LOOK), (0, -1)), BROW_HIGH, _mirror(BROW_HIGH), MOUTH_SMILE),
    _make_frame(_shift(EYE_LEFT_LOOK, (0, -1)), _shift(_mirror(EYE_RIGHT_LOOK), (0, -1)), BROW_HIGH, _mirror(BROW_HIGH), MOUTH_SMILE),
    _make_frame(EYE_LEFT_LOOK, _mirror(EYE_RIGHT_LOOK), BROW_HIGH, _mirror(BROW_HIGH), MOUTH_SMILE),

    _make_frame(EYE_RIGHT_LOOK, _mirror(EYE_LEFT_LOOK), BROW_HIGH, _mirror(BROW_HIGH), MOUTH_SMILE),
    _make_frame(_shift(EYE_RIGHT_LOOK, (0, 1)), _shift(_mirror(EYE_LEFT_LOOK), (0, 1)), BROW_HIGH, _mirror(BROW_HIGH), MOUTH_SMILE),
    _make_frame(_shift(EYE_RIGHT_LOOK, (0, 1)), _shift(_mirror(EYE_LEFT_LOOK), (0, 1)), BROW_HIGH, _mirror(BROW_HIGH), MOUTH_SMILE),
    _make_frame(_shift(EYE_RIGHT_LOOK, (0, 1)), _shift(_mirror(EYE_LEFT_LOOK), (0, 1)), BROW_HIGH, _mirror(BROW_HIGH), MOUTH_SMILE),
    _make_frame(EYE_RIGHT_LOOK, _mirror(EYE_LEFT_LOOK), BROW_HIGH, _mirror(BROW_HIGH), MOUTH_SMILE),

    _make_frame(EYE_OPEN, _mirror(EYE_OPEN), BROW_HIGH, _mirror(BROW_HIGH), MOUTH_SMILE),
  ],
  mode=AnimationMode.REPEAT_FORWARD,
  frame_duration=0.15,
  repeat_interval=10
)

# low battery: heavy eyelids and a long, slow blink
TIRED = Animation(
  frames=[
    _make_frame(EYE_HALF, _mirror(EYE_HALF), BROW_LOWERED, _mirror(BROW_LOWERED), MOUTH_NORMAL),
    _make_frame(EYE_CLOSED, _mirror(EYE_CLOSED), _shift(BROW_STRAIGHT, (1, 0)), _mirror(_shift(BROW_STRAIGHT, (1, 0))), MOUTH_NORMAL),
  ],
  frame_duration=0.4,
  repeat_interval=4,
  hold_end=0.8,
)

# charging: eyes closed and smiling, with the occasional peek
CONTENT = Animation(
  frames=[
    _make_frame(EYE_CLOSED, _mirror(EYE_CLOSED), BROW_HIGH, _mirror(BROW_HIGH), MOUTH_SMILE),
    _make_frame(EYE_HALF, _mirror(EYE_HALF), BROW_HIGH, _mirror(BROW_HIGH), MOUTH_SMILE),
    _make_frame(EYE_OPEN, _mirror(EYE_OPEN), BROW_HIGH, _mirror(BROW_HIGH), MOUTH_SMILE),
  ],
  frame_duration=0.2,
  repeat_interval=8,
  hold_end=0.8,
)

# someone just connected to drive: a quick wink
WINK = Animation(
  frames=[
    _make_frame(EYE_OPEN, _mirror(EYE_OPEN), BROW_HIGH, _mirror(BROW_HIGH), MOUTH_SMILE),
    _make_frame(EYE_OPEN, _mirror(EYE_HALF), BROW_HIGH, _mirror(BROW_LOWERED), MOUTH_SMILE),
    _make_frame(EYE_OPEN, _mirror(EYE_CLOSED), BROW_HIGH, _mirror(BROW_LOWERED), MOUTH_SMILE),
  ],
  frame_duration=0.12,
  mode=AnimationMode.ONCE_FORWARD_BACKWARD,
  hold_end=0.5,
)

# shown (in red) while someone is connected and driving; a corner no face uses
LIVE_DOT = (7, 15)

# --- Scenes ---
# short shows played now and then while the body sleeps. each starts and ends on a
# frame the sleeping face can cut to, and plays forward once

GRID_ROWS, GRID_COLS = 8, 16

MOUTH_OPEN = [
        (5, 7), (5, 8),
(6, 6),                 (6, 9),
        (7, 7), (7, 8),
]

# a tiny comma body: head, pole and its two wheels
_BODY = [
(0, 0), (0, 1), (0, 2),
(1, 0), (1, 1), (1, 2),
        (2, 1),
        (3, 1),
(4, 0),         (4, 2),
]
_COMMA = [
(0, 0), (0, 1),
(1, 0), (1, 1),
        (2, 1),
(3, 0),
]
# the tiny body and the comma are drawn smaller than the face (dots and spacing both scaled down),
# the same size in every scene
_TINY_SCALE = 0.7
_TINY_BODY = [(r * _TINY_SCALE, c * _TINY_SCALE, _TINY_SCALE) for r, c in _BODY]
_TINY_COMMA = [(r * _TINY_SCALE, c * _TINY_SCALE, _TINY_SCALE) for r, c in _COMMA]
_TINY_ROW = (GRID_ROWS - 1) - 4 * _TINY_SCALE  # wheels on the bottom row

# sprites move a quarter of a dot at a time so they glide instead of stepping
_GLIDE_STEP = 0.25
_GLIDE_FRAME = 0.05  # seconds per step: 5 dots per second


def _place(dots: list[tuple], rc: tuple[float, float]) -> list[tuple]:
  """Move a sprite (by any fraction of a dot) and drop whatever falls off the grid."""
  return [d for d in _shift(dots, rc) if 0 <= d[0] <= GRID_ROWS - 1 and 0 <= d[1] <= GRID_COLS - 1]


def _glide(start: float, end: float) -> list[float]:
  """Column positions from start to end in quarter-dot steps."""
  steps = round(abs(end - start) / _GLIDE_STEP)
  return [start + (end - start) * i / steps for i in range(steps + 1)]


_SLEEP_FACE = _make_frame(EYE_CLOSED, _mirror(EYE_CLOSED), [], [], MOUTH_NORMAL)

# slow breathing: the sleeping face swells a little on each breath
_BREATH_PERIOD = 4.0  # seconds per breath
_BREATH_FRAME = 0.05
_BREATH_STEPS = round(2 * _BREATH_PERIOD / _BREATH_FRAME)  # two breaths
SNORE = Animation(
  frames=[_SLEEP_FACE] +
         [_sized(_SLEEP_FACE, 1. + 0.14 * math.sin(math.pi * i * _BREATH_FRAME / _BREATH_PERIOD) ** 2) for i in range(_BREATH_STEPS)] +
         [_SLEEP_FACE],
  frame_duration=_BREATH_FRAME,
  mode=AnimationMode.ONCE_FORWARD,
)

# one eye opens, has a look around, and goes back to sleep
PEEK = Animation(
  frames=[
    _SLEEP_FACE,
    _make_frame(EYE_HALF, _mirror(EYE_CLOSED), [], [], MOUTH_NORMAL),
    _make_frame(EYE_OPEN, _mirror(EYE_CLOSED), BROW_HIGH, [], MOUTH_NORMAL),
    _make_frame(EYE_LEFT_LOOK, _mirror(EYE_CLOSED), BROW_HIGH, [], MOUTH_NORMAL),
    _make_frame(EYE_LEFT_LOOK, _mirror(EYE_CLOSED), BROW_HIGH, [], MOUTH_NORMAL),
    _make_frame(EYE_RIGHT_LOOK, _mirror(EYE_CLOSED), BROW_HIGH, [], MOUTH_NORMAL),
    _make_frame(EYE_RIGHT_LOOK, _mirror(EYE_CLOSED), BROW_HIGH, [], MOUTH_NORMAL),
    _make_frame(EYE_OPEN, _mirror(EYE_CLOSED), BROW_HIGH, [], MOUTH_NORMAL),
    _make_frame(EYE_HALF, _mirror(EYE_CLOSED), BROW_LOWERED, [], MOUTH_NORMAL),
    _SLEEP_FACE,
  ],
  frame_duration=0.3,
  mode=AnimationMode.ONCE_FORWARD,
)

# the tiny body rolls across the screen
ROLL = Animation(frames=[_place(_TINY_BODY, (_TINY_ROW, c)) for c in _glide(-3, GRID_COLS)], frame_duration=_GLIDE_FRAME, mode=AnimationMode.ONCE_FORWARD)
ROLL_BACK = Animation(frames=ROLL.frames[::-1], frame_duration=_GLIDE_FRAME, mode=AnimationMode.ONCE_FORWARD)

# a comma hops across in arcs, one hop every two dots, with the tiny body rolling after it
CHASE = Animation(
  frames=[_place(_TINY_COMMA, (_TINY_ROW + _TINY_SCALE * (1 - abs(math.sin(math.pi * c / 2))), c + 6)) + _place(_TINY_BODY, (_TINY_ROW, c))
          for c in _glide(-9, GRID_COLS)],
  frame_duration=_GLIDE_FRAME,
  mode=AnimationMode.ONCE_FORWARD,
)

# waking up into drive mode: a big yawn, ending on the normal face
YAWN = Animation(
  frames=[
    _SLEEP_FACE,
    _make_frame(EYE_CLOSED, _mirror(EYE_CLOSED), BROW_HIGH, _mirror(BROW_HIGH), MOUTH_OPEN),
    _make_frame(EYE_CLOSED, _mirror(EYE_CLOSED), BROW_HIGH, _mirror(BROW_HIGH), MOUTH_OPEN),
    _make_frame(EYE_CLOSED, _mirror(EYE_CLOSED), BROW_HIGH, _mirror(BROW_HIGH), MOUTH_OPEN),
    _make_frame(EYE_HALF, _mirror(EYE_HALF), BROW_LOWERED, _mirror(BROW_LOWERED), MOUTH_NORMAL),
    _make_frame(EYE_OPEN, _mirror(EYE_OPEN), BROW_HIGH, _mirror(BROW_HIGH), MOUTH_SMILE),
  ],
  frame_duration=0.3,
  mode=AnimationMode.ONCE_FORWARD,
)

# played in this order, one at a time, while the body sleeps
OFFROAD_SCENES = [ROLL, PEEK, CHASE, SNORE, ROLL_BACK, PEEK]
# the smooth face keeps only the tiny body's scenes. None stands for the chase drawn in the smooth style
# (smooth_face.chase_scene), which follows the dot chase
SMOOTH_FACE_SCENES = [CHASE, None, ROLL, CHASE, None, ROLL_BACK]


def duration(animation: Animation) -> float:
  """Seconds one forward pass of an animation takes."""
  return len(animation.frames) * animation.frame_duration


# --- Reactions ---
# short expressions the face plays in drive mode in response to something, then returns to normal

EYE_HAPPY = [
        (4, 2), (4, 3),
(5, 1),                 (5, 4),
]
_REACTION_FRAME = 0.05


def _eased(n: int) -> list[float]:
  """0 -> 1 -> 0 over n steps, easing in and out."""
  return [math.sin(math.pi * i / (n - 1)) ** 2 for i in range(n)]


# tapped, or just plugged in: smiling eyes and two little bounces
HAPPY = Animation(
  # the brows sit on the top row, so only the eyes and mouth bounce
  frames=[BROW_HIGH + _mirror(BROW_HIGH) + _shift(EYE_HAPPY + _mirror(EYE_HAPPY) + MOUTH_SMILE, (-0.35 * abs(math.sin(2 * math.pi * i / 32)), 0))
          for i in range(33)],
  frame_duration=_REACTION_FRAME,
  mode=AnimationMode.ONCE_FORWARD,
)

# charger pulled out: eyes go wide and the mouth drops open
SURPRISED = Animation(
  frames=[_sized(EYE_OPEN + _mirror(EYE_OPEN), 1. + 0.22 * k) + BROW_HIGH + _mirror(BROW_HIGH) + _sized(MOUTH_OPEN, 0.7 + 0.3 * k)
          for k in [0., 0.5, 1.] + [1.] * 18 + [0.5, 0.]],
  frame_duration=_REACTION_FRAME,
  mode=AnimationMode.ONCE_FORWARD,
)

# been spinning in place: the eyes keep going round for a moment
_EYE_CENTER = (3.5, 2.5)


def _spin_eye(angle: float, center_col: float) -> list[tuple]:
  return [(_EYE_CENTER[0] + 1.05 * math.sin(angle + q * math.pi / 2), center_col + 1.05 * math.cos(angle + q * math.pi / 2), 0.85) for q in range(4)]


DIZZY = Animation(
  # the spin slows to a stop
  frames=[_spin_eye(a, _EYE_CENTER[1]) + _spin_eye(-a, 15 - _EYE_CENTER[1]) + MOUTH_NORMAL
          for a in [9 * math.pi * (1 - (1 - i / 50) ** 2) for i in range(51)]],
  frame_duration=_REACTION_FRAME,
  mode=AnimationMode.ONCE_FORWARD,
)

# driving fast: brows down, concentrating. looks where it turns, like the normal face
FOCUSED = Animation(
  frames=[_make_frame(EYE_OPEN, _mirror(EYE_OPEN), BROW_LOWERED, _mirror(BROW_LOWERED), MOUTH_NORMAL)],
  left_turn_remove=NORMAL.left_turn_remove,
  right_turn_remove=NORMAL.right_turn_remove,
)

# --- Battery meter ---
# shown above the face while charging; fills left to right
BATTERY_METER = [(0, c) for c in range(4, 12)]
METER_EMPTY = (255, 255, 255, 45)
_METER_SWEEP = 0.09  # seconds per dot when the meter fills in after plugging in


def meter_color(level: float) -> tuple[int, int, int, int]:
  """Red when nearly empty, through amber, to green."""
  stops = [(0.0, (255, 70, 60)), (0.2, (255, 110, 50)), (0.5, (255, 200, 60)), (0.8, (80, 220, 120))]
  for (l0, c0), (l1, c1) in zip(stops, stops[1:], strict=False):
    if level <= l1:
      k = max(0., (level - l0) / (l1 - l0))
      return (*(round(a + (b - a) * k) for a, b in zip(c0, c1, strict=True)), 255)
  return (*stops[-1][1], 255)


def battery_meter(level: float, now: float, plugged_for: float) -> list[tuple[tuple, tuple[int, int, int, int]]]:
  """The meter as (dot, color) pairs: filled dots with a pulse of charge running along them, and the next dot swelling."""
  n = len(BATTERY_METER)
  filled = min(int(level * n), n)
  shown = min(filled, int(plugged_for / _METER_SWEEP))  # fills in one dot at a time just after plugging in
  color = meter_color(level)
  wave = (now * 6) % (n + 5) - 2
  dots = [((r, c, 1. + 0.28 * math.exp(-(i - wave) ** 2 / 1.2)), color) for i, (r, c) in enumerate(BATTERY_METER[:shown])]
  dots += [(d, METER_EMPTY) for d in BATTERY_METER[shown:]]
  if shown == filled and filled < n:
    dots.append(((*BATTERY_METER[filled], 0.35 + 0.65 * (0.5 - 0.5 * math.cos(2 * math.pi * now / 1.6))), color))
  return dots

# --- Face Animator Class ---

class FaceAnimator:
  def __init__(self, animation: Animation):
    self._animation = animation
    self._next: Animation | None = None
    self._start_time = time.monotonic()
    self._rewinding = False
    self._rewind_start: float = 0.0
    self._rewind_from: int = 0
    self._seen_nonzero = False

  def set_animation(self, animation: Animation):
    if animation is not self._animation:
      self._next = animation

  def get_dots(self) -> list[tuple[int, int]]:
    now = time.monotonic()
    elapsed = now - self._start_time

    # Handle rewind for forward-only animations
    if self._rewinding:
      rewind_elapsed = now - self._rewind_start
      frames_back = round(rewind_elapsed / self._animation.frame_duration)
      frame_index = self._rewind_from - frames_back
      if frame_index <= 0:
        if self._next is None:
          self._rewinding = False
          return self._animation.frames[0]
        return self._switch_to_next(now, self._next)
      return self._animation.frames[frame_index]

    # Play starting frames first (once)
    starting = self._animation.starting_frames or []
    starting_duration = len(starting) * self._animation.frame_duration
    if starting and elapsed < starting_duration:
      frame_index = min(int(elapsed / self._animation.frame_duration), len(starting) - 1)
      return starting[frame_index]

    # Main loop
    loop_elapsed = elapsed - starting_duration if starting else elapsed
    frame_index = _get_frame_index(self._animation, loop_elapsed, gap_first=bool(starting))

    if frame_index != 0:
      self._seen_nonzero = True

    if self._next is not None:
      repeats = self._animation.mode in (AnimationMode.REPEAT_FORWARD, AnimationMode.REPEAT_FORWARD_BACKWARD)
      if frame_index == 0 and (len(self._animation.frames) == 1 or self._seen_nonzero or repeats):
        return self._switch_to_next(now, self._next)
      # a play-once animation resting on its last frame is finished: hand over instead of rewinding
      if self._animation.mode == AnimationMode.ONCE_FORWARD and frame_index == len(self._animation.frames) - 1:
        return self._switch_to_next(now, self._next)
      # No natural return to frame 0 — start rewinding
      if self._animation.mode in (AnimationMode.ONCE_FORWARD, AnimationMode.REPEAT_FORWARD):
        self._rewinding = True
        self._rewind_start = now
        self._rewind_from = frame_index

    return self._animation.frames[frame_index]

  def _switch_to_next(self, now: float, animation: Animation) -> list[tuple[int, int]]:
    self._animation = animation
    self._next = None
    self._rewinding = False
    self._seen_nonzero = False
    self._start_time = now
    return self._animation.frames[0]


def _get_frame_index(animation: Animation, elapsed: float, gap_first: bool = False) -> int:
  """Get the current frame index given elapsed time and animation mode."""
  num_frames = len(animation.frames)
  if num_frames == 1:
    return 0

  fd = animation.frame_duration
  has_backward = animation.mode in (AnimationMode.ONCE_FORWARD_BACKWARD, AnimationMode.REPEAT_FORWARD_BACKWARD)
  repeats = animation.mode in (AnimationMode.REPEAT_FORWARD, AnimationMode.REPEAT_FORWARD_BACKWARD)

  forward_duration = num_frames * fd
  backward_frames = max(num_frames - 2, 0) if has_backward else 0
  hold = animation.hold_end if has_backward else 0.0
  cycle_duration = forward_duration + hold + backward_frames * fd

  if not repeats:
    t = min(elapsed, cycle_duration)
  else:
    t = (elapsed + cycle_duration if gap_first else elapsed) % animation.repeat_interval

  # Forward phase
  if t < forward_duration:
    return min(int(t / fd), num_frames - 1)
  t -= forward_duration

  # Hold at last frame
  if t < hold:
    return num_frames - 1
  t -= hold

  # Backward phase
  if backward_frames and t < backward_frames * fd:
    return num_frames - 2 - min(int(t / fd), backward_frames - 1)

  return 0 if has_backward else num_frames - 1
