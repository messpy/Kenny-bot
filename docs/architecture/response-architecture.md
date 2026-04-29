# Response Architecture

This document is for humans only.
It is intentionally kept outside the bot's local RAG inputs.

## Purpose

Kenny Bot has two recurring failure modes that must be prevented:

- a server or channel description answer mixes in unrelated chat history, web search, or another guild's data
- the runtime looks like it is using multiple AI backends at once, which makes the response source unclear

This document defines the intended response architecture so the implementation can be judged against a fixed contract.

## Scope

This design applies to:

- mention and reply based chat responses
- `serverinfo` and channel profile style questions
- local profile preview and debug route preview
- model selection and model display in runtime UI

It does not redefine meeting minutes, TTS, or image generation behavior.

## Goals

- Server and channel explanations must be scoped to the current guild only.
- A place-description query must prefer local scoped data over recent chat and over web search.
- The user-visible runtime should present one primary LLM path for chat.
- User-visible answers should be natural Japanese, not debug text, not planner output, and not fixed canned prose when source text exists.
- Fallbacks should be minimal, explicit, and scoped.

## Non-Goals

- Supporting broad multi-backend routing in normal chat UI
- Exposing Gemini and Ollama side by side in admin-facing model summaries
- Using recent chat as evidence for what a server or channel fundamentally is

## Terms

- `serverinfo`: a query asking what the current server, channel, world, or place is for
- `scoped RAG`: files resolved from the current `guild_id` and optional `channel_id`
- `location meta`: `guild.name`, `channel.name`, category name, and topic from the current Discord context
- `chat model`: the primary model used for normal message responses

## Source Priority

### For `serverinfo` queries

Priority order:

1. current guild and channel scoped profile data
2. current guild and channel location meta
3. same-guild scoped RAG that directly supports the place description

Must not be used:

- `recent_turns`
- generic `local_knowledge`
- web search
- data from another guild

Rationale:

- A place-description query is a stable identity question, not a summary of the latest conversation.
- Recent turns often mention temporary topics and can overwrite the actual meaning of the place.

### For normal chat queries

Priority order:

1. direct user question
2. current message context and relevant recent turns
3. scoped RAG or local knowledge when the question needs bot or server facts
4. web search only when freshness or external verification is required

## Guild Boundary Rules

The current guild is always the guild attached to the incoming Discord event:

- message path: `msg.guild.id`
- interaction path: `interaction.guild_id`

Rules:

- All server and channel lookups must start from the current guild id.
- Any path resolution must remain under that guild's scoped directories.
- If a guild id is unavailable, the bot may use channel-local metadata, but must not search another guild as fallback.
- Legacy paths are allowed only if they are still inside the same guild scope.

## Model Policy

### Primary runtime policy

- Normal chat uses a single primary chat model from `ollama.model_chat`.
- The visible default model should also be an Ollama-served model.
- Runtime status and admin-facing summaries should not list Gemini alongside Ollama for ordinary operation.

### Compatibility policy

- Gemini support may remain in the codebase as an implementation compatibility path.
- Compatibility support must not change the normal visible model identity for chat unless explicitly selected for that use case.

### Why

If the user sees both `Ollama` and `Gemini` in the same runtime surface, it becomes unclear which system produced the final answer and which operational path should be debugged.

## Answer Generation Rules

- The responder layer returns only the final user-visible answer.
- Internal planner output, tool names, trace labels, JSON, and source annotations must not be shown.
- The answer should be 1 to 3 short sentences when possible.
- If source text already contains a usable description, prefer paraphrasing it over writing a canned introduction.
- Fixed phrases like `ここは、...です。` should not be injected unless they are justified by the actual source text.

## Fallback Policy

Fallbacks are allowed only in this order:

1. scoped profile data
2. current location meta
3. same-guild scoped RAG summary

Disallowed fallback behavior:

- switching to recent chat to explain the server identity
- switching to web search for local server identity
- synthesizing a broad generic answer when scoped data exists

If the bot still cannot answer, it should produce a short natural sentence explaining that the information is not yet organized, without exposing implementation details.

## Debug and Preview Contract

Preview and debug tools must preserve the same retrieval contract as production:

- `--dry-run-send` may suppress delivery, but should not change source priority
- trace output may record internal sources, but those labels must not leak into the final answer
- preview tooling should make it obvious which guild id, channel id, and files were consulted

## Current Intended Mapping

- primary chat model: `ollama.model_chat`
- primary visible default model: `ollama.model_default`
- profile preview AI model: same Ollama family unless explicitly overridden
- `serverinfo` retrieval path: strict scoped profile path

## Required Invariants

- A `serverinfo` query never uses `recent_turns`.
- A `serverinfo` query never uses another guild's scoped data.
- Web search is never the first source for local place identity.
- The final answer never exposes planner or tool internals.
- Normal runtime UI does not imply simultaneous primary use of both Gemini and Ollama.

## Verification Checklist

- Ask: `このサーバーは何をするところ？`
  - expected: scoped guild profile or location meta only
- Ask: `今日のニュースは？`
  - expected: web search path
- Ask: `このBotは何ができる？`
  - expected: local bot knowledge path
- Check `/bot_info`
  - expected: one primary visible model family
- Check `/model_list`
  - expected: normal runtime list shows the active Ollama-oriented surface

## Implementation Notes

Relevant files today:

- [src/kennybot/cogs/message_logger.py](/home/kennypi/work/Kenny-bot/src/kennybot/cogs/message_logger.py)
- [src/kennybot/utils/profile_preview.py](/home/kennypi/work/Kenny-bot/src/kennybot/utils/profile_preview.py)
- [src/kennybot/utils/profile_preview_api.py](/home/kennypi/work/Kenny-bot/src/kennybot/utils/profile_preview_api.py)
- [src/kennybot/ai/client.py](/home/kennypi/work/Kenny-bot/src/kennybot/ai/client.py)
- [src/kennybot/cogs/slash_commands.py](/home/kennypi/work/Kenny-bot/src/kennybot/cogs/slash_commands.py)
- [src/kennybot/utils/runtime_settings.py](/home/kennypi/work/Kenny-bot/src/kennybot/utils/runtime_settings.py)

This document is normative for future cleanup work around model routing and place-description retrieval.
