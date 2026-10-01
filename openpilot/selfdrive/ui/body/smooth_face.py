"""
A second face for the comma body, drawn with smooth shapes instead of the dot grid.

Two eyes and nothing else: the expression is all in their shape and how they move.
Everything moves on springs, so it overshoots a little and settles rather than
sliding, and the face keeps itself busy with blinks and glances when nothing is
asking for its attention.

Everything here is plain math: given what the body is doing and how much time has
passed, it works out a short list of shapes to draw. The layout draws them with
raylib; nothing in this file touches the screen, so it can be tested and
previewed anywhere.

All positions and sizes are in units of the screen height, with (0, 0) at the
top left of the face area, so the face scales with the screen.
"""
import math
import random
from dataclasses import dataclass, field

WHITE = (255, 255, 255, 255)
BLACK = (0, 0, 0, 255)
TRACK = (255, 255, 255, 36)
DIM_TEXT = (255, 255, 255, 150)

EYE_WIDTH = 0.28
EYE_HEIGHT = 0.41
EYE_RADIUS = 0.095
EYE_SPACING = 0.32      # distance of each eye's center from the middle of the face
EYE_Y = 0.47
LOOK_X = 0.15           # how far the eyes travel when looking fully left/right
LOOK_Y = 0.09
LOOK_GROW = 0.10        # the eye on the side it's looking toward grows a little, like it's nearer
CHEEK_RADIUS = 0.34     # the cheek that pushes up into a happy eye
CLOSED_BELOW = 0.14     # below this openness an eye is drawn as a closed, curved line
LID_THICKNESS = 0.03

BLINK_TIME = 0.15       # seconds
BLINK_GAP = (2.2, 6.0)  # seconds between blinks
DOUBLE_BLINK_CHANCE = 0.25
GLANCE_GAP = (2.5, 6.0)  # seconds between idle glances
GLANCE_HOLD = (0.5, 1.3)
BREATH_PERIOD = 4.2     # seconds per breath while asleep
HAPPY_HOPS = 2
HAPPY_HOP_TIME = 0.36   # seconds per hop
CHARGING_EYE_LIFT = 0.19  # the eyes move up this far while the charging readout is showing
MOUTH_Y = 0.86


@dataclass
class Spring:
  """A value that chases a target and overshoots a little, like it's on a spring."""
  value: float = 0.
  velocity: float = 0.
  stiffness: float = 170.
  damping: float = 17.

  def step(self, target: float, dt: float) -> float:
    # small fixed steps keep it stable when a frame takes long
    steps = max(1, math.ceil(dt / 0.008))
    h = dt / steps
    for _ in range(steps):
      self.velocity += (self.stiffness * (target - self.value) - self.damping * self.velocity) * h
      self.value += self.velocity * h
    return self.value


# --- poses and expressions ---
# A pose is a handful of numbers that fully describe the eyes. Every expression is just a
# pose, the face springs from one to the next, and anything (a script, a model) can ask for
# a named expression, a blend toward one, or its own numbers.
POSE_DEFAULTS = {
  "open": 1.0,    # 0 shut .. 1 open (a little over 1 for wide-eyed)
  "cheek": 0.0,   # 0..1: cheeks push up from below and arch the eyes (smiling)
  "lid": 0.0,     # 0..1: how far the upper lids come down
  "slant": 0.0,   # -1..1: lids slanted; + lowers the inner ends (determined), - lowers the outer ends (sad)
  "size": 1.0,    # overall eye size
  "round": 0.0,   # 0..1: 0 = rounded rectangles, 1 = as round as they go
  "skew": 0.0,    # -1..1: one eye bigger and more open than the other (+ = the right one), a quizzical look
}
POSE_LIMITS = {"open": (0., 1.15), "cheek": (0., 1.), "lid": (0., 0.9), "slant": (-1., 1.), "size": (0.7, 1.25), "round": (0., 1.), "skew": (-1., 1.)}

EXPRESSIONS: dict[str, dict[str, float]] = {
  "normal": {},
  "asleep": {"open": 0.},
  "happy": {"cheek": 1.},
  "surprised": {"open": 1.12, "size": 1.12, "round": 1.},
  "sad": {"lid": 0.3, "slant": -0.85, "size": 0.95},
  "determined": {"lid": 0.22, "slant": 0.85},
  "sleepy": {"lid": 0.52, "open": 0.9},
  "curious": {"skew": 0.8, "size": 1.03},
}


