# Codex Adversarial Brainstorm

A Codex skill for adversarial solution exploration with Claude Code as a local, read-only challenger.

Codex remains the lead agent. Claude Code is invoked through the bundled bridge script to independently inspect scoped repository context, challenge assumptions, and return a structured second opinion.

## Claude Advisor

This repository also includes [Claude Advisor](skills/claude-fable-advisor/SKILL.md), a separate skill for an independent technical opinion from **Claude Opus 5.5 at max effort**. Codex remains responsible for implementation and the final decision.

The advisor can independently read and search the current project, including uncommitted files, and cite file-and-line evidence. It exposes only Read, Glob, and Grep; editing, command execution, network tools, and subagents are disabled. Common credential files and paths outside the selected workspace are restricted. Use `--brief-only` to disable workspace access, or `--workspace` with an absolute path to select a project explicitly.

The bridge pins Opus 5.5, disables automatic model switching, validates the returned model and structured advice, and reports actionable authentication and compatibility errors. A provider refusal is respected rather than retried on an older model. Claude calls its highest reasoning effort `max`; there is no `ultra` effort value.

### Advisor requirements and installation

- Windows, Python 3.10 or newer, and Anthropic-signed Claude Code 2.x version 2.1.280 or later
- An existing first-party Claude.ai team login with access to Opus 5.5; no API key is required

Install the `skills/claude-fable-advisor` subdirectory as its own Codex skill. For example, ask Codex:

```text
Use $skill-installer to install Claude Advisor from https://github.com/seenpartners/codex-adversarial-brainstorm/tree/main/skills/claude-fable-advisor.
```

Then invoke it with:

```text
Use $claude-fable-advisor to inspect the current project and independently review this technical decision.
```

Each consultation requests a nominal $1 maximum and has a 480-second process timeout; final inference costs can exceed the requested budget. The skill permits at most two consultations per task. Its credential-file exclusions are filename rules, not content redaction; use a safe brief-only summary when source content itself must not be shared.

Run the advisor's standalone tests from this repository:

```powershell
python -m unittest discover -s skills/claude-fable-advisor/tests -v
```

## Requirements

- Codex with skills support
- Python 3.10 or newer
- Claude Code CLI installed and logged in locally
- `claude` available on `PATH`

No Claude API key or separate credential setup is required. The bridge uses the existing local Claude Code CLI session.

## Install

Clone this repository into your Codex skills directory:

```powershell
git clone https://github.com/seenpartners/codex-adversarial-brainstorm `
  C:\Users\Milan\.codex\skills\codex-adversarial-brainstorm
```

For another machine, replace `C:\Users\Milan\.codex\skills` with that machine's Codex skills directory.

## Usage

Ask Codex to use the skill:

```text
Use $codex-adversarial-brainstorm to compare implementation approaches and pressure-test the plan with Claude Code.
```

The bridge can also be run directly:

```powershell
python C:\Users\Milan\.codex\skills\codex-adversarial-brainstorm\scripts\run_claude_challenger.py `
  --cwd C:\path\to\workspace `
  --topic "Compare two approaches for this refactor" `
  --constraints "Read-only; no file edits" `
  --files "src/app.ts,src/app.test.ts" `
  --round independent `
  --timeout 900 `
  --json
```

For realtime terminal output, use `--stream` instead of `--json`:

```powershell
python C:\Users\Milan\.codex\skills\codex-adversarial-brainstorm\scripts\run_claude_challenger.py `
  --cwd C:\path\to\workspace `
  --topic "Compare two approaches for this refactor" `
  --constraints "Read-only; no file edits" `
  --files "src/app.ts,src/app.test.ts" `
  --round independent `
  --timeout 900 `
  --stream
```

`--stream` emits Claude Code `stream-json` events as they arrive and uses Claude's required verbose stream mode internally. The bridge filters raw internal thinking/signature events and keeps user-visible text, tool events, and final result data. Terminal users can watch progress live; some Codex execution surfaces may still buffer command output until the command finishes.

## Interaction Model

1. Codex inspects the repo and forms its own Position A.
2. Codex calls the local Claude Code CLI through `scripts/run_claude_challenger.py`.
3. Claude receives only the raw topic, constraints, cwd, and scoped files for the first independent pass.
4. Claude returns a structured challenger response through stdout/JSON.
5. Codex compares both positions, optionally runs rebuttal rounds, and makes the final decision.

Claude is constrained to read/search tools only:

```text
claude -p --output-format json --permission-mode plan --tools Read,Grep,Glob
```

The prompt also tells Claude not to create files, write `plan.md`, or produce any handoff artifact. The handoff back to Codex is stdout only.

The bridge asks Claude for a `Reasoning summary`, meaning a concise user-visible rationale with evidence, assumptions, and tradeoffs. It does not request or expose hidden chain-of-thought, and stream mode filters raw internal thinking/signature events.

## Validate

From this repository:

```powershell
python C:\Users\Milan\.codex\skills\.system\skill-creator\scripts\quick_validate.py .
claude --version
python scripts\run_claude_challenger.py --cwd . --topic "Smoke test" --constraints "Read-only" --round independent --json
```

Expected validation result:

```text
Skill is valid!
```
