"use client";

import { useEffect } from "react";
import { PITCH_FIELDS } from "@/lib/pitch";
import { PHASES } from "@/lib/session-state";
import type { ScreenStateView, SessionView } from "@/lib/views";
import { WINNER_RULE, winnerExplanation } from "@/lib/winner";
import { FocusTag, InvestorAvatar, Wordmark } from "./brand";
import { AutoAdvanceNote, Countdown } from "./Countdown";
import { FinaleCallout, ScoringRules, Splash, StageCue } from "./ui";
import { usePoll } from "./usePoll";

const lionsInvest = (count: number, investorCount: number) =>
  `${count} of ${investorCount} lions invest`;
const LONG_FINALE_NAME = 26;

export function ScreenApp() {
  const { data, loading, error } = usePoll<ScreenStateView>("/api/screen/state", 1200);
  useEffect(() => { document.title = "Projector | The AI Lion's Den"; }, []);
  if (loading && !data) return <Splash label="Opening the den" />;
  if (!data) return (
    <main className="stage grid min-h-dvh place-items-center">
      <div className="text-center"><h1 className="display text-page">Projector offline</h1>
        <p className="mt-4 text-subhead text-muted">{error ?? "Waiting for the session."}</p></div>
    </main>
  );
  const phase = data.session.phase;
  return (
    <main className="stage projector">
      <StageCue cue={phase} />
      <ScreenHeader session={data.session} />
      {/* The key limits the entrance to real phase changes; polling keeps it stable. */}
      <div key={phase} className="phase-enter flex min-h-0 flex-1 flex-col">
        {phase === "lobby" ? <LobbyStage data={data} /> : null}
        {phase === "ideation" || phase === "defense" ? <BuildStage data={data} /> : null}
        {phase === "jury" || phase === "verdict" ? <JuryStage data={data} /> : null}
        {phase === "leaderboard" ? <LeaderboardStage data={data} /> : null}
        {phase === "finale" ? <FinaleStage data={data} /> : null}
        {phase === "voting" ? <VotingStage data={data} /> : null}
        {phase === "winner" ? <WinnerStage data={data} /> : null}
      </div>
    </main>
  );
}

/**
 * The header's top-right spot. Ideation and defense show their own large clock instead, and
 * the finale and winner are untimed. The jury and verdict rounds only wait for the lions, so
 * a phase timer there would count down to nothing: the jury round shows the 15 s countdown
 * to the defense once it runs, the verdict round shows nothing.
 */
function HeaderClock({ session }: { session: SessionView }) {
  switch (session.phase) {
    case "ideation":
    case "defense":
    case "verdict":
      return null;
    case "jury":
      return (
        <AutoAdvanceNote
          endsAt={session.autoAdvanceAt}
          serverNow={session.serverNow}
          className="screen-header-note"
        />
      );
    default:
      return <Countdown endsAt={session.phaseEndsAt} serverNow={session.serverNow} sizeClass="screen-header-clock" />;
  }
}

function ScreenHeader({ session }: { session: SessionView }) {
  const index = PHASES.findIndex((phase) => phase.id === session.phase);
  return (
    <header className="screen-header">
      <Wordmark compact />
      <p className="screen-phase">{index + 1} / {PHASES.length} <span className="ml-4 text-cream">{session.label}</span></p>
      <HeaderClock session={session} />
    </header>
  );
}

function ClockPanel({ session }: { session: SessionView }) {
  return (
    <aside className="screen-clock">
      <p className="screen-note">Time remaining</p>
      <Countdown endsAt={session.phaseEndsAt} serverNow={session.serverNow} sizeClass="clock-value" className="mt-2" />
    </aside>
  );
}

