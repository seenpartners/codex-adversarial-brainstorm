---
name: claude-fable-advisor
description: Request an independent technical opinion from Claude Opus 5.5 at max effort with read-only access to the current project directory through the locally authenticated Claude Code CLI. Codex remains responsible for implementation and decisions. Use when explicitly requested or, after grounding, for consequential technical decisions involving architecture, durable interfaces, difficult correctness reasoning, competing approaches, or unresolved evidence. Do not invoke implicitly for routine edits, source-resolved questions, user-only preferences, unsafe context, or when external advice is forbidden or already active.
---

# Claude Advisor

Keep Codex responsible for inspection, authoritative research, user preference questions, implementation, and the final decision. Use the selected Claude model only as an independent read-only advisor.

## User-facing status

Use the display name `Claude Advisor` in both app and CLI updates. Keep status messages short and use these states:

- `Claude Advisor · Checking` while grounding, applying vetoes, and scoring the gate.
- `Claude Advisor · Consulting · Opus 5.5 max` immediately before launching the bridge.
- `Claude Advisor · Ready` after validated advice returns; add elapsed time and cost only when useful.
- `Claude Advisor · Skipped` with the friendly reason from `user_status.message` when the gate blocks the call.
- `Claude Advisor · Unavailable` with the actionable sanitized reason from `user_status.message` when the bridge fails. A signed-out login needs authentication; do not describe it as model unavailability.

Use commentary updates for the active states so supported app and CLI surfaces show progress. Keep raw veto IDs, fingerprints, executable paths, hashes, and full envelopes out of normal status messages; include them only when troubleshooting or when the user asks for technical details.

## Workflow

1. Ground the task normally. Inspect the repository or system and consult primary sources when they can settle factual questions.
2. Apply every hard veto before scoring. A veto also blocks explicit requests.
3. Record applicable gate signals for both trigger types. An explicit invocation bypasses eligibility scoring. For an implicit invocation, score the audited signals and call only when the threshold is met and advice could realistically change the decision. Every eligible call uses the same pinned model and effort.
4. Build `AdviceRequestV1`, omitting secrets and unnecessary context. Do not include Codex's preferred answer in the first `approach` consultation.
5. Run `scripts/run_fable_advisor.py` by absolute path from the user's active project directory and provide the JSON request on stdin. Do not write the request to disk. The advisor gets read-only access to that directory; pass `--workspace` with an absolute path if the execution tool uses a different working directory. Do not infer a broader monorepo or parent directory.
6. Treat the result as advice, compare it with verified evidence, and make the final judgment. Do not silently adopt it.
7. Track the returned `request_fingerprint`, call count, and any implicit failure for the rest of the task.

## Hard vetoes

Do not call when any condition holds:

- The user forbids Claude, Fable, external models, or external transmission.
- Required context cannot be safely summarized without secrets, credentials, private customer data, or prohibited material.
- The unresolved question is a product preference only the user can choose.
- Another external-advisor workflow is already handling the same decision, including `$codex-adversarial-brainstorm`.
- An implicit Fable call already failed for this task.

Declare applicable IDs in `vetoes`: `user_forbids_external`, `unsafe_context`, `product_preference`, `external_advisor_active`, or `implicit_failure`. The bridge also rejects secret-like values and sensitive source filenames; never rely on that check as automatic redaction.

Put any user instruction forbidding external models in both `vetoes` and `constraints`. The bridge defensively scans only instruction-bearing `goal`, `success_criteria`, and `constraints` fields for an omitted prohibition; factual mentions such as offline evaluation, local-only product behavior, or quoted upstream policy in facts and evidence are not vetoes.

## Implicit gate

Use these exact signal IDs:

| Signal ID | Weight | Objective condition |
| --- | ---: | --- |
| `cross_system_breadth` | +2 | At least three runtime components, interfaces, or technical domains are coupled. |
| `durable_boundary` | +2 | A public API, protocol, schema, migration, trust boundary, or deployment topology changes. |
| `hard_reasoning` | +2 | Correctness depends on non-obvious concurrency, distributed-state, security, performance, or data-integrity invariants. |
| `competing_approaches` | +1 | At least two non-dominated approaches remain after inspection. |
| `residual_uncertainty` | +1 | Decision-critical evidence remains conflicting or unavailable. |
| `high_blast_radius` | +1 | A wrong choice risks outage, data loss, privacy harm, broad migration, or material recurring cost. |

