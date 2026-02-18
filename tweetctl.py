#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests
from dotenv import load_dotenv


# =========================================================
# .env 読み込み（実行場所に依存させない）
# =========================================================
DOTENV_PATH = Path(__file__).resolve().with_name(".env")
load_dotenv(DOTENV_PATH if DOTENV_PATH.exists() else None)


def must_env(name: str) -> str:
    v = os.getenv(name)
    if not v:
        raise SystemExit(f"env missing: {name} (set in .env or export it)")
    return v


# =========================================================
# レート制限表示（JST）
# =========================================================
def show_rate_limit(label: str, r: requests.Response) -> None:
    print(f"[429] {label} のレート制限に達しました")
    limit = r.headers.get("x-rate-limit-limit")
    remain = r.headers.get("x-rate-limit-remaining")
    reset = r.headers.get("x-rate-limit-reset")

    print(" limit     :", limit)
    print(" remaining :", remain)
    print(" reset(UTC):", reset)

    if reset:
        ts = datetime.fromtimestamp(int(reset), tz=timezone.utc)
        jst = ts.astimezone(timezone(timedelta(hours=9)))
        print(" reset(JST):", jst.strftime("%Y-%m-%d %H:%M:%S"))


# =========================================================
# X API: READ
# =========================================================
def x_get(url: str, bearer: str, params: dict) -> dict:
    headers = {"Authorization": f"Bearer {bearer}"}
    r = requests.get(url, headers=headers, params=params, timeout=15)

    if r.status_code == 429:
        show_rate_limit(url, r)
        raise SystemExit("レート制限中のため終了します。")

    if not r.ok:
        raise SystemExit(f"HTTP ERROR: {r.status_code} {r.text}")

    return r.json()


def get_profile(user_id: str, bearer: str, detail: bool) -> dict:
    fields = (
        "id,username,name,description,profile_image_url,created_at,public_metrics"
        if detail else
        "id,username,name"
    )
    url = f"https://api.x.com/2/users/{user_id}"
    data = x_get(url, bearer, {"user.fields": fields})
    return data.get("data", {})


def get_latest_tweet(user_id: str, bearer: str) -> Optional[dict]:
    url = f"https://api.x.com/2/users/{user_id}/tweets"
    data = x_get(url, bearer, {
        "max_results": 5,
        "tweet.fields": "id,text,created_at,public_metrics",
    })
    arr = data.get("data", [])
    return arr[0] if arr else None


# =========================================================
# 表情（整形スタイル）
# =========================================================
def format_profile(p: dict, *, style: str, detail: bool) -> str:
    u = p.get("username", "")
    n = p.get("name", "")
    pid = p.get("id", "")

    if style == "raw":
        return str(p)

    if style == "compact":
        # 1～2行に収める
        base = f"@{u} / {n}"
        return base if not detail else f"{base} (id={pid})"

    # default
    lines = []
    lines.append("=== プロフィール ===")
    lines.append(f"@{u} / {n}")
    if detail:
        lines.append(f"id        : {pid}")
        desc = p.get("description") or ""
        lines.append(f"bio       : {desc}")
        lines.append(f"icon      : {p.get('profile_image_url')}")
        lines.append(f"created   : {p.get('created_at')}")
        m = p.get("public_metrics") or {}
        lines.append(f"followers : {m.get('followers_count', 'N/A')}")
        lines.append(f"following : {m.get('following_count', 'N/A')}")
        lines.append(f"tweet_cnt : {m.get('tweet_count', 'N/A')}")
        lines.append(f"listed    : {m.get('listed_count', 'N/A')}")
    return "\n".join(lines)


def format_tweet(t: dict, *, username: str, style: str, detail: bool) -> str:
    tid = t.get("id", "")
    url = f"https://x.com/{username}/status/{tid}"

    if style == "raw":
        return str(t)

    if style == "compact":
        text = (t.get("text") or "").replace("\n", " ")
        if len(text) > 80:
            text = text[:80] + "..."
        return f"{url}\n{text}"

    # default
    lines = []
    lines.append("=== 最新ツイート ===")
    lines.append(f"url       : {url}")
    lines.append(f"created   : {t.get('created_at')}")
    if detail:
        m = t.get("public_metrics") or {}
        lines.append(
            "metrics   : "
            f"♥ {m.get('like_count', 0)} / "
            f"RT {m.get('retweet_count', 0)} / "
            f"返信 {m.get('reply_count', 0)} / "
            f"引用 {m.get('quote_count', 0)}"
        )
    lines.append("------ text ------")
    lines.append(t.get("text", ""))
    return "\n".join(lines)


def wrap_for_discord(text: str, wrap: str) -> str:
    if wrap == "none":
        return text
    if wrap == "codeblock":
        return f"```\n{text}\n```"
    raise SystemExit(f"invalid wrap: {wrap}")


def send_webhook(webhook_url: str, text: str, wrap: str) -> None:
    payload = {"content": wrap_for_discord(text, wrap)}
    r = requests.post(webhook_url, json=payload, timeout=15)
    if not r.ok:
        raise SystemExit(f"Webhook ERROR: {r.status_code} {r.text}")


