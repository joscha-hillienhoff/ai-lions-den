#!/usr/bin/env python3
"""Demo-mode capture harness for viewport layout and the layout regression check.

Starts the real app on a random local port with an isolated SQLite database,
seeds a deterministic demo session with eight teams (including a 40-character
team name and pitch, answer and one-change text filled to the server limits), drives
every phase through the HTTP API, and captures each view with Playwright.

The module is shared by ``scripts/capture_layout.py`` (screenshots) and
``tests/test_viewport_layout.py`` (CI invariants).
"""

from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.pitch import PITCH_LIMITS_BY_KEY  # noqa: E402
from backend.state import DEFENSE_ANSWER_LIMIT, DEFENSE_CHANGE_LIMIT  # noqa: E402

MODERATOR_PASSWORD = "layout-check-password"
DEMO_DELAY_MS = 2
# The team view is captured as the very long team name, the hardest case.
TEAM_VIEW_INDEX = 1

# A representative set: 1920x1080 and 1280x720 are 16:9 classrooms, 1024x768 is
# the old 4:3 projector, 2560x1440 a large hall display. 125% and 80% browser
# zoom are modelled as the CSS viewport the same physical screen produces.
SCREEN_VIEWPORTS: tuple[tuple[int, int, str], ...] = (
    (1920, 1080, "1920x1080"),
    (1280, 720, "1280x720"),
    (1024, 768, "1024x768"),
    (2560, 1440, "2560x1440"),
    (1536, 864, "1920x1080-zoom125"),
    (2400, 1350, "1920x1080-zoom80"),
)
TEAM_VIEWPORTS: tuple[tuple[int, int, str], ...] = (
    (1440, 900, "1440x900"),
    (1280, 800, "1280x800"),
    (1920, 1080, "1920x1080"),
    (1152, 720, "1440x900-zoom125"),
    (1800, 1125, "1440x900-zoom80"),
)
VOTE_VIEWPORTS: tuple[tuple[int, int, str], ...] = (
    (375, 667, "375x667"),
    (390, 844, "390x844"),
    (430, 932, "430x932"),
)
MODERATOR_VIEWPORTS: tuple[tuple[int, int, str], ...] = (
    (1440, 900, "1440x900"),
    (1512, 982, "1512x982"),
)

