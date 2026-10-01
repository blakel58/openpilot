import math
import random
import time
import pyray as rl

import openpilot.cereal.messaging as messaging

from openpilot.system.ui.lib.application import gui_app, FontWeight, TextAlignment, TextAlignmentVertical
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.widgets.label import UnifiedLabel
from openpilot.selfdrive.ui.ui_state import device, ui_state
from openpilot.selfdrive.ui.body.animations import FaceAnimator, ASLEEP, CONTENT, DIZZY, FOCUSED, HAPPY, INQUISITIVE, LIVE_DOT, NORMAL, \
                                                     OFFROAD_SCENES, SLEEPY, SURPRISED, TIRED, WINK, YAWN, battery_meter, duration

GRID_COLS = 16
GRID_ROWS = 8
DOT_RADIUS = 50 if gui_app.big_ui() else 10

IDLE_TIMEOUT = 30.0        # seconds of no joystick input before playing INQUISITIVE
IDLE_STEER_THRESH = 0.5    # degrees — below this counts as no input
IDLE_SPEED_THRESH = 0.01   # m/s — below this counts as no input
LOW_BATTERY = 0.15         # fuelGauge below this looks tired
TELEOP_TIMEOUT = 1.0       # seconds since the last joystick message before teleop counts as disconnected
WINK_DURATION = 1.5        # seconds the wink plays when someone connects
# offroad the screen only stays on for 30s after a touch, so scenes are timed from when it wakes
SCENE_FIRST_DELAY = 2.0    # seconds after the screen wakes before the first scene plays
SCENE_GAP = (4.0, 8.0)     # seconds of plain sleep between scenes
BODY_DATA_ADDR = 0x203     # BODY_DATA in comma_body.dbc, sent by the body at 1Hz even when offroad
BODY_DATA_TIMEOUT = 5.0    # seconds before the last offroad battery reading is stale
FAST_SPEED = 0.45          # m/s — above this the face concentrates
SPIN_STEER = 0.5           # joystick turn axis beyond this, while barely moving, counts as spinning in place
SPIN_SPEED = 0.15          # m/s
SPIN_TIME = 3.0            # seconds of spinning before it gets dizzy


