import unittest

from openpilot.selfdrive.ui.body.smooth_face import EXPRESSIONS, EYE_HEIGHT, LOOK_X, SmoothFace, charge_bar


def settle(face: SmoothFace, expression: str, look=(0., 0.), seconds: float = 2.0):
  for _ in range(round(seconds / 0.05)):
    face.update(0.05, expression, look)


def eyes(face: SmoothFace):
  return [s for s in face.shapes() if s[0] == "pill"]


class TestSmoothFace(unittest.TestCase):
  def test_settles_on_each_expression(self):
    face = SmoothFace()
    face._next_blink = 1e9  # no blinking during the test
    for name, target in EXPRESSIONS.items():
      settle(face, name)
      self.assertAlmostEqual(face.open, target["open"], places=2)
      self.assertAlmostEqual(face.smile, target["smile"], places=2)

  def test_eyes_open_when_awake_and_shut_asleep(self):
    face = SmoothFace()
    face._next_blink = 1e9
    settle(face, "normal")
    self.assertAlmostEqual(eyes(face)[0][4], EYE_HEIGHT, places=2)
    settle(face, "asleep")
    self.assertLess(eyes(face)[0][4], 0.05)

  def test_look_moves_both_eyes_and_is_clamped(self):
    face = SmoothFace()
    face._next_blink = 1e9
    settle(face, "normal")
    centered = [e[1] for e in eyes(face)]
    settle(face, "normal", look=(5., 0.))  # far beyond the range
    looking = [e[1] for e in eyes(face)]
    for a, b in zip(centered, looking, strict=True):
      self.assertAlmostEqual(b - a, LOOK_X, places=2)

  def test_asleep_ignores_look(self):
    face = SmoothFace()
    settle(face, "asleep", look=(1., 1.))
    self.assertAlmostEqual(face.look_x, 0., places=3)

  def test_blinks_while_awake(self):
    face = SmoothFace()
    face._rng.seed(1)
    heights = []
    for _ in range(round(12 / 0.02)):
      face.update(0.02, "normal")
      heights.append(eyes(face)[0][4])
    self.assertLess(min(heights[200:]), 0.5 * EYE_HEIGHT)   # it blinked
    self.assertGreater(sorted(heights[200:])[len(heights[200:]) // 2], 0.9 * EYE_HEIGHT)  # and is open most of the time

  def test_stays_on_screen(self):
    face = SmoothFace()
    for expression, look in (("normal", (1., 1.)), ("normal", (-1., -1.)), ("happy", (1., -1.)), ("asleep", (0., 0.))):
      settle(face, expression, look)
      for shape in face.shapes():
        if shape[0] == "pill":
          _, cx, cy, w, h, _ = shape
          self.assertTrue(0 <= cx - w / 2 and cx + w / 2 <= face.aspect and 0 <= cy - h / 2 and cy + h / 2 <= 1)
        else:
          self.assertTrue(all(0 <= x <= face.aspect and 0 <= y <= 1 for x, y in shape[1]))

  def test_charge_bar(self):
    green = (80, 220, 120, 255)
    self.assertEqual(len(charge_bar(2.0, 0.0, green)), 1)   # just the track
    track, fill = charge_bar(2.0, 0.5, green)
    self.assertAlmostEqual(fill[3], track[3] / 2)
    self.assertAlmostEqual(charge_bar(2.0, 7.0, green)[1][3], track[3])  # clamped to full
