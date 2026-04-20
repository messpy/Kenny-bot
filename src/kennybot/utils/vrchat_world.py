from __future__ import annotations

import importlib.util
from functools import lru_cache
from pathlib import Path
from typing import Any


@lru_cache(maxsize=1)
def _load_vrchat_staff_bot_class() -> type[Any]:
    module_path = Path(__file__).resolve().parents[2] / "api" / "vrchat" / "getVrcWorld.py"
    spec = importlib.util.spec_from_file_location("kennybot_vrchat_world", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"VRChat module could not be loaded: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    staff_bot = getattr(module, "VRChatStaffBot", None)
    if staff_bot is None:
        raise RuntimeError("VRChatStaffBot が見つかりませんでした。")
    return staff_bot


def search_vrchat_worlds(
    keyword: str,
    count: int,
    author: str | None = None,
    tag: str | None = None,
) -> tuple[Any, list[dict[str, Any]]]:
    staff_bot_class = _load_vrchat_staff_bot_class()
    client = staff_bot_class()
    if not client.login():
        raise RuntimeError(
            "VRChat API のログインに失敗しました。api/vrchat 側の既存認証情報を確認してください。"
        )
    results = client.search_worlds(keyword, n=count, author=author, tag=tag)
    if results is None:
        raise RuntimeError("VRChat API の検索に失敗しました。")
    return client, list(results)


def format_vrchat_world_lines(
    formatter: Any,
    worlds: list[dict[str, Any]],
) -> list[str]:
    lines: list[str] = []
    for index, world in enumerate(worlds, start=1):
        name = str(world.get("name") or "unknown")
        author = str(world.get("authorName") or "unknown")
        capacity = int(world.get("capacity") or 0)
        occupants = int(world.get("occupants") or 0)
        world_id = str(world.get("id") or "-")
        tags = formatter.format_tags(world.get("tags", []))
        unity_packages = world.get("unityPackages", [])
        is_android = any(
            isinstance(package, dict) and package.get("platform") == "android"
            for package in unity_packages
        )
        lines.extend(
            [
                f"**{index}. {name}**",
                f"作者: {author}",
                f"人数: {occupants}/{capacity} | Quest対応: {'✅' if is_android else '❌'}",
                f"タグ: {tags}",
                f"URL: https://vrchat.com/home/world/{world_id}",
                "",
            ]
        )
    return lines[:-1] if lines else lines


def format_vrchat_world_text(
    formatter: Any,
    worlds: list[dict[str, Any]],
    *,
    max_len: int = 8000,
) -> str:
    text = "\n".join(format_vrchat_world_lines(formatter, worlds))
    if max_len > 0 and len(text) > max_len:
        return text[:max_len] + "\n...(省略)..."
    return text
