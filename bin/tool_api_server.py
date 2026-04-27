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

from src.kennybot.utils.live_info import LiveInfoService
from src.kennybot.utils.local_rag import LocalRAG
from src.kennybot.utils.tool_api import build_tool_response, list_tool_menu
from src.kennybot.utils.profile_preview_api import parse_json_payload


logger = logging.getLogger(__name__)


def dispatch_tool_api_request(
    *,
    root: Path,
    method: str,
    path: str,
    raw_body: str = "",
    rag: LocalRAG | None = None,
    searcher: Any | None = None,
    live_info: LiveInfoService | None = None,
) -> tuple[HTTPStatus, dict[str, object]]:
    normalized_path = urlparse(path).path.rstrip("/") or "/"
    normalized_method = method.upper().strip()
    if normalized_method == "GET":
        if normalized_path == "/healthz":
            return HTTPStatus.OK, {"ok": True}
        if normalized_path == "/tools":
            return HTTPStatus.OK, list_tool_menu()
        return HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"}
    if normalized_method != "POST":
        return HTTPStatus.METHOD_NOT_ALLOWED, {"ok": False, "error": "method_not_allowed"}
    if normalized_path not in {"/tool/serverinfo", "/tool/rag", "/tool/web_search"}:
        return HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"}
    try:
        payload = parse_json_payload(raw_body)
    except json.JSONDecodeError as exc:
        return HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid_json", "detail": str(exc)}
    tool_name = normalized_path.rsplit("/", 1)[-1]
    try:
        response = build_tool_response(
            root=root,
            tool=tool_name,
            payload=payload if isinstance(payload, dict) else {},
            rag=rag,
            searcher=searcher,
            live_info=live_info,
        )
    except Exception as exc:  # pragma: no cover - defensive server boundary
        return HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": "internal_error", "detail": str(exc)}
    return HTTPStatus.OK, response


class ToolAPIHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, server_address: tuple[str, int], root: Path):
        super().__init__(server_address, ToolAPIRequestHandler)
        self.root = root
        self.rag = LocalRAG(root)
        self.searcher = None
        self.live_info = LiveInfoService()


class ToolAPIRequestHandler(BaseHTTPRequestHandler):
    server: ToolAPIHTTPServer

    def _write_json(self, status: HTTPStatus, body: dict[str, object]) -> None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        status, body = dispatch_tool_api_request(
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
        status, body = dispatch_tool_api_request(
            root=self.server.root,
            method="POST",
            path=self.path,
            raw_body=raw,
            rag=self.server.rag,
            searcher=self.server.searcher,
            live_info=self.server.live_info,
        )
        self._write_json(status, body)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Standalone local tool API HTTP server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--root", type=Path, default=sys_root)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    server = ToolAPIHTTPServer((args.host, args.port), Path(args.root))
    try:
        logger.info("listening on http://%s:%s", args.host, args.port)
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
