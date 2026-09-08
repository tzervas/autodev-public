"""The stub audit: the gate that keeps an annotated stub from becoming permanent.

Each test below corresponds to a way an unenforced stub contract goes wrong --
a stub with no blocker recorded, a stub whose goal was closed around it, a
silent body that a caller cannot distinguish from a working function, and an
interface declaration mistaken for unfinished work. The assertions are about
which findings the audit produces and what it exits with, because the exit code
is the only part of it CI reads.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "scripts" / "csd-stub-audit"


def load_audit() -> Any:
    loader = importlib.machinery.SourceFileLoader("csd_stub_audit", str(AUDIT))
    spec = importlib.util.spec_from_loader("csd_stub_audit", loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def write(root: Path, rel: str, body: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    return p


def names(result: dict) -> list[str]:
    return [s["name"] for s in result["stubs"]]


def by_name(result: dict, name: str) -> dict:
    hits = [s for s in result["stubs"] if s["name"] == name]
    assert hits, f"{name} not in {names(result)}"
    return hits[0]


ANNOTATED = '''
def reindex(store):
    """Rebuild the index from the source documents.

    STUB: not implemented.
    GOAL:     G-INDEX-3
    BLOCKED:  needs the Store.iter_documents contract from G-INDEX-1
    UNBLOCK:  land G-INDEX-1, then this becomes a 30-line implementation
    SEQUENCE: after G-INDEX-1, before G-INDEX-4
    OWNER:    implementer
    """
    raise NotImplementedError("G-INDEX-3")
'''


# --------------------------------------------------------------------------- #
# Rule 1: an unannotated stub fails the gate
# --------------------------------------------------------------------------- #
def test_a_stub_missing_one_marker_is_a_failure(tmp_path):
    """Three of four markers is not "nearly compliant", it is unschedulable.

    A stub with GOAL, BLOCKED and UNBLOCK but no SEQUENCE cannot be ordered
    against the other work, so nothing can decide when it becomes actionable --
    which is the whole point of annotating it. Accepting a partial annotation
    would let the gate pass on exactly the stubs it exists to track.
    """
    mod = load_audit()
    write(
        tmp_path,
        "a.py",
        '''
def reindex(store):
    """Rebuild the index.

    GOAL:    G-1
    BLOCKED: needs G-0
    UNBLOCK: land G-0
    """
    raise NotImplementedError("G-1")
''',
    )
    res = mod.audit(tmp_path, None)
    stub = by_name(res, "reindex")
    assert stub["missing"] == ["SEQUENCE"]
    assert stub["compliant"] is False
    assert res["failures"] == 1


def test_a_stub_with_no_annotation_at_all_names_every_missing_marker(tmp_path):
    """The report has to say what to add, not just that something is wrong.

    "csd-stub-audit failed" with no marker list makes the fix a guessing game
    against a document nobody has open, and a gate that is annoying to satisfy
    gets disabled rather than satisfied.
    """
    mod = load_audit()
    write(tmp_path, "a.py", "def later():\n    pass\n")
    res = mod.audit(tmp_path, None)
    assert by_name(res, "later")["missing"] == ["GOAL", "BLOCKED", "UNBLOCK", "SEQUENCE"]


def test_every_stub_body_shape_is_detected(tmp_path):
    """All five shapes are the same defect wearing different syntax.

    An audit that knew only `pass` would be trivially evaded -- by accident,
    not by malice: `...` is what an agent writes when it is sketching an
    interface, and a bare `return` is what it writes when the signature says
    None.
    """
    mod = load_audit()
    write(
        tmp_path,
        "a.py",
        '''
def a():
    pass


def b():
    ...


def c():
    return None


def d():
    return


def e():
    raise NotImplementedError


def f():
    """Only a docstring."""
''',
    )
    res = mod.audit(tmp_path, None)
    assert sorted(names(res)) == ["a", "b", "c", "d", "e", "f"]
    assert {s["name"]: s["body"] for s in res["stubs"]} == {
        "a": "pass",
        "b": "ellipsis",
        "c": "return-none",
        "d": "return-none",
        "e": "notimplemented",
        "f": "docstring-only",
    }


def test_a_function_that_does_work_is_not_a_stub(tmp_path):
    """The false-positive direction, which is the one that gets a gate ignored.

    `except OSError: pass` appears eleven times in this repo's own scripts and
    is not a stub; neither is a one-line function that returns a real value.
    Flagging those would have made the audit's first real run 100% noise.
    """
    mod = load_audit()
    write(
        tmp_path,
        "a.py",
        """