def pose_for(expression: str, intensity: float = 1., overrides: dict[str, float] | None = None) -> dict[str, float]:
  """The pose for an expression, blended from the normal face by intensity (0..1), with any numbers overridden."""
  preset = EXPRESSIONS.get(expression, {})
  k = max(0., min(1., intensity))
  pose = {name: default + (preset.get(name, default) - default) * k for name, default in POSE_DEFAULTS.items()}
  for name, value in (overrides or {}).items():
    if name in pose and isinstance(value, (int, float)):
      pose[name] = float(value)
  return {name: max(POSE_LIMITS[name][0], min(POSE_LIMITS[name][1], value)) for name, value in pose.items()}


def _pose_springs() -> dict[str, Spring]:
  springs = {name: Spring(default, stiffness=150., damping=15.) for name, default in POSE_DEFAULTS.items()}
  springs["open"] = Spring(0., stiffness=210., damping=17.)  # starts asleep; opening is the snappiest move
  return springs


@dataclass
class SmoothFace:
  aspect: float = 2.0   # face area width / height
  time: float = 0.
  expression: str = "asleep"
  pose: dict[str, Spring] = field(default_factory=_pose_springs)
  look_x: Spring = field(default_factory=lambda: Spring(0., stiffness=230., damping=20.))
  look_y: Spring = field(default_factory=lambda: Spring(0., stiffness=230., damping=20.))
  lift: Spring = field(default_factory=lambda: Spring(0., stiffness=90., damping=15.))
  talk: Spring = field(default_factory=lambda: Spring(0., stiffness=260., damping=24.))
  mouth: Spring = field(default_factory=lambda: Spring(0., stiffness=120., damping=18.))
  _rng: random.Random = field(default_factory=random.Random)
  _blinks: list[float] = field(default_factory=list)       # start times of blinks under way
  _next_blink: float = 2.5
  _glance: tuple[float, float] = (0., 0.)
  _glance_until: float = 0.
  _next_glance: float = 3.0
  _script: list[tuple[float, tuple[float, float]]] = field(default_factory=list)  # (until, look) steps, e.g. waking up
  _happy_start: float = -10.

  def update(self, dt: float, expression: str, look: tuple[float, float] | None = None, speed: float = 0., charging: bool = False,
             talking: float | None = None, intensity: float = 1., overrides: dict[str, float] | None = None) -> None:
    """Advance the face.

    expression: a name from EXPRESSIONS
    look:       where something worth looking at is (each axis -1..1), or None to let the eyes wander on their own
    speed:      0..1, how fast the body is driving; it narrows its eyes a little to concentrate
    charging:   asleep on the charger: the eyes move up to make room for the charging readout
    talking:    None when it isn't speaking; otherwise 0..1, how loud right now. A small mouth appears and moves with it
    intensity:  0..1, how far toward the expression to go
    overrides:  pose numbers to use instead of the expression's
    """
    self.time += dt
    now = self.time
    if expression != self.expression:
      if self.expression == "asleep":
        # waking up: look one way, then the other, with a couple of blinks
        self._script = [(now + 0.45, (0., 0.)), (now + 0.85, (-0.75, 0.1)), (now + 1.3, (0.75, 0.1)), (now + 1.5, (0., 0.))]
        self._blinks = [now + 1.5, now + 1.78]
        self._next_blink = now + 3.5
        self._next_glance = now + 4.0
      if expression == "happy":
        self._happy_start = now
      self.expression = expression

    awake = expression != "asleep"
    target_pose = pose_for(expression, intensity, overrides)
    if awake:
      target_pose["open"] *= 1. - 0.22 * max(0., min(1., speed))
    for name, spring in self.pose.items():
      spring.step(target_pose[name], dt)
    self.lift.step(CHARGING_EYE_LIFT if (charging and not awake) else 0., dt)
    self.mouth.step(1. if (talking is not None and awake) else 0., dt)
    self.talk.step(max(0., min(1., talking or 0.)), dt)

    # where to look: a scripted move, then whatever was asked for, then an idle glance
    self._script = [s for s in self._script if s[0] > now]
    if not awake:
      target = (0., 0.)
    elif self._script:
      target = self._script[0][1]
    elif look is not None:
      target = (max(-1., min(1., look[0])), max(-1., min(1., look[1])))
      self._next_glance = now + self._rng.uniform(*GLANCE_GAP)
    else:
      if now >= self._next_glance:
        self._glance = (self._rng.uniform(-0.8, 0.8), self._rng.uniform(-0.45, 0.35))
        self._glance_until = now + self._rng.uniform(*GLANCE_HOLD)
        self._next_glance = self._glance_until + self._rng.uniform(*GLANCE_GAP)
      target = self._glance if now < self._glance_until else (0., 0.)
    self.look_x.step(target[0], dt)
    self.look_y.step(target[1], dt)

    self._blinks = [b for b in self._blinks if now < b + BLINK_TIME]
    if awake and expression != "happy" and now >= self._next_blink:
      self._blinks.append(now)
      if self._rng.random() < DOUBLE_BLINK_CHANCE:
        self._blinks.append(now + BLINK_TIME * 1.7)
      self._next_blink = now + self._rng.uniform(*BLINK_GAP)

  def _blink(self) -> float:
    """1 = as open as it would otherwise be, 0 = shut."""
    amount = 1.
    for start in self._blinks:
      t = (self.time - start) / BLINK_TIME
      if 0 <= t <= 1:
        amount = min(amount, abs(2 * t - 1))
    return amount

  def _hop(self) -> float:
    """How far up the happy hops have the eyes right now (0..1)."""
    t = (self.time - self._happy_start) / HAPPY_HOP_TIME
    return abs(math.sin(math.pi * t)) * (1 - 0.35 * int(t)) if 0 <= t < HAPPY_HOPS else 0.

  def shapes(self) -> list[tuple]:
    """The face as a list of shapes, in drawing order:
         ("rrect", center_x, center_y, width, height, corner_radius, color)
         ("circle", center_x, center_y, radius, color)
         ("stroke", [(x, y), ...], thickness, color)
         ("poly", [(x, y), (x, y), (x, y), (x, y)], color)
    """
    cx = self.aspect / 2
    asleep = self.expression == "asleep"
    p = {name: spring.value for name, spring in self.pose.items()}
    blink = self._blink()
    cheek = max(0., min(1.2, p["cheek"]))
    hop = self._hop()
    breath = math.sin(2 * math.pi * self.time / BREATH_PERIOD) if asleep else 0.
    mouth = max(0., min(1., self.mouth.value))

    shapes: list[tuple] = []
    for side in (-1, 1):
      # the eye it's looking toward is a touch bigger, and the pair slides that way
      grow = max(0.5, p["size"]) * (1. + LOOK_GROW * self.look_x.value * side) * (1. + 0.16 * p["skew"] * side)
      openness = max(0., p["open"]) * blink * (1. + 0.10 * p["skew"] * side)
      x = cx + side * EYE_SPACING + self.look_x.value * LOOK_X
      # the eyes shift up a little to make room when the mouth is showing
      y = EYE_Y + self.look_y.value * LOOK_Y - 0.05 * hop - 0.012 * cheek + 0.012 * breath - self.lift.value - 0.05 * mouth

      if openness < CLOSED_BELOW:
        # shut: a gentle downward curve, like a closed eyelid
        w = EYE_WIDTH * 0.92 * (1. + 0.03 * breath)
        sag = 0.035 * (1. - openness / CLOSED_BELOW) + 0.004
        points = [(x + u * w / 2, y + sag * (1 - u * u)) for u in (i / 8 * 2 - 1 for i in range(9))]
        shapes.append(("stroke", points, LID_THICKNESS, WHITE))
        continue

      # squash and stretch: a closing eye gets wider, a hopping eye gets taller
      squash = 1. - min(1., openness)
      w = EYE_WIDTH * grow * (1. + 0.16 * squash + 0.06 * cheek - 0.05 * hop)
      h = EYE_HEIGHT * grow * min(1.15, openness) * (1. + 0.08 * hop)
      roundness = max(0., min(1., p["round"]))
      radius = min(EYE_RADIUS * grow + roundness * w / 2, h / 2, w / 2)
      shapes.append(("rrect", x, y, w, h, radius, WHITE))

      # upper lid: a dark shape over the top of the eye, level or slanted
      inner = max(0., p["lid"] + 0.3 * p["slant"])
      outer = max(0., p["lid"] - 0.3 * p["slant"])
      if inner > 0.01 or outer > 0.01:
        top, margin = y - h / 2 - 0.02, 0.02
        # the inner end is the one nearer the middle of the face
        left, right = (outer, inner) if side < 0 else (inner, outer)
        shapes.append(("poly", [(x - w / 2 - margin, top), (x + w / 2 + margin, top),
                                (x + w / 2 + margin, y - h / 2 + right * h), (x - w / 2 - margin, y - h / 2 + left * h)], BLACK))
      if cheek > 0.02:
        # a cheek pushes up from below and turns the eye into an arch
        cover = 0.58 * min(1., cheek) * h
        shapes.append(("circle", x, y + h / 2 - cover + CHEEK_RADIUS, CHEEK_RADIUS, BLACK))

    if mouth > 0.03:
      # only while it's speaking: a small mouth that opens with the sound
      level = max(0., min(1., self.talk.value))
      mw = (0.15 + 0.03 * level) * mouth
      mh = (0.028 + 0.11 * level) * mouth
      shapes.append(("rrect", cx + self.look_x.value * LOOK_X * 0.6, MOUTH_Y, mw, mh, min(mw, mh) / 2, WHITE))
    return shapes


