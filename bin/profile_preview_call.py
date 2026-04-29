#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from urllib import error, request

sys_root = Path(__file__).resolve().parent.parent
if str(sys_root) not in sys.path:
    sys.path.insert(0, str(sys_root))

from bin.profile_preview_server import ProfilePreviewHTTPServer  # noqa: E402
from src.kennybot.utils.profile_preview_api import parse_json_payload  # noqa: E402


logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="One-shot local profile preview call")
    parser.add_argument("--root", type=Path, default=sys_root)
    parser.add_argument("--guild-id", type=int, default=972052382315855912)
    parser.add_argument("--channel-id", type=int, default=972052382315855912)
    parser.add_argument(
        "--scope",
        type=str,
        default="auto",
        choices=["auto", "guild", "channel", "legacy_channel"],
    )
    parser.add_argument("--question", type=str, default="このサーバーはなにするところ？")
    parser.add_argument("--limit", type=int, default=6)
    parser.add_argument("--max-chars", type=int, default=2600)
    parser.add_argument("--emit-log", action="store_true")
    parser.add_argument("--log-file", type=Path, default=sys_root / "runtime" / "logs" / "profile_preview.log")
    parser.add_argument("--no-ai", action="store_true")
    parser.add_argument("--ollama-model", type=str, default="")
    parser.add_argument("--ollama-host", type=str, default="")
    parser.add_argument("--wait-seconds", type=float, default=8.0)
    parser.add_argument("--request-timeout-seconds", type=float, default=180.0)
    parser.add_argument("--json", action="store_true", help="Print JSON only")
    return parser


def _wait_until_ready(base_url: str, timeout: float) -> None:
    deadline = time.time() + max(1.0, float(timeout))
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            with request.urlopen(f"{base_url}/healthz", timeout=2) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            if body.get("ok") is True:
                return
        except Exception as exc:  # pragma: no cover - transient startup path
            last_error = exc
            time.sleep(0.1)
    if last_error is not None:
        raise RuntimeError(f"server did not become ready: {last_error}") from last_error
    raise RuntimeError("server did not become ready")


def main() -> int:
    args = _build_parser().parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    server = ProfilePreviewHTTPServer(("127.0.0.1", 0), Path(args.root))
    import threading

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    base_url = f"http://{host}:{port}"
    try:
        logger.info("started local profile preview server at %s (ai=%s)", base_url, "on")
        _wait_until_ready(base_url, args.wait_seconds)
        payload = {
            "root": str(args.root),
            "guild_id": args.guild_id,
            "channel_id": args.channel_id,
            "scope": args.scope,
            "question": args.question,
            "limit": args.limit,
            "max_chars": args.max_chars,
            "emit_log": bool(args.emit_log),
            "log_file": str(args.log_file),
            "use_ai": not bool(args.no_ai),
            "ollama_model": str(args.ollama_model or ""),
            "ollama_host": str(args.ollama_host or ""),
        }
        req = request.Request(
            f"{base_url}/profile-preview",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with request.urlopen(req, timeout=max(5.0, float(args.request_timeout_seconds))) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        if args.json:
            print(json.dumps(body, ensure_ascii=False))
        else:
            print(body.get("answer") or "")
            print(json.dumps(body.get("management_log"), ensure_ascii=False))
        return 0
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        logger.error("request failed: %s %s", exc.code, detail)
        return 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
