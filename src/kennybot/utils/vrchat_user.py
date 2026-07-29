from __future__ import annotations

import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from http.cookiejar import MozillaCookieJar
from pathlib import Path
from typing import Any, Iterator

import vrchatapi
from vrchatapi.api import authentication_api, users_api
from vrchatapi.exceptions import UnauthorizedException
from vrchatapi.models.two_factor_auth_code import TwoFactorAuthCode
from vrchatapi.models.two_factor_email_code import TwoFactorEmailCode

from src.kennybot.utils.paths import RUNTIME_STATE_DIR


USER_ID_RE = re.compile(r"\b(usr_[0-9a-fA-F-]{36})\b")
DEFAULT_COOKIE_PATH = RUNTIME_STATE_DIR / "vrchat" / "auth_cookies.txt"


class VRChatAuthError(RuntimeError):
    pass


class VRChatTwoFactorRequired(VRChatAuthError):
    def __init__(self, method: str) -> None:
        self.method = method
        label = "email_code" if method == "email" else "totp_code"
        super().__init__(f"VRChat 2FA が必要です。管理者が `{label}` を指定して再実行してください。")


@dataclass(frozen=True)
class VRChatUserLookup:
    user: Any
    user_id: str
    cookie_saved: bool


def extract_vrchat_user_id(text: str) -> str:
    match = USER_ID_RE.search((text or "").strip())
    if not match:
        raise ValueError("VRChat ユーザーURLまたは usr_ から始まるユーザーIDを指定してください。")
    return match.group(1)


def _cookie_path() -> Path:
    raw = os.getenv("VRCHAT_COOKIE_PATH", "").strip()
    return Path(raw) if raw else DEFAULT_COOKIE_PATH


def _load_cookie_jar(path: Path) -> MozillaCookieJar:
    path.parent.mkdir(parents=True, exist_ok=True)
    jar = MozillaCookieJar(str(path))
    if path.exists():
        jar.load(ignore_discard=True, ignore_expires=False)
    return jar


def _save_cookie_jar(jar: MozillaCookieJar, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    jar.save(ignore_discard=True, ignore_expires=True)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _credentials() -> tuple[str, str]:
    username = os.getenv("VRCHAT_USERNAME", "").strip()
    password = os.getenv("VRCHAT_PASSWORD", "")
    if not username or not password:
        raise VRChatAuthError(".env に VRCHAT_USERNAME と VRCHAT_PASSWORD を設定してください。")
    return username, password


def _two_factor_method(error: UnauthorizedException) -> str | None:
    reason = str(getattr(error, "reason", "") or "")
    body = str(getattr(error, "body", "") or "")
    text = f"{reason}\n{body}"
    if "Email 2 Factor Authentication" in text or "emailOtp" in text:
        return "email"
    if "2 Factor Authentication" in text or "totp" in text or "otp" in text:
        return "totp"
    return None


def _ensure_authenticated(
    auth_api: authentication_api.AuthenticationApi,
    *,
    totp_code: str | None,
    email_code: str | None,
) -> None:
    try:
        auth_api.get_current_user()
        return
    except UnauthorizedException as exc:
        method = _two_factor_method(exc)
        if method is None:
            raise
        if method == "email":
            code = (email_code or "").strip()
            if not code:
                raise VRChatTwoFactorRequired("email") from exc
            auth_api.verify2_fa_email_code(TwoFactorEmailCode(code))
        else:
            code = (totp_code or "").strip()
            if not code:
                raise VRChatTwoFactorRequired("totp") from exc
            auth_api.verify2_fa(TwoFactorAuthCode(code))
        auth_api.get_current_user()


def get_vrchat_user_from_url(
    url_or_user_id: str,
    *,
    totp_code: str | None = None,
    email_code: str | None = None,
) -> VRChatUserLookup:
    user_id = extract_vrchat_user_id(url_or_user_id)
    with authenticated_vrchat_api_client(totp_code=totp_code, email_code=email_code) as api_client:
        users = users_api.UsersApi(api_client)
        user = users.get_user(user_id)
        return VRChatUserLookup(user=user, user_id=user_id, cookie_saved=True)


@contextmanager
def authenticated_vrchat_api_client(
    *,
    totp_code: str | None = None,
    email_code: str | None = None,
) -> Iterator[vrchatapi.ApiClient]:
    username, password = _credentials()
    cookie_path = _cookie_path()
    cookie_jar = _load_cookie_jar(cookie_path)

    configuration = vrchatapi.Configuration(username=username, password=password)
    with vrchatapi.ApiClient(configuration) as api_client:
        api_client.user_agent = os.getenv(
            "VRCHAT_USER_AGENT",
            "KennyBot/0.1.0 (Discord bot; operator configured)",
        )
        api_client.rest_client.cookie_jar = cookie_jar
        auth_api = authentication_api.AuthenticationApi(api_client)
        _ensure_authenticated(auth_api, totp_code=totp_code, email_code=email_code)
        try:
            yield api_client
        finally:
            _save_cookie_jar(cookie_jar, cookie_path)


def format_vrchat_user(user: Any) -> str:
    display_name = str(getattr(user, "display_name", "") or getattr(user, "username", "") or "unknown")
    user_id = str(getattr(user, "id", "") or "")
    status = str(getattr(user, "status", "") or "-")
    status_description = str(getattr(user, "status_description", "") or "").strip()
    pronouns = str(getattr(user, "pronouns", "") or "").strip()
    state = str(getattr(user, "state", "") or "").strip()
    platform = str(getattr(user, "last_platform", "") or getattr(user, "platform", "") or "").strip()
    last_login = str(getattr(user, "last_login", "") or "").strip()
    date_joined = str(getattr(user, "date_joined", "") or "").strip()
    bio = str(getattr(user, "bio", "") or "").strip()

    lines = [
        f"**{display_name}**",
        f"ID: `{user_id}`",
        f"URL: https://vrchat.com/home/user/{user_id}",
        f"Status: {status}",
    ]
    if status_description:
        lines.append(f"Status Message: {status_description}")
    if state:
        lines.append(f"State: {state}")
    if pronouns:
        lines.append(f"Pronouns: {pronouns}")
    if platform:
        lines.append(f"Last Platform: {platform}")
    if last_login:
        lines.append(f"Last Login: {last_login}")
    if date_joined:
        lines.append(f"Date Joined: {date_joined}")
    if bio:
        compact_bio = re.sub(r"\s+", " ", bio)
        lines.append(f"Bio: {compact_bio[:500]}")
    return "\n".join(lines)
