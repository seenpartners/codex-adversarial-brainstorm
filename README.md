# Codex Adversarial Brainstorm

A Codex skill for adversarial solution exploration with Claude Code as a local, read-only challenger.

Codex remains the lead agent. Claude Code is invoked through the bundled bridge script to independently inspect scoped repository context, challenge assumptions, and return a structured second opinion.

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
