#!/usr/bin/env python3
"""Tiny health server: always responds with HTTP 200 on port 7000."""

from wsgiref.simple_server import make_server


HOST = "0.0.0.0"
PORT = 7000


def app(environ, start_response):
    body = b"OK\n"
    start_response(
        "200 OK",
        [
            ("Content-Type", "text/plain; charset=utf-8"),
            ("Content-Length", str(len(body))),
        ],
    )
    return [body]


if __name__ == "__main__":
    print(f"Health server listening on http://{HOST}:{PORT}")
    with make_server(HOST, PORT, app) as server:
        server.serve_forever()