# =========================================================
# Xへ投稿（WRITE）
# =========================================================
def post_tweet(text: str, image_path: Optional[str], *, dry_run: bool) -> str:
    consumer_key = must_env("X_CONSUMER_KEY")
    consumer_secret = must_env("X_CONSUMER_SECRET")
    access_token = must_env("X_ACCESS_TOKEN")
    access_token_secret = must_env("X_ACCESS_TOKEN_SECRET")

    if dry_run:
        return "dry-run"

    try:
        import tweepy
    except Exception as e:
        raise SystemExit(f"tweepy missing. run: uv add tweepy  (detail: {e})")

    auth = tweepy.OAuth1UserHandler(
        consumer_key,
        consumer_secret,
        access_token,
        access_token_secret,
    )
    api_v1 = tweepy.API(auth)
    client_v2 = tweepy.Client(
        consumer_key=consumer_key,
        consumer_secret=consumer_secret,
        access_token=access_token,
        access_token_secret=access_token_secret,
    )

    media_ids = None
    if image_path:
        p = Path(image_path).expanduser()
        if not p.exists():
            raise SystemExit(f"image not found: {p}")
        media = api_v1.media_upload(filename=str(p))
        media_ids = [media.media_id]

    res = client_v2.create_tweet(text=text, media_ids=media_ids)
    tid = None
    try:
        tid = res.data.get("id")
    except Exception:
        pass
    return str(tid) if tid else "unknown"


# =========================================================
# CLI
# =========================================================
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(add_help=True)

    sub = p.add_subparsers(dest="cmd")

    # read (default)
    p.add_argument("--style", choices=["default", "compact", "raw"], default="default",
                   help="出力の表情（整形スタイル）")
    p.add_argument("--wrap", choices=["codeblock", "none"], default="codeblock",
                   help="Discord送信時の包み方")
    p.add_argument("-i", "--detail", action="store_true", help="詳細表示")
    p.add_argument("--no-webhook", action="store_true", help="Discordへ送らない（標準出力のみ）")

    # post
    p_post = sub.add_parser("post")
    p_post.add_argument("--text", required=True)
    p_post.add_argument("--dry-run", action="store_true")
    p_post.add_argument("--notify", action="store_true", help="投稿結果をDiscordへ通知")
    p_post.add_argument("--wrap", choices=["codeblock", "none"], default="codeblock")
    p_post.add_argument("--style", choices=["default", "compact", "raw"], default="default")

    # postimg
    p_postimg = sub.add_parser("postimg")
    p_postimg.add_argument("--text", required=True)
    p_postimg.add_argument("--image", required=True)
    p_postimg.add_argument("--dry-run", action="store_true")
    p_postimg.add_argument("--notify", action="store_true", help="投稿結果をDiscordへ通知")
    p_postimg.add_argument("--wrap", choices=["codeblock", "none"], default="codeblock")
    p_postimg.add_argument("--style", choices=["default", "compact", "raw"], default="default")

    # profile only
    p_profile = sub.add_parser("profile")
    p_profile.add_argument("--style", choices=["default", "compact", "raw"], default="default")
    p_profile.add_argument("--wrap", choices=["codeblock", "none"], default="codeblock")
    p_profile.add_argument("-i", "--detail", action="store_true")
    p_profile.add_argument("--no-webhook", action="store_true")

    # tweet only
    p_tweet = sub.add_parser("tweet")
    p_tweet.add_argument("--style", choices=["default", "compact", "raw"], default="default")
    p_tweet.add_argument("--wrap", choices=["codeblock", "none"], default="codeblock")
    p_tweet.add_argument("-i", "--detail", action="store_true")
    p_tweet.add_argument("--no-webhook", action="store_true")

    return p.parse_args()


def main() -> int:
    args = parse_args()

    # ===== env必須（全て.env方式）=====
    user_id = must_env("X_USER_ID")
    username = must_env("X_USERNAME")
    bearer = must_env("X_BEARER")
    webhook = must_env("DISCORD_WEBHOOK_URL")

    # ===== 投稿モード =====
    if args.cmd in ("post", "postimg"):
        image = getattr(args, "image", None)
        tid = post_tweet(args.text, image, dry_run=args.dry_run)

        msg_lines = []
        msg_lines.append("=== 投稿 ===")
        msg_lines.append(f"result    : {tid}")
        msg_lines.append(f"text      : {args.text}")
        if image:
            msg_lines.append(f"image     : {image}")
        msg = "\n".join(msg_lines)

        print(msg)

        if args.notify:
            send_webhook(webhook, msg, wrap=args.wrap)

        return 0

    # ===== READ モード =====
    style = getattr(args, "style", "default")
    wrap = getattr(args, "wrap", "codeblock")
    detail = getattr(args, "detail", False)
    no_webhook = getattr(args, "no_webhook", False)

    blocks = []

    if args.cmd in (None, "profile"):
        p = get_profile(user_id, bearer, detail=detail)
        blocks.append(format_profile(p, style=style, detail=detail))

    if args.cmd in (None, "tweet"):
        t = get_latest_tweet(user_id, bearer)
        blocks.append(format_tweet(t, username=username, style=style, detail=detail) if t else "最新ツイートが見つかりませんでした。")

    output = "\n\n".join(blocks).rstrip()
    print(output)

    if not no_webhook:
        send_webhook(webhook, output, wrap=wrap)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
