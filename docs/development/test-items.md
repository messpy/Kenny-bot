# Kenny Bot Test Items

## Scope

This checklist covers the duplicate mention reply fix, the shared message claim guard, and the adjacent message routing paths that can regress when `MessageLogger.on_message` changes.

## Test Environment

| Item | Value |
| --- | --- |
| Runtime | Python 3.13 |
| Bot entrypoint | `bin/run.py` |
| Service | `kennybot.service` |
| Main cog | `src/kennybot/cogs/message_logger.py` |
| Shared guard | `src/kennybot/utils/message_claims.py` |
| Claim directory | `runtime/state/message_claims/` |

## Functional Test Items

| ID | Area | Preconditions | Steps | Expected Result |
| --- | --- | --- | --- | --- |
| F-001 | Bot mention reply | `kennybot.service` is active and connected | Mention the bot once in a text channel | Exactly one bot reply is posted |
| F-002 | Duplicate process guard | Two bot processes receive the same Discord message ID | Trigger the same mention event | Only the first process handles the message; the second logs `Skipped duplicate message handling` |
| F-003 | Normal message routing | Send a normal message without mention or reply | Observe channel and logs | No AI reply is posted; normal reaction and command paths remain available |
| F-004 | Reply-to-bot routing | Reply to a bot message | Observe channel | The bot treats the message as an AI conversation trigger |
| F-005 | Recent mention window | Mention the bot, then send a follow-up within the configured window | Observe channel | The follow-up is treated as part of the conversation and produces only one reply |
| F-006 | DM routing | Send a DM to the bot | Observe DM reply | DM AI path still works; duplicate claim does not block valid DM messages |
| F-007 | Invalid or missing message ID | Run message route mocks with objects lacking `id` | Execute targeted unit tests | The route does not raise `AttributeError`; claim guard allows the mock through |
| F-008 | Claim pruning | Create stale `.claim` files older than the max age | Run prune logic | Stale claim files are removed; fresh claim files remain |

## Regression Test Commands

| ID | Command | Expected Result |
| --- | --- | --- |
| R-001 | `python3 -m py_compile bin/run.py src/kennybot/cogs/message_logger.py src/kennybot/utils/message_claims.py` | Completes with exit code 0 |
| R-002 | `python3 -m unittest tests.test_message_claims` | Completes with `OK` |
| R-003 | `python3 -m unittest tests.test_message_logger_summary.MessageLoggerSummaryTests.test_on_message_routes_channel_profile_directly tests.test_message_logger_summary.MessageLoggerSummaryTests.test_on_message_routes_server_stats_directly tests.test_message_logger_summary.MessageLoggerSummaryTests.test_on_message_fix_request_marks_followup_activity_as_codex_mode` | Completes with `OK` |
| R-004 | `python3 -m unittest tests.test_message_logger_summary` | Completes with `OK` when DB dependencies are available |

## Mock Test Items

| ID | Purpose | Steps | Expected Result |
| --- | --- | --- | --- |
| M-001 | Claim idempotency | Instantiate `MessageClaimStore` with a temp directory and call `claim_once(123456)` twice | First call returns `True`; second call returns `False` |
| M-002 | Distinct message IDs | Call `claim_once(123456)` and `claim_once(123457)` | Both IDs can be claimed independently |
| M-003 | MessageLogger mock compatibility | Run targeted `on_message` unit tests using `SimpleNamespace` messages | Missing `id` does not break mock tests |
| M-004 | Debug route smoke | Run `./.venv/bin/python3 bin/debug_route.py mention "<@BOT_ID> テスト" --no-ai` with DB/network access available | Route preview completes without posting to Discord |

## Operational Checks

| ID | Check | Command | Expected Result |
| --- | --- | --- | --- |
| O-001 | Service state | `systemctl --user status kennybot.service` | Service is `active (running)` |
| O-002 | Gateway connection | `journalctl --user -u kennybot.service -n 80` | Recent log includes Discord Gateway connection and `Bot Ready` |
| O-003 | Process count | `ps -ef | rg "uv run bin/run.py|python3 bin/run.py"` | Only the service-managed bot process pair is active |
| O-004 | Duplicate suppression evidence | `journalctl --user -u kennybot.service -n 120 | rg "Skipped duplicate message handling"` | Entries appear only when duplicate handling was actually suppressed |

## Known Environment Notes

| Item | Note |
| --- | --- |
| `pymysql` | Full `tests.test_message_logger_summary` may fail with `ModuleNotFoundError: No module named 'pymysql'` if DB extras are not installed. |
| Network sandbox | `bin/debug_route.py` can fail to connect to local MySQL/Ollama when the command runs inside a restricted sandbox. |
| Dirty worktree | This repository often has unrelated data migration and documentation changes. Stage only the files relevant to the current fix when committing. |
