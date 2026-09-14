"""The two reply protocols validate as JSON Schema, not just as parser output.

G-SCHEMA-1. docs/LESSONS.md pattern 4: the loop's two reply protocols were
defined only by the parser that reads them, so a model could never check its
own output against them, and being strict about the format only relocated the
failure -- to nine identical retries and an invalidated goal, in the case that
gives this file its sharpest test below. config/reply-schemas.json writes the
two shapes down as data; this file is what makes that a specification instead
of a comment, per the house rule that a specification nothing checks is one.

Nothing here touches the parser. These tests read the same fixtures
tests/test_substitutions.py already asserts the parser accepts, and check that
their PARSED result also fits the schema meant to describe it -- proving the
schema was derived from what the parser actually does, not from a guess at it.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
LOOP_SCRIPT = REPO_ROOT / "scripts" / "csd-autodev-loop"
SUBSTITUTIONS_TEST = Path(__file__).resolve().parent / "test_substitutions.py"
SCHEMA_FILE = REPO_ROOT / "config" / "reply-schemas.json"


def _load(path: Path, name: str) -> Any:
    """Load a file as a module without needing it importable as a package.

    tests/ has no __init__.py (see pyproject.toml's import-mode note), and
    scripts/csd-autodev-loop has no .py extension, so a plain `import` cannot
    reach either. Every other test file in this repo already loads the loop
    script this way; loading tests/test_substitutions.py the same way reuses
    its REPLY fixture instead of retyping it, which would let the two drift.
    """
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def load_loop() -> Any:
    return _load(LOOP_SCRIPT, "csd_autodev_loop")


def load_substitutions_tests() -> Any:
    return _load(SUBSTITUTIONS_TEST, "test_substitutions_fixtures")


def load_schemas() -> dict:
    return json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))


def as_from_to_pairs(subs: list[tuple[str, str]]) -> list[dict[str, str]]:
    """Translate the parser's runtime shape into the schema's JSON shape.

    parse_substitutions returns a bare (from, to) TUPLE for each pair -- see
    scripts/csd-autodev-loop -- and JSON has no tuple type. config/reply-
    schemas.json deliberately names the two sides `from`/`to` on an object
    rather than leaving them positional in a 2-element array, because a
    position is exactly the kind of thing that silently drifts once nothing
    names it -- which is the same class of failure this whole goal exists to
    close, one layer up. This helper is that one, intentional translation
    step, kept in one place so every test below applies it identically.
    """
    return [{"from": frm, "to": to} for frm, to in subs]


def test_the_reply_constant_validates_against_the_substitution_schema() -> None:
    """Acceptance criterion 1: REPLY, parsed, fits substitution_proposal."""
    loop = load_loop()
    fixtures = load_substitutions_tests()
    parsed = loop.extract_json(fixtures.REPLY)
    schema = load_schemas()["substitution_proposal"]

    instance = {
        "path": parsed["path"],
        "substitutions": as_from_to_pairs(parsed["substitutions"]),
    }
    jsonschema.validate(instance=instance, schema=schema)


def test_a_file_proposal_still_parses_and_fits_the_whole_file_schema() -> None:
    """Acceptance criterion 2: the whole-file fixture fits whole_file_proposal.

    Same reply text test_substitutions.py::test_a_file_proposal_still_parses
    asserts on, so a change to the parser's PATH/fence handling that this
    schema would reject shows up here too, not only in that other file.
    """
    loop = load_loop()
    parsed = loop.extract_json('PATH: src/x.py\n```python\nprint("hi")\n```\n')
    assert parsed == {"path": "src/x.py", "content": 'print("hi")\n'}

    schema = load_schemas()["whole_file_proposal"]
    jsonschema.validate(instance=parsed, schema=schema)


def test_an_arrow_split_across_two_lines_is_rejected() -> None:
    """Acceptance criterion 3, and the sharpest case in LESSONS.md pattern 4.

    "Given a multi-line passage and a one-line `from => to` grammar, the model
    put the arrow on its own line. Sensible, unparseable, retried nine times,
    goal invalidated." (docs/LESSONS.md #4). parse_substitutions was later
    taught to read that shape from text -- see ARROW_ON_ITS_OWN_LINE in
    test_substitutions.py -- but the JSON protocol this schema describes is
    the one where that ambiguity cannot arise in the first place: `from` and
    `to` are two separately-named fields, not two sides of a line-oriented
    arrow, so there is no "own line" for the arrow to end up on.

    This test reconstructs exactly that failure as a JSON instance: the real
    from/to pair the parser recovered from ARROW_ON_ITS_OWN_LINE, glued back
    together across an arrow the way the model actually produced it, with no
    separate `to` field to hold the second half. That is what "the arrow split
    across two lines" looks like once it is forced into this schema's shape --
    a `from` with no matching `to` -- and the schema must refuse it.
    """
    loop = load_loop()
    fixtures = load_substitutions_tests()
    subs = loop.extract_json(fixtures.ARROW_ON_ITS_OWN_LINE)["substitutions"]
    frm, to = subs[1]
    assert frm == "Goals are stored in `docs/GOALS.md`."  # the fixture, unmoved

    reply_reassembled_around_the_stray_arrow = frm + "\n=> " + to
    instance = {
        "path": "README.md",
        "substitutions": [{"from": reply_reassembled_around_the_stray_arrow}],
    }
    schema = load_schemas()["substitution_proposal"]

    with pytest.raises(jsonschema.ValidationError, match="'to' is a required"):
        jsonschema.validate(instance=instance, schema=schema)


@pytest.mark.parametrize("schema_name", ["whole_file_proposal", "substitution_proposal"])
def test_neither_content_nor_substitutions_is_rejected(schema_name: str) -> None:
    """Acceptance criterion 4: a path with no payload at all fits neither form.

    `{"path": ...}` alone is what a reply carrying no `content` and no
    `substitutions` looks like -- the shape apply_proposal treats as a no-op
    ("no-file") today, per its own comment. A schema meant to describe what a
    real proposal looks like must not call that valid either.
    """
    schema = load_schemas()[schema_name]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance={"path": "docs/A.md"}, schema=schema)


def test_an_empty_to_is_allowed_but_an_empty_from_is_not() -> None:
    """Derived from apply_substitutions, not from the goal text.

    Its own comment: an empty FROM would match at every position in a file
    (never a real substitution), while an empty TO is a legitimate deletion of
    whatever FROM matched. The schema encodes exactly that asymmetry.
    """
    schema = load_schemas()["substitution_proposal"]
    deletion = {"path": "docs/A.md", "substitutions": [{"from": "x", "to": ""}]}
    jsonschema.validate(instance=deletion, schema=schema)

    everywhere = {"path": "docs/A.md", "substitutions": [{"from": "", "to": "y"}]}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=everywhere, schema=schema)


@pytest.mark.parametrize("schema_name", ["whole_file_proposal", "substitution_proposal"])
def test_an_unlisted_property_is_rejected(schema_name: str) -> None:
    """additionalProperties: false is required by strict structured-output
    mode (see the schema file's own _comment), but it is also a real guard
    here: a stray key is exactly the shape "the arrow became its own field"
    would take.
    """
    schema = load_schemas()[schema_name]
    payload = {"path": "docs/A.md", "content": "x", "substitutions": [], "extra": 1}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=payload, schema=schema)


def test_both_schemas_are_schemas() -> None:
    """The file itself must be valid JSON Schema, or none of the above proves
    anything -- a schema that jsonschema silently treats as "matches
    everything" would pass every rejection test above by accident.
    """
    schemas = load_schemas()
    for name in ("whole_file_proposal", "substitution_proposal"):
        jsonschema.Draft202012Validator.check_schema(schemas[name])
        assert "$ref" not in json.dumps(schemas[name]), (
            f"{name} must inline everything -- see the file's own _comment on why"
        )
