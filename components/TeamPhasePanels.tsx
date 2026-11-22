"use client";

import { useEffect, useRef, useState } from "react";
import { INVESTOR_IDS } from "@/lib/investors";
import { PITCH_FIELDS } from "@/lib/pitch";
import { DEFENSE_ANSWER_LIMIT, DEFENSE_CHANGE_LIMIT } from "@/lib/session-state";
import type { FinalistView, PublicInvestor, RankedListView, SessionView, TeamStateView, WinnerBasis } from "@/lib/views";
import { WINNER_RULE, winnerExplanation } from "@/lib/winner";
import { FocusTag, InvestorAvatar } from "./brand";
import { Countdown, PitchClock } from "./Countdown";
import {
  DecisionChip,
  FinaleCallout,
  InlineNotice,
  PhaseHeading,
  ScoreDial,
  ScoreReading,
  ScoringRules,
  Stat,
  TeamResult,
} from "./ui";

type TeamView = NonNullable<TeamStateView["team"]>;

export function LobbyPanel({
  team,
  session,
  investors,
}: {
  team: TeamView;
  session: SessionView;
  investors: PublicInvestor[];
}) {
  return (
    <div>
      <PhaseHeading
        title={
          <>
            Welcome,
            <br />
            {team.name}.
          </>
        }
        lead="The moderator will start in a moment. Your team gets twenty minutes to shape a startup and write the pitch. Then four investors take it apart."
        aside={
          <div className="panel w-full max-w-[20rem] p-5">
            <p className="label">Session opens in</p>
            <Countdown
              endsAt={session.phaseEndsAt}
              serverNow={session.serverNow}
              sizeClass="text-stat-lg leading-none"
              className="mt-2"
            />
          </div>
        }
      />

      <div className="mt-8 max-w-3xl">
        <ScoringRules threshold={session.investmentThreshold} />
        <div className="mt-3">
          <FinaleCallout />
        </div>
      </div>

      <div className="lion-lineup investor-cards mt-10">
        {investors.map((investor) => (
          <div key={investor.id} className="min-w-0">
            <InvestorAvatar investor={investor} size={160} />
            <p className="investor-name text-body font-semibold text-cream">{investor.name}</p>
            <FocusTag investor={investor} />
            <p className="investor-role">{investor.title}</p>
            <p className="border-t border-line/60 pt-3 text-label leading-snug text-gold-2">
              “{investor.typicalConcern}”
            </p>
          </div>
        ))}
      </div>
    </div>
  );
}