# This class is used both in BIG (tizi) and small (mici) UIs
class BodyLayout(Widget):
  def __init__(self):
    super().__init__()
    self._animator = FaceAnimator(ASLEEP)
    self._turning_left = False
    self._turning_right = False
    self._last_input_time = time.monotonic()
    self._was_active = False
    self._teleop_connected = False
    self._wink_until = 0.
    self._yawn_until = 0.
    self._scene_index = 0
    self._next_scene_time = 0.
    self._was_awake = False
    self._scene = ASLEEP
    self._scene_until = 0.
    self._reaction = NORMAL
    self._reaction_until = 0.
    self._spin_start: float | None = None
    self._plug_time = 0.
    self._charging = False
    self._battery = 0.
    self._battery_time = 0.
    # offroad, card isn't running to parse the body's CAN into carState, so read BODY_DATA here
    self._can_sock = messaging.sub_sock('can', conflate=False, timeout=0)
    self._offroad_label = UnifiedLabel("switch to drive mode to use", 95 if gui_app.big_ui() else 45, FontWeight.DISPLAY,
                                       alignment=TextAlignment.CENTER,
                                       alignment_vertical=TextAlignmentVertical.MIDDLE)

  def draw_dot_grid(self, rect: rl.Rectangle, dots: list[tuple[int, int]], color: rl.Color):
    spacing = min(rect.height / GRID_ROWS, rect.width / GRID_COLS)

    grid_w = (GRID_COLS - 1) * spacing
    grid_h = (GRID_ROWS - 1) * spacing

    offset_x = rect.x + (rect.width - grid_w) / 2
    offset_y = rect.y + (rect.height - grid_h) / 2

    # a dot is (row, col) or (row, col, size); size scales the radius
    for dot in dots:
      x = int(offset_x + dot[1] * spacing)
      y = int(offset_y + dot[0] * spacing)
      rl.draw_circle(x, y, DOT_RADIUS * (dot[2] if len(dot) > 2 else 1.), color)

  def _update_battery(self, sm):
    now = time.monotonic()
    was_charging = self._charging
    if ui_state.is_onroad() and sm.recv_frame['carState'] > 0:
      self._charging, self._battery, self._battery_time = sm['carState'].charging, sm['carState'].fuelGauge, now
    for dat in messaging.drain_sock_raw(self._can_sock):
      if ui_state.is_onroad():
        continue  # carState has it; don't parse the busy onroad bus
      for c in messaging.log_from_bytes(dat).can:
        if c.address == BODY_DATA_ADDR and len(c.dat) >= 4:
          # byte 3: BATT_PERCENTAGE in the top 7 bits, CHARGER_CONNECTED in the lowest
          self._charging, self._battery, self._battery_time = bool(c.dat[3] & 1), (c.dat[3] >> 1) / 100., now
    if now - self._battery_time > BODY_DATA_TIMEOUT:
      self._charging = False

    if self._charging != was_charging:
      if self._charging:
        self._plug_time = now
      if ui_state.is_onroad():
        self._react(HAPPY if self._charging else SURPRISED)

  def _react(self, animation):
    self._reaction = animation
    self._reaction_until = time.monotonic() + duration(animation)

  def _update_state(self):
    super()._update_state()

    sm = ui_state.sm
    self._update_battery(sm)

    if ui_state.is_onroad():
      if not self._was_active:
        self._last_input_time = time.monotonic()
        self._was_active = True
        self._yawn_until = time.monotonic() + duration(YAWN)  # waking up

      cs = sm['carState']
      has_input = abs(cs.steeringAngleDeg) > IDLE_STEER_THRESH or abs(cs.vEgo) > IDLE_SPEED_THRESH
      if has_input:
        self._last_input_time = time.monotonic()

      # spinning in place for a while makes it dizzy once it stops
      steer_axis = sm['testJoystick'].axes[1] if len(sm['testJoystick'].axes) > 1 else 0
      if abs(steer_axis) > SPIN_STEER and abs(cs.vEgo) < SPIN_SPEED:
        if self._spin_start is None:
          self._spin_start = time.monotonic()
      else:
        if self._spin_start is not None and time.monotonic() - self._spin_start > SPIN_TIME:
          self._react(DIZZY)
        self._spin_start = None

      if time.monotonic() < self._yawn_until:
        self._animator.set_animation(YAWN)
      elif time.monotonic() < self._reaction_until:
        self._animator.set_animation(self._reaction)
      elif time.monotonic() < self._wink_until:
        self._animator.set_animation(WINK)
      elif has_input:
        self._animator.set_animation(FOCUSED if abs(cs.vEgo) > FAST_SPEED else NORMAL)
      elif self._charging:
        self._animator.set_animation(CONTENT)
      elif sm.recv_frame['carState'] > 0 and cs.fuelGauge < LOW_BATTERY:
        self._animator.set_animation(TIRED)
      elif time.monotonic() - self._last_input_time > IDLE_TIMEOUT:
        self._animator.set_animation(INQUISITIVE)
      else:
        self._animator.set_animation(NORMAL)
    else:
      now = time.monotonic()
      if self._was_active or (device.awake and not self._was_awake):
        self._next_scene_time = now + SCENE_FIRST_DELAY
      self._was_active = False
      # now and then, play a short scene, then go back to the sleeping face
      if device.awake and now >= self._next_scene_time:
        self._scene = OFFROAD_SCENES[self._scene_index % len(OFFROAD_SCENES)]
        self._scene_index += 1
        self._scene_until = now + duration(self._scene)
        self._next_scene_time = self._scene_until + random.uniform(*SCENE_GAP)
      # keep asking for the scene until it's done: asking for another animation mid-play rewinds it
      self._animator.set_animation(self._scene if now < self._scene_until else ASLEEP)

    self._was_awake = device.awake

    # someone is connected and driving (comma connect or local teleop both send testJoystick)
    teleop_connected = sm.recv_frame['testJoystick'] > 0 and (time.monotonic() - sm.recv_time['testJoystick']) < TELEOP_TIMEOUT
    if teleop_connected and not self._teleop_connected and ui_state.is_onroad():
      self._wink_until = time.monotonic() + WINK_DURATION
    self._teleop_connected = teleop_connected

    steer = sm['testJoystick'].axes[1] if len(sm['testJoystick'].axes) > 1 else 0
    self._turning_left = steer >= 0.05
    self._turning_right = steer <= -0.05

  # play animation on screen tap
  def _handle_mouse_release(self, mouse_pos):
    super()._handle_mouse_release(mouse_pos)
    if not self._was_active:
      self._animator.set_animation(SLEEPY)
    else:
      self._react(HAPPY)  # a pat on the head

  def _render(self, rect: rl.Rectangle):
    dots = self._animator.get_dots()
    animation = self._animator._animation
    if self._turning_left and animation.left_turn_remove:
      remove_set = set(animation.left_turn_remove)
      dots = [d for d in dots if d not in remove_set]
    elif self._turning_right and animation.right_turn_remove:
      remove_set = set(animation.right_turn_remove)
      dots = [d for d in dots if d not in remove_set]
    self.draw_dot_grid(rect, dots, rl.WHITE)

    # the sleeping face is dimmed behind the text; scenes have the screen to themselves at full brightness
    if ui_state.is_offroad() and animation not in OFFROAD_SCENES:
      rl.draw_rectangle(int(self.rect.x), int(self.rect.y), int(self.rect.width), int(self.rect.height), rl.Color(0, 0, 0, 175))
      upper_half = rl.Rectangle(rect.x, rect.y, rect.width, rect.height / 2)
      self._offroad_label.set_text(f"charging {round(self._battery * 100)}%" if self._charging else "switch to drive mode to use")
      self._offroad_label.render(upper_half)

    # charge meter above the face. drawn after the dimming so it stays bright over the sleeping face
    if self._charging:
      now = time.monotonic()
      for dot, color in battery_meter(self._battery, now, now - self._plug_time):
        self.draw_dot_grid(rect, [dot], rl.Color(*color))

    # pulsing red dot while someone is connected and driving
    if self._teleop_connected:
      pulse = 0.7 + 0.3 * (0.5 - 0.5 * math.cos(2 * math.pi * time.monotonic() / 1.2))
      self.draw_dot_grid(rect, [(*LIVE_DOT, pulse)], rl.Color(255, 60, 50, 255))
