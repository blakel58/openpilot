#!/usr/bin/env python3
"""
Tell the comma body's smooth face what to show.

Anything on the device can drive the face: a script, a voice assistant, a model
deciding how the body feels. A command is a small JSON message on the
customReservedRawData0 service (cereal's channel reserved for forks):

  {"face": {"expression": "curious", "intensity": 0.8, "look": [0.5, 0.0], "talking": 0.6, "seconds": 3}}

  expression  one of smooth_face.EXPRESSIONS (default "normal")
  intensity   0..1, how far toward that expression to go (default 1)
  look        [x, y], each -1..1, where to look; leave out to let the eyes wander
  talking     0..1, how loud it's speaking right now; leave out when it isn't speaking.
              send a fresh command a few times a second to make the mouth follow the sound
  pose        raw pose numbers (see smooth_face.POSE_DEFAULTS) to use instead of the expression's
  seconds     how long the command holds before the face goes back to its own behavior (default 4, at most 30)

Commands only change what the face shows, and only while the body is awake.

A message can instead carry a guide to show on the screen, for jobs where someone is
standing in front of the body and needs to see what its camera sees (calibrating it):

  {"overlay": {"grid": [4, 3], "done": [[0, 1]], "target": [0, 0], "outline": [[0.4, 0.3], ...], "ok": true, "text": "3/40"}}

  grid      columns and rows the view is divided into
  done      cells [row, col] that are finished (filled in)
  target    the cell to move to next (highlighted)
  outline   a shape to draw, points in 0..1 of the screen (e.g. where the camera sees the board)
  ok        whether the outline is good (green) or not (red)
  text      a line of text along the bottom
The guide shows whether the body is awake or asleep, and goes away a second after the messages stop.

A message can also set the word shown in the microphone badge, so whoever is talking to the body can
see that it heard them and what it is doing about it:

  {"status": {"text": "listening", "seconds": 3}}

  text      a word or two (at most 16 characters); "" goes back to the plain badge
  seconds   how long to show it (default 3, at most 15)

From a shell on the device:
  python -m openpilot.selfdrive.ui.body.face_command surprised --seconds 2
  python -m openpilot.selfdrive.ui.body.face_command happy --look 0.5 0 --talking 0.7
"""
import argparse
import json
import time
from dataclasses import dataclass, field

from openpilot.selfdrive.ui.body.smooth_face import EXPRESSIONS, POSE_DEFAULTS

SERVICE = "customReservedRawData0"
DEFAULT_SECONDS = 4.0
MAX_SECONDS = 30.0
MAX_BYTES = 2048


@dataclass
class FaceCommand:
  expression: str = "normal"
  intensity: float = 1.
  look: tuple[float, float] | None = None
  talking: float | None = None
  pose: dict[str, float] = field(default_factory=dict)
  seconds: float = DEFAULT_SECONDS

  def to_bytes(self) -> bytes:
    face: dict = {"expression": self.expression, "intensity": self.intensity, "seconds": self.seconds}
    if self.look is not None:
      face["look"] = list(self.look)
    if self.talking is not None:
      face["talking"] = self.talking
    if self.pose:
      face["pose"] = self.pose
    return json.dumps({"face": face}).encode()


@dataclass
class Overlay:
  grid: tuple[int, int] = (1, 1)
  done: list[tuple[int, int]] = field(default_factory=list)
  target: tuple[int, int] | None = None
  outline: list[tuple[float, float]] = field(default_factory=list)
  ok: bool = True
  text: str = ""

  def to_bytes(self) -> bytes:
    overlay: dict = {"grid": list(self.grid), "done": [list(c) for c in self.done], "ok": self.ok, "text": self.text,
                     "outline": [list(pt) for pt in self.outline]}
    if self.target is not None:
      overlay["target"] = list(self.target)
    return json.dumps({"overlay": overlay}).encode()


OVERLAY_SECONDS = 1.0
MAX_GRID = 8
MAX_OUTLINE_POINTS = 64


def _cell(value, cols: int, rows: int) -> tuple[int, int] | None:
  if not isinstance(value, list) or len(value) != 2 or not all(isinstance(v, int) and not isinstance(v, bool) for v in value):
    return None
  return (value[0], value[1]) if 0 <= value[0] < rows and 0 <= value[1] < cols else None


def parse_overlay(data: bytes) -> Overlay | None:
  """Read a screen guide, ignoring anything malformed."""
  if len(data) > MAX_BYTES:
    return None
  try:
    raw = json.loads(data).get("overlay")
  except (ValueError, AttributeError):
    return None
  if not isinstance(raw, dict):
    return None
  overlay = Overlay()
  grid = raw.get("grid")
  if isinstance(grid, list) and len(grid) == 2 and all(isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= MAX_GRID for v in grid):
    overlay.grid = (grid[0], grid[1])
  cols, rows = overlay.grid
  if isinstance(raw.get("done"), list):
    overlay.done = [c for c in (_cell(v, cols, rows) for v in raw["done"][:MAX_GRID * MAX_GRID]) if c is not None]
  overlay.target = _cell(raw.get("target"), cols, rows)
  if isinstance(raw.get("outline"), list):
    for pt in raw["outline"][:MAX_OUTLINE_POINTS]:
      if isinstance(pt, list) and len(pt) == 2:
        x, y = _number(pt[0], 0., 1.), _number(pt[1], 0., 1.)
        if x is not None and y is not None:
          overlay.outline.append((x, y))
  overlay.ok = raw.get("ok") is not False
  if isinstance(raw.get("text"), str):
    overlay.text = raw["text"][:60]
  return overlay


