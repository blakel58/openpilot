"""
Privacy-by-default for the comma body.

The comma body is a robot that lives in homes and other personal spaces, so it
does not record or upload anything unless the owner opts in with the
"BodyDataSharing" toggle. Cars are unaffected.

While body privacy is active:
  * loggerd does not run (nothing is written to disk)
  * uploader does not run and uploads nothing
  * athenad keeps its connection for live teleop, but does not upload files,
    forward logs, list the data directory, open the SSH proxy, or return live
    messages other than basic device status

Live streaming (teleop) is unaffected: it is peer-to-peer and nothing is stored.
"""
from __future__ import annotations

from openpilot.common.params import Params
from opendbc.car.structs import car

BODY_DATA_SHARING_PARAM = "BodyDataSharing"
BODY_CONNECT_PARAM = "BodyConnect"

# athena RPC methods allowed while body privacy is active. Everything needed for
# Connect pairing and live teleop; nothing that reads or moves stored data.
ALLOWED_ATHENA_METHODS = frozenset({
  "startStream",
  "getVersion",
  "getNotCar",
  "getNetworkType",
  "getNetworkMetered",
  "getSimInfo",
  "getPublicKey",
  "getGithubUsername",
  "getSshAuthorizedKeys",
  "listUploadQueue",
  "cancelUpload",
  "setRouteViewed",
  "getMessage",
})

# services athena's getMessage may return while body privacy is active
# (status Connect can show: battery, network, device health). No camera,
# location, map or sensor data.
ALLOWED_ATHENA_SERVICES = frozenset({
  "deviceState",
  "peripheralState",
  "pandaStates",
  "carState",
})

_cached_cp_bytes: bytes | None = None
_cached_not_car = True


def _persistent_not_car(params: Params) -> bool:
  """notCar from the last CarParams seen by this device (cached; parsing is not free).

  Fails private: a device that has never identified a vehicle (or can't parse
  what it saved) is treated as a body until card says otherwise.
  """
  global _cached_cp_bytes, _cached_not_car
  cp_bytes = params.get("CarParamsPersistent")
  if cp_bytes is None:
    return True
  if cp_bytes != _cached_cp_bytes:
    try:
      with car.CarParams.from_bytes(cp_bytes) as CP:
        _cached_not_car = bool(CP.notCar)
    except Exception:
      _cached_not_car = True
    _cached_cp_bytes = cp_bytes
  return _cached_not_car


def is_body(params: Params, CP: car.CarParams | None = None) -> bool:
  """True if this device is (or was last) attached to a comma body, or has never identified a vehicle."""
  if CP is not None and CP.notCar:
    return True
  return _persistent_not_car(params)


def body_privacy_active(params: Params, CP: car.CarParams | None = None) -> bool:
  """True when on a body and the owner has not opted in to data sharing."""
  return is_body(params, CP) and not params.get_bool(BODY_DATA_SHARING_PARAM)


def connect_allowed(params: Params) -> bool:
  """athenad (comma connect) only talks to comma's servers from a body if the owner turned it on.

  While connected, comma's servers can start a live stream, so it's off by default.
  """
  return not is_body(params) or params.get_bool(BODY_CONNECT_PARAM)
