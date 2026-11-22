# Live lion benchmark

A first assessment of whether the lions give good answers, and in which cases they do
not, measured on the **deployed app** instead of calling the model directly. The script
plays one scripted session over the app's public HTTP
API: it registers the teams, submits the test pitches, advances the phases as moderator,
submits the scripted defense answers and then scores what the lions said.

It reuses the test set (`benchmarks/lion_quality_cases.json`) and the deterministic
scoring and report code of the [lion quality benchmark](lion-benchmark.md); it does not
change prompts, thresholds, app behaviour or phase logic.

## Run it against a deployment

```bash
export MODERATOR_PASSWORD=...            # the target's moderator password, never an argument
uv run python -m backend.live_benchmark --base-url https://<your-app-host>
```

Without `--confirm` that only prints the plan and changes nothing: the target, whether it
reports a demo or a live jury (from `/api/health`), the number of teams and the expected
jury calls. Check it, then add `--confirm`:

```bash
uv run python -m backend.live_benchmark --base-url https://<your-app-host> --confirm
```

**This replaces the current session on the target.** First the existing session is
exported through the moderator export API (kept as `previous-session-export.json` in the
output directory), then reset, which archives it under **Past runs**; the run stops
before the reset if the export or the archive fails. Teams that are connected have to
rejoin. Do not run it during a live event.

The target must be in the intended jury mode: for a real assessment the app has to run
the real jury (`DEMO_MODE=false` with the model settings). A demo target works for a dry
run, but its prepared answers say nothing about the lions; the plan and the report state
which mode the target reported.

## What it does

1. Logs in as moderator, exports and resets the old session.
2. Registers one team per pitch (the startup name; the repeated name of the paired case
   gets a plain number, so the jury never sees which defense a team plays).
3. Advances to ideation, submits every pitch, advances to the jury and waits for round 1.
4. Moves on to the defense with "Start now" (or tolerates the 15 s automatic advance;
   every advance sends `expectedPhase`, so a phase is never advanced twice), submits the
   scripted answers and the one change of every team.
5. Advances to the verdict, waits for it and advances to the leaderboard so the ranking is
   final. It stops there: no finale, no voting, no winner.

Polling uses `--poll-interval` (default 2 s) and `--jury-timeout` (default 600 s per jury
step). A failed jury call is retried through the moderator retry **at most once per team
and step**; a team that fails again is reported as failed and the others continue. On a
timeout or another error the run ends cleanly and writes a partial report. The exit code is
0 for a complete run, 1 for a partial or failed run and 2 when it refused to start.

## Limits and cost

One invocation plays **one session with at most 10 pitches**. The default is the 10
defense scenarios of the case file, one team each. A configuration with more than 10
pitches is refused before any request; `--only <case ids>` selects fewer. There is no
repeat option: the intended use is **one live run with the 10 scenarios**.

Every pitch costs 8 jury calls (4 round 1 plus 4 final verdict), so the default run makes
80 calls, and at most 160 if every team needed one retry in both steps. The app has no
token metering, so the report gives the number of calls (retries included) instead of
dollars. Transient retries inside a call are not visible from outside, so the count is a
lower bound of the billed requests.

## Output

A timestamped, gitignored directory under `data/benchmark-live/`:

- `live-benchmark.md`: the report. It starts with the target, jury mode, outcome, call
  count, durations and what happened to the previous session, then lists every team and
  states **per case whether the lions met the expectations** of the case file and what
  missed. Below that come the shared metric sections of the lion benchmark
  (discrimination by tier, defense effect including the paired fix-versus-dodge gap, lens
  adherence, faithfulness, length and tone, "I'm in" rate, manipulation resistance) and the
  worst examples with the lions' own words quoted.
- `live-benchmark.json`: the raw results (every score, assessment, question, statement and
  decision), metrics, expectations, issues, the call accounting and an event log.
- `previous-session-export.json`: the exported old session, when there was one.

With a single run, repeat consistency is not measured: the spread and flip columns stay
empty. A partial run reports the quality checks as not evaluated rather than as failures.
The moderator password is read from the environment, never logged and never written to a
file.

## Trying it without cost

Start the app locally in demo mode and point the script at it:

```bash
DEMO_MODE=true MODERATOR_PASSWORD=local-test-pw uv run ai-lions-den &
MODERATOR_PASSWORD=local-test-pw uv run python -m backend.live_benchmark \
  --base-url http://127.0.0.1:3000 --confirm
```

The prepared demo answers do not discriminate between good and bad pitches, so the report
will show failed expectations; that only demonstrates the flow. `tests/test_live_benchmark.py`
verifies the whole script in-process against the real app with a deterministic fake jury.
