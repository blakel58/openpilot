import json
import unittest

from openpilot.selfdrive.ui.body import face_command
from openpilot.selfdrive.ui.body.smooth_face import CLOSED_BELOW, EXPRESSIONS, EYE_HEIGHT, LOOK_X, POSE_DEFAULTS, POSE_LIMITS, ChargeEstimator, \
                                                    SmoothFace, Spring, CHASE_SECONDS, charge_panel, charge_strip, chase_scene, format_eta, \
                                                    format_eta_short, pose_for

DT = 1 / 30


def quiet_face() -> SmoothFace:
  """A face that won't blink or glance by itself during a test."""
  face = SmoothFace()
  face._next_blink = face._next_glance = 1e9
  return face


def settle(face: SmoothFace, expression: str, look=(0., 0.), seconds: float = 3.0, **kwargs):
  for _ in range(round(seconds / DT)):
    face.update(DT, expression, look, **kwargs)
  face._blinks.clear()  # waking up queues a couple of blinks


def eyes(face: SmoothFace):
  return [s for s in face.shapes() if s[0] == "rrect"]


class TestSpring(unittest.TestCase):
  def test_overshoots_then_settles(self):
    spring = Spring(0., stiffness=170., damping=17.)
    values = [spring.step(1., DT) for _ in range(90)]
    self.assertGreater(max(values), 1.01)             # it overshoots a little
    self.assertLess(max(values), 1.25)                # but not wildly
    self.assertAlmostEqual(values[-1], 1., places=2)  # and comes to rest

  def test_stable_with_a_long_frame(self):
    spring = Spring(0.)
    for _ in range(20):
      spring.step(1., 0.1)
    self.assertAlmostEqual(spring.value, 1., places=2)


class TestSmoothFace(unittest.TestCase):
  def test_asleep_eyes_are_closed_lines(self):
    face = quiet_face()
    settle(face, "asleep")
    kinds = [s[0] for s in face.shapes()]
    self.assertEqual(kinds, ["stroke", "stroke"])

  def test_awake_eyes_are_open(self):
    face = quiet_face()
    settle(face, "normal")
    self.assertEqual(len(eyes(face)), 2)
    for eye in eyes(face):
      self.assertAlmostEqual(eye[4], EYE_HEIGHT, delta=0.02)

  def test_waking_up_looks_around(self):
    face = quiet_face()
    settle(face, "asleep")
    looks = []
    for _ in range(round(2.0 / DT)):
      face.update(DT, "normal", None)
      looks.append(face.look_x.value)
    self.assertLess(min(looks), -0.4)     # it looked one way
    self.assertGreater(max(looks), 0.4)   # then the other
    face._next_glance = 1e9               # waking schedules a glance; keep it out of the way
    settle(face, "normal", None)
    self.assertAlmostEqual(face.look_x.value, 0., places=1)

  def test_look_moves_both_eyes_is_clamped_and_grows_the_near_eye(self):
    face = quiet_face()
    settle(face, "normal")
    centered = [e[1] for e in eyes(face)]
    settle(face, "normal", look=(5., 0.))  # far beyond the range
    left, right = eyes(face)
    for before, eye in zip(centered, (left, right), strict=True):
      self.assertAlmostEqual(eye[1] - before, LOOK_X, places=2)
    self.assertGreater(right[3], left[3])  # the eye on the side it's looking toward is bigger

  def test_asleep_ignores_look(self):
    face = quiet_face()
    settle(face, "asleep", look=(1., 1.))
    self.assertAlmostEqual(face.look_x.value, 0., places=3)

  def test_happy_arches_the_eyes(self):
    face = quiet_face()
    settle(face, "normal")
    settle(face, "happy", seconds=1.5)
    kinds = [s[0] for s in face.shapes()]
    self.assertEqual(kinds, ["rrect", "circle", "rrect", "circle"])  # each eye has a cheek pushing into it
    settle(face, "normal")
    self.assertEqual([s[0] for s in face.shapes()], ["rrect", "rrect"])

  def test_blinks_and_glances_on_its_own(self):
    face = SmoothFace()
    face._rng.seed(3)
    settle(face, "asleep", seconds=0.5)
    closed, looks = 0, []
    for _ in range(round(30 / DT)):
      face.update(DT, "normal", None)
      closed += face.pose["open"].value * face._blink() < CLOSED_BELOW
      looks.append(abs(face.look_x.value))
    self.assertGreater(closed, 3)                       # it blinked
    self.assertLess(closed, 0.1 * 30 / DT)              # but its eyes are open nearly all the time
    self.assertGreater(max(looks[round(5 / DT):]), 0.2)  # and it glanced around after waking

  def test_narrows_eyes_when_driving_fast(self):
    face = quiet_face()
    settle(face, "normal")
    relaxed = eyes(face)[0][4]
    settle(face, "normal", speed=1.)
    self.assertLess(eyes(face)[0][4], 0.9 * relaxed)

  def test_stays_on_screen(self):
    for expression, look in (("normal", (1., 1.)), ("normal", (-1., -1.)), ("happy", (1., -1.)), ("asleep", (0., 0.))):
      face = quiet_face()
      for _ in range(round(4 / DT)):
        face.update(DT, expression, look)
        for shape in face.shapes():
          if shape[0] == "rrect":
            _, cx, cy, w, h, _, _ = shape
            self.assertTrue(0 <= cx - w / 2 and cx + w / 2 <= face.aspect and 0 <= cy - h / 2 and cy + h / 2 <= 1, (expression, look))
          elif shape[0] == "stroke":
            self.assertTrue(all(0 <= x <= face.aspect and 0 <= y <= 1 for x, y in shape[1]))

  def test_eyes_lift_for_the_charging_readout(self):
    face = quiet_face()
    settle(face, "asleep")
    resting = face.shapes()[0][1][0][1]
    settle(face, "asleep", charging=True)
    self.assertLess(face.shapes()[0][1][0][1], resting - 0.15)


