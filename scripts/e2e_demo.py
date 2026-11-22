#!/usr/bin/env python3
"""End-to-end demo session against the real HTTP server.

Starts the built app in demo mode, joins 20 teams, runs a full session from
lobby to winner, verifies that every jury step completes, and measures that
page and API requests stay responsive while jury work runs.

Usage: npm run build && uv run python scripts/e2e_demo.py
Optional: E2E_OUTPUT=/path/report.json E2E_PORT=3210
"""

from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent.parent
PORT = int(os.environ.get("E2E_PORT") or random.randint(3200, 3699))
BASE = f"http://127.0.0.1:{PORT}"
DEMO_DELAY_MS = int(os.environ.get("E2E_DEMO_DELAY_MS") or 200)
INVESTMENT_SCORE_THRESHOLD = 7
STARTED_AT = time.time()

CHECKS: list[str] = []
TIMINGS: list[dict[str, Any]] = []

TEAM_NAMES = [
    "The Margin Calls",
    "Loopline",
    "Northstar Labs",
    "Peak Freight",
    "Second Opinion",
    "Grain & Gain",
    "Kite Health",
    "Nova Threads",
    "ClearRoute",
    "Bright Ledger",
    "Local Hero",
    "Tidal",
    "Urban Canopy",
    "Mendwell",
    "Polestar Grid",
    "Shelf Life",
    "Cobalt Care",
    "Riverbank",
    "Quiet Hours",
    "Field Notes",
]

