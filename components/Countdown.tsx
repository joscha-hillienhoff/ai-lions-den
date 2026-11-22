"use client";

import { useEffect, useRef, useState } from "react";

function format(remainingMs: number): string {
  const totalSeconds = Math.max(0, Math.ceil(remainingMs / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

/**
 * Milliseconds until a server deadline, ticking locally between polls. The browser clock
 * only measures elapsed time since the last server timestamp, so a wrong device clock
 * changes nothing. Null while there is no deadline.
 */
export function useRemainingMs(endsAt: number | null, serverNow: number): number | null {
  const sync = useRef({ local: Date.now(), server: serverNow });
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    sync.current = { local: Date.now(), server: serverNow };
    setNow(Date.now());
  }, [serverNow]);

  useEffect(() => {
    if (endsAt === null) return;
    const timer = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(timer);
  }, [endsAt]);

  if (endsAt === null) return null;
  return endsAt - sync.current.server - (now - sync.current.local);
}

/**
 * "Defense starts in 15 s": the live seconds until the jury round advances by itself.
 * Renders nothing while no countdown runs.
 */
export function AutoAdvanceNote({
  endsAt,
  serverNow,
  className = "",
}: {
  endsAt: number | null;
  serverNow: number;
  className?: string;
}) {
  const remaining = useRemainingMs(endsAt, serverNow);
  if (remaining === null) return null;
  const seconds = Math.max(0, Math.ceil(remaining / 1000));
  return (
    <span className={className} data-auto-advance>
      Defense starts in <span className="clock-digits">{seconds} s</span>
    </span>
  );
}

/**
 * The phase countdown. `endsAt` is null for an untimed phase (the finale and the
 * winner page); then nothing renders, so there is no clock and no "Time is up".
 */
export function Countdown({
  endsAt,
  serverNow,
  className = "",
  showLabel = false,
  sizeClass = "text-heading",
}: {
  endsAt: number | null;
  serverNow: number;
  className?: string;
  showLabel?: boolean;
  sizeClass?: string;
}) {
  const remaining = useRemainingMs(endsAt, serverNow);

  if (remaining === null) return null;

  const urgent = remaining <= 60_000;
  const over = remaining < 0;

  return (
    <div className={className}>
      {showLabel ? <p className="label mb-1">Time left</p> : null}
      <p
        className={[
          "clock-digits font-semibold transition-colors",
          sizeClass,
          over ? "text-muted" : urgent ? "text-rose" : "text-cream",
        ].join(" ")}
      >
        {format(remaining)}
      </p>
      {over ? (
        <p className="mt-1 text-label font-medium text-muted">
          Time is up
        </p>
      ) : null}
    </div>
  );
}

const PITCH_GUIDE_MS = 3 * 60_000;

/**
 * The orientation clock of one live finale pitch (about 3 minutes). It counts down from the
 * server timestamp of the moment the moderator started it (or picked another finalist), runs on into visible
 * overtime after 00:00 and never advances anything. Renders nothing outside the finale.
 */
export function PitchClock({
  endsAt,
  serverNow,
  className = "",
  sizeClass = "text-heading",
  notStarted = false,
  notStartedHint = "Not started",
}: {
  endsAt: number | null;
  serverNow: number;
  className?: string;
  sizeClass?: string;
  /** The moderator has not started the clock yet: show a static 3:00 and a hint. */
  notStarted?: boolean;
  notStartedHint?: string;
}) {
  const remaining = useRemainingMs(endsAt, serverNow);
  if (remaining === null) {
    if (!notStarted) return null;
    return (
      <div className={className} data-pitch-clock="idle">
        <p className={["clock-digits font-semibold text-muted", sizeClass].join(" ")}>
          {format(PITCH_GUIDE_MS)}
        </p>
        <p className="mt-1 text-label font-medium text-muted">{notStartedHint}</p>
      </div>
    );
  }
  const over = remaining < 0;
  const clock = over ? format(-remaining + 999) : format(remaining);
  return (
    <div className={className} data-pitch-clock={over ? "over" : "running"}>
      <p
        className={[
          "clock-digits font-semibold transition-colors",
          sizeClass,
          over ? "text-rose" : remaining <= 30_000 ? "text-gold" : "text-cream",
        ].join(" ")}
      >
        {over ? "+" : ""}
        {clock}
      </p>
      <p className="mt-1 text-label font-medium text-muted">
        {over ? "Over the guide time" : "Pitch guide time"}
      </p>
    </div>
  );
}
