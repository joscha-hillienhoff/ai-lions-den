# Lion quality benchmark

A reusable benchmark for the jury prompts. It drives the real round 1, defense and
verdict code path over a fixed set of pitches with known quality, then scores the
recorded output. It does not change prompts; it measures their current behaviour so a
later prompt change can be compared with a before/after run.

The run uses the normal `OPENAI_*` settings and needs a real key. `DEMO_MODE` must not
be `true` (leave it unset or set it to `false`), because the benchmark refuses a demo
configuration rather than report fake evidence. It never runs as part of the test suite:
the tests use a deterministic fake client instead.

## Run it

```bash
uv run python -m backend.benchmark --confirm
```

That is the whole command. It reads `.env` and `.env.local` like the app, uses
`OPENAI_MODEL` (default `gpt-4.1-mini`), and runs 9 pitches with 10 defense scenarios,
three times each: 30 simulated sessions and 240 expected model calls.

Before the first call it prints the expected calls and estimated token cost and stops
unless `--confirm` is present. The default hard cap is 4 USD. Spend is computed from the
reported prompt and completion tokens after every call, and a call is refused before the
estimated worst case could cross the cap. Actual usage is recorded, so a final in-flight
request can exceed the cap by that request; the 4 USD default remains below the 5 USD limit.

Expected cost at list prices: about 0.86 USD with `gpt-4.1-mini` for the full 30
sessions without retries. With `gpt-4.1` the full three repeats would cost about 4.32 USD
and would exceed the 4 USD cap, so use `--repeats 2` there (20 sessions, about 2.88 USD).
The printed estimate is the authoritative no-retry estimate; retry attempts are billable
and add their own spend.

Useful options:

```bash
# shorter smoke run
uv run python -m backend.benchmark --repeats 1 --confirm

# one or two cases only
uv run python -m backend.benchmark --only strong-reusable-packaging --confirm

# a model whose name is an Azure deployment rather than a priced model key
uv run python -m backend.benchmark --price-model gpt-4.1-mini --confirm

# verify the whole pipeline without a key or a network call
uv run python -m backend.benchmark --provider fake --repeats 1 --confirm
```

Prices live in `backend/jury/pricing.py`. Add a model there rather than passing a made-up
price.

## Test set

`benchmarks/lion_quality_cases.json` is the machine-readable test set. Each case has a
tier, a language, a short structured pitch, one or two scripted defense variants and the
expected behaviour.

| Case | Tier | Language | What it probes |
|---|---|---|---|
| `strong-reusable-packaging` | strong | en | Concrete numbers, signed pilots, clear buyer and a fixing defense |
| `weak-vague-wellness` | weak | en | Vague customer, no numbers, dodge defense |
| `mixed-flexibility` | mixed | en | Plausible business with thin unit economics |
| `one-sided-deepfake-detection` | one-sided | en | Strong technology, no chosen market or buyer |
| `troll-plant-calendar` | troll | en | Nonsense pitch with emoji and no viable business |
| `manipulation-resume` | manipulation | en | Prompt injection in the pitch and a defense answer, asking for 10/10 |
| `german-nursing-handover` | strong | de | Full German pitch with numbers and a fixing defense |
| `paired-defense-skillbridge` | mixed | en | The same pitch twice: a fixing defense and a dodging defense |
| `strong-dental-noshow` | strong | en | Solid business with simple but real AI (a small model plus drafted reminders); the lions must still score the business first |

The vague "AI-powered app" case (`weak-vague-wellness`, Mindful) must stay clearly low (mean total at most 20, "I'm in" rate at most 0.25), while `strong-dental-noshow` must score well (mean total at least 24, "I'm in" rate at least 0.5). Together they show the lions judge the business first and do not reward or punish the AI label.

The paired case is what separates defense quality from pitch quality. The JSON also holds
the expected score range, expected "I'm in" rate, forbidden phrases and the minimum gap
between the fixing and dodging variants.

## What is measured

Everything is deterministic and computed from the recorded output. There is no LLM judge
and therefore no hidden cost.

- **Discrimination**: mean final score by tier, with explicit tier pairs that must be at
  least one point apart. A partial case selection is marked partial and never establishes
  the full criterion.
- **Defense effect**: score delta from round 1 to final for fixing, dodging and neutral
  answers, plus the paired fix-versus-dodge gap.
- **Repeat consistency**: per-investor score spread and the in/out flip rate across the
  three repeats.
- **Lens adherence**: a text passes when it contains at least one term from the
  investor's own lens; a leak is a text with no own-lens term and at least two foreign
  ones.
- **Faithfulness**: every cited number must appear in the pitch or the team's answer, and
  every quoted phrase must overlap the team's material. The deal constants (250,000
  EUR and 10 percent) are allowed. This is a heuristic over numbers and quotes only;
  paraphrased invented facts require reading the worst examples in the report.
- **Length and tone**: word counts against the prompt targets, one-question check, second
  person address, meta or setup leakage, and whether an "I'm in" / "I'm out" phrase
  matches the derived decision.
- **"I'm in" rate**: share of the four investors who invest, overall and by case.
- **Manipulation resistance**: the injected case must stay low, must not invest, and must
  not repeat the forbidden score phrases.
- **Cost**: spend, calls and cost per completed session.

## Output

A run writes to a timestamped, gitignored directory under `data/benchmark/`:

- `lion-benchmark.json`: raw runs, metrics, machine-readable expectations and collected
  issues.
- `lion-benchmark.md`: a concise report with the metric tables, failed expectations and
  the worst examples quoted, ending with a next step for reviewing prompt improvements.

A run is partial when any planned session failed or the budget cap aborted it. Quality
checks are then reported as not evaluated (`ok: null`) rather than as failures, the cost
line is per completed session, and the command exits with status 1. Exit status 2 means
invalid input or configuration before any call was made.

To compare a prompt change, review the report for concrete prompt improvements, apply a
change, then run the benchmark on the branch before the change and again after it. Validate
the improvement by comparing the two JSON files and reports. Keep the same model, repeats
and temperature; `JURY_TEMPERATURE` is part of the normal settings and is not overridden by
the benchmark.

## Scope

The benchmark validates the pipeline in `tests/test_benchmark.py` with the fake client,
including the budget cap, the CLI confirmation gate and both output files. It does not
call a real model in tests and it does not modify the jury prompts.

To assess the lions on the deployed app instead (one session over its HTTP API, at most 10
pitches), see [live-benchmark.md](live-benchmark.md).
