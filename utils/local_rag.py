from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class RagChunk:
    source: str
    title: str
    body: str


def _tokenize(text: str) -> list[str]:
    text = (text or "").lower()
    parts = re.split(r"[\s\r\n\t:：、。・,./()（）\[\]{}!?！？]+", text)
    return [p for p in parts if p]


def _split_markdown_sections(text: str) -> list[RagChunk]:
    chunks: list[RagChunk] = []
    cur_title = "README"
    cur_lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("#"):
            if cur_lines:
                body = "\n".join(cur_lines).strip()
                if body:
                    chunks.append(RagChunk(source="README", title=cur_title, body=body))
            cur_title = line.lstrip("#").strip() or "README"
            cur_lines = []
        else:
            cur_lines.append(line)
    if cur_lines:
        body = "\n".join(cur_lines).strip()
        if body:
            chunks.append(RagChunk(source="README", title=cur_title, body=body))
    return chunks


def _static_chunks() -> list[RagChunk]:
    return [
        RagChunk(
            source="BOT",
            title="会話",
            body=(
                "Bot はメンションや Bot への返信で AI 応答できます。"
                "DM でもそのまま AI 会話できます。"
                "会話時は直近100件のメッセージ履歴を参照します。"
            ),
        ),
        RagChunk(
            source="BOT",
            title="音声",
            body=(
                "VOICEVOX 読み上げは /tts_join で開始します。"
                "コマンドを実行した人がいる通話に参加し、そのチャンネルを読み上げ対象にします。"
                "議事録は /minutes_start で開始し、同じチャンネルに文字起こしや結果を返します。"
            ),
        ),
        RagChunk(
            source="BOT",
            title="ゲーム",
            body=(
                "人狼役職配布は /game mode:人狼役職配布 です。"
                "人狼だけが DM のリアクションで襲撃対象を選びます。"
                "あいうえおバトルは /game mode:あいうえおバトル で、1人から開始できます。"
                "お題は DM で送信し、ひらがなのみ7文字以下、小文字や濁点や半濁点やーも使えます。"
            ),
        ),
    ]


class LocalRAG:
    def __init__(self, root: Path):
        self.root = root

    def _load_chunks(self) -> list[RagChunk]:
        chunks = _static_chunks()
        readme = self.root / "README.md"
        if readme.exists():
            try:
                chunks.extend(_split_markdown_sections(readme.read_text(encoding="utf-8", errors="ignore")))
            except Exception:
                pass
        return chunks

    def retrieve(self, query: str, limit: int = 4) -> list[RagChunk]:
        tokens = set(_tokenize(query))
        if not tokens:
            return self._load_chunks()[:limit]

        scored: list[tuple[int, RagChunk]] = []
        for chunk in self._load_chunks():
            hay = f"{chunk.title}\n{chunk.body}".lower()
            score = 0
            for token in tokens:
                if token in hay:
                    score += 2
            if query.lower() in hay:
                score += 4
            if score > 0:
                scored.append((score, chunk))

        scored.sort(key=lambda x: x[0], reverse=True)
        top = [chunk for _, chunk in scored[:limit]]
        if top:
            return top
        return self._load_chunks()[:limit]