STATUS_SECONDS = 3.0
MAX_STATUS_SECONDS = 15.0
MAX_STATUS_CHARS = 16


def parse_status(data: bytes) -> tuple[str, float] | None:
  """Read a status word for the microphone badge: (text, seconds). Ignores anything malformed."""
  if len(data) > MAX_BYTES:
    return None
  try:
    raw = json.loads(data).get("status")
  except (ValueError, AttributeError):
    return None
  if not isinstance(raw, dict) or not isinstance(raw.get("text"), str):
    return None
  text = "".join(c for c in raw["text"] if c.isalnum() or c in " .!?'")[:MAX_STATUS_CHARS]
  return text, _number(raw.get("seconds", STATUS_SECONDS), 0.1, MAX_STATUS_SECONDS) or STATUS_SECONDS


def _number(value, lo: float, hi: float) -> float | None:
  if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
    return None
  return max(lo, min(hi, float(value)))


def parse(data: bytes) -> FaceCommand | None:
  """Read a command, ignoring anything malformed. Unknown expressions and out-of-range numbers never reach the face."""
  if len(data) > MAX_BYTES:
    return None
  try:
    face = json.loads(data).get("face")
  except (ValueError, AttributeError):
    return None
  if not isinstance(face, dict):
    return None

  cmd = FaceCommand()
  if face.get("expression") in EXPRESSIONS and face["expression"] != "asleep":
    cmd.expression = face["expression"]
  intensity = _number(face.get("intensity", 1.), 0., 1.)
  cmd.intensity = 1. if intensity is None else intensity
  cmd.seconds = _number(face.get("seconds", DEFAULT_SECONDS), 0.1, MAX_SECONDS) or DEFAULT_SECONDS
  cmd.talking = _number(face.get("talking"), 0., 1.)
  look = face.get("look")
  if isinstance(look, list) and len(look) == 2:
    x, y = _number(look[0], -1., 1.), _number(look[1], -1., 1.)
    if x is not None and y is not None:
      cmd.look = (x, y)
  pose = face.get("pose")
  if isinstance(pose, dict):
    for name, value in pose.items():
      number = _number(value, -2., 2.)  # smooth_face clamps each one to its own range
      if name in POSE_DEFAULTS and number is not None:
        cmd.pose[name] = number
  return cmd


def to_message(cmd: FaceCommand):
  """A cereal message carrying the command, ready for PubMaster.send(SERVICE, ...)."""
  import openpilot.cereal.messaging as messaging
  data = cmd.to_bytes()
  msg = messaging.new_message(SERVICE, len(data))  # a raw-bytes field has to be sized up front
  msg.customReservedRawData0 = data
  return msg


def relay_stdin() -> None:
  """Publish each line read from standard input as a message, until it closes.

  Lets another computer drive the face or the guide through an SSH connection:
    ssh comma@body "... python -m openpilot.selfdrive.ui.body.face_command --stdin"
  Only well-formed commands and guides are passed on.
  """
  import sys
  import openpilot.cereal.messaging as messaging
  pm = messaging.PubMaster([SERVICE])
  for line in sys.stdin.buffer:
    data = line.strip()
    if parse(data) is None and parse_overlay(data) is None and parse_status(data) is None:
      continue
    msg = messaging.new_message(SERVICE, len(data))
    msg.customReservedRawData0 = data
    pm.send(SERVICE, msg)


def send(cmd: FaceCommand, repeat_for: float = 0.) -> None:
  """Publish a command. With repeat_for, keep sending it for that many seconds (a new publisher needs a moment to be heard)."""
  import openpilot.cereal.messaging as messaging
  pm = messaging.PubMaster([SERVICE])
  end = time.monotonic() + max(repeat_for, 0.5)
  while time.monotonic() < end:
    pm.send(SERVICE, to_message(cmd))
    time.sleep(0.1)


def main():
  parser = argparse.ArgumentParser(description="Show an expression on the comma body's smooth face")
  parser.add_argument("expression", nargs="?", choices=sorted(e for e in EXPRESSIONS if e != "asleep"))
  parser.add_argument("--stdin", action="store_true", help="relay commands read from standard input, one JSON message per line")
  parser.add_argument("--intensity", type=float, default=1.)
  parser.add_argument("--look", type=float, nargs=2, metavar=("X", "Y"))
  parser.add_argument("--talking", type=float)
  parser.add_argument("--seconds", type=float, default=DEFAULT_SECONDS)
  args = parser.parse_args()
  if args.stdin:
    relay_stdin()
    return
  if args.expression is None:
    parser.error("give an expression, or --stdin")
  send(FaceCommand(args.expression, args.intensity, tuple(args.look) if args.look else None, args.talking, seconds=args.seconds))


if __name__ == "__main__":
  main()
