"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { stepFinalistIndex } from "@/lib/finale";
import { INVESTOR_IDS } from "@/lib/investors";
import { PHASES, isAtLeast, type PhaseId } from "@/lib/session-state";
import type {
  ArchiveSummary,
  ArchivedRunView,
  ArchivedTeamView,
  ModeratorStateView,
  ModeratorTeamView,
  PublicInvestor,
} from "@/lib/views";
import { FocusTag, InvestorAvatar, Wordmark } from "./brand";
import { AutoAdvanceNote, Countdown, PitchClock } from "./Countdown";
import {
  CopyButton,
  InlineNotice,
  Modal,
  ScoreReading,
  Splash,
  TeamResult,
} from "./ui";
import { usePoll } from "./usePoll";

export function ModeratorApp() {
  const { data, status, loading, error, refresh } = usePoll<ModeratorStateView>(
    "/api/moderator/state",
    2000,
  );

  if (loading && !data) return <Splash label="Opening the control room" />;
  if (status === 401 || (!data && status !== null)) {
    return <ModeratorLogin onLoggedIn={refresh} serverError={error} />;
  }
  if (!data) {
    return (
      <main className="stage relative grid min-h-dvh place-items-center p-6">
        <div className="panel relative z-10 rounded p-8 text-center">
          <p className="display text-heading">Connection lost</p>
          <p className="mt-2 text-small text-muted">{error}</p>
          <button className="btn btn-gold mt-5 px-5 py-3" onClick={() => refresh()}>
            Reconnect
          </button>
        </div>
      </main>
    );
  }

  return <ModeratorDashboard data={data} refresh={refresh} pollError={error} />;
}

function ModeratorLogin({
  onLoggedIn,
  serverError,
}: {
  onLoggedIn: () => Promise<void> | void;
  serverError: string | null;
}) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function login(event: React.FormEvent) {
    event.preventDefault();
    if (pending) return;
    setPending(true);
    setError(null);
    try {
      const response = await fetch("/api/moderator/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password }),
      });
      const payload = (await response.json().catch(() => null)) as { error?: string } | null;
      if (!response.ok) {
        setError(payload?.error ?? "Login failed.");
        return;
      }
      await onLoggedIn();
    } catch {
      setError("Could not reach the server.");
    } finally {
      setPending(false);
    }
  }

  return (
    <main className="stage relative grid min-h-dvh place-items-center overflow-hidden p-6">
      <div className="relative z-10 w-full max-w-md">
        <div className="mb-8 flex justify-center">
          <Wordmark />
        </div>
        <form onSubmit={login} className="panel rounded p-7 md:p-8">
          <p className="label label-gold">Control room</p>
          <h1 className="display mt-2 text-heading">Moderator access</h1>
          <p className="mt-2 text-small leading-relaxed text-muted">
            Advance the session, watch every team, and retry jury steps if something fails.
          </p>

          <label htmlFor="moderator-password" className="label mb-2 mt-7 block">
            Password
          </label>
          <input
            id="moderator-password"
            type="password"
            className="input"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoFocus
            autoComplete="current-password"
          />

          {error ? (
            <div className="mt-3">
              <InlineNotice tone="error">{error}</InlineNotice>
            </div>
          ) : null}
          {!error && serverError ? (
            <div className="mt-3">
              <InlineNotice tone="error">{serverError}</InlineNotice>
            </div>
          ) : null}

          <button
            type="submit"
            className="btn btn-gold mt-6 w-full px-6 py-3.5"
            disabled={pending || password.length === 0}
          >
            {pending ? "Checking..." : "Enter control room"}
          </button>
        </form>
      </div>
    </main>
  );
}

