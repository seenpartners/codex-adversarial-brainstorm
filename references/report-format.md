# Decision Report Format

Use this format when reporting the result of an adversarial brainstorm.

```markdown
**Decision**
State the selected approach in one or two sentences.

**Why This Wins**
Explain the strongest evidence and constraints that support the decision.

**Rejected Options**
List meaningful alternatives and the concrete reason each was rejected.

**Risks**
Name residual risks, likely failure modes, and mitigations.

**Open Questions**
List only questions that materially affect the decision or implementation.

**Verification**
Describe the checks, commands, tests, or manual validation needed before trusting the result.

**Claude Challenger Notes**
Summarize what Claude contributed, where Codex agreed, and where Codex overruled it.
```

Keep this as a decision artifact. Do not paste full Claude transcripts unless the user asks for them.
