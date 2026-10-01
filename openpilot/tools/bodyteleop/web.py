#!/usr/bin/env python3
"""
Local teleop for the comma body, without comma connect.

Runs on your computer and serves a control page at http://localhost:5005.
The browser talks WebRTC directly to the body over your network; the only
thing sent through this server is the connection offer, which goes to the
body's webrtcd over an SSH tunnel (so it's authenticated with your SSH key).
Nothing goes through comma's servers.

  tools/bodyteleop/web.py --body 192.168.1.42

When a ROS bridge on another computer is the thing connected to the body (only one thing
can be at a time), drive through it instead:

  tools/bodyteleop/web.py --ros jetson

That runs the bridge's teleop relay over SSH: drive commands go in, and the camera and
status come back the same way.
"""
import argparse
import dataclasses
import json
import os
import re
import socket
import subprocess
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from openpilot.system.webrtc.helpers import StreamRequestBody, WEBRTCD_PORT

TELEOPDIR = os.path.dirname(os.path.abspath(__file__))
CAMERAS = ("driver", "wideRoad", "road")  # driver camera faces the body's front


def _local_ip() -> str | None:
  # route lookup only, nothing is sent
  s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
  try:
    s.connect(("8.8.8.8", 53))
    return s.getsockname()[0]
  except OSError:
    return None
  finally:
    s.close()


def _free_port() -> int:
  with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    return int(s.getsockname()[1])


def resolve_mdns_candidates(sdp: str, ip: str | None) -> str:
  # browsers hide their LAN address behind a random .local name, which webrtcd can't resolve.
  # the browser runs on this computer, so swap in this computer's address instead of dropping them
  if ip is None:
    return "".join(l for l in sdp.splitlines(keepends=True) if not (l.startswith("a=candidate:") and ".local " in l))
  return re.sub(r"[\w-]+\.local\b", ip, sdp)


