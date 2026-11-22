"use client";

import { useEffect } from "react";
import type { TeamStateView } from "@/lib/views";
import { Wordmark } from "./brand";
import { Countdown } from "./Countdown";
import { JoinScreen } from "./JoinScreen";
import { PitchPhase } from "./PitchPhase";
import {
  DefensePhase,
  FinalePanel,
  JuryWaiting,
  LobbyPanel,
  TeamLeaderboard,
  TeamMeta,
  VerdictPhase,
  VotingPanel,
  WinnerPanel,
} from "./TeamPhasePanels";
import { InlineNotice, Splash, StageCue } from "./ui";
import { usePoll } from "./usePoll";

export function TeamApp() {
  const { data, loading, error, refresh } = usePoll<TeamStateView>("/api/team/state", 1500);

  useEffect(() => {
    if (data) {
      document.title = data.team
        ? `${data.team.name} - ${data.session.label} | The AI Lion's Den`
        : "Join The AI Lion's Den";
    }
  }, [data]);

  if (loading && !data) return <Splash label="Taking your seat" />;

  if (!data) {
    return (
      <main className="stage relative grid min-h-dvh place-items-center p-6">
        <div className="relative z-10 w-full max-w-md">
          <div className="panel rounded p-8 text-center">
            <p className="display text-heading">Connecting...</p>
            <p className="mt-3 text-small text-muted">{error ?? "The session is loading."}</p>
            <button className="btn btn-gold mt-6 px-6 py-3" onClick={() => refresh()}>
              Try again
            </button>
          </div>
        </div>
      </main>
    );
  }

  if (!data.team) {
    return <JoinScreen session={data.session} investors={data.investors} onJoined={refresh} />;
  }

  const team = data.team;

  return (
    <main className="stage min-h-dvh">
      <StageCue cue={data.session.phase} />
      <header className="sticky top-0 z-40 border-b border-line/60 bg-ink">
        <div className="mx-auto flex w-full max-w-7xl items-center justify-between gap-4 px-5 py-3.5 md:px-8">
          <Wordmark compact />
          <div className="flex min-w-0 items-center gap-3 md:gap-5">
            <TeamMeta name={team.name} />
            {data.session.phase !== "lobby" && data.session.phase !== "winner" ? (
              <div className="hidden text-right sm:block">
                <p className="label">{data.session.label}</p>
                {/* The lions are the only thing the teams wait for in these two rounds. */}
                {data.session.phase !== "jury" && data.session.phase !== "verdict" ? (
                  <Countdown
                    endsAt={data.session.phaseEndsAt}
                    serverNow={data.session.serverNow}
                    className="mt-0.5 text-subhead"
                  />
                ) : null}
              </div>
            ) : null}
          </div>
        </div>
      </header>

      {/* The key limits the entrance to real phase changes; polling keeps it stable. */}
      <div key={data.session.phase} className="phase-enter mx-auto w-full max-w-7xl px-5 py-8 md:px-8 md:py-12">
        {error ? (
          <div className="mb-5">
            <InlineNotice tone="error">{error}</InlineNotice>
          </div>
        ) : null}

        {data.session.phase === "lobby" ? (
          <LobbyPanel team={team} session={data.session} investors={data.investors} />
        ) : null}

        {data.session.phase === "ideation" ? (
          <PitchPhase team={team} investors={data.investors} onRefresh={refresh} />
        ) : null}

        {data.session.phase === "jury" ? (
          <JuryWaiting team={team} investors={data.investors} />
        ) : null}

        {data.session.phase === "defense" ? (
          <DefensePhase
            team={team}
            investors={data.investors}
            onRefresh={refresh}
            threshold={data.session.investmentThreshold}
            scoreMax={data.session.scoreMax}
          />
        ) : null}

        {data.session.phase === "verdict" ? (
          <VerdictPhase
            team={team}
            investors={data.investors}
            threshold={data.session.investmentThreshold}
            scoreMax={data.session.scoreMax}
          />
        ) : null}

        {data.session.phase === "leaderboard" ? (
          <TeamLeaderboard
            finalists={data.finalists}
            team={team}
            investors={data.investors}
            scoreMax={data.session.scoreMax}
          />
        ) : null}

        {data.session.phase === "finale" ? (
          <FinalePanel
            team={team}
            investors={data.investors}
            finalists={data.finalists}
            selectedFinalistIndex={data.selectedFinalistIndex}
            session={data.session}
          />
        ) : null}

        {data.session.phase === "voting" ? (
          <VotingPanel
            team={team}
            investors={data.investors}
            isFinalist={data.finalists.some((entry) => entry.teamId === team.id)}
          />
        ) : null}

        {data.session.phase === "winner" ? (
          <WinnerPanel
            teamId={team.id}
            finalists={data.finalists}
            ranking={data.ranking}
            votesCast={data.votesCast}
            winnerBasis={data.winnerBasis}
          />
        ) : null}
      </div>

    </main>
  );
}
