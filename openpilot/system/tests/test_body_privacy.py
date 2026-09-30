from opendbc.car.structs import car
from openpilot.common.params import Params
from openpilot.system import body_privacy
from openpilot.system.manager.process_config import logging, uploads_allowed


def _cp(not_car: bool):
  CP = car.CarParams.new_message()
  CP.notCar = not_car
  return CP


class TestBodyPrivacy:
  def setup_method(self):
    self.params = Params()
    self.params.remove("BodyDataSharing")
    self.params.remove("CarParamsPersistent")
    self.params.remove("DisableLogging")

  def test_car_unaffected(self):
    CP = _cp(False)
    assert not body_privacy.body_privacy_active(self.params, CP)
    assert logging(True, self.params, CP)
    assert uploads_allowed(False, self.params, CP)

  def test_body_private_by_default(self):
    CP = _cp(True)
    assert body_privacy.body_privacy_active(self.params, CP)
    assert not logging(True, self.params, CP)
    assert not uploads_allowed(True, self.params, CP)

  def test_body_private_while_offroad(self):
    # offroad, the manager's CP is a default message; the persisted one says body
    self.params.put("CarParamsPersistent", _cp(True).to_bytes())
    assert not uploads_allowed(False, self.params, _cp(False))

  def test_body_opt_in(self):
    self.params.put_bool("BodyDataSharing", True)
    CP = _cp(True)
    assert not body_privacy.body_privacy_active(self.params, CP)
    assert logging(True, self.params, CP)
    assert uploads_allowed(True, self.params, CP)

  def test_body_opt_in_respects_disable_logging(self):
    self.params.put_bool("BodyDataSharing", True)
    self.params.put_bool("DisableLogging", True)
    assert not logging(True, self.params, _cp(True))