def swallow(path):
    try:
        path.unlink()
    except OSError:
        pass


def value():
    return 3
""",
    )
    res = mod.audit(tmp_path, None)
    assert names(res) == []
    assert res["failures"] == 0


# --------------------------------------------------------------------------- #
# Rule 2: a fully annotated stub is legitimate
# --------------------------------------------------------------------------- #
def test_a_fully_annotated_stub_does_not_fail_the_gate(tmp_path):
    """Planning forward is allowed, and the gate must say so.

    If a compliant stub still failed, the only way to get a green gate would be
    to delete the annotation or write a fake body -- both strictly worse than
    an honest, tracked gap.
    """
    mod = load_audit()
    write(tmp_path, "a.py", ANNOTATED)
    res = mod.audit(tmp_path, None)
    stub = by_name(res, "reindex")
    assert stub["compliant"] is True
    assert stub["failures"] == []
    assert stub["goal"] == "G-INDEX-3"
    assert stub["owner"] == "implementer"
    assert res["failures"] == 0


def test_markers_are_accepted_in_an_adjacent_comment_block(tmp_path):
    """The contract is about the information, not about docstring syntax.

    A stub written as a bare `...` under a comment block carries every marker;
    failing it for the choice of syntax would teach agents that the audit is
    arbitrary, and an arbitrary gate gets worked around.
    """
    mod = load_audit()
    write(
        tmp_path,
        "a.py",
        """
# GOAL:     G-2
# BLOCKED:  needs the transport split
# UNBLOCK:  land G-1
# SEQUENCE: after G-1
def later():
    ...
""",
    )
    res = mod.audit(tmp_path, None)
    assert by_name(res, "later")["compliant"] is True
    assert res["failures"] == 0


# --------------------------------------------------------------------------- #
# Rule 3: silent bodies warn, they do not fail
# --------------------------------------------------------------------------- #
def test_a_compliant_silent_stub_warns_but_passes(tmp_path):
    """`pass` is worse than `raise`, but it is not worth failing a build over.

    The runtime difference is real -- a silent stub returns None to a caller
    that believes it worked -- but making it a failure would block a legitimate
    annotated stub, so it is advice, not a gate.
    """
    mod = load_audit()
    write(
        tmp_path,
        "a.py",
        '''
def later():
    """Do the thing.

    GOAL:     G-2
    BLOCKED:  needs G-1
    UNBLOCK:  land G-1
    SEQUENCE: after G-1
    """
    pass
''',
    )
    res = mod.audit(tmp_path, None)
    stub = by_name(res, "later")
    assert stub["failures"] == []
    assert stub["warnings"] == [
        "a silent stub is indistinguishable from a working function at runtime"
    ]
    assert res["failures"] == 0
    assert res["warnings"] == 1


def test_raising_notimplementederror_earns_no_warning(tmp_path):
    """The preferred body must be the quiet one.

    If the recommended form still produced output, the warning would carry no
    information and the whole WARN section would be skimmed past.
    """
    mod = load_audit()
    write(tmp_path, "a.py", ANNOTATED)
    res = mod.audit(tmp_path, None)
    assert by_name(res, "reindex")["warnings"] == []
    assert res["warnings"] == 0


# --------------------------------------------------------------------------- #
# Rule 4: the GOAL id is checked against the goals file
# --------------------------------------------------------------------------- #
GOALS = """# goals

