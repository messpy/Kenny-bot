#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys_root = Path(__file__).resolve().parent.parent
if str(sys_root) not in sys.path:
    sys.path.insert(0, str(sys_root))

from src.kennybot.utils.profile_preview_api import build_profile_preview_response, parse_json_payload


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


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    payload = parse_json_payload(args.input_json) if args.input_json else {}
    response = build_profile_preview_response(root=sys_root, payload=payload, args=args)

    if args.json:
        print(json.dumps(response, ensure_ascii=False))
    else:
        print("=== profile block ===")
        print(response["profile"] or "<empty>")
        print("=== answer preview ===")
        print(response["answer"] or "<empty>")
        if bool(payload.get("emit_log", False) or getattr(args, "emit_log", False)):
            print("=== management log ===")
            print(json.dumps(response["management_log"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
