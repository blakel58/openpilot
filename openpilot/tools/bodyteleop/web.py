#!/usr/bin/env python3
"""
Local teleop for the comma body, without comma connect.

Runs on your computer and serves a control page at http://localhost:5000.
The browser talks WebRTC directly to the body over your network; the only
thing sent through this server is the connection offer, which goes to the
body's webrtcd over an SSH tunnel (so it's authenticated with your SSH key).
Nothing goes through comma's servers.

  tools/bodyteleop/web.py --body 192.168.1.42
"""
import argparse
import dataclasses
import json
import os
import re
import socket
import subprocess
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
    if webrtcd_url is not None:
      self.webrtcd_url = webrtcd_url
    else:
      assert host is not None
      port = _free_port()
      self.webrtcd_url = f"http://127.0.0.1:{port}"
      # -N: tunnel only, no remote shell. ExitOnForwardFailure so a bad tunnel fails loudly
      self.tunnel = subprocess.Popen(["ssh", "-N", "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=10",
                                      "-L", f"{port}:127.0.0.1:{WEBRTCD_PORT}", f"{user}@{host}"])

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
    if not self.webrtcd_up():
      self.wake_webrtcd()
      for _ in range(40):
        if self.webrtcd_up():
          break
        time.sleep(0.25)
      else:
        return {"error": "body not reachable: is it on, and is SSH working?"}

    body = StreamRequestBody(sdp, [CAMERAS[0]], True, ["testJoystick"], ["carState", "deviceState"])
    req = urllib.request.Request(f"{self.webrtcd_url}/stream", data=json.dumps(dataclasses.asdict(body)).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
      return json.loads(resp.read())


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
    if self.path in ("/", "/index.html"):
      with open(os.path.join(TELEOPDIR, "static", "index.html"), "rb") as f:
        self._send(200, f.read(), "text/html; charset=utf-8")
    else:
      self._send(404, b"not found", "text/plain")

  def do_POST(self):
    if self.path != "/offer":
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
  body: Body


def main():
  parser = argparse.ArgumentParser(description="comma body local teleop")
  parser.add_argument("--body", help="body IP address or hostname (connects over SSH)")
  parser.add_argument("--user", default="comma", help="SSH user on the body")
  parser.add_argument("--webrtcd", help="webrtcd URL to use directly instead of an SSH tunnel, e.g. when running on the body")
  parser.add_argument("--host", default="127.0.0.1", help="address to serve the control page on (default: this computer only)")
  parser.add_argument("--port", type=int, default=5000)
  args = parser.parse_args()
  if args.body is None and args.webrtcd is None:
    parser.error("pass --body <ip> (or --webrtcd <url>)")

  server = TeleopServer((args.host, args.port), Handler)
  server.body = Body(args.body, args.user, args.webrtcd)
  print(f"comma body teleop: http://{'localhost' if args.host == '127.0.0.1' else args.host}:{args.port}")
  try:
    server.serve_forever()
  except KeyboardInterrupt:
    pass
  finally:
    server.body.close()


if __name__ == "__main__":
  main()
