from __future__ import annotations

import unittest
from types import SimpleNamespace

from src.kennybot.utils.vrchat_user import extract_vrchat_user_id, format_vrchat_user


class VRChatUserTests(unittest.TestCase):
    def test_extract_vrchat_user_id_from_url(self) -> None:
        self.assertEqual(
            extract_vrchat_user_id(
                "https://vrchat.com/home/user/usr_113475ea-b9e4-4509-959f-0a19c0337be2"
            ),
            "usr_113475ea-b9e4-4509-959f-0a19c0337be2",
        )

    def test_extract_vrchat_user_id_rejects_missing_id(self) -> None:
        with self.assertRaises(ValueError):
            extract_vrchat_user_id("https://vrchat.com/home/world/wrld_abc")

    def test_format_vrchat_user_includes_profile_fields(self) -> None:
        user = SimpleNamespace(
            id="usr_113475ea-b9e4-4509-959f-0a19c0337be2",
            display_name="Kenny",
            username="kenny",
            status="active",
            status_description="hello",
            pronouns="they/them",
            state="online",
            last_platform="standalonewindows",
            last_login="2026-06-01T00:00:00.000Z",
            date_joined="2020-01-01",
            bio="VRChat profile",
        )

        text = format_vrchat_user(user)

        self.assertIn("**Kenny**", text)
        self.assertIn("ID: `usr_113475ea-b9e4-4509-959f-0a19c0337be2`", text)
        self.assertIn("Status: active", text)
        self.assertIn("Bio: VRChat profile", text)


if __name__ == "__main__":
    unittest.main()