class Body:
  def __init__(self, host: str | None, user: str, webrtcd_url: str | None):
    self.host = host
    self.user = user
    self.tunnel: subprocess.Popen | None = None
    self.tunnel_port: int | None = None
    if webrtcd_url is not None:
      self.webrtcd_url = webrtcd_url
    else:
      assert host is not None
      self.tunnel_port = _free_port()
      self.webrtcd_url = f"http://127.0.0.1:{self.tunnel_port}"
      self.ensure_tunnel()

  def ensure_tunnel(self):
    # the tunnel dies when the body reboots or drops off wifi; reopen it when needed
    if self.tunnel_port is None or (self.tunnel is not None and self.tunnel.poll() is None):
      return
    # -N: tunnel only, no remote shell. ExitOnForwardFailure so a bad tunnel fails loudly
    self.tunnel = subprocess.Popen(["ssh", "-N", "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=10",
                                    "-L", f"{self.tunnel_port}:127.0.0.1:{WEBRTCD_PORT}", f"{self.user}@{self.host}"])
    for _ in range(20):
      if self.webrtcd_up() or self.tunnel.poll() is not None:
        break
      time.sleep(0.25)

  def close(self):
    if self.tunnel is not None:
      self.tunnel.terminate()

  def webrtcd_up(self) -> bool:
    try:
      with urllib.request.urlopen(f"{self.webrtcd_url}/schema?services=carState", timeout=1):
        return True
    except OSError:
      return False

  def wake_webrtcd(self):
    # offroad, webrtcd only runs while IsLiveStreaming is set (it clears it when the session ends)
    if self.host is None:
      return
    subprocess.run(["ssh", f"{self.user}@{self.host}", "echo -n 1 > /data/params/d/IsLiveStreaming"], check=True, timeout=10)

  def stream(self, sdp: str) -> dict:
    self.ensure_tunnel()
    if not self.webrtcd_up():
      self.wake_webrtcd()
      for _ in range(40):
        if self.webrtcd_up():
          break
        time.sleep(0.25)
      else:
        return {"error": "body not reachable: is it on, and is SSH working?"}

    body = StreamRequestBody(sdp, [CAMERAS[0]], True, ["testJoystick"], ["carState", "carOutput", "deviceState"])
    req = urllib.request.Request(f"{self.webrtcd_url}/stream", data=json.dumps(dataclasses.asdict(body)).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
      return json.loads(resp.read())


class RosLink:
  """Drive through a ROS bridge on another computer, over one SSH connection to its teleop relay."""
  RELAY = "docker exec -i comma_body_bridge /entrypoint.sh ros2 run comma_body_bridge teleop_relay"
  MAX_SPEED = 0.8   # m/s at full stick
  MAX_TURN = 1.6    # rad/s at full stick

  def __init__(self, host: str):
    self.host = host
    self.proc: subprocess.Popen | None = None
    self.jpeg = b""
    self.frame_id = 0
    self.status: dict = {}
    self.lock = threading.Lock()

  def _ensure(self):
    if self.proc is not None and self.proc.poll() is None:
      return
    self.proc = subprocess.Popen(["ssh", self.host, self.RELAY], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()

  def _read(self, proc: subprocess.Popen):
    # "J <bytes>" then a JPEG, or "S <json>"
    out = proc.stdout
    assert out is not None
    while True:
      header = out.readline()
      if not header:
        return
      kind, _, rest = header.strip().partition(b" ")
      if kind == b"J" and rest.isdigit():
        data = out.read(int(rest))
        self.jpeg, self.frame_id = data, self.frame_id + 1
      elif kind == b"S":
        try:
          self.status = json.loads(rest)
        except ValueError:
          pass

  def drive(self, axes: list[float]):
    """Joystick axes like the body takes: [speed (negative = forward), turn (positive = left)]."""
    self._ensure()
    forward = -max(-1., min(1., float(axes[0]))) * self.MAX_SPEED
    turn = max(-1., min(1., float(axes[1]))) * self.MAX_TURN
    with self.lock:
      try:
        assert self.proc is not None and self.proc.stdin is not None
        self.proc.stdin.write(f"{forward:.3f} {turn:.3f}\n".encode())
        self.proc.stdin.flush()
      except OSError:
        pass  # the relay went away; it's restarted on the next command

  def close(self):
    if self.proc is not None:
      self.proc.terminate()


class Handler(BaseHTTPRequestHandler):
  server: "TeleopServer"

  def _send(self, status: int, body: bytes, content_type: str):
    self.send_response(status)
    self.send_header("Content-Type", content_type)
    self.send_header("Content-Length", str(len(body)))
    self.send_header("Cache-Control", "no-store")
    self.end_headers()
    self.wfile.write(body)

  def do_GET(self):
    ros = self.server.ros
    if self.path in ("/", "/index.html"):
      with open(os.path.join(TELEOPDIR, "static", "index.html"), "rb") as f:
        self._send(200, f.read(), "text/html; charset=utf-8")
    elif self.path == "/mode":
      self._send(200, json.dumps({"mode": "ros" if ros is not None else "webrtc", "host": ros.host if ros is not None else ""}).encode(), "application/json")
    elif self.path == "/status" and ros is not None:
      self._send(200, json.dumps({**ros.status, "frames": ros.frame_id}).encode(), "application/json")
    elif self.path == "/video" and ros is not None:
      ros._ensure()
      self.send_response(200)
      self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
      self.end_headers()
      sent = -1
      try:
        while True:
          if ros.frame_id != sent and ros.jpeg:
            sent, jpeg = ros.frame_id, ros.jpeg
            self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(jpeg) + jpeg + b"\r\n")
          time.sleep(0.02)
      except OSError:
        pass
    else:
      self._send(404, b"not found", "text/plain")

  def do_POST(self):
    if self.path == "/cmd" and self.server.ros is not None:
      try:
        self.server.ros.drive(json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))["axes"])
        self._send(200, b"{}", "application/json")
      except (ValueError, KeyError, IndexError, TypeError):
        self._send(400, b"{}", "application/json")
      return
    if self.path != "/offer" or self.server.body is None:
      self._send(404, b"not found", "text/plain")
      return
    try:
      offer = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
      answer = self.server.body.stream(resolve_mdns_candidates(offer["sdp"], _local_ip()))
      self._send(200, json.dumps(answer).encode(), "application/json")
    except Exception as e:
      self._send(500, json.dumps({"error": f"{type(e).__name__}: {e}"}).encode(), "application/json")

  def log_message(self, format, *args):  # noqa: A002  # stdlib override
    pass


class TeleopServer(ThreadingHTTPServer):
  daemon_threads = True
  body: Body | None = None
  ros: RosLink | None = None


def main():
  parser = argparse.ArgumentParser(description="comma body local teleop")
  parser.add_argument("--body", help="body IP address or hostname (connects over SSH)")
  parser.add_argument("--user", default="comma", help="SSH user on the body")
  parser.add_argument("--ros", metavar="HOST", help="drive through the ROS bridge running on this SSH host instead of connecting to the body directly")
  parser.add_argument("--webrtcd", help="webrtcd URL to use directly instead of an SSH tunnel, e.g. when running on the body")
  parser.add_argument("--host", default="127.0.0.1", help="address to serve the control page on (default: this computer only)")
  parser.add_argument("--port", type=int, default=5005, help="port for the control page (5000 is taken by AirPlay on macOS)")
  args = parser.parse_args()
  if args.body is None and args.webrtcd is None and args.ros is None:
    parser.error("pass --body <ip>, --ros <host>, or --webrtcd <url>")

  server = TeleopServer((args.host, args.port), Handler)
  if args.ros is not None:
    server.ros = RosLink(args.ros)
  else:
    server.body = Body(args.body, args.user, args.webrtcd)
  print(f"comma body teleop: http://{'localhost' if args.host == '127.0.0.1' else args.host}:{args.port}")
  try:
    server.serve_forever()
  except KeyboardInterrupt:
    pass
  finally:
    for link in (server.body, server.ros):
      if link is not None:
        link.close()


if __name__ == "__main__":
  main()