# The CI run keeps every phase and the smallest and largest agreed viewport for
# each view. Browser zoom variants remain in the full audit only.
CI_SESSION_PHASES = (
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
CI_SCREEN_VIEWPORTS: tuple[tuple[int, int, str], ...] = (
    (1024, 768, "1024x768"),
    (2560, 1440, "2560x1440"),
)
CI_TEAM_VIEWPORTS: tuple[tuple[int, int, str], ...] = (
    (1280, 800, "1280x800"),
    (1920, 1080, "1920x1080"),
)
CI_PUBLIC_TEAM_LOBBY_VIEWPORTS: tuple[tuple[int, int, str], ...] = ((1280, 800, "1280x800"),)
PRE_VOTING_PHASES = CI_SESSION_PHASES[: CI_SESSION_PHASES.index("voting")]
VOTE_STATE_BY_PHASE: dict[str, str] = {
    **{phase: "waiting" for phase in PRE_VOTING_PHASES},
    "voting": "form",
    "winner": "reveal",
}
CI_VOTE_VIEWPORTS: tuple[tuple[int, int, str], ...] = (
    (375, 667, "375x667"),
    (430, 932, "430x932"),
)
CI_MODERATOR_STATES = ("lobby", "voting", "winner")
# The team's own "Your pitch" card shows in these phases, for finalists and non-finalists alike.
# It is read on a phone on stage, so both kinds of team are also captured at phone width.
PITCH_CARD_PHASES = ("leaderboard", "finale", "voting")
PITCH_CARD_PHONE_VIEWPORT: tuple[int, int, str] = (390, 844, "390x844")

LONG_TEAM_NAME = "The Extremely Long Team Name Company Ltd"
LONG_STARTUP_NAME = "The Incredibly Long Startup Name That Tests Projector Layout"
LONG_TAGLINE = (
    "The return button that makes online fashion profitable for mid-sized European "
    "retailers by turning every unwanted order into a local resale."
)
LONG_ANSWER = (
    "We start with one narrow beachhead: independent pharmacies in two Berlin districts "
    "that already lose money on expired stock. The first twenty stores pay a real monthly "
    "fee from day one, which funds the next stage without a sales team. We measure "
    "activation, retention, contribution margin and payback per store, and expand to a "
    "second district only after ten stores renew twice. One buyer, one channel, one "
    "repeatable onboarding motion, and a number that proves the model before growth."
)
LONG_CHANGE = (
    "We narrow the first customer to independent pharmacies in two Berlin districts and "
    "charge a flat fee per store per month. That removes the enterprise sales cycle, makes "
    "twenty paying customers reachable in one quarter, and gives the jury one proof point: "
    "ten stores renewing twice before we expand."
)


def fill_to_limit(text: str, limit: int) -> str:
    """Repeat the text until it is exactly ``limit`` characters, the longest the server accepts."""
    filled = text
    while len(filled) < limit:
        filled += " " + text
    cut = filled[:limit]
    return cut[:-1] + "." if cut.endswith(" ") else cut


# Every startup name is long, so whichever teams reach the finale stress the projector.
TEAMS: tuple[dict[str, str], ...] = (
    {
        "team": "The Margin Calls",
        "startup": "Loopline Local Resale Network - Long Startup Content",
        "tagline": "The return button that makes online fashion profitable.",
        "problem": (
            "European fashion retailers lose 20 to 30 percent of margin on returns, mostly "
            "because returned items never re-enter local inventory. Customers wait two weeks "
            "for a refund and often never buy again."
        ),
        "solution": (
            "Loopline turns a return into a local resale in one click. The customer prints "
            "nothing, the retailer recovers most of the item value, and the next buyer nearby "
            "gets it in two days."
        ),
        "customers": (
            "Mid-sized online fashion retailers in Germany, France and the Netherlands with "
            "at least 100,000 orders per year and a return rate above 20 percent."
        ),
        "businessModel": (
            "A monthly SaaS fee plus 8 percent of every recovered item. We estimate 24,000 "
            "EUR annual contract value for a mid-sized retailer and a 14-month payback after "
            "the first warehouse integration."
        ),
    },
    {
        "team": LONG_TEAM_NAME,
        "startup": LONG_STARTUP_NAME,
        "tagline": LONG_TAGLINE,
        "problem": (
            "Independent pharmacies throw away expired stock every month because expiry dates "
            "live in three systems and staff only react when a shelf looks empty. The loss is "
            "invisible until the quarterly review, and the workaround is a paper list nobody trusts."
        ),
        "solution": (
            "A shelf sensor and a short daily list tell the pharmacist which items to discount "
            "today, before they expire. It works with the existing point-of-sale system and needs "
            "no new hardware beyond one gateway per store."
        ),
        "customers": (
            "Independent pharmacies in dense German city districts with one to three stores, "
            "starting with twenty stores in two Berlin districts that already use electronic "
            "shelf labels and have a named person responsible for stock."
        ),
        "businessModel": (
            "A flat fee of 89 EUR per store per month, paid by the pharmacy, plus a one-time "
            "setup fee of 240 EUR for the gateway. At 20 percent waste reduction the fee pays "
            "for itself in the first month, and gross margin is 74 percent after sensor cost."
        ),
    },
    {
        "team": "Loopline Returns Collective",
        "startup": "Northstar Compliance Graph - Long Startup Content",
        "tagline": (
            "Compliance answers before the auditor asks, with a live evidence graph that "
            "cites every control, owner and policy."
        ),
        "problem": (
            "Compliance teams at fintechs answer the same 200 questions every quarter in "
            "spreadsheets. Audits take weeks, evidence is stale, and senior people spend their "
            "time copying documents instead of reducing risk."
        ),
        "solution": (
            "Northstar keeps a live evidence graph of every control, owner and policy. It drafts "
            "answers with citations, flags gaps before an audit, and exports a reviewer pack in minutes."
        ),
        "customers": (
            "Series A to Series C fintechs with 20 to 200 employees and an internal compliance "
            "lead. We start with payment institutions in the EU."
        ),
        "businessModel": (
            "Per-seat pricing at 90 EUR per month with a platform fee of 1,500 EUR per month. "
            "Gross margin is 82 percent and the first ten design partners already pay for pilot access."
        ),
    },
    {
        "team": "Peak Freight Systems",
        "startup": "Peak Freight Empty Leg Exchange - Long Startup Content",
        "tagline": "Freight capacity that finds its own way home.",
        "problem": (
            "Small freight forwarders run 40 percent of their trucks empty because matching "
            "happens by phone and email. A single missed backhaul can erase the margin on an "
            "entire route."
        ),
        "solution": (
            "Peak Freight predicts empty legs 48 hours ahead and offers them to nearby shippers "
            "at a dynamic price. Drivers accept from a phone, and forwarders keep their existing systems."
        ),
        "customers": (
            "Regional freight forwarders with 20 to 200 trucks, starting in the Rhine-Ruhr "
            "corridor where routes are dense and empty legs are visible."
        ),
        "businessModel": (
            "A 6 percent take rate on matched freight plus 49 EUR per vehicle per month. At 15 "
            "percent marketplace penetration, a corridor becomes profitable within two quarters."
        ),
    },
    {
        "team": "Second Opinion Health",
        "startup": "Second Opinion Specialist Network - Long Startup Content",
        "tagline": "The second opinion every patient deserves.",
        "problem": (
            "Patients with a rare diagnosis wait months for a specialist and often receive "
            "conflicting advice. General practitioners do not have the time or the network to "
            "resolve uncertainty."
        ),
        "solution": (
            "Second Opinion routes a structured case to three matching specialists and returns "
            "a consolidated, cited view within five days. The GP stays in charge and the patient "
            "keeps one record."
        ),
        "customers": (
            "Private health insurers and corporate health programs in Germany that already pay "
            "for second-opinion services and want faster, measurable outcomes."
        ),
        "businessModel": (
            "A per-case fee of 390 EUR paid by insurers, plus an annual integration fee. Each "
            "specialist receives 120 EUR per review, leaving a 35 percent contribution margin per case."
        ),
    },
    {
        "team": "Grain and Gain Cooperative",
        "startup": "Grain & Gain Storage Intelligence - Long Startup Content",
        "tagline": "Grain storage that pays for itself in one season.",
        "problem": (
            "Up to 15 percent of stored grain spoils because farmers cannot see temperature and "
            "moisture inside a silo. A single spoiled batch can wipe out a season's profit."
        ),
        "solution": (
            "Grain & Gain installs cheap wireless probes and forecasts spoilage risk seven days "
            "ahead. The app tells the farmer when to ventilate or move grain, with no new silo hardware."
        ),
        "customers": (
            "Cooperative grain storage operators in Poland and Romania managing at least 20,000 "
            "tonnes across multiple silos."
        ),
        "businessModel": (
            "Hardware at cost plus 1.20 EUR per tonne per season for monitoring. A 30,000 tonne "
            "cooperative pays 36,000 EUR per year and avoids an estimated 120,000 EUR of spoilage."
        ),
    },
    {
        "team": "Kite Health Hiring",
        "startup": "Kite Health Work Sample Hiring - Long Startup Content",
        "tagline": "Hiring signals that actually predict performance.",
        "problem": (
            "Small companies hire on gut feel and lose 30,000 EUR or more on a bad senior hire. "
            "Assessment tools are either generic personality tests or expensive consulting projects."
        ),
        "solution": (
            "Kite Health runs a short, role-specific work sample and scores it against the team's "
            "best performers. Hiring managers get a structured interview guide and a decision memo."
        ),
        "customers": (
            "Founder-led companies with 20 to 150 employees hiring at least five senior roles per "
            "year, starting with software and professional services."
        ),
        "businessModel": (
            "1,200 EUR per role, billed only when a shortlist is delivered. Gross margin is 70 "
            "percent and the average customer runs eight roles per year."
        ),
    },
    {
        "team": "Nova Threads Studio",
        "startup": "Nova Threads Wardrobe Renewal - Long Startup Content",
        "tagline": "A wardrobe that renews itself.",
        "problem": (
            "Fashion rental is stuck between cheap one-off rentals and expensive subscription "
            "services. Customers churn because the selection does not match their real wardrobe, "
            "and brands cannot forecast demand."
        ),
        "solution": (
            "Nova Threads combines a style profile with local inventory from partner brands, then "
            "ships a rotating capsule that customers can buy at a discount."
        ),
        "customers": (
            "Urban professionals aged 25 to 40 who already spend over 150 EUR per month on "
            "fashion, reached through three Berlin and Munich brand partners."
        ),
        "businessModel": (
            "A 79 EUR monthly subscription, 30 percent of which goes to the brand partner, plus "
            "resale margin on items kept. Contribution margin turns positive at 2,200 subscribers."
        ),
    },
)


def _check(response: httpx.Response, label: str) -> dict[str, Any]:
    if response.status_code != 200:
        raise RuntimeError(f"{label} failed: {response.status_code} {response.text[:400]}")
    return response.json()


@dataclass
class DemoSession:
    """A running demo server plus the seeded team clients that drive it."""

    base_url: str
    directory: Path
    process: subprocess.Popen[bytes]
    teams: list[dict[str, Any]] = field(default_factory=list)
    moderator: httpx.Client | None = None
    anonymous: httpx.Client | None = None
    phase: str = "lobby"

    def get(self, path: str) -> httpx.Response:
        assert self.anonymous is not None
        return self.anonymous.get(f"{self.base_url}{path}")

    def advance(self, expected: str) -> dict[str, Any]:
        assert self.moderator is not None
        response = self.moderator.post(
            f"{self.base_url}/api/moderator/phase",
            json={"expectedPhase": self.phase},
        )
        payload = _check(response, f"advance to {expected}")
        actual = payload["session"]["phase"]
        if actual != expected:
            raise RuntimeError(f"expected phase {expected}, got {actual}")
        self.phase = actual
        return payload

    def moderator_state(self) -> dict[str, Any]:
        assert self.moderator is not None
        return _check(
            self.moderator.get(f"{self.base_url}/api/moderator/state"),
            "moderator state",
        )

    def wait_for(self, label: str, predicate: Any, timeout_s: float = 120) -> None:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if predicate():
                return
            time.sleep(0.1)
        raise TimeoutError(f"Timed out waiting for {label}")

    def wait_for_round1(self) -> None:
        def done() -> bool:
            state = self.moderator_state()
            return all(
                all(entry["status"] == "done" for entry in team["round1"].values())
                for team in state["teams"]
            )

        self.wait_for("round 1", done)

    def wait_for_verdicts(self) -> None:
        def done() -> bool:
            state = self.moderator_state()
            return all(team["verdictCompletedAt"] is not None for team in state["teams"])

        self.wait_for("final verdicts", done, timeout_s=180)

    def close(self) -> None:
        for team in self.teams:
            team["client"].close()
        if self.moderator is not None:
            self.moderator.close()
        if self.anonymous is not None:
            self.anonymous.close()
        self.process.terminate()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)


