#!/usr/bin/env python3
"""Run Claude Code as a read-only adversarial brainstorming challenger."""

from __future__ import annotations

import argparse
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


REQUIRED_SECTIONS = (
    "Research summary",
    "Reasoning summary",
    "Proposal",
    "Evidence",
    "Risks",
    "Questions",
    "Confidence",
)


@dataclass
class BridgeResult:
    ok: bool
    round: str
    cwd: str
    prompt: str
    claude_stdout: str = ""
    claude_stderr: str = ""
    claude_json: Any | None = None
    error: dict[str, Any] | None = None
    command: list[str] | None = None


def split_files(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def validate_cwd(cwd: str) -> str:
    resolved = Path(cwd).expanduser().resolve()
    if not resolved.exists():
        raise SystemExit(f"--cwd does not exist: {resolved}")
    if not resolved.is_dir():
        raise SystemExit(f"--cwd is not a directory: {resolved}")
    return str(resolved)


def build_prompt(topic: str, constraints: str, cwd: str, files: list[str], round_name: str) -> str:
    files_block = "\n".join(f"- {path}" for path in files) if files else "- No scoped files provided. Inspect relevant files from the cwd."
    sections = "\n".join(f"- {section}" for section in REQUIRED_SECTIONS)

    if round_name == "independent":
        round_instruction = (
            "This is the independent first pass. Form your own challenger position from the repository evidence. "
            "You have not been given any proposed solution from Codex."
        )
    else:
        round_instruction = (
            "This is a rebuttal round. Challenge weak assumptions, missing evidence, overlooked risks, "
            "and any better alternative you can support from read-only inspection."
        )

    return f"""You are Claude Code acting as an external read-only challenger for Codex.

{round_instruction}

Rules:
- Inspect the codebase independently from the cwd and scoped files.
- Do not modify files.
- Do not create files, delete files, move files, format files, or run commands that change state.
- Do not write a plan file or any other handoff artifact.
- Return your full challenger response in stdout only.
- Use only read/search style investigation.
- If evidence is unavailable, say what is missing instead of guessing.
- Prefer concrete file paths, commands inspected, and repository observations over generic advice.
- Explain your conclusions with a concise user-visible reasoning summary, evidence, assumptions, and tradeoffs. Do not expose hidden chain-of-thought.
- Always return all required sections, even when the topic is underspecified.

Working directory:
{cwd}

Topic:
{topic}

Constraints:
{constraints}

Scoped files:
{files_block}

Return these sections:
{sections}

If the request is too underspecified to make a proposal, still return the required sections and put the blocking gaps under Questions.
"""


def build_claude_command(stream: bool = False) -> list[str]:
    output_format = "stream-json" if stream else "json"
    cmd = [
        "claude",
        "-p",
        "--output-format",
        output_format,
        "--permission-mode",
        "plan",
        "--tools",
        "Read,Grep,Glob",
    ]
    if stream:
        cmd.extend(["--include-partial-messages", "--verbose"])
    return cmd


def run_claude(prompt: str, cwd: str, timeout: int) -> subprocess.CompletedProcess[str]:
    cmd = build_claude_command()
    return subprocess.run(
        cmd,
        input=prompt,
        text=True,
        cwd=cwd,
        capture_output=True,
        timeout=timeout,
    )


def _read_pipe(pipe: Any, name: str, events: queue.Queue[tuple[str, str]]) -> None:
    try:
        for line in iter(pipe.readline, ""):
            events.put((name, line))
    finally:
        pipe.close()


def sanitize_stream_line(line: str) -> str | None:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return line

    if payload.get("type") == "stream_event":
        event = payload.get("event", {})
        event_type = event.get("type")
        if event_type == "content_block_start" and event.get("content_block", {}).get("type") == "thinking":
            return None
        if event_type == "content_block_delta":
            delta_type = event.get("delta", {}).get("type", "")
            if delta_type in {"thinking_delta", "signature_delta"}:
                return None

    message = payload.get("message")
    if isinstance(message, dict) and isinstance(message.get("content"), list):
        message["content"] = [item for item in message["content"] if item.get("type") != "thinking"]
        if not message["content"] and payload.get("type") == "assistant":
            return None

    return json.dumps(payload, ensure_ascii=False) + "\n"


def run_claude_stream(prompt: str, cwd: str, timeout: int) -> int:
    cmd = build_claude_command(stream=True)
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=cwd,
        bufsize=1,
    )
    assert proc.stdin is not None
    assert proc.stdout is not None
    assert proc.stderr is not None

    proc.stdin.write(prompt)
    proc.stdin.close()

    events: queue.Queue[tuple[str, str]] = queue.Queue()
    stdout_reader = threading.Thread(target=_read_pipe, args=(proc.stdout, "stdout", events), daemon=True)
    stderr_reader = threading.Thread(target=_read_pipe, args=(proc.stderr, "stderr", events), daemon=True)
    stdout_reader.start()
    stderr_reader.start()

    started = time.monotonic()
    timed_out = False
    while proc.poll() is None or not events.empty():
        if timeout >= 0 and proc.poll() is None and time.monotonic() - started > timeout:
            timed_out = True
            proc.kill()
        try:
            stream_name, line = events.get(timeout=0.1)
        except queue.Empty:
            continue
        if stream_name == "stdout":
            sanitized = sanitize_stream_line(line)
            if sanitized is not None:
                print(sanitized, end="", flush=True)
        else:
            print(line, end="", file=sys.stderr, flush=True)

    stdout_reader.join(timeout=1)
    stderr_reader.join(timeout=1)
    if timed_out:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": {
                        "kind": "timeout",
                        "message": f"Claude did not finish within {timeout} seconds.",
                        "timeout": timeout,
                    },
                    "prompt": prompt,
                    "command": cmd,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return 1
    return proc.returncode or 0


