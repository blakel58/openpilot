"""
Sound sent to the comma body for its speaker, so a computer on the network can
talk through it: a voice, or the other side of a call. soundd owns the speaker
and mixes this in with its own sounds.

Audio arrives as small JSON messages on the customReservedRawData1 service
(the teleop data channel passes them on, like joystick commands):

  {"audio": {"pcm": "<base64 of 16-bit mono samples>", "rate": 16000}}
  {"audio": {"flush": true}}     stop talking right away

Send it about as fast as it should play. A little is buffered to ride out
network jitter, and anything more than a few seconds ahead is dropped.
"""
import base64
import json
import threading

import numpy as np

from openpilot.cereal import messaging

SERVICE = "customReservedRawData1"
OUTPUT_RATE = 48000
MAX_MESSAGE_BYTES = 256 * 1024
MAX_BUFFER_SECONDS = 5.
START_BUFFER_SECONDS = 0.12  # wait for this much before starting, so playback doesn't stutter
RATES = (8000, 16000, 22050, 24000, 44100, 48000)
VOLUME = 0.9


def parse(data: bytes) -> tuple[np.ndarray | None, bool]:
  """Read a message: (samples at the output rate or None, whether to flush). Malformed messages are ignored."""
  if len(data) > MAX_MESSAGE_BYTES:
    return None, False
  try:
    audio = json.loads(data).get("audio")
  except (ValueError, AttributeError):
    return None, False
  if not isinstance(audio, dict):
    return None, False
  if audio.get("flush") is True:
    return None, True
  rate, pcm = audio.get("rate", 16000), audio.get("pcm")
  if rate not in RATES or not isinstance(pcm, str):
    return None, False
  try:
    raw = base64.b64decode(pcm, validate=True)
  except ValueError:
    return None, False
  samples = np.frombuffer(raw[:len(raw) // 2 * 2], dtype="<i2").astype(np.float32) / 32768.
  if samples.size == 0:
    return None, False
  if rate != OUTPUT_RATE:
    out = np.arange(int(samples.size * OUTPUT_RATE / rate)) * (rate / OUTPUT_RATE)
    samples = np.interp(out, np.arange(samples.size), samples).astype(np.float32)
  return samples, False


class Speaker:
  def __init__(self):
    self.lock = threading.Lock()
    self.buffer = np.empty(0, dtype=np.float32)
    self.playing = False

  def add(self, samples: np.ndarray) -> None:
    with self.lock:
      self.buffer = np.concatenate((self.buffer, samples))[-int(MAX_BUFFER_SECONDS * OUTPUT_RATE):]

  def flush(self) -> None:
    with self.lock:
      self.buffer = np.empty(0, dtype=np.float32)
      self.playing = False

  def take(self, frames: int) -> np.ndarray:
    """The next samples to play, padded with silence."""
    out = np.zeros(frames, dtype=np.float32)
    with self.lock:
      if not self.playing and self.buffer.size >= START_BUFFER_SECONDS * OUTPUT_RATE:
        self.playing = True
      if self.playing:
        n = min(frames, self.buffer.size)
        out[:n] = self.buffer[:n]
        self.buffer = self.buffer[n:]
        if self.buffer.size == 0:
          self.playing = False
    return out * VOLUME

  def receive_forever(self) -> None:
    sock = messaging.sub_sock(SERVICE, timeout=1000)
    while True:
      for msg in messaging.drain_sock(sock, wait_for_one=True):
        samples, flush = parse(bytes(msg.customReservedRawData1))
        if flush:
          self.flush()
        elif samples is not None:
          self.add(samples)

  def start(self) -> None:
    threading.Thread(target=self.receive_forever, daemon=True).start()
