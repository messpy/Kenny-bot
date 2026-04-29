from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from src.kennybot.utils.paths import RUNTIME_HISTORY_DIR, RUNTIME_STATE_DIR, RUNTIME_TMP_DIR
from src.kennybot.utils.time import now_jst


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class CodexJobHandle:
    job_id: str
    branch_name: str
    worktree_dir: Path
    prompt_path: Path
    state_path: Path
    stdout_path: Path
    stderr_path: Path
    last_message_path: Path


class CodexJobManager:
    def __init__(self, root: Path, codex_bin: str | None = None) -> None:
        self.root = Path(root)
        self.codex_bin = codex_bin or self._find_codex_bin()
        self.state_dir = self.root / RUNTIME_STATE_DIR / "codex_jobs"
        self.history_dir = self.root / RUNTIME_HISTORY_DIR / "codex_jobs"
        self.worktree_root = self.root / RUNTIME_TMP_DIR / "codex_jobs"

    @staticmethod
    def _find_codex_bin() -> str | None:
        candidates = [
            shutil.which("codex"),
            str(Path.home() / ".npm-global/bin/codex"),
        ]
        for candidate in candidates:
            if candidate and Path(candidate).exists():
                return candidate
        return None

    def is_available(self) -> bool:
        return bool(self.codex_bin and Path(self.codex_bin).exists())

    @staticmethod
    def _now() -> datetime:
        return now_jst()

    @classmethod
    def _job_id(cls) -> str:
        return f"{cls._now().strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:6]}"

    @staticmethod
    def _slugify(text: str, *, limit: int = 24) -> str:
        cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in (text or "").strip())
        cleaned = "-".join(part for part in cleaned.split("-") if part)
        if not cleaned:
            return "repair"
        return cleaned[:limit].strip("-") or "repair"

    @classmethod
    def build_branch_name(cls, *, issue: str, target_area: str, job_id: str) -> str:
        scope = cls._slugify(target_area or issue, limit=24)
        suffix = job_id.replace("-", "")[-10:]
        return f"codex/{scope}-{suffix}"

    @staticmethod
    def build_exec_prompt(
        *,
        issue: str,
        previous_prompt: str,
        previous_response: str,
        target_area: str,
        planned_fix: str,
        branch_name: str,
        job_id: str,
    ) -> str:
        return "\n".join(
            [
                "Kenny-bot の Codex 修繕ジョブです。",
                "この作業ツリーは修繕専用の worktree です。現在のブランチ上で直接修正してください。",
                "必ずコードを確認してから最小限の修正を行い、関連テストを実行してください。",
                "ユーザー向けの自然文ではなく、実際のコード修正と検証を優先してください。",
                "既存の未コミット変更を壊さないこと。不要なファイルは触らないこと。commit はまだ作らないこと。",
                "",
                f"[job_id]\n{job_id}",
                "",
                f"[branch]\n{branch_name}",
                "",
                f"[issue]\n{issue or '不明'}",
                "",
                f"[target_area]\n{target_area or '一般的な応答品質'}",
                "",
                f"[planned_fix]\n{planned_fix or 'ユーザー指摘に基づいて修正する'}",
                "",
                f"[previous_user_prompt]\n{previous_prompt or '取得できませんでした'}",
                "",
                f"[previous_bot_response]\n{previous_response or '取得できませんでした'}",
                "",
                "[required_output]",
                "- 何を直したか",
                "- 実行したテスト",
                "- 残課題があれば短く",
            ]
        )

    def _write_state(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    async def start_job(
        self,
        *,
        issue: str,
        previous_prompt: str,
        previous_response: str,
        target_area: str,
        planned_fix: str,
    ) -> tuple[CodexJobHandle, asyncio.Task[None]]:
        if not self.is_available():
            raise RuntimeError("codex CLI is not available")

        job_id = self._job_id()
        branch_name = self.build_branch_name(issue=issue, target_area=target_area, job_id=job_id)
        worktree_dir = self.worktree_root / job_id
        prompt_path = self.history_dir / f"{job_id}.prompt.txt"
        state_path = self.state_dir / f"{job_id}.json"
        stdout_path = self.history_dir / f"{job_id}.stdout.jsonl"
        stderr_path = self.history_dir / f"{job_id}.stderr.log"
        last_message_path = self.history_dir / f"{job_id}.last.txt"
        handle = CodexJobHandle(
            job_id=job_id,
            branch_name=branch_name,
            worktree_dir=worktree_dir,
            prompt_path=prompt_path,
            state_path=state_path,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            last_message_path=last_message_path,
        )
        prompt_text = self.build_exec_prompt(
            issue=issue,
            previous_prompt=previous_prompt,
            previous_response=previous_response,
            target_area=target_area,
            planned_fix=planned_fix,
            branch_name=branch_name,
            job_id=job_id,
        )
        self.history_dir.mkdir(parents=True, exist_ok=True)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.worktree_root.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text(prompt_text, encoding="utf-8")
        self._write_state(
            state_path,
            {
                "job_id": job_id,
                "status": "preparing",
                "branch_name": branch_name,
                "worktree_dir": str(worktree_dir),
                "prompt_path": str(prompt_path),
                "created_at": self._now().isoformat(timespec="seconds"),
            },
        )

        try:
            subprocess.run(
                ["git", "-C", str(self.root), "worktree", "add", "-b", branch_name, str(worktree_dir), "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            self._write_state(
                state_path,
                {
                    "job_id": job_id,
                    "status": "failed",
                    "branch_name": branch_name,
                    "worktree_dir": str(worktree_dir),
                    "error": (exc.stderr or exc.stdout or str(exc)).strip(),
                    "failed_at": self._now().isoformat(timespec="seconds"),
                },
            )
            raise RuntimeError((exc.stderr or exc.stdout or str(exc)).strip()) from exc

        stdout_fp = stdout_path.open("w", encoding="utf-8")
        stderr_fp = stderr_path.open("w", encoding="utf-8")
        try:
            proc = await asyncio.create_subprocess_exec(
                str(self.codex_bin),
                "exec",
                "--cd",
                str(worktree_dir),
                "--sandbox",
                "workspace-write",
                "-a",
                "never",
                "--json",
                "--output-last-message",
                str(last_message_path),
                "-",
                stdin=asyncio.subprocess.PIPE,
                stdout=stdout_fp,
                stderr=stderr_fp,
            )
        except Exception:
            stdout_fp.close()
            stderr_fp.close()
            self._write_state(
                state_path,
                {
                    "job_id": job_id,
                    "status": "failed",
                    "branch_name": branch_name,
                    "worktree_dir": str(worktree_dir),
                    "error": "failed to start codex exec",
                    "failed_at": self._now().isoformat(timespec="seconds"),
                },
            )
            raise

        if proc.stdin is not None:
            proc.stdin.write(prompt_text.encode("utf-8"))
            await proc.stdin.drain()
            proc.stdin.close()

        self._write_state(
            state_path,
            {
                "job_id": job_id,
                "status": "running",
                "branch_name": branch_name,
                "worktree_dir": str(worktree_dir),
                "prompt_path": str(prompt_path),
                "stdout_path": str(stdout_path),
                "stderr_path": str(stderr_path),
                "last_message_path": str(last_message_path),
                "pid": proc.pid,
                "started_at": self._now().isoformat(timespec="seconds"),
            },
        )
        monitor_task = asyncio.create_task(
            self._monitor_job(proc=proc, handle=handle, stdout_fp=stdout_fp, stderr_fp=stderr_fp)
        )
        return handle, monitor_task

    async def _monitor_job(
        self,
        *,
        proc: asyncio.subprocess.Process,
        handle: CodexJobHandle,
        stdout_fp: Any,
        stderr_fp: Any,
    ) -> None:
        try:
            exit_code = await proc.wait()
        finally:
            stdout_fp.close()
            stderr_fp.close()

        worktree_status = await asyncio.to_thread(
            subprocess.run,
            ["git", "-C", str(handle.worktree_dir), "status", "--short"],
            check=False,
            capture_output=True,
            text=True,
        )
        changed_files = [
            line.strip()
            for line in (worktree_status.stdout or "").splitlines()
            if line.strip()
        ]
        final_message = ""
        if handle.last_message_path.exists():
            try:
                final_message = handle.last_message_path.read_text(encoding="utf-8").strip()
            except Exception:
                logger.exception("Failed to read Codex last message")

        self._write_state(
            handle.state_path,
            {
                "job_id": handle.job_id,
                "status": "completed" if exit_code == 0 else "failed",
                "branch_name": handle.branch_name,
                "worktree_dir": str(handle.worktree_dir),
                "prompt_path": str(handle.prompt_path),
                "stdout_path": str(handle.stdout_path),
                "stderr_path": str(handle.stderr_path),
                "last_message_path": str(handle.last_message_path),
                "exit_code": exit_code,
                "changed_files": changed_files[:100],
                "has_diff": bool(changed_files),
                "finished_at": self._now().isoformat(timespec="seconds"),
                "final_message": final_message[:4000],
            },
        )