PITCH_TEMPLATES = [
    {
        "tagline": "The return button that makes online fashion profitable.",
        "problem": "European fashion retailers lose 20 to 30 percent of margin on returns, mostly because returned items never re-enter local inventory. Customers wait two weeks for a refund and often never buy again.",
        "solution": "Loopline turns a return into a local resale in one click. The customer prints nothing, the retailer recovers most of the item value, and the next buyer nearby gets it in two days.",
        "customers": "Mid-sized online fashion retailers in Germany, France and the Netherlands with at least 100,000 orders per year and a return rate above 20 percent.",
        "businessModel": "A monthly SaaS fee plus 8 percent of every recovered item. We estimate 24,000 EUR annual contract value for a mid-sized retailer and a 14-month payback after the first warehouse integration.",
    },
    {
        "tagline": "Compliance answers before the auditor asks.",
        "problem": "Compliance teams at fintechs answer the same 200 questions every quarter in spreadsheets. Audits take weeks, evidence is stale, and senior people spend their time copying documents instead of reducing risk.",
        "solution": "Northstar keeps a live evidence graph of every control, owner and policy. It drafts answers with citations, flags gaps before an audit, and exports a reviewer pack in minutes.",
        "customers": "Series A to Series C fintechs with 20 to 200 employees and an internal compliance lead. We start with payment institutions in the EU.",
        "businessModel": "Per-seat pricing at 90 EUR per month with a platform fee of 1,500 EUR per month. Gross margin is 82 percent and the first ten design partners are already paying for pilot access.",
    },
    {
        "tagline": "Freight capacity that finds its own way home.",
        "problem": "Small freight forwarders run 40 percent of their trucks empty because matching happens by phone and email. A single missed backhaul can erase the margin on an entire route.",
        "solution": "Peak Freight predicts empty legs 48 hours ahead and offers them to nearby shippers at a dynamic price. Drivers accept from a phone, and forwarders keep their existing systems.",
        "customers": "Regional freight forwarders with 20 to 200 trucks, starting in the Rhine-Ruhr corridor where routes are dense and empty legs are visible.",
        "businessModel": "A 6 percent take rate on matched freight plus 49 EUR per vehicle per month. At 15 percent marketplace penetration, a corridor becomes profitable within two quarters.",
    },
    {
        "tagline": "The second opinion every patient deserves.",
        "problem": "Patients with a rare diagnosis wait months for a specialist and often receive conflicting advice. General practitioners do not have the time or the network to resolve uncertainty.",
        "solution": "Second Opinion routes a structured case to three matching specialists and returns a consolidated, cited view within five days. The GP stays in charge and the patient keeps one record.",
        "customers": "Private health insurers and corporate health programs in Germany that already pay for second-opinion services and want faster, measurable outcomes.",
        "businessModel": "A per-case fee of 390 EUR paid by insurers, plus an annual integration fee. Each specialist receives 120 EUR per review, leaving a 35 percent contribution margin per case.",
    },
    {
        "tagline": "Grain storage that pays for itself in one season.",
        "problem": "Up to 15 percent of stored grain spoils because farmers cannot see temperature and moisture inside a silo. A single spoiled batch can wipe out a season's profit.",
        "solution": "Grain & Gain installs cheap wireless probes and forecasts spoilage risk seven days ahead. The app tells the farmer when to ventilate or move grain, with no new silo hardware.",
        "customers": "Cooperative grain storage operators in Poland and Romania managing at least 20,000 tonnes across multiple silos.",
        "businessModel": "Hardware at cost plus 1.20 EUR per tonne per season for monitoring. A 30,000 tonne cooperative pays 36,000 EUR per year and avoids an estimated 120,000 EUR of spoilage.",
    },
    {
        "tagline": "Hiring signals that actually predict performance.",
        "problem": "Small companies hire on gut feel and lose 30,000 EUR or more on a bad senior hire. Assessment tools are either generic personality tests or expensive consulting projects.",
        "solution": "Kite Health runs a short, role-specific work sample and scores it against the team's best performers. Hiring managers get a structured interview guide and a clear decision memo.",
        "customers": "Founder-led companies with 20 to 150 employees hiring at least five senior roles per year, starting with software and professional services.",
        "businessModel": "1,200 EUR per role, billed only when a shortlist is delivered. Gross margin is 70 percent and the average customer runs eight roles per year.",
    },
    {
        "tagline": "A wardrobe that renews itself.",
        "problem": "Fashion rental is stuck between cheap one-off rentals and expensive subscription services. Customers churn because the selection does not match their real wardrobe, and brands cannot forecast demand.",
        "solution": "Nova Threads combines a style profile with local inventory from partner brands, then ships a rotating capsule that customers can buy at a discount. Brands get demand data and a new resale channel.",
        "customers": "Urban professionals aged 25 to 40 who already spend over 150 EUR per month on fashion, reached through three Berlin and Munich brand partners.",
        "businessModel": "A 79 EUR monthly subscription, 30 percent of which goes to the brand partner, plus resale margin on items kept. Contribution margin turns positive at 2,200 subscribers.",
    },
    {
        "tagline": "Every route cleared before the truck moves.",
        "problem": "Municipal waste fleets are planned weeks in advance and break daily because of construction, weather and blocked access. Dispatchers rebuild routes by hand while residents complain.",
        "solution": "ClearRoute ingests live road and asset data, re-plans routes overnight, and sends drivers a clean sequence each morning. Dispatchers see exceptions and approve changes in one screen.",
        "customers": "Mid-sized municipal waste operators serving 100,000 to 500,000 residents, starting with two German districts that already run telematics.",
        "businessModel": "An annual license based on vehicles, roughly 1,800 EUR per vehicle per year, plus a 25,000 EUR integration fee. The two pilot districts cover 60 percent of fixed costs.",
    },
    {
        "tagline": "Bookkeeping that closes itself.",
        "problem": "Small accounting firms spend 60 percent of their time chasing receipts and matching bank lines. Clients send photos, accountants re-key data, and month-end becomes a crisis.",
        "solution": "Bright Ledger connects to the bank and inbox, classifies transactions with an audit trail, and asks the client only for the five documents that are actually missing.",
        "customers": "Accounting firms with 5 to 30 staff in the DACH region that serve small businesses on fixed monthly retainers.",
        "businessModel": "149 EUR per client file per month, sold through the accounting firm. Each firm brings 80 to 300 files, and the gross margin is 78 percent after document processing costs.",
    },
    {
        "tagline": "The neighbourhood marketplace that shows up.",
        "problem": "Local shops lose customers to same-day delivery because they cannot afford a courier network. Marketplaces aggregate demand but take the customer relationship away from the shop.",
        "solution": "Local Hero gives independent shops a white-label storefront and pools deliveries across a postcode. Shops keep the customer, and the route density makes delivery profitable.",
        "customers": "Independent grocery, pharmacy and specialty food shops in dense city districts, starting with two Berlin neighbourhoods and 25 partner shops.",
        "businessModel": "12 percent commission on orders plus 2.90 EUR per delivery paid by the customer. A route needs 18 orders per hour to break even, which the pilot reached on Saturdays.",
    },
    {
        "tagline": "Water intelligence for buildings that cannot afford surprises.",
        "problem": "Hidden leaks in commercial buildings waste water and cause damage that insurers pay for. Facility managers only learn about a leak when a bill spikes or a ceiling collapses.",
        "solution": "Tidal attaches non-invasive acoustic sensors to main lines and learns each building's normal flow. It detects anomalies within hours and routes a work order to the right plumber.",
        "customers": "Facility managers of 20 to 100 commercial buildings, sold through insurance brokers who want to reduce water damage claims.",
        "businessModel": "A 35 EUR per building per month monitoring fee plus installation. Insurers subsidize the first year, and the average detected leak saves 4,000 EUR in damage.",
    },
    {
        "tagline": "The co-pilot for public procurement.",
        "problem": "Public buyers spend weeks turning a need into a compliant tender. Small suppliers cannot decode the documents, so competition stays thin and prices stay high.",
        "solution": "Local Hero Procurement drafts the tender, checks it against EU thresholds, and gives suppliers a plain-language checklist. Buyers approve every clause; nothing is auto-published.",
        "customers": "Municipal procurement offices in Germany with 5 to 40 buyers and an annual volume below the EU-wide threshold, plus the suppliers who bid.",
        "businessModel": "An annual license of 18,000 EUR per municipality plus 250 EUR per supplier onboarding. The first two municipalities signed letters of intent after a four-week pilot.",
    },
    {
        "tagline": "Shade and cooling as a service for city streets.",
        "problem": "Cities face hotter summers but cannot plant enough mature trees in time. Pavements store heat all day, and residents without shade suffer the most.",
        "solution": "Urban Canopy installs modular shade structures with sensors and native climbing plants. Each unit cools the surface below it within one season and reports its impact to the city.",
        "customers": "Municipal climate adaptation teams in German cities above 200,000 residents that already fund heat action plans and street greening.",
        "businessModel": "A per-unit installation fee of 4,500 EUR plus 480 EUR per year for maintenance and impact reporting. A pilot of 40 units covers the operating cost of one maintenance crew.",
    },
    {
        "tagline": "Repairs that keep equipment out of the landfill.",
        "problem": "Small repair shops cannot find spare parts for older machines, so half-broken devices are replaced instead of fixed. That wastes money and creates avoidable electronic waste.",
        "solution": "Mendwell scans a broken part, matches it to a network of local repair shops and 3D printing partners, and ships a verified replacement with a six-month warranty.",
        "customers": "Independent repair shops and facility managers running commercial coffee, cooling and cleaning equipment in Germany and Austria.",
        "businessModel": "A 19 percent marketplace fee on every repaired part plus a 49 EUR monthly subscription for shops that want priority matching and warranty handling.",
    },
    {
        "tagline": "The cheapest kilowatt is the one you do not need.",
        "problem": "Industrial sites pay for peak load they could avoid, but energy managers lack the time to shift flexible processes. Demand charges quietly become a large fixed cost.",
        "solution": "Polestar Grid forecasts the next day's load, finds flexible machines, and sends operators a short shift plan. The site keeps control and sees the savings per shift.",
        "customers": "Mid-sized manufacturers with flexible thermal or pumping loads and an annual electricity bill above 2 million EUR, starting in Germany and Poland.",
        "businessModel": "A share of verified savings in year one, then a fixed annual license of 1 percent of energy spend. A single site typically shifts 8 percent of its peak.",
    },
    {
        "tagline": "Fresh food that never becomes a surprise write-off.",
        "problem": "Grocery stores throw away perishables because expiry dates sit in different systems and staff only react when a shelf looks empty. The margin loss is invisible until month-end.",
        "solution": "Shelf Life combines shelf sensors with sales forecasts and marks down items at the right moment. Staff get one short list per shift instead of another dashboard.",
        "customers": "Independent grocery chains with 10 to 80 stores in the DACH region that already use electronic shelf labels.",
        "businessModel": "A monthly fee per store of 290 EUR plus a 15 percent share of the first year's verified waste reduction. A pilot store cut spoilage by 22 percent.",
    },
    {
        "tagline": "Care plans that follow the patient home.",
        "problem": "Patients leave hospital with paper instructions and no reliable follow-up. Readmissions rise, and nurses spend hours calling people who never answer.",
        "solution": "Cobalt Care turns a discharge plan into a short daily check-in by SMS or app, escalates warning signs to a nurse, and gives the clinic a single view of everyone at risk.",
        "customers": "Hospitals and care networks in Germany with high readmission rates for chronic conditions, starting with two cardiology departments.",
        "businessModel": "A per-patient fee of 18 EUR for a 30-day program, paid by hospitals and insurers. A 15 percent readmission reduction covers the fee after the first cohort.",
    },
    {
        "tagline": "Flood risk you can see before the water arrives.",
        "problem": "Communities learn about flood exposure only when a warning is already too late. Insurers and planners lack a street-level view of which assets are actually at risk.",
        "solution": "Riverbank combines open terrain, drainage and weather data into a street-level risk map, then sends asset owners a concrete list of protective measures and their cost.",
        "customers": "Municipal drainage authorities and property insurers in river catchments with repeated flood events, starting in the Rhine and Elbe corridors.",
        "businessModel": "An annual license of 25,000 EUR per municipality plus a per-asset assessment fee for insurers. Two pilot catchments already share their drainage data.",
    },
    {
        "tagline": "Focus time that a whole team can actually protect.",
        "problem": "Knowledge teams lose their best hours to meetings that nobody owns. Calendar tools optimize individual slots but ignore the team's shared work and time zones.",
        "solution": "Quiet Hours finds the team's collective focus windows, reschedules low-value meetings, and protects the result. Every change is suggested and approved by the team.",
        "customers": "Product and engineering teams of 20 to 200 people in European software companies that already measure delivery flow.",
        "businessModel": "A per-seat subscription of 6 EUR per month, sold to team leads. A 20-person team that recovers two focus hours per week pays less than one hour of engineering time.",
    },
    {
        "tagline": "Field data that survives bad reception.",
        "problem": "Surveyors, farmers and utility crews work where phones lose signal. They write on paper and re-enter everything later, which is slow and error-prone.",
        "solution": "Field Notes captures structured observations offline, syncs when a connection returns, and merges conflicts without losing either version. The office sees clean data the same day.",
        "customers": "Utilities, agricultural cooperatives and environmental consultancies that run field crews of 10 to 200 people in rural areas.",
        "businessModel": "A per-seat fee of 29 EUR per month plus a one-time setup package. The first utility customer replaced 400 paper forms in a single season.",
    },
]


