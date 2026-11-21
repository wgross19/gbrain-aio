"""Unit contract for the survivor lifecycle in the release harness.

These tests need no Docker. They pin the teardown command shape and the
no-survivor safety, so a future edit cannot silently orphan the surviving
candidate container or touch resources the run does not own.
"""

from __future__ import annotations

from tests.release import release_ops


def _fake_d(calls: list[list[str]]):
    def d(*args, check=True, timeout=120):  # noqa: ANN001, ANN002
        calls.append(list(args))
        return ""

    return d


def test_teardown_survivor_removes_exactly_the_recorded_resources(monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(release_ops, "d", _fake_d(calls))
    release_ops.teardown_survivor(
        {
            "survivor": {
                "container": "run-candidate",
                "volumes": ["run-brain", "run-data"],
            }
        }
    )
    assert calls == [
        ["rm", "-fv", "run-candidate"],
        ["volume", "rm", "run-brain"],
        ["volume", "rm", "run-data"],
    ]  # nosec B101


def test_teardown_survivor_is_a_noop_without_usable_survivor(monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(release_ops, "d", _fake_d(calls))
    release_ops.teardown_survivor({})
    release_ops.teardown_survivor({"survivor": {}})
    release_ops.teardown_survivor({"survivor": {"container": "", "volumes": []}})
    release_ops.teardown_survivor({"survivor": {"volumes": ["run-brain"]}})
    assert calls == [["volume", "rm", "run-brain"]]  # nosec B101


def test_teardown_survivor_only_issues_remove_commands(monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(release_ops, "d", _fake_d(calls))
    release_ops.teardown_survivor(
        {
            "survivor": {
                "container": "aio-rel-x-candidate",
                "volumes": ["aio-rel-x-brain"],
            }
        }
    )
    assert all(
        call[:2] in (["rm", "-fv"], ["volume", "rm"]) for call in calls
    )  # nosec B101
