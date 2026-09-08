"""The security scan: the gate that must never report clean for a scan it did not run.

Each test below is a way that a security gate lies. It says nothing blocks when
no scanner was installed; it counts an absent tool as a pass; it lets a marker
with no reason silence a finding; it lets an agent annotate its way past a
must-fix; or it expires an acceptance because an unrelated edit moved the line.
The assertions are about which findings block and what the exit code is,
because the exit code is the only part of it CI reads.

The scanners themselves are replaced at the two seams the script exposes --
`which` and `run_command` -- so the suite asserts on the POLICY, not on which
tools happen to be installed on the host running it. Requiring the real
gitleaks would make these tests skip silently in exactly the environment where
the gate matters least being tested.

Every scanner id used here is deliberately fake (S9001, RULE-FAKE-1). The
markers below sit inside string literals, which the annotation walk does not
read as acceptances -- but that is one edit away from not being true, and a
real id in a fixture would then accept that finding repo-wide when the scan is
run against this repo: the tests silencing the gate they exist to check.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCAN = ROOT / "scripts" / "csd-security-scan"
POLICY = json.loads((ROOT / "config" / "security-policy.json").read_text())


def load_scan() -> Any:
    loader = importlib.machinery.SourceFileLoader("csd_security_scan", str(SCAN))
    spec = importlib.util.spec_from_loader("csd_security_scan", loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def install(mod, monkeypatch, present: set[str], outputs: dict[str, tuple[int, Any]]):
    """Stand in for the scanners.

    `present` is keyed by TOOL name and `outputs` by the executable actually
    invoked, because those differ for exactly one scanner: cargo-audit is a
    separate binary that is run as `cargo audit`. That mismatch is the reason
    the script looks up the tool rather than command[0], so the fake has to
    reproduce it or the test would not exercise the real lookup.
    """

    monkeypatch.setattr(
        mod, "which", lambda tool: f"/usr/bin/{tool}" if tool in present else None
    )

    def fake_run(cmd, cwd, timeout=900):
        rc, payload = outputs.get(cmd[0], (0, None))
        text = "" if payload is None else json.dumps(payload)
        for i, arg in enumerate(cmd):
            # gitleaks writes a report FILE and prints only a banner. The fake
            # behaves the same way, so a regression back to parsing its stdout
            # shows up here as zero secrets rather than as a passing test.
            if arg == "--report-path" and i + 1 < len(cmd):
                Path(cmd[i + 1]).write_text(text)
                return rc, "INF no leaks found", ""
        return rc, text, ""

    monkeypatch.setattr(mod, "run_command", fake_run)


def ruff_out(repo: Path, code: str, row: int = 10, msg: str = "insecure thing"):
    return [
        {
            "code": code,
            "filename": str(repo / "src" / "a.py"),
            "location": {"row": row, "column": 1},
            "end_location": {"row": row, "column": 9},
            "message": msg,
            "fix": None,
            "url": "https://example.invalid",
        }
    ]


def gitleaks_out(rule: str = "generic-api-key"):
    return [
        {
            "RuleID": rule,
            "Description": "Detected a generic API key",
            "File": "deploy/unit.env",
            "StartLine": 4,
            "Secret": "REDACTED",
        }
    ]


def semgrep_out(severity: str, check_id: str = "RULE-FAKE-1"):
    return {
        "results": [
            {
                "check_id": check_id,
                "path": "src/b.py",
                "start": {"line": 7},
                "extra": {"severity": severity, "message": "tainted sink"},
            }
        ],
        "errors": [],
    }


def osv_out(vector: str, vuln_id: str = "OSV-FAKE-1"):
    return {
        "results": [
            {
                "source": {"path": "uv.lock", "type": "lockfile"},
                "packages": [
                    {
                        "package": {"name": "pkg", "version": "1.0", "ecosystem": "PyPI"},
                        "groups": [{"ids": [vuln_id], "max_severity": "0.0"}],
                        "vulnerabilities": [
                            {
                                "id": vuln_id,
                                "summary": "pkg has a hole",
                                "severity": [{"type": "CVSS_V3", "score": vector}],
                            }
                        ],
                    }
                ],
            }
        ]
    }


def accepted(rel: str, ident: str, severity: str, fields: tuple[str, ...] = ("all",)):
    """A source file carrying an acceptance marker for `ident`.

    Written as a comment block because that is the shape the policy's own
    example uses, and because the fields have to sit on their OWN lines to
    count -- the config's single-line JSON example is the case that must not.
    """
    lines = [f"# SEC-ACCEPTED: {ident} the fake rule"]
    if "all" in fields or "SEVERITY" in fields:
        lines.append(f"# SEVERITY: {severity}")
    if "all" in fields or "REACHABLE" in fields:
        lines.append("# REACHABLE: no -- the service binds loopback and no network")
        lines.append("#            input reaches this argv")
    if "all" in fields or "REVISIT" in fields:
        lines.append("# REVISIT: if this ever takes a path from a webhook body")
    lines.append("")
    lines.append("def thing():")
    lines.append("    return 1")
    return rel, "\n".join(lines) + "\n"


def write(root: Path, rel: str, body: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    return p


def ids(result: dict) -> list[str]:
    return [f["id"] for f in result["blocking"]]


# --------------------------------------------------------------------------- #
# Rule 1: the blanket rule. critical and high block a merge, medium and low do not
# --------------------------------------------------------------------------- #
def test_a_secret_is_critical_and_blocks_the_merge_gate(tmp_path, monkeypatch):
    """Any gitleaks finding is critical, because a committed credential is
    already published to everyone with repo access -- reachability is not a
    question anyone gets to argue about."""
    mod = load_scan()
    install(mod, monkeypatch, {"gitleaks"}, {"gitleaks": (1, gitleaks_out())})
    res = mod.scan(tmp_path, POLICY, "merge")
    assert [f["severity"] for f in res["findings"]] == ["critical"]
    assert ids(res) == ["generic-api-key"]
    assert mod.exit_code(res) == 1


def test_a_semgrep_error_is_high_and_blocks_the_merge_gate(tmp_path, monkeypatch):
    """ERROR maps to high through the config's own map, not through a guess.

    A scanner's own word for a severity is not the policy's word for it; if the
    mapping lived in this script instead of the config, changing the policy
    would mean changing code, and the config would quietly stop being the spec.
    """
    mod = load_scan()
    install(mod, monkeypatch, {"semgrep"}, {"semgrep": (1, semgrep_out("ERROR"))})
    res = mod.scan(tmp_path, POLICY, "merge")
    assert res["findings"][0]["severity"] == "high"
    assert ids(res) == ["RULE-FAKE-1"]
    assert mod.exit_code(res) == 1


def test_a_medium_does_not_block_a_merge(tmp_path, monkeypatch):
    """The merge gate is the blanket rule and nothing else.

    If medium blocked a merge, every ruff S finding in the fleet would be a
    merge blocker on day one of adoption, and the gate would be turned off
    within the hour rather than satisfied.
    """
    mod = load_scan()
    install(mod, monkeypatch, {"ruff"}, {"ruff": (1, ruff_out(tmp_path, "S9001"))})
    res = mod.scan(tmp_path, POLICY, "merge")
    assert res["findings"][0]["severity"] == "medium"
    assert res["blocking"] == []
    assert mod.exit_code(res) == 0


def test_a_low_blocks_neither_gate(tmp_path, monkeypatch):
    """ "acceptable day to day; fixed for a release when tractable" -- and a
    release is explicitly never blocked on an unreachable low."""
    mod = load_scan()
    install(mod, monkeypatch, {"semgrep"}, {"semgrep": (1, semgrep_out("INFO"))})
    for gate in ("merge", "release"):
        res = mod.scan(tmp_path, POLICY, gate)
        assert res["findings"][0]["severity"] == "low"
        assert res["blocking"] == []
        assert mod.exit_code(res) == 0


def test_release_raises_the_gate_to_medium(tmp_path, monkeypatch):
    """The same finding, the same repo, a different question being asked.

    The whole point of two gates is that "may this merge" and "may this ship"
    have different answers; a single gate forces one of them to be wrong.
    """
    mod = load_scan()
    install(mod, monkeypatch, {"ruff"}, {"ruff": (1, ruff_out(tmp_path, "S9001"))})
    assert mod.scan(tmp_path, POLICY, "merge")["blocking"] == []
    res = mod.scan(tmp_path, POLICY, "release")
    assert ids(res) == ["S9001"]
    assert mod.exit_code(res) == 1


# --------------------------------------------------------------------------- #
# Rule 2: an annotation is a decision; three fields or it is a silence
# --------------------------------------------------------------------------- #
def test_a_three_field_annotation_accepts_a_medium_at_release(tmp_path, monkeypatch):
    """The acceptance path the policy actually grants.

    Without it the only way to ship with an unreachable medium would be to
    delete the finding or disable the scanner, both of which destroy the record
    of the decision -- which is the thing the annotation exists to keep.
    """
    mod = load_scan()
    write(tmp_path, *accepted("src/a.py", "S9001", "medium"))
    install(mod, monkeypatch, {"ruff"}, {"ruff": (1, ruff_out(tmp_path, "S9001"))})
    res = mod.scan(tmp_path, POLICY, "release")
    assert res["findings"][0]["accepted"] is True
    assert res["findings"][0]["accepted_by"]["file"] == "src/a.py"
    assert res["blocking"] == []
    assert mod.exit_code(res) == 0


def test_a_three_field_annotation_accepts_a_low(tmp_path, monkeypatch):
    """A low is acceptable anyway, so this asserts the annotation is RECORDED.

    An accepted low that reported `accepted: false` would look identical to an
    unexamined one in the JSON a planner reads, and the decision would have to
    be made again from scratch every cycle.
    """
    mod = load_scan()
    write(tmp_path, *accepted("src/b.py", "RULE-FAKE-1", "low"))
    install(mod, monkeypatch, {"semgrep"}, {"semgrep": (1, semgrep_out("INFO"))})
    res = mod.scan(tmp_path, POLICY, "release")
    assert res["accepted"] == 1
    assert res["findings"][0]["accepted"] is True


def test_a_two_field_annotation_does_not_suppress_the_finding(tmp_path, monkeypatch):
    """Missing REVISIT means nothing will ever bring the decision back.

    An acceptance with no revisit condition is a permanent exception dressed as
    a temporary one. Accepting two of three fields would make the third
    optional in practice, and the one that gets dropped is always the one that
    costs something to write.
    """
    mod = load_scan()
    write(
        tmp_path,
        *accepted("src/a.py", "S9001", "medium", fields=("SEVERITY", "REACHABLE")),
    )
    install(mod, monkeypatch, {"ruff"}, {"ruff": (1, ruff_out(tmp_path, "S9001"))})
    res = mod.scan(tmp_path, POLICY, "release")
    assert res["findings"][0]["accepted"] is False
    assert ids(res) == ["S9001"]
    assert res["annotations"][0]["missing"] == ["REVISIT"]
    assert mod.exit_code(res) == 1


def test_an_annotation_on_a_high_is_itself_a_failure(tmp_path, monkeypatch):
    """The policy's sharpest line: no acceptance path exists at high.

    A silently-ignored marker would be worse than a rejected one -- the agent
    that wrote it believes the finding is handled, and nothing in the output
    would say otherwise. So the attempt is surfaced as its own finding.
    """
    mod = load_scan()
    write(tmp_path, *accepted("src/b.py", "RULE-FAKE-1", "high"))
    install(mod, monkeypatch, {"semgrep"}, {"semgrep": (1, semgrep_out("ERROR"))})
    res = mod.scan(tmp_path, POLICY, "merge")
    assert res["findings"][0]["accepted"] is False
    assert len(res["violations"]) == 1
    assert res["violations"][0]["id"] == "RULE-FAKE-1"
    assert res["violations"][0]["severity"] == "high"
    assert mod.exit_code(res) == 1


def test_an_annotation_naming_another_id_does_not_travel(tmp_path, monkeypatch):
    """The control for every acceptance test above.

    Without it, an implementation that accepted any finding whenever ANY valid
    marker existed in the tree would pass all of them, and one annotation would
    silence the repo.
    """
    mod = load_scan()
    write(tmp_path, *accepted("src/a.py", "S9002", "medium"))
    install(mod, monkeypatch, {"ruff"}, {"ruff": (1, ruff_out(tmp_path, "S9001"))})
    res = mod.scan(tmp_path, POLICY, "release")
    assert res["findings"][0]["accepted"] is False
    assert ids(res) == ["S9001"]


def test_a_quoted_copy_of_the_policy_example_is_not_an_acceptance(tmp_path, monkeypatch):
    """The config's `example` field is a whole worked annotation on ONE line.

    Reading it as source would let the policy document accept, repo-wide,
    whatever id the example uses. Two things stop that: the policy file itself
    is skipped, and a marker sitting behind a quote or an assignment is source
    that MENTIONS the contract rather than source that accepts a finding. This
    asserts the second, which is what also covers the example pasted into a doc.
    """
    mod = load_scan()
    write(
        tmp_path,
        "notes.md",
        '"example": "# SEC-ACCEPTED: S9001 x\\n# SEVERITY: low\\n'
        '# REACHABLE: no\\n# REVISIT: never"\n',
    )
    install(mod, monkeypatch, {"ruff"}, {"ruff": (1, ruff_out(tmp_path, "S9001"))})
    res = mod.scan(tmp_path, POLICY, "release")
    assert res["annotations"] == []
    assert res["findings"][0]["accepted"] is False
    assert ids(res) == ["S9001"]


def test_fields_crammed_onto_the_marker_line_do_not_count(tmp_path, monkeypatch):
    """The fields must be on their OWN lines below the marker.

    One line holding all four is the shape of the config's example and of any
    generated one-liner -- not the shape of something a person wrote down and
    another person read. It is reported as an acceptance that does not count,
    which is different from being ignored: the author needs to be told.
    """
    mod = load_scan()
    write(
        tmp_path,
        "src/a.py",
        "# SEC-ACCEPTED: S9001 SEVERITY: low REACHABLE: no REVISIT: never\n"
        "def thing():\n    return 1\n",
    )
    install(mod, monkeypatch, {"ruff"}, {"ruff": (1, ruff_out(tmp_path, "S9001"))})
    res = mod.scan(tmp_path, POLICY, "release")
    assert res["annotations"][0]["missing"] == ["SEVERITY", "REACHABLE", "REVISIT"]
    assert res["findings"][0]["accepted"] is False
    assert ids(res) == ["S9001"]


# --------------------------------------------------------------------------- #
# Rule 3: absent is not clean
# --------------------------------------------------------------------------- #
def test_an_absent_scanner_is_reported_absent_not_clean(tmp_path, monkeypatch):
    """The policy names this failure in its own words.

    A scanner that is not installed asserts nothing, and a gate that omits it
    from the output is indistinguishable from one that ran it and found
    nothing -- the same lie as a skipped CI job reading as success.
    """
    mod = load_scan()
    install(mod, monkeypatch, {"ruff"}, {"ruff": (0, [])})
    res = mod.scan(tmp_path, POLICY, "merge")
    status = {r["tool"]: r["status"] for r in res["scanners"]}
    assert status["ruff"] == "ok"
    assert status["gitleaks"] == "absent"
    assert status["semgrep"] == "absent"
    assert status["osv-scanner"] == "absent"
    assert res["scanners_ok"] == 1
    # It ran one scanner and that one was clean, so nothing blocks -- but the
    # absent ones are still in the report, which is the whole point.
    assert mod.exit_code(res) == 0


def test_the_report_says_absent_scanners_are_not_clean(tmp_path, monkeypatch, capsys):
    """A status column nobody can interpret is not a disclosure.

    "absent" next to a green summary reads as "not applicable" unless the
    output says, in words, that nothing was asserted for it.
    """
    mod = load_scan()
    install(mod, monkeypatch, {"ruff"}, {"ruff": (0, [])})
    res = mod.scan(tmp_path, POLICY, "merge")
    mod.report(res, POLICY, False)
    out = capsys.readouterr().out
    assert "absent" in out
    assert "NOT clean" in out


def test_no_scanner_at_all_is_exit_three_not_zero(tmp_path, monkeypatch):
    """Nothing ran, so nothing was asserted, and exit 0 would claim otherwise.

    This is the failure the whole script exists for: a gate that goes green on
    a host where the tools were never installed protects nothing while looking
    exactly like a gate that works.
    """
    mod = load_scan()
    install(mod, monkeypatch, set(), {})
    res = mod.scan(tmp_path, POLICY, "merge")
    assert res["scanners_ok"] == 0
    assert res["findings"] == []
    assert mod.exit_code(res) == 3


def test_no_scanner_at_all_says_so_in_the_report(tmp_path, monkeypatch, capsys):
    """Exit 3 is for CI; the human reading the terminal needs the sentence."""
    mod = load_scan()
    install(mod, monkeypatch, set(), {})
    mod.report(mod.scan(tmp_path, POLICY, "merge"), POLICY, False)
    assert "NOTHING RAN" in capsys.readouterr().out


def test_a_scanner_that_ran_and_broke_is_an_error_not_a_pass(tmp_path, monkeypatch):
    """Unparseable output is not zero findings.

    semgrep --config auto reaches the network for its rules; on a host that
    cannot, it exits non-zero with prose on stderr. Treating that as an empty
    result set would report a clean SAST scan for a scan that never happened.
    """
    mod = load_scan()
    monkeypatch.setattr(mod, "which", lambda tool: f"/usr/bin/{tool}")
    monkeypatch.setattr(
        mod, "run_command", lambda cmd, cwd, timeout=900: (2, "not json", "boom")
    )
    res = mod.scan(tmp_path, POLICY, "merge")
    status = {r["scanner"]: r["status"] for r in res["scanners"]}
    assert status["secrets"] == status["python_sast"] == status["sast"] == "error"
    assert status["deps"] == "error"
    assert res["scanners_ok"] == 0
    assert mod.exit_code(res) == 3


def test_cargo_audit_is_not_applicable_without_a_cargo_toml(tmp_path, monkeypatch):
    """ "n/a" and "absent" are different claims and must not share a word.

    A Python repo has genuinely nothing for cargo-audit to say; a missing
    gitleaks means a real question went unasked. Collapsing the two would make
    the absent row unreadable in the repos where it matters.
    """
    mod = load_scan()
    install(mod, monkeypatch, {"ruff", "cargo-audit"}, {"ruff": (0, [])})
    res = mod.scan(tmp_path, POLICY, "merge")
    status = {r["scanner"]: r["status"] for r in res["scanners"]}
    assert status["rust_deps"] == "n/a"

    (tmp_path / "Cargo.toml").write_text("[package]\nname = 'x'\n")
    res = mod.scan(tmp_path, POLICY, "merge")
    status = {r["scanner"]: r["status"] for r in res["scanners"]}
    assert status["rust_deps"] == "ok"


def test_cargo_on_path_is_not_cargo_audit_on_path(tmp_path, monkeypatch):
    """cargo is on every dev box here; the audit subcommand is a separate crate.

    Checking command[0] would report the dependency scanner present, run it,
    get "no such subcommand" and hand back a scan that asserted nothing.
    """
    mod = load_scan()
    (tmp_path / "Cargo.toml").write_text("[package]\nname = 'x'\n")
    install(mod, monkeypatch, {"cargo"}, {})
    status = {r["scanner"]: r["status"] for r in mod.scan(tmp_path, POLICY)["scanners"]}
    assert status["rust_deps"] == "absent"


def test_a_declared_scanner_with_no_normaliser_is_not_clean(tmp_path, monkeypatch):
    """trivy is in the config and has no parser here.

    Skipping it silently would mean the config can grow a scanner that this
    script pretends to run. It is reported, with a status that says so.
    """
    mod = load_scan()
    install(mod, monkeypatch, {"trivy"}, {})
    res = mod.scan(tmp_path, POLICY, "merge")
    status = {r["tool"]: r["status"] for r in res["scanners"]}
    assert status["trivy"] == "unsupported"
    assert res["scanners_ok"] == 0
    assert mod.exit_code(res) == 3


# --------------------------------------------------------------------------- #
# Rule 4: gitleaks' own contract, which the config gets wrong in two places
# --------------------------------------------------------------------------- #
def test_gitleaks_exit_one_is_a_finding_not_a_failure(tmp_path, monkeypatch):
    """gitleaks exits 1 when it finds something.

    An implementation that read a non-zero exit as "the scanner broke" would
    downgrade every real secret into a tool error, which is the one severity
    the policy says can never be argued with.
    """
    mod = load_scan()
    install(mod, monkeypatch, {"gitleaks"}, {"gitleaks": (1, gitleaks_out())})
    res = mod.scan(tmp_path, POLICY, "merge")
    assert [r["status"] for r in res["scanners"] if r["tool"] == "gitleaks"] == ["ok"]
    assert res["counts"]["critical"] == 1


def test_the_gitleaks_report_path_is_redirected_to_a_real_file(tmp_path, monkeypatch):
    """MEASURED: `--report-path -` writes a file named `-`, not stdout.

    Run verbatim, the config's command prints a banner, drops a file called `-`
    in the repo, and yields zero secrets for every repo forever. The rewrite is
    what makes the declared command mean what it says.
    """
    mod = load_scan()
    seen = {}

    monkeypatch.setattr(mod, "which", lambda tool: f"/usr/bin/{tool}")

    def fake_run(cmd, cwd, timeout=900):
        if cmd[0] != "gitleaks":
            return 0, "", ""
        seen["cmd"] = list(cmd)
        Path(cmd[cmd.index("--report-path") + 1]).write_text(
            json.dumps(gitleaks_out("aws-access-token"))
        )
        return 1, "INF 87 commits scanned.", ""

    monkeypatch.setattr(mod, "run_command", fake_run)
    res = mod.scan(tmp_path, POLICY, "merge")
    assert "-" not in seen["cmd"][seen["cmd"].index("--report-path") + 1 :]
    assert not (tmp_path / "-").exists()
    assert ids(res) == ["aws-access-token"]


# --------------------------------------------------------------------------- #
# Rule 5: the fingerprint outlives an unrelated edit
# --------------------------------------------------------------------------- #
def test_a_fingerprint_survives_a_line_number_change(tmp_path, monkeypatch):
    """Adding an import above a finding must not invalidate its acceptance.

    A line-sensitive fingerprint expires acceptances on edits that have nothing
    to do with them, which trains people to re-accept findings without
    re-reading them -- the annotation becomes the ritual the policy exists to
    prevent.
    """
    mod = load_scan()

    def at(row: int) -> dict:
        return {"ruff": (1, ruff_out(tmp_path, "S9001", row=row))}

    install(mod, monkeypatch, {"ruff"}, at(10))
    before = mod.scan(tmp_path, POLICY, "release")["findings"][0]
    install(mod, monkeypatch, {"ruff"}, at(97))
    after = mod.scan(tmp_path, POLICY, "release")["findings"][0]
    assert before["line"] == 10
    assert after["line"] == 97
    assert before["fingerprint"] == after["fingerprint"]


def test_a_fingerprint_changes_when_the_finding_does(tmp_path, monkeypatch):
    """The control. A fingerprint that never changed would be a constant.

    Two different rules in the same file must not share an id, or accepting one
    would accept the other and the acceptance record would be meaningless.
    """
    mod = load_scan()
    install(mod, monkeypatch, {"ruff"}, {"ruff": (1, ruff_out(tmp_path, "S9001"))})
    a = mod.scan(tmp_path, POLICY, "release")["findings"][0]
    install(mod, monkeypatch, {"ruff"}, {"ruff": (1, ruff_out(tmp_path, "S9002"))})
    b = mod.scan(tmp_path, POLICY, "release")["findings"][0]
    assert a["fingerprint"] != b["fingerprint"]


# --------------------------------------------------------------------------- #
# Rule 6: severity comes from the scanner's own data, not from a guess
# --------------------------------------------------------------------------- #
def test_semgrep_severities_map_through_the_config(tmp_path, monkeypatch):
    mod = load_scan()
    for raw, expected in (("ERROR", "high"), ("WARNING", "medium"), ("INFO", "low")):
        install(mod, monkeypatch, {"semgrep"}, {"semgrep": (1, semgrep_out(raw))})
        res = mod.scan(tmp_path, POLICY, "merge")
        assert res["findings"][0]["severity"] == expected, raw


def test_a_cvss_vector_is_scored_not_guessed(tmp_path, monkeypatch):
    """osv-scanner reports the VECTOR, so the band has to be computed.

    The vector below is the one this repo's own pytest advisory carries, and
    osv independently scores it 6.8 -- a medium. A guess from the vector's
    shape would put it wherever the guess happened to favour, and the merge
    gate would then either block on it or ignore a high.
    """
    mod = load_scan()
    vector = "CVSS:3.1/AV:L/AC:L/PR:N/UI:N/S:C/C:L/I:L/A:L"
    assert mod.cvss3_base_score(vector) == 6.8
    install(mod, monkeypatch, {"osv-scanner"}, {"osv-scanner": (1, osv_out(vector))})
    res = mod.scan(tmp_path, POLICY, "merge")
    assert res["findings"][0]["severity"] == "medium"
    assert res["blocking"] == []


def test_a_critical_cvss_vector_blocks_the_merge_gate(tmp_path, monkeypatch):
    """The other end of the scale, so the band is not constant.

    log4shell's vector scores 10.0; a dependency scanner whose findings all
    landed on medium would let it through the merge gate.
    """
    mod = load_scan()
    vector = "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:C/C:H/I:H/A:H"
    assert mod.cvss3_base_score(vector) == 10.0
    install(mod, monkeypatch, {"osv-scanner"}, {"osv-scanner": (1, osv_out(vector))})
    res = mod.scan(tmp_path, POLICY, "merge")
    assert res["findings"][0]["severity"] == "critical"
    assert ids(res) == ["OSV-FAKE-1"]


def test_an_unscoreable_severity_falls_back_to_medium(tmp_path, monkeypatch):
    """CVSS:4.0 is not attempted, and the fallback must not be "clean".

    A wrong score is worse than no score, but a dropped finding is worse than
    both: defaulting to medium keeps it in the release gate where a human has
    to look at it.
    """
    mod = load_scan()
    assert mod.cvss3_base_score("CVSS:4.0/AV:N/AC:L") is None
    install(
        mod,
        monkeypatch,
        {"osv-scanner"},
        {"osv-scanner": (1, osv_out("CVSS:4.0/AV:N/AC:L"))},
    )
    res = mod.scan(tmp_path, POLICY, "release")
    assert res["findings"][0]["severity"] == "medium"
    assert ids(res) == ["OSV-FAKE-1"]


# --------------------------------------------------------------------------- #
# The CLI surface, which is the only part CI reads
# --------------------------------------------------------------------------- #
def test_a_missing_repo_is_exit_two_not_exit_one(tmp_path):
    """A wrong path must not read as "this repo is insecure"."""
    mod = load_scan()
    assert mod.main([str(tmp_path / "nowhere")]) == 2


def test_a_missing_policy_is_exit_two_not_a_builtin_default(tmp_path):
    """No fallback copy of the policy.

    A hardcoded default would drift from the file the operator edits, and the
    scan would go on enforcing a rule nobody wrote down.
    """
    mod = load_scan()
    assert mod.main([str(tmp_path), "--policy", str(tmp_path / "nope.json")]) == 2


def test_the_default_report_shows_one_finding_and_the_count_behind_it(
    tmp_path, monkeypatch, capsys
):
    """Eighty-six findings handed over at once produce a diff nobody reviews.

    csd-repo-onboard settled this for house rules; a security report that
    behaved differently would train the reader to skim it.
    """
    mod = load_scan()
    findings = ruff_out(tmp_path, "S9001") + ruff_out(tmp_path, "S9002", row=44)
    install(mod, monkeypatch, {"ruff"}, {"ruff": (1, findings)})
    res = mod.scan(tmp_path, POLICY, "release")
    mod.report(res, POLICY, False)
    out = capsys.readouterr().out
    assert "FIX THIS FIRST" in out
    assert "S9001" in out
    assert "S9002" not in out
    assert "1 more behind it" in out


def test_all_lists_every_finding(tmp_path, monkeypatch, capsys):
    """--all is what a human triaging a backlog needs; the default is not."""
    mod = load_scan()
    findings = ruff_out(tmp_path, "S9001") + ruff_out(tmp_path, "S9002", row=44)
    install(mod, monkeypatch, {"ruff"}, {"ruff": (1, findings)})
    mod.report(mod.scan(tmp_path, POLICY, "release"), POLICY, True)
    out = capsys.readouterr().out
    assert "S9001" in out
    assert "S9002" in out


def test_json_output_carries_every_normalised_field(tmp_path, monkeypatch, capsys):
    """--json is what a planner consumes, and it turns findings into goals.

    Without the fingerprint it cannot tell a new finding from one it already
    queued, and the loop would re-raise the same task every cycle.
    """
    mod = load_scan()
    install(mod, monkeypatch, {"gitleaks"}, {"gitleaks": (1, gitleaks_out())})
    rc = mod.main([str(tmp_path), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    f = payload["findings"][0]
    assert set(f) >= {"tool", "id", "severity", "file", "line", "title", "fingerprint"}
    assert f["tool"] == "gitleaks"
    assert f["file"] == "deploy/unit.env"


def test_release_flag_selects_the_release_gate(tmp_path, monkeypatch, capsys):
    """The flag is the only difference between "may merge" and "may ship"."""
    mod = load_scan()
    install(mod, monkeypatch, {"ruff"}, {"ruff": (1, ruff_out(tmp_path, "S9001"))})
    assert mod.main([str(tmp_path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["gate"] == "merge"
    assert mod.main([str(tmp_path), "--release", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["gate"] == "release"