def _wait_for_server(base_url: str, log_path: Path, timeout_s: float = 60) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            response = httpx.get(f"{base_url}/api/health", timeout=2)
            if response.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    log = log_path.read_text(errors="replace")[-4000:] if log_path.exists() else ""
    raise RuntimeError(f"Server did not start.\n{log}")


def start_demo_session(directory: Path) -> DemoSession:
    """Start the real server in demo mode and seed eight teams with long content."""
    port = random.randint(3800, 4800)
    base_url = f"http://127.0.0.1:{port}"
    db_path = directory / "session.db"
    log_path = directory / "server.log"
    log_file = log_path.open("w")
    env = {
        **os.environ,
        "DEMO_MODE": "true",
        "MODERATOR_PASSWORD": MODERATOR_PASSWORD,
        "SESSION_DB": str(db_path),
        "DEMO_DELAY_MS": str(DEMO_DELAY_MS),
        "JURY_MAX_CONCURRENCY": "8",
        # The jury round shows its "Defense starts in ..." line while views are captured.
        # A long countdown keeps the session in the jury phase for the whole capture, and
        # its widest wording is the hardest layout case.
        "JURY_AUTO_ADVANCE_SECONDS": "900",
        "PUBLIC_BASE_URL": base_url,
    }
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "backend.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=ROOT,
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
    session = DemoSession(base_url=base_url, directory=directory, process=process)
    try:
        _wait_for_server(base_url, log_path)
        session.anonymous = httpx.Client(timeout=15, follow_redirects=True)
        session.moderator = httpx.Client(timeout=15, follow_redirects=True)
        _check(
            session.moderator.post(
                f"{base_url}/api/moderator/login",
                json={"password": MODERATOR_PASSWORD},
            ),
            "moderator login",
        )

        for entry in TEAMS:
            client = httpx.Client(timeout=15, follow_redirects=True)
            joined = _check(
                client.post(f"{base_url}/api/teams/join", json={"name": entry["team"]}),
                f"join {entry['team']}",
            )
            session.teams.append(
                {
                    "client": client,
                    "id": joined["team"]["id"],
                    "name": entry["team"],
                    "pitchUpdatedAt": None,
                    "data": entry,
                }
            )
        return session
    except Exception:
        session.close()
        raise


