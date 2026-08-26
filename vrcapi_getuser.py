from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.kennybot.utils.env import load_env_file
from src.kennybot.utils.vrchat_user import (
    VRChatTwoFactorRequired,
    format_vrchat_user,
    get_vrchat_user_from_url,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch a VRChat user profile.")
    parser.add_argument("url", help="VRChat user URL or usr_ user id")
    parser.add_argument("--totp-code", default=None, help="TOTP 2FA code when required")
    parser.add_argument("--email-code", default=None, help="Email 2FA code when required")
    args = parser.parse_args()

    load_env_file()
    try:
        lookup = get_vrchat_user_from_url(
            args.url,
            totp_code=args.totp_code,
            email_code=args.email_code,
        )
    except VRChatTwoFactorRequired as exc:
        print(exc, file=sys.stderr)
        return 2

    print(format_vrchat_user(lookup.user))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
