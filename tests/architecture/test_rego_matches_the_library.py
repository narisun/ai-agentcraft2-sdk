"""The Rego bundle accepts what the library's rules document accepts, and nothing else.

OPA cannot be started here, so the two engines are held together at the source: the sets the
bundle spells out are compared with the library's own. A drift between them is how a rules
file that loads in-process came to be denied entirely under OPA (an action the bundle did not
know). The behaviour itself is judged by the shared corpus, against both engines.
"""

from __future__ import annotations

import re
from pathlib import Path

from agentcraft2_core.adapters.policy_documents import _OBLIGATION_KEYS, _RuleModel
from agentcraft2_core.contracts import Classification, PolicyAction, PrincipalKind

REGO = (
    Path(__file__).resolve().parents[2]
    / "policies"
    / "bundle"
    / "agentcraft2"
    / "authz"
    / "authz.rego"
).read_text(encoding="utf-8")


def _set_named(name: str) -> set[str]:
    """Return the strings of the set the bundle defines as ``name := { ... }``."""
    found = re.search(rf"^{name} := \{{(.*?)\}}", REGO, re.DOTALL | re.MULTILINE)
    assert found is not None, f"the bundle defines no {name}"
    return set(re.findall(r'"([^"]+)"', found.group(1)))


def test_the_bundle_knows_every_action_the_library_does() -> None:
    assert _set_named("actions") == {action.value for action in PolicyAction}


def test_the_bundle_knows_every_key_of_a_rule() -> None:
    assert _set_named("rule_keys") == set(_RuleModel.model_fields)


def test_the_bundle_knows_every_key_of_the_obligations() -> None:
    assert _set_named("obligation_keys") == set(_OBLIGATION_KEYS)


def test_the_bundle_knows_the_classification_levels_in_order() -> None:
    levels = re.search(r"^levels := \{(.*?)\}", REGO, re.MULTILINE)
    assert levels is not None
    ordered = re.findall(r'"([a-z]+)": (\d)', levels.group(1))
    assert [name for name, _ in sorted(ordered, key=lambda item: int(item[1]))] == [
        level.name.lower() for level in sorted(Classification)
    ]


def test_the_bundle_knows_the_kinds_of_caller() -> None:
    kinds = re.search(r"kind in \{([^}]*)\}", REGO)
    assert kinds is not None
    assert set(re.findall(r'"([a-z]+)"', kinds.group(1))) == {kind.value for kind in PrincipalKind}
