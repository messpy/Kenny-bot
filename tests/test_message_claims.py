import os
import tempfile
import time
import unittest
from pathlib import Path

from src.kennybot.utils.message_claims import MessageClaimStore


class MessageClaimStoreTests(unittest.TestCase):
    def test_claim_once_suppresses_same_message_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MessageClaimStore(Path(tmp) / "claims")

            self.assertTrue(store.claim_once(123456))
            self.assertFalse(store.claim_once(123456))
            self.assertTrue(store.claim_once(123457))

    def test_invalid_message_id_is_not_claimed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            claim_dir = Path(tmp) / "claims"
            store = MessageClaimStore(claim_dir)

            self.assertTrue(store.claim_once(0))
            self.assertFalse(claim_dir.exists())

    def test_prune_removes_old_claims(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            claim_dir = Path(tmp) / "claims"
            claim_dir.mkdir()
            old_claim = claim_dir / "1.claim"
            new_claim = claim_dir / "2.claim"
            old_claim.write_text("old\n", encoding="utf-8")
            new_claim.write_text("new\n", encoding="utf-8")
            old_time = time.time() - 3600
            now = time.time()
            os.utime(old_claim, (old_time, old_time))
            os.utime(new_claim, (now, now))

            store = MessageClaimStore(claim_dir, prune_interval_seconds=0)
            store.prune(max_age_seconds=60)

            self.assertFalse(old_claim.exists())
            self.assertTrue(new_claim.exists())


if __name__ == "__main__":
    unittest.main()
