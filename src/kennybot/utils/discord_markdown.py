from __future__ import annotations


class DiscordMarkdown:
    @staticmethod
    def link(label: str, url: str) -> str:
        clean_label = str(label or "").strip() or "link"
        clean_url = str(url or "").strip().replace("<", "%3C").replace(">", "%3E")
        return f"[{clean_label}](<{clean_url}>)"
