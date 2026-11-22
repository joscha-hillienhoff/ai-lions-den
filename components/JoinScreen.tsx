"use client";

import { useState } from "react";
import type { SessionView } from "@/lib/views";
import type { PublicInvestor } from "@/lib/views";
import { FocusTag, InvestorAvatar, Wordmark } from "./brand";
import { InlineNotice, ScoringRules } from "./ui";

export function JoinScreen({
  session,
  investors,
  onJoined,
}: {
  session: SessionView;
  investors: PublicInvestor[];
  onJoined: () => Promise<void> | void;
}) {
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const joinClosed = session.phase !== "lobby" && session.phase !== "ideation";

  async function join(event: React.FormEvent) {
    event.preventDefault();
    if (!name.trim() || pending) return;
    setPending(true);
    setError(null);
    try {
      const response = await fetch("/api/teams/join", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      const payload = (await response.json().catch(() => null)) as { error?: string } | null;
      if (!response.ok) {
        setError(payload?.error ?? "Could not join the session.");
        return;
      }
      await onJoined();
    } catch {
      setError("Could not reach the server. Try again.");
    } finally {
      setPending(false);
    }
  }

  return (
    <main className="stage relative min-h-dvh overflow-hidden">
      <div className="mx-auto flex min-h-dvh w-full max-w-7xl flex-col px-6 py-8 lg:px-12">
        <Wordmark />
        <div className="grid flex-1 grid-cols-1 items-center gap-12 py-10 lg:grid-cols-[1.3fr_0.7fr]">
        <section>
          <div className="max-w-2xl">
            <h1 className="display join-title text-cream">An idea worth<br />roaring for.</h1>
            <p className="mt-6 max-w-xl text-body leading-relaxed text-muted md:text-subhead">
              Twenty minutes to build your pitch. Four lion investors to win over.
              A fictional 250,000 EUR investment. The top three teams pitch live.
            </p>
          </div>

          <div className="mt-8">
            <ScoringRules threshold={session.investmentThreshold} />
          </div>

          <div className="lion-lineup investor-cards mt-10">
            {investors.map((investor) => (
              <div key={investor.id} className="min-w-0">
                <InvestorAvatar investor={investor} size={160} />
                <p className="investor-name text-small font-semibold text-cream">{investor.name}</p>
                <FocusTag investor={investor} />
                <p className="investor-role">{investor.title}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="w-full">
          <div className="panel p-7 md:p-8">
            <h2 className="display text-heading text-cream md:text-title">
              {joinClosed ? "Team check-in closed" : "Join the den"}
            </h2>
            <p className="mt-3 text-small leading-relaxed text-muted">
              {joinClosed
                ? "Team registration is available during the lobby and ideation phases only."
                : "One laptop per team. No account. Your team stays signed in in this browser."}
            </p>

            {joinClosed ? (
              <div className="mt-8">
                <InlineNotice tone="info">Follow the session from the projector.</InlineNotice>
              </div>
            ) : (
              <form onSubmit={join} className="mt-8 space-y-4">
                <div>
                  <label htmlFor="team-name" className="label mb-2 block">
                    Team name
                  </label>
                  <input
                    id="team-name"
                    className="input"
                    value={name}
                    onChange={(event) => setName(event.target.value)}
                    maxLength={40}
                    placeholder="e.g. The Margin Calls"
                    autoFocus
                    autoComplete="off"
                  />
                  <div className="mt-1.5 flex justify-between text-label text-muted">
                    <span>Up to 40 characters.</span>
                    <span className="tabular-nums">{name.length}/40</span>
                  </div>
                </div>

                {error ? <InlineNotice tone="error">{error}</InlineNotice> : null}

                <button
                  type="submit"
                  className="btn btn-gold w-full px-6 py-4"
                  disabled={pending || name.trim().length === 0}
                >
                  {pending ? "Joining..." : "Enter the session"}
                </button>
              </form>
            )}
          </div>
          <p className="label mt-4 text-center">
            {session.phase === "lobby" ? "The moderator is about to start." : session.label}
          </p>
        </section>
        </div>
      </div>
    </main>
  );
}
