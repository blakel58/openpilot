"""
A second face for the comma body, drawn with smooth shapes instead of the dot grid.

Everything here is plain math: given what the body is doing and how much time
has passed, it works out a short list of shapes to draw. The layout draws them
with raylib; nothing in this file touches the screen, so it can be tested and
previewed anywhere.

All positions and sizes are in units of the screen height, with (0, 0) at the
top left of the face area, so the face scales with the screen.
"""
import math
import random
from dataclasses import dataclass, field

WHITE = (255, 255, 255, 255)
TRACK = (255, 255, 255, 40)

# what each expression looks like: how open the eyes are (1 = fully open), how much
# the mouth smiles (0 = flat line, 1 = big smile), and how far the eyes sit below their usual height
EXPRESSIONS = {
  "normal": {"open": 1.0, "smile": 0.4, "drop": 0.0},
  "happy": {"open": 0.34, "smile": 1.0, "drop": -0.03},
  "asleep": {"open": 0.06, "smile": 0.12, "drop": 0.04},
}

EYE_WIDTH = 0.26
EYE_HEIGHT = 0.34
EYE_SPACING = 0.34      # distance of each eye's center from the middle of the face
EYE_Y = 0.46
LOOK_X = 0.13           # how far the eyes travel when looking fully left/right
LOOK_Y = 0.08
MOUTH_Y = 0.80
MOUTH_HALF_WIDTH = 0.10
MOUTH_DEPTH = 0.075     # how far a full smile curves down
MOUTH_THICKNESS = 0.022

EASE_RATE = 9.0         # 1/s: how quickly the face moves toward a new expression
LOOK_RATE = 7.0
BLINK_TIME = 0.16       # seconds
BLINK_GAP = (2.0, 6.0)  # seconds between blinks
BREATH_PERIOD = 4.0     # seconds per breath while asleep
BOUNCE_TIME = 0.9       # seconds a happy bounce lasts


@dataclass
class SmoothFace:
  aspect: float = 2.0   # face area width / height
  open: float = 0.06
  smile: float = 0.12
  drop: float = 0.04
  look_x: float = 0.
  look_y: float = 0.
  time: float = 0.
  expression: str = "asleep"
  _next_blink: float = 3.0
  _blink_start: float = -1.
  _bounce_start: float = -10.
  _rng: random.Random = field(default_factory=random.Random)

  def update(self, dt: float, expression: str, look: tuple[float, float] = (0., 0.)) -> None:
    """Move the face toward an expression and a look direction (each axis -1..1)."""
    self.time += dt
    if expression == "happy" and self.expression != "happy":
      self._bounce_start = self.time
    self.expression = expression
    target = EXPRESSIONS[expression]
    k = 1. - math.exp(-EASE_RATE * dt)
    self.open += (target["open"] - self.open) * k
    self.smile += (target["smile"] - self.smile) * k
    self.drop += (target["drop"] - self.drop) * k

    # asleep, the eyes rest in the middle
    lx, ly = (0., 0.) if expression == "asleep" else (max(-1., min(1., look[0])), max(-1., min(1., look[1])))
    k = 1. - math.exp(-LOOK_RATE * dt)
    self.look_x += (lx - self.look_x) * k
    self.look_y += (ly - self.look_y) * k

    if expression == "normal" and self.time >= self._next_blink:
      self._blink_start = self.time
      self._next_blink = self.time + self._rng.uniform(*BLINK_GAP)

  def _blink(self) -> float:
    """1 = eyes as open as the expression allows, 0 = shut."""
    t = (self.time - self._blink_start) / BLINK_TIME
    return abs(2 * t - 1) if 0 <= t <= 1 else 1.

  def shapes(self) -> list[tuple]:
    """The face as a list of shapes:
         ("pill", center_x, center_y, width, height, color)
         ("stroke", [(x, y), ...], thickness, color)
    """
    cx = self.aspect / 2
    eye_h = max(0.03, EYE_HEIGHT * self.open * self._blink())
    y = EYE_Y + self.drop + self.look_y * LOOK_Y
    if self.expression == "asleep":
      y += 0.012 * math.sin(2 * math.pi * self.time / BREATH_PERIOD)
    bounce = (self.time - self._bounce_start) / BOUNCE_TIME
    if 0 <= bounce <= 1:
      y -= 0.035 * abs(math.sin(2 * math.pi * bounce))
    dx = self.look_x * LOOK_X

    shapes: list[tuple] = [
      ("pill", cx - EYE_SPACING + dx, y, EYE_WIDTH, eye_h, WHITE),
      ("pill", cx + EYE_SPACING + dx, y, EYE_WIDTH, eye_h, WHITE),
    ]
    # the mouth is a curve through three points; a little of the look carries into it
    mx = cx + dx * 0.6
    depth = MOUTH_DEPTH * self.smile
    points = []
    for i in range(13):
      u = i / 12 * 2 - 1
      points.append((mx + u * MOUTH_HALF_WIDTH, MOUTH_Y + depth * (1 - u * u)))
    shapes.append(("stroke", points, MOUTH_THICKNESS, WHITE))
    return shapes


def charge_bar(aspect: float, level: float, color: tuple[int, int, int, int]) -> list[tuple]:
  """A plain charge bar under the sleeping face: a track and the filled part."""
  width, height, y = 0.9, 0.045, 0.925
  x0 = aspect / 2 - width / 2
  level = max(0., min(1., level))
  shapes = [("pill", aspect / 2, y, width, height, TRACK)]
  if level > 0.02:
    filled = max(height, width * level)
    shapes.append(("pill", x0 + filled / 2, y, filled, height, color))
  return shapes