def check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"Check failed: {name}{' - ' + detail if detail else ''}")
    CHECKS.append(name)
    print(f"  ok  {name}")


def assert_(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def advance_phase(moderator: httpx.Client) -> httpx.Response:
    state = moderator.get(f"{BASE}/api/moderator/state")
    assert_(state.status_code == 200, "moderator state unavailable before phase advance")
    current = state.json()["session"]["phase"]
    return moderator.post(f"{BASE}/api/moderator/phase", json={"expectedPhase": current})


def wait_for(
    label: str,
    predicate: Callable[[], bool],
    timeout_ms: float = 90_000,
    interval_ms: float = 150,
) -> None:
    deadline = time.time() * 1000 + timeout_ms
    while time.time() * 1000 < deadline:
        if predicate():
            return
        time.sleep(interval_ms / 1000)
    raise AssertionError(f"Timed out waiting for {label}.")


def percentile(values: list[float], point: float) -> float:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int((point / 100) * len(ordered) + 0.999999) - 1))
    return round(ordered[index] * 10) / 10


def measure_responsiveness(team: httpx.Client, moderator: httpx.Client, label: str) -> None:
    targets = [
        "/",
        "/team",
        "/screen",
        "/vote",
        "/moderator",
        "/api/team/state",
        "/api/screen/state",
        "/api/vote/state",
    ]
    local: list[float] = []
    for _ in range(6):
        for target in targets:
            started = time.perf_counter()
            client = team if target in ("/api/team/state", "/team") else moderator
            response = client.get(f"{BASE}{target}")
            elapsed = (time.perf_counter() - started) * 1000
            local.append(elapsed)
            assert_(
                response.status_code == 200,
                f"{label}: {target} returned {response.status_code} during jury work",
            )
            assert_(elapsed < 2_500, f"{label}: {target} took {round(elapsed)}ms")
    TIMINGS.append(
        {
            "label": label,
            "count": len(local),
            "p50": percentile(local, 50),
            "p95": percentile(local, 95),
            "max": round(max(local) * 10) / 10,
        }
    )
    check(
        f"{label}: pages and state stay responsive "
        f"(p95 {percentile(local, 95)}ms, max {round(max(local))}ms)",
        percentile(local, 95) < 1_000,
    )


