import random
import time
import pyray as rl

import openpilot.cereal.messaging as messaging

from openpilot.system.ui.lib.application import gui_app, FontWeight, TextAlignment, TextAlignmentVertical
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.widgets.label import UnifiedLabel
from openpilot.selfdrive.ui.ui_state import device, ui_state
from openpilot.selfdrive.ui.body.animations import FaceAnimator, ASLEEP, BATTERY_METER, CONTENT, INQUISITIVE, LIVE_DOT, NORMAL, \
                                                     OFFROAD_SCENES, SLEEPY, TIRED, WINK, YAWN, duration

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
METER_GREEN = rl.Color(80, 220, 120, 255)
METER_EMPTY = rl.Color(255, 255, 255, 45)


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
    self._charging = False
    self._battery = 0.
    self._battery_time = 0.
    # offroad, card isn't running to parse the body's CAN into carState, so read BODY_DATA here
    self._can_sock = messaging.sub_sock('can', conflate=False, timeout=0)
    self._offroad_label = UnifiedLabel("drive mode to wake", 95 if gui_app.big_ui() else 45, FontWeight.DISPLAY,
                                       alignment=TextAlignment.CENTER,
                                       alignment_vertical=TextAlignmentVertical.MIDDLE)

  def draw_dot_grid(self, rect: rl.Rectangle, dots: list[tuple[int, int]], color: rl.Color):
    spacing = min(rect.height / GRID_ROWS, rect.width / GRID_COLS)

    grid_w = (GRID_COLS - 1) * spacing
    grid_h = (GRID_ROWS - 1) * spacing

    offset_x = rect.x + (rect.width - grid_w) / 2
    offset_y = rect.y + (rect.height - grid_h) / 2

    for row, col in dots:
      x = int(offset_x + col * spacing)
      y = int(offset_y + row * spacing)
      rl.draw_circle(x, y, DOT_RADIUS, color)

  def _update_battery(self, sm):
    now = time.monotonic()
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

      if time.monotonic() < self._yawn_until:
        self._animator.set_animation(YAWN)
      elif time.monotonic() < self._wink_until:
        self._animator.set_animation(WINK)
      elif has_input:
        self._animator.set_animation(NORMAL)
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

    # pulsing red dot while someone is connected and driving
    if self._teleop_connected and time.monotonic() % 1.0 < 0.7:
      self.draw_dot_grid(rect, [LIVE_DOT], rl.Color(255, 60, 50, 255))

    if ui_state.is_offroad():
      # the sleeping face is dimmed behind the text; scenes play at full brightness
      if animation not in OFFROAD_SCENES:
        rl.draw_rectangle(int(self.rect.x), int(self.rect.y), int(self.rect.width), int(self.rect.height), rl.Color(0, 0, 0, 175))
      upper_half = rl.Rectangle(rect.x, rect.y, rect.width, rect.height / 2)
      self._offroad_label.set_text(f"charging {round(self._battery * 100)}%" if self._charging else "drive mode to wake")
      self._offroad_label.render(upper_half)

    # charge meter above the face: filled dots for the battery level, the next one pulses while charging.
    # drawn last so it stays bright over the dimmed offroad face
    if self._charging:
      filled = min(int(self._battery * len(BATTERY_METER)), len(BATTERY_METER))
      self.draw_dot_grid(rect, BATTERY_METER[filled:], METER_EMPTY)
      self.draw_dot_grid(rect, BATTERY_METER[:filled], METER_GREEN)
      if filled < len(BATTERY_METER) and time.monotonic() % 1.2 < 0.6:
        self.draw_dot_grid(rect, [BATTERY_METER[filled]], METER_GREEN)
