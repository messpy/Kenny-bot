#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys_root = Path(__file__).resolve().parent.parent
if str(sys_root) not in sys.path:
    sys.path.insert(0, str(sys_root))

from src.kennybot.utils.profile_preview import (
    build_channel_profile_preview,
    build_profile_management_log,
    write_jsonl_log,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Standalone local channel profile preview")
    parser.add_argument("--root", type=Path, default=sys_root, help="Repository root used to resolve data/channel_rag")
    parser.add_argument("--guild-id", type=int, default=972052382315855912)
    parser.add_argument("--channel-id", type=int, default=972052382315855912)
    parser.add_argument(
        "--scope",
        type=str,
        default="auto",
        choices=["auto", "guild", "channel", "legacy_channel"],
        help="Which scoped RAG to read",
    )
    parser.add_argument("--question", type=str, default="このサーバーはなにするところ？")
    parser.add_argument("--limit", type=int, default=6)
    parser.add_argument("--max-chars", type=int, default=2600)
    parser.add_argument("--emit-log", action="store_true", help="Append a local management-log style JSONL entry")
    parser.add_argument("--log-file", type=Path, default=sys_root / "runtime" / "logs" / "profile_preview.log")
    parser.add_argument("--input-json", type=str, default="", help="Override arguments from a JSON object")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of human-readable text")
    return parser


def _parse_payload(args: argparse.Namespace) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if not args.input_json:
        return payload
    loaded = json.loads(args.input_json)
    if isinstance(loaded, dict):
        payload = loaded
    return payload


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    payload = _parse_payload(args)

    def _get(name: str, default: Any) -> Any:
        if name in payload and payload[name] is not None:
            return payload[name]
        return getattr(args, name, default)

    preview = build_channel_profile_preview(
        root=Path(_get("root", sys_root)),
        guild_id=_get("guild_id", 972052382315855912),
        channel_id=_get("channel_id", 972052382315855912),
        scope=str(_get("scope", "auto")),
        question=str(_get("question", "このサーバーはなにするところ？")),
        limit=int(_get("limit", 6)),
        max_chars=int(_get("max_chars", 2600)),
    )
    management_log = build_profile_management_log(preview)
    if bool(_get("emit_log", False)):
        write_jsonl_log(Path(_get("log_file", sys_root / "runtime" / "logs" / "profile_preview.log")), management_log)

    if args.json:
        payload = dict(preview)
        payload["management_log"] = management_log
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print("=== profile block ===")
        print(preview["profile"] or "<empty>")
        print("=== answer preview ===")
        print(preview["answer"] or "<empty>")
        if bool(_get("emit_log", False)):
            print("=== management log ===")
            print(json.dumps(management_log, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