function ModeratorDashboard({
  data,
  refresh,
  pollError,
}: {
  data: ModeratorStateView;
  refresh: () => Promise<void>;
  pollError: string | null;
}) {
  const [advancing, setAdvancing] = useState(false);
  const [advanceError, setAdvanceError] = useState<string | null>(null);
  const [backOpen, setBackOpen] = useState(false);
  const [goingBack, setGoingBack] = useState(false);
  const [holding, setHolding] = useState(false);
  const [resetOpen, setResetOpen] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [pastRunsVersion, setPastRunsVersion] = useState(0);
  const [retrying, setRetrying] = useState<string | null>(null);
  const [retryNote, setRetryNote] = useState<string | null>(null);
  const [finaleBusy, setFinaleBusy] = useState(false);
  const [finaleError, setFinaleError] = useState<string | null>(null);

  const phase = data.session.phase;
  const next = PHASES[PHASES.findIndex((entry) => entry.id === phase) + 1] ?? null;
  const previous = PHASES[PHASES.findIndex((entry) => entry.id === phase) - 1] ?? null;
  const joinBase =
    data.mode.publicBaseUrl ??
    (typeof window !== "undefined" ? window.location.origin : "");
  const joinUrl = `${joinBase}/team`;
  const screenUrl = `${joinBase}/screen`;
  const voteUrl = `${joinBase}/vote`;

  const sortedTeams = useMemo(
    () => [...data.teams].sort((a, b) => a.createdAt - b.createdAt),
    [data.teams],
  );

  const failedTeams = data.teams.filter((team) => team.errorCount > 0).length;
  const spotlight = data.finale.finalists[data.finale.selectedIndex];

  async function advance() {
    if (advancing || !next) return;
    setAdvancing(true);
    setAdvanceError(null);
    try {
      const response = await fetch("/api/moderator/phase", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ expectedPhase: phase }),
      });
      const payload = (await response.json().catch(() => null)) as { error?: string } | null;
      if (!response.ok) {
        setAdvanceError(payload?.error ?? "Could not advance the phase.");
        await refresh();
        return;
      }
      await refresh();
    } catch {
      setAdvanceError("Could not reach the server.");
    } finally {
      setAdvancing(false);
    }
  }

  async function holdAutoAdvance() {
    if (holding) return;
    setHolding(true);
    setAdvanceError(null);
    try {
      const response = await fetch("/api/moderator/phase/hold", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ expectedPhase: phase }),
      });
      if (!response.ok) {
        const payload = (await response.json().catch(() => null)) as { error?: string } | null;
        setAdvanceError(payload?.error ?? "Could not hold the countdown.");
      }
      await refresh();
    } catch {
      setAdvanceError("Could not reach the server.");
    } finally {
      setHolding(false);
    }
  }

  async function goBack() {
    if (goingBack || !previous) return;
    setGoingBack(true);
    setAdvanceError(null);
    try {
      const response = await fetch("/api/moderator/phase/back", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ expectedPhase: phase }),
      });
      const payload = (await response.json().catch(() => null)) as { error?: string } | null;
      if (!response.ok) {
        setAdvanceError(payload?.error ?? "Could not go back a phase.");
      }
      setBackOpen(false);
      await refresh();
    } catch {
      setAdvanceError("Could not reach the server.");
      setBackOpen(false);
    } finally {
      setGoingBack(false);
    }
  }

  async function retry(teamId: string, step: "round1" | "verdict") {
    setRetrying(`${teamId}:${step}`);
    setRetryNote(null);
    try {
      const response = await fetch("/api/moderator/retry", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ teamId, step }),
      });
      if (!response.ok) {
        const payload = (await response.json().catch(() => null)) as { error?: string } | null;
        setRetryNote(payload?.error ?? "Retry failed to start.");
        return;
      }
      setRetryNote(`${step === "round1" ? "Round 1" : "Verdict"} retry started.`);
      setTimeout(() => void refresh(), 600);
    } catch {
      setRetryNote("Could not reach the server.");
    } finally {
      setRetrying(null);
    }
  }

  async function selectFinalist(index: number) {
    if (finaleBusy) return;
    setFinaleBusy(true);
    setFinaleError(null);
    try {
      const response = await fetch("/api/moderator/finale", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ index }),
      });
      if (!response.ok) {
        const payload = (await response.json().catch(() => null)) as { error?: string } | null;
        setFinaleError(payload?.error ?? "Could not change the finale spotlight.");
        return;
      }
      await refresh();
    } catch {
      setFinaleError("Could not reach the server.");
    } finally {
      setFinaleBusy(false);
    }
  }

  async function startPitchClock() {
    if (finaleBusy) return;
    setFinaleBusy(true);
    setFinaleError(null);
    try {
      const response = await fetch("/api/moderator/finale/start", { method: "POST" });
      if (!response.ok) {
        const payload = (await response.json().catch(() => null)) as { error?: string } | null;
        setFinaleError(payload?.error ?? "Could not start the pitch clock.");
        return;
      }
      await refresh();
    } catch {
      setFinaleError("Could not reach the server.");
    } finally {
      setFinaleBusy(false);
    }
  }

  async function reset() {
    setResetting(true);
    try {
      const response = await fetch("/api/moderator/reset", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirm: "RESET" }),
      });
      if (response.ok) {
        setResetOpen(false);
        setPastRunsVersion((value) => value + 1);
        await refresh();
      }
    } finally {
      setResetting(false);
    }
  }

  async function logout() {
    await fetch("/api/moderator/logout", { method: "POST" });
    await refresh();
  }

  return (
    <main className="stage relative min-h-dvh">
      <header className="sticky top-0 z-40 border-b border-line/60 bg-ink ">
        <div className="mx-auto flex w-full max-w-[1600px] flex-wrap items-center justify-between gap-4 px-5 py-3 md:px-8">
          <div className="flex items-center gap-5">
            <Wordmark compact />
            <span className="hidden h-6 w-px bg-line sm:block" />
            <span
              className={`hidden items-center gap-2 text-label font-medium sm:inline-flex ${
                data.mode.demo ? "text-gold" : "text-mint"
              }`}
            >
              <span className="h-1.5 w-1.5 rounded-full bg-current" />
              {data.mode.demo ? "Demo jury" : "Live model"}
            </span>
          </div>

          <div className="flex items-center gap-4">
            <div className="hidden text-right sm:block">
              <p className="label">{data.session.label}</p>
              <Countdown
                endsAt={data.session.phaseEndsAt}
                serverNow={data.session.serverNow}
                className="mt-0.5 text-subhead"
              />
            </div>
            {previous ? (
              <button
                className="btn btn-ghost px-4 py-3"
                onClick={() => setBackOpen(true)}
                disabled={advancing || goingBack}
              >
                <span aria-hidden="true">←</span>
                Back to {previous.label}
              </button>
            ) : null}
            <button
              className="btn btn-gold px-5 py-3"
              onClick={() => void advance()}
              disabled={advancing || !next}
            >
              {advancing
                ? "Advancing..."
                : next
                  ? `Advance to ${next.label}`
                  : "Final phase"}
              {next && !advancing ? <span aria-hidden="true">→</span> : null}
            </button>
            <button className="btn btn-ghost px-4 py-3" onClick={() => void logout()}>
              Log out
            </button>
          </div>
        </div>
      </header>

      <div className="relative z-10 mx-auto w-full max-w-[1600px] px-5 py-7 md:px-8">
        {pollError ? (
          <div className="mb-4">
            <InlineNotice tone="error">{pollError}</InlineNotice>
          </div>
        ) : null}
        {advanceError ? (
          <div className="mb-4">
            <InlineNotice tone="error">{advanceError}</InlineNotice>
          </div>
        ) : null}
        {retryNote ? (
          <div className="mb-4">
            <InlineNotice tone="info">{retryNote}</InlineNotice>
          </div>
        ) : null}
        {phase === "jury" && data.session.autoAdvanceAt !== null ? (
          <div
            className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded border border-gold/40 bg-gold/[0.07] px-4 py-3"
            data-testid="auto-advance"
          >
            <div>
              <AutoAdvanceNote
                endsAt={data.session.autoAdvanceAt}
                serverNow={data.session.serverNow}
                className="text-body font-semibold text-cream"
              />
              <p className="text-label text-muted">
                Every jury answer is in. The session moves on by itself unless you hold it.
              </p>
            </div>
            <div className="flex gap-3">
              <button
                className="btn btn-ghost px-4 py-2"
                onClick={() => void holdAutoAdvance()}
                disabled={holding || advancing}
              >
                {holding ? "Holding..." : "Hold"}
              </button>
              <button
                className="btn btn-gold px-4 py-2"
                onClick={() => void advance()}
                disabled={advancing}
              >
                Start now
              </button>
            </div>
          </div>
        ) : null}
        {phase === "jury" && data.session.autoAdvanceHeld ? (
          <div className="mb-4">
            <InlineNotice tone="info">
              The automatic advance is off for this visit to the jury round. Advance to the
              defense yourself when you are ready.
            </InlineNotice>
          </div>
        ) : null}

        <PhaseRail phase={phase} />

        <div className="mt-6 grid gap-4 lg:grid-cols-[1.35fr_0.65fr] lg:items-start">
          <div className="panel rounded p-5 md:p-6">
            <p className="label">Now running</p>
            <h1 className="display mt-1 text-heading md:text-title">{data.session.label}</h1>
            <p className="mt-1 max-w-xl text-small text-muted">{data.session.tagline}</p>

            <div className="mt-6 grid grid-cols-2 gap-4 sm:grid-cols-5">
              <MiniStat label="Teams joined" value={`${data.teams.length} / ${data.maxTeams}`} />
              <MiniStat
                label="Pitches submitted"
                value={data.teams.filter((team) => team.submittedAt !== null).length}
              />
              <MiniStat
                label="Evaluated"
                value={data.teams.filter((team) => team.eligible === true).length}
              />
              <MiniStat
                label="Jury verdicts"
                value={data.teams.filter((team) => team.verdictCompletedAt !== null).length}
              />
              <MiniStat
                label="Failed steps"
                value={failedTeams}
                accent={failedTeams > 0 ? "var(--color-rose)" : undefined}
              />
            </div>

            {phase === "voting" ? (
              <div className="mt-6 rounded border border-gold/25 bg-gold/[0.04] p-4">
                <div className="flex flex-wrap items-end justify-between gap-4">
                  <div className="min-w-0">
                    <p className="label label-gold">Audience vote open</p>
                    <p className="stat-value mt-1 text-title text-cream">
                      {data.votesCast}
                    </p>
                    <p className="mt-0.5 text-label text-muted">votes cast</p>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <CopyButton value={voteUrl} label="Copy vote link" />
                    <a
                      className="btn btn-ghost px-4 py-2.5"
                      href="/vote"
                      target="_blank"
                      rel="noreferrer"
                    >
                      Vote page ↗
                    </a>
                  </div>
                </div>
                <p className="mt-2 break-all text-label text-muted">{voteUrl}</p>
              </div>
            ) : null}

            {phase === "winner" ? (
              <p className="mt-5 text-label text-muted">
                Voting closed. {data.votesCast}{" "}
                {data.votesCast === 1 ? "vote was" : "votes were"} cast.
              </p>
            ) : null}

            {phase === "finale" && data.finale.finalists.length > 0 ? (
              <div className="mt-6 rounded border border-gold/25 bg-gold/[0.04] p-4">
                <div className="flex flex-wrap items-center justify-between gap-4">
                  <div className="min-w-0">
                    <p className="label label-gold">Finale spotlight</p>
                    <p className="mt-1 truncate text-small font-semibold text-cream">
                      {spotlight?.startupName}
                      {spotlight && spotlight.teamName !== spotlight.startupName ? (
                        <span className="ml-2 font-normal text-muted">{spotlight.teamName}</span>
                      ) : null}
                    </p>
                    <p className="mt-0.5 text-label text-muted">
                      Pitch {data.finale.selectedIndex + 1} of {data.finale.finalists.length} on
                      the projector
                    </p>
                  </div>
                  <PitchClock
                    endsAt={data.session.pitchEndsAt}
                    serverNow={data.session.serverNow}
                    sizeClass="text-heading"
                    notStarted={data.session.pitchNotStarted}
                  />
                  <div className="flex flex-wrap gap-2">
                    {data.session.pitchNotStarted ? (
                      <button
                        className="btn btn-gold px-4 py-2.5"
                        onClick={() => void startPitchClock()}
                        disabled={finaleBusy}
                      >
                        Start pitch clock
                      </button>
                    ) : null}
                    <button
                      className="btn btn-ghost px-4 py-2.5"
                      onClick={() =>
                        void selectFinalist(
                          stepFinalistIndex(
                            data.finale.selectedIndex,
                            -1,
                            data.finale.finalists.length,
                          ),
                        )
                      }
                      disabled={finaleBusy || data.finale.selectedIndex <= 0}
                    >
                      ← Previous finalist
                    </button>
                    <button
                      className="btn btn-gold px-4 py-2.5"
                      onClick={() =>
                        void selectFinalist(
                          stepFinalistIndex(
                            data.finale.selectedIndex,
                            1,
                            data.finale.finalists.length,
                          ),
                        )
                      }
                      disabled={
                        finaleBusy ||
                        data.finale.selectedIndex >= data.finale.finalists.length - 1
                      }
                    >
                      Next finalist →
                    </button>
                  </div>
                </div>
                {finaleError ? (
                  <p className="mt-2 text-label text-rose">{finaleError}</p>
                ) : null}
              </div>
            ) : null}

            <div className="mt-6 flex flex-wrap items-center gap-2 border-t border-line/60 pt-4">
              <span className="label mr-1">Open</span>
              <a
                className="btn btn-ghost px-4 py-2"
                href="/screen"
                target="_blank"
                rel="noreferrer"
              >
                Projector view ↗
              </a>
              <a
                className="btn btn-ghost px-4 py-2"
                href="/team"
                target="_blank"
                rel="noreferrer"
              >
                Team view ↗
              </a>
              <CopyButton value={joinUrl} label="Copy join link" />
              <CopyButton value={screenUrl} label="Copy projector link" />
              <CopyButton value={voteUrl} label="Copy vote link" />
              <span className="w-full truncate text-label text-muted">{joinUrl}</span>
            </div>
          </div>

          <div className="panel p-5 md:p-6">
            <p className="label">Jury engine</p>
            <p className="mt-2 text-subhead font-semibold text-cream">
              {data.mode.demo ? "Demo jury" : data.mode.model}
            </p>
            <p className="mt-1 text-label leading-relaxed text-muted">
              {data.mode.demo
                ? "Prepared, persona-consistent responses. No network calls, no key, no cost."
                : `OpenAI-compatible endpoint${data.mode.baseUrl ? `: ${data.mode.baseUrl}` : " (OpenAI)"}.`}
            </p>
            {!data.mode.demo && data.mode.baseUrl ? (
              <p className="mt-2 break-all text-label text-muted">{data.mode.baseUrl}</p>
            ) : null}
            {data.mode.demo ? (
              <p className="mt-3 text-label leading-relaxed text-gold">
                Set OPENAI_API_KEY and DEMO_MODE=false to use the real jury.
              </p>
            ) : null}
            <div className="mt-5 border-t border-line/60 pt-5">
              <p className="label">Export this run</p>
              <p className="mt-2 text-label leading-relaxed text-muted">
                Download the complete run: every pitch, the four answers, the one change, all jury
                scores and decisions, and the audience votes. The file is stored by you, not on the
                server.
              </p>
              <div className="mt-3 flex flex-wrap gap-2">
                <a
                  className="btn btn-ghost px-4 py-2.5"
                  href="/api/moderator/export?format=json"
                  download
                >
                  Download JSON
                </a>
                <a
                  className="btn btn-ghost px-4 py-2.5"
                  href="/api/moderator/export?format=csv"
                  download
                >
                  Download CSV
                </a>
              </div>
            </div>
            <div className="mt-5 border-t border-line/60 pt-5">
              <button className="btn btn-quiet-danger w-full px-4 py-2.5" onClick={() => setResetOpen(true)}>
                Reset session
              </button>
              <p className="mt-2 text-label leading-relaxed text-muted">
                Saves the run under Past runs, then clears all teams and returns to the lobby. Use
                it only for rehearsals or between sessions.
              </p>
            </div>
          </div>
        </div>

        <div className="mt-8 flex flex-wrap items-end justify-between gap-3">
          <div>
            <p className="label">Teams</p>
            <h2 className="display mt-1 text-heading">Every team at a glance</h2>
          </div>
          <p className="label">
            {data.teams.length === 0
              ? "Waiting for the first team to join."
              : `${data.teams.length} team${data.teams.length === 1 ? "" : "s"} in the session`}
          </p>
        </div>

        {data.teams.length === 0 ? (
          <div className="panel mt-4 p-10 text-center">
            <p className="display text-heading text-muted">The room is empty.</p>
            <p className="mt-2 text-small text-muted">
              Show the projector view so teams can scan the QR code.
            </p>
          </div>
        ) : (
          <div className="mt-5">
            <div className="team-grid team-ledger-head label">
              <span>Team</span>
              <span>Pitch</span>
              <span>Round 1</span>
              <span>Defense</span>
              <span>Verdict</span>
              <span />
            </div>
            {sortedTeams.map((team) => (
              <TeamCard
                key={team.id}
                team={team}
                phase={phase}
                retrying={retrying}
                onRetry={retry}
                investors={data.investors}
                threshold={data.session.investmentThreshold}
                scoreMax={data.session.scoreMax}
              />
            ))}
          </div>
        )}

        <PastRuns
          refreshKey={pastRunsVersion}
          investors={data.investors}
          threshold={data.session.investmentThreshold}
          scoreMax={data.session.scoreMax}
        />
      </div>

      <Modal
        open={backOpen}
        title={previous ? `Go back to ${previous.label}?` : "Go back?"}
        onClose={() => setBackOpen(false)}
      >
        <p className="text-small leading-relaxed text-muted">
          Use this to undo an accidental advance. The session returns to{" "}
          {previous?.label ?? "the previous phase"} and every team, pitch, jury result and vote
          stays exactly as it is. Jury calls that already finished are not repeated when you
          advance again. The phase timer starts over with the full time of that phase, and the
          projector, team phones and vote page switch back immediately.
        </p>
        <div className="mt-5 flex gap-3">
          <button className="btn btn-ghost flex-1 px-4 py-3" onClick={() => setBackOpen(false)}>
            Cancel
          </button>
          <button
            className="btn btn-gold flex-1 px-4 py-3"
            onClick={() => void goBack()}
            disabled={goingBack}
          >
            {goingBack ? "Going back..." : `Back to ${previous?.label ?? "previous phase"}`}
          </button>
        </div>
      </Modal>

      <Modal open={resetOpen} title="Reset the session?" onClose={() => setResetOpen(false)}>
        <p className="text-small leading-relaxed text-muted">
          This saves the current run under Past runs, then clears every team, every pitch, and
          every jury result and returns the room to the lobby. An empty session is cleared without
          an archive entry. Only do this between sessions or during a rehearsal.
        </p>
        <div className="mt-5 flex gap-3">
          <button className="btn btn-ghost flex-1 px-4 py-3" onClick={() => setResetOpen(false)}>
            Cancel
          </button>
          <button
            className="btn btn-danger flex-1 px-4 py-3"
            onClick={() => void reset()}
            disabled={resetting}
          >
            {resetting ? "Resetting..." : "Reset everything"}
          </button>
        </div>
      </Modal>
    </main>
  );
}

