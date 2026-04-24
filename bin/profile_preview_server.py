#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

sys_root = Path(__file__).resolve().parent.parent
if str(sys_root) not in sys.path:
    sys.path.insert(0, str(sys_root))

from src.kennybot.utils.profile_preview_api import build_profile_preview_response, parse_json_payload


class ProfilePreviewHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, server_address: tuple[str, int], root: Path):
        super().__init__(server_address, ProfilePreviewRequestHandler)
        self.root = root


class ProfilePreviewRequestHandler(BaseHTTPRequestHandler):
    server: ProfilePreviewHTTPServer

    def _write_json(self, status: HTTPStatus, body: dict[str, object]) -> None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path == "/healthz":
            self._write_json(HTTPStatus.OK, {"ok": True})
            return
        self._write_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path != "/profile-preview":
            self._write_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid_content_length"})
            return

        raw = self.rfile.read(max(0, length)).decode("utf-8", errors="replace")
        try:
            payload = parse_json_payload(raw)
        except json.JSONDecodeError as exc:
            self._write_json(
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "error": "invalid_json", "detail": str(exc)},
            )
            return

        try:
            response = build_profile_preview_response(root=self.server.root, payload=payload)
        except Exception as exc:  # pragma: no cover - defensive server boundary
            self._write_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"ok": False, "error": "internal_error", "detail": str(exc)},
            )
            return

        self._write_json(HTTPStatus.OK, {"ok": True, **response})

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Standalone local profile preview HTTP API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8089)
    parser.add_argument("--root", type=Path, default=sys_root)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    server = ProfilePreviewHTTPServer((args.host, args.port), Path(args.root))
    try:
        print(f"listening on http://{args.host}:{args.port}")
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
