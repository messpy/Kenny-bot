# utils/config.py
# 互換レイヤー: prompt template のみを公開

from src.kennybot.utils.prompts import get_prompt
PROMPT_TEMPLATE = get_prompt("chat", "prompt_template")
HISTORY_CONTEXT_TEMPLATE = get_prompt("chat", "history_context_template")