function archiveWinnerLabel(winner: string | null, phase: PhaseId): string {
  if (winner) return winner;
  if (phase === "winner") return "No winner recorded";
  const phaseLabel = PHASES.find((entry) => entry.id === phase)?.label ?? phase;
  return `No winner (stopped in ${phaseLabel})`;
}

function PastRuns({
  refreshKey,
  investors,
  threshold,
  scoreMax,
}: {
  refreshKey: number;
  investors: PublicInvestor[];
  threshold: number;
  scoreMax: number;
}) {
  const [archives, setArchives] = useState<ArchiveSummary[] | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [openRun, setOpenRun] = useState<ArchivedRunView | null>(null);
  const [loadingRunId, setLoadingRunId] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ArchiveSummary | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const response = await fetch("/api/moderator/archives", { cache: "no-store" });
      if (!response.ok) {
        setListError("Could not load past runs.");
        return;
      }
      const payload = (await response.json()) as { archives: ArchiveSummary[] };
      setArchives(payload.archives);
      setListError(null);
    } catch {
      setListError("Could not reach the server.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  async function viewRun(id: string) {
    setLoadingRunId(id);
    setActionError(null);
    try {
      const response = await fetch(`/api/moderator/archives/${id}`, { cache: "no-store" });
      const payload = (await response.json().catch(() => null)) as
        | ArchivedRunView
        | { error?: string }
        | null;
      if (!response.ok) {
        setActionError((payload as { error?: string } | null)?.error ?? "Could not load that run.");
        return;
      }
      setOpenRun(payload as ArchivedRunView);
    } catch {
      setActionError("Could not reach the server.");
    } finally {
      setLoadingRunId(null);
    }
  }

  async function deleteRun() {
    if (!deleteTarget || deleting) return;
    setDeleting(true);
    setActionError(null);
    try {
      const response = await fetch(`/api/moderator/archives/${deleteTarget.id}`, {
        method: "DELETE",
      });
      if (!response.ok) {
        const payload = (await response.json().catch(() => null)) as { error?: string } | null;
        setActionError(payload?.error ?? "Could not delete that run.");
        return;
      }
      setDeleteTarget(null);
      await load();
    } catch {
      setActionError("Could not reach the server.");
    } finally {
      setDeleting(false);
    }
  }

  return (
    <section className="panel mt-6 p-5 md:p-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="label">Past runs</p>
          <h2 className="display mt-1 text-heading">Archived sessions</h2>
        </div>
        <p className="label">Saved automatically when you reset a session</p>
      </div>

      {listError ? (
        <div className="mt-4">
          <InlineNotice tone="error">{listError}</InlineNotice>
        </div>
      ) : null}
      {actionError ? (
        <div className="mt-4">
          <InlineNotice tone="error">{actionError}</InlineNotice>
        </div>
      ) : null}

      {archives === null ? (
        <p className="mt-4 text-label text-muted">Loading past runs...</p>
      ) : archives.length === 0 ? (
        <p className="mt-4 text-small text-muted">
          No past runs yet. The first reset of a session with teams creates one here.
        </p>
      ) : (
        <div className="mt-4 divide-y divide-line/60 border-y border-line/60">
          {archives.map((archive) => (
            <div
              key={archive.id}
              className="flex flex-wrap items-center justify-between gap-3 py-3"
            >
              <div className="min-w-0">
                <p className="truncate text-small font-semibold text-cream">
                  {archiveWinnerLabel(archive.winner, archive.phase)}
                </p>
                <p className="mt-0.5 text-label text-muted">
                  {new Date(archive.archivedAt).toLocaleString()} · {archive.teamCount}{" "}
                  {archive.teamCount === 1 ? "team" : "teams"} · {archive.votesCast}{" "}
                  {archive.votesCast === 1 ? "vote" : "votes"}
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <a
                  className="btn btn-ghost px-3 py-2"
                  href={`/api/moderator/archives/${archive.id}/export?format=json`}
                  download
                >
                  JSON
                </a>
                <a
                  className="btn btn-ghost px-3 py-2"
                  href={`/api/moderator/archives/${archive.id}/export?format=csv`}
                  download
                >
                  CSV
                </a>
                <button
                  className="btn btn-ghost px-3 py-2"
                  onClick={() => void viewRun(archive.id)}
                  disabled={loadingRunId === archive.id}
                >
                  {loadingRunId === archive.id ? "Opening..." : "View"}
                </button>
                <button
                  className="btn btn-quiet-danger px-3 py-2"
                  onClick={() => setDeleteTarget(archive)}
                >
                  Delete
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      <Modal
        open={openRun !== null}
        title={openRun ? `Past run: ${new Date(openRun.archivedAt).toLocaleString()}` : "Past run"}
        onClose={() => setOpenRun(null)}
        wide
      >
        {openRun ? (
          <>
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div className="min-w-0">
                <p className="label">Winner</p>
                <p className="mt-1 text-subhead font-semibold text-gold">
                  {openRun.winner?.startupName ?? archiveWinnerLabel(null, openRun.session.phase)}
                </p>
                {openRun.winner ? (
                  <p className="mt-0.5 text-label text-muted">
                    {openRun.winner.teamName} · {openRun.winner.audienceVotes} audience votes
                  </p>
                ) : null}
                <p className="mt-2 text-label text-muted">
                  {openRun.teamCount} {openRun.teamCount === 1 ? "team" : "teams"} ·{" "}
                  {openRun.votesCast} {openRun.votesCast === 1 ? "vote" : "votes"} · ended in{" "}
                  {openRun.session.label}
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                <a
                  className="btn btn-ghost px-4 py-2"
                  href={`/api/moderator/archives/${openRun.id}/export?format=json`}
                  download
                >
                  Download JSON
                </a>
                <a
                  className="btn btn-ghost px-4 py-2"
                  href={`/api/moderator/archives/${openRun.id}/export?format=csv`}
                  download
                >
                  Download CSV
                </a>
              </div>
            </div>

            <div className="mt-5">
              <p className="label">Final ranking</p>
              <div className="mt-2 divide-y divide-line/60 border-y border-line/60">
                {openRun.ranking.length === 0 ? (
                  <p className="py-2 text-small text-muted">No team completed a final verdict.</p>
                ) : (
                  openRun.ranking.map((entry) => (
                    <div
                      key={entry.teamId}
                      className="flex flex-wrap items-center justify-between gap-3 py-2"
                    >
                      <div className="flex min-w-0 items-baseline gap-3">
                        <span className="text-small font-semibold tabular-nums text-gold">
                          #{entry.rank}
                        </span>
                        <span className="truncate text-small font-semibold text-cream">
                          {entry.startupName}
                        </span>
                        <span className="truncate text-label text-muted">{entry.teamName}</span>
                      </div>
                      <p className="text-label text-muted">
                        {entry.inCount} of {INVESTOR_IDS.length} lions invest · {entry.total} points
                        · {entry.audienceVotes} votes
                      </p>
                    </div>
                  ))
                )}
              </div>
            </div>

            <div className="mt-5">
              <p className="label">Team results</p>
              <div className="mt-2 space-y-2">
                {openRun.teams.map((team) => (
                  <ArchivedTeam
                    key={team.id}
                    team={team}
                    investors={investors}
                    threshold={threshold}
                    scoreMax={scoreMax}
                  />
                ))}
              </div>
            </div>
          </>
        ) : null}
      </Modal>

      <Modal
        open={deleteTarget !== null}
        title="Delete this past run?"
        onClose={() => setDeleteTarget(null)}
      >
        <p className="text-small leading-relaxed text-muted">
          {deleteTarget
            ? `${deleteTarget.winner ?? "This run"} from ${new Date(
                deleteTarget.archivedAt,
              ).toLocaleString()} will be removed from the archive.`
            : ""}{" "}
          Download it first if you want to keep a copy. This cannot be undone.
        </p>
        <div className="mt-5 flex gap-3">
          <button className="btn btn-ghost flex-1 px-4 py-3" onClick={() => setDeleteTarget(null)}>
            Cancel
          </button>
          <button
            className="btn btn-danger flex-1 px-4 py-3"
            onClick={() => void deleteRun()}
            disabled={deleting}
          >
            {deleting ? "Deleting..." : "Delete run"}
          </button>
        </div>
      </Modal>
    </section>
  );
}

function ArchivedTeam({
  team,
  investors,
  threshold,
  scoreMax,
}: {
  team: ArchivedTeamView;
  investors: PublicInvestor[];
  threshold: number;
  scoreMax: number;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded border border-line/60">
      <button
        className="flex w-full flex-wrap items-center justify-between gap-3 px-4 py-3 text-left"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <div className="min-w-0">
          <p className="truncate text-small font-semibold text-cream">{team.name}</p>
          <p className="mt-0.5 text-label text-muted">
            {team.pitch.startupName}
            {team.rank !== null ? ` · #${team.rank}` : ""} · {team.audienceVotes} audience{" "}
            {team.audienceVotes === 1 ? "vote" : "votes"}
          </p>
        </div>
        <span className="text-label text-gold">{open ? "Hide details" : "View details"}</span>
      </button>
      {open ? (
        <TeamDetails
          team={team}
          canRetryVerdict={false}
          retrying={null}
          onRetry={async () => {}}
          round1Failed={false}
          investors={investors}
          threshold={threshold}
          scoreMax={scoreMax}
        />
      ) : null}
    </div>
  );
}

function MiniStat({
  label,
  value,
  accent = "var(--color-cream)",
}: {
  label: string;
  value: number | string;
  accent?: string;
}) {
  return (
    <div className="border-t border-line/70 pt-3">
      <p className="stat-value text-heading" style={{ color: accent }}>
        {value}
      </p>
      <p className="label mt-1">{label}</p>
    </div>
  );
}

function PhaseRail({ phase }: { phase: PhaseId }) {
  const current = PHASES.findIndex((entry) => entry.id === phase);
  return (
    <div className="phase-rail">
      {PHASES.map((entry, index) => (
        <div
          key={entry.id}
          aria-current={index === current ? "step" : undefined}
          className={`phase-step ${index === current ? "is-current" : index < current ? "is-past" : ""}`}
        >
          <span className="font-semibold tabular-nums">{String(index + 1).padStart(2, "0")}</span>
          <span className="hidden truncate sm:inline">{entry.label}</span>
        </div>
      ))}
    </div>
  );
}

function TeamCard({
  team,
  phase,
  retrying,
  onRetry,
  investors,
  threshold,
  scoreMax,
}: {
  team: ModeratorTeamView;
  phase: PhaseId;
  retrying: string | null;
  onRetry: (teamId: string, step: "round1" | "verdict") => Promise<void>;
  investors: PublicInvestor[];
  threshold: number;
  scoreMax: number;
}) {
  const [open, setOpen] = useState(false);
  const round1Done = INVESTOR_IDS.filter((id) => team.round1[id].status === "done").length;
  const round1Failed = INVESTOR_IDS.some((id) => team.round1[id].status === "failed");
  const verdictDone = INVESTOR_IDS.filter((id) => team.verdict[id].status === "done").length;
  const verdictFailed = INVESTOR_IDS.some((id) => team.verdict[id].status === "failed");
  const verdictRunning = INVESTOR_IDS.some((id) => team.verdict[id].status === "running");
  const canRetryVerdict =
    team.eligible === true &&
    isAtLeast(phase, "verdict") &&
    round1Done === 4 &&
    !verdictRunning &&
    verdictDone < 4;

  return (
    <div className="team-row">
      <div className="team-grid team-row-main">
          <div className="team-cell-name min-w-0">
            <div className="flex items-baseline gap-2">
              <h3 className="truncate text-small font-semibold text-cream" title={team.name}>{team.name}</h3>
              {team.rank !== null ? (
                <span className="text-small font-semibold tabular-nums text-gold">#{team.rank}</span>
              ) : null}
            </div>
            <p className="mt-0.5 text-label text-muted">
              Joined {new Date(team.createdAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
              {team.submittedAt
                ? ` · submitted ${new Date(team.submittedAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`
                : " · pitch not submitted"}
            </p>
          </div>
          <StepBadge
            label="Pitch"
            state={
              team.submittedAt
                ? "done"
                : team.pitchComplete
                  ? "draft"
                  : team.pitch.startupName || team.pitch.tagline
                    ? "draft"
                    : "idle"
            }
            detail={team.submittedAt ? "Submitted" : team.pitchComplete ? "Complete draft" : "Incomplete"}
          />
          <StepBadge
            label="Round 1"
            state={round1Failed ? "error" : round1Done === 4 ? "done" : round1Done > 0 ? "active" : "idle"}
            detail={round1Failed ? "Needs retry" : `${round1Done}/4`}
          />
          <StepBadge
            label="Defense"
            state={team.defense.submittedAt ? "done" : team.eligible === false ? "idle" : "idle"}
            detail={team.defense.submittedAt ? "Submitted" : team.eligible === false ? "Not evaluated" : "Not yet"}
          />
          <StepBadge
            label="Verdict"
            state={verdictFailed ? "error" : verdictDone === 4 ? "done" : verdictDone > 0 ? "active" : "idle"}
            detail={verdictFailed ? "Needs retry" : `${verdictDone}/4`}
          />
          <div className="team-cell-actions flex items-center justify-end gap-3">
            {team.inCount !== null && team.total !== null ? (
              <TeamResult
                compact
                inCount={team.inCount}
                total={team.total}
                investorCount={INVESTOR_IDS.length}
                scoreMax={scoreMax}
              />
            ) : null}
            <button
              className="btn btn-ghost px-3 py-2"
              aria-expanded={open}
              onClick={() => setOpen((value) => !value)}
            >
              {open ? "Hide details" : "View details"}
            </button>
          </div>
      </div>

        {team.errorCount > 0 ? (
          <div className="flex flex-wrap gap-2 pb-3">
            {round1Failed ? (
              <button
                className="btn btn-danger px-4 py-2"
                onClick={() => void onRetry(team.id, "round1")}
                disabled={retrying === `${team.id}:round1`}
              >
                {retrying === `${team.id}:round1` ? "Starting..." : "Retry jury round 1"}
              </button>
            ) : null}
            {canRetryVerdict ? (
              <button
                className="btn btn-danger px-4 py-2"
                onClick={() => void onRetry(team.id, "verdict")}
                disabled={retrying === `${team.id}:verdict`}
              >
                {retrying === `${team.id}:verdict` ? "Starting..." : "Retry final verdict"}
              </button>
            ) : null}
          </div>
        ) : null}

        {team.eligible === false ? (
          <p className="pb-3 text-label text-rose">
            Not evaluated: pitch was incomplete or unsubmitted when the jury started.
          </p>
        ) : null}

      {open ? (
        <TeamDetails
          team={team}
          canRetryVerdict={canRetryVerdict}
          retrying={retrying}
          onRetry={onRetry}
          round1Failed={round1Failed}
          investors={investors}
          threshold={threshold}
          scoreMax={scoreMax}
        />
      ) : null}
    </div>
  );
}

function StepBadge({
  label,
  state,
  detail,
}: {
  label: string;
  state: "idle" | "draft" | "active" | "done" | "error";
  detail: string;
}) {
  const colors = {
    idle: "var(--color-muted)",
    draft: "var(--color-muted)",
    active: "var(--color-gold)",
    done: "var(--color-mint)",
    error: "var(--color-rose)",
  } as const;
  return (
    <div className="min-w-0">
      <p className="text-label font-medium text-muted lg:sr-only">{label}</p>
      <p className="flex items-center gap-1.5 text-label font-medium" style={{ color: colors[state] }}>
        <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-current" />
        {detail}
      </p>
    </div>
  );
}

function TeamDetails({
  team,
  canRetryVerdict,
  retrying,
  onRetry,
  round1Failed,
  investors,
  threshold,
  scoreMax,
}: {
  team: ModeratorTeamView;
  canRetryVerdict: boolean;
  retrying: string | null;
  onRetry: (teamId: string, step: "round1" | "verdict") => Promise<void>;
  round1Failed: boolean;
  investors: PublicInvestor[];
  threshold: number;
  scoreMax: number;
}) {
  return (
    <div className="border-t border-line/60 bg-black/20 px-5 py-5 mb-3">
      <div>
        <p className="label">Pitch</p>
        <dl className="mt-3 space-y-2.5 text-small">
          {[
            ["Startup", team.pitch.startupName],
            ["Tagline", team.pitch.tagline],
            ["Problem", team.pitch.problem],
            ["Solution", team.pitch.solution],
            ["Customers", team.pitch.customers],
            ["Business model", team.pitch.businessModel],
          ].map(([label, value]) => (
            <div key={label} className="grid grid-cols-[7rem_1fr] gap-3">
              <dt className="text-label font-semibold text-muted">
                {label}
              </dt>
              <dd className={value ? "whitespace-pre-wrap text-cream/85" : "text-muted"}>
                {value || "-"}
              </dd>
            </div>
          ))}
        </dl>
      </div>

      <div className="mt-6">
        <p className="label">Jury round 1</p>
        <div className="mt-3 grid gap-2">
          {INVESTOR_IDS.map((id) => {
            const entry = team.round1[id];
            const investor = investors.find((candidate) => candidate.id === id)!;
            return (
              <div key={id} className="rounded border border-line/60 bg-panel-2/40 p-3">
                <div className="flex items-center justify-between gap-3">
                  <div className="flex items-center gap-2">
                    <InvestorAvatar investor={investor} size={48} />
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                        <span className="text-label font-semibold text-cream">{investor.name}</span>
                        <FocusTag investor={investor} />
                      </div>
                      <p className="mt-1 text-label text-muted">{investor.title}</p>
                    </div>
                  </div>
                  {entry.status !== "done" ? (
                    <span className="text-label font-semibold text-gold">{entry.status}</span>
                  ) : null}
                </div>
                {entry.status === "done" && entry.score != null ? (
                  <ScoreReading
                    score={entry.score}
                    threshold={threshold}
                    scoreMax={scoreMax}
                    provisional
                    className="mt-2"
                  />
                ) : null}
                {entry.status === "done" ? (
                  <>
                    <p className="mt-2 text-label leading-relaxed text-muted">{entry.assessment}</p>
                    <p className="mt-1.5 text-label leading-relaxed text-cream/80">
                      <span className="text-brass">Q: </span>
                      {entry.question}
                    </p>
                  </>
                ) : entry.error ? (
                  <p className="mt-2 break-words text-label text-rose">{entry.error}</p>
                ) : null}
              </div>
            );
          })}
        </div>
        {round1Failed ? (
          <button
            className="btn btn-danger mt-3 px-4 py-2"
            onClick={() => void onRetry(team.id, "round1")}
            disabled={retrying === `${team.id}:round1`}
          >
            Retry failed round 1 calls
          </button>
        ) : null}
      </div>

      <div className="mt-6">
        <p className="label">Defense answers</p>
        <div className="mt-3 rounded border border-gold/25 bg-gold/[0.05] p-3">
          <p className="label label-gold">Our one change</p>
          <p className="mt-1.5 whitespace-pre-wrap text-label leading-relaxed text-cream/90">
            {team.defense.oneChange || "No one change submitted."}
          </p>
        </div>
        <div className="mt-2 space-y-2">
          {INVESTOR_IDS.map((id, index) => {
            const question = team.round1[id].question;
            const answer = team.defense.answers[index];
            const investor = investors.find((candidate) => candidate.id === id)!;
            return (
              <div key={id} className="rounded border border-line/60 bg-panel-2/40 p-3">
                <div className="flex items-center gap-3">
                  <InvestorAvatar investor={investor} size={48} />
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                      <p className="text-small font-semibold text-muted">{investor.name}</p>
                      <FocusTag investor={investor} />
                    </div>
                    <p className="mt-1 text-label text-muted">{investor.title}</p>
                  </div>
                </div>
                <p className="mt-1 text-label text-cream/75">{question ?? "No question yet."}</p>
                <p className="mt-2 text-label leading-relaxed text-cream/90">
                  {answer || <span className="text-muted">No answer submitted.</span>}
                </p>
              </div>
            );
          })}
        </div>
      </div>

      <div className="mt-6">
        <p className="label">Final verdict</p>
        <div className="mt-3 space-y-2">
          {INVESTOR_IDS.map((id) => {
            const entry = team.verdict[id];
            const investor = investors.find((candidate) => candidate.id === id)!;
            return (
              <div key={id} className="rounded border border-line/60 bg-panel-2/40 p-3">
                <div className="flex items-center justify-between gap-3">
                  <div className="flex items-center gap-2">
                    <InvestorAvatar investor={investor} size={48} />
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                        <span className="text-label font-semibold text-cream">{investor.name}</span>
                        <FocusTag investor={investor} />
                      </div>
                      <p className="mt-1 text-label text-muted">{investor.title}</p>
                    </div>
                  </div>
                  <span className="flex items-center gap-2 text-label text-muted">
                    {entry.decision ? (
                      <span
                        className="font-semibold"
                        style={{ color: entry.decision === "in" ? "var(--color-mint)" : "var(--color-rose)" }}
                      >
                        {entry.decision === "in" ? "I'm in" : "I'm out"}
                      </span>
                    ) : null}
                    {entry.score != null ? (
                      <span className="font-semibold text-gold">
                        {entry.previousScore != null
                          ? `${entry.previousScore} → ${entry.score}/${scoreMax}`
                          : `${entry.score}/${scoreMax}`}
                      </span>
                    ) : null}
                    {entry.status !== "done" ? <span>{entry.status}</span> : null}
                  </span>
                </div>
                {entry.score != null ? (
                  <ScoreReading
                    score={entry.score}
                    threshold={threshold}
                    scoreMax={scoreMax}
                    className="mt-2"
                  />
                ) : null}
                {entry.statement ? (
                  <p className="mt-2 text-label leading-relaxed text-cream/80">{entry.statement}</p>
                ) : entry.error ? (
                  <p className="mt-2 break-words text-label text-rose">{entry.error}</p>
                ) : null}
              </div>
            );
          })}
        </div>
        {canRetryVerdict ? (
          <button
            className="btn btn-danger mt-3 px-4 py-2"
            onClick={() => void onRetry(team.id, "verdict")}
            disabled={retrying === `${team.id}:verdict`}
          >
            Retry final verdict for this team
          </button>
        ) : null}
      </div>
    </div>
  );
}
