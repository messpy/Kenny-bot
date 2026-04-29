#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

sys_root = Path(__file__).resolve().parent.parent
if str(sys_root) not in sys.path:
    sys.path.insert(0, str(sys_root))

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="One-command local profile preview with Ollama")
    parser.add_argument("--root", type=Path, default=sys_root)
    parser.add_argument("--host", type=str, default="127.0.0.1")
    parser.add_argument("--port", type=int, default=11435)
    parser.add_argument("--ollama-model", type=str, default="llama3.2:1b")
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
    parser.add_argument("--json", action="store_true", help="Print JSON only")
    parser.add_argument("--wait-seconds", type=float, default=30.0)
    parser.add_argument("--request-timeout-seconds", type=float, default=180.0)
    parser.add_argument("--pull-timeout-seconds", type=float, default=1800.0)
    return parser


def _env_with_ollama_host(host: str, port: int) -> dict[str, str]:
    env = os.environ.copy()
    env["OLLAMA_HOST"] = f"{host}:{port}"
    return env


def _wait_for_ollama_ready(env: dict[str, str], timeout: float) -> None:
    deadline = time.time() + max(1.0, float(timeout))
    last_error: subprocess.CalledProcessError | None = None
    while time.time() < deadline:
        try:
            subprocess.run(
                ["ollama", "list"],
                env=env,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
        except subprocess.CalledProcessError as exc:
            last_error = exc
            time.sleep(0.5)
    if last_error is not None:
        raise RuntimeError("ollama server did not become ready") from last_error
    raise RuntimeError("ollama server did not become ready")


def _run_profile_preview(args: argparse.Namespace, env: dict[str, str]) -> int:
    cmd = [
        sys.executable,
        "bin/profile_preview_call.py",
        "--root",
        str(args.root),
        "--guild-id",
        str(args.guild_id),
        "--channel-id",
        str(args.channel_id),
        "--scope",
        str(args.scope),
        "--question",
        str(args.question),
        "--limit",
        str(args.limit),
        "--max-chars",
        str(args.max_chars),
        "--log-file",
        str(args.log_file),
        "--ollama-host",
        f"http://{args.host}:{args.port}",
        "--ollama-model",
        str(args.ollama_model),
        "--request-timeout-seconds",
        str(args.request_timeout_seconds),
    ]
    if args.json:
        cmd.insert(2, "--json")
    if args.emit_log:
        cmd.extend(["--emit-log"])
    logger.info("running profile preview via %s", " ".join(cmd))
    completed = subprocess.run(cmd, cwd=str(args.root), env=env)
    return completed.returncode


def main() -> int:
    args = _build_parser().parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    env = _env_with_ollama_host(args.host, args.port)
    serve_proc = subprocess.Popen(
        ["ollama", "serve"],
        env=env,
        cwd=str(args.root),
    )
    try:
        logger.info("started ollama serve on %s:%s", args.host, args.port)
        _wait_for_ollama_ready(env, args.wait_seconds)
        logger.info("ollama is ready; pulling model %s", args.ollama_model)
        subprocess.run(
            ["ollama", "pull", args.ollama_model],
            env=env,
            cwd=str(args.root),
            check=True,
            timeout=max(1.0, float(args.pull_timeout_seconds)),
        )
        return _run_profile_preview(args, env)
    except KeyboardInterrupt:
        return 130
    finally:
        if serve_proc.poll() is None:
            serve_proc.terminate()
            try:
                serve_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                serve_proc.kill()
                serve_proc.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