class TestExpressions(unittest.TestCase):
  def test_pose_for(self):
    self.assertEqual(pose_for("normal"), POSE_DEFAULTS)
    self.assertEqual(pose_for("no such face"), POSE_DEFAULTS)
    self.assertEqual(pose_for("happy")["cheek"], 1.)
    self.assertAlmostEqual(pose_for("happy", intensity=0.5)["cheek"], 0.5)
    # overrides win, and everything is kept in range
    pose = pose_for("happy", overrides={"cheek": 0.2, "size": 99., "made up": 1., "lid": "nope"})
    self.assertEqual(pose["cheek"], 0.2)
    self.assertEqual(pose["size"], POSE_LIMITS["size"][1])
    self.assertNotIn("made up", pose)

  def test_every_expression_settles_and_stays_on_screen(self):
    for name in EXPRESSIONS:
      face = quiet_face()
      settle(face, name, seconds=3.)
      for field, value in pose_for(name).items():
        self.assertAlmostEqual(face.pose[field].value, value, places=2, msg=f"{name}.{field}")
      for shape in face.shapes():
        if shape[0] == "rrect":
          _, cx, cy, w, h, _, _ = shape
          self.assertTrue(0 <= cx - w / 2 and cx + w / 2 <= face.aspect and 0 <= cy - h / 2 and cy + h / 2 <= 1, name)

  def test_slanted_lids(self):
    for name, inner_lower in (("determined", True), ("sad", False)):
      face = quiet_face()
      settle(face, name)
      lids = [s for s in face.shapes() if s[0] == "poly"]
      self.assertEqual(len(lids), 2)
      (_, left_pts, _), (_, right_pts, _) = lids
      # corners: top-left, top-right, bottom-right, bottom-left. the left eye's inner end is its right side
      left_inner, left_outer = left_pts[2][1], left_pts[3][1]
      right_inner, right_outer = right_pts[3][1], right_pts[2][1]
      self.assertEqual(left_inner > left_outer, inner_lower)
      self.assertEqual(right_inner > right_outer, inner_lower)

  def test_curious_is_lopsided(self):
    face = quiet_face()
    settle(face, "curious")
    left, right = eyes(face)
    self.assertGreater(right[3], 1.2 * left[3])

  def test_mouth_only_while_talking(self):
    face = quiet_face()
    settle(face, "normal")
    self.assertEqual(len(eyes(face)), 2)
    settle(face, "normal", talking=0.1, seconds=1.)
    quiet = face.shapes()[-1]
    settle(face, "normal", talking=1.0, seconds=1.)
    loud = face.shapes()[-1]
    self.assertEqual(len(eyes(face)), 3)          # two eyes and a mouth
    self.assertGreater(loud[4], 2 * quiet[4])     # it opens wider when louder
    settle(face, "normal", seconds=2.)
    self.assertEqual(len(eyes(face)), 2)          # and goes away again