# --- charging ---

@dataclass
class ChargeEstimator:
  """Works out how long until the battery is full from how fast its level is rising.

  The body reports whole percents, so the rate comes from the times at which the
  level ticks up. The first reading after plugging in is ignored for timing: the
  level jumps when the charger connects, and how long it had been at that value is unknown.
  """
  MIN_SPAN = 90.  # seconds of rise to watch before trusting the rate

  _ticks: list[tuple[float, float]] = field(default_factory=list)  # (time, level) each time the level went up
  _last_level: float | None = None

  def update(self, now: float, level: float, charging: bool) -> float | None:
    """Returns seconds until full, or None while it doesn't know yet."""
    if not charging:
      self._ticks, self._last_level = [], None
      return None
    if self._last_level is None:
      self._last_level = level
    elif level > self._last_level + 1e-6:
      self._ticks.append((now, level))
      self._last_level = level
    elif level < self._last_level - 0.03:
      # the level dropped (it's being used hard while plugged in); start over
      self._ticks, self._last_level = [], level
    if level >= 0.995:
      return 0.
    if len(self._ticks) < 2 or self._ticks[-1][0] - self._ticks[0][0] < self.MIN_SPAN:
      return None
    rate = (self._ticks[-1][1] - self._ticks[0][1]) / (self._ticks[-1][0] - self._ticks[0][0])
    return (1. - level) / rate if rate > 0 else None


