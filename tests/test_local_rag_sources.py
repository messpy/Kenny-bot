from __future__ import annotations

import tempfile
from pathlib import Path
from unittest import TestCase

from src.kennybot.utils.local_rag import LocalRAG


class LocalRAGSourcesTest(TestCase):
    def test_readme_is_not_loaded_as_conversation_rag(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "README.md").write_text(
                "# Human README\nREADME_ONLY_SENTINEL",
                encoding="utf-8",
            )
            knowledge_dir = root / "data" / "knowledge"
            knowledge_dir.mkdir(parents=True)
            (knowledge_dir / "chat_rag.md").write_text(
                "# Bot Knowledge\nKNOWLEDGE_ONLY_SENTINEL",
                encoding="utf-8",
            )

            rag = LocalRAG(root)
            chunks = rag.retrieve("README_ONLY_SENTINEL KNOWLEDGE_ONLY_SENTINEL", limit=10)
            body = "\n".join(chunk.body for chunk in chunks)

            self.assertIn("KNOWLEDGE_ONLY_SENTINEL", body)
            self.assertNotIn("README_ONLY_SENTINEL", body)
