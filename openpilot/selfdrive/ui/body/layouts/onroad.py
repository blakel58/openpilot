import math
import random
import time
import pyray as rl

import openpilot.cereal.messaging as messaging

from openpilot.system.ui.lib.application import gui_app, FontWeight, TextAlignment, TextAlignmentVertical
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.widgets.label import UnifiedLabel
from openpilot.selfdrive.ui.ui_state import device, ui_state
from openpilot.selfdrive.ui.body.animations import FaceAnimator, ASLEEP, CONTENT, DIZZY, FOCUSED, HAPPY, INQUISITIVE, NORMAL, \
                                                     OFFROAD_SCENES, SLEEPY, SMOOTH_FACE_SCENES, SURPRISED, TIRED, WINK, YAWN, battery_meter, \
                                                     duration, meter_color
from openpilot.selfdrive.ui.body import face_command
from openpilot.selfdrive.ui.body.companion import companion
from openpilot.selfdrive.ui.body.smooth_face import CHASE_SECONDS, ChargeEstimator, SmoothFace, asleep_hint, charge_panel, charge_strip, \
                                                    SIGNAL_Y, THINKING_LOOK, chase_scene, format_eta_short, listening_shapes, thinking_shapes

GRID_COLS = 16
GRID_ROWS = 8
DOT_RADIUS = 50 if gui_app.big_ui() else 10