def asleep_hint(aspect: float) -> list[tuple]:
  """What to do to wake it, under the sleeping eyes."""
  return [("text", aspect / 2, 0.86, 0.062, "switch to drive mode to use", DIM_TEXT, False)]


def format_eta_short(seconds: float | None) -> str:
  """For the dot face's one-line label: "" until it knows."""
  if seconds is None:
    return ""
  return "full" if seconds <= 0 else format_eta(seconds).removeprefix("about ").replace(" to full", " left")


def format_eta(seconds: float | None) -> str:
  if seconds is None:
    return "working out time left"
  if seconds <= 0:
    return "fully charged"
  minutes = max(1, round(seconds / 60 / 5) * 5) if seconds > 600 else max(1, round(seconds / 60))
  if minutes >= 60:
    hours, rest = divmod(minutes, 60)
    return f"about {hours} h {rest} min to full" if rest else f"about {hours} h to full"
  return f"about {minutes} min to full"


def charge_panel(aspect: float, level: float, color: tuple[int, int, int, int], eta: float | None, now: float, plugged_for: float) -> list[tuple]:
  """The charging readout under the sleeping eyes: a big percentage, a bar, and the time left.

  Adds one more shape kind:
    ("text", center_x, center_y, height, string, color, bold)
  """
  level = max(0., min(1., level))
  cx = aspect / 2
  width, height, y = 0.86, 0.05, 0.80
  x0 = cx - width / 2
  # the bar fills up from empty when the charger goes in
  shown = level * min(1., plugged_for / 0.9) ** 0.5
  shapes: list[tuple] = [
    ("text", cx, 0.655, 0.17, f"{round(level * 100)}%", WHITE, True),
    ("rrect", cx, y, width, height, height / 2, TRACK),
  ]
  if shown > 0.01:
    filled = max(height, width * shown)
    shapes.append(("rrect", x0 + filled / 2, y, filled, height, height / 2, color))
    # a highlight sweeps along the filled part, like charge flowing in
    sweep = (now % 2.2) / 2.2
    glint_w = 0.11
    gx = x0 + sweep * (filled + glint_w) - glint_w / 2
    left, right = max(x0, gx - glint_w / 2), min(x0 + filled, gx + glint_w / 2)
    if right - left > height:
      shapes.append(("rrect", (left + right) / 2, y, right - left, height, height / 2, (255, 255, 255, 70)))
  shapes.append(("text", cx, 0.905, 0.058, format_eta(eta), DIM_TEXT, False))
  return shapes