def submit_pitches(session: DemoSession) -> None:
    for team in session.teams:
        entry = team["data"]
        # Every long field runs to the server limit, so each view is checked at the longest
        # text a team can submit. The first team's problem is one unbroken token, the
        # hardest wrapping case.
        pitch = {
            "startupName": entry["startup"],
            "tagline": entry["tagline"],
            **{
                key: fill_to_limit(entry[key], PITCH_LIMITS_BY_KEY[key])
                for key in ("problem", "solution", "customers", "businessModel")
            },
        }
        if team is session.teams[0]:
            pitch["problem"] = "x" * PITCH_LIMITS_BY_KEY["problem"]
        saved = _check(
            team["client"].post(
                f"{session.base_url}/api/teams/pitch",
                json={
                    "pitch": pitch,
                    "submit": False,
                    "expectedPitchUpdatedAt": team["pitchUpdatedAt"],
                },
            ),
            f"draft pitch {team['name']}",
        )
        team["pitchUpdatedAt"] = saved["team"]["pitchUpdatedAt"]
        submitted = _check(
            team["client"].post(
                f"{session.base_url}/api/teams/pitch",
                json={
                    "pitch": pitch,
                    "submit": True,
                    "expectedPitchUpdatedAt": team["pitchUpdatedAt"],
                },
            ),
            f"submit pitch {team['name']}",
        )
        team["pitchUpdatedAt"] = submitted["team"]["pitchUpdatedAt"]


def submit_defenses(session: DemoSession) -> None:
    answers = [fill_to_limit(LONG_ANSWER, DEFENSE_ANSWER_LIMIT)] * 4
    for index, team in enumerate(session.teams):
        current = _check(
            team["client"].get(f"{session.base_url}/api/team/state"),
            f"team state {team['name']}",
        )
        one_change = fill_to_limit(LONG_CHANGE, DEFENSE_CHANGE_LIMIT)
        _check(
            team["client"].post(
                f"{session.base_url}/api/teams/defense",
                json={
                    "answers": answers if index % 2 == 0 else [LONG_ANSWER[:250]] * 4,
                    "oneChange": one_change,
                    "expectedDefenseSubmittedAt": current["team"]["defense"]["submittedAt"],
                },
            ),
            f"defense {team['name']}",
        )


def cast_votes(session: DemoSession) -> None:
    state = _check(session.get("/api/screen/state"), "screen state")
    finalists = state["finalists"]
    if len(finalists) < 3:
        raise RuntimeError("expected three finalists before voting")
    # A clear winner plus a tie-like spread: enough to exercise the reveal layout.
    plan = [0, 0, 0, 0, 1, 1, 2]
    for index, choice in enumerate(plan):
        with httpx.Client(timeout=15, follow_redirects=True) as voter:
            _check(
                voter.post(
                    f"{session.base_url}/api/vote",
                    json={"teamId": finalists[choice]["teamId"]},
                ),
                f"vote {index}",
            )


def run_full_session(session: DemoSession, on_phase: Any = None) -> str:
    """Drive the seeded session through every phase, calling on_phase().

    Returns the startup name put on stage in the finale.
    """

    def captured() -> None:
        if on_phase:
            on_phase()

    captured()
    session.advance("ideation")
    submit_pitches(session)
    captured()
    session.advance("jury")
    session.wait_for_round1()
    captured()
    session.advance("defense")
    submit_defenses(session)
    captured()
    session.advance("verdict")
    session.wait_for_verdicts()
    captured()
    session.advance("leaderboard")
    captured()
    session.advance("finale")
    # Put the longest finalist name on stage, the hardest case for the projector.
    state = _check(session.get("/api/screen/state"), "screen state")
    longest = max(
        range(len(state["finalists"])),
        key=lambda index: len(state["finalists"][index]["startupName"]),
    )
    assert session.moderator is not None
    _check(
        session.moderator.post(f"{session.base_url}/api/moderator/finale", json={"index": longest}),
        "select finalist",
    )
    captured()
    session.advance("voting")
    cast_votes(session)
    captured()
    session.advance("winner")
    captured()
    return str(state["finalists"][longest]["startupName"])


# --- Browser probes -------------------------------------------------------