def wait_for_server(client: httpx.Client, log_path: Path) -> None:
    deadline = time.time() + 45
    while time.time() < deadline:
        try:
            response = client.get(f"{BASE}/api/health")
            if response.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.3)
    log = log_path.read_text(errors="replace")[-2000:] if log_path.exists() else ""
    raise AssertionError(f"Server did not start.\n{log}")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="jury-e2e-") as directory:
        db_path = str(Path(directory) / "session.db")
        log_path = Path(directory) / "server.log"
        log_file = log_path.open("w")
        env = {
            **os.environ,
            "DEMO_MODE": "true",
            "MODERATOR_PASSWORD": "e2e-password",
            "SESSION_DB": db_path,
            "DEMO_DELAY_MS": str(DEMO_DELAY_MS),
            "JURY_MAX_CONCURRENCY": "8",
            # This script advances every phase itself.
            "JURY_AUTO_ADVANCE_SECONDS": "0",
            "PUBLIC_BASE_URL": BASE,
        }
        server = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "backend.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(PORT),
            ],
            cwd=ROOT,
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )

        try:
            with httpx.Client(timeout=10, follow_redirects=True) as anonymous:
                wait_for_server(anonymous, log_path)
                print(f"\nE2E demo session on {BASE}\n")

                unauthorized = anonymous.get(f"{BASE}/api/moderator/state")
                check("moderator state is protected", unauthorized.status_code == 401)

                anonymous_advance = anonymous.post(
                    f"{BASE}/api/moderator/phase", json={"expectedPhase": "lobby"}
                )
                check(
                    "moderator phase endpoint is protected",
                    anonymous_advance.status_code == 401,
                )

                with httpx.Client(timeout=10, follow_redirects=True) as moderator:
                    wrong = moderator.post(
                        f"{BASE}/api/moderator/login", json={"password": "wrong"}
                    )
                    check("wrong moderator password is rejected", wrong.status_code == 401)
                    login = moderator.post(
                        f"{BASE}/api/moderator/login", json={"password": "e2e-password"}
                    )
                    check(
                        "moderator logs in",
                        login.status_code == 200 and login.json().get("ok") is True,
                    )

                    teams: list[dict[str, Any]] = []
                    for index in range(20):
                        client = httpx.Client(timeout=10, follow_redirects=True)
                        response = client.post(
                            f"{BASE}/api/teams/join", json={"name": TEAM_NAMES[index]}
                        )
                        assert_(
                            response.status_code == 200,
                            f"join failed for {TEAM_NAMES[index]}: {response.status_code}",
                        )
                        teams.append(
                            {
                                "client": client,
                                "id": response.json()["team"]["id"],
                                "name": TEAM_NAMES[index],
                                "pitchUpdatedAt": None,
                            }
                        )
                    check("20 teams join the lobby", len(teams) == 20)

                    with httpx.Client(timeout=10, follow_redirects=True) as duplicate_client:
                        full = duplicate_client.post(
                            f"{BASE}/api/teams/join", json={"name": "Team 21"}
                        )
                        check(
                            "a 21st team is rejected because the session is full",
                            full.status_code == 409
                            and full.json()["error"] == "The session is full (20 teams).",
                        )

                    state = moderator.get(f"{BASE}/api/moderator/state")
                    check("moderator sees all 20 teams", len(state.json()["teams"]) == 20)

                    advance = advance_phase(moderator)
                    check(
                        "phase advances to ideation",
                        advance.status_code == 200
                        and advance.json()["session"]["phase"] == "ideation",
                    )

                    draft = teams[0]["client"].post(
                        f"{BASE}/api/teams/pitch",
                        json={
                            "pitch": {
                                "startupName": "Loopline",
                                "tagline": "A draft that saves itself.",
                            },
                            "submit": False,
                            "expectedPitchUpdatedAt": teams[0]["pitchUpdatedAt"],
                        },
                    )
                    check(
                        "an incomplete draft saves without submitting",
                        draft.status_code == 200 and draft.json()["team"]["submittedAt"] is None,
                    )
                    teams[0]["pitchUpdatedAt"] = draft.json()["team"]["pitchUpdatedAt"]

                    for index, template in enumerate(PITCH_TEMPLATES):
                        pitch = {
                            "startupName": TEAM_NAMES[index].removeprefix("The "),
                            **template,
                        }
                        saved = teams[index]["client"].post(
                            f"{BASE}/api/teams/pitch",
                            json={
                                "pitch": pitch,
                                "submit": False,
                                "expectedPitchUpdatedAt": teams[index]["pitchUpdatedAt"],
                            },
                        )
                        assert_(
                            saved.status_code == 200,
                            f"draft save failed for {teams[index]['name']}: {saved.status_code}",
                        )
                        teams[index]["pitchUpdatedAt"] = saved.json()["team"]["pitchUpdatedAt"]
                        submitted = teams[index]["client"].post(
                            f"{BASE}/api/teams/pitch",
                            json={
                                "pitch": pitch,
                                "submit": True,
                                "expectedPitchUpdatedAt": teams[index]["pitchUpdatedAt"],
                            },
                        )
                        teams[index]["pitchUpdatedAt"] = submitted.json()["team"]["pitchUpdatedAt"]
                        assert_(
                            submitted.status_code == 200,
                            f"submit failed for {teams[index]['name']}: {submitted.status_code}",
                        )
                        assert_(
                            submitted.json()["team"]["submittedAt"] is not None,
                            "submittedAt missing",
                        )
                    check("all 20 pitches are submitted", True)

                    advance = advance_phase(moderator)
                    check(
                        "phase advances to jury round 1",
                        advance.status_code == 200 and advance.json()["session"]["phase"] == "jury",
                    )
                    check(
                        "all submitted pitches are eligible",
                        all(team["eligible"] is True for team in advance.json()["teams"]),
                    )

                    locked = teams[0]["client"].post(
                        f"{BASE}/api/teams/pitch",
                        json={
                            "pitch": {
                                "startupName": "Changed",
                                "tagline": "Changed",
                                "problem": "Changed",
                                "solution": "Changed",
                                "customers": "Changed",
                                "businessModel": "Changed",
                            },
                            "submit": True,
                            "expectedPitchUpdatedAt": teams[0]["pitchUpdatedAt"],
                        },
                    )
                    check("the pitch is locked after the jury starts", locked.status_code == 409)

                    responsiveness_during_jury: list[Exception] = []

                    def jury_measure() -> None:
                        try:
                            measure_responsiveness(
                                teams[0]["client"], moderator, "during jury round 1"
                            )
                        except Exception as error:  # noqa: BLE001 - re-raised below
                            responsiveness_during_jury.append(error)

                    import threading

                    measurement = threading.Thread(target=jury_measure)
                    measurement.start()

                    def round1_done() -> bool:
                        response = moderator.get(f"{BASE}/api/moderator/state")
                        return all(
                            all(
                                team["round1"][investor_id]["status"] == "done"
                                for investor_id in (
                                    "growth",
                                    "finance",
                                    "customer",
                                    "technology",
                                )
                            )
                            for team in response.json()["teams"]
                        )

                    wait_for("round 1 to finish", round1_done)
                    measurement.join()
                    if responsiveness_during_jury:
                        raise responsiveness_during_jury[0]
                    check(
                        "all 80 round 1 verdicts are complete",
                        all(
                            all(
                                team["round1"][investor_id]["status"] == "done"
                                and not team["round1"][investor_id].get("error")
                                for investor_id in (
                                    "growth",
                                    "finance",
                                    "customer",
                                    "technology",
                                )
                            )
                            for team in moderator.get(f"{BASE}/api/moderator/state").json()["teams"]
                        ),
                    )

                    advance = advance_phase(moderator)
                    check(
                        "phase advances to defense",
                        advance.status_code == 200
                        and advance.json()["session"]["phase"] == "defense",
                    )

                    answers = [
                        "We start with a single narrow beachhead and one channel. The first twenty design partners pay a real price, which funds the next stage without a sales team. We measure activation, retention and payback weekly.",
                        "The 250,000 EUR buys twelve months of product and two customer-facing hires. Our milestone is a repeatable sales motion with payback below eighteen months, not headcount growth for its own sake.",
                        "Our customer already pays for a worse workaround and feels the pain at least weekly. We reach them through the channels where they already look, and the first ten conversations are booked before launch.",
                        "The defensible asset is the workflow and proprietary outcome data that compounds with every customer. A copycat can rebuild the surface, but not the operating history or the integrations our customers depend on.",
                    ]
                    for team in teams:
                        own = team["client"].get(f"{BASE}/api/team/state")
                        assert_(own.status_code == 200, "team state unavailable in defense")
                        assert_(
                            own.json()["team"]["round1"]["growth"]["question"],
                            "round 1 question missing in defense",
                        )
                        one_change = (
                            f"We narrow {team['name']} to one paying customer segment "
                            "and charge a flat fee per site."
                        )
                        submitted = team["client"].post(
                            f"{BASE}/api/teams/defense",
                            json={
                                "answers": answers,
                                "oneChange": one_change,
                                "expectedDefenseSubmittedAt": own.json()["team"]["defense"][
                                    "submittedAt"
                                ],
                            },
                        )
                        assert_(
                            submitted.status_code == 200,
                            f"defense submit failed for {team['name']}",
                        )
                        assert_(
                            submitted.json()["team"]["defense"]["oneChange"] == one_change,
                            f"one change missing for {team['name']}",
                        )
                    check("all 20 teams submit four answers and their one change", True)
                    moderator_defense = moderator.get(f"{BASE}/api/moderator/state").json()
                    check(
                        "the moderator team detail holds the one change",
                        all(team["defense"]["oneChange"] for team in moderator_defense["teams"]),
                    )

                    advance = advance_phase(moderator)
                    check(
                        "phase advances to the final verdict",
                        advance.status_code == 200
                        and advance.json()["session"]["phase"] == "verdict",
                    )

                    responsiveness_during_verdict: list[Exception] = []

                    def verdict_measure() -> None:
                        try:
                            measure_responsiveness(
                                teams[0]["client"], moderator, "during final verdicts"
                            )
                        except Exception as error:  # noqa: BLE001 - re-raised below
                            responsiveness_during_verdict.append(error)

                    measurement = threading.Thread(target=verdict_measure)
                    measurement.start()

                    def verdicts_done() -> bool:
                        response = moderator.get(f"{BASE}/api/moderator/state")
                        return all(
                            team["verdictCompletedAt"] is not None
                            for team in response.json()["teams"]
                        )

                    wait_for("final verdicts to finish", verdicts_done, 120_000)
                    measurement.join()
                    if responsiveness_during_verdict:
                        raise responsiveness_during_verdict[0]

                    for team in teams:
                        own = team["client"].get(f"{BASE}/api/team/state")
                        assert_(
                            own.status_code == 200,
                            f"{team['name']} state unavailable after verdict",
                        )
                        verdict = own.json()["team"]["verdict"]
                        for investor_id in (
                            "growth",
                            "finance",
                            "customer",
                            "technology",
                        ):
                            entry = verdict[investor_id]
                            assert_(
                                entry["status"] == "done",
                                f"{team['name']}: {investor_id} verdict missing",
                            )
                            assert_(
                                isinstance(entry.get("statement"), str)
                                and len(entry["statement"]) > 20,
                                f"{team['name']}: {investor_id} statement missing",
                            )
                            assert_(
                                1 <= entry["score"] <= 10,
                                f"{team['name']}: {investor_id} score invalid",
                            )
                            assert_(
                                isinstance(entry.get("previousScore"), int),
                                f"{team['name']}: {investor_id} previous score missing",
                            )
                            assert_(
                                abs(entry["score"] - entry["previousScore"]) <= 2,
                                f"{team['name']}: {investor_id} moved more than two points",
                            )
                            expected_decision = (
                                "in" if entry["score"] >= INVESTMENT_SCORE_THRESHOLD else "out"
                            )
                            assert_(
                                entry["decision"] == expected_decision,
                                f"{team['name']}: {investor_id} decision does not match the score",
                            )
                    check("all 20 teams see complete final verdicts with no errors", True)

                    advance = advance_phase(moderator)
                    check(
                        "phase advances to the leaderboard",
                        advance.status_code == 200
                        and advance.json()["session"]["phase"] == "leaderboard",
                    )
                    ranking = moderator.get(f"{BASE}/api/moderator/state").json()["ranking"]
                    check(
                        "the ranked leaderboard contains all 20 evaluated teams",
                        len(ranking) == 20,
                    )
                    check(
                        "the ranking counts deals first and breaks ties on points",
                        all(
                            index == 0
                            or (-ranking[index - 1]["inCount"], -ranking[index - 1]["total"])
                            <= (-entry["inCount"], -entry["total"])
                            for index, entry in enumerate(ranking)
                        ),
                    )
                    funded_ids = [entry["teamId"] for entry in ranking if entry["inCount"] > 0]
                    expected_finalist_ids = [entry["teamId"] for entry in ranking[:3]]
                    check(
                        "the finale has three finalists even when fewer teams are funded",
                        len(expected_finalist_ids) == 3,
                    )

                    screen_leaderboard = anonymous.get(f"{BASE}/api/screen/state")
                    screen_data = screen_leaderboard.json()
                    check(
                        "the projector names three finalists without jury figures",
                        screen_leaderboard.status_code == 200
                        and screen_data["ranking"] == []
                        and len(screen_data["finalists"]) == 3
                        and all(
                            not {"total", "inCount", "rank"} & set(entry)
                            for entry in screen_data["finalists"]
                        ),
                    )
                    check(
                        "the finalists are the top three of the ranking in a fixed pitch order",
                        sorted(entry["teamId"] for entry in screen_data["finalists"])
                        == sorted(expected_finalist_ids)
                        and screen_data["finalists"]
                        == anonymous.get(f"{BASE}/api/screen/state").json()["finalists"],
                    )
                    check(
                        "the finalists have an I'm in when three or more teams are funded",
                        len(funded_ids) < 3
                        or all(entry["teamId"] in funded_ids for entry in screen_data["finalists"]),
                    )
                    hidden_team = teams[0]["client"].get(f"{BASE}/api/team/state").json()
                    check(
                        "the team view hides the ranking and jury figures before the reveal",
                        hidden_team["ranking"] == []
                        and hidden_team["placement"] is None
                        and all(
                            not {"total", "inCount", "rank"} & set(entry)
                            for entry in hidden_team["finalists"]
                        ),
                    )
                    check(
                        "the projector has a join QR code and URL",
                        screen_data["qrDataUrl"].startswith("data:image/png;base64,")
                        and screen_data["joinUrl"].endswith("/team"),
                    )

                    early_finale = moderator.post(f"{BASE}/api/moderator/finale", json={"index": 0})
                    check(
                        "the finale spotlight is unavailable before the finale phase",
                        early_finale.status_code == 409,
                    )

                    anonymous_finale = anonymous.post(
                        f"{BASE}/api/moderator/finale", json={"index": 0}
                    )
                    check(
                        "the finale spotlight control is moderator-protected",
                        anonymous_finale.status_code == 401,
                    )

                    advance = advance_phase(moderator)
                    check(
                        "phase advances to the finale",
                        advance.status_code == 200
                        and advance.json()["session"]["phase"] == "finale",
                    )

                    finale_state = moderator.get(f"{BASE}/api/moderator/state").json()
                    check(
                        "the finale spotlight starts at the first of three finalists",
                        finale_state["finale"]["selectedIndex"] == 0
                        and len(finale_state["finale"]["finalists"]) == 3,
                    )
                    check(
                        "the moderator finale keeps the jury figures in the projector order",
                        [entry["teamId"] for entry in finale_state["finale"]["finalists"]]
                        == [entry["teamId"] for entry in screen_data["finalists"]]
                        and all(
                            {"total", "inCount", "rank"} <= set(entry)
                            for entry in finale_state["finale"]["finalists"]
                        ),
                    )

                    def select_finalist(index: int) -> httpx.Response:
                        return moderator.post(f"{BASE}/api/moderator/finale", json={"index": index})

                    selected = select_finalist(1)
                    check(
                        "the moderator Next finalist control selects finalist 2",
                        selected.status_code == 200
                        and selected.json()["finale"]["selectedIndex"] == 1,
                    )
                    projector = anonymous.get(f"{BASE}/api/screen/state").json()
                    check(
                        "the projector follows the moderator selection",
                        projector["selectedFinalistIndex"] == 1
                        and projector["finalists"][1]["teamId"]
                        == selected.json()["finale"]["finalists"][1]["teamId"],
                    )

                    selected = select_finalist(2)
                    check(
                        "the moderator Next finalist control selects finalist 3",
                        selected.status_code == 200
                        and selected.json()["finale"]["selectedIndex"] == 2,
                    )

                    selected = select_finalist(99)
                    check(
                        "a selection beyond the finalist list is clamped to the last finalist",
                        selected.status_code == 200
                        and selected.json()["finale"]["selectedIndex"] == 2,
                    )

                    selected = select_finalist(0)
                    check(
                        "the moderator Previous finalist control returns to finalist 1",
                        selected.status_code == 200
                        and selected.json()["finale"]["selectedIndex"] == 0,
                    )

                    selected = select_finalist(-1)
                    check(
                        "a selection below the finalist list is clamped to the first finalist",
                        selected.status_code == 200
                        and selected.json()["finale"]["selectedIndex"] == 0,
                    )

                    team_finale_state = teams[0]["client"].get(f"{BASE}/api/team/state")
                    check(
                        "the team view sees the same finale spotlight index",
                        team_finale_state.status_code == 200
                        and team_finale_state.json()["selectedFinalistIndex"] == 0,
                    )

                    advance = advance_phase(moderator)
                    check(
                        "phase advances to the audience vote",
                        advance.status_code == 200
                        and advance.json()["session"]["phase"] == "voting",
                    )

                    finalist_ids = [entry["teamId"] for entry in screen_data["finalists"]]
                    voting_screen = anonymous.get(f"{BASE}/api/screen/state").json()
                    check(
                        "the projector shows a vote QR code and URL",
                        voting_screen["voteQrDataUrl"].startswith("data:image/png;base64,")
                        and voting_screen["voteUrl"].endswith("/vote")
                        and voting_screen["votesCast"] == 0,
                    )
                    check(
                        "the projector shows no audience tally during voting",
                        voting_screen["ranking"] == []
                        and all(
                            "audienceVotes" not in entry for entry in voting_screen["finalists"]
                        ),
                    )

                    vote_state = anonymous.get(f"{BASE}/api/vote/state")
                    check(
                        "the vote page opens without an account",
                        vote_state.status_code == 200
                        and vote_state.json()["open"] is True
                        and len(vote_state.json()["finalists"]) == 3
                        and all("total" not in entry for entry in vote_state.json()["finalists"]),
                    )

                    duplicate_client = httpx.Client(timeout=10, follow_redirects=True)
                    duplicate_client.get(f"{BASE}/api/vote/state")
                    first_vote = duplicate_client.post(
                        f"{BASE}/api/vote", json={"teamId": finalist_ids[0]}
                    )
                    second_vote = duplicate_client.post(
                        f"{BASE}/api/vote", json={"teamId": finalist_ids[1]}
                    )
                    check(
                        "one browser cannot vote twice",
                        first_vote.status_code == 200 and second_vote.status_code == 409,
                    )
                    duplicate_client.close()

                    non_finalist_id = next(
                        team["id"] for team in teams if team["id"] not in finalist_ids
                    )
                    with httpx.Client(timeout=10, follow_redirects=True) as outsider:
                        outsider.get(f"{BASE}/api/vote/state")
                        rejected = outsider.post(
                            f"{BASE}/api/vote", json={"teamId": non_finalist_id}
                        )
                        check(
                            "a vote for a non-finalist is rejected",
                            rejected.status_code == 400,
                        )

                    vote_plan = (
                        [finalist_ids[0]] * 30 + [finalist_ids[1]] * 20 + [finalist_ids[2]] * 10
                    )

                    def cast_one(team_id: str) -> int:
                        voter = httpx.Client(timeout=10, follow_redirects=True)
                        try:
                            return voter.post(
                                f"{BASE}/api/vote", json={"teamId": team_id}
                            ).status_code
                        finally:
                            voter.close()

                    with ThreadPoolExecutor(max_workers=30) as pool:
                        statuses = list(pool.map(cast_one, vote_plan))
                    check(
                        "60 people vote at once",
                        statuses.count(200) == 60,
                    )

                    after_votes = anonymous.get(f"{BASE}/api/screen/state").json()
                    check(
                        "the projector counts votes without a tally",
                        after_votes["votesCast"] == 61
                        and after_votes["ranking"] == []
                        and all("audienceVotes" not in entry for entry in after_votes["finalists"]),
                    )

                    advance = advance_phase(moderator)
                    check(
                        "phase advances to the winner reveal",
                        advance.status_code == 200
                        and advance.json()["session"]["phase"] == "winner"
                        and advance.json()["votesCast"] == 61,
                    )

                    with httpx.Client(timeout=10, follow_redirects=True) as late_voter:
                        late_voter.get(f"{BASE}/api/vote/state")
                        closed = late_voter.post(
                            f"{BASE}/api/vote", json={"teamId": finalist_ids[0]}
                        )
                        check(
                            "voting closes when the reveal starts",
                            closed.status_code == 409,
                        )

                    reveal = anonymous.get(f"{BASE}/api/screen/state").json()
                    reveal_finalists = reveal["finalists"]
                    check(
                        "the reveal shows audience votes next to the jury result",
                        len(reveal_finalists) == 3
                        and all(
                            "audienceVotes" in entry and "total" in entry and "inCount" in entry
                            for entry in reveal_finalists
                        )
                        and len(reveal["ranking"]) == 20,
                    )
                    check(
                        "the audience winner has the most votes",
                        reveal_finalists[0]["isWinner"] is True
                        and reveal_finalists[0]["teamId"] == finalist_ids[0]
                        and reveal_finalists[0]["audienceVotes"] == 31
                        and reveal_finalists[0]["audienceVotes"]
                        >= max(entry["audienceVotes"] for entry in reveal_finalists[1:]),
                    )
                    check(
                        "the reveal explains that the most audience votes decided the winner",
                        reveal["winnerBasis"] == "audienceVotes",
                    )

                    final_team_state = teams[0]["client"].get(f"{BASE}/api/team/state")
                    team_reveal = final_team_state.json()
                    check(
                        "the team view shows the same reveal and the full ranking",
                        final_team_state.status_code == 200
                        and len(team_reveal["finalists"]) == 3
                        and team_reveal["finalists"][0]["isWinner"] is True
                        and len(team_reveal["ranking"]) == 20,
                    )
                    vote_reveal = anonymous.get(f"{BASE}/api/vote/state").json()
                    check(
                        "the team and vote views show the same winner explanation",
                        team_reveal["winnerBasis"] == "audienceVotes"
                        and vote_reveal["winnerBasis"] == "audienceVotes",
                    )

                    health = anonymous.get(f"{BASE}/api/health")
                    check(
                        "the session ended with all 20 teams persisted",
                        health.json()["teams"] == 20,
                    )

                    report = {
                        "ok": True,
                        "base": BASE,
                        "demo": True,
                        "durationMs": int((time.time() - STARTED_AT) * 1000),
                        "teams": 20,
                        "phaseSequence": [
                            "lobby",
                            "ideation",
                            "jury",
                            "defense",
                            "verdict",
                            "leaderboard",
                            "finale",
                            "voting",
                            "winner",
                        ],
                        "checks": CHECKS,
                        "timings": TIMINGS,
                        "ranking": [
                            {
                                "rank": entry["rank"],
                                "team": entry["teamName"],
                                "total": entry["total"],
                                "inCount": entry["inCount"],
                            }
                            for entry in ranking
                        ],
                        "finalists": [
                            {
                                "team": entry["teamName"],
                                "startup": entry["startupName"],
                                "audienceVotes": entry["audienceVotes"],
                                "audienceShare": entry["audienceShare"],
                                "juryTotal": entry["total"],
                                "inCount": entry["inCount"],
                                "winner": entry["isWinner"],
                            }
                            for entry in reveal_finalists
                        ],
                        "serverLogTail": (
                            log_path.read_text(errors="replace")[-4000:]
                            if log_path.exists()
                            else ""
                        ),
                    }

                    output = os.environ.get("E2E_OUTPUT")
                    if output:
                        Path(output).write_text(json.dumps(report, indent=2))
                        print(f"\nReport written to {output}")

                    print(
                        f"\nAll {len(CHECKS)} checks passed in {report['durationMs'] / 1000:.1f}s."
                    )
                    print(
                        "Finalists: "
                        + ", ".join(
                            f"{entry['startup']} "
                            f"({entry['audienceVotes']} audience, {entry['juryTotal']} jury)"
                            for entry in report["finalists"]
                        )
                    )
                    for team in teams:
                        team["client"].close()
                    return 0
        except Exception as error:  # noqa: BLE001 - top-level e2e failure
            print(f"\nE2E failed: {error}")
            if log_path.exists():
                print(f"\nServer log tail:\n{log_path.read_text(errors='replace')[-4000:]}")
            return 1
        finally:
            server.terminate()
            try:
                server.wait(timeout=3)
            except subprocess.TimeoutExpired:
                server.kill()
            log_file.close()
            shutil.rmtree(directory, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
