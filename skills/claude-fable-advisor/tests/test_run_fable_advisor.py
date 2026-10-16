from __future__ import annotations

import base64
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "scripts" / "run_fable_advisor.py"
SKILL_DIR = SCRIPT.parents[1]
SPEC = importlib.util.spec_from_file_location("run_fable_advisor", SCRIPT)
assert SPEC and SPEC.loader
advisor = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = advisor
SPEC.loader.exec_module(advisor)


def request(**overrides):
    value = {
        "protocol_version": "1",
        "trigger": "implicit",
        "phase": "approach",
        "gate_signals": ["durable_boundary", "competing_approaches", "residual_uncertainty"],
        "decision_question": "Which protocol boundary should coordinate the three services?",
        "goal": "Choose a safe integration shape.",
        "could_change_decision": True,
        "success_criteria": ["Preserve data integrity", "Remain operable during rollout"],
        "constraints": ["No repository writes by the advisor"],
        "verified_facts": [{"fact": "Three services share the boundary.", "source": "src/orchestration.py:12"}],
        "unknowns": ["Peak retry volume"],
        "viable_options": [
            {"name": "Outbox", "summary": "Persist events with state changes."},
            {"name": "Coordinator", "summary": "Use an explicit workflow coordinator."},
        ],
        "failed_attempts": [],
        "evidence": [],
        "vetoes": [],
        "consultation_state": {"call_count": 0, "prior_fingerprints": []},
    }
    value.update(overrides)
    return value


