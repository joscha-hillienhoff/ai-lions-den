"""Layout regressions across every view, phase and agreed viewport.

Set ``RUN_LAYOUT_CHECK=1`` after ``npm run build`` and
``uv run playwright install chromium`` to run the real app in demo mode and
assert layout invariants. CI runs this after building the frontend.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from backend.pitch import PITCH_FIELDS
from scripts.layout_harness import alignment_issues, collect_session_layout, layout_issues

requires_browser = pytest.mark.skipif(
    os.environ.get("RUN_LAYOUT_CHECK") != "1",
    reason="set RUN_LAYOUT_CHECK=1 after npm run build and playwright install",
)


EXPECTED_PHASES = (
    "lobby",
    "ideation",
    "jury",
    "defense",
    "verdict",
    "leaderboard",
    "finale",
    "voting",
    "winner",
)
EXPECTED_VIEWPORTS = {
    "screen": ((1024, 768, "1024x768"), (2560, 1440, "2560x1440")),
    "team": ((1280, 800, "1280x800"), (1920, 1080, "1920x1080")),
    "team-public-lobby": ((1280, 800, "1280x800"),),
    "vote": ((375, 667, "375x667"), (430, 932, "430x932")),
    "moderator": ((1440, 900, "1440x900"), (1512, 982, "1512x982")),
}
EXPECTED_MODERATOR_STATES = ("lobby", "voting", "winner")
PITCH_CARD_PHASES = ("leaderboard", "finale", "voting")
PITCH_CARD_KINDS = ("finalist", "nonfinalist")
PITCH_CARD_PHONE = (390, 844, "390x844")
EXPECTED_VOTE_STATES = {
    "lobby": "waiting",
    "ideation": "waiting",
    "jury": "waiting",
    "defense": "waiting",
    "verdict": "waiting",
    "leaderboard": "waiting",
    "finale": "waiting",
    "voting": "form",
    "winner": "reveal",
}
UNTIMED_PHASES = ("finale", "winner")
# The jury and verdict rounds only wait for the lions, so they show no phase timer. The jury
# round shows the auto-advance countdown on the projector while it runs; the harness keeps it
# running for the whole capture.
TIMED_SCREEN_CLOCKS = ("lobby", "ideation", "defense", "leaderboard", "voting")
LION_WAIT_PHASES = ("jury", "verdict")
LONG_TEAM_NAME_LENGTH = 40
MIN_STARTUP_NAME_LENGTH = 45
EXPECTED_PATHS = {
    "screen": "/screen/",
    "team": "/team/",
    "vote": "/vote/",
    "moderator": "/moderator/",
}


def expected_capture_keys() -> set[tuple[str, str, int, int, str]]:
    keys = {
        (view, phase, width, height, name)
        for view in ("screen", "team")
        for phase in EXPECTED_PHASES
        for width, height, name in EXPECTED_VIEWPORTS[view]
    }
    keys.update(
        ("team", "lobby-public", width, height, name)
        for width, height, name in EXPECTED_VIEWPORTS["team-public-lobby"]
    )
    keys.update(
        ("moderator", phase, width, height, name)
        for phase in EXPECTED_MODERATOR_STATES
        for width, height, name in EXPECTED_VIEWPORTS["moderator"]
    )
    keys.update(
        ("team", f"{phase}-{kind}", *PITCH_CARD_PHONE)
        for phase in PITCH_CARD_PHASES
        for kind in PITCH_CARD_KINDS
    )
    keys.update(
        ("vote", state, width, height, name)
        for state in (f"{kind}-{phase}" for phase, kind in EXPECTED_VOTE_STATES.items())
        for width, height, name in EXPECTED_VIEWPORTS["vote"]
    )
    return keys


# Alignment checks compare edges across a group, so they only run with two or more members.
def required_groups(view: str, state: str) -> dict[str, int]:
    """Probe groups, with minimum sizes, the rendered page must contain."""
    lineup = dict.fromkeys(("screenInvestorNames", "screenInvestorTags", "screenInvestorRoles"), 2)
    team_cards = dict.fromkeys(("teamCardNames", "teamCardTags", "teamCardRoles"), 2)
    lobby_jury = dict.fromkeys(("lobbyJuryNames", "lobbyJuryTags", "lobbyJuryLines"), 2)
    ideation_guide = {
        "buildNeedLabels": len(PITCH_FIELDS),
        "buildNeedHints": len(PITCH_FIELDS),
        "buildJuryNames": 4,
        "buildPortraits": 4,
    }
    if view == "screen":
        frame = dict.fromkeys(("screenHeader", "screenContent", "phaseContainer"), 1)
        extra = {
            "lobby": lobby_jury,
            "ideation": ideation_guide,
            "jury": lineup,
            "verdict": lineup,
            "leaderboard": {"neutralNames": 2},
            "winner": {"revealNames": 2},
        }.get(state, {})
        return {**frame, **extra}
    if view == "team":
        if state == "lobby-public":
            return {"wordmark": 1, "teamJoinForm": 1, **team_cards}
        extra = team_cards if state in ("lobby", "jury") else {}
        return {"wordmark": 1, "phaseContainer": 1, **extra}
    if view == "moderator":
        return {"moderatorHeader": 1, "moderatorMain": 1, "teamRows": 2}
    return {"voteHeader": 1, "voteMain": 1}


@requires_browser
def test_viewport_layout(tmp_path: Path) -> None:
    captures, summary = collect_session_layout(tmp_path, full_matrix=False)

    assert 6 <= len(summary["teams"]) <= 8
    assert any(len(name) == LONG_TEAM_NAME_LENGTH for name in summary["teams"])
    assert all(len(name) >= MIN_STARTUP_NAME_LENGTH for name in summary["startups"])
    assert len(summary["finaleStartup"]) >= MIN_STARTUP_NAME_LENGTH

    expected = expected_capture_keys()
    actual = {
        (capture.view, capture.state, capture.width, capture.height, capture.name)
        for capture in captures
    }
    assert summary["captures"] == len(expected)
    assert actual == expected

    expected_runtime = {
        (view, state, width, height, name): (EXPECTED_PATHS[view], width, height)
        for view, state, width, height, name in expected
    }
    for capture in captures:
        key = (capture.view, capture.state, capture.width, capture.height, capture.name)
        expected_path, expected_width, expected_height = expected_runtime[key]
        assert capture.metrics["url"] == expected_path
        assert capture.metrics["viewport"] == {
            "width": expected_width,
            "height": expected_height,
        }

    for capture in captures:
        for group, minimum in required_groups(capture.view, capture.state).items():
            assert len(capture.metrics["groups"][group]) >= minimum, (
                f"{capture.summary()}: probe group {group} has fewer than {minimum} members, "
                "so its alignment check would not run"
            )

    # The finale and the winner page are untimed: no countdown and no "time is up" anywhere.
    for capture in captures:
        if capture.state.split("-")[-1] in UNTIMED_PHASES:
            assert capture.metrics["clockCount"] == 0, (
                f"{capture.summary()}: untimed phase shows a clock"
            )
            assert capture.metrics["timeIsUp"] is False, (
                f"{capture.summary()}: untimed phase shows time is up"
            )
    # The 3-minute pitch orientation clock: never on the projector, once in the moderator view,
    # and at most once in a team view (only for the finalist on stage).
    for capture in captures:
        if capture.state.split("-")[-1] == "finale":
            expected_max = {"screen": 0, "moderator": 1}.get(capture.view, 1)
            assert capture.metrics["pitchClockCount"] <= expected_max, (
                f"{capture.summary()}: unexpected pitch orientation clock"
            )
            if capture.view == "moderator":
                assert capture.metrics["pitchClockCount"] == 1, (
                    f"{capture.summary()}: moderator lacks the pitch orientation clock"
                )
    # The timed screens still render their countdown.
    for capture in captures:
        if capture.view == "screen" and capture.state in TIMED_SCREEN_CLOCKS:
            assert capture.metrics["clockCount"] >= 1, (
                f"{capture.summary()}: timed phase lost its clock"
            )
    # The jury and verdict rounds show no phase timer on the projector or in the team header.
    # The projector's jury round shows the "Defense starts in" countdown once, in the header,
    # and the verdict round shows no clock at all.
    for capture in captures:
        if capture.view in ("screen", "team") and capture.state in LION_WAIT_PHASES:
            clocks = 1 if (capture.view, capture.state) == ("screen", "jury") else 0
            assert capture.metrics["clockCount"] == clocks, (
                f"{capture.summary()}: expected {clocks} clock(s) while the lions decide"
            )
            assert capture.metrics["timeIsUp"] is False, (
                f"{capture.summary()}: shows time is up while the lions decide"
            )

    # The team's own "Your pitch" card shows for finalists and non-finalists in exactly the
    # leaderboard, finale and voting phases, and never on the projector, vote page or moderator.
    for capture in captures:
        cards = capture.metrics["yourPitch"]
        phase = capture.state.split("-")[0]
        if capture.view == "team" and phase in PITCH_CARD_PHASES:
            assert len(cards) == 1, f"{capture.summary()}: expected one pitch card"
            assert cards[0]["cheatSheet"], f"{capture.summary()}: pitch card has no cheat sheet"
            assert cards[0]["answers"] == 4, (
                f"{capture.summary()}: cheat sheet lacks the team's own defense answers"
            )
            if capture.state.endswith("-finalist"):
                assert cards[0]["finalist"], f"{capture.summary()}: not the finalist card"
            if capture.state.endswith("-nonfinalist"):
                assert not cards[0]["finalist"], f"{capture.summary()}: not the read-only card"
        else:
            assert cards == [], f"{capture.summary()}: pitch card outside its phases"
    for phase in PITCH_CARD_PHASES:
        kinds = {
            capture.state.removeprefix(f"{phase}-")
            for capture in captures
            if capture.view == "team" and capture.state.startswith(f"{phase}-")
        }
        assert kinds == set(PITCH_CARD_KINDS)

    # The projector guides the teams in ideation: every pitch field with its hint, and the jury.
    # Every other screen phase, the defense included, stays without the guide.
    for capture in captures:
        if capture.view != "screen":
            continue
        groups = capture.metrics["groups"]
        if capture.state == "ideation":
            assert [entry["text"] for entry in groups["buildNeedLabels"]] == [
                field.label for field in PITCH_FIELDS
            ], f"{capture.summary()}: pitch labels differ from PITCH_FIELDS"
            assert [entry["text"] for entry in groups["buildNeedHints"]] == [
                field.hint for field in PITCH_FIELDS
            ], f"{capture.summary()}: pitch hints differ from PITCH_FIELDS"
        else:
            assert not groups["buildNeedLabels"], f"{capture.summary()}: unexpected pitch guide"
            assert not groups["buildJuryNames"], f"{capture.summary()}: unexpected jury guide"

    screenshot_dir = tmp_path / "screenshots"
    expected_screenshots = {
        f"{view}-{state}-{name}.png" for view, state, _width, _height, name in expected
    }
    actual_screenshots = {path.name for path in screenshot_dir.glob("*.png")}
    assert actual_screenshots == expected_screenshots
    assert all(
        (screenshot_dir / name).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
        for name in expected_screenshots
    )

    failures = [
        f"{capture.summary()}: {issue}" for capture in captures for issue in capture.issues()
    ]
    assert not failures, "layout regressions:\n" + "\n".join(failures)


def rect(left: float, right: float, top: float = 0.0) -> dict[str, float]:
    return {
        "left": left,
        "right": right,
        "top": top,
        "contentLeft": left,
        "contentRight": right,
    }


def clean_metrics() -> dict[str, object]:
    return {
        "viewport": {"width": 1000, "height": 700},
        "scroll": {"width": 1000, "height": 700},
        "screenContentBottom": 650,
        "horizontal": [],
        "clipped": [],
        "overlaps": [],
        "tagOverflow": [],
        "groups": {
            "screenHeader": [rect(40, 960)],
            "screenContent": [rect(40, 960)],
            "screenPhase": [rect(450, 550)],
            "wordmark": [rect(40, 200)],
            "phaseContainer": [rect(40, 960)],
            "voteHeader": [rect(20, 380)],
            "voteMain": [rect(20, 380)],
            "moderatorHeader": [rect(20, 980)],
            "moderatorMain": [rect(20, 980)],
            "teamMeta": [rect(100, 300)],
            "screenInvestorNames": [rect(0, 1, 50), rect(0, 1, 50)],
            "teamRows": [rect(40, 900), rect(40, 900)],
        },
    }


@pytest.mark.parametrize("mode", ["screen", "team", "vote", "moderator"])
def test_clean_metrics_report_no_issues(mode: str) -> None:
    metrics = clean_metrics()
    assert layout_issues(metrics, mode=mode) == []  # type: ignore[arg-type]
    assert alignment_issues(metrics, mode=mode) == []  # type: ignore[arg-type]


def with_scroll(metrics: dict, **scroll: int) -> None:
    metrics["scroll"] = {**metrics["scroll"], **scroll}


def with_group(metrics: dict, key: str, entries: list[dict[str, float]]) -> None:
    metrics["groups"][key] = entries


LAYOUT_VIOLATIONS = [
    ("team", lambda m: with_scroll(m, width=1100), "horizontal document scroll"),
    ("screen", lambda m: with_scroll(m, height=800), "vertical document scroll"),
    ("screen", lambda m: m.update(screenContentBottom=720), "below the fold"),
    (
        "team",
        lambda m: m.update(horizontal=[{"over": 9, "selector": "div.x", "text": "t"}]),
        "past viewport edge",
    ),
    (
        "team",
        lambda m: m.update(
            tagOverflow=[{"over": 4, "selector": "span.tag", "text": "t", "card": "li"}]
        ),
        "focus tag spills out",
    ),
    (
        "team",
        lambda m: m.update(
            clipped=[
                {
                    "selector": "p.x",
                    "text": "t",
                    "clientWidth": 1,
                    "scrollWidth": 2,
                    "clientHeight": 1,
                    "scrollHeight": 2,
                }
            ]
        ),
        "clipped content",
    ),
    (
        "screen",
        lambda m: with_group(
            m, "buildPortraits", [{**rect(0, 100), "width": 100.0, "height": 60.0}]
        ),
        "portrait is not square",
    ),
    (
        "team",
        lambda m: m.update(overlaps=[{"a": "a", "b": "b", "overlapX": 9, "overlapY": 9}]),
        "overlapping text",
    ),
]


@pytest.mark.parametrize(("mode", "mutate", "expected"), LAYOUT_VIOLATIONS)
def test_layout_issues_flags_each_violation(mode: str, mutate, expected: str) -> None:
    metrics = clean_metrics()
    mutate(metrics)
    assert any(expected in issue for issue in layout_issues(metrics, mode=mode))  # type: ignore[arg-type]


ALIGNMENT_VIOLATIONS = [
    (
        "screen",
        lambda m: with_group(m, "screenHeader", [rect(60, 960)]),
        "do not share the gutter",
    ),
    (
        "screen",
        lambda m: with_group(m, "screenPhase", [rect(100, 200)]),
        "not centred",
    ),
    (
        "screen",
        lambda m: with_group(m, "screenInvestorNames", [rect(0, 1, 50), rect(0, 1, 60)]),
        "projector investor names should share the same top edge",
    ),
    (
        "team",
        lambda m: with_group(m, "phaseContainer", [rect(70, 960)]),
        "do not share the left edge",
    ),
    (
        "team",
        lambda m: with_group(m, "teamRows", [rect(40, 900), rect(60, 900)]),
        "team ledger rows should share the same left edge",
    ),
    (
        "team",
        lambda m: with_group(m, "teamMeta", [rect(-5, 300)]),
        "leaves the viewport",
    ),
    (
        "vote",
        lambda m: with_group(m, "voteMain", [rect(40, 380)]),
        "do not share the column",
    ),
    (
        "moderator",
        lambda m: with_group(m, "moderatorMain", [rect(40, 980)]),
        "do not share the column",
    ),
]


@pytest.mark.parametrize(("mode", "mutate", "expected"), ALIGNMENT_VIOLATIONS)
def test_alignment_issues_flags_each_violation(mode: str, mutate, expected: str) -> None:
    metrics = clean_metrics()
    mutate(metrics)
    assert any(expected in issue for issue in alignment_issues(metrics, mode=mode))  # type: ignore[arg-type]