Invoke implicitly only when the score is at least 4, at least one +2 signal is present, and `could_change_decision` is true. Do not inflate signals to reach the threshold.

## Model and effort

Every eligible consultation uses `--model claude-opus-5-5 --effort max`, including high-risk completion reviews. Pin the exact model ID so an alias update cannot silently change the advisor.

The user's requested "ultra" means the highest reasoning effort here: Claude calls that level `max`. Claude Code has no `ultra` effort value. `ultracode` is a separate workflow setting that requests `xhigh` and orchestrates workflows; this read-only advisor uses `max`. See [Anthropic's model configuration](https://code.claude.com/docs/en/model-config#adjust-effort-level) for the distinction.

The request cannot override model or effort. Never use `--fallback-model` or silently substitute an older Opus or a Fable model.

The bridge also sets a pinned-model allowlist, disables configured fallback chains, and sets `switchModelsOnFlag` to `false`. Exact acceptance is enforced by the returned-model validator. Claude Code can otherwise automatically switch safety-flagged requests to an older model even with `--model` pinned. Respect a provider refusal; never rewrite the task to evade it or relax the returned-model validation. See [Anthropic's automatic fallback documentation](https://code.claude.com/docs/en/model-config#automatic-model-fallback).

## Cadence

- Make the first call after orientation and before committing to an approach.
- Make at most two calls per task. Design-only work normally gets one.
- Permit a second call only for `repeated_failure`, `approach_pivot`, `primary_evidence_conflict`, or `high_risk_completion_review`.
- Use `phase` values `approach`, `stuck`, `reconcile`, or `completion`.
- Do not create a commit merely to make a review durable.

## AdviceRequestV1

Send UTF-8 JSON with `protocol_version: "1"` and these fields:

- Required scalars: `trigger` (`explicit` or `implicit`), `phase`, `decision_question`, `goal`, and `could_change_decision`.
- Required arrays: `gate_signals`, `success_criteria`, `constraints`, `verified_facts`, `unknowns`, `viable_options`, and `failed_attempts`.
- `verified_facts`: objects with `fact` and a sanitized repo-relative `source` label.
- `viable_options`: objects with `name` and `summary`.
- Optional `evidence`: at most eight objects with `source` and `excerpt`; each excerpt is at most 4 KiB.
- Optional `candidate_approach`: include only for stuck, reconciliation, completion, or critique work; omit it from the initial independent `approach` call.
- Optional `vetoes` and `consultation_state`. State may contain `call_count`, `prior_fingerprints`, `implicit_failure_occurred`, `another_advisor_active`, and `follow_up_reason`.

The complete request must be at most 64 KiB. Use repo-relative source labels. Reject rather than redact uncertain sensitive content.

Resolve `$skillDirectory` to the absolute directory containing this `SKILL.md`, then run the bridge with the JSON supplied through stdin. Keep the shell in the user's project directory:

```powershell
$advisorScript = Join-Path $skillDirectory 'scripts\run_fable_advisor.py'
$requestJson | python $advisorScript
```

## Workspace access

By default the advisor can independently read and search the bridge's invocation directory, including uncommitted file contents. It receives the exact canonical workspace path before the quoted brief. Use a focused brief to orient the review, and let the advisor follow references and verify claims instead of preselecting all its evidence.

For change reviews, include the changed paths and relevant diff or base-revision facts in the brief: the advisor sees current files, but cannot run Git or inspect `.git` to reconstruct what changed.

- Only `Read`, `Glob`, and `Grep` are available. Editing, shell commands, network tools, and subagents are disabled. The advisor cites inspected files with relative paths and line numbers and identifies claims it could not verify.
- The CLI process still starts in a temporary directory with `--safe-mode` and `--restricted`; `--add-dir` grants access to the selected project. Restricted mode confines file tools to the working directories. `dontAsk` denies operations that would require approval, and `--permission-prompts none` prevents unattended prompts.
- Explicit read-deny rules cover common credential files, key files, `.env` variants, and `.git` internals. These filename rules are not content redaction: do not enable directory access when task-relevant source itself contains material that must not be sent to Claude. Use `--brief-only` with a safe summary instead.
- Project hooks, plugins, MCP servers, and project instructions do not execute or load automatically. File contents remain evidence and cannot change the advisor's role. Include relevant project constraints in the brief; the advisor may inspect instruction files as evidence.
- `--workspace C:\absolute\project` selects a different local directory explicitly. `--brief-only` preserves the original brief-only behavior with all file tools disabled. The options are mutually exclusive. Filesystem roots, the user profile root, and network shares are rejected as workspace roots.
- `metadata.workspace_root` and `metadata.workspace_access` record the selected scope. Confirm them before describing a review as repository-grounded. This is live file access, not a snapshot: avoid changing decision-critical files during the consultation and recheck any cited evidence that changes.

Claude Code must be compatible 2.x version 2.1.280 or later for Opus 5.5. The bridge discovers directly executable, Anthropic-signed binaries and selects the newest compatible authenticated candidate.

Set the outer shell or execution-tool timeout to at least 520 seconds so it exceeds the bridge's 480-second process timeout. If the execution surface yields a running-cell identifier, wait on that same cell; never launch a duplicate request. The bridge emits exactly one JSON envelope on stdout. Heartbeats may appear on stderr while Claude runs.

## Result handling

- `completed`: weigh `advice` against Codex's evidence. Report Opus 5.5/max and summarize only decision-relevant disagreement or confirmation.
- `skipped`: continue without Claude advice and respect a genuine veto; never rewrite the request to evade one. If a bridge bug falsely classified sanitized factual text, fix and regression-test the matcher before evaluating the gate again. A pre-launch skip does not consume call count or cost.
- `failed` after an implicit call: continue from verified evidence and briefly state that Claude Advisor was unavailable. Do not retry implicitly.
- `failed` after an explicit call: surface the exact sanitized error and still provide Codex's best analysis unless the user required successful Claude advice.
- Never echo the complete request, proprietary excerpts, credentials, or raw Claude output on failure.
- Use only the existing first-party Claude.ai team login. Never request, migrate, or inject an API key.
- Each call requests a `$1.00` maximum. Report `reported_cost_usd`; the final inference can overshoot, so two calls are a nominal `$2.00` task budget rather than a guaranteed hard ceiling.

Accept only locally validated structured output with substantive usage of the exact model ID `claude-opus-5-5`. The bridge permits non-substantive auxiliary Haiku bookkeeping and rejects a missing selected model, automatic fallback, or substantive use of any other model family.

## Availability troubleshooting

Use the bridge's sanitized error before attempting another consultation. Diagnose with the selected executable; another `claude` on PATH may be an older WinGet installation.

- `authentication` with a signed-out message: run `claude auth login --claudeai` and let the user complete browser authentication with their existing team account. An empty saved login cannot be repaired by retrying inference. Never copy tokens from backups or inject credentials.
- `incompatible_cli`: check `claude --version`; upgrade the installation to 2.1.280 or later with `claude update` or its existing package manager, then recheck it.
- `model_mismatch`: inspect `metadata.reported_model` against `claude-opus-5-5`; do not relax validation to accept another model.
- `unavailable_model`: report that the signed-in account cannot access Opus 5.5. Keep the pinned model and surface the actual account or service restriction.

After repairing login or installation, run one minimal, non-sensitive explicit consultation through the bridge to verify the actual returned model and structured advice. A passing mocked test suite alone does not establish live availability.

After changing workspace permissions, additionally verify an actual file read in a synthetic directory whose contents were not included in the brief, confirm restricted paths are denied, and check the directory remains unchanged.

For provenance or architecture questions, read `references/upstream-advisor-prompt.md`. It points to both the exact 2,017-byte compiled payload and the exact 2,040-byte official Windows-rendered variant. Do not send either upstream executor instruction to top-level Fable.