| id | task | state |
|---|---|---|
| G-INDEX-1 | the Store.iter_documents contract | done |
| G-INDEX-3 | rebuild the index | open |
"""


def test_a_goal_id_that_names_no_row_is_a_failure(tmp_path):
    """A GOAL id nothing tracks is the same as no GOAL id.

    The marker is present so the stub looks compliant, but no planner will ever
    schedule it -- this is the annotation passing the gate while doing none of
    the work the gate was added for.
    """
    mod = load_audit()
    goals = write(tmp_path, "GOALS.md", GOALS)
    write(
        tmp_path,
        "src/a.py",
        '''
def later():
    """Do the thing.

    GOAL:     G-TYPO-9
    BLOCKED:  needs G-INDEX-1
    UNBLOCK:  land G-INDEX-1
    SEQUENCE: after G-INDEX-1
    """
    raise NotImplementedError
''',
    )
    res = mod.audit(tmp_path, goals)
    stub = by_name(res, "later")
    assert stub["failures"] == ["G-TYPO-9 names a goal that does not exist"]
    assert res["failures"] == 1


def test_a_stub_whose_goal_is_already_done_is_a_failure(tmp_path):
    """The case the contract is actually about.

    The goal row was closed, everyone stopped thinking about it, and the stub
    it was tracking is still here waiting to be called. Nothing else in the
    pipeline can notice this: the goals file looks finished and the code looks
    annotated.
    """
    mod = load_audit()
    goals = write(tmp_path, "GOALS.md", GOALS)
    write(
        tmp_path,
        "src/a.py",
        '''
def iter_documents():
    """Walk the store.

    GOAL:     G-INDEX-1
    BLOCKED:  nothing any more
    UNBLOCK:  just write it
    SEQUENCE: before G-INDEX-3
    """
    raise NotImplementedError
''',
    )
    res = mod.audit(tmp_path, goals)
    assert by_name(res, "iter_documents")["failures"] == [
        "its goal G-INDEX-1 is closed but the stub remains"
    ]
    assert res["failures"] == 1


def test_an_open_goal_row_passes(tmp_path):
    """The control for the two failures above.

    Without it, an audit that failed every goal id would pass both of those
    tests while being useless.
    """
    mod = load_audit()
    goals = write(tmp_path, "GOALS.md", GOALS)
    write(tmp_path, "src/a.py", ANNOTATED)
    res = mod.audit(tmp_path, goals)
    assert by_name(res, "reindex")["failures"] == []
    assert res["failures"] == 0


def test_without_goals_the_id_is_reported_but_not_checked(tmp_path):
    """--goals is optional, and its absence must not fake a passing check.

    A repo can be audited before it has a goals file; what it must never do is
    report a goal id as verified when nothing verified it.
    """
    mod = load_audit()
    write(
        tmp_path,
        "a.py",
        '''
def later():
    """Do the thing.

    GOAL:     G-NOT-REAL
    BLOCKED:  x
    UNBLOCK:  y
    SEQUENCE: z
    """
    raise NotImplementedError
''',
    )
    res = mod.audit(tmp_path, None)
    assert by_name(res, "later")["goal"] == "G-NOT-REAL"
    assert res["failures"] == 0
    assert res["goals_file"] is None


def test_a_goals_path_that_does_not_exist_is_a_usage_error(tmp_path):
    """Exit 2, not a silent pass.

    A typo'd --goals path that audited anyway would report a green gate for a
    check that never ran. This repo has had that failure once already: an
    allow-list in pytest.ini made a whole test file invisible while the suite
    reported green.
    """
    mod = load_audit()
    write(tmp_path, "a.py", ANNOTATED)
    rc = mod.main([str(tmp_path), "--goals", str(tmp_path / "nope.md")])
    assert rc == 2


# --------------------------------------------------------------------------- #
# Rule 5: interface declarations are not unfinished work
# --------------------------------------------------------------------------- #
def test_abstract_methods_and_protocols_are_not_stubs(tmp_path):
    """`...` in a Protocol is the declaration, not a missing body.

    Auditing these would make every interface in the repo a failure, and a gate
    whose first run is all false positives gets `|| true`-d into a no-op rather
    than fixed.
    """
    mod = load_audit()
    write(
        tmp_path,
        "a.py",
        """
