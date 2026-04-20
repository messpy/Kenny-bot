from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from google.auth.transport.requests import Request
from google.oauth2 import service_account
import google.auth


GOOGLE_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
GOOGLE_RECOGNIZE_URL = "https://speech.googleapis.com/v1/speech:recognize"


@dataclass
class GoogleSpeechConfig:
    language_code: str = "ja-JP"
    chunk_seconds: int = 20
    timeout_sec: int = 90
    model: str = ""


class GoogleSpeechClient:
    def __init__(self, config: GoogleSpeechConfig):
        self.config = config

    def transcribe_pcm(
        self,
        pcm: bytes,
        *,
        sample_rate_hz: int = 48000,
        channels: int = 2,
    ) -> str:
        if not pcm:
            return ""

        frame_bytes = channels * 2
        chunk_size = max(frame_bytes, self.config.chunk_seconds * sample_rate_hz * frame_bytes)
        chunk_size -= chunk_size % frame_bytes

        transcripts: list[str] = []
        for start in range(0, len(pcm), chunk_size):
            chunk = pcm[start : start + chunk_size]
            if not chunk:
                continue
            text = self._recognize_chunk(chunk, sample_rate_hz=sample_rate_hz, channels=channels)
            if text:
                transcripts.append(text)
        return " ".join(part.strip() for part in transcripts if part.strip()).strip()

    def _recognize_chunk(
        self,
        pcm: bytes,
        *,
        sample_rate_hz: int,
        channels: int,
    ) -> str:
        creds, quota_project_id = _load_google_credentials()
        creds.refresh(Request())
        token = getattr(creds, "token", None)
        if not token:
            raise RuntimeError("Google アクセストークンを取得できませんでした。")

        config: dict[str, Any] = {
            "encoding": "LINEAR16",
            "sampleRateHertz": sample_rate_hz,
            "audioChannelCount": channels,
            "languageCode": self.config.language_code,
            "enableAutomaticPunctuation": True,
        }
        if self.config.model:
            config["model"] = self.config.model

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        if quota_project_id:
            headers["x-goog-user-project"] = quota_project_id

        payload = {
            "config": config,
            "audio": {
                "content": base64.b64encode(pcm).decode("ascii"),
            },
        }

        with httpx.Client(timeout=self.config.timeout_sec) as client:
            response = client.post(GOOGLE_RECOGNIZE_URL, headers=headers, json=payload)

        if response.status_code >= 400:
            try:
                detail = response.json()
            except Exception:
                detail = response.text
            raise RuntimeError(f"Google Speech-to-Text エラー: {response.status_code} {detail}")

        data = response.json()
        results = data.get("results", [])
        parts: list[str] = []
        for result in results:
            alts = result.get("alternatives") or []
            if not alts:
                continue
            text = str(alts[0].get("transcript", "")).strip()
            if text:
                parts.append(text)
        return " ".join(parts).strip()


def _load_google_credentials():
    service_account_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    service_account_json_b64 = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON_BASE64", "").strip()

    if service_account_json_b64:
        info = json.loads(base64.b64decode(service_account_json_b64).decode("utf-8"))
        creds = service_account.Credentials.from_service_account_info(info, scopes=[GOOGLE_SCOPE])
        project_id = str(info.get("project_id", "")).strip() or os.getenv("GOOGLE_CLOUD_PROJECT", "").strip()
        return creds, project_id

    if service_account_json:
        raw = service_account_json
        possible_path = Path(service_account_json)
        if possible_path.exists():
            raw = possible_path.read_text(encoding="utf-8")
        info = json.loads(raw)
        creds = service_account.Credentials.from_service_account_info(info, scopes=[GOOGLE_SCOPE])
        project_id = str(info.get("project_id", "")).strip() or os.getenv("GOOGLE_CLOUD_PROJECT", "").strip()
        return creds, project_id

    creds, project_id = google.auth.default(scopes=[GOOGLE_SCOPE])
    quota_project_id = getattr(creds, "quota_project_id", None) or project_id or os.getenv("GOOGLE_CLOUD_PROJECT", "").strip()
    return creds, quota_project_id
