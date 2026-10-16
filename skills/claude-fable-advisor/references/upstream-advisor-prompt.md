# Upstream Advisor Prompt Provenance

Captured on 2026-07-16 in Europe/Berlin from the locally installed npm Claude Code package and Anthropic's official advisor documentation.

## Installed binary

- Executable: `%APPDATA%\npm\node_modules\@anthropic-ai\claude-code\bin\claude.exe`
- Reported version: `2.1.211 (Claude Code)`
- Binary size: `253293728` bytes
- Binary SHA-256: `3D8509AE7DE11D77DBDC711AA320FC6D5064CE795464A8670696611B57093CAF`

The first compiled `# Advisor Tool` payload starts at byte offset `87277336` (`0x533BF18`). Its four-byte little-endian prefix is `E1 07 00 00`, so the NUL-terminated payload is exactly 2,017 ASCII bytes with LF line endings and no final newline. Its SHA-256 is `E9187A1EFAA41F6D7414AC079366961E0FB4B80455F49E6A460CEEB2CFEB1589`. That byte-for-byte payload is preserved in `upstream-advisor-prompt.binary.txt`.

## Official rendered variant

Anthropic's [advisor tool documentation](https://platform.claude.com/docs/en/agents-and-tools/tool-use/advisor-tool) publishes the matching suggested coding prompt with six typographic em dashes. Prepending `# Advisor Tool`, using CRLF line endings, and retaining the final CRLF produces the previously identified 2,040-byte form. It is preserved in `upstream-advisor-prompt.txt` with SHA-256 `AA8F9A6729F20125E67BEC3278AA94B0B14D14B153F86B7A3696FCB66A396EF8`.

The 2,040-byte file is the Windows-formatted official rendering, not a byte-for-byte binary extraction. Replacing its six em dashes with ASCII `--`, converting CRLF to LF, and removing the final LF yields the exact 2,017-byte binary payload.

The architecture is also described in Anthropic's [advisor strategy overview](https://claude.com/blog/the-advisor-strategy): the executor chooses consultation timing, the server forwards context, and the advisor supplies guidance without taking over execution.

## Adaptation boundary

This upstream instruction is provenance only. The Codex skill adapts its timing behavior into an audited local gate. It never sends this executor instruction to top-level Fable, because Fable is invoked directly and has no nested advisor tool.