def charge_strip(aspect: float, level: float, color: tuple[int, int, int, int]) -> list[tuple]:
  """A small bar and percentage along the bottom, for when it's awake and on the charger."""
  level = max(0., min(1., level))
  cx = aspect / 2
  width, height, y = 0.5, 0.03, 0.93
  x0 = cx - width / 2
  shapes: list[tuple] = [("rrect", cx, y, width, height, height / 2, TRACK)]
  if level > 0.02:
    filled = max(height, width * level)
    shapes.append(("rrect", x0 + filled / 2, y, filled, height, height / 2, color))
  shapes.append(("text", cx + width / 2 + 0.09, y, 0.055, f"{round(level * 100)}%", DIM_TEXT, False))
  return shapes


# --- showing that it heard you ---
# Drawn under the eyes, in the middle of the face, where whoever is talking to it is already looking.

SIGNAL_Y = 0.85
LISTENING_BARS = 5


def listening_shapes(aspect: float, t: float, y: float = SIGNAL_Y) -> list[tuple]:
  """A row of bars rippling like a sound level: it is hearing something."""
  shapes: list[tuple] = []
  width, gap = 0.045, 0.082
  for i in range(LISTENING_BARS):
    # each bar swells in turn, the middle ones tallest
    swell = 0.5 + 0.5 * math.sin(t * 7.5 - i * 1.1)
    height = width + (0.07 + 0.08 * (1. - abs(i - (LISTENING_BARS - 1) / 2) / LISTENING_BARS * 2)) * swell
    shapes.append(("rrect", aspect / 2 + (i - (LISTENING_BARS - 1) / 2) * gap, y, width, height, width / 2, WHITE))
  return shapes


def thinking_shapes(aspect: float, t: float, y: float = SIGNAL_Y) -> list[tuple]:
  """Three dots hopping one after another: it is working out what was said."""
  shapes: list[tuple] = []
  for i in range(3):
    phase = (t * 1.6 - i * 0.18) % 1.
    hop = math.sin(math.pi * phase / 0.45) if phase < 0.45 else 0.
    shapes.append(("circle", aspect / 2 + (i - 1) * 0.12, y - 0.06 * hop, 0.034, WHITE))
  return shapes


THINKING_LOOK = (0., -0.6)   # where the eyes rest while it thinks: lifted a little, and still


# --- the chase, in the smooth style ---
# A comma hops across the screen with a tiny comma body rolling after it. Halfway, the comma
# stops for a breather, the body nearly catches it, and the comma bolts.

CHASE_SECONDS = 6.4
GROUND_Y = 0.88
_S = 1.5                 # overall size of the two characters
_WHEEL_R = 0.042 * _S
_COMMA_HEIGHT = 0.19 * _S


def _ease(t: float) -> float:
  t = max(0., min(1., t))
  return t * t * (3 - 2 * t)