import abc
import typing
from abc import ABC, abstractmethod
from typing import Protocol


class Store(Protocol):
    def get(self, key): ...

    def put(self, key, value): ...


class Base(ABC):
    @abstractmethod
    def run(self):
        ...

    @abc.abstractmethod
    def stop(self):
        pass


class Mixed:
    @abstractmethod
    def declared(self):
        ...

    @typing.overload
    def load(self, x: int) -> int: ...

    def real(self):
        pass
""",
    )
    res = mod.audit(tmp_path, None)
    assert names(res) == ["Mixed.real"]
    assert res["failures"] == 1


def test_a_concrete_method_inside_an_abc_is_still_skipped(tmp_path):
    """Deliberate scope, recorded so the next reader does not call it a bug.

    The contract says "skip anything inside a class inheriting from Protocol or
    ABC". A per-method rule would be finer, but it would also mean deciding
    which half of an interface class counts as implementation -- judgement the
    audit is specified not to exercise.
    """
    mod = load_audit()
    write(
        tmp_path,
        "a.py",
        """
from abc import ABC


class Base(ABC):
    def hook(self):
        pass
""",
    )
    res = mod.audit(tmp_path, None)
    assert names(res) == []


# --------------------------------------------------------------------------- #
# Rule 6: what the walk skips
# --------------------------------------------------------------------------- #
def test_tests_and_vendor_directories_are_not_audited(tmp_path):
    """A test's `pass` placeholder is not a tracked piece of product work.

    Auditing tests/ and .venv/ would bury the three real findings under
    hundreds from third-party packages, which is how a report stops being read.
    """
    mod = load_audit()
    write(tmp_path, "tests/test_thing.py", "def test_x():\n    pass\n")
    write(tmp_path, "test_top.py", "def test_y():\n    pass\n")
    write(tmp_path, "conftest.py", "def fixture_z():\n    pass\n")
    write(tmp_path, ".venv/lib/dep.py", "def vendored():\n    pass\n")
    write(tmp_path, "node_modules/pkg/x.py", "def vendored2():\n    pass\n")
    write(tmp_path, "src/real.py", "def real():\n    pass\n")
    res = mod.audit(tmp_path, None)
    assert names(res) == ["real"]
    assert res["files_scanned"] == 1


def test_an_extensionless_script_with_a_python_shebang_is_audited(tmp_path):
    """Every script in this repo is one, and none of them ends in .py.

    `csd-repo-survey` walks `rglob("*.py")` and therefore sees zero of the 19
    scripts in scripts/. An audit with the same walk would have reported this
    repo clean without ever opening its source -- the worst possible result,
    because it is indistinguishable from a real pass.
    """
    mod = load_audit()
    write(tmp_path, "scripts/csd-thing", "#!/usr/bin/env python3\ndef x():\n    pass\n")
    write(tmp_path, "scripts/csd-shell", "#!/usr/bin/env bash\necho hi\n")
    write(tmp_path, "notes.md", "# not python\n")
    res = mod.audit(tmp_path, None)
    assert names(res) == ["x"]
    assert res["files_scanned"] == 1


# --------------------------------------------------------------------------- #
# Survivability: the audit runs over a tree an agent is mid-edit in
# --------------------------------------------------------------------------- #
def test_an_empty_repository_passes_and_says_so(tmp_path):
    """Zero stubs must exit 0, not crash on an empty result.

    The audit is specified to run every cycle, so it runs against repos that
    have not been onboarded yet; a traceback there would look like a broken
    gate rather than a clean one.
    """
    mod = load_audit()
    (tmp_path / "src").mkdir()
    res = mod.audit(tmp_path, None)
    assert res["stubs"] == []
    assert res["files_scanned"] == 0
    assert mod.main([str(tmp_path)]) == 0


def test_a_syntax_error_is_reported_and_the_walk_continues(tmp_path):
    """A half-written file is the normal case, not the exceptional one.

    The audit runs over a tree an agent is actively editing. If one unparseable
    file aborted the walk, the audit would go dark exactly when the loop is
    moving fastest -- and every other file's findings would vanish with it.
    """
    mod = load_audit()
    write(tmp_path, "broken.py", "def oops(:\n    pass\n")
    write(tmp_path, "fine.py", "def later():\n    pass\n")
    res = mod.audit(tmp_path, None)
    assert names(res) == ["later"]
    assert len(res["unreadable"]) == 1
    assert res["unreadable"][0]["path"] == "broken.py"
    assert res["unreadable"][0]["error"] == "SyntaxError"


def test_an_unreadable_file_does_not_by_itself_fail_the_gate(tmp_path):
    """Reported, but not a failure: the audit cannot know what is in there.

    Failing would mean a syntax error surfaces as "stub contract violated",
    which sends whoever reads the gate output to the wrong document.
    """
    mod = load_audit()
    write(tmp_path, "broken.py", "def oops(:\n    pass\n")
    assert mod.main([str(tmp_path)]) == 0


# --------------------------------------------------------------------------- #
# The CLI surface, which is the only part CI reads
# --------------------------------------------------------------------------- #
def test_exit_code_is_one_when_anything_fails(tmp_path):
    """CI reads the exit code and nothing else."""
    mod = load_audit()
    write(tmp_path, "a.py", "def later():\n    pass\n")
    assert mod.main([str(tmp_path)]) == 1


def test_a_missing_path_is_exit_two_not_exit_one(tmp_path):
    """A wrong path must not read as "the code is bad".

    Exit 1 for a typo'd path would send an agent hunting for stubs that do not
    exist, and the retry would fail identically.
    """
    mod = load_audit()
    assert mod.main([str(tmp_path / "nowhere")]) == 2


def test_json_output_carries_the_whole_finding(tmp_path, capsys):
    """--json is what a planner consumes; a summary line is not enough.

    It has to see the missing markers and the goal id to turn a finding into a
    task, which is the entire point of the audit running every cycle.
    """
    import json as _json

    mod = load_audit()
    write(tmp_path, "a.py", "def later():\n    pass\n")
    assert mod.main([str(tmp_path), "--json"]) == 1
    payload = _json.loads(capsys.readouterr().out)
    assert payload["failures"] == 1
    assert payload["stubs"][0]["name"] == "later"
    assert payload["stubs"][0]["missing"] == ["GOAL", "BLOCKED", "UNBLOCK", "SEQUENCE"]


def test_the_human_report_says_what_to_do_about_each_finding(tmp_path, capsys):
    """A finding without a next action is a complaint.

    The reviewer role returns findings to the layer that caused them; a report
    that only says "missing SEQUENCE" leaves that routing to whoever reads it.
    """
    mod = load_audit()
    write(tmp_path, "a.py", "def later():\n    pass\n")
    mod.main([str(tmp_path)])
    out = capsys.readouterr().out
    assert "-> add GOAL:/BLOCKED:/UNBLOCK:/SEQUENCE:" in out
    assert "docs/AGENT-ORGANISATION.md" in out


def test_qualified_names_distinguish_two_methods_of_the_same_name(tmp_path):
    """`log_message` is a stub in three different classes in this repo.

    Reporting three findings all called `log_message` with no class would make
    them look like one duplicated finding, and two of the three would go
    unfixed.
    """
    mod = load_audit()
    write(
        tmp_path,
        "a.py",
        """
class A:
    def log_message(self):
        pass


class B:
    def log_message(self):
        pass
""",
    )
    res = mod.audit(tmp_path, None)
    assert sorted(names(res)) == ["A.log_message", "B.log_message"]