LAYOUT_PROBE = r"""
() => {
  const viewportWidth = window.innerWidth;
  const viewportHeight = window.innerHeight;
  const doc = document.documentElement;
  const body = document.body;

  const selectorOf = (element) => {
    if (!element || element.nodeType !== 1) return "?";
    let value = element.tagName.toLowerCase();
    if (element.id) value += "#" + element.id;
    if (typeof element.className === "string" && element.className.trim()) {
      value += "." + element.className.trim().split(/\s+/).slice(0, 4).join(".");
    }
    return value;
  };
  const textOf = (element) => (element.textContent || "").trim().replace(/\s+/g, " ").slice(0, 70);
  const describe = (element) => selectorOf(element) + (textOf(element) ? ' "' + textOf(element) + '"' : "");
  const rectOf = (element) => element.getBoundingClientRect();

  const visible = (element) => {
    if (!element || element.nodeType !== 1) return false;
    const style = getComputedStyle(element);
    if (style.display === "none" || style.visibility === "hidden") return false;
    if (Number(style.opacity) === 0) return false;
    const rect = element.getBoundingClientRect();
    if (rect.width <= 1 || rect.height <= 1) return false;
    if (element.closest(".sr-only")) return false;
    return true;
  };

  const overflowAllowed = (element) => {
    if (element.closest(".sr-only")) return true;
    if (element.matches(".truncate, .ranking-name, .ranking-sub, .line-clamp-1, .line-clamp-2, .line-clamp-3")) return true;
    if (element.matches(".investor-portrait, .stage-cue")) return true;
    if (element.matches("textarea, input, select")) return true;
    return false;
  };

  const all = Array.from(document.querySelectorAll("body *"));
  const horizontal = [];
  const clipped = [];
  const textBlocks = [];

  for (const element of all) {
    if (!visible(element)) continue;
    const style = getComputedStyle(element);
    const rect = rectOf(element);
    if (element.matches("svg, path, circle, rect, line, img, canvas")) continue;
    if (overflowAllowed(element)) continue;

    if (style.position !== "fixed") {
      const leftOver = Math.max(0, -rect.left);
      const rightOver = Math.max(0, rect.right - viewportWidth);
      if (leftOver > 1 || rightOver > 1) {
        horizontal.push({
          selector: selectorOf(element),
          text: textOf(element),
          left: Math.round(rect.left * 10) / 10,
          right: Math.round(rect.right * 10) / 10,
          over: Math.round(Math.max(leftOver, rightOver) * 10) / 10,
        });
      }
    }

    if (element.matches("h1, h2, h3, p, dd, li, a, button, span.label, span.line-clamp-2, .screen-note, .screen-lead, .screen-title")) {
      textBlocks.push(element);
    }

    const ownOverflow = style.overflow + " " + style.overflowX + " " + style.overflowY;
    if (/(hidden|clip|auto|scroll)/.test(ownOverflow)) {
      if (element.matches(".truncate, .line-clamp-1, .line-clamp-2, .line-clamp-3, .ranking-name, .ranking-sub")) continue;
      const clippedX = element.scrollWidth > element.clientWidth + 1;
      const clippedY = element.scrollHeight > element.clientHeight + 1;
      if ((clippedX || clippedY) && textOf(element)) {
        clipped.push({
          selector: selectorOf(element),
          text: textOf(element),
          clientWidth: element.clientWidth,
          scrollWidth: element.scrollWidth,
          clientHeight: element.clientHeight,
          scrollHeight: element.scrollHeight,
          clippedX,
          clippedY,
        });
      }
    }
  }

  const overlaps = [];
  for (let i = 0; i < textBlocks.length; i += 1) {
    const a = textBlocks[i];
    if (!visible(a)) continue;
    const ra = rectOf(a);
    for (let j = i + 1; j < textBlocks.length; j += 1) {
      const b = textBlocks[j];
      if (!visible(b)) continue;
      if (a.contains(b) || b.contains(a)) continue;
      const rb = rectOf(b);
      const overlapX = Math.min(ra.right, rb.right) - Math.max(ra.left, rb.left);
      const overlapY = Math.min(ra.bottom, rb.bottom) - Math.max(ra.top, rb.top);
      if (overlapX > 4 && overlapY > 4) {
        overlaps.push({
          a: describe(a),
          b: describe(b),
          overlapX: Math.round(overlapX),
          overlapY: Math.round(overlapY),
        });
      }
    }
  }

  const rects = (selector) => Array.from(document.querySelectorAll(selector))
    .filter(visible)
    .map((element) => {
      const rect = rectOf(element);
      const style = getComputedStyle(element);
      return {
        selector: selectorOf(element),
        text: textOf(element),
        left: Math.round(rect.left * 10) / 10,
        right: Math.round(rect.right * 10) / 10,
        top: Math.round(rect.top * 10) / 10,
        bottom: Math.round(rect.bottom * 10) / 10,
        width: Math.round(rect.width * 10) / 10,
        height: Math.round(rect.height * 10) / 10,
        contentLeft: Math.round((rect.left + parseFloat(style.paddingLeft || "0")) * 10) / 10,
        contentRight: Math.round((rect.right - parseFloat(style.paddingRight || "0")) * 10) / 10,
      };
    });

  const tagOverflow = [];
  for (const tag of document.querySelectorAll(".lobby-jury .focus-tag, .lion-lineup .focus-tag")) {
    if (!visible(tag)) continue;
    const card = tag.closest(".lobby-jury-item, .lion-lineup > *");
    if (!card) continue;
    const tagRect = rectOf(tag);
    const cardRect = rectOf(card);
    const over = Math.max(0, cardRect.left - tagRect.left, tagRect.right - cardRect.right);
    if (over > 1) {
      tagOverflow.push({
        selector: selectorOf(tag),
        text: textOf(tag),
        over: Math.round(over * 10) / 10,
        card: selectorOf(card),
      });
    }
  }

  const screenContent = document.querySelector(".screen-content");
  const screenContentBottom = screenContent
    ? Math.round(Math.max(...[screenContent, ...screenContent.querySelectorAll("*")]
        .filter(visible)
        .map((element) => rectOf(element).bottom)) * 10) / 10
    : null;

  // Countdown consumers: every rendered clock, and any "time is up" notice.
  // The finale's 3-minute orientation clock is not a phase countdown: count it apart.
  const clockCount = [...document.querySelectorAll(".clock-digits")]
    .filter((el) => !el.closest("[data-pitch-clock]"))
    .filter(visible).length;
  const pitchClockCount = [...document.querySelectorAll("[data-pitch-clock]")].filter(visible).length;
  const timeIsUp = /time is up/i.test(document.body ? document.body.innerText : "");

  // The team's own pitch card: one per team view, flagged when it speaks to a finalist.
  const yourPitch = [...document.querySelectorAll('[data-testid="your-pitch"]')].map((element) => ({
    finalist: /use this while you pitch/i.test(element.innerText),
    cheatSheet: /cheat sheet/i.test(element.innerText),
    answers: element.querySelectorAll('[data-testid="cheat-sheet-answer"]').length,
    text: element.innerText,
  }));

  return {
    url: location.pathname + location.search,
    clockCount,
    pitchClockCount,
    yourPitch,
    timeIsUp,
    viewport: { width: viewportWidth, height: viewportHeight },
    scroll: {
      width: doc.scrollWidth,
      height: doc.scrollHeight,
      bodyHeight: body ? body.scrollHeight : 0,
      bodyWidth: body ? body.scrollWidth : 0,
    },
    screenContentBottom,
    horizontal,
    clipped,
    overlaps,
    tagOverflow,
    groups: {
      screenHeader: rects(".screen-header"),
      screenContent: rects(".screen-content"),
      screenPhase: rects(".screen-phase"),
      wordmark: rects(".wordmark"),
      phaseContainer: rects(".phase-enter"),
      teamLedgerHead: rects(".team-ledger-head"),
      teamRows: rects(".team-row-main"),
      neutralOrdinals: rects(".neutral-ordinal"),
      neutralNames: rects(".neutral-name"),
      revealOrdinals: rects(".reveal-ordinal"),
      revealNames: rects(".reveal-name"),
      podiumNames: rects(".podium-name"),
      podiumResults: rects(".podium-result"),
      finalistNames: rects(".finalist-band li"),
      voteHeader: rects("header > div"),
      voteMain: rects("main > div.mx-auto"),
      moderatorHeader: rects("header > div"),
      moderatorMain: rects("main > div.relative"),
      scoreReadings: rects(".score-reading"),
      verdictCards: rects(".verdict-card"),
      lobbyJuryTags: rects(".lobby-jury .focus-tag"),
      lobbyJuryNames: rects(".lobby-jury-name"),
      lobbyJuryLines: rects(".lobby-jury-line"),
      teamCardNames: rects(".investor-cards .investor-name"),
      teamCardTags: rects(".investor-cards .focus-tag"),
      teamCardRoles: rects(".investor-cards .investor-role"),
      screenInvestorNames: rects(".screen-lineup .investor-name"),
      screenInvestorTags: rects(".screen-lineup .focus-tag"),
      screenInvestorRoles: rects(".screen-lineup .investor-role"),
      screenInvestorCounts: rects(".screen-lineup .investor-count"),
      buildNeedLabels: rects(".build-need dt"),
      buildNeedHints: rects(".build-need dd"),
      buildJuryNames: rects(".build-lineup .investor-name"),
      buildPortraits: rects(".build-lineup .investor-portrait"),
      teamMeta: rects("header .line-clamp-2"),
      teamJoinForm: rects("#team-name"),
    },
  };
}
"""


