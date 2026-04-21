# utils/text.py
# テキスト処理（正規化、キーワード判定など）

import re
import unicodedata


# =========================
# 正規表現
# =========================
MENTION_RE = re.compile(r"<@!?\d+>")
ROLE_MENTION_RE = re.compile(r"<@&\d+>")
CHANNEL_MENTION_RE = re.compile(r"<#\d+>")
ANSI_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")


# =========================
# テキスト正規化
# =========================
def strip_ansi_and_ctrl(s: str) -> str:
    """ANSI エスケープと制御文字を除去"""
    s = ANSI_RE.sub("", s)
    out = []
    for ch in s:
        o = ord(ch)
        if o < 32 and ch not in ("\n", "\r", "\t"):
            continue
        out.append(ch)
    return "".join(out)


def normalize_user_text(raw: str) -> str:
    """メンション、ロール、チャンネルタグを除去してテキストを正規化"""
    s = raw or ""
    s = MENTION_RE.sub("", s)
    s = ROLE_MENTION_RE.sub("", s)
    s = CHANNEL_MENTION_RE.sub("", s)
    s = s.strip()
    return s


def normalize_keyword_match_text(raw: str) -> str:
    """キーワード一致用に文字種の揺れを小さくする"""
    text = unicodedata.normalize("NFKC", raw or "").casefold()
    out: list[str] = []
    for ch in text:
        code = ord(ch)
        # カタカナをひらがなに寄せる
        if 0x30A1 <= code <= 0x30F6:
            out.append(chr(code - 0x60))
        else:
            out.append(ch)
    return "".join(out)


# =========================
# 意図判定
# =========================
def is_search_intent(text: str) -> bool:
    """一般的な検索・調査意図を判定する。web 検索とは限らない。"""
    t = normalize_keyword_match_text(text or "")
    keywords = (
        "調べて",
        "しらべて",
        "検索",
        "検索して",
        "探して",
        "リサーチ",
    )
    return any(keyword in t for keyword in keywords)


def is_current_info_intent(text: str) -> bool:
    """最新情報や今日の情報を欲しがっているかを判定"""
    t = normalize_keyword_match_text(text or "")
    keywords = (
        "今日",
        "きょう",
        "現在",
        "今",
        "いま",
        "今の",
        "今週",
        "来週",
        "先週",
        "今月",
        "来月",
        "先月",
        "この時期",
        "時事",
        "時事ネタ",
        "話題",
        "最新",
        "最近",
        "ニュース",
        "天気",
        "気温",
        "季節",
        "しき",
        "時期",
        "旬",
        "服装",
        "気候",
        "速報",
        "トレンド",
        "株価",
        "レート",
        "流行",
        "日時",
        "何日",
        "何時",
        "何曜日",
        "いつ",
    )
    return any(keyword in t for keyword in keywords)


def looks_like_web_search_artifact(text: str) -> bool:
    """Web 検索結果の要約っぽい本文かどうかを判定する。

    会話履歴や semantic memory に混ぜたくない生成物を弾くための
    かなり保守的な判定にする。
    """
    raw = strip_ansi_and_ctrl(text or "").strip()
    if not raw:
        return False

    normalized = normalize_keyword_match_text(raw)
    if "web検索" in normalized and (
        "失敗" in normalized or "エラー" in normalized or "実行に失敗" in normalized
    ):
        return True

    summary_markers = (
        "全体要約",
        "補足",
        "参考URL",
        "出典元",
        "検索結果の要約",
    )
    if not any(marker in raw for marker in summary_markers):
        return False

    return "http://" in raw or "https://" in raw or "](" in raw