IDLE_TIMEOUT = 30.0        # seconds of no joystick input before playing INQUISITIVE
IDLE_STEER_THRESH = 0.5    # degrees — below this counts as no input
IDLE_SPEED_THRESH = 0.01   # m/s — below this counts as no input
LOW_BATTERY = 0.15         # fuelGauge below this looks tired
TELEOP_TIMEOUT = 1.0       # seconds since the last joystick message before teleop counts as disconnected
MIC_TIMEOUT = 1.0          # seconds since the last microphone audio before the mic counts as off
MIC_COLOR = (70, 150, 255, 255)
STATUS_COLOR = (80, 220, 130, 255)   # the microphone badge while the body is doing something with what it heard
BADGE_HEIGHT = 0.1         # of the screen's height
WINK_DURATION = 1.5        # seconds the wink plays when someone connects
# offroad the screen only stays on for 30s after a touch, so scenes are timed from when it wakes
SCENE_FIRST_DELAY = 2.0    # seconds after the screen wakes before the first scene plays
SCENE_GAP = (4.0, 8.0)     # seconds of plain sleep between scenes
BODY_DATA_ADDR = 0x203     # BODY_DATA in comma_body.dbc, sent by the body at 1Hz even when offroad
BODY_DATA_TIMEOUT = 5.0    # seconds before the last offroad battery reading is stale
FAST_SPEED = 0.45          # m/s — above this the face concentrates
FULL_SPEED = 0.8           # m/s at full stick
SPIN_STEER = 0.5           # joystick turn axis beyond this, while barely moving, counts as spinning in place
SPIN_SPEED = 0.15          # m/s
SPIN_TIME = 3.0            # seconds of spinning before it gets dizzy
SMOOTH_FACE_PARAM = "BodySmoothFace"
FACE_PROB_THRESH = 0.5     # driver monitoring's confidence that it sees a face, to look at it


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
    self._smooth_chase_start: float | None = None
    self._reaction = NORMAL
    self._reaction_until = 0.
    self._spin_start: float | None = None
    self._plug_time = 0.
    # the optional smooth face (BodySmoothFace); the dot face stays the default
    self._smooth = SmoothFace()
    self._smooth_enabled = False
    self._smooth_checked = 0.
    self._smooth_time = time.monotonic()
    self._touch: tuple[float, float] | None = None
    # commands for the smooth face from anything else on the device (see face_command.py)
    self._face_sock = messaging.sub_sock(face_command.SERVICE, timeout=0)
    self._face_cmd: face_command.FaceCommand | None = None
    self._face_cmd_until = 0.
    self._status_text = ""
    self._status_until = 0.
    self._caption_text = ""
    self._caption_until = 0.
    self._overlay: face_command.Overlay | None = None
    self._overlay_until = 0.
    self._charge_estimator = ChargeEstimator()
    self._charge_eta: float | None = None
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

    self._charge_eta = self._charge_estimator.update(now, self._battery, self._charging)

    if self._charging != was_charging:
      if self._charging:
        self._plug_time = now
      if ui_state.is_onroad():
        self._react(HAPPY if self._charging else SURPRISED)

  def _react(self, animation):
    self._reaction = animation
    self._reaction_until = time.monotonic() + duration(animation)

  def _handle_mouse_event(self, mouse_event):
    super()._handle_mouse_event(mouse_event)
    # remember where a finger is, so the smooth face can look at it
    self._touch = (mouse_event.pos.x, mouse_event.pos.y) if mouse_event.left_down else None
    if mouse_event.left_down:
      companion.touched()   # so a companion computer driving the body can hold it still while it's being used

  def _update_state(self):
    super()._update_state()

    sm = ui_state.sm
    self._update_battery(sm)
    companion.update()

    if time.monotonic() - self._smooth_checked > 1.0:
      self._smooth_enabled = ui_state.params.get_bool(SMOOTH_FACE_PARAM)
      self._smooth_checked = time.monotonic()

    # every message, in order: a status word and a face command often arrive together
    for msg in messaging.drain_sock(self._face_sock):
      data = bytes(msg.customReservedRawData0)
      cmd = face_command.parse(data)
      if cmd is not None:
        self._face_cmd, self._face_cmd_until = cmd, time.monotonic() + cmd.seconds
      status = face_command.parse_status(data)
      if status is not None:
        self._status_text, self._status_until = status[0], time.monotonic() + status[1]
      caption = face_command.parse_caption(data)
      if caption is not None:
        self._caption_text, self._caption_until = caption[0], time.monotonic() + caption[1]
      overlay = face_command.parse_overlay(data)
      if overlay is not None:
        self._overlay, self._overlay_until = overlay, time.monotonic() + face_command.OVERLAY_SECONDS
        device._reset_interactive_timeout()  # someone is standing in front of it following the guide: keep the screen on

    if ui_state.is_onroad():
      if not self._was_active:
        self._last_input_time = time.monotonic()
        self._was_active = True
        self._yawn_until = time.monotonic() + duration(YAWN)  # waking up
        # cut straight to the yawn, rather than rewinding whatever sleep scene was playing
        self._animator = FaceAnimator(ASLEEP)
        self._scene_until = 0.

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
      if device.awake and now >= self._next_scene_time and not self._teleop_connected:
        scenes = SMOOTH_FACE_SCENES if self._smooth_enabled else OFFROAD_SCENES
        scene = scenes[self._scene_index % len(scenes)]
        self._scene_index += 1
        if scene is None:
          # the chase drawn in the smooth style
          self._scene, self._smooth_chase_start, self._scene_until = ASLEEP, now, now + CHASE_SECONDS
        else:
          self._scene, self._smooth_chase_start, self._scene_until = scene, None, now + duration(scene)
        self._next_scene_time = self._scene_until + random.uniform(*SCENE_GAP)
      if self._teleop_connected:
        # someone is connected and can see through the camera: it shouldn't look asleep
        self._scene_until, self._smooth_chase_start = 0., None
        self._animator.set_animation(NORMAL)
      else:
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
  def _mic_live(self) -> bool:
    # goes by the audio itself, not the setting: the dot is on exactly when the microphone is producing sound
    sm = ui_state.sm
    return sm.recv_frame['rawAudioData'] > 0 and (time.monotonic() - sm.recv_time['rawAudioData']) < MIC_TIMEOUT

  def _signal_shapes(self, now: float, aspect: float) -> list[tuple]:
    shapes: list[tuple] = []
    caption = self._caption_text if now < self._caption_until else ""
    # with a caption along the bottom, the bars and dots move up to make room
    y = SIGNAL_Y - 0.09 if caption else SIGNAL_Y
    signal = self._signal(now)
    if signal == "listening":
      shapes += listening_shapes(aspect, now, y)
    elif signal == "thinking":
      shapes += thinking_shapes(aspect, now, y)
    if caption:
      shapes.append(("text", aspect / 2, 0.925, 0.085, caption, (255, 255, 255, 255), False))
    return shapes

  def _draw_badge(self, rect: rl.Rectangle, text: str, color: rl.Color, right: bool, icon: str, pulse: float = 1.):
    """A labelled pill in a top corner: an icon and a word, so what it means doesn't have to be guessed."""
    h = BADGE_HEIGHT * rect.height
    font = gui_app.font(FontWeight.MEDIUM)
    text_size = measure_text_cached(font, text, int(0.5 * h))
    width = h * 1.25 + text_size.x + 0.4 * h
    margin = 0.05 * rect.height
    x = rect.x + rect.width - margin - width if right else rect.x + margin
    y = rect.y + margin
    rl.draw_rectangle_rounded(rl.Rectangle(x, y, width, h), 1., 16, rl.Color(color.r, color.g, color.b, 46))
    rl.draw_rectangle_rounded_lines_ex(rl.Rectangle(x, y, width, h), 1., 16, max(2., 0.03 * h), rl.Color(color.r, color.g, color.b, 170))
    cx, cy = x + 0.62 * h, y + 0.5 * h
    if icon == "mic":
      # a microphone: the capsule, the cradle under it, and the stand
      w = 0.2 * h * pulse
      rl.draw_rectangle_rounded(rl.Rectangle(cx - w / 2, cy - 0.3 * h, w, 0.42 * h), 1., 12, color)
      rl.draw_ring(rl.Vector2(cx, cy), 0.19 * h, 0.235 * h, 0., 180., 24, color)
      rl.draw_line_ex(rl.Vector2(cx, cy + 0.21 * h), rl.Vector2(cx, cy + 0.33 * h), max(2., 0.045 * h), color)
    else:
      rl.draw_circle(int(cx), int(cy), 0.17 * h * pulse, color)
    rl.draw_text_ex(font, text, rl.Vector2(x + 1.25 * h, cy - text_size.y / 2), int(0.5 * h), 0, rl.Color(255, 255, 255, 235))

  def _draw_badges(self, rect: rl.Rectangle):
    now = time.monotonic()
    pulse = 0.5 - 0.5 * math.cos(2 * math.pi * now / 1.2)
    # the microphone, whenever it is live, with what the body is doing about what it hears
    if self._mic_live():
      status = self._status_text if now < self._status_until else ""
      self._draw_badge(rect, status or "mic on", rl.Color(*(STATUS_COLOR if status else MIC_COLOR)), False, "mic", 1. + (0.25 * pulse if status else 0.))
    # someone is connected and can drive it and see through its camera
    if self._teleop_connected:
      self._draw_badge(rect, "connected", rl.Color(255, 70, 60, 255), True, "dot", 0.7 + 0.3 * pulse)

  def _handle_mouse_release(self, mouse_pos):
    super()._handle_mouse_release(mouse_pos)
    if not self._was_active:
      self._animator.set_animation(SLEEPY)
    else:
      self._react(HAPPY)  # a pat on the head

  def _look_target(self, rect: rl.Rectangle) -> tuple[float, float] | None:
    """Where the smooth face looks: at a finger on the screen, into a turn, or at a face it can see.
    None lets the eyes wander on their own."""
    if self._touch is not None:
      return (2 * (self._touch[0] - rect.x) / rect.width - 1, 2 * (self._touch[1] - rect.y) / rect.height - 1)
    if self._turning_left or self._turning_right:
      return (-1. if self._turning_left else 1., 0.)
    if ui_state.is_onroad():
      driver = ui_state.sm['driverStateV2'].leftDriverData
      if driver.faceProb > FACE_PROB_THRESH and len(driver.facePosition) >= 2:
        # TODO: check the signs on the body; the driver camera is the body's front camera
        return (-4 * driver.facePosition[0], 4 * driver.facePosition[1])
    return None

  def _draw_shapes(self, rect: rl.Rectangle, shapes: list[tuple]):
    """Draw smooth face shapes; they're in units of the face area's height."""
    u = rect.height
    for shape in shapes:
      kind = shape[0]
      if kind == "rrect":
        _, cx, cy, w, h, radius, color = shape
        r = rl.Rectangle(rect.x + (cx - w / 2) * u, rect.y + (cy - h / 2) * u, w * u, h * u)
        # raylib's roundness is the corner radius as a fraction of half the shorter side
        rl.draw_rectangle_rounded(r, min(1., 2 * radius / max(min(w, h), 1e-6)), 24, rl.Color(*color))
      elif kind == "circle":
        _, cx, cy, radius, color = shape
        rl.draw_circle_v(rl.Vector2(rect.x + cx * u, rect.y + cy * u), radius * u, rl.Color(*color))
      elif kind == "stroke":
        _, points, thickness, color = shape
        pts = [rl.Vector2(rect.x + x * u, rect.y + y * u) for x, y in points]
        for a, b in zip(pts, pts[1:], strict=False):
          rl.draw_line_ex(a, b, thickness * u, rl.Color(*color))
        for pt in pts:
          rl.draw_circle_v(pt, thickness * u / 2, rl.Color(*color))
      elif kind == "text":
        _, cx, cy, height, string, color, bold = shape
        font = gui_app.font(FontWeight.BOLD if bold else FontWeight.MEDIUM)
        size = measure_text_cached(font, string, int(height * u))
        rl.draw_text_ex(font, string, rl.Vector2(rect.x + cx * u - size.x / 2, rect.y + cy * u - size.y / 2), int(height * u), 0, rl.Color(*color))
      elif kind == "image":
        _, asset, box, cx, cy, w, h = shape
        texture = gui_app.texture(asset, 512, 512)
        source = rl.Rectangle(box[0] * texture.width, box[1] * texture.height, (box[2] - box[0]) * texture.width, (box[3] - box[1]) * texture.height)
        dest = rl.Rectangle(rect.x + (cx - w / 2) * u, rect.y + (cy - h / 2) * u, w * u, h * u)
        rl.draw_texture_pro(texture, source, dest, rl.Vector2(0, 0), 0., rl.WHITE)
      elif kind == "poly":
        _, points, color = shape
        a, b, c, d = (rl.Vector2(rect.x + x * u, rect.y + y * u) for x, y in points)
        # raylib only fills triangles wound one way; draw both windings so the order of the corners doesn't matter
        for tri in ((a, b, c), (a, c, d), (c, b, a), (d, c, a)):
          rl.draw_triangle(*tri, rl.Color(*color))

  def _signal(self, now: float) -> str | None:
    """Whether the companion computer says it is hearing something ("listening") or working on it ("thinking")."""
    if now >= self._status_until or not self._mic_live():
      return None
    if self._status_text == "listening":
      return "listening"
    if self._status_text in ("thinking", "heard you"):
      return "thinking"
    return None

  def _smooth_expression(self, now: float) -> dict:
    """What the smooth face should be doing right now, as arguments for SmoothFace.update."""
    if ui_state.is_offroad():
      # asleep, unless someone is connected and can see through the camera
      return {"expression": "normal" if self._teleop_connected else "asleep"}
    cs = ui_state.sm['carState']
    speed = abs(cs.vEgo) / FULL_SPEED
    # someone is talking to it: the whole face shows it, not just the badge
    signal = self._signal(now)
    if signal == "listening":
      # wide-eyed and still, looking at whoever it was already looking at
      look = self._face_cmd.look if self._face_cmd is not None and now < self._face_cmd_until else None
      return {"expression": "surprised", "intensity": 0.55, "look": look or (0., -0.1), "speed": speed}
    if signal == "thinking":
      # calm: the eyes lift a little, and the dots under them do the moving
      return {"expression": "normal", "look": THINKING_LOOK, "speed": speed}
    # something on the device is driving the face
    if self._face_cmd is not None and now < self._face_cmd_until:
      cmd = self._face_cmd
      return {"expression": cmd.expression, "intensity": cmd.intensity, "look": cmd.look, "talking": cmd.talking, "overrides": cmd.pose, "speed": speed}
    # its own reactions
    if now < self._reaction_until and self._reaction is HAPPY:
      return {"expression": "happy"}
    if now < self._reaction_until and self._reaction is SURPRISED:
      return {"expression": "surprised"}
    if not self._charging and ui_state.sm.recv_frame['carState'] > 0 and cs.fuelGauge < LOW_BATTERY:
      return {"expression": "sleepy", "speed": speed}
    if abs(cs.vEgo) > FAST_SPEED:
      # the faster it goes, the more it concentrates
      return {"expression": "determined", "intensity": min(1., (abs(cs.vEgo) - FAST_SPEED) / (FULL_SPEED - FAST_SPEED) + 0.4), "speed": speed}
    return {"expression": "normal", "speed": speed}

  def _render_smooth(self, rect: rl.Rectangle):
    now = time.monotonic()
    dt, self._smooth_time = min(now - self._smooth_time, 0.1), now
    state = self._smooth_expression(now)
    if state.get("look") is None:
      state["look"] = self._look_target(rect)
    self._smooth.aspect = rect.width / rect.height
    self._smooth.update(dt, charging=self._charging, **state)

    shapes = self._smooth.shapes()
    if ui_state.is_offroad() and self._teleop_connected:
      if self._charging:
        shapes += charge_strip(self._smooth.aspect, self._battery, meter_color(self._battery))
    elif ui_state.is_offroad():
      if self._charging:
        shapes += charge_panel(self._smooth.aspect, self._battery, meter_color(self._battery), self._charge_eta, now, now - self._plug_time)
      else:
        shapes += asleep_hint(self._smooth.aspect)
    elif self._charging and not (self._caption_text and now < self._caption_until):
      shapes += charge_strip(self._smooth.aspect, self._battery, meter_color(self._battery))
    shapes += self._signal_shapes(now, self._smooth.aspect)
    self._draw_shapes(rect, shapes)

    self._draw_badges(rect)

  def _render_overlay(self, rect: rl.Rectangle, overlay: face_command.Overlay):
    """A guide for someone standing in front of the body: which parts of the view are done, where to go next, and an outline."""
    cols, rows = overlay.grid
    cw, ch = rect.width / cols, (rect.height * 0.86) / rows
    pulse = 0.5 - 0.5 * math.cos(2 * math.pi * time.monotonic() / 1.0)
    for row in range(rows):
      for col in range(cols):
        cell = rl.Rectangle(rect.x + col * cw + 6, rect.y + row * ch + 6, cw - 12, ch - 12)
        if (row, col) in overlay.done:
          rl.draw_rectangle_rounded(cell, 0.12, 8, rl.Color(60, 200, 90, 120))
        elif (row, col) == overlay.target:
          rl.draw_rectangle_rounded(cell, 0.12, 8, rl.Color(255, 255, 255, int(40 + 70 * pulse)))
          rl.draw_rectangle_rounded_lines_ex(cell, 0.12, 8, 6, rl.WHITE)
        else:
          rl.draw_rectangle_rounded_lines_ex(cell, 0.12, 8, 2, rl.Color(255, 255, 255, 70))
    if len(overlay.outline) >= 2:
      color = rl.Color(60, 230, 90, 255) if overlay.ok else rl.Color(255, 80, 70, 255)
      pts = [rl.Vector2(rect.x + x * rect.width, rect.y + y * rect.height * 0.86) for x, y in overlay.outline]
      for a, b in zip(pts, pts[1:] + pts[:1], strict=True):
        rl.draw_line_ex(a, b, 10, color)
        rl.draw_circle_v(a, 5, color)
    if overlay.text:
      self._draw_shapes(rect, [("text", rect.width / rect.height / 2, 0.93, 0.085, overlay.text, (255, 255, 255, 255), True)])

  def _render(self, rect: rl.Rectangle):
    if self._overlay is not None and time.monotonic() < self._overlay_until:
      self._render_overlay(rect, self._overlay)
      return
    dots = self._animator.get_dots()
    animation = self._animator._animation
    if self._smooth_enabled and self._smooth_chase_start is not None and time.monotonic() < self._scene_until:
      # the characters come in from off the side; keep them from being drawn over the sidebar
      rl.begin_scissor_mode(int(rect.x), int(rect.y), int(rect.width), int(rect.height))
      self._draw_shapes(rect, chase_scene(rect.width / rect.height, time.monotonic() - self._smooth_chase_start))
      rl.end_scissor_mode()
      return
    # the smooth face takes over, except while one of the tiny body's dot scenes is playing
    if self._smooth_enabled and animation not in OFFROAD_SCENES:
      self._render_smooth(rect)
      return
    if self._turning_left and animation.left_turn_remove:
      remove_set = set(animation.left_turn_remove)
      dots = [d for d in dots if d not in remove_set]
    elif self._turning_right and animation.right_turn_remove:
      remove_set = set(animation.right_turn_remove)
      dots = [d for d in dots if d not in remove_set]
    self.draw_dot_grid(rect, dots, rl.WHITE)

    # the sleeping face is dimmed behind the text; scenes have the screen to themselves at full brightness
    if ui_state.is_offroad() and animation not in OFFROAD_SCENES and not self._teleop_connected:
      rl.draw_rectangle(int(self.rect.x), int(self.rect.y), int(self.rect.width), int(self.rect.height), rl.Color(0, 0, 0, 175))
      upper_half = rl.Rectangle(rect.x, rect.y, rect.width, rect.height / 2)
      eta = format_eta_short(self._charge_eta)
      charging_text = f"charging {round(self._battery * 100)}%" + (f" · {eta}" if eta else "")
      self._offroad_label.set_text(charging_text if self._charging else "switch to drive mode to use")
      self._offroad_label.render(upper_half)

    # charge meter above the face. drawn after the dimming so it stays bright over the sleeping face.
    # (the smooth face has its own charging readout, so its scenes play without the dot meter)
    if self._charging and not self._smooth_enabled:
      now = time.monotonic()
      for dot, color in battery_meter(self._battery, now, now - self._plug_time):
        self.draw_dot_grid(rect, [dot], rl.Color(*color))

    self._draw_shapes(rect, self._signal_shapes(time.monotonic(), rect.width / rect.height))
    self._draw_badges(rect)