class TestFaceCommand(unittest.TestCase):
  def test_round_trip(self):
    cmd = face_command.FaceCommand("curious", 0.8, (0.5, -0.25), 0.6, {"lid": 0.1}, 3.)
    self.assertEqual(face_command.parse(cmd.to_bytes()), cmd)

  def test_message_round_trip(self):
    import openpilot.cereal.messaging as messaging
    cmd = face_command.FaceCommand("surprised", look=(0.25, 0.5), talking=0.5, seconds=2.)
    event = messaging.log_from_bytes(face_command.to_message(cmd).to_bytes())
    self.assertEqual(event.which(), face_command.SERVICE)
    self.assertEqual(face_command.parse(bytes(event.customReservedRawData0)), cmd)

  def test_defaults(self):
    cmd = face_command.parse(b'{"face": {}}')
    self.assertEqual(cmd, face_command.FaceCommand())
    self.assertIsNone(cmd.look)
    self.assertIsNone(cmd.talking)

  def test_ignores_junk(self):
    for data in (b"", b"not json", b"[1, 2]", b'{"other": 1}', b'{"face": "happy"}', b'{"face": {}}' + b" " * 5000):
      self.assertIsNone(face_command.parse(data))

  def test_clamps_and_drops_bad_values(self):
    bad = {"expression": "evil", "intensity": 9, "look": [5, "x"], "talking": -3, "seconds": 9999, "pose": {"size": 50, "nope": 1, "lid": True}}
    cmd = face_command.parse(json.dumps({"face": bad}).encode())
    self.assertEqual(cmd.expression, "normal")
    self.assertEqual(cmd.intensity, 1.)
    self.assertIsNone(cmd.look)
    self.assertEqual(cmd.talking, 0.)
    self.assertEqual(cmd.seconds, face_command.MAX_SECONDS)
    self.assertEqual(cmd.pose, {"size": 2.})   # smooth_face clamps this to its real range

  def test_cannot_put_it_to_sleep(self):
    self.assertEqual(face_command.parse(b'{"face": {"expression": "asleep"}}').expression, "normal")


class TestChaseScene(unittest.TestCase):
  def test_plays_through(self):
    for aspect in (1.72, 2.0):
      on_screen = []
      for i in range(round(CHASE_SECONDS / DT) + 1):
        shapes = chase_scene(aspect, i * DT)
        self.assertEqual(shapes[0][0], "stroke")  # the ground line
        xs = [s[1] for s in shapes[1:] if s[0] in ("rrect", "circle")] + [s[3] for s in shapes if s[0] == "image"]
        on_screen.append(sum(0 <= x <= aspect for x in xs))
        # the comma never sinks into the ground or stretches off the top
        for s in shapes:
          if s[0] == "image":
            self.assertLessEqual(s[4] + s[6] / 2, 0.88 + 1e-6)
            self.assertGreaterEqual(s[4] - s[6] / 2, 0.)
        # nothing is drawn below the ground
        for s in shapes[1:]:
          if s[0] == "circle":
            self.assertLessEqual(s[2] + s[3], 0.9 + 1e-6)
      self.assertGreaterEqual(max(on_screen), 8)   # both characters are on screen in the middle (comma head, wheels, hubs, head, eyes)
      self.assertEqual(on_screen[-1], 0)       # and both have left by the end