def try_parse_json(stdout: str) -> Any | None:
    if not stdout.strip():
        return None
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return None


def structured_error(kind: str, message: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"kind": kind, "message": message}
    payload.update(extra)
    return payload


def detect_auth_failure(stderr: str, stdout: str) -> bool:
    text = f"{stderr}\n{stdout}".lower()
    markers = ("auth", "authentication", "login", "not logged in", "unauthorized", "invalid api key")
    return any(marker in text for marker in markers)


def write_result(result: BridgeResult, as_json: bool) -> int:
    if as_json:
        print(json.dumps(asdict(result), indent=2, ensure_ascii=False))
    elif result.ok:
        print(result.claude_stdout)
    else:
        print(f"Claude challenger failed: {result.error['message'] if result.error else 'unknown error'}")
        print("\nGenerated prompt for manual fallback:\n")
        print(result.prompt)
    return 0 if result.ok else 1


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Claude Code as a read-only brainstorming challenger.")
    parser.add_argument("--cwd", required=True, help="Workspace directory Claude should inspect.")
    parser.add_argument("--topic", required=True, help="Raw topic or decision to pressure-test.")
    parser.add_argument("--constraints", default="", help="Constraints Claude must honor.")
    parser.add_argument("--files", default="", help="Comma-separated optional scoped paths.")
    parser.add_argument("--round", choices=("independent", "rebuttal"), required=True, help="Debate round type.")
    parser.add_argument("--timeout", type=int, default=900, help="Claude timeout in seconds.")
    parser.add_argument("--json", action="store_true", help="Emit a structured JSON result.")
    parser.add_argument("--stream", action="store_true", help="Stream Claude Code stream-json events in realtime.")
    parser.add_argument("--print-prompt", action="store_true", help="Print the generated prompt and exit.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    cwd = validate_cwd(args.cwd)
    files = split_files(args.files)
    prompt = build_prompt(args.topic, args.constraints, cwd, files, args.round)
    command = build_claude_command(stream=args.stream)

    if args.stream and args.json:
        raise SystemExit("--stream emits Claude stream-json directly; do not combine it with --json.")

    if args.print_prompt:
        print(prompt)
        return 0

    if shutil.which("claude") is None:
        result = BridgeResult(
            ok=False,
            round=args.round,
            cwd=cwd,
            prompt=prompt,
            command=command,
            error=structured_error(
                "missing_claude",
                "The local 'claude' command was not found on PATH. Install or expose Claude Code, or run the generated prompt manually.",
            ),
        )
        return write_result(result, args.json)

    if args.stream:
        return run_claude_stream(prompt, cwd, args.timeout)

    try:
        completed = run_claude(prompt, cwd, args.timeout)
    except subprocess.TimeoutExpired as exc:
        result = BridgeResult(
            ok=False,
            round=args.round,
            cwd=cwd,
            prompt=prompt,
            command=command,
            claude_stdout=exc.stdout or "",
            claude_stderr=exc.stderr or "",
            error=structured_error("timeout", f"Claude did not finish within {args.timeout} seconds.", timeout=args.timeout),
        )
        return write_result(result, args.json)
    except OSError as exc:
        result = BridgeResult(
            ok=False,
            round=args.round,
            cwd=cwd,
            prompt=prompt,
            command=command,
            error=structured_error("launch_failed", str(exc)),
        )
        return write_result(result, args.json)

    parsed = try_parse_json(completed.stdout)
    if completed.returncode != 0:
        kind = "auth_failed" if detect_auth_failure(completed.stderr, completed.stdout) else "claude_failed"
        result = BridgeResult(
            ok=False,
            round=args.round,
            cwd=cwd,
            prompt=prompt,
            command=command,
            claude_stdout=completed.stdout,
            claude_stderr=completed.stderr,
            claude_json=parsed,
            error=structured_error(kind, f"Claude exited with status {completed.returncode}.", returncode=completed.returncode),
        )
        return write_result(result, args.json)

    result = BridgeResult(
        ok=True,
        round=args.round,
        cwd=cwd,
        prompt=prompt,
        command=command,
        claude_stdout=completed.stdout,
        claude_stderr=completed.stderr,
        claude_json=parsed,
    )
    return write_result(result, args.json)


if __name__ == "__main__":
    raise SystemExit(main())