export function JuryWaiting({
  team,
  investors,
}: {
  team: TeamView;
  investors: PublicInvestor[];
}) {
  if (team.eligible === false) {
    return (
      <PhaseHeading
        title="Not evaluated."
        lead="Only submitted, complete pitches from the pitch phase are judged. Watch the projector for the ranking and the finale."
      />
    );
  }

  return (
    <div>
      <PhaseHeading
        title="The jury is reading your pitch."
        lead="Four investors, four independent verdicts. Your pitch is locked."
      />

      <div className="lion-lineup investor-cards mt-10">
        {investors.map((investor) => {
          const entry = team.round1[investor.id];
          return (
            <div key={investor.id} className="min-w-0">
              <InvestorAvatar investor={investor} size={160} />
              <p className="investor-name text-body font-semibold text-cream">{investor.name}</p>
              <FocusTag investor={investor} />
              <p className="investor-role">{investor.title}</p>
              <div className="mt-3">
                <StatusPill status={entry.status} />
                <p className="text-label leading-relaxed text-muted">
                  {entry.status === "done"
                    ? "Verdict sealed. You will see the feedback in the defense phase."
                    : entry.status === "failed"
                      ? "The moderator is retrying this investor."
                      : entry.status === "running"
                        ? "Reading the pitch and scoring it."
                        : "Waiting for their turn."}
                </p>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function StatusPill({ status }: { status: "pending" | "running" | "done" | "failed" }) {
  const map = {
    pending: { text: "Queued", color: "var(--color-muted)" },
    running: { text: "Reading", color: "var(--color-gold)" },
    done: { text: "Sealed", color: "var(--color-mint)" },
    failed: { text: "Retrying", color: "var(--color-rose)" },
  } as const;
  const state = map[status];
  return (
    <span className="chip shrink-0" style={{ color: state.color }}>
      <span className="h-1.5 w-1.5 rounded-full bg-current" />
      {state.text}
    </span>
  );
}

export function DefensePhase({
  team,
  investors,
  onRefresh,
  threshold,
  scoreMax,
}: {
  team: TeamView;
  investors: PublicInvestor[];
  onRefresh: () => Promise<void> | void;
  threshold: number;
  scoreMax: number;
}) {
  const [answers, setAnswers] = useState<string[]>(
    team.defense.answers.length === 4 ? team.defense.answers : ["", "", "", ""],
  );
  const [oneChange, setOneChange] = useState(team.defense.oneChange ?? "");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [submittedAt, setSubmittedAt] = useState(team.defense.submittedAt);
  const expectedDefenseSubmittedAt = useRef(team.defense.submittedAt);
  const dirty = useRef(false);

  useEffect(() => {
    setSubmittedAt(team.defense.submittedAt);
    if ((team.defense.submittedAt ?? 0) > (expectedDefenseSubmittedAt.current ?? 0)) {
      expectedDefenseSubmittedAt.current = team.defense.submittedAt;
    }
    // Sync a submission made in another tab, but never overwrite active typing.
    if (!dirty.current) {
      setAnswers(team.defense.answers);
      setOneChange(team.defense.oneChange ?? "");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [team.defense.submittedAt]);

  if (team.eligible === false) {
    return (
      <PhaseHeading
        title="Not evaluated."
        lead="Only submitted, complete pitches from the pitch phase received jury questions. Follow the rest of the session on the projector."
      />
    );
  }

  const entries = INVESTOR_IDS.map((id) => ({
    investor: investors.find((candidate) => candidate.id === id)!,
    entry: team.round1[id],
  }));
  const allSealed = entries.every(({ entry }) => entry.status === "done");
  const failed = entries.some(({ entry }) => entry.status === "failed");
  const locked = team.defense.locked;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (submitting) return;
    const nextErrors: Record<string, string> = {};
    answers.forEach((answer, index) => {
      if (!answer.trim()) nextErrors[index] = "Answer this question.";
    });
    if (!oneChange.trim()) {
      nextErrors.oneChange = "Describe the one change you make because of the criticism.";
    }
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) return;
    setSubmitting(true);
    setFormError(null);
    try {
      const response = await fetch("/api/teams/defense", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          answers,
          oneChange,
          expectedDefenseSubmittedAt: expectedDefenseSubmittedAt.current,
        }),
      });
      const payload = (await response.json().catch(() => null)) as
        | {
            error?: string;
            fieldErrors?: Record<string, string>;
            team?: { defense?: { submittedAt: number | null } };
          }
        | null;
      if (!response.ok) {
        setFormError(payload?.error ?? "Could not submit the answers.");
        setErrors(payload?.fieldErrors ?? {});
        return;
      }
      const nextSubmittedAt = payload?.team?.defense?.submittedAt;
      if (typeof nextSubmittedAt === "number") {
        expectedDefenseSubmittedAt.current = nextSubmittedAt;
      }
      dirty.current = false;
      setSubmittedAt(payload?.team?.defense?.submittedAt ?? Date.now());
      await onRefresh();
    } catch {
      setFormError("Could not reach the server.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div>
      <div className="min-w-0">
        <PhaseHeading
          title="Answer the jury."
          lead="Four questions, one per investor, plus the one change you make. Up to 1000 characters per answer and 1000 for the change. The pitch itself stays locked."
          aside={
            <div className="text-right">
              <p className="label">Answered</p>
              <p className="stat-value mt-1 text-heading">
                {answers.filter((answer) => answer.trim().length > 0).length}
                <span className="text-body text-muted"> / 4</span>
              </p>
            </div>
          }
        />

        {locked ? (
          <div className="mt-6">
            <InlineNotice tone="info">
              The lions already judged this - it can no longer be changed.
            </InlineNotice>
          </div>
        ) : null}

        {!allSealed ? (
          <div className="mt-6">
            <InlineNotice tone={failed ? "error" : "info"}>
              {failed
                ? "One investor hit a technical snag. The moderator can retry; this page updates automatically."
                : "The jury is still finishing. Questions appear here as soon as all four verdicts are in."}
            </InlineNotice>
          </div>
        ) : null}

        {submittedAt && !locked ? (
          <div className="mt-6 flex flex-wrap items-center gap-3 rounded border border-mint/25 bg-mint/[0.06] px-4 py-3 text-small text-mint">
            <span className="font-semibold">Answers submitted.</span>
            <span>You can still refine them until the defense phase ends.</span>
          </div>
        ) : null}

        <form onSubmit={submit} className="mt-8 space-y-4">
          {entries.map(({ investor, entry }, index) => (
            <div
              key={investor.id}
              className="panel gap-8 p-5 md:p-6 lg:grid lg:grid-cols-[minmax(0,22rem)_minmax(0,1fr)]"
            >
              <div className="min-w-0">
                <div className="flex items-start gap-4">
                  <InvestorAvatar investor={investor} size={96} />
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                      <p className="text-body font-semibold text-cream">{investor.name}</p>
                      <FocusTag investor={investor} />
                    </div>
                    <p className="mt-0.5 text-label text-muted">{investor.title}</p>
                    {entry.score !== null ? (
                      <div className="mt-2">
                        <span className="label">Round 1</span>
                        <ScoreReading
                          score={entry.score}
                          threshold={threshold}
                          scoreMax={scoreMax}
                          provisional
                        />
                      </div>
                    ) : null}
                  </div>
                </div>
                <p className="mt-4 text-small leading-relaxed text-muted">
                  {entry.assessment ?? "The assessment appears once the investor has finished."}
                </p>
              </div>

              <div className="mt-5 min-w-0 lg:mt-0">
                <p className="display text-subhead leading-snug text-cream md:text-heading">
                  {entry.question ?? "Waiting for the question..."}
                </p>
                <div className="mt-4">
                  <textarea
                    className="input min-h-[7.5rem] leading-relaxed"
                    value={answers[index] ?? ""}
                    onChange={(event) => {
                      dirty.current = true;
                      const next = [...answers];
                      next[index] = event.target.value;
                      setAnswers(next);
                      setErrors((current) => ({ ...current, [index]: "" }));
                    }}
                    maxLength={DEFENSE_ANSWER_LIMIT}
                    readOnly={locked}
                    placeholder="Your answer, in at most 1000 characters."
                  />
                  <div className="mt-1.5 flex justify-between gap-3 text-label">
                    <span className={errors[index] ? "text-rose" : "text-muted"}>
                      {errors[index] || "Be concrete. Numbers beat adjectives."}
                    </span>
                    <span className="tabular-nums text-muted">
                      {(answers[index] ?? "").length}/{DEFENSE_ANSWER_LIMIT}
                    </span>
                  </div>
                </div>
              </div>
            </div>
          ))}

          <div className="panel p-5 md:p-6">
            <div className="flex items-baseline justify-between gap-3">
              <label htmlFor="defense-one-change" className="text-small font-semibold text-cream">
                Our one change
              </label>
              <span
                className={`text-label tabular-nums ${
                  oneChange.length > DEFENSE_CHANGE_LIMIT ? "text-rose" : "text-muted"
                }`}
              >
                {oneChange.length}/{DEFENSE_CHANGE_LIMIT}
              </span>
            </div>
            <p className="mt-1.5 text-label leading-relaxed text-muted">
              The one concrete change you make to the idea because of the criticism. The jury
              weighs it together with your four answers.
            </p>
            <textarea
              id="defense-one-change"
              className="input mt-3 min-h-[7rem] leading-relaxed"
              value={oneChange}
              onChange={(event) => {
                dirty.current = true;
                setOneChange(event.target.value);
                setErrors((current) => ({ ...current, oneChange: "" }));
              }}
              maxLength={DEFENSE_CHANGE_LIMIT}
              readOnly={locked}
              placeholder="e.g. We narrow the first customer to independent pharmacies and charge per store."
            />
            <p className={`mt-1.5 text-label ${errors.oneChange ? "text-rose" : "text-muted"}`}>
              {errors.oneChange || "One change, stated concretely."}
            </p>
          </div>

          {formError && !locked ? <InlineNotice tone="error">{formError}</InlineNotice> : null}

          <div className="flex flex-wrap items-center justify-between gap-4 pt-2">
            <p className="max-w-md text-label leading-relaxed text-muted">
              Each answer goes only to its investor; the one change goes to all four independently.
            </p>
            <button
              type="submit"
              className="btn btn-gold px-7 py-3.5"
              disabled={!allSealed || submitting || locked}
            >
              {submitting ? "Submitting..." : submittedAt ? "Update answers" : "Submit answers"}
              {!submitting ? <span aria-hidden="true">→</span> : null}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

function ScoreMovement({
  previousScore,
  score,
  threshold,
  scoreMax,
}: {
  previousScore: number | null;
  score: number | null;
  threshold: number;
  scoreMax: number;
}) {
  if (score === null) {
    return <ScoreDial score={score} size={80} />;
  }
  return (
    <div
      className="stat-value text-right"
      aria-label={`Round 1 score ${previousScore ?? "pending"}, final score ${score}`}
    >
      <p className="leading-none">
        <span className="text-heading font-semibold text-muted">{previousScore ?? "-"}</span>
        <span className="mx-1.5 text-subhead text-muted" aria-hidden="true">
          →
        </span>
        <span className="text-title font-semibold text-gold">{score}</span>
        <span className="ml-1 text-small text-muted">/{scoreMax}</span>
      </p>
      <p className="label mt-1">Round 1 → final</p>
      <ScoreReading score={score} threshold={threshold} scoreMax={scoreMax} className="mt-2" />
    </div>
  );
}

export function VerdictPhase({
  team,
  investors,
  threshold,
  scoreMax,
}: {
  team: TeamView;
  investors: PublicInvestor[];
  threshold: number;
  scoreMax: number;
}) {
  if (team.eligible === false) {
    return (
      <PhaseHeading
        title="Not evaluated."
        lead="Your pitch was not complete when the jury phase started, so the jury is not evaluating it. Watch the projector for the ranking and the finale."
      />
    );
  }

  const entries = INVESTOR_IDS.map((id) => ({
    investor: investors.find((candidate) => candidate.id === id)!,
    entry: team.verdict[id],
  }));
  const completed = entries.every(({ entry }) => entry.status === "done");
  const total = entries.reduce((sum, { entry }) => sum + (entry.score ?? 0), 0);
  const inCount = entries.filter(({ entry }) => entry.decision === "in").length;
  const failed = entries.some(({ entry }) => entry.status === "failed");

  return (
    <div>
      <PhaseHeading
        title="The jury decides."
        lead="Four independent verdicts arrive in parallel. Watch each investor's view unfold."
        aside={
          completed ? (
            <div className="reveal">
              <TeamResult
                inCount={inCount}
                total={total}
                investorCount={INVESTOR_IDS.length}
                scoreMax={scoreMax}
              />
            </div>
          ) : null
        }
      />

      <div className="panel mt-6 p-5">
        <p className="label label-gold">Our one change</p>
        <p className="mt-2 whitespace-pre-wrap text-small leading-relaxed text-cream/90">
          {team.defense.oneChange || "No one change was submitted."}
        </p>
      </div>

      <div className="mt-8 space-y-4">
        {entries.map(({ investor, entry }) => (
          <div
            key={investor.id}
            className={`panel verdict-card ${entry.status === "pending" ? "opacity-60" : ""}`}
          >
            <InvestorAvatar investor={investor} size={168} />
            <div className="min-w-0">
              <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                <p className="text-subhead font-semibold leading-tight text-cream">{investor.name}</p>
                <FocusTag investor={investor} />
              </div>
              <p className="mt-1 text-small text-muted">{investor.title}</p>
            </div>
            {/* Keyed by status so a newly arrived result reveals once and polling never replays it. */}
            <div
              key={`result-${entry.status}`}
              className={`verdict-result flex items-center gap-5 ${entry.status === "done" ? "reveal" : ""}`}
            >
              {entry.decision ? <DecisionChip decision={entry.decision} /> : null}
              {entry.score !== null ? (
                <ScoreMovement
                  previousScore={entry.previousScore}
                  score={entry.score}
                  threshold={threshold}
                  scoreMax={scoreMax}
                />
              ) : entry.status === "running" ? (
                <span className="chip text-gold">
                  <span className="h-1.5 w-1.5 rounded-full bg-gold" />
                  Evaluating
                </span>
              ) : entry.status === "failed" ? (
                <span className="chip text-rose">Interrupted</span>
              ) : (
                <span className="chip">Waiting</span>
              )}
            </div>

            <div
              key={`statement-${entry.status}`}
              className={`verdict-statement ${entry.status === "done" ? "reveal" : ""}`}
            >
              {entry.statement ? (
                <p>“{entry.statement}”</p>
              ) : entry.status === "running" ? (
                <p className="text-body text-muted">This investor is preparing an independent verdict.</p>
              ) : entry.status === "failed" ? (
                <p className="text-body text-rose">
                  The jury hit a technical snag. The moderator can retry this independent verdict.
                </p>
              ) : (
                <p className="text-body text-muted">Waiting for this independent verdict.</p>
              )}
            </div>
          </div>
        ))}
      </div>

      {failed ? (
        <div className="mt-6">
          <InlineNotice tone="error">
            The verdicts were interrupted. The moderator can retry without losing the other
            teams.
          </InlineNotice>
        </div>
      ) : null}
    </div>
  );
}

/**
 * The team's own pitch, its one change and a cheat sheet from the jury feedback and its own defense answers, read-only and
 * large enough to read on a phone on stage. It only uses the viewing team's own data.
 */
function YourPitchCard({
  team,
  investors,
  isFinalist,
}: {
  team: TeamView;
  investors: PublicInvestor[];
  isFinalist: boolean;
}) {
  const fields = PITCH_FIELDS.filter((field) => field.key !== "startupName");
  const hasPitch = PITCH_FIELDS.some((field) => team.pitch[field.key].trim().length > 0);
  const cheatSheet = INVESTOR_IDS.flatMap((id, index) => {
    const investor = investors.find((candidate) => candidate.id === id);
    const question = team.round1[id].question?.trim() ?? "";
    // The defense answers follow the lion order, the same way the defense form shows them.
    const answer = team.defense.answers[index]?.trim() ?? "";
    const statement = team.verdict[id].statement?.trim() ?? "";
    return investor && (question || answer || statement)
      ? [{ investor, question, answer, statement }]
      : [];
  });
  const oneChange = team.defense.oneChange.trim();

  return (
    <section className="panel reveal p-5 md:p-8" data-testid="your-pitch">
      <p className="label label-gold">Your pitch</p>
      <p className="mt-1 text-small leading-relaxed text-muted">
        {isFinalist
          ? "Use this while you pitch. It is only visible to your team."
          : "Your submitted pitch, read only. It is only visible to your team."}
      </p>

      {hasPitch ? (
        <>
          <h2 className="display mt-4 break-words text-title leading-tight text-cream [overflow-wrap:anywhere]">
            {team.pitch.startupName.trim() || team.name}
          </h2>
          <dl className="mt-5 space-y-5">
            {fields.map((field) => (
              <div key={field.key}>
                <dt className="label">{field.label}</dt>
                <dd className="mt-1 whitespace-pre-wrap break-words text-subhead leading-relaxed text-cream [overflow-wrap:anywhere]">
                  {team.pitch[field.key].trim() || "-"}
                </dd>
              </div>
            ))}
          </dl>
        </>
      ) : (
        <p className="mt-4 text-body text-muted">No pitch was submitted.</p>
      )}

      {oneChange ? (
        <div className="mt-6 border-t border-line/60 pt-5">
          <p className="label label-gold">Your one change</p>
          <p className="mt-1 whitespace-pre-wrap break-words text-subhead leading-relaxed text-cream [overflow-wrap:anywhere]">
            {oneChange}
          </p>
        </div>
      ) : null}

      {cheatSheet.length > 0 ? (
        <div className="mt-6 border-t border-line/60 pt-5">
          <p className="label label-gold">Cheat sheet: what the jury said</p>
          <ul className="mt-3 space-y-5">
            {cheatSheet.map(({ investor, question, answer, statement }) => (
              <li key={investor.id}>
                <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                  <p className="text-subhead font-semibold leading-tight text-cream">{investor.name}</p>
                  <FocusTag investor={investor} />
                </div>
                {question ? (
                  <p className="mt-2 break-words text-body leading-relaxed text-cream [overflow-wrap:anywhere]">
                    <span className="font-semibold text-gold">Asked: </span>
                    {question}
                  </p>
                ) : null}
                {answer ? (
                  <p
                    className="mt-2 whitespace-pre-wrap break-words text-body leading-relaxed text-cream [overflow-wrap:anywhere]"
                    data-testid="cheat-sheet-answer"
                  >
                    <span className="font-semibold text-gold">You answered: </span>
                    {answer}
                  </p>
                ) : null}
                {statement ? (
                  <p className="mt-2 break-words text-body leading-relaxed text-muted [overflow-wrap:anywhere]">
                    <span className="font-semibold text-gold">Final word: </span>
                    {statement}
                  </p>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}

function FinalistRows({
  finalists,
  teamId,
}: {
  finalists: FinalistView[];
  teamId?: string;
}) {
  return (
    <div className="panel overflow-hidden">
      {finalists.map((entry, index) => (
        <div
          key={entry.teamId}
          className={`reveal flex items-center gap-4 border-b border-line/60 px-5 py-4 last:border-0 ${
            entry.teamId === teamId ? "bg-gold/[0.08]" : ""
          }`}
          style={{ animationDelay: `${Math.min(index, 8) * 60}ms` }}
        >
          <span className="w-8 text-center text-small font-semibold tabular-nums text-brass">
            {String(index + 1).padStart(2, "0")}
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex min-w-0 items-baseline gap-2">
              <p className="min-w-0 truncate text-small font-semibold text-cream">
                {entry.startupName}
              </p>
              {entry.teamId === teamId ? (
                <span className="shrink-0 text-label font-medium text-gold">you</span>
              ) : null}
            </div>
            {entry.teamName !== entry.startupName ? (
              <p className="truncate text-label text-muted">{entry.teamName}</p>
            ) : null}
            <p className="mt-1 line-clamp-2 text-label leading-relaxed text-muted">
              {entry.tagline}
            </p>
          </div>
        </div>
      ))}
    </div>
  );
}

export function TeamLeaderboard({
  finalists,
  team,
  investors,
  scoreMax,
}: {
  finalists: FinalistView[];
  team: TeamView;
  investors: PublicInvestor[];
  scoreMax: number;
}) {
  const teamId = team.id;
  const isFinalist = finalists.some((entry) => entry.teamId === teamId);
  // Only the team's own verdict result is shown; no other team's figures exist here.
  const ownEntries = INVESTOR_IDS.map((id) => team.verdict[id]);
  const ownResult =
    team.eligible === true && ownEntries.every((entry) => entry.status === "done")
      ? {
          total: ownEntries.reduce((sum, entry) => sum + (entry.score ?? 0), 0),
          inCount: ownEntries.filter((entry) => entry.decision === "in").length,
        }
      : null;
  return (
    <div>
      <PhaseHeading
        title={isFinalist ? "You made the top three." : "The stage is set."}
        lead="The three finalists pitch live. The audience then picks the winner, and the full ranking is revealed together with the audience vote."
      />

      {isFinalist ? (
        <div className="mt-8">
          <InlineNotice tone="info">
            You are in the finale. Prepare your pitch now (about 3 minutes each): use the card below with your pitch, your one change and the jury cheat sheet. You go live right after this phase.
          </InlineNotice>
          <div className="mt-4" />
          <YourPitchCard team={team} investors={investors} isFinalist />
        </div>
      ) : null}

      <div className="mt-8 grid grid-cols-[minmax(0,1fr)] gap-6 lg:grid-cols-[21rem_minmax(0,1fr)] lg:items-start">
        <div className="panel reveal p-6">
          <p className="label">Your status</p>
          <p className={`display mt-2 text-title leading-tight ${isFinalist ? "text-gold" : "text-cream"}`}>
            {isFinalist ? "Finalist" : "Not in the finale"}
          </p>
          {ownResult ? (
            <div className="mt-5 border-t border-line/60 pt-4">
              <p className="label mb-2">Your verdict result</p>
              <TeamResult
                inCount={ownResult.inCount}
                total={ownResult.total}
                investorCount={INVESTOR_IDS.length}
                scoreMax={scoreMax}
              />
            </div>
          ) : null}
          <p className="mt-5 text-small leading-relaxed text-muted">
            {isFinalist
              ? "You pitch live next. The audience decides the winner."
              : "The top three teams take the stage. The full ranking is revealed after the audience vote."}
          </p>
        </div>

        <div>
          <p className="label mb-2">Pitch order (not a ranking)</p>
          {finalists.length === 0 ? (
            <InlineNotice tone="info">Waiting for the jury result.</InlineNotice>
          ) : (
            <FinalistRows finalists={finalists} teamId={teamId} />
          )}
        </div>
      </div>

      {isFinalist ? null : (
        <div className="mt-8">
          <YourPitchCard team={team} investors={investors} isFinalist={false} />
        </div>
      )}
    </div>
  );
}

function FinalistRow({
  entry,
  index,
  active = false,
  isTeam = false,
}: {
  entry: FinalistView;
  index: number;
  active?: boolean;
  isTeam?: boolean;
}) {
  return (
    <div className={`finalist-row ${active ? "is-active" : ""}`}>
      <span className="finalist-rank display">{String(index + 1).padStart(2, "0")}</span>
      <div className="min-w-0">
        <p className="display text-heading md:text-title">{entry.startupName}</p>
        {entry.teamName !== entry.startupName ? (
          <p className="mt-1 text-small text-muted">{entry.teamName}</p>
        ) : null}
        <p className="mt-2 max-w-2xl text-small leading-relaxed text-muted">{entry.tagline}</p>
      </div>
      <div className="text-right">
        {active ? <p className="text-label font-semibold text-gold">On stage now</p> : null}
        {isTeam ? <p className="mt-1 text-label font-semibold text-gold">That is you</p> : null}
      </div>
    </div>
  );
}

export function FinalePanel({
  team,
  investors,
  finalists,
  selectedFinalistIndex,
  session,
}: {
  team: TeamView;
  investors: PublicInvestor[];
  finalists: FinalistView[];
  selectedFinalistIndex: number;
  session: SessionView;
}) {
  const teamId = team.id;
  const isFinalist = finalists.some((entry) => entry.teamId === teamId);
  const activeIndex = Math.min(selectedFinalistIndex, Math.max(0, finalists.length - 1));
  return (
    <div>
      <PhaseHeading
        title={isFinalist ? "You are on stage." : "The top three pitch live."}
        lead={
          isFinalist
            ? "The room is watching. Make these minutes count."
            : "The finalists present in the room. The audience votes for the winner."
        }
      />

      {/* Only the finalist currently on stage sees the orientation clock; the projector shows none. */}
      {finalists[activeIndex]?.teamId === teamId ? (
        <div className="panel mt-8 p-5">
          <p className="label">You are on stage: pitch {activeIndex + 1} of {finalists.length}</p>
          <PitchClock
            endsAt={session.pitchEndsAt}
            serverNow={session.serverNow}
            className="mt-2"
            sizeClass="text-title"
            notStarted={session.pitchNotStarted}
            notStartedHint="Not started - the moderator will start your 3 minutes"
          />
        </div>
      ) : null}

      {isFinalist ? (
        <div className="mt-8">
          <YourPitchCard team={team} investors={investors} isFinalist />
        </div>
      ) : null}

      <div className="mt-8">
        {finalists.map((entry, index) => (
          <FinalistRow
            key={entry.teamId}
            entry={entry}
            index={index}
            active={index === activeIndex}
            isTeam={entry.teamId === teamId}
          />
        ))}
      </div>

      {isFinalist ? null : (
        <div className="mt-8">
          <YourPitchCard team={team} investors={investors} isFinalist={false} />
        </div>
      )}
    </div>
  );
}

export function VotingPanel({
  team,
  investors,
  isFinalist,
}: {
  team: TeamView;
  investors: PublicInvestor[];
  isFinalist: boolean;
}) {
  return (
    <div>
      <PhaseHeading
        title="The room is voting."
        lead="The audience picks the winner from their phones. One vote per browser. The result is revealed together with the jury result."
      />
      <div className="panel mt-8 p-6">
        <p className="label">On the projector</p>
        <p className="display mt-2 text-heading leading-tight">
          {isFinalist ? "Your team is one of the three finalists." : "The audience has the floor."}
        </p>
        <p className="mt-3 max-w-2xl text-small leading-relaxed text-muted">
          The vote closes when the moderator reveals the result. The projector shows the live
          number of votes, never a tally.
        </p>
      </div>
      <div className="mt-8">
        <YourPitchCard team={team} investors={investors} isFinalist={isFinalist} />
      </div>
    </div>
  );
}

export function WinnerPanel({
  teamId,
  finalists,
  ranking,
  votesCast,
  winnerBasis,
}: {
  teamId: string;
  finalists: FinalistView[];
  ranking: RankedListView[];
  votesCast: number;
  winnerBasis: WinnerBasis | null;
}) {
  const winner = finalists.find((entry) => entry.isWinner) ?? finalists[0];
  const winnerBadge =
    winnerBasis === "audienceVotes"
      ? "Audience winner"
      : winnerBasis === "juryInCount" || winnerBasis === "juryTotal"
        ? "Jury tie-break"
        : winnerBasis === "juryRanking"
          ? "Jury ranking"
          : "Winner";
  return (
    <div>
      <PhaseHeading
        title={winner ? `${winner.startupName} wins.` : "The room has decided."}
        lead={winnerExplanation(winnerBasis, votesCast)}
        aside={<Stat value={votesCast} label="Audience votes" />}
      />
      <p className="mt-2 text-label leading-relaxed text-muted">{WINNER_RULE}</p>

      {finalists.length === 0 ? (
        <div className="mt-8">
          <InlineNotice tone="info">Waiting for the audience result.</InlineNotice>
        </div>
      ) : (
        <div className="mt-8 space-y-3">
          {finalists.map((entry, index) => (
            <div
              key={entry.teamId}
              className={`panel reveal p-5 md:p-6 ${entry.isWinner ? "border-gold/50" : ""}`}
              style={{ animationDelay: `${index * 140}ms` }}
            >
              <div className="flex flex-wrap items-start justify-between gap-5">
                <div className="flex min-w-0 items-baseline gap-4">
                  <span className="display text-heading text-brass">
                    {String(index + 1).padStart(2, "0")}
                  </span>
                  <div className="min-w-0">
                    <p className="display text-heading leading-tight md:text-title">
                      {entry.startupName}
                      {entry.teamId === teamId ? (
                        <span className="ml-3 text-small font-sans font-semibold text-gold">you</span>
                      ) : null}
                    </p>
                    {entry.teamName !== entry.startupName ? (
                      <p className="mt-1 text-small text-muted">{entry.teamName}</p>
                    ) : null}
                  </div>
                </div>
                {entry.isWinner ? (
                  <p className="text-label font-semibold text-gold">{winnerBadge}</p>
                ) : null}
              </div>

              <div className="mt-5 grid gap-4 border-t border-line/60 pt-4 sm:grid-cols-3">
                <Stat value={entry.audienceVotes ?? 0} label="Audience votes" />
                <Stat value={`${entry.audienceShare ?? 0}%`} label="Audience share" />
                <TeamResult
                  inCount={entry.inCount ?? 0}
                  total={entry.total ?? 0}
                  investorCount={INVESTOR_IDS.length}
                />
              </div>
            </div>
          ))}
        </div>
      )}

      {ranking.length > 0 ? (
        <div className="mt-10">
          <p className="label">Full jury ranking · lions in, points break ties</p>
          <div className="panel mt-3 overflow-hidden">
            {ranking.map((entry) => (
              <div
                key={entry.teamId}
                className={`flex items-center gap-4 border-b border-line/60 px-5 py-3 last:border-0 ${
                  entry.teamId === teamId ? "bg-gold/[0.08]" : ""
                }`}
              >
                <span className="w-8 text-center text-small font-semibold tabular-nums text-muted">
                  {entry.rank}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-small font-semibold text-cream">
                    {entry.startupName}
                    {entry.teamId === teamId ? (
                      <span className="ml-2 text-label font-medium text-gold">you</span>
                    ) : null}
                  </p>
                  {entry.teamName !== entry.startupName ? (
                    <p className="truncate text-label text-muted">{entry.teamName}</p>
                  ) : null}
                </div>
                <div className="shrink-0 text-right">
                  <TeamResult
                    compact
                    inCount={entry.inCount}
                    total={entry.total}
                    investorCount={INVESTOR_IDS.length}
                  />
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

export function TeamMeta({ name }: { name: string }) {
  return (
    <span className="line-clamp-2 max-w-[22rem] text-right text-small font-medium leading-snug text-cream/90" title={name}>
      {name}
    </span>
  );
}
