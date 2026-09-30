import threading

from opendbc.car.structs import car
from openpilot.common.params import Params
from openpilot.common.test import OpenpilotTestCase
from openpilot.system import body_privacy
from openpilot.system.manager.process_config import logging, uploads_allowed


def _cp(not_car: bool):
  CP = car.CarParams.new_message()
  CP.notCar = not_car
  return CP


class TestBodyPrivacy(OpenpilotTestCase):
  def setup_method(self):
    self.params = Params()
    for k in ("BodyDataSharing", "CarParams", "CarParamsPersistent", "DisableLogging"):
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
