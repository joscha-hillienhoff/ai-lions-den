"use client";

import { useEffect, useState } from "react";
import { INVESTOR_IDS } from "@/lib/investors";
import type { FinalistView, VoteStateView } from "@/lib/views";
import { WINNER_RULE, winnerExplanation } from "@/lib/winner";
import { Wordmark } from "./brand";
import { InlineNotice, Splash } from "./ui";
import { usePoll } from "./usePoll";

export function VoteApp() {
  const { data, loading, error, refresh } = usePoll<VoteStateView>("/api/vote/state", 1500);
  const [selectedTeamId, setSelectedTeamId] = useState<string | null>(null);
  const [submittedTeamId, setSubmittedTeamId] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  useEffect(() => {
    document.title = "Vote | The AI Lion's Den";
  }, []);

  if (loading && !data) return <Splash label="Opening the vote" />;

  if (!data) {
    return (
      <main className="stage grid min-h-dvh place-items-center p-6">
        <div className="panel w-full max-w-md p-8 text-center">
          <p className="display text-heading">Connecting...</p>
          <p className="mt-3 text-small text-muted">{error ?? "The vote is loading."}</p>
          <button className="btn btn-gold mt-6 px-6 py-3" onClick={() => void refresh()}>
            Try again
          </button>
        </div>
      </main>
    );
  }

  const hasVoted = data.hasVoted || submittedTeamId !== null;
  const votedTeamId = submittedTeamId ?? data.votedTeamId;
  const selected = data.finalists.find((entry) => entry.teamId === selectedTeamId) ?? null;
  const reveal = data.session.phase === "winner" && data.finalists.some((entry) => entry.isWinner);

  async function submit() {
    if (!selected || submitting || hasVoted) return;
    setSubmitting(true);
    setFormError(null);
    try {
      const response = await fetch("/api/vote", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ teamId: selected.teamId }),
      });
      const payload = (await response.json().catch(() => null)) as { error?: string } | null;
      if (!response.ok) {
        setFormError(payload?.error ?? "Could not send your vote.");
        await refresh();
        return;
      }
      setSubmittedTeamId(selected.teamId);
      await refresh();
    } catch {
      setFormError("Could not reach the server.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="stage min-h-dvh pb-32">
      <header className="border-b border-line/60">
        <div className="mx-auto flex w-full max-w-2xl items-center justify-between px-5 py-4">
          <Wordmark compact />
          <p className="label">{data.session.label}</p>
        </div>
      </header>

      <div className="mx-auto w-full max-w-2xl px-5 py-8">
        {reveal ? (
          <RevealView votedTeamId={votedTeamId} data={data} />
        ) : hasVoted ? (
          <ThankYou
            startupName={
              data.finalists.find((entry) => entry.teamId === votedTeamId)?.startupName ?? null
            }
          />
        ) : data.open ? (
          <VoteForm
            data={data}
            selectedTeamId={selectedTeamId}
            onSelect={(teamId) => {
              setSelectedTeamId(teamId);
              setFormError(null);
            }}
          />
        ) : (
          <Waiting data={data} />
        )}

        {formError ? (
          <div className="fixed inset-x-0 bottom-32 z-50 mx-auto max-w-2xl px-5">
            <InlineNotice tone="error">{formError}</InlineNotice>
          </div>
        ) : null}
      </div>

      {!hasVoted && !reveal && data.open && selected ? (
        <div className="fixed inset-x-0 bottom-0 z-50 border-t border-line bg-ink/95 px-5 py-4 backdrop-blur">
          <div className="mx-auto flex w-full max-w-2xl items-center justify-between gap-4">
            <div className="min-w-0">
              <p className="label">Your vote</p>
              <p className="truncate text-body font-semibold text-cream">{selected.startupName}</p>
            </div>
            <button
              className="btn btn-gold shrink-0 px-6 py-3.5"
              onClick={() => void submit()}
              disabled={submitting}
            >
              {submitting ? "Sending..." : "Confirm vote"}
            </button>
          </div>
        </div>
      ) : null}
    </main>
  );
}

