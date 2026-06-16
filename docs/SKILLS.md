# External Skills — what to install and how to vet it

This project uses Claude Code skills. **Project skills** live in
[.claude/skills/](../.claude/skills/) and are version-controlled here. This file covers
**external/third-party skills**: how to register Anthropic's skills marketplace, what this
project endorses, and the supply-chain rules for installing anything new.

> `/plugin` commands are interactive and run by a human in Claude Code — this doc lists
> the verified commands to run; it does not auto-install anything.

## Verified install commands

Verified 2026-06-16 against the official sources (see Sources). Run inside Claude Code.
A plugin/skill can add commands, agents, hooks, MCP servers, and skills — so installing
one is a trust decision (see Supply-chain caution).

### Anthropic skills marketplace (`anthropics/skills`)

```text
# 1. Register the marketplace (registers the catalog; installs nothing yet)
/plugin marketplace add anthropics/skills

# 2. Install a specific, inspected skill set from it
#    (the marketplace name resolves to: anthropic-agent-skills)
/plugin install document-skills@anthropic-agent-skills
/plugin install example-skills@anthropic-agent-skills

# Verify / manage
/plugin marketplace list
/plugin list
/reload-plugins
```

`document-skills` = DOCX/PDF/PPTX/XLSX creation & editing. `example-skills` = a broad
example bundle (creative/design, dev/technical, enterprise/comms). Prefer installing only
the specific set you need.

### Official Anthropic marketplace (`claude-plugins-official`)

Available by default in Claude Code. Relevant to this project's Claude API/SDK work:

```text
# Claude Agent SDK development tooling (closest official "SDK" skill set)
/plugin install agent-sdk-dev@claude-plugins-official

# Optional: official GitHub integration, PR review, etc.
/plugin install github@claude-plugins-official
/plugin install pr-review-toolkit@claude-plugins-official
```

### Claude API / SDK documentation skill — note

There is **no single dedicated "Claude API docs" skill** in `anthropics/skills`; that
marketplace is document/example focused. For SDK/agent building, the official
`agent-sdk-dev@claude-plugins-official` plugin is the closest fit. This repo also already
ships a session-available `claude-api` skill (API reference: model ids, pricing, params,
tool use, caching, migration) — prefer it for Claude API questions instead of installing a
third-party equivalent. Confirm the exact current catalog with `/plugin` → **Discover**.

## Supply-chain caution (read before installing anything)

A `SKILL.md` is **instructions the agent will follow**, and a plugin can also bundle
hooks/MCP servers/code that execute with your privileges. Treat every third-party skill as
untrusted input:

- **This is a PHI-handling, safety-critical project.** A malicious or careless skill could
  exfiltrate data, weaken a safety check, or inject unsafe instructions into the pipeline.
- **Read before you install.** Open the skill's `SKILL.md` and any bundled hooks/MCP
  config. Reject anything that asks to disable safety, exfiltrate data, run opaque
  scripts, or touch `src/safety/` or `src/orchestrator/`.
- **Install specific, inspected skills — not large bundles.** Smaller surface, easier review.
- **Pin to trusted sources.** Prefer Anthropic-managed marketplaces
  (`claude-plugins-official`, `anthropics/skills`). The community marketplace
  (`anthropics/claude-plugins-community`) pins commit SHAs but is third-party — still vet.
- **Scope deliberately** (user/project/local). Use project scope only for skills the whole
  team has reviewed; a project-scoped skill affects every collaborator.
- **No skill overrides ORCA's safety invariants** ([CLAUDE.md §3](../CLAUDE.md)). If a
  skill's guidance conflicts with the fail-closed safety rules, the safety rules win.

## Skills this project endorses

| Skill / source | Why | Status |
|---|---|---|
| Project skills in `.claude/skills/` (`modifying-safety-orchestrator`, `adding-a-vertical`, `voice-pipeline-operations`) | Core safety + extension guidance, grounded in this codebase | **Required reading** (version-controlled here) |
| `claude-api` (session skill) | Authoritative Claude API/model reference for AI-feature work | Endorsed |
| `agent-sdk-dev@claude-plugins-official` | Building with the Claude Agent SDK | Endorsed if doing SDK work |
| `document-skills@anthropic-agent-skills` | Generating DOCX/PDF/XLSX (e.g. pilot/report artifacts) | Endorsed on demand |
| Any other third-party skill | — | **Vet per the caution above before installing** |

## Sources

Verified 2026-06-16:
- Anthropic skills repo — <https://github.com/anthropics/skills>
- Claude Code "Discover and install prebuilt plugins" — <https://code.claude.com/docs/en/discover-plugins>