def probe_page(page: Any) -> dict[str, Any]:
    return page.evaluate(LAYOUT_PROBE)


def _round(value: float) -> float:
    return round(value * 10) / 10


def layout_issues(metrics: dict[str, Any], *, mode: str) -> list[str]:
    """Turn raw browser metrics into human-readable invariant violations."""
    issues: list[str] = []
    width = metrics["viewport"]["width"]
    height = metrics["viewport"]["height"]
    scroll = metrics["scroll"]

    if scroll["width"] > width + 1:
        issues.append(
            f"horizontal document scroll: {scroll['width']}px wide in a {width}px viewport"
        )
    if mode == "screen" and scroll["height"] > height + 1:
        # The projector must not scroll at all; the moderator may scroll its long ledger.
        issues.append(
            f"vertical document scroll: {scroll['height']}px tall in a {height}px viewport"
        )
    content_bottom = metrics.get("screenContentBottom")
    if mode == "screen" and content_bottom is not None and content_bottom > height + 1:
        issues.append(
            f"projector content extends {_round(content_bottom - height)}px below the fold "
            f"(bottom {_round(content_bottom)}px in a {height}px viewport)"
        )

    for entry in metrics["groups"].get("buildPortraits", []):
        # The portraits are square images: a non-square box would crop them.
        if abs(entry["width"] - entry["height"]) > 1.5:
            issues.append(
                f"ideation investor portrait is not square ({_round(entry['width'])}x"
                f"{_round(entry['height'])}px), so it would be cropped"
            )
    for entry in metrics["horizontal"][:12]:
        issues.append(
            f"element past viewport edge by {entry['over']}px: {entry['selector']} {entry['text']!r}"
        )
    for entry in metrics.get("tagOverflow", [])[:12]:
        issues.append(
            f"focus tag spills out of its card by {entry['over']}px: "
            f"{entry['selector']} {entry['text']!r} in {entry['card']}"
        )
    for entry in metrics["clipped"][:12]:
        issues.append(
            "clipped content: {selector} {text!r} "
            "(x {clientWidth}->{scrollWidth}, y {clientHeight}->{scrollHeight})".format(**entry)
        )
    for entry in metrics["overlaps"][:12]:
        issues.append(
            f"overlapping text: {entry['a']} <> {entry['b']}"
            f" ({entry['overlapX']}x{entry['overlapY']}px)"
        )
    return issues