class TestOverlay(unittest.TestCase):
  def test_round_trip(self):
    overlay = face_command.Overlay((4, 3), [(0, 1), (2, 3)], (1, 0), [(0.1, 0.2), (0.5, 0.25), (0.4, 0.9)], False, "3/40  closer")
    self.assertEqual(face_command.parse_overlay(overlay.to_bytes()), overlay)
    self.assertIsNone(face_command.parse(overlay.to_bytes()))                    # a guide isn't a face command
    self.assertIsNone(face_command.parse_overlay(face_command.FaceCommand().to_bytes()))  # and the other way round

  def test_drops_bad_values(self):
    raw = {"grid": [4, 3], "done": [[0, 0], [9, 9], "x", [1, True]], "target": [5, 5], "outline": [[0.5, 0.5], [7, -3], ["a", 1], [1]],
           "ok": "maybe", "text": "t" * 500}
    overlay = face_command.parse_overlay(json.dumps({"overlay": raw}).encode())
    self.assertEqual(overlay.done, [(0, 0)])
    self.assertIsNone(overlay.target)
    self.assertEqual(overlay.outline, [(0.5, 0.5), (1., 0.)])   # out-of-range points are pulled onto the screen
    self.assertTrue(overlay.ok)
    self.assertEqual(len(overlay.text), 60)
    self.assertEqual(face_command.parse_overlay(b'{"overlay": {"grid": [99, 0]}}').grid, (1, 1))
    for junk in (b"", b"nope", b'{"overlay": 3}'):
      self.assertIsNone(face_command.parse_overlay(junk))


class TestCharging(unittest.TestCase):
  def test_estimates_time_to_full(self):
    est = ChargeEstimator()
    self.assertIsNone(est.update(0., 0.50, True))
    eta = None
    # 1% every 2 minutes
    for i in range(1, 8):
      eta = est.update(i * 120., 0.50 + i * 0.01, True)
    self.assertIsNotNone(eta)
    self.assertAlmostEqual(eta, (1 - 0.57) * 100 * 120., delta=60.)

  def test_needs_to_watch_for_a_while_first(self):
    est = ChargeEstimator()
    est.update(0., 0.50, True)
    self.assertIsNone(est.update(10., 0.51, True))
    self.assertIsNone(est.update(20., 0.52, True))  # two ticks, but only 10s apart

  def test_resets_when_unplugged_and_knows_full(self):
    est = ChargeEstimator()
    for i in range(8):
      est.update(i * 120., 0.50 + i * 0.01, True)
    self.assertIsNone(est.update(1000., 0.57, False))
    self.assertIsNone(est.update(1001., 0.57, True))
    self.assertEqual(est.update(1002., 1.0, True), 0.)

  def test_formats(self):
    self.assertEqual(format_eta(None), "working out time left")
    self.assertEqual(format_eta(0), "fully charged")
    self.assertEqual(format_eta(7 * 60), "about 7 min to full")
    self.assertEqual(format_eta(47 * 60), "about 45 min to full")
    self.assertEqual(format_eta(80 * 60), "about 1 h 20 min to full")
    self.assertEqual(format_eta(120 * 60), "about 2 h to full")
    self.assertEqual(format_eta_short(None), "")
    self.assertEqual(format_eta_short(47 * 60), "45 min left")
    self.assertEqual(format_eta_short(0), "full")

  def test_panel_and_strip(self):
    green = (80, 220, 120, 255)
    panel = charge_panel(2.0, 0.55, green, 47 * 60, now=10., plugged_for=10.)
    texts = [s[4] for s in panel if s[0] == "text"]
    self.assertEqual(texts, ["55%", "about 45 min to full"])
    track, fill = [s for s in panel if s[0] == "rrect"][:2]
    self.assertAlmostEqual(fill[3], 0.55 * track[3], places=3)
    # just plugged in: the bar hasn't filled in yet
    self.assertEqual(len([s for s in charge_panel(2.0, 0.55, green, None, now=0., plugged_for=0.) if s[0] == "rrect"]), 1)
    self.assertEqual([s[4] for s in charge_strip(2.0, 0.55, green) if s[0] == "text"], ["55%"])