def encoded(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def advice_result():
    return {
        "recommendation": "Use an outbox boundary.",
        "rationale": "It keeps publication atomic with state changes.",
        "alternatives": [{"approach": "Coordinator", "when_it_wins": "Long-running compensation dominates."}],
        "risks": [{"risk": "Backlog growth", "mitigation": "Alert on age and drain rate."}],
        "assumptions": ["The database supports transactions."],
        "missing_evidence": ["Measured retry volume"],
        "verification_steps": ["Run fault-injection tests."],
        "confidence": "high",
    }


def invocation(payload, *, returncode=0, stderr=""):
    return advisor.InvocationResult(
        returncode=returncode,
        stdout=json.dumps(payload, separators=(",", ":")),
        stderr=stderr,
        elapsed_seconds=0.1,
    )


class SkillArtifactTests(unittest.TestCase):
    def test_required_structure_and_policies(self):
        self.assertTrue((SKILL_DIR / "SKILL.md").is_file())
        self.assertTrue((SKILL_DIR / "agents" / "openai.yaml").is_file())
        self.assertFalse((SKILL_DIR / "README.md").exists())
        own_metadata = (SKILL_DIR / "agents" / "openai.yaml").read_text(encoding="utf-8")
        skill_text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("allow_implicit_invocation: true", own_metadata)
        self.assertIn('display_name: "Claude Advisor"', own_metadata)
        self.assertIn('brand_color: "#6D5EF5"', own_metadata)
        self.assertIn("$claude-fable-advisor", own_metadata)
        for label in ("Checking", "Consulting", "Ready", "Skipped", "Unavailable"):
            self.assertIn(f"Claude Advisor · {label}", skill_text)
        self.assertIn("at least 520 seconds", skill_text)
        self.assertIn("wait on that same cell", skill_text)
        self.assertIn("A pre-launch skip does not consume call count or cost", skill_text)

    def test_upstream_prompt_provenance_bytes(self):
        binary = (SKILL_DIR / "references" / "upstream-advisor-prompt.binary.txt").read_bytes()
        rendered = (SKILL_DIR / "references" / "upstream-advisor-prompt.txt").read_bytes()
        self.assertEqual(2017, len(binary))
        self.assertEqual(
            "E9187A1EFAA41F6D7414AC079366961E0FB4B80455F49E6A460CEEB2CFEB1589",
            __import__("hashlib").sha256(binary).hexdigest().upper(),
        )
        self.assertEqual(2040, len(rendered))
        self.assertEqual(
            "AA8F9A6729F20125E67BEC3278AA94B0B14D14B153F86B7A3696FCB66A396EF8",
            __import__("hashlib").sha256(rendered).hexdigest().upper(),
        )
        normalized = rendered.decode("utf-8").replace(" — ", " -- ").replace("\r\n", "\n").removesuffix("\n")
        self.assertEqual(binary.decode("ascii"), normalized)


class GateTests(unittest.TestCase):
    def parsed(self, value):
        return advisor.parse_request(encoded(value))

    def test_threshold_matrix(self):
        cases = [
            ([], False),
            (["durable_boundary"], False),
            (["durable_boundary", "competing_approaches"], False),
            (["durable_boundary", "competing_approaches", "residual_uncertainty"], True),
            (["durable_boundary", "hard_reasoning"], True),
            (["competing_approaches", "residual_uncertainty", "high_blast_radius"], False),
        ]
        for signals, expected in cases:
            with self.subTest(signals=signals):
                decision = advisor.evaluate_gate(self.parsed(request(gate_signals=signals)))
                self.assertEqual(expected, decision.proceed)

    def test_explicit_bypasses_score_and_can_change_only(self):
        value = self.parsed(request(trigger="explicit", gate_signals=[], could_change_decision=False))
        self.assertTrue(advisor.evaluate_gate(value).proceed)

    def test_each_veto_wins_for_explicit_request(self):
        for veto in sorted(advisor.VETO_IDS):
            with self.subTest(veto=veto):
                value = self.parsed(request(trigger="explicit", gate_signals=[], vetoes=[veto]))
                decision = advisor.evaluate_gate(value)
                self.assertFalse(decision.proceed)
                self.assertTrue(decision.reason.startswith("veto:"))

    def test_natural_language_external_ban_becomes_veto(self):
        for constraint in (
            "Never use Fable for this task.",
            "No external models.",
            "Work without using any external model.",
            "I forbid Claude.",
            "Keep this entirely offline only.",
            "Keep this task entirely offline.",
            "Keep this work local-only.",
            "Do not disclose this to Claude.",
        ):
            with self.subTest(constraint=constraint):
                value = request(trigger="explicit", constraints=[constraint])
                parsed = self.parsed(value)
                self.assertIn("user_forbids_external", parsed["vetoes"])
                self.assertFalse(advisor.evaluate_gate(parsed).proceed)

    def test_offline_evaluation_facts_do_not_trigger_external_veto(self):
        value = request(
            trigger="explicit",
            gate_signals=[
                "cross_system_breadth",
                "durable_boundary",
                "hard_reasoning",
                "competing_approaches",
                "high_blast_radius",
            ],
            decision_question=(
                "Which patterns from shrimbly/project-wiki should FilmWiki v2 adopt, adapt, or reject, "
                "and in what implementation order, given FilmWiki already has a stronger production "
                "runtime but lacks a human knowledge explorer and passage-level feedback loop?"
            ),
            goal=(
                "Choose a low-risk, high-value architecture proposal that strengthens source fidelity "
                "and human collaboration without weakening tenant isolation, provenance, write safety, "
                "or the existing React and SEEN platform boundaries."
            ),
            constraints=["This task is investigation and design only; no implementation is authorized."],
            verified_facts=[
                {
                    "fact": "FilmWiki has read-only MCP and offline evaluation.",
                    "source": "filmwiki-v2/mcp_server.py",
                },
                {
                    "fact": "The complete offline round-trip suite passed.",
                    "source": "filmwiki-v2/evals/roundtrip/run.py",
                },
                {
                    "fact": "Upstream documentation discusses a local-only mode.",
                    "source": "upstream/docs/runtime.md",
                },
            ],
        )
        parsed = advisor.parse_request(encoded(value))
        decision = advisor.evaluate_gate(parsed)
        self.assertNotIn("user_forbids_external", parsed["vetoes"])
        self.assertEqual("88345298b009cff87b332442", decision.fingerprint)
        self.assertTrue(decision.proceed)

    def test_quoted_external_ban_in_evidence_is_not_a_user_veto(self):
        value = request(
            trigger="explicit",
            verified_facts=[
                {
                    "fact": "The upstream document literally says no external models.",
                    "source": "upstream/docs/policy.md",
                }
            ],
        )
        parsed = advisor.parse_request(encoded(value))
        self.assertNotIn("user_forbids_external", parsed["vetoes"])
        self.assertTrue(advisor.evaluate_gate(parsed).proceed)

    def test_product_offline_requirement_is_not_an_advisor_veto(self):
        value = request(
            trigger="explicit",
            constraints=["The delivered product must support offline evaluation and local-only deployment."],
        )
        parsed = advisor.parse_request(encoded(value))
        self.assertNotIn("user_forbids_external", parsed["vetoes"])
        self.assertTrue(advisor.evaluate_gate(parsed).proceed)

    def test_follow_up_matrix_and_maximum(self):
        first = self.parsed(request())
        fingerprint = advisor.request_fingerprint(first)
        allowed = request(
            phase="reconcile",
            candidate_approach="Use an outbox.",
            consultation_state={
                "call_count": 1,
                "prior_fingerprints": [fingerprint],
                "follow_up_reason": "primary_evidence_conflict",
            },
        )
        self.assertTrue(advisor.evaluate_gate(self.parsed(allowed)).proceed)

        denied = request(
            phase="reconcile",
            candidate_approach="Use an outbox.",
            consultation_state={"call_count": 1, "prior_fingerprints": [fingerprint]},
        )
        self.assertEqual("follow_up_not_justified", advisor.evaluate_gate(self.parsed(denied)).reason)

        third = request(consultation_state={"call_count": 2, "prior_fingerprints": []})
        self.assertEqual("maximum_calls_reached", advisor.evaluate_gate(self.parsed(third)).reason)

    def test_high_risk_completion_follow_up_requires_blast_radius(self):
        state = {"call_count": 1, "prior_fingerprints": [], "follow_up_reason": "high_risk_completion_review"}
        value = request(
            phase="completion",
            candidate_approach="Ship after fault injection.",
            gate_signals=["durable_boundary", "competing_approaches", "residual_uncertainty"],
            consultation_state=state,
        )
        self.assertEqual("completion_review_not_high_risk", advisor.evaluate_gate(self.parsed(value)).reason)
        value["gate_signals"].append("high_blast_radius")
        self.assertTrue(advisor.evaluate_gate(self.parsed(value)).proceed)

    def test_duplicate_and_implicit_failure_circuit(self):
        value = self.parsed(request())
        fingerprint = advisor.request_fingerprint(value)
        duplicate = request(consultation_state={"call_count": 1, "prior_fingerprints": [fingerprint], "follow_up_reason": "approach_pivot"})
        self.assertEqual("duplicate_decision", advisor.evaluate_gate(self.parsed(duplicate)).reason)

        circuit = request(trigger="explicit", consultation_state={"call_count": 1, "prior_fingerprints": [], "implicit_failure_occurred": True})
        decision = advisor.evaluate_gate(self.parsed(circuit))
        self.assertEqual("veto:implicit_failure", decision.reason)

    def test_unknown_and_duplicate_signals_reject(self):
        for signals in (["unknown"], ["hard_reasoning", "hard_reasoning"]):
            with self.subTest(signals=signals):
                with self.assertRaises(advisor.BridgeProblem):
                    self.parsed(request(gate_signals=signals))


class ModelSelectionTests(unittest.TestCase):
    def parsed_and_gated(self, value):
        parsed = advisor.parse_request(encoded(value))
        gate = advisor.evaluate_gate(parsed)
        self.assertTrue(gate.proceed)
        return parsed, gate

    def test_pinned_opus_max_for_simple_explicit_and_minimum_threshold_requests(self):
        values = [
            request(trigger="explicit", gate_signals=[]),
            request(gate_signals=["durable_boundary", "competing_approaches", "residual_uncertainty"]),
        ]
        for value in values:
            with self.subTest(signals=value["gate_signals"]):
                parsed, gate = self.parsed_and_gated(value)
                self.assertEqual(4 if value["gate_signals"] else 0, gate.score)
                self.assertEqual(
                    advisor.AdvisorSelection("claude-opus-5-5", "claude-opus-5-5", "Opus 5.5", "max"),
                    advisor.select_advisor(parsed, gate),
                )

    def test_complexity_does_not_switch_away_from_pinned_opus(self):
        parsed, gate = self.parsed_and_gated(
            request(gate_signals=["durable_boundary", "hard_reasoning", "competing_approaches"])
        )
        self.assertEqual(5, gate.score)
        self.assertEqual(
            advisor.AdvisorSelection("claude-opus-5-5", "claude-opus-5-5", "Opus 5.5", "max"),
            advisor.select_advisor(parsed, gate),
        )

    def test_high_risk_completion_review_keeps_pinned_opus_max(self):
        parsed, gate = self.parsed_and_gated(
            request(
                trigger="explicit",
                phase="completion",
                candidate_approach="Ship after fault injection.",
                gate_signals=["high_blast_radius"],
                consultation_state={
                    "call_count": 1,
                    "prior_fingerprints": [],
                    "follow_up_reason": "high_risk_completion_review",
                },
            )
        )
        self.assertEqual(1, gate.score)
        self.assertEqual(
            advisor.AdvisorSelection("claude-opus-5-5", "claude-opus-5-5", "Opus 5.5", "max"),
            advisor.select_advisor(parsed, gate),
        )


class RequestValidationTests(unittest.TestCase):
    def test_utf8_request_limit(self):
        base = request()
        low = 0
        high = advisor.REQUEST_LIMIT_BYTES
        while low <= high:
            middle = (low + high) // 2
            base["constraints"] = ["x" * middle]
            size = len(encoded(base))
            if size < advisor.REQUEST_LIMIT_BYTES:
                low = middle + 1
            elif size > advisor.REQUEST_LIMIT_BYTES:
                high = middle - 1
            else:
                break
        self.assertEqual(advisor.REQUEST_LIMIT_BYTES, len(encoded(base)))
        advisor.parse_request(encoded(base))
        base["constraints"][0] += "x"
        with self.assertRaisesRegex(advisor.BridgeProblem, "exceeds"):
            advisor.parse_request(encoded(base))

    def test_excerpt_count_and_utf8_byte_limit(self):
        valid = request(evidence=[{"source": f"src/f{i}.py", "excerpt": "x" * 4096} for i in range(8)])
        advisor.parse_request(encoded(valid))
        too_many = request(evidence=valid["evidence"] + [{"source": "src/extra.py", "excerpt": "x"}])
        with self.assertRaises(advisor.BridgeProblem):
            advisor.parse_request(encoded(too_many))
        multibyte = request(evidence=[{"source": "src/f.py", "excerpt": "é" * 2049}])
        with self.assertRaisesRegex(advisor.BridgeProblem, "4096"):
            advisor.parse_request(encoded(multibyte))

    def test_source_labels_and_sensitive_names(self):
        for source in ("/etc/passwd", "C:/Users/a/file.py", "../secret.txt", "https://example.test/a", "\\\\server\\share\\a"):
            value = request(verified_facts=[{"fact": "fact", "source": source}])
            with self.subTest(source=source), self.assertRaises(advisor.BridgeProblem):
                advisor.parse_request(encoded(value))
        for source in (".env", "config/credentials.json", "keys/service.pem", "ops/auth-token.txt"):
            value = request(verified_facts=[{"fact": "fact", "source": source}])
            with self.subTest(source=source), self.assertRaises(advisor.SensitiveContext):
                advisor.parse_request(encoded(value))

    def test_secret_patterns_reject_and_hashes_do_not(self):
        secrets = [
            "api_key=abcdefghijklmnop",
            "AKIAABCDEFGHIJKLMNOP",
            "ghp_abcdefghijklmnopqrstuvwxyz123456",
            "github_pat_abcdefghijklmnopqrstuvwxyz123456",
            "secret_key=abcdefghijklmnop",
            "sk_" + "live_" + ("dummy" * 6),
            "Bearer abcdefghijklmnopqrstuvwxyz",
            "postgres://user:password@example.test/db",
            "-----BEGIN PRIVATE KEY-----",
        ]
        for secret in secrets:
            value = request(constraints=[secret])
            with self.subTest(secret=secret), self.assertRaises(advisor.SensitiveContext):
                advisor.parse_request(encoded(value))
        safe = request(constraints=["Rotate the API key; no value is included.", "SHA-256 3D8509AE7DE11D77DBDC711AA320FC6D5064CE795464A8670696611B57093CAF"])
        advisor.parse_request(encoded(safe))

    def test_structured_secret_fields_are_rejected_recursively(self):
        candidates = [
            {"api_key": "abcdefghijklmnop"},
            {"credentials": {"api_key": "abcdefghijklmnop"}},
            {"deployment": {"client_secret": "abcdefghijklmnop"}},
            {"authorization": "Bearer abcdefghijklmnop"},
            {"ghp_abcdefghijklmnopqrstuvwxyz123456": True},
            {"github_pat_abcdefghijklmnopqrstuvwxyz123456": {}},
        ]
        for candidate in candidates:
            value = request(phase="reconcile", candidate_approach=candidate)
            with self.subTest(candidate=candidate), self.assertRaises(advisor.SensitiveContext):
                advisor.parse_request(encoded(value))
        advisor.parse_request(
            encoded(request(phase="reconcile", candidate_approach={"strategy": "Use a token bucket."}))
        )

    def test_initial_candidate_rejected_but_reconcile_candidate_allowed(self):
        with self.assertRaisesRegex(advisor.BridgeProblem, "candidate_approach"):
            advisor.parse_request(encoded(request(candidate_approach="Preferred answer")))
        advisor.parse_request(encoded(request(phase="reconcile", candidate_approach="Preferred answer")))

    def test_unknown_fields_and_nonfinite_numbers_reject(self):
        value = request(unexpected=True)
        with self.assertRaises(advisor.BridgeProblem):
            advisor.parse_request(encoded(value))
        raw = encoded(request()).replace(b'"call_count":0', b'"call_count":NaN')
        with self.assertRaises(advisor.BridgeProblem):
            advisor.parse_request(raw)

    def test_success_criteria_must_not_be_empty(self):
        with self.assertRaisesRegex(advisor.BridgeProblem, "success_criteria"):
            advisor.parse_request(encoded(request(success_criteria=[])))


class DiscoveryTests(unittest.TestCase):
    def test_newest_compatible_candidate_wins(self):
        with tempfile.TemporaryDirectory() as directory:
            old = Path(directory) / "old.exe"
            current = Path(directory) / "current.exe"
            future = Path(directory) / "future.exe"
            for path in (old, current, future):
                path.touch()
            versions = {str(old): (2, 1, 279), str(current): (2, 1, 280), str(future): (3, 0, 0)}

            def probe(path, source):
                version = versions[str(path)]
                if version[0] != 2 or version < advisor.MIN_CLAUDE_VERSION:
                    raise advisor.BridgeProblem("incompatible_cli", "bad")
                return advisor.ClaudeCandidate(str(path), source, version, ".".join(map(str, version)))

            chosen = advisor.resolve_claude(
                {}, candidates=[(old, "winget"), (current, "npm"), (future, "path")], probe=probe
            )
            self.assertEqual(str(current), chosen.path)

    def test_semantic_version_sort_and_override_priority(self):
        with tempfile.TemporaryDirectory() as directory:
            a = Path(directory) / "a.exe"
            b = Path(directory) / "b.exe"
            a.touch()
            b.touch()

            def probe(path, source):
                version = (2, 10, 0) if path == a else (2, 9, 999)
                return advisor.ClaudeCandidate(str(path), source, version, ".".join(map(str, version)))

            self.assertEqual(str(a), advisor.resolve_claude({}, candidates=[(b, "path"), (a, "npm")], probe=probe).path)

    def test_override_is_exclusive_and_missing_fails_closed(self):
        env = {"FABLE_ADVISOR_CLAUDE_EXE": "C:/missing/claude.exe"}
        candidates = advisor.enumerate_claude_candidates(env)
        self.assertEqual(1, len(candidates))
        self.assertEqual("environment_override", candidates[0][1])
        with self.assertRaisesRegex(advisor.BridgeProblem, "does not exist"):
            advisor.resolve_claude(env, candidates=candidates)

    def test_relative_override_rejects_before_probe(self):
        called = False

        def probe(path, source):
            nonlocal called
            called = True
            raise AssertionError("relative override must not be probed")

        with self.assertRaisesRegex(advisor.BridgeProblem, "absolute"):
            advisor.resolve_claude(
                {"FABLE_ADVISOR_CLAUDE_EXE": "claude.exe"},
                candidates=[(Path("claude.exe"), "environment_override")],
                probe=probe,
            )
        self.assertFalse(called)

    def test_probe_requires_version_and_exact_flags(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / ("claude.exe" if os.name == "nt" else "claude")
            executable.touch()

            def runner(args, **kwargs):
                if "--version" in args:
                    return subprocess.CompletedProcess(args, 0, "2.1.280 (Claude Code)\n", "")
                if "auth" in args:
                    auth = {"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty", "subscriptionType": "team"}
                    return subprocess.CompletedProcess(args, 0, json.dumps(auth), "")
                return subprocess.CompletedProcess(args, 0, " ".join(advisor.REQUIRED_CLI_FLAGS), "")

            candidate = advisor.probe_claude_candidate(
                executable, "test", run=runner, verify_publisher=lambda path, source: None
            )
            self.assertEqual((2, 1, 280), candidate.version)
            self.assertEqual(advisor._sha256_file(executable), candidate.sha256)

            def missing_flag_runner(args, **kwargs):
                if "--version" in args:
                    return subprocess.CompletedProcess(args, 0, "2.1.280 (Claude Code)\n", "")
                if "auth" in args:
                    return subprocess.CompletedProcess(args, 0, "{}", "")
                flags = [flag for flag in advisor.REQUIRED_CLI_FLAGS if flag != "--tools"] + ["--tools-extra"]
                return subprocess.CompletedProcess(args, 0, " ".join(flags), "")

            with self.assertRaisesRegex(advisor.BridgeProblem, "missing required"):
                advisor.probe_claude_candidate(
                    executable, "test", run=missing_flag_runner, verify_publisher=lambda path, source: None
                )

    def test_signed_out_probe_exposes_recovery_without_launching_inference(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / ("claude.exe" if os.name == "nt" else "claude")
            executable.touch()
            calls = []

            def runner(args, **kwargs):
                calls.append(args)
                if "--version" in args:
                    return subprocess.CompletedProcess(args, 0, "2.1.300 (Claude Code)", "")
                if "auth" in args:
                    auth = {"loggedIn": False, "authMethod": "none", "apiProvider": "firstParty"}
                    return subprocess.CompletedProcess(args, 1, json.dumps(auth), "")
                return subprocess.CompletedProcess(args, 0, " ".join(advisor.REQUIRED_CLI_FLAGS), "")

            with self.assertRaises(advisor.BridgeProblem) as caught:
                advisor.probe_claude_candidate(
                    executable, "test", run=runner, verify_publisher=lambda path, source: None
                )
            self.assertEqual("authentication", caught.exception.kind)
            self.assertIn("claude auth login --claudeai", caught.exception.message)
            payload = advisor.envelope("failed", error=caught.exception)
            self.assertEqual(caught.exception.message, payload["user_status"]["message"])
            self.assertTrue(all("-p" not in args for args in calls))

    def test_discovery_preserves_actionable_error_among_stale_and_missing_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            current = root / "current.exe"
            stale = root / "stale.exe"
            missing = root / "missing.exe"
            current.touch()
            stale.touch()
            for problem in (
                advisor.BridgeProblem("authentication", "Claude Code is signed out. Run claude auth login --claudeai."),
                advisor.BridgeProblem("incompatible_cli", "Opus 5.5 requires an upgrade of this installation."),
            ):
                def probe(path, source):
                    if path == current:
                        raise problem
                    if path == stale:
                        raise advisor.BridgeProblem("incompatible_cli", "Stale installation.")
                    raise advisor.BridgeProblem("missing_cli", "Candidate does not exist.")

                with self.subTest(kind=problem.kind), self.assertRaises(advisor.BridgeProblem) as caught:
                    advisor.resolve_claude(
                        {}, candidates=[(current, "npm"), (stale, "winget"), (missing, "path")], probe=probe
                    )
                self.assertIs(problem, caught.exception)

    def test_prerelease_and_old_versions_reject(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / ("claude.exe" if os.name == "nt" else "claude")
            executable.touch()
            for version in ("2.1.279", "2.1.280-beta.1", "3.0.0"):
                def runner(args, **kwargs):
                    if "--version" in args:
                        return subprocess.CompletedProcess(args, 0, version, "")
                    if "auth" in args:
                        return subprocess.CompletedProcess(args, 0, "{}", "")
                    return subprocess.CompletedProcess(args, 0, " ".join(advisor.REQUIRED_CLI_FLAGS), "")
                with self.subTest(version=version), self.assertRaises(advisor.BridgeProblem):
                    advisor.probe_claude_candidate(
                        executable, "test", run=runner, verify_publisher=lambda path, source: None
                    )

    def test_untrusted_publisher_rejects_before_candidate_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / ("claude.exe" if os.name == "nt" else "claude")
            executable.touch()
            executed = False

            def runner(args, **kwargs):
                nonlocal executed
                executed = True
                raise AssertionError("untrusted candidate must not run")

            def reject(path, source):
                raise advisor.BridgeProblem("incompatible_cli", "unsigned")

            with self.assertRaisesRegex(advisor.BridgeProblem, "unsigned"):
                advisor.probe_claude_candidate(executable, "path", run=runner, verify_publisher=reject)
            self.assertFalse(executed)

    @unittest.skipUnless(os.name == "nt", "Windows Authenticode verifier behavior")
    def test_publisher_verifier_disables_user_module_resolution(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "claude.exe"
            executable.touch()

            def runner(args, **kwargs):
                script = args[-1]
                env = kwargs["env"]
                trusted = advisor.trusted_windows_directory()
                trusted_modules = trusted / "System32/WindowsPowerShell/v1.0/Modules"
                self.assertEqual(str(trusted_modules), env["PSModulePath"])
                self.assertEqual(
                    str(trusted_modules / "Microsoft.PowerShell.Security/Microsoft.PowerShell.Security.psd1"),
                    env["FABLE_ADVISOR_SECURITY_MODULE"],
                )
                for name in ("HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA"):
                    self.assertNotIn(name, env)
                self.assertIn("$PSModuleAutoLoadingPreference='None'", script)
                self.assertIn("Import-Module -Name $env:FABLE_ADVISOR_SECURITY_MODULE", script)
                self.assertIn("Microsoft.PowerShell.Security\\Get-AuthenticodeSignature", script)
                subject = 'CN="Anthropic, PBC", O="Anthropic, PBC", L=San Francisco, S=California, C=US'
                response = "|".join(
                    base64.b64encode(value.encode("utf-8")).decode("ascii")
                    for value in ("Valid", subject)
                )
                return subprocess.CompletedProcess(args, 0, response, "")

            advisor.verify_anthropic_publisher(executable, "test", run=runner)

    def test_revalidation_rejects_executable_changed_after_discovery(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / ("claude.exe" if os.name == "nt" else "claude")
            executable.write_bytes(b"first executable")
            initial = advisor.ClaudeCandidate(
                str(executable.resolve()),
                "test",
                (2, 1, 280),
                "2.1.280",
                advisor._sha256_file(executable),
            )
            executable.write_bytes(b"replacement executable")

            def probe(path, source):
                return advisor.ClaudeCandidate(
                    str(path), source, (2, 1, 280), "2.1.280", advisor._sha256_file(path)
                )

            with self.assertRaisesRegex(advisor.BridgeProblem, "changed after discovery"):
                with advisor.revalidated_candidate(initial, probe=probe):
                    self.fail("a changed executable must never be yielded")

    @unittest.skipUnless(os.name == "nt", "Windows executable locking behavior")
    def test_executable_lock_denies_concurrent_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "claude.exe"
            executable.write_bytes(b"locked executable")
            with advisor.locked_executable(executable):
                with self.assertRaises(PermissionError):
                    executable.write_bytes(b"replacement")
            executable.write_bytes(b"replacement after unlock")


class WorkspaceTests(unittest.TestCase):
    def test_default_workspace_is_exact_invocation_directory_without_parent_expansion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "monorepo" / "product"
            project.mkdir(parents=True)
            with mock.patch.object(Path, "cwd", return_value=project):
                self.assertEqual(project.resolve(), advisor.resolve_workspace())

    def test_missing_file_relative_network_and_broad_roots_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ordinary_file = root / "file.txt"
            ordinary_file.touch()
            invalid = [
                str(root / "missing"), str(ordinary_file), "relative/path",
                "\\\\host\\share", "//host/share", str(Path(root.anchor)), str(Path.home()),
            ]
            for value in invalid:
                with self.subTest(value=value), self.assertRaises(advisor.BridgeProblem) as caught:
                    advisor.resolve_workspace(value)
                self.assertEqual("invalid_workspace", caught.exception.kind)

    def test_workspace_is_explicit_in_prompt_and_metadata_but_not_inserted_into_brief(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            brief = request()
            prompt = advisor.build_task_prompt(brief, workspace=workspace)
            self.assertIn(json.dumps(str(workspace)), prompt)
            self.assertIn("current uncommitted", prompt)
            self.assertNotIn("workspace", brief)
            metadata = advisor.envelope("completed", workspace=workspace)["metadata"]
            self.assertEqual(str(workspace), metadata["workspace_root"])
            self.assertEqual("read_only", metadata["workspace_access"])
            self.assertEqual("brief_only", advisor.envelope("completed")["metadata"]["workspace_access"])


class CommandAndInvocationTests(unittest.TestCase):
    def test_command_is_read_only_budgeted_and_contains_no_task_data(self):
        sentinel = "TASK-SENTINEL-1ac5c4"
        command = advisor.build_command("C:/Program Files/Claude/claude.exe")
        joined = " ".join(command)
        self.assertNotIn(sentinel, joined)
        self.assertEqual("dontAsk", command[command.index("--permission-mode") + 1])
        self.assertEqual("", command[command.index("--tools") + 1])
        self.assertEqual("claude-opus-5-5", command[command.index("--model") + 1])
        self.assertEqual("max", command[command.index("--effort") + 1])
        self.assertEqual("1.00", command[command.index("--max-budget-usd") + 1])
        settings = json.loads(command[command.index("--settings") + 1])
        self.assertEqual(["claude-opus-5-5"], settings["availableModels"])
        self.assertIs(False, settings["switchModelsOnFlag"])
        self.assertEqual([], settings["fallbackModel"])
        for flag in ("--safe-mode", "--restricted", "--strict-mcp-config", "-p", "--no-session-persistence", "--json-schema"):
            self.assertEqual(1, command.count(flag))
        for forbidden in ("--bare", "--fallback-model", "--resume", "--continue", "--dangerously-skip-permissions"):
            self.assertNotIn(forbidden, command)
        prompt = advisor.build_task_prompt(request(decision_question=sentinel))
        self.assertIn(sentinel, prompt)

        for model, effort in (
            ("opus", "max"), ("claude-opus-5", "max"), ("fable", "max"),
            ("claude-opus-5-5", "high"), ("claude-opus-5-5", "ultra"),
            ("claude-opus-5-5", "ultracode"), ("sonnet", "high"),
        ):
            with self.subTest(model=model, effort=effort), self.assertRaises(advisor.BridgeProblem):
                advisor.build_command("C:/Program Files/Claude/claude.exe", model=model, effort=effort)

    def test_workspace_enables_only_scoped_read_tools_and_denies_sensitive_files(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            command = advisor.build_command("claude.exe", workspace=workspace)
            self.assertEqual("Read,Glob,Grep", command[command.index("--tools") + 1])
            self.assertEqual(str(workspace), command[command.index("--add-dir") + 1])
            self.assertEqual("none", command[command.index("--permission-prompts") + 1])
            settings = json.loads(command[command.index("--settings") + 1])
            permissions = settings["permissions"]
            self.assertTrue(permissions["blockReadsOutsideWorkingDirectories"])
            for tool in ("Write", "Edit", "Bash", "PowerShell", "Agent", "WebFetch"):
                self.assertIn(tool, permissions["deny"])
            for path in ("//**/.env", "//**/.env.*", "//**/*.pem", "//**/credentials.json", "//**/.git/**"):
                self.assertIn(f"Read({path})", permissions["deny"])
            brief_command = advisor.build_command("claude.exe")
            self.assertNotIn("--add-dir", brief_command)
            self.assertEqual("", brief_command[brief_command.index("--tools") + 1])

    def test_child_receives_task_only_on_stdin_and_uses_neutral_cwd(self):
        sentinel = "ONLY-IN-STDIN-62cd"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            neutral = root / "neutral"
            neutral.mkdir()
            fake = root / "fake.py"
            fake.write_text(
                "import json, os, sys\n"
                "print(json.dumps({'stdin': sys.stdin.read(), 'argv': sys.argv[1:], 'cwd': os.getcwd()}))\n",
                encoding="utf-8",
            )
            result = advisor.invoke_claude(
                [sys.executable, str(fake), "--fixed"], sentinel, cwd=str(neutral), heartbeat_seconds=1
            )
            payload = json.loads(result.stdout)
            self.assertEqual(sentinel, payload["stdin"])
            self.assertNotIn(sentinel, json.dumps(payload["argv"]))
            self.assertEqual(str(neutral), payload["cwd"])

    def test_fake_cli_pipeline_returns_valid_advice_without_workspace_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            neutral = root / "neutral"
            workspace.mkdir()
            neutral.mkdir()
            protected = workspace / "state.txt"
            protected.write_text("unchanged", encoding="utf-8")
            fake = root / "fake_claude.py"
            fake_payload = {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "structured_output": advice_result(),
                "modelUsage": {
                    "claude-opus-5-5": {"inputTokens": 900, "outputTokens": 300, "costUSD": 0.24},
                    "claude-haiku-4-5-20251001": {
                        "inputTokens": 200,
                        "outputTokens": 8,
                        "costUSD": 0.0004,
                    },
                },
                "total_cost_usd": 0.2404,
            }
            fake.write_text(
                "import sys\n"
                "sys.stdin.read()\n"
                f"print({json.dumps(fake_payload, separators=(',', ':'))!r})\n",
                encoding="utf-8",
            )
            before = protected.read_bytes()
            invocation_result = advisor.invoke_claude(
                [sys.executable, str(fake), "--safe-mode"],
                "sanitized brief",
                cwd=str(neutral),
            )
            parsed_advice, model, cost = advisor.parse_claude_result(invocation_result)
            self.assertEqual(1, len(invocation_result.stdout.strip().splitlines()))
            self.assertEqual("claude-opus-5-5", model)
            self.assertEqual(0.2404, cost)
            self.assertEqual("high", parsed_advice["confidence"])
            self.assertEqual(before, protected.read_bytes())
            self.assertEqual(["state.txt"], [item.name for item in workspace.iterdir()])

    def test_child_environment_drops_credentials_and_model_overrides(self):
        env = {
            "PATH": "C:/attacker",
            "SystemRoot": "C:/attacker-root",
            "WINDIR": "C:/attacker-root",
            "COMSPEC": "C:/attacker-root/cmd.exe",
            "ANTHROPIC_API_KEY": "secret-value",
            "ANTHROPIC_BASE_URL": "https://attacker.invalid",
            "CLAUDE_CODE_OAUTH_TOKEN": "secret-value",
            "CLAUDE_CODE_USE_BEDROCK": "1",
            "GITHUB_TOKEN": "secret-value",
            "ANTHROPIC_MODEL": "opus",
            "HTTPS_PROXY": "https://attacker.invalid",
            "AWS_PROFILE": "attacker",
            "NODE_OPTIONS": "--require attacker.js",
            "NORMAL_SETTING": "kept",
        }
        sanitized = advisor.sanitized_child_env(env)
        self.assertNotIn("attacker", sanitized["PATH"].lower())
        if os.name == "nt":
            trusted = str(advisor.trusted_windows_directory())
            self.assertEqual(trusted, sanitized["SYSTEMROOT"])
            self.assertEqual(trusted, sanitized["WINDIR"])
            self.assertEqual(str(Path(trusted) / "System32/cmd.exe"), sanitized["COMSPEC"])
        for name in (
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_BASE_URL",
            "CLAUDE_CODE_OAUTH_TOKEN",
            "CLAUDE_CODE_USE_BEDROCK",
            "GITHUB_TOKEN",
            "ANTHROPIC_MODEL",
            "HTTPS_PROXY",
            "AWS_PROFILE",
            "NODE_OPTIONS",
            "NORMAL_SETTING",
        ):
            self.assertNotIn(name, sanitized)

    def test_heartbeat_is_stderr_only(self):
        with tempfile.TemporaryDirectory() as directory:
            stream = io.StringIO()
            result = advisor.invoke_claude(
                [sys.executable, "-c", "import sys,time; sys.stdin.read(); time.sleep(.12); print('ok')"],
                "brief",
                cwd=directory,
                heartbeat_seconds=0.03,
                stderr_stream=stream,
            )
            self.assertEqual("ok", result.stdout.strip())
            self.assertIn("[Claude Advisor · Consulting] Started.", stream.getvalue())
            self.assertGreaterEqual(stream.getvalue().count("Still working"), 2)

    def test_timeout_terminates_descendant_process_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "child-survived.txt"
            parent = root / "parent.py"
            child_code = f"import time; from pathlib import Path; time.sleep(2.5); Path({str(marker)!r}).write_text('alive')"
            parent.write_text(
                "import subprocess, sys, time\n"
                f"subprocess.Popen([sys.executable, '-c', {child_code!r}])\n"
                "time.sleep(30)\n",
                encoding="utf-8",
            )
            result = advisor.invoke_claude(
                [sys.executable, str(parent)],
                "brief",
                cwd=directory,
                timeout_seconds=0.2,
                heartbeat_seconds=1,
                stderr_stream=io.StringIO(),
            )
            self.assertTrue(result.timed_out)
            time.sleep(2.7)
            self.assertFalse(marker.exists(), "descendant survived timeout termination")


class ResultValidationTests(unittest.TestCase):
    def success_payload(self, model_usage=None, **overrides):
        payload = {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "structured_output": advice_result(),
            "modelUsage": model_usage
            if model_usage is not None
            else {
                "claude-opus-5-5": {"inputTokens": 1000, "outputTokens": 400, "costUSD": 0.29},
                "claude-haiku-4-5-20251001": {
                    "inputTokens": 903,
                    "outputTokens": 19,
                    "costUSD": 0.000998,
                },
            },
            "total_cost_usd": 0.29,
        }
        payload.update(overrides)
        return payload

    def test_valid_opus_plus_auxiliary_haiku(self):
        advice, model, cost = advisor.parse_claude_result(invocation(self.success_payload()))
        self.assertEqual("claude-opus-5-5", model)
        self.assertEqual(0.29, cost)
        self.assertEqual("high", advice["confidence"])

    def test_fable_cannot_be_approved_as_the_expected_model(self):
        payload = self.success_payload(
            model_usage={
                "claude-fable-5": {"inputTokens": 1000, "outputTokens": 400, "costUSD": 0.49},
                "claude-haiku-4-5-20251001": {"outputTokens": 9, "costUSD": 0.0005},
            }
        )
        with self.assertRaises(advisor.BridgeProblem) as caught:
            advisor.parse_claude_result(invocation(payload), "claude-fable-5")
        self.assertEqual("internal_error", caught.exception.kind)

    def test_cost_overshoot_is_reported_not_failed(self):
        _, _, cost = advisor.parse_claude_result(invocation(self.success_payload(total_cost_usd=1.17)))
        self.assertEqual(1.17, cost)

    def test_model_mismatch_matrix(self):
        fixtures = [
            {},
            {"claude-opus-5": {"outputTokens": 100, "costUSD": 0.2}},
            {
                "claude-opus-5-5": {"inputTokens": 4, "outputTokens": 0, "costUSD": 0.0246484},
                "claude-opus-4-8": {"inputTokens": 4, "outputTokens": 7794, "costUSD": 0.2477675},
            },
            {"claude-opus-4-8": {"outputTokens": 100, "costUSD": 0.2}},
            {"claude-fable-5": {"outputTokens": 100, "costUSD": 0.2}},
            {
                "claude-opus-5-5": {"outputTokens": 100, "costUSD": 0.2},
                "claude-fable-5": {"outputTokens": 20, "costUSD": 0.02},
            },
            {"claude-opus-5-5": {"outputTokens": 0, "costUSD": 0}},
            {"claude-opus-50": {"outputTokens": 100, "costUSD": 0.2}},
            {"claude-opus-5-fable": {"outputTokens": 100, "costUSD": 0.2}},
            {"claude-opus-5-sonnet": {"outputTokens": 100, "costUSD": 0.2}},
            {"claude-opus-5-mythos": {"outputTokens": 100, "costUSD": 0.2}},
            {"claude-opus-5-5": {"outputTokens": True, "costUSD": 0.2}},
            {"claude-opus-5-5": {"outputTokens": -1, "costUSD": 0.2}},
            {
                "claude-opus-5-5": {"outputTokens": 1, "costUSD": 0.01},
                "claude-haiku-4-5": {"outputTokens": 400, "costUSD": 0.2},
            },
            {
                "claude-opus-5-5": {"outputTokens": 100, "costUSD": 0.2},
                "claude-haiku-4-5-20251001-opus": {"outputTokens": 1, "costUSD": 0.0001},
            },
            {"claude-opus-5 task excerpt": {"outputTokens": 100, "costUSD": 0.2}},
        ]
        for usage in fixtures:
            with self.subTest(usage=usage), self.assertRaises(advisor.BridgeProblem) as caught:
                advisor.parse_claude_result(invocation(self.success_payload(model_usage=usage)))
            self.assertEqual("model_mismatch", caught.exception.kind)

    def test_schema_mismatch_matrix(self):
        bad_values = [None, {}, {**advice_result(), "extra": True}, {**advice_result(), "confidence": "certain"}]
        for value in bad_values:
            with self.subTest(value=value), self.assertRaises(advisor.BridgeProblem) as caught:
                advisor.parse_claude_result(invocation(self.success_payload(structured_output=value)))
            self.assertEqual("schema_mismatch", caught.exception.kind)

    def test_result_level_budget_error_wins_over_partial_output(self):
        payload = self.success_payload(
            subtype="error_max_budget_usd",
            is_error=True,
            result="partial structured result must not win",
            errors=["Reached maximum budget ($0.05)"],
            total_cost_usd=0.092981,
        )
        with self.assertRaises(advisor.BridgeProblem) as caught:
            advisor.parse_claude_result(invocation(payload))
        self.assertEqual("budget_exhaustion", caught.exception.kind)
        self.assertEqual("Reached maximum budget ($0.05)", caught.exception.message)
        self.assertEqual(0.092981, caught.exception.metadata["reported_cost_usd"])
        self.assertIn("claude-opus-5-5", caught.exception.metadata["reported_model"])

    def test_auth_unavailable_refusal_and_malformed_json(self):
        cases = [
            ({"is_error": True, "subtype": "error", "result": "Authentication failed; please login"}, "authentication"),
            ({"is_error": True, "subtype": "error", "result": "Requested model is not available"}, "unavailable_model"),
            ({"is_error": True, "subtype": "error", "result": "Request refused by content policy"}, "refusal"),
        ]
        for payload, kind in cases:
            with self.subTest(kind=kind), self.assertRaises(advisor.BridgeProblem) as caught:
                advisor.parse_claude_result(invocation(payload))
            self.assertEqual(kind, caught.exception.kind)
        broken = advisor.InvocationResult(0, "not-json", "", 0.1)
        with self.assertRaises(advisor.BridgeProblem) as caught:
            advisor.parse_claude_result(broken)
        self.assertEqual("malformed_json", caught.exception.kind)

    def test_nonfinite_or_missing_cost_rejects(self):
        for cost in (None, -1, True, float("inf"), float("nan")):
            payload = self.success_payload()
            if cost is None:
                payload.pop("total_cost_usd")
            else:
                payload["total_cost_usd"] = cost
            with self.subTest(cost=cost), self.assertRaises(advisor.BridgeProblem):
                advisor.parse_claude_result(invocation(payload))

    def test_error_sanitization_does_not_echo_secret(self):
        payload = {"is_error": True, "subtype": "error", "result": "Authentication failed api_key=abcdefghijklmnop"}
        with self.assertRaises(advisor.BridgeProblem) as caught:
            advisor.parse_claude_result(invocation(payload))
        self.assertNotIn("abcdefghijklmnop", caught.exception.message)
        self.assertIn("[REDACTED]", caught.exception.message)

    def test_error_sanitization_does_not_echo_prompt_or_mixed_case_tags(self):
        payload = {
            "type": "result",
            "subtype": "error",
            "is_error": True,
            "result": "Authentication failed <ADVICE_REQUEST_V1>private customer excerpt</ADVICE_REQUEST_V1>",
            "total_cost_usd": 0,
            "modelUsage": {},
        }
        with self.assertRaises(advisor.BridgeProblem) as caught:
            advisor.parse_claude_result(invocation(payload, returncode=1))
        self.assertEqual("authentication", caught.exception.kind)
        self.assertNotIn("private customer excerpt", caught.exception.message)


class CliEnvelopeTests(unittest.TestCase):
    def test_explicit_workspace_and_brief_only_reach_real_command_and_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            candidate = advisor.ClaudeCandidate("claude.exe", "test", (2, 1, 300), "2.1.300", "A" * 64)
            result = invocation({
                "type": "result", "subtype": "success", "is_error": False,
                "structured_output": advice_result(),
                "modelUsage": {"claude-opus-5-5": {"outputTokens": 30, "costUSD": 0.01}},
                "total_cost_usd": 0.01,
            })

            @contextmanager
            def revalidated(value):
                yield value

            for arguments, expected_workspace in (
                (["--workspace", str(workspace)], workspace),
                (["--brief-only"], None),
            ):
                stdin = io.TextIOWrapper(io.BytesIO(encoded(request())), encoding="utf-8")
                stdout_bytes = io.BytesIO()
                stdout = io.TextIOWrapper(stdout_bytes, encoding="utf-8")
                with (
                    self.subTest(arguments=arguments),
                    mock.patch.object(sys, "stdin", stdin),
                    mock.patch.object(sys, "stdout", stdout),
                    mock.patch.object(sys, "stderr", io.StringIO()),
                    mock.patch.object(Path, "cwd", return_value=Path.home()),
                    mock.patch.object(advisor, "resolve_claude", return_value=candidate),
                    mock.patch.object(advisor, "revalidated_candidate", side_effect=revalidated),
                    mock.patch.object(advisor, "invoke_claude", return_value=result) as invoke,
                ):
                    self.assertEqual(0, advisor.main(arguments))
                    payload = json.loads(stdout_bytes.getvalue())
                    command, prompt = invoke.call_args.args
                    self.assertEqual("completed", payload["status"])
                    if expected_workspace is not None:
                        self.assertEqual(str(workspace), payload["metadata"]["workspace_root"])
                        self.assertEqual("read_only", payload["metadata"]["workspace_access"])
                        self.assertEqual(str(workspace), command[command.index("--add-dir") + 1])
                        self.assertIn(json.dumps(str(workspace)), prompt)
                    else:
                        self.assertIsNone(payload["metadata"]["workspace_root"])
                        self.assertEqual("brief_only", payload["metadata"]["workspace_access"])
                        self.assertNotIn("--add-dir", command)
                        self.assertEqual("", command[command.index("--tools") + 1])
                        self.assertIn("No workspace access", prompt)

    def test_completed_main_pipeline_emits_one_envelope_and_keeps_workspace_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            protected = workspace / "state.txt"
            protected.write_text("unchanged", encoding="utf-8")
            fake = root / "fake_claude.py"
            fake_payload = {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "structured_output": advice_result(),
                "modelUsage": {
                    "claude-opus-5-5": {"inputTokens": 900, "outputTokens": 300, "costUSD": 0.24},
                    "claude-haiku-4-5-20251001": {
                        "inputTokens": 200,
                        "outputTokens": 8,
                        "costUSD": 0.0004,
                    },
                },
                "total_cost_usd": 0.2404,
            }
            fake.write_text(
                "import sys\n"
                "sys.stdin.read()\n"
                f"print({json.dumps(fake_payload, separators=(',', ':'))!r})\n",
                encoding="utf-8",
            )
            candidate = advisor.ClaudeCandidate(
                str(Path(sys.executable).resolve()), "test", (2, 1, 280), "2.1.280", "A" * 64
            )

            @contextmanager
            def revalidated(value):
                self.assertEqual(candidate, value)
                yield value

            stdin = io.TextIOWrapper(io.BytesIO(encoded(request())), encoding="utf-8")
            stdout_bytes = io.BytesIO()
            stdout = io.TextIOWrapper(stdout_bytes, encoding="utf-8")
            stderr = io.StringIO()
            with (
                mock.patch.object(sys, "stdin", stdin),
                mock.patch.object(sys, "stdout", stdout),
                mock.patch.object(sys, "stderr", stderr),
                mock.patch.object(advisor, "resolve_claude", return_value=candidate),
                mock.patch.object(Path, "cwd", return_value=workspace),
                mock.patch.object(advisor, "revalidated_candidate", side_effect=revalidated),
                mock.patch.object(
                    advisor, "build_command", return_value=[sys.executable, str(fake)]
                ) as build_command,
            ):
                returncode = advisor.main([])
            payload = json.loads(stdout_bytes.getvalue().decode("utf-8"))
            self.assertEqual(0, returncode)
            self.assertEqual(1, len(stdout_bytes.getvalue().splitlines()))
            self.assertEqual("completed", payload["status"])
            self.assertEqual(
                {
                    "label": "Claude Advisor",
                    "state": "ready",
                    "message": "Independent advice is ready.",
                },
                payload["user_status"],
            )
            self.assertEqual("Claude Advisor", payload["metadata"]["display_name"])
            self.assertEqual("claude-opus-5-5", payload["metadata"]["requested_model"])
            self.assertEqual("claude-opus-5-5", payload["metadata"]["reported_model"])
            self.assertEqual("max", payload["metadata"]["requested_effort"])
            self.assertEqual(0.2404, payload["metadata"]["reported_cost_usd"])
            build_command.assert_called_once_with(
                candidate.path, model="claude-opus-5-5", effort="max", workspace=workspace.resolve()
            )
            self.assertEqual(str(workspace.resolve()), payload["metadata"]["workspace_root"])
            self.assertEqual("read_only", payload["metadata"]["workspace_access"])
            self.assertIn("[Claude Advisor · Consulting · Opus 5.5 max] Started.", stderr.getvalue())
            self.assertIn("[Claude Advisor · Ready] Independent advice is ready.", stderr.getvalue())
            self.assertEqual(b"unchanged", protected.read_bytes())
            self.assertEqual(["state.txt"], [item.name for item in workspace.iterdir()])

    def test_emit_bypasses_ascii_console_codec_with_utf8_bytes(self):
        raw = io.BytesIO()
        ascii_stdout = io.TextIOWrapper(raw, encoding="ascii", errors="strict")
        with mock.patch.object(sys, "stdout", ascii_stdout):
            advisor.emit({"recommendation": "Use A → B — safely"})
        self.assertEqual(
            {"recommendation": "Use A → B — safely"},
            json.loads(raw.getvalue().decode("utf-8")),
        )

    def test_skipped_cli_emits_one_json_envelope_without_discovery(self):
        value = request(gate_signals=[])
        completed = subprocess.run(
            [sys.executable, str(SCRIPT)],
            input=json.dumps(value),
            text=True,
            capture_output=True,
            timeout=10,
        )
        self.assertEqual(0, completed.returncode)
        self.assertEqual(1, len(completed.stdout.strip().splitlines()))
        payload = json.loads(completed.stdout)
        self.assertEqual("skipped", payload["status"])
        self.assertEqual("skipped", payload["user_status"]["state"])
        self.assertEqual("No external review was needed.", payload["user_status"]["message"])
        self.assertIsNone(payload["metadata"]["chosen_executable"])

    def test_malformed_input_emits_one_failed_envelope(self):
        completed = subprocess.run(
            [sys.executable, str(SCRIPT)], input="{broken", text=True, capture_output=True, timeout=10
        )
        self.assertEqual(1, completed.returncode)
        self.assertEqual(1, len(completed.stdout.strip().splitlines()))
        payload = json.loads(completed.stdout)
        self.assertEqual("failed", payload["status"])
        self.assertEqual("unavailable", payload["user_status"]["state"])
        self.assertEqual("invalid_request", payload["error"]["kind"])

    def test_sensitive_input_is_skipped_without_echoing_value(self):
        value = request(constraints=["api_key=abcdefghijklmnop"])
        completed = subprocess.run(
            [sys.executable, str(SCRIPT)], input=json.dumps(value), text=True, capture_output=True, timeout=10
        )
        payload = json.loads(completed.stdout)
        self.assertEqual("skipped", payload["status"])
        self.assertEqual("unsafe_context", payload["error"]["kind"])
        self.assertNotIn("abcdefghijklmnop", completed.stdout + completed.stderr)


if __name__ == "__main__":
    unittest.main()
