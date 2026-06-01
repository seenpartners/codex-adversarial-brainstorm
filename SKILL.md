---
name: codex-adversarial-brainstorm
description: Codex-led adversarial brainstorming with the locally authenticated Claude Code CLI as the external challenger. Use when comparing approaches, exploring feasibility, exhausting solution options, pressure-testing architecture, or asking for a second opinion before implementation. Codex remains the main agent and runs Claude through the bundled read-only bridge.
---

# Codex Adversarial Brainstorm

Use this skill to make Codex lead a structured debate while Claude Code acts as an independent, read-only challenger. Keep Codex responsible for repository investigation, final judgment, implementation decisions, and user-facing recommendations.

## Workflow

1. Inspect the repository independently and form Codex Position A before invoking Claude.
2. Resolve the skill directory as the directory containing this `SKILL.md`.
3. Run the bridge script by absolute path with the raw topic, constraints, current working directory, and only the scoped files Claude should inspect.
4. Do not include Codex Position A in Claude's first `independent` prompt.
5. Ask Claude for Position B through the bridge.
6. Compare Position A and Position B. Run two debate rounds by default, using `--round rebuttal` only after Claude has produced an independent response.
7. Produce a final decision report with the selected approach, rejected options, risks, open questions, and verification steps.

## Bridge

Use the bundled bridge from this skill directory. Do not stop after reading `SKILL.md` or the script; execute the bridge unless the user only asked for an explanation.

From PowerShell on this machine:

```powershell
python C:\Users\Milan\.codex\skills\codex-adversarial-brainstorm\scripts\run_claude_challenger.py `
  --cwd <workspace> `
  --topic "<text>" `
  --constraints "<text>" `
  --files "<comma-separated optional paths>" `
  --round independent `
  --timeout 900 `
  --json
```

For realtime terminal output, use stream mode instead of `--json`:

```powershell
python C:\Users\Milan\.codex\skills\codex-adversarial-brainstorm\scripts\run_claude_challenger.py `
  --cwd <workspace> `
  --topic "<text>" `
  --constraints "<text>" `
  --files "<comma-separated optional paths>" `
  --round independent `
  --timeout 900 `
  --stream
```

`--stream` emits Claude Code `stream-json` events as they arrive and uses Claude's required verbose stream mode internally. The bridge filters raw internal thinking/signature events and keeps user-visible text, tool events, and final result data. It is useful in a terminal, but some Codex tool surfaces may still buffer command output until the process exits.

Portable form after resolving the skill directory:

```bash
python <skill-dir>/scripts/run_claude_challenger.py \
  --cwd <workspace> \
  --topic "<text>" \
  --constraints "<text>" \
  --files "<comma-separated optional paths>" \
  --round independent \
  --timeout 900 \
  --json
```

The bridge calls the local `claude` command and relies on the user's existing Claude Code login. Never ask the user for Claude API keys, token exports, credential files, or credential migration for this workflow.

The bridge enforces the v1 boundary by:

- Using `claude -p --output-format json --permission-mode plan --tools Read,Grep,Glob`.
- Sending instructions that Claude must not modify files.
- Sending instructions that Claude must not write `plan.md` or any other handoff artifact.
- Returning structured errors and the generated prompt when Claude is missing, times out, or reports an auth/runtime failure.
- Writing no run folders or repository artifacts by default.

## Prompt Discipline

For `independent` rounds, give Claude only the topic, constraints, cwd, and scoped file list. Do not include Codex's preferred approach, draft plan, critique, or conclusion.

For `rebuttal` rounds, include a concise neutral summary of the competing claims and ask Claude to challenge assumptions, missing evidence, and failure modes. Keep this phase read-only.

Claude's response should contain:

- Research summary
- Reasoning summary
- Proposal
- Evidence
- Risks
- Questions
- Confidence

Detailed prompt expectations are in `references/challenger-prompts.md`.

Ask Claude for user-visible reasoning only: concise rationale, evidence, assumptions, and tradeoffs. Do not ask Claude to expose hidden chain-of-thought. The stream path must not expose raw thinking events.

## Report

End with a decision report, not a transcript dump. Use `references/report-format.md` when the user wants a formal output or when the decision is complex.

Prefer this structure:

- Decision
- Why this wins
- Rejected options
- Risks and mitigations
- Open questions
- Verification steps
- Claude challenger notes

## Failure Handling

If Claude is unavailable or unauthenticated, continue the Codex-led analysis and show the structured bridge error. Include the generated prompt so the user can run Claude manually if they want. Do not block implementation solely because the challenger is unavailable unless the user explicitly required a successful Claude pass.
