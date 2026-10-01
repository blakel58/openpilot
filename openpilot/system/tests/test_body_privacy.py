import threading

from opendbc.car.structs import car
from openpilot.common.params import Params
from openpilot.common.test import OpenpilotTestCase
from openpilot.system import body_privacy
from openpilot.system.manager.process_config import body_listening, lan_bridge, logging, procs, qcomgps, uploads_allowed


def _cp(not_car: bool):
  CP = car.CarParams.new_message()
  CP.notCar = not_car
  return CP


class TestBodyPrivacy(OpenpilotTestCase):
  def setup_method(self):
    self.params = Params()
    for k in ("BodyDataSharing", "BodyListening", "CarParams", "CarParamsPersistent", "DisableLogging"):
      self.params.remove(k)

  def _drive(self, not_car: bool):
    # what card writes once it identifies the vehicle for this drive
    CP = _cp(not_car)
    cp_bytes = CP.to_bytes()
    self.params.put("CarParams", cp_bytes, block=True)
    self.params.put("CarParamsPersistent", cp_bytes, block=True)
    return CP

  def test_car_unaffected(self):
    CP = self._drive(False)
    assert not body_privacy.body_privacy_active(self.params, CP)
    assert logging(True, self.params, CP)
    assert uploads_allowed(False, self.params, CP)

  def test_body_private_by_default(self):
    CP = self._drive(True)
    assert body_privacy.body_privacy_active(self.params, CP)
    assert not logging(True, self.params, CP)
    assert not uploads_allowed(True, self.params, CP)

  def test_unidentified_device_private(self):
    # never seen a vehicle: don't assume a car while card is still fingerprinting
    assert body_privacy.body_privacy_active(self.params, _cp(False))
    assert not logging(True, self.params, _cp(False))
    assert not uploads_allowed(False, self.params, _cp(False))

  def test_no_logging_before_vehicle_identified(self):
    # last drive was a car, now onroad on a body: nothing is recorded until card identifies it
    self.params.put("CarParamsPersistent", _cp(False).to_bytes(), block=True)
    assert not logging(True, self.params, _cp(False))
    self._drive(True)
    assert not logging(True, self.params, _cp(False))

  def test_body_private_while_offroad(self):
    # offroad, the manager's CP is a default message; the persisted one says body
    self.params.put("CarParamsPersistent", _cp(True).to_bytes(), block=True)
    assert not uploads_allowed(False, self.params, _cp(False))

  def test_body_opt_in(self):
    self.params.put_bool("BodyDataSharing", True, block=True)
    CP = self._drive(True)
    assert not body_privacy.body_privacy_active(self.params, CP)
    assert logging(True, self.params, CP)
    assert uploads_allowed(True, self.params, CP)

  def test_body_opt_in_respects_disable_logging(self):
    self.params.put_bool("BodyDataSharing", True, block=True)
    self.params.put_bool("DisableLogging", True, block=True)
    assert not logging(True, self.params, self._drive(True))

  def test_athena_get_message_limited(self):
    from openpilot.system.athena import athenad
    self.params.put("CarParamsPersistent", _cp(True).to_bytes(), block=True)
    with self.assertRaisesRegex(Exception, "body privacy"):
      athenad.getMessage("livestreamWideRoadEncodeData")

  def test_athena_aborts_upload_in_progress(self):
    from openpilot.system.athena import athenad
    self.params.put("CarParamsPersistent", _cp(True).to_bytes(), block=True)
    item = athenad.UploadItem(path="", url="", headers={}, created_at=0, id="x", allow_cellular=True)
    with self.assertRaises(athenad.AbortTransferException):
      athenad.cb(None, item, 0, threading.Event(), 100, 50)

  def test_connect_off_by_default_on_body(self):
    self._drive(True)
    assert not body_privacy.connect_allowed(self.params)
    self.params.put_bool("BodyConnect", True, block=True)
    assert body_privacy.connect_allowed(self.params)

  def test_connect_unaffected_on_car(self):
    self._drive(False)
    assert body_privacy.connect_allowed(self.params)

  def test_no_lan_bridge_on_private_body(self):
    CP = self._drive(True)
    assert not lan_bridge(True, self.params, CP)
    self.params.put_bool("BodyDataSharing", True, block=True)
    assert lan_bridge(True, self.params, CP)

  def test_no_gps_on_private_body(self):
    assert not qcomgps(True, self.params, self._drive(True))

  def test_body_microphone_off_by_default(self):
    micd = next(p for p in procs if p.name == "micd")
    CP = self._drive(True)
    assert not micd.should_run(True, self.params, CP)
    self.params.put_bool("BodyListening", True, block=True)
    assert micd.should_run(True, self.params, CP)
    assert not micd.should_run(False, self.params, CP)  # never while the body is asleep
    # the setting does nothing on a car, where the microphone runs as before
    assert not body_listening(True, self.params, self._drive(False))

  def test_microphone_only_streams_when_listening(self):
    from openpilot.cereal import messaging
    from openpilot.system.webrtc.webrtcd import MicrophoneProxy

    class Channel:
      def __init__(self):
        self.sent = []
      def is_open(self):
        return True
      def send(self, data):
        self.sent.append(data)

    self._drive(True)
    pm = messaging.PubMaster(["rawAudioData"])
    proxy, channel = MicrophoneProxy(self.params), Channel()
    proxy.add_channel(channel)

    def speak():
      import time
      time.sleep(0.2)
      for _ in range(3):
        msg = messaging.new_message("rawAudioData", valid=True)
        msg.rawAudioData.data = bytes(range(16))
        msg.rawAudioData.sampleRate = 16000
        pm.send("rawAudioData", msg)
      time.sleep(0.2)
      proxy.update()

    speak()
    assert channel.sent == []

    self.params.put_bool("BodyListening", True, block=True)
    proxy.allowed_checked = 0.
    speak()
    assert len(channel.sent) == 3  # every chunk, in order
    import base64
    import json
    data = json.loads(channel.sent[0])
    assert data["type"] == "rawAudioData"
    assert base64.b64decode(data["data"]["data"]) == bytes(range(16))
    assert data["data"]["sampleRate"] == 16000

  def test_private_body_streams_without_stun(self):
    from teleoprtc import stream as teleoprtc_stream
    from openpilot.system.webrtc.webrtcd import local_only_ice
    stock = teleoprtc_stream.Configuration
    with local_only_ice(True):
      config = teleoprtc_stream.Configuration()
      config.ice_servers = [teleoprtc_stream.IceServer("stun:stun.l.google.com:19302")]
      assert len(stock.ice_servers.__get__(config)) == 0
    assert teleoprtc_stream.Configuration is stock
    with local_only_ice(False):
      assert teleoprtc_stream.Configuration is stock
