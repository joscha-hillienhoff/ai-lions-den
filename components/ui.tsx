"use client";

import { useState, type ReactNode } from "react";
import { FINALE_DETAIL, FINALE_HEADLINE, scoringRules, SCORING_RULES_TITLE } from "@/lib/rules";
import { pointsTieBreak, scoreReading } from "@/lib/scoring";
import { BrandMark } from "./brand";

/** One-shot brightening of the stage light. Remounts only when `cue` changes. */
export function StageCue({ cue }: { cue: string }) {
  return <div key={cue} className="stage-cue" aria-hidden="true" />;
}

export function PhaseHeading({
  title,
  lead,
  aside,
}: {
  title: ReactNode;
  lead?: ReactNode;
  aside?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-x-10 gap-y-5">
      <div className="min-w-0 max-w-3xl">
        <h1 className="display text-title leading-[1.06] md:text-page">{title}</h1>
        {lead ? <p className="lead mt-3 max-w-2xl">{lead}</p> : null}
      </div>
      {aside}
    </div>
  );
}

export function Splash({ label = "Setting the stage" }: { label?: string }) {
  return (
    <main className="stage relative grid min-h-dvh place-items-center overflow-hidden">
      <div className="relative z-10 flex flex-col items-center gap-5 animate-fade">
        <BrandMark size={56} />
        <p className="label">{label}</p>
      </div>
    </main>
  );
}

export function Stat({
  value,
  label,
  accent = "var(--color-gold)",
}: {
  value: ReactNode;
  label: string;
  accent?: string;
}) {
  return (
    <div>
      <p className="stat-value text-stat md:text-stat-lg" style={{ color: accent }}>
        {value}
      </p>
      <p className="label mt-1">{label}</p>
    </div>
  );
}

export function DecisionChip({ decision }: { decision: "in" | "out" | null }) {
  if (!decision) return null;
  const isIn = decision === "in";
  return (
    <span
      className="inline-flex items-center gap-2 text-label font-semibold"
      style={{ color: isIn ? "var(--color-mint)" : "var(--color-rose)" }}
    >
      <span className="h-1.5 w-1.5 rounded-full bg-current" />
      {isIn ? "I'm in" : "I'm out"}
    </span>
  );
}

export function ScoreDial({
  score,
  max = 10,
  size = 64,
}: {
  score: number | null;
  max?: number;
  size?: number;
}) {
  return (
    <div className="stat-value text-right" style={{ minWidth: size }} aria-label={score === null ? "Score pending" : `${score} out of ${max}`}>
      <span style={{ fontSize: size * 0.5, color: score === null ? "var(--color-muted)" : "var(--color-gold)" }}>{score ?? "-"}</span>
      <span className="ml-1 text-small font-medium text-muted">/{max}</span>
    </div>
  );
}

/** The 1 to 10 scale with the "I'm in" line marked, plus plain words. */
export function ScoreReading({
  score,
  threshold,
  scoreMax = 10,
  provisional = false,
  className = "",
}: {
  score: number;
  threshold: number;
  scoreMax?: number;
  provisional?: boolean;
  className?: string;
}) {
  const reading = scoreReading(score, threshold, { scoreMax, provisional });
  // The line sits at the start of the first "I'm in" point.
  const markerLeft = ((threshold - 1) / scoreMax) * 100;
  return (
    <div className={`score-reading ${className}`}>
      <div
        className="score-scale"
        role="img"
        aria-label={`${reading}. ${scoreMax} point scale, I'm in from ${threshold}.`}
      >
        <div className="score-scale-segments">
          {Array.from({ length: scoreMax }, (_, index) => {
            const point = index + 1;
            return (
              <span
                key={point}
                className={`score-scale-segment ${point <= score ? "is-filled" : ""} ${
                  point >= threshold ? "is-in-zone" : ""
                }`}
              />
            );
          })}
        </div>
        <span className="score-scale-marker" style={{ left: `${markerLeft}%` }} aria-hidden="true" />
      </div>
      <p className="score-scale-caption">
        <span>1-{scoreMax}</span>
        <span>I'm in from {threshold}</span>
      </p>
      <p className="score-reading-text">{reading}</p>
    </div>
  );
}

/** The team figure that counts: lions who invest, with points as the tie-break. */
export function TeamResult({
  inCount,
  total,
  investorCount,
  scoreMax = 10,
  compact = false,
}: {
  inCount: number;
  total: number;
  investorCount: number;
  scoreMax?: number;
  compact?: boolean;
}) {
  const maxPoints = investorCount * scoreMax;
  return (
    <div>
      <p className={compact ? "team-result-main is-compact" : "team-result-main"}>
        {inCount} of {investorCount}{" "}
        <span className="team-result-unit">lions invest</span>
      </p>
      <p className="label mt-1">{pointsTieBreak(total, maxPoints)}</p>
    </div>
  );
}

/** The three rules of the session, one source for every place they appear. */
export function ScoringRules({
  threshold,
  projector = false,
}: {
  threshold: number;
  projector?: boolean;
}) {
  return (
    <section className={projector ? "rules-panel rules-panel-projector" : "panel rules-panel"}>
      <p className={projector ? "rules-title rules-title-projector" : "label label-gold"}>
        {SCORING_RULES_TITLE}
      </p>
      <ol className="rules-list">
        {scoringRules(threshold).map((rule, index) => (
          <li key={rule}>
            <span className="rules-number" aria-hidden="true">
              {index + 1}
            </span>
            <span>{rule}</span>
          </li>
        ))}
      </ol>
    </section>
  );
}

/** A small, quiet note that the top 3 teams pitch live at the end. It must not compete with the task explanation. */
export function FinaleCallout({
  variant = "team",
}: {
  variant?: "team" | "projector" | "projector-line";
}) {
  return (
    <aside className={`finale-callout finale-callout-${variant}`} aria-label="Live finale">
      <span className="finale-callout-mark" aria-hidden="true" />
      <p>
        <strong>{FINALE_HEADLINE}</strong>
        {" "}
        <span>{FINALE_DETAIL}</span>
      </p>
    </aside>
  );
}

export function CopyButton({ value, label = "Copy link" }: { value: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      className="btn btn-ghost px-4 py-2"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(value);
          setCopied(true);
          setTimeout(() => setCopied(false), 1600);
        } catch {
          setCopied(false);
        }
      }}
    >
      {copied ? "Copied" : label}
    </button>
  );
}

export function InlineNotice({
  tone = "info",
  children,
}: {
  tone?: "info" | "error" | "success";
  children: ReactNode;
}) {
  const styles =
    tone === "error"
      ? "border-rose/30 bg-rose/[0.07] text-rose"
      : tone === "success"
        ? "border-mint/30 bg-mint/[0.07] text-mint"
        : "border-line bg-panel-2/60 text-muted";
  return (
    <div className={`rounded border px-4 py-3 text-small ${styles}`}>{children}</div>
  );
}

export function Modal({
  open,
  title,
  children,
  onClose,
  wide = false,
}: {
  open: boolean;
  title: string;
  children: ReactNode;
  onClose: () => void;
  wide?: boolean;
}) {
  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center bg-ink/85 p-6 animate-fade"
      onClick={onClose}
      role="presentation"
    >
      <div
        className={`panel reveal w-full ${wide ? "max-w-4xl" : "max-w-md"} p-6 max-h-[90dvh] overflow-y-auto`}
        onClick={(event) => event.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <h2 className="display text-heading">{title}</h2>
        <div className="mt-4">{children}</div>
      </div>
    </div>
  );
}
