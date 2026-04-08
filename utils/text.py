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
    """検索クエリ生成が必要かどうかを判定"""
    t = text or ""
    return ("教えて" in t) or ("調べて" in t) or ("ニュース" in t)
