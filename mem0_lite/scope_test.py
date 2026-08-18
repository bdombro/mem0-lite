"""Project/workstream/scope helpers. No Memory()."""

from __future__ import annotations

import pytest

from mem0_lite.scope import (
    agent_passed_scope_filters,
    canonical_project,
    default_recall_filters,
    explicit_tags,
    extra_metadata_filters,
    merge_tags,
    normalize_scope,
)


@pytest.mark.parametrize(
    ("project", "repo", "want"),
    [
        (None, "github.com/org/repo", "github.com/org/repo"),
        ("keep", "ignored", "keep"),
        ("  ", None, None),
    ],
)
def test_repo_alias_normalizes_to_project(project, repo, want) -> None:
    assert canonical_project(project, repo) == want


def test_explicit_tags_drop_invalid_scope() -> None:
    tags = explicit_tags(project="p", scope="nope", workstream="w")
    assert tags == {"project": "p", "workstream": "w"}
    assert normalize_scope("STANDING") == "standing"
    assert normalize_scope("nope") is None


def test_explicit_tags_win_over_stamped() -> None:
    merged = merge_tags(
        {"project": "explicit", "scope": "wip"},
        {"project": "stamped", "workstream": "branch:feat", "scope": "standing"},
    )
    assert merged["project"] == "explicit"
    assert merged["scope"] == "wip"
    assert merged["workstream"] == "branch:feat"


def test_default_or_filter_shape() -> None:
    assert default_recall_filters("github.com/org/repo", "PROJ-1") == {
        "OR": [
            {"scope": "standing", "project": "github.com/org/repo"},
            {"scope": "wip", "workstream": "PROJ-1", "project": "github.com/org/repo"},
        ]
    }


def test_default_recall_without_workstream_is_standing_only() -> None:
    assert default_recall_filters("p", None) == {"scope": "standing", "project": "p"}


def test_unfiltered_when_project_unknown() -> None:
    assert not agent_passed_scope_filters(None, None, None, None)
    assert agent_passed_scope_filters(None, "github.com/o/r", None, None)


def test_explicit_filters_omit_source_ref() -> None:
    extra = extra_metadata_filters(
        {"project": "p", "workstream": "w", "source_ref": "feat/x", "scope": "wip"}
    )
    assert extra == {"project": "p", "workstream": "w", "scope": "wip"}
    assert "source_ref" not in extra