def alignment_issues(metrics: dict[str, Any], *, mode: str) -> list[str]:
    """Check that elements which should share an edge really do."""
    issues: list[str] = []
    groups = metrics["groups"]

    def edges(key: str, edge: str) -> list[float]:
        return [entry[edge] for entry in groups.get(key, [])]

    def same_edge(key: str, edge: str, tolerance: float = 1.5, label: str = "") -> None:
        values = edges(key, edge)
        if len(values) < 2:
            return
        spread = max(values) - min(values)
        if spread > tolerance:
            issues.append(
                f"{label or key} should share the same {edge} edge but spans "
                f"{_round(min(values))}..{_round(max(values))}px (spread {_round(spread)}px)"
            )

    if mode == "screen":
        header = groups["screenHeader"]
        content = groups["screenContent"]
        if header and content:
            left = abs(header[0]["left"] - content[0]["contentLeft"])
            right = abs(header[0]["right"] - content[0]["contentRight"])
            if left > 1.5 or right > 1.5:
                issues.append(
                    "projector header and content do not share the gutter: "
                    f"left delta {_round(left)}px, right delta {_round(right)}px"
                )
        for entry in groups["screenPhase"]:
            center = (entry["left"] + entry["right"]) / 2
            if abs(center - metrics["viewport"]["width"] / 2) > 2:
                issues.append(
                    "projector phase label is not centred: "
                    f"centre {_round(center)}px in a {metrics['viewport']['width']}px viewport"
                )
        same_edge("neutralOrdinals", "left", label="leaderboard ordinals")
        same_edge("neutralNames", "left", label="leaderboard names")
        same_edge("revealOrdinals", "left", label="reveal ordinals")
        same_edge("revealNames", "left", label="reveal names")
        same_edge("screenInvestorNames", "top", label="projector investor names")
        same_edge("screenInvestorTags", "top", label="projector focus tags")
        same_edge("screenInvestorRoles", "top", label="projector investor roles")
        same_edge("screenInvestorCounts", "top", label="projector investor counts")
        same_edge("lobbyJuryNames", "top", label="lobby investor names")
        same_edge("lobbyJuryTags", "top", label="lobby focus tags")
        same_edge("lobbyJuryLines", "top", label="lobby investor lines")

    if mode == "team":
        header = groups["wordmark"]
        content = groups["phaseContainer"]
        if header and content:
            delta = abs(header[0]["left"] - content[0]["contentLeft"])
            if delta > 2:
                issues.append(
                    f"team header and content do not share the left edge: delta {_round(delta)}px"
                )
        same_edge("teamRows", "left", label="team ledger rows")
        same_edge("teamCardNames", "top", label="team investor names")
        same_edge("teamCardTags", "top", label="team focus tags")
        same_edge("teamCardRoles", "top", label="team investor roles")
        meta = groups.get("teamMeta", [])
        if meta:
            entry = meta[0]
            if entry["left"] < 0 or entry["right"] > metrics["viewport"]["width"] + 1:
                issues.append(
                    "team name in the header leaves the viewport: "
                    f"{_round(entry['left'])}..{_round(entry['right'])}px"
                )

    if mode == "vote":
        header = groups["voteHeader"]
        content = groups["voteMain"]
        if header and content:
            left = abs(header[0]["left"] - content[0]["left"])
            right = abs(header[0]["right"] - content[0]["right"])
            if left > 1.5 or right > 1.5:
                issues.append(
                    "vote header and content do not share the column: "
                    f"left delta {_round(left)}px, right delta {_round(right)}px"
                )

    if mode == "moderator":
        header = groups["moderatorHeader"]
        content = groups["moderatorMain"]
        if header and content:
            left = abs(header[0]["left"] - content[0]["left"])
            right = abs(header[0]["right"] - content[0]["right"])
            if left > 2 or right > 2:
                issues.append(
                    "moderator header and content do not share the column: "
                    f"left delta {_round(left)}px, right delta {_round(right)}px"
                )

    return issues


@dataclass
class Capture:
    view: str
    state: str
    width: int
    height: int
    name: str
    metrics: dict[str, Any]

    def issues(self, *, mode: str | None = None) -> list[str]:
        effective_mode = mode or self.view
        return layout_issues(self.metrics, mode=effective_mode) + alignment_issues(
            self.metrics, mode=effective_mode
        )

    def summary(self) -> str:
        return (
            f"{self.view}/{self.state}/{self.name}: "
            f"{self.metrics['viewport']['width']}x{self.metrics['viewport']['height']}"
        )


VIEW_SELECTORS = {
    "screen": ".screen-header",
    "team": "main .wordmark",
    "vote": "main .wordmark, main .panel, main h1",
    "moderator": ".phase-rail, form",
}


def capture(
    session: DemoSession,
    browser: Any,
    view: str,
    state: str,
    width: int,
    height: int,
    name: str,
    directory: Path | None = None,
    *,
    team_token: str | None = None,
    moderator_token: str | None = None,
    wait_ms: int = 900,
) -> Capture:
    """Open one view at one viewport, optionally save a PNG, and probe layout."""
    device_scale_factor = (
        1.25 if name.endswith("-zoom125") else 0.8 if name.endswith("-zoom80") else 1
    )
    # Browser zoom changes the CSS viewport and device pixel ratio together.
    context = browser.new_context(
        viewport={"width": width, "height": height},
        device_scale_factor=device_scale_factor,
        reduced_motion="reduce",
    )
    try:
        cookies = []
        if team_token:
            cookies.append({"name": "jury_team", "value": team_token, "url": session.base_url})
        if moderator_token:
            cookies.append(
                {"name": "jury_moderator", "value": moderator_token, "url": session.base_url}
            )
        if cookies:
            context.add_cookies(cookies)
        page = context.new_page()
        path = {"screen": "/screen", "team": "/team", "vote": "/vote", "moderator": "/moderator"}[
            view
        ]
        page.goto(f"{session.base_url}{path}", wait_until="networkidle", timeout=30_000)
        page.wait_for_selector(VIEW_SELECTORS[view], timeout=20_000)
        page.wait_for_timeout(wait_ms)
        if view == "moderator" and state == "winner":
            # Open the first two team details: the unbroken problem text and a full-length
            # pitch, with all four answers and the one change at the server limits.
            for button in page.locator(".team-row button[aria-expanded=false]").all()[:2]:
                button.click()
            page.wait_for_selector("text=Defense answers", timeout=10_000)
            page.evaluate(
                "window.scrollTo(0, 0); document.querySelectorAll('*').forEach(e => { e.scrollTop = 0 })"
            )
            page.wait_for_timeout(200)
        metrics = probe_page(page)
        if directory is not None:
            directory.mkdir(parents=True, exist_ok=True)
            page.screenshot(
                path=str(directory / f"{view}-{state}-{name}.png"),
                full_page=False,
            )
        return Capture(
            view=view, state=state, width=width, height=height, name=name, metrics=metrics
        )
    finally:
        context.close()