def _comma_x(t: float, aspect: float) -> float:
  rest = 0.6 * aspect
  if t < 2.6:
    return -0.25 + (rest + 0.25) * (t / 2.6)
  if t < 3.5:
    return rest
  return rest + (aspect + 0.6 - rest) * ((t - 3.5) / 1.5) ** 1.6


def _body_x(t: float, aspect: float) -> float:
  caught_up = 0.6 * aspect - 0.42
  if t < 3.6:
    return -0.6 + (caught_up + 0.6) * _ease((t - 0.5) / 3.0)
  return caught_up + (aspect + 0.8 - caught_up) * _ease((t - 3.9) / 2.3) ** 1.3


# the comma is comma's own logo: the image, and where the comma sits inside it (left, top, right, bottom as fractions)
COMMA_IMAGE = "images/spinner_comma.png"
COMMA_IMAGE_BOX = (0.361, 0.261, 0.639, 0.763)
COMMA_ASPECT = (COMMA_IMAGE_BOX[2] - COMMA_IMAGE_BOX[0]) / (COMMA_IMAGE_BOX[3] - COMMA_IMAGE_BOX[1])  # width / height


def comma_shapes(cx: float, bottom: float, height: float, squash: float = 0.) -> list[tuple]:
  """The comma logo, standing on `bottom`, `height` tall. squash flattens it (landing from a hop).

  Adds one more shape kind:
    ("image", asset, (left, top, right, bottom of the part to draw, as fractions), center_x, center_y, width, height)
  """
  h = height * (1. - squash)
  w = height * COMMA_ASPECT * (1. + squash)
  return [("image", COMMA_IMAGE, COMMA_IMAGE_BOX, cx, bottom - h / 2, w, h)]


def chase_scene(aspect: float, t: float) -> list[tuple]:
  """The shapes for the chase, t seconds in."""
  shapes: list[tuple] = [("stroke", [(0.06, GROUND_Y + 0.012), (aspect - 0.06, GROUND_Y + 0.012)], 0.006, (255, 255, 255, 45))]

  # the comma hops along; it squashes as it lands
  cx = _comma_x(t, aspect)
  resting = 2.6 <= t < 3.5
  dashing = t >= 3.5
  hops = cx / (0.46 if dashing else 0.32)
  height = 0. if resting else abs(math.sin(math.pi * hops)) * (0.26 if dashing else 0.17)
  squash = 0.22 * max(0., 1. - height / 0.05) if not resting else 0.05 * math.sin(t * 9)
  shapes += comma_shapes(cx, GROUND_Y - height, _COMMA_HEIGHT, squash)

  # the tiny body: wheels that turn, a neck, and a head with two eyes fixed on the comma
  bx = _body_x(t, aspect)
  startled = 3.5 <= t < 4.0   # the comma just bolted
  speed = _body_x(t + 0.05, aspect) - bx
  lean = min(0.03, speed * 0.9) * _S
  bob = 0.006 * _S * math.sin(bx * 28)
  axle_y = GROUND_Y - _WHEEL_R
  for side in (-1, 1):
    wx = bx + side * 0.068 * _S
    shapes.append(("circle", wx, axle_y, _WHEEL_R, WHITE))
    angle = wx / _WHEEL_R  # rolling without slipping
    shapes.append(("circle", wx + 0.022 * _S * math.cos(angle), axle_y + 0.022 * _S * math.sin(angle), 0.009 * _S, BLACK))
  shapes.append(("stroke", [(bx, axle_y - 0.02 * _S), (bx + lean, axle_y - 0.115 * _S + bob)], 0.022 * _S, WHITE))
  hx, hy = bx + lean * 1.4, axle_y - 0.175 * _S + bob - (0.012 * _S if startled else 0.)
  shapes.append(("rrect", hx, hy, 0.215 * _S, 0.135 * _S, 0.04 * _S, WHITE))
  eye_h = (0.075 if startled else 0.052) * _S
  for side in (-1, 1):
    shapes.append(("rrect", hx + (side * 0.045 + 0.014) * _S, hy, 0.03 * _S, eye_h, 0.014 * _S, BLACK))
  return shapes