class TestStatus(unittest.TestCase):
  def test_status_word(self):
    self.assertEqual(face_command.parse_status(b'{"status": {"text": "listening", "seconds": 5}}'), ("listening", 5.))
    self.assertEqual(face_command.parse_status(b'{"status": {"text": ""}}'), ("", face_command.STATUS_SECONDS))

  def test_status_is_kept_short_and_plain(self):
    text, seconds = face_command.parse_status(json.dumps({"status": {"text": "<b>way too long a status</b>", "seconds": 999}}).encode())
    self.assertEqual(text, "bway too long a ")
    self.assertEqual(seconds, face_command.MAX_STATUS_SECONDS)

  def test_other_messages_are_not_status(self):
    for data in (b"", b"nope", b'{"face": {"expression": "happy"}}', b'{"status": "listening"}', b'{"status": {"text": 5}}'):
      self.assertIsNone(face_command.parse_status(data), data)
    # and a status message is neither a face command nor a guide
    self.assertIsNone(face_command.parse(b'{"status": {"text": "listening"}}'))
    self.assertIsNone(face_command.parse_overlay(b'{"status": {"text": "listening"}}'))


class TestCompanion(unittest.TestCase):
  def test_names_are_kept_speakable(self):
    from openpilot.selfdrive.ui.body.companion import DEFAULT_NAME, clean_name
    self.assertEqual(clean_name("  Milo "), "Milo")
    self.assertEqual(clean_name("R2-D2!"), "RD")
    self.assertEqual(clean_name("Sir   Roberto the Third of House Comma"), "Sir Roberto the")
    self.assertEqual(clean_name(""), DEFAULT_NAME)
    self.assertEqual(clean_name(None), DEFAULT_NAME)
    self.assertEqual(clean_name("1234"), DEFAULT_NAME)

  def test_messages(self):
    from openpilot.selfdrive.ui.body.companion import event_message, settings_message
    self.assertEqual(json.loads(settings_message("Milo")), {"body": {"name": "Milo"}})
    self.assertEqual(json.loads(event_message("enroll", 7)), {"body": {"event": "enroll", "id": 7}})
    # neither is mistaken for a face command, a guide or a status word
    for data in (settings_message("Milo"), event_message("enroll", 7)):
      self.assertIsNone(face_command.parse(data))
      self.assertIsNone(face_command.parse_overlay(data))
      self.assertIsNone(face_command.parse_status(data))


class TestSignals(unittest.TestCase):
  def test_listening_and_thinking_stay_under_the_eyes(self):
    from openpilot.selfdrive.ui.body.smooth_face import SIGNAL_Y, THINKING_LOOK, listening_shapes, thinking_shapes
    for t in (0., 0.3, 1.7, 12.4):
      bars, dots = listening_shapes(2., t), thinking_shapes(2., t)
      self.assertEqual((len(bars), len(dots)), (5, 3))
      for shape in bars + dots:
        self.assertAlmostEqual(shape[1], 1., delta=0.25)        # centred on the face
        self.assertAlmostEqual(shape[2], SIGNAL_Y, delta=0.08)  # below the eyes, above the bottom edge
    self.assertEqual(THINKING_LOOK[0], 0.)   # no wandering from side to side
    self.assertLess(THINKING_LOOK[1], -0.3)  # looking up a little

  def test_they_move(self):
    from openpilot.selfdrive.ui.body.smooth_face import listening_shapes, thinking_shapes
    self.assertNotEqual(listening_shapes(2., 0.), listening_shapes(2., 0.2))
    self.assertNotEqual(thinking_shapes(2., 0.), thinking_shapes(2., 0.2))


class TestCaption(unittest.TestCase):
  def test_caption(self):
    self.assertEqual(face_command.parse_caption(b'{"caption": {"text": "Say Roberto   3 of 8", "seconds": 6}}'), ("Say Roberto   3 of 8", 6.))
    self.assertEqual(face_command.parse_caption(b'{"caption": {"text": ""}}'), ("", face_command.STATUS_SECONDS))

  def test_caption_is_kept_short_and_plain(self):
    text, seconds = face_command.parse_caption(json.dumps({"caption": {"text": "<script>" + "x" * 80, "seconds": 500}}).encode())
    self.assertEqual(len(text), face_command.MAX_CAPTION_CHARS)
    self.assertNotIn("<", text)
    self.assertEqual(seconds, face_command.MAX_STATUS_SECONDS)

  def test_other_messages_are_not_captions(self):
    for data in (b"", b"nope", b'{"status": {"text": "listening"}}', b'{"caption": "hi"}', b'{"caption": {"text": 5}}'):
      self.assertIsNone(face_command.parse_caption(data), data)