def moderator_token() -> str:
    from backend.auth import moderator_token as token_for_password

    return token_for_password(MODERATOR_PASSWORD)


def pitch_card_team_tokens(session: DemoSession) -> dict[str, str]:
    """One finalist and one non-finalist team token, preferring the long-name team per kind."""
    first = session.teams[TEAM_VIEW_INDEX]
    state = _check(
        first["client"].get(f"{session.base_url}/api/team/state"),
        f"team state {first['name']}",
    )
    finalist_ids = {entry["teamId"] for entry in state["finalists"]}
    ordered = [first, *(team for team in session.teams if team is not first)]
    tokens: dict[str, str] = {}
    for team in ordered:
        kind = "finalist" if team["id"] in finalist_ids else "nonfinalist"
        token = team["client"].cookies.get("jury_team")
        if token and kind not in tokens:
            tokens[kind] = token
    assert set(tokens) == {"finalist", "nonfinalist"}, "need a finalist and a non-finalist team"
    return tokens


def capture_all(
    session: DemoSession,
    browser: Any,
    *,
    directory: Path | None = None,
    full_matrix: bool = True,
) -> list[Capture]:
    """Capture every view/phase/viewport combination in the agreed matrix."""
    captures: list[Capture] = []
    team_token = session.teams[TEAM_VIEW_INDEX]["client"].cookies.get("jury_team")
    assert team_token, "seeded team has no session cookie"
    mod_token = moderator_token()

    screen_viewports = SCREEN_VIEWPORTS if full_matrix else CI_SCREEN_VIEWPORTS
    team_viewports = TEAM_VIEWPORTS if full_matrix else CI_TEAM_VIEWPORTS
    public_team_viewports = TEAM_VIEWPORTS if full_matrix else CI_PUBLIC_TEAM_LOBBY_VIEWPORTS
    vote_viewports = VOTE_VIEWPORTS if full_matrix else CI_VOTE_VIEWPORTS
    screen_phases = CI_SESSION_PHASES
    team_phases = CI_SESSION_PHASES
    moderator_states = CI_MODERATOR_STATES if not full_matrix else CI_SESSION_PHASES

    phase_sets = [
        ("screen", screen_phases, screen_viewports),
        ("team", team_phases, team_viewports),
        ("moderator", moderator_states, MODERATOR_VIEWPORTS),
    ]
    for view, states, viewports in phase_sets:
        for state in states:
            if state != session.phase:
                continue
            for width, height, name in viewports:
                captures.append(
                    capture(
                        session,
                        browser,
                        view,
                        state,
                        width,
                        height,
                        name,
                        directory,
                        team_token=team_token if view == "team" else None,
                        moderator_token=mod_token if view == "moderator" else None,
                    )
                )

    if session.phase in PITCH_CARD_PHASES:
        width, height, name = PITCH_CARD_PHONE_VIEWPORT
        for kind, token in pitch_card_team_tokens(session).items():
            captures.append(
                capture(
                    session,
                    browser,
                    "team",
                    f"{session.phase}-{kind}",
                    width,
                    height,
                    name,
                    directory,
                    team_token=token,
                )
            )

    if session.phase == "lobby":
        for width, height, name in public_team_viewports:
            captures.append(
                capture(
                    session,
                    browser,
                    "team",
                    "lobby-public",
                    width,
                    height,
                    name,
                    directory,
                )
            )

    vote_state = f"{VOTE_STATE_BY_PHASE[session.phase]}-{session.phase}"
    for width, height, name in vote_viewports:
        captures.append(
            capture(session, browser, "vote", vote_state, width, height, name, directory)
        )

    return captures


def collect_session_layout(
    directory: Path,
    *,
    full_matrix: bool = True,
    screenshot_dir: Path | None = None,
) -> tuple[list[Capture], dict[str, Any]]:
    """Run a whole demo session and capture layout at every phase."""
    from playwright.sync_api import sync_playwright

    shots = screenshot_dir if screenshot_dir is not None else directory / "screenshots"
    shots.mkdir(parents=True, exist_ok=True)

    captures: list[Capture] = []
    session = start_demo_session(directory)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:

                def on_phase() -> None:
                    captures.extend(
                        capture_all(session, browser, directory=shots, full_matrix=full_matrix)
                    )

                finale_startup = run_full_session(session, on_phase)
            finally:
                browser.close()
        report: dict[str, Any] = {
            "baseUrl": session.base_url,
            "teams": [team["name"] for team in session.teams],
            "startups": [team["data"]["startup"] for team in session.teams],
            "finaleStartup": finale_startup,
            "phase": session.phase,
            "captures": len(captures),
        }
        return captures, report
    finally:
        session.close()


def write_issues_report(captures: list[Capture], path: Path) -> int:
    lines: list[str] = []
    failures = 0
    for capture_result in captures:
        issues = capture_result.issues()
        if issues:
            failures += 1
            lines.append(f"\n{capture_result.summary()}")
            lines.extend(f"  - {issue}" for issue in issues)
    path.write_text("\n".join(lines).strip() + "\n" if lines else "no layout issues\n")
    return failures


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="layout-check-") as temp:
        result, summary = collect_session_layout(Path(temp))
        print(json.dumps(summary, indent=2))
        for entry in result:
            for issue in entry.issues():
                print(f"{entry.summary()}: {issue}")