function VoteForm({
  data,
  selectedTeamId,
  onSelect,
}: {
  data: VoteStateView;
  selectedTeamId: string | null;
  onSelect: (teamId: string) => void;
}) {
  return (
    <div>
      <p className="label label-gold">Audience vote</p>
      <h1 className="display mt-2 text-title leading-[1.05] md:text-page">Pick the winner.</h1>
      <p className="mt-3 text-small leading-relaxed text-muted">
        Three finalists. One vote. Choose the team you want to win, then confirm.
      </p>
      <p className="mt-2 text-label leading-relaxed text-muted">
        One vote per browser. Your vote is final and cannot be changed.
      </p>

      <div className="mt-6 space-y-3">
        {data.finalists.map((entry, index) => {
          const active = entry.teamId === selectedTeamId;
          return (
            <button
              key={entry.teamId}
              type="button"
              aria-pressed={active}
              onClick={() => onSelect(entry.teamId)}
              className={`w-full rounded border p-5 text-left transition-colors ${
                active
                  ? "border-gold bg-gold/[0.08]"
                  : "border-line bg-panel hover:border-brass"
              }`}
            >
              <div className="flex items-start justify-between gap-4">
                <div className="min-w-0">
                  <p className="label text-brass">Pitch {index + 1}</p>
                  <p className="display mt-1 text-heading leading-tight text-cream">
                    {entry.startupName}
                  </p>
                  {entry.teamName !== entry.startupName ? (
                    <p className="mt-1 text-label text-muted">{entry.teamName}</p>
                  ) : null}
                  <p className="mt-2 text-small leading-relaxed text-muted">{entry.tagline}</p>
                </div>
                <span
                  aria-hidden="true"
                  className={`mt-1 grid h-7 w-7 shrink-0 place-items-center rounded-full border text-small font-semibold ${
                    active ? "border-gold bg-gold text-ink" : "border-line text-transparent"
                  }`}
                >
                  ✓
                </span>
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function ThankYou({ startupName }: { startupName: string | null }) {
  return (
    <div className="panel p-7 text-center md:p-9">
      <p className="label label-gold">Vote received</p>
      <p className="display mt-2 text-title leading-tight md:text-page">Thank you.</p>
      <p className="mx-auto mt-4 max-w-md text-small leading-relaxed text-muted">
        {startupName ? `Your vote for ${startupName} is final. ` : "Your vote is final. "}
        Watch the projector for the reveal.
      </p>
    </div>
  );
}

function Waiting({ data }: { data: VoteStateView }) {
  return (
    <div>
      <p className="label label-gold">Audience vote</p>
      <h1 className="display mt-2 text-title leading-[1.05] md:text-page">
        {data.session.phase === "winner" ? "The vote is closed." : "The vote opens after the finale."}
      </h1>
      <p className="mt-3 text-small leading-relaxed text-muted">
        {data.session.phase === "winner"
          ? "The result is on the projector."
          : "Watch the projector. The three finalists and the vote code appear at the end of the finale."}
      </p>
      {data.finalists.length > 0 ? (
        <div className="mt-7">
          <p className="label mb-2">Pitch order (not a ranking)</p>
          <div className="panel overflow-hidden">
            {data.finalists.map((entry, index) => (
              <div
                key={entry.teamId}
                className="flex items-center gap-4 border-b border-line/60 px-5 py-4 last:border-0"
              >
                <span className="w-7 text-center text-small font-semibold tabular-nums text-brass">
                  {String(index + 1).padStart(2, "0")}
                </span>
                <div className="min-w-0">
                  <p className="truncate text-small font-semibold text-cream">
                    {entry.startupName}
                  </p>
                  {entry.teamName !== entry.startupName ? (
                    <p className="truncate text-label text-muted">{entry.teamName}</p>
                  ) : null}
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function RevealView({ data, votedTeamId }: { data: VoteStateView; votedTeamId: string | null }) {
  const winner = data.finalists.find((entry) => entry.isWinner) ?? data.finalists[0];
  return (
    <div>
      <p className="label label-gold">The reveal</p>
      <h1 className="display mt-2 text-title leading-[1.05] md:text-page">
        {winner?.startupName ?? "The room has decided."}
      </h1>
      <p className="mt-2 text-small leading-relaxed text-muted">
        {winnerExplanation(data.winnerBasis, data.votesCast)}{" "}
        {data.votesCast} {data.votesCast === 1 ? "vote" : "votes"} from the room.
      </p>
      <p className="mt-2 text-label leading-relaxed text-muted">{WINNER_RULE}</p>

      <div className="mt-7 space-y-3">
        {data.finalists.map((entry, index) => (
          <RevealRow
            key={entry.teamId}
            entry={entry}
            index={index}
            isVoted={entry.teamId === votedTeamId}
          />
        ))}
      </div>

      <p className="mt-6 text-center text-label leading-relaxed text-muted">
        Watch the projector for the full result.
      </p>
    </div>
  );
}

function RevealRow({
  entry,
  index,
  isVoted,
}: {
  entry: FinalistView;
  index: number;
  isVoted: boolean;
}) {
  return (
    <div className={`panel p-5 ${entry.isWinner ? "border-gold/50" : ""}`}>
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="label text-brass">
            Finalist {index + 1}
            {isVoted ? <span className="ml-2 font-semibold text-gold">Your vote</span> : null}
          </p>
          <p className="display mt-1 text-heading leading-tight">{entry.startupName}</p>
          {entry.teamName !== entry.startupName ? (
            <p className="mt-1 text-label text-muted">{entry.teamName}</p>
          ) : null}
        </div>
        {entry.isWinner ? <p className="text-label font-semibold text-gold">Winner</p> : null}
      </div>
      <div className="mt-4 grid grid-cols-2 gap-3 border-t border-line/60 pt-4 text-small">
        <div>
          <p className="stat-value text-stat text-cream">{entry.audienceVotes ?? 0}</p>
          <p className="label mt-1">Audience votes · {entry.audienceShare ?? 0}%</p>
        </div>
        <div>
          <p className="stat-value text-stat text-mint">
            {entry.inCount ?? 0} of {INVESTOR_IDS.length}{" "}
            <span className="text-small font-semibold">lions invest</span>
          </p>
          <p className="label mt-1">
            {entry.total ?? 0}/{INVESTOR_IDS.length * 10} points, breaks ties
          </p>
        </div>
      </div>
    </div>
  );
}
