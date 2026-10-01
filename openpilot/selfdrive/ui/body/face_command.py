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
  parser.add_argument("expression", choices=sorted(e for e in EXPRESSIONS if e != "asleep"))
  parser.add_argument("--intensity", type=float, default=1.)
  parser.add_argument("--look", type=float, nargs=2, metavar=("X", "Y"))
  parser.add_argument("--talking", type=float)
  parser.add_argument("--seconds", type=float, default=DEFAULT_SECONDS)
  args = parser.parse_args()
  send(FaceCommand(args.expression, args.intensity, tuple(args.look) if args.look else None, args.talking, seconds=args.seconds))


if __name__ == "__main__":
  main()
