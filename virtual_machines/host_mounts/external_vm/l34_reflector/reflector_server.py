#!/usr/bin/env python3
"""Small HTTP reflector that returns source/destination IP:port for each request."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class ReflectorHandler(BaseHTTPRequestHandler):
    server_version = "l34-reflector/1.0"

    def _log_incoming_packet(
        self,
        timestamp_utc: str,
        src_ip: str,
        src_port: int,
        dst_ip: str,
        dst_port: int,
    ) -> None:
        print(
            f'{timestamp_utc} incoming_packet src={src_ip}:{src_port} dst={dst_ip}:{dst_port} '
            f'method="{self.command}" path="{self.path}"',
            flush=True,
        )

    def _send_reflection(self) -> None:
        src_ip, src_port = self.client_address
        dst_ip, dst_port = self.connection.getsockname()[:2]
        timestamp_utc = datetime.now(timezone.utc).isoformat()

        self._log_incoming_packet(timestamp_utc, src_ip, src_port, dst_ip, dst_port)

        payload = {
            "method": self.command,
            "path": self.path,
            "src_ip": src_ip,
            "src_port": src_port,
            "dst_ip": dst_ip,
            "dst_port": dst_port,
            "timestamp_utc": timestamp_utc,
        }
        body = json.dumps(payload, ensure_ascii=True, indent=2) + "\n"
        encoded = body.encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()

        if self.command != "HEAD":
            self.wfile.write(encoded)

    def do_GET(self) -> None:  # noqa: N802
        self._send_reflection()

    def do_POST(self) -> None:  # noqa: N802
        self._send_reflection()

    def do_PUT(self) -> None:  # noqa: N802
        self._send_reflection()

    def do_PATCH(self) -> None:  # noqa: N802
        self._send_reflection()

    def do_DELETE(self) -> None:  # noqa: N802
        self._send_reflection()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send_reflection()

    def do_HEAD(self) -> None:  # noqa: N802
        self._send_reflection()

    def log_message(self, _fmt: str, *_args: object) -> None:
        # Incoming packet logging is handled explicitly in _send_reflection.
        return


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="HTTP reflector returning src/dst IP+port in response body"
    )
    parser.add_argument("--host", default="0.0.0.0", help="Listen host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8080, help="Listen port (default: 8080)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with ThreadingHTTPServer((args.host, args.port), ReflectorHandler) as httpd:
        print(f"l34_reflector listening on http://{args.host}:{args.port}", flush=True)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped", flush=True)


if __name__ == "__main__":
    main()
