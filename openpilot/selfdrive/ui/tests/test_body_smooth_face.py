import unittest

from openpilot.selfdrive.ui.body.smooth_face import CLOSED_BELOW, EYE_HEIGHT, LOOK_X, ChargeEstimator, SmoothFace, Spring, \
                                                    charge_panel, charge_strip, format_eta, format_eta_short

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
      closed += face.open.value * face._blink() < CLOSED_BELOW
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
