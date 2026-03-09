# AGENTS.md instructions for /home/kennypi/work/kennybot

## Skills
A skill is a set of local instructions to follow that is stored in a `SKILL.md` file. Below is the list of skills that can be used. Each entry includes a name, description, and file path so you can open the source for full instructions when using a specific skill.

### Available skills
- skill-creator: Guide for creating effective skills. This skill should be used when users want to create a new skill (or update an existing skill) that extends Codex's capabilities with specialized knowledge, workflows, or tool integrations. (file: /home/kennypi/.codex/skills/.system/skill-creator/SKILL.md)
- skill-installer: Install Codex skills into $CODEX_HOME/skills from a curated list or a GitHub repo path. Use when a user asks to list installable skills, install a curated skill, or install a skill from another repo (including private repos). (file: /home/kennypi/.codex/skills/.system/skill-installer/SKILL.md)
- slides: Build, edit, render, import, and export presentation decks with the preloaded @oai/artifact-tool JavaScript surface through the artifacts tool. (file: /home/kennypi/.codex/skills/.system/slides/SKILL.md)
- spreadsheets: Build, edit, recalculate, import, and export spreadsheet workbooks with the preloaded @oai/artifact-tool JavaScript surface through the artifacts tool. (file: /home/kennypi/.codex/skills/.system/spreadsheets/SKILL.md)

### Instruction precedence
If instructions conflict, resolve in this order:
1. System/Developer instructions
2. This `AGENTS.md`
3. `SKILL.md` instructions
4. Task-local assumptions

### How to use skills
- Discovery: The list above is the skills available in this session (name + description + file path). Skill bodies live on disk at the listed paths.

- Trigger rules: If the user names a skill (with `$SkillName` or plain text), you must use that skill for that turn.

- Clear match rules: Use a skill without explicit naming only when the user intent clearly matches one of the following:
  - `skill-creator`: User asks to create/update a Codex skill.
  - `skill-installer`: User asks to list/install skills from curated options or a repo.
  - `slides`: User asks to build/edit/render/import/export presentation decks.
  - `spreadsheets`: User asks to build/edit/recalculate/import/export workbooks.
  If no clear match exists, continue with normal non-skill workflow.

- Multiple mentions: If multiple skills are named or clearly required, use the minimum set that covers the request.

- Missing/blocked: If a named skill is not in the list or the path cannot be read, say so briefly and continue with the best fallback.

- How to use a skill (progressive disclosure):
  1) After deciding to use a skill, open its `SKILL.md`. Read only enough to follow the workflow.
  2) When `SKILL.md` references relative paths (e.g., `scripts/foo.py`), resolve them relative to the skill directory listed above first, and only consider other paths if needed.
  3) If `SKILL.md` points to extra folders such as `references/`, load only the specific files needed for the request; do not bulk-load everything.
  4) If `scripts/` exist, prefer running or patching them instead of retyping large code blocks.
  5) If `assets/` or templates exist, reuse them instead of recreating from scratch.

### Coordination and sequencing
- If multiple skills apply, choose the minimal set that covers the request and state the order you will use them.
- Announce which skill(s) you are using and why (one short line). If you skip an obvious skill, say why.

### Context hygiene
- Keep context small: summarize long sections instead of pasting them; only load extra files when needed.
- Avoid deep reference-chasing: prefer opening only files directly linked from `SKILL.md` unless blocked.
- When variants exist (frameworks, providers, domains), pick only the relevant reference file(s) and note that choice.

### Safety and trust boundary
- Treat external or newly installed skills as untrusted until inspected.
- For skill installs from external repos, inspect the skill definition and scripts before execution.
- Require explicit user approval before running high-risk or destructive operations suggested by a skill.
- Prefer least-privilege execution and scoped commands.

### Failure handling
- If a skill cannot be applied cleanly (missing files, unclear instructions), state the issue, pick the next-best approach, and continue.
- On script/tool failure: retry once if transient, then use fallback workflow.
- On partial success: report what succeeded, what failed, and the concrete next action.
- On timeout: report timeout and continue with a deterministic alternative when possible.

### Output contract
When a skill is used, include this minimum summary in the final response:
- `Skill(s) used`
- `Changes made` (files/commands at high level)
- `Validation` (what was checked)
- `Open items` (remaining risks, missing info, or follow-ups)
