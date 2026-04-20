# ai/runner.py
# Ollama ランナー（低レイヤ実行）

import asyncio
import subprocess
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class OllamaConfig:
    """Ollama 実行設定"""
    model: str = "gemma2:2b"
    timeout_sec: int = 120


class OllamaRunner:
    """Ollama モデル実行クラス"""

    def __init__(self, config: OllamaConfig, *, debug: bool = False):
        self.config = config
        self.debug = debug

    def run_sync(self, prompt: str, *, model: Optional[str] = None) -> str:
        """同期実行"""
        m = model or self.config.model
        proc = subprocess.Popen(
            ["ollama", "run", m],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout, stderr = proc.communicate(prompt.encode("utf-8"))

        if proc.returncode != 0:
            raise RuntimeError(
                f"ollama run failed (model={m}, rc={proc.returncode}): "
                f"{stderr.decode('utf-8', errors='replace')}"
            )
        return stdout.decode("utf-8", errors="replace").strip()

    async def run_async(self, prompt: str, *, model: Optional[str] = None) -> str:
        """非同期実行"""
        m = model or self.config.model
        proc = await asyncio.create_subprocess_exec(
            "ollama",
            "run",
            m,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(prompt.encode("utf-8")),
                timeout=self.config.timeout_sec,
            )
        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError(
                f"ollama timeout (model={m}, timeout={self.config.timeout_sec}s)"
            )

        if proc.returncode != 0:
            raise RuntimeError(
                f"ollama run failed (model={m}, rc={proc.returncode}): "
                f"{stderr.decode('utf-8', errors='replace')}"
            )
        return stdout.decode("utf-8", errors="replace").strip()
