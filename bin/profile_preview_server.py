#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

sys_root = Path(__file__).resolve().parent.parent
if str(sys_root) not in sys.path:
    sys.path.insert(0, str(sys_root))

from src.kennybot.features.search import build_profile_preview_response, parse_json_payload


logger = logging.getLogger(__name__)


def dispatch_profile_preview_request(
    *,
    root: Path,
    method: str,
    path: str,
    raw_body: str = "",
    ai_client: Any | None = None,
) -> tuple[HTTPStatus, dict[str, object]]:
    normalized_path = urlparse(path).path.rstrip("/") or "/"
    normalized_method = method.upper().strip()
    if normalized_method == "GET":
        if normalized_path == "/healthz":
            return HTTPStatus.OK, {"ok": True}
        return HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"}
    if normalized_method != "POST":
        return HTTPStatus.METHOD_NOT_ALLOWED, {"ok": False, "error": "method_not_allowed"}
    if normalized_path != "/profile-preview":
        return HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"}
    try:
        payload = parse_json_payload(raw_body)
    except json.JSONDecodeError as exc:
        return HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid_json", "detail": str(exc)}
    try:
        response = build_profile_preview_response(root=root, payload=payload, ai_client=ai_client)
    except Exception as exc:  # pragma: no cover - defensive server boundary
        return HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": "internal_error", "detail": str(exc)}
    return HTTPStatus.OK, {"ok": True, **response}


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
        status, body = dispatch_profile_preview_request(
            root=self.server.root,
            method="GET",
            path=self.path,
        )
        self._write_json(status, body)

    def do_POST(self) -> None:  # noqa: N802
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid_content_length"})
            return

        raw = self.rfile.read(max(0, length)).decode("utf-8", errors="replace")
        status, body = dispatch_profile_preview_request(
            root=self.server.root,
            method="POST",
            path=self.path,
            raw_body=raw,
        )
        self._write_json(status, body)

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Standalone local profile preview HTTP API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8089)
    parser.add_argument("--root", type=Path, default=sys_root)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    server = ProfilePreviewHTTPServer((args.host, args.port), Path(args.root))
    try:
        logger.info("listening on http://%s:%s (ai=%s)", args.host, args.port, "on")
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