function InvestorLineup({ data, showCounts = false, showIntro = false, className }: {
  data: ScreenStateView;
  showCounts?: boolean;
  showIntro?: boolean;
  className: string;
}) {
  const verdict = data.session.phase === "verdict";
  return (
    <div className={`lion-lineup ${className}`}>
      {data.investors.map((investor) => (
        <div key={investor.id} className="min-w-0">
          <InvestorAvatar investor={investor} size={220} />
          <p className="investor-name font-semibold">{investor.name}</p>
          <FocusTag investor={investor} />
          <p className="investor-role">{investor.title}</p>
          {showIntro ? <p className="investor-intro">{investor.intro}</p> : null}
          {showCounts ? <p className="investor-count">
            {(verdict ? data.verdictByInvestor : data.round1ByInvestor)[investor.id]} / {data.counts.eligible} {verdict ? "verdicts" : "reviewed"}
          </p> : null}
        </div>
      ))}
    </div>
  );
}

function Progress({ completed, total, label, teams, compact = false }: {
  compact?: boolean;
  completed: number;
  total: number;
  label: string;
  teams: Array<{ id: string; name: string; done: boolean }>;
}) {
  return (
    <section className={`screen-progress ${compact ? "is-compact" : ""}`}>
      <p className="screen-progress-head">
        <span className="screen-stat-value">{completed} / {total}</span>
        <span className="screen-note">{label}</span>
      </p>
      <ul className="screen-roster">
        {teams.map((team) => (
          <li key={team.id} className={team.done ? "is-done" : ""}>
            <span className="roster-mark" aria-hidden="true" />
            <span className="sr-only">{team.done ? "Done: " : "Waiting: "}</span>
            <span className="min-w-0">{team.name}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function LobbyStage({ data }: { data: ScreenStateView }) {
  return (
    <div className="screen-content">
      <div className="lobby-top">
        <div className="lobby-copy">
          <div>
            <h1 className="display screen-title">Enter the den.</h1>
            <p className="screen-lead">One AI startup. Four investors.<br />Make it worth backing.</p>
          </div>
          <ScoringRules threshold={data.session.investmentThreshold} projector />
          <FinaleCallout variant="projector" />
          <JuryIntro data={data} />
          <div className="lobby-join">
            <div className="min-w-0">
              <p className="screen-note">Scan the code or open</p>
              <p className="lobby-url">{data.joinShortUrl ? data.joinShortUrl.replace(/^https?:\/\//, "") : data.joinUrl}</p>
            </div>
            <div className="lobby-count">
              <p className="screen-stat-value">{data.counts.joined} / {data.maxTeams}</p>
              <p className="screen-note">teams joined</p>
            </div>
          </div>
        </div>
        <div className="lobby-qr">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={data.qrDataUrl} alt={`QR code to join at ${data.joinUrl}`} />
        </div>
      </div>
    </div>
  );
}

function JuryIntro({ data }: { data: ScreenStateView }) {
  return (
    <div className="lobby-jury">
      {data.investors.map((investor) => (
        <div key={investor.id} className="lobby-jury-item">
          <InvestorAvatar investor={investor} size={150} />
          <p className="lobby-jury-name">{investor.name}</p>
          <FocusTag investor={investor} />
          <p className="lobby-jury-line">{investor.intro}</p>
        </div>
      ))}
    </div>
  );
}

function IdeationGuide({ data }: { data: ScreenStateView }) {
  return (
    <div className="build-guide">
      <section aria-labelledby="build-needs-title">
        <h2 id="build-needs-title" className="screen-eyebrow">What your pitch needs</h2>
        <dl className="build-needs">
          {PITCH_FIELDS.map((field) => (
            <div key={field.key} className="build-need">
              <dt>{field.label}</dt>
              <dd>{field.hint}</dd>
            </div>
          ))}
        </dl>
      </section>
      <section aria-labelledby="build-jury-title">
        <h2 id="build-jury-title" className="screen-eyebrow">Your jury</h2>
        <InvestorLineup data={data} showIntro className="build-lineup" />
      </section>
    </div>
  );
}

function BuildStage({ data }: { data: ScreenStateView }) {
  const defense = data.session.phase === "defense";
  const completed = defense ? data.counts.defenseSubmitted : data.counts.submitted;
  const teams = defense ? data.teams.filter((team) => team.eligible) : data.teams;
  return (
    <div className="screen-content">
      <div className={`screen-top ${defense ? "is-build" : "is-guide"}`}>
        <div>
          <h1 className="display screen-title">{defense ? "Make your case." : "Build your pitch."}</h1>
          <p className="screen-lead">{defense
            ? "Answer your four investor questions. Be specific. The pitch stays locked."
            : "Twenty minutes to shape your AI startup. Complete the pitch and submit before the jury begins."}</p>
        </div>
        <ClockPanel session={data.session} />
      </div>
      {defense ? null : (
        <>
          <FinaleCallout variant="projector-line" />
          <IdeationGuide data={data} />
        </>
      )}
      <Progress
        completed={completed}
        total={teams.length}
        label={defense ? "Teams answered" : "Pitches submitted"}
        compact={!defense}
        teams={teams.map((team) => ({ id: team.id, name: team.name, done: defense ? team.defenseSubmitted : team.submitted }))}
      />
    </div>
  );
}

function JuryStage({ data }: { data: ScreenStateView }) {
  const verdict = data.session.phase === "verdict";
  const completed = verdict ? data.counts.verdictDone : data.counts.round1Done;
  return (
    <div className="screen-content">
      <h1 className="display screen-title">{verdict ? "The lions decide." : "Meet your investors."}</h1>
      <p className="screen-lead">{verdict
        ? "Four perspectives. One final call from each investor."
        : "Four independent scores. Four questions to defend."}</p>
      <InvestorLineup data={data} showCounts className="screen-lineup jury-lineup" />
      <Progress
        completed={completed}
        total={data.counts.eligible}
        label={verdict ? "Final verdicts" : "Pitches reviewed"}
        teams={data.teams.filter((team) => team.eligible).map((team) => ({ id: team.id, name: team.name, done: verdict ? team.verdictDone : team.round1Done }))}
      />
    </div>
  );
}

function FinalistList({ data }: { data: ScreenStateView }) {
  return (
    <ol className="neutral-list">
      {data.finalists.map((entry, index) => (
        <li
          key={entry.teamId}
          className="neutral-entry reveal"
          style={{ animationDelay: `${index * 90}ms` }}
        >
          <span className="neutral-ordinal display">{String(index + 1).padStart(2, "0")}</span>
          <div className="min-w-0">
            <p className={`neutral-name display ${entry.startupName.length > LONG_FINALE_NAME ? "is-long" : ""}`}>{entry.startupName}</p>
            {entry.teamName !== entry.startupName ? (
              <p className="neutral-meta">{entry.teamName}</p>
            ) : null}
            <p className="neutral-tagline">{entry.tagline}</p>
          </div>
        </li>
      ))}
    </ol>
  );
}

function LeaderboardStage({ data }: { data: ScreenStateView }) {
  return (
    <div className="screen-content">
      <div className="leaderboard-head">
        <h1 className="display screen-title">The stage is set.</h1>
        <p className="screen-note">Pitch order: the finalists pitch in this sequence. It is not a ranking.</p>
      </div>
      <p className="screen-lead" role="status">Finalists: prepare your pitch now. About 3 minutes each, live right after this.</p>
      <div className="flex flex-1 flex-col justify-center">
        {data.finalists.length === 0 ? (
          <p className="screen-lead">Waiting for the jury results.</p>
        ) : (
          <FinalistList data={data} />
        )}
      </div>
      <p className="screen-note pt-[3vh]">
        The audience picks the winner. The full ranking is revealed after the vote.
      </p>
    </div>
  );
}

function FinaleStage({ data }: { data: ScreenStateView }) {
  const active = Math.min(data.selectedFinalistIndex, data.finalists.length - 1);
  const current = data.finalists[active];
  if (!current) return <div className="screen-content"><p className="screen-lead">No finalists yet.</p></div>;
  const longName = current.startupName.length > LONG_FINALE_NAME;
  return (
    <div className="screen-content">
      <p className="screen-eyebrow">On stage now · Pitch {active + 1} of {data.finalists.length}</p>
      <article key={current.teamId} className="finale-spot reveal">
        <div className="min-w-0">
          <h1 className={`display finale-name ${longName ? "is-long" : ""}`}>{current.startupName}</h1>
          {current.teamName !== current.startupName ? <p className="screen-note finale-team">{current.teamName}</p> : null}
          <p className="screen-lead">{current.tagline}</p>
        </div>
      </article>
      <ol className="finalist-band">{data.finalists.map((entry, index) => (
        <li key={entry.teamId} aria-current={index === active ? "true" : undefined}>
          <span className="finalist-ordinal">{String(index + 1).padStart(2, "0")}</span>
          <span className={`min-w-0 ${entry.startupName.length > LONG_FINALE_NAME ? "is-long" : ""}`}>{entry.startupName}</span>
        </li>
      ))}</ol>
    </div>
  );
}

function VotingStage({ data }: { data: ScreenStateView }) {
  const voteUrl = data.voteUrl || "/vote";
  return (
    <div className="screen-content">
      <div className="vote-top">
        <div className="vote-copy">
          <div>
            <h1 className="display screen-title">The room decides.</h1>
            <p className="screen-lead">Scan the code. Pick your winner.<br />One vote per phone.</p>
          </div>
          <div className="vote-count">
            <p className="vote-count-value">{data.votesCast}</p>
            <p className="screen-note">votes cast</p>
          </div>
        </div>
        <div className="vote-qr">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={data.voteQrDataUrl} alt={`QR code to vote at ${voteUrl}`} />
          <p className="vote-url">{voteUrl}</p>
        </div>
      </div>
    </div>
  );
}

function WinnerStage({ data }: { data: ScreenStateView }) {
  const winner = data.finalists.find((entry) => entry.isWinner) ?? data.finalists[0];
  if (!winner) {
    return (
      <div className="screen-content">
        <h1 className="display screen-title">The room has decided.</h1>
        <p className="screen-lead">Waiting for the audience result.</p>
      </div>
    );
  }
  return (
    <div className="screen-content winner-stage">
      <p className="screen-eyebrow">The reveal · {data.votesCast} audience {data.votesCast === 1 ? "vote" : "votes"}</p>
      <h1 className={`display winner-title ${winner.startupName.length > LONG_FINALE_NAME ? "is-long" : ""}`}>
        {winner.startupName}
      </h1>
      <p className="winner-sub">{winnerExplanation(data.winnerBasis, data.votesCast)}</p>
      <p className="screen-note mt-[1.2vh]">{WINNER_RULE}</p>
      <ol className="reveal-list">
        {data.finalists.map((entry, index) => (
          <li
            key={entry.teamId}
            className={`reveal-entry reveal ${entry.isWinner ? "is-winner" : ""}`}
            style={{ animationDelay: `${index * 140}ms` }}
          >
            <span className="reveal-ordinal display">{String(index + 1).padStart(2, "0")}</span>
            <div className="reveal-body min-w-0">
              <p className={`reveal-name display ${entry.startupName.length > LONG_FINALE_NAME ? "is-long" : ""}`}>{entry.startupName}</p>
              {entry.teamName !== entry.startupName ? (
                <p className="neutral-meta">{entry.teamName}</p>
              ) : null}
            </div>
            <div className="reveal-metric">
              <p className="reveal-votes">{entry.audienceVotes ?? 0}</p>
              <p className="reveal-label">
                votes · {entry.audienceShare ?? 0}%
              </p>
            </div>
            <div className="reveal-metric reveal-jury">
              <p className="reveal-invest">
                {lionsInvest(entry.inCount ?? 0, data.investors.length)}
              </p>
              <p className="reveal-label">
                {entry.total ?? 0}/{data.investors.length * data.session.scoreMax} points, breaks ties
              </p>
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}
