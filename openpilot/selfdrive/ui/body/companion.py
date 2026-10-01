"""
What the comma body tells a companion computer on the network (the one doing its hearing, speech
and following): its settings, and buttons pressed on its screen.

Small JSON messages on the customReservedRawData2 service, which the teleop data channel passes on:

  {"body": {"name": "Roberto"}}                  the settings, repeated every couple of seconds
  {"body": {"event": "enroll", "id": 1790000}}   a button was pressed; repeated briefly, same id each time
  {"body": {"event": "touch", "id": 1790003}}    the screen was touched (so it can hold still while someone uses it)

Nothing here leaves the device by itself: a computer has to be connected over teleop to receive it.
"""
import json
import time

from openpilot.cereal import messaging
from openpilot.common.params import Params

SERVICE = "customReservedRawData2"
SETTINGS_INTERVAL = 2.0   # seconds between repeats of the settings
EVENT_REPEATS = 5         # an event is sent this many times, in case the first ones are missed
EVENT_INTERVAL = 0.3
MAX_NAME_LENGTH = 16
DEFAULT_NAME = "Roberto"


def clean_name(name: str | None) -> str:
  """A name the speech recognizer has a chance with: letters and spaces only, not too long."""
  name = "".join(c for c in (name or "") if c.isalpha() or c == " ").strip()
  return " ".join(name.split())[:MAX_NAME_LENGTH].strip() or DEFAULT_NAME


def settings_message(name: str) -> bytes:
  return json.dumps({"body": {"name": name}}).encode()


def event_message(event: str, event_id: int) -> bytes:
  return json.dumps({"body": {"event": event, "id": event_id}}).encode()


class Companion:
  def __init__(self):
    self._params = Params()
    self._pm: messaging.PubMaster | None = None
    self._last_settings = 0.
    self._last_touch = 0.
    self._events: list[tuple[bytes, int, float]] = []  # message, repeats left, when to send next

  @property
  def name(self) -> str:
    return clean_name(self._params.get("BodyName"))

  def set_name(self, name: str) -> str:
    name = clean_name(name)
    self._params.put("BodyName", name)
    self._last_settings = 0.  # tell the companion right away
    return name

  def touched(self) -> None:
    """The screen was touched: lets the companion hold the body still while someone uses it. At most once a second."""
    now = time.monotonic()
    if now - self._last_touch > 1.:
      self._last_touch = now
      self.send_event("touch")

  def send_event(self, event: str) -> None:
    self._events.append((event_message(event, int(time.time())), EVENT_REPEATS, 0.))  # noqa: TID251

  def _send(self, data: bytes) -> None:
    if self._pm is None:
      self._pm = messaging.PubMaster([SERVICE])
    msg = messaging.new_message(SERVICE, len(data))
    msg.customReservedRawData2 = data
    self._pm.send(SERVICE, msg)

  def update(self) -> None:
    """Call every frame from whatever is on screen; it sends only when something is due."""
    now = time.monotonic()
    if now - self._last_settings > SETTINGS_INTERVAL:
      self._last_settings = now
      self._send(settings_message(self.name))
    pending = []
    for data, repeats, due in self._events:
      if now >= due:
        self._send(data)
        repeats, due = repeats - 1, now + EVENT_INTERVAL
      if repeats > 0:
        pending.append((data, repeats, due))
    self._events = pending


companion = Companion()
