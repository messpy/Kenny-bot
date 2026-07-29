from __future__ import annotations

from typing import Any

from vrchatapi.api import worlds_api

from src.kennybot.utils.vrchat_user import authenticated_vrchat_api_client


def _get_field(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        if name in item:
            return item[name]
        parts = name.split("_")
        camel_name = parts[0] + "".join(part.capitalize() for part in parts[1:])
        return item.get(camel_name, default)
    return getattr(item, name, default)


def _format_tags(tags: Any) -> str:
    if not isinstance(tags, list):
        return "-"
    visible_tags = [
        str(tag)
        for tag in tags
        if isinstance(tag, str)
        and tag
        and not tag.startswith("admin_")
        and not tag.startswith("system_")
    ]
    return ", ".join(visible_tags[:12]) if visible_tags else "-"


def search_vrchat_worlds(
    keyword: str,
    count: int,
    author: str | None = None,
    tag: str | None = None,
    *,
    totp_code: str | None = None,
    email_code: str | None = None,
) -> tuple[None, list[Any]]:
    search_keyword = (keyword or "").strip()
    if not search_keyword:
        raise ValueError("検索キーワードを指定してください。")

    limit = max(1, min(int(count), 10))
    request_count = min(100, max(limit, limit * 5 if author else limit))
    params: dict[str, Any] = {
        "search": search_keyword,
        "n": request_count,
    }
    tag_value = (tag or "").strip()
    if tag_value:
        params["tag"] = tag_value

    with authenticated_vrchat_api_client(totp_code=totp_code, email_code=email_code) as api_client:
        worlds = list(worlds_api.WorldsApi(api_client).search_worlds(**params) or [])

    author_query = (author or "").strip().lower()
    if author_query:
        worlds = [
            world
            for world in worlds
            if author_query in str(_get_field(world, "author_name", "") or "").lower()
        ]
    return None, worlds[:limit]


def format_vrchat_world_lines(
    formatter: Any,
    worlds: list[Any],
) -> list[str]:
    lines: list[str] = []
    for index, world in enumerate(worlds, start=1):
        name = str(_get_field(world, "name", "unknown") or "unknown")
        author = str(_get_field(world, "author_name", "unknown") or "unknown")
        capacity = int(_get_field(world, "capacity", 0) or 0)
        occupants = int(_get_field(world, "occupants", 0) or 0)
        world_id = str(_get_field(world, "id", "-") or "-")
        tags = _format_tags(_get_field(world, "tags", []))
        unity_packages = _get_field(world, "unity_packages", []) or []
        is_android = any(
            (
                isinstance(package, dict)
                and package.get("platform") == "android"
            )
            or str(getattr(package, "platform", "") or "") == "android"
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
    worlds: list[Any],
    *,
    max_len: int = 8000,
) -> str:
    text = "\n".join(format_vrchat_world_lines(formatter, worlds))
    if max_len > 0 and len(text) > max_len:
        return text[:max_len] + "\n...(省略)..."
    return text
