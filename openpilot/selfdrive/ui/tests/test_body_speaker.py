import base64
import unittest
import json

import numpy as np

from openpilot.selfdrive.ui.body.speaker import MAX_BUFFER_SECONDS, OUTPUT_RATE, START_BUFFER_SECONDS, Speaker, parse


def message(samples: np.ndarray, rate: int = 16000) -> bytes:
  return json.dumps({"audio": {"pcm": base64.b64encode(samples.astype("<i2").tobytes()).decode(), "rate": rate}}).encode()


class TestParse(unittest.TestCase):
  def test_resamples_to_output_rate(self):
    samples, flush = parse(message(np.full(1600, 16384), 16000))
    assert not flush
    assert samples.size == 4800
    assert np.allclose(samples, 0.5, atol=1e-3)

  def test_output_rate_passes_through(self):
    samples, _ = parse(message(np.arange(480), OUTPUT_RATE))
    assert samples.size == 480

  def test_flush(self):
    assert parse(b'{"audio": {"flush": true}}') == (None, True)

  def test_malformed_is_ignored(self):
    for data in (b"", b"not json", b"[]", b'{"audio": 3}', b'{"audio": {"pcm": 5}}', b'{"audio": {"pcm": "!!!"}}',
                 b'{"audio": {"pcm": ""}}', b'{"audio": {"pcm": "AAAA", "rate": 12345}}', b'{"face": {}}'):
      assert parse(data) == (None, False), data

  def test_oversized_is_ignored(self):
    assert parse(message(np.zeros(200000))) == (None, False)


class TestSpeaker(unittest.TestCase):
  def test_silent_until_enough_is_buffered(self):
    speaker = Speaker()
    speaker.add(np.ones(int(START_BUFFER_SECONDS * OUTPUT_RATE) - 1, dtype=np.float32))
    assert not speaker.take(480).any()
    speaker.add(np.ones(480, dtype=np.float32))
    assert speaker.take(480).all()

  def test_plays_out_then_goes_silent(self):
    speaker = Speaker()
    speaker.add(np.ones(OUTPUT_RATE, dtype=np.float32))
    played = sum(int(np.count_nonzero(speaker.take(2400))) for _ in range(30))
    assert played == OUTPUT_RATE
    assert not speaker.playing

  def test_flush_stops_playback(self):
    speaker = Speaker()
    speaker.add(np.ones(OUTPUT_RATE, dtype=np.float32))
    speaker.take(2400)
    speaker.flush()
    assert not speaker.take(2400).any()

  def test_buffer_is_capped(self):
    speaker = Speaker()
    for _ in range(10):
      speaker.add(np.ones(OUTPUT_RATE, dtype=np.float32))
    assert speaker.buffer.size == int(MAX_BUFFER_SECONDS * OUTPUT_RATE)
