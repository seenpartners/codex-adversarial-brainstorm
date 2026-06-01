# Challenger Prompts

Use these rules when preparing prompts for the Claude Code challenger.

## Independent Round

Claude must receive only:

- The raw topic
- The constraints
- The working directory
- The scoped file list, if any
- Instructions to inspect the codebase independently

Do not include Codex Position A, a draft implementation plan, preferred option, or any conclusion from Codex.

Required response shape:

```text
Research summary:
Reasoning summary:
Proposal:
Evidence:
Risks:
Questions:
Confidence:
```

Claude must return all sections even when the topic is underspecified. Put blocking gaps under `Questions` instead of abandoning the format.

`Reasoning summary` means a concise, user-visible rationale with evidence, assumptions, and tradeoffs. Do not ask for hidden chain-of-thought.

## Rebuttal Round

Use rebuttal only after an independent Claude response exists. Include a neutral summary of the competing positions and ask Claude to identify weak assumptions, missing evidence, overlooked risks, and a better alternative if one exists.

Keep the prompt explicit that Claude must remain read-only and must not edit files.

## Read-Only Boundaries

Tell Claude:

- Inspect files only through read/search operations.
- Do not create, edit, delete, move, format, or run modifying commands.
- Do not write `plan.md`, temporary notes, or any handoff artifact.
- Return the full challenger response through stdout only.
- For realtime terminal use, prefer Claude Code `stream-json` events through the bridge's `--stream` mode. The bridge filters raw internal thinking/signature events; rely on `Reasoning summary` for user-visible rationale.
- If evidence is unavailable, state what evidence is missing instead of guessing.
- Prefer file paths and concrete observations over generic advice.
