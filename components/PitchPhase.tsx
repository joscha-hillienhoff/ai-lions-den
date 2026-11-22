"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { PITCH_FIELDS, pitchIsComplete } from "@/lib/pitch";
import type { Pitch, PitchFieldKey } from "@/lib/session-state";
import type { PublicInvestor } from "@/lib/views";
import { InvestorMini } from "./brand";
import { FinaleCallout, InlineNotice, PhaseHeading } from "./ui";

type SaveState = "idle" | "saving" | "saved" | "error";

export function PitchPhase({
  team,
  investors,
  onRefresh,
}: {
  team: {
    id: string;
    name: string;
    pitch: Pitch;
    pitchUpdatedAt: number | null;
    submittedAt: number | null;
    pitchLocked: boolean;
  };
  investors: PublicInvestor[];
  onRefresh: () => Promise<void> | void;
}) {
  const [pitch, setPitch] = useState<Pitch>(team.pitch);
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const [submittedAt, setSubmittedAt] = useState<number | null>(team.submittedAt);
  const [fieldErrors, setFieldErrors] = useState<Partial<Record<PitchFieldKey, string>>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const dirty = useRef(false);
  const latest = useRef(pitch);
  const expectedPitchUpdatedAt = useRef(team.pitchUpdatedAt);
  const pitchWriteQueue = useRef(Promise.resolve());
  latest.current = pitch;

  const queuePitchWrite = useCallback((value: Pitch, submit: boolean) => {
    const request = pitchWriteQueue.current.then(async () => {
      const response = await fetch("/api/teams/pitch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          pitch: value,
          submit,
          expectedPitchUpdatedAt: expectedPitchUpdatedAt.current,
        }),
      });
      if (response.ok) {
        const payload = (await response.clone().json().catch(() => null)) as {
          team?: { pitchUpdatedAt?: number | null };
        } | null;
        if (payload?.team && "pitchUpdatedAt" in payload.team) {
          expectedPitchUpdatedAt.current = payload.team.pitchUpdatedAt ?? null;
        }
      }
      return response;
    });
    pitchWriteQueue.current = request.then(
      () => undefined,
      () => undefined,
    );
    return request;
  }, []);

  useEffect(() => {
    setSubmittedAt(team.submittedAt);
    if ((team.pitchUpdatedAt ?? 0) > (expectedPitchUpdatedAt.current ?? 0)) {
      expectedPitchUpdatedAt.current = team.pitchUpdatedAt;
    }
  }, [team.pitchUpdatedAt, team.submittedAt]);

  // Pick up a draft saved from another tab, but never overwrite active typing.
  useEffect(() => {
    if (!dirty.current) setPitch(team.pitch);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [team.pitchUpdatedAt]);

  const saveDraft = useCallback(async (value: Pitch) => {
    setSaveState("saving");
    try {
      const response = await queuePitchWrite(value, false);
      if (!response.ok) {
        setSaveState("error");
        return;
      }
      setSaveState("saved");
      setSavedAt(Date.now());
      setFieldErrors({});
    } catch {
      setSaveState("error");
    }
  }, [queuePitchWrite]);

  useEffect(() => {
    if (!dirty.current) return;
    const timer = setTimeout(() => {
      void saveDraft(latest.current);
    }, 700);
    return () => clearTimeout(timer);
  }, [pitch, saveDraft]);

  function updateField(key: PitchFieldKey, value: string) {
    dirty.current = true;
    setPitch((current) => ({ ...current, [key]: value }));
    setFieldErrors((current) => ({ ...current, [key]: undefined }));
  }

  async function submitPitch(event: React.FormEvent) {
    event.preventDefault();
    if (submitting) return;
    setFormError(null);
    setSubmitting(true);
    try {
      const response = await queuePitchWrite(pitch, true);
      const payload = (await response.json().catch(() => null)) as
        | { error?: string; fieldErrors?: Partial<Record<PitchFieldKey, string>>; team?: { submittedAt: number | null } }
        | null;
      if (!response.ok) {
        setFormError(payload?.error ?? "Could not submit the pitch.");
        setFieldErrors(payload?.fieldErrors ?? {});
        return;
      }
      dirty.current = false;
      setSubmittedAt(payload?.team?.submittedAt ?? Date.now());
      setSaveState("saved");
      setSavedAt(Date.now());
      await onRefresh();
    } catch {
      setFormError("Could not reach the server. Your draft is still here.");
    } finally {
      setSubmitting(false);
    }
  }

  const filled = PITCH_FIELDS.filter((field) => pitch[field.key].trim().length > 0).length;
  const complete = pitchIsComplete(pitch);
  const missing = PITCH_FIELDS.length - filled;
  const locked = team.pitchLocked;

  return (
    <div className="grid grid-cols-1 gap-6 xl:grid-cols-[1fr_20rem]">
      <form onSubmit={submitPitch} className="min-w-0">
        <PhaseHeading
          title="Build the pitch."
          lead="Pitch an AI startup. Short, concrete, and believable. The four investors read only what you write here."
          aside={
            <div className="text-right text-label text-muted">
              <p className="font-semibold tabular-nums text-cream">
                {filled} / {PITCH_FIELDS.length} fields complete
              </p>
              <p className="mt-1 flex items-center justify-end gap-2">
                <span
                  className="h-1.5 w-1.5 rounded-full transition-colors"
                  style={{
                    background:
                      saveState === "error"
                        ? "var(--color-rose)"
                        : saveState === "saving"
                          ? "var(--color-gold)"
                          : "var(--color-mint)",
                  }}
                />
                {saveState === "saving"
                  ? "Saving draft..."
                  : saveState === "error"
                    ? "Draft not saved - keep typing to retry"
                    : savedAt
                      ? `Draft saved ${new Date(savedAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`
                      : "Draft saves automatically"}
              </p>
            </div>
          }
        />
        <div className="mt-5 max-w-3xl">
          <FinaleCallout />
        </div>

        {locked ? (
          <div className="mt-6">
            <InlineNotice tone="info">
              The lions already judged this - it can no longer be changed.
            </InlineNotice>
          </div>
        ) : null}

        {submittedAt && !locked ? (
          <div className="mt-6 flex flex-wrap items-center gap-3 rounded border border-mint/25 bg-mint/[0.06] px-4 py-3 text-small text-mint">
            <span className="font-semibold">Pitch submitted.</span>
            <span className="text-mint/75">
              You can keep refining it until the jury phase starts.
            </span>
          </div>
        ) : null}

        {!complete && filled > 0 && !locked ? (
          <div className="mt-4">
            <InlineNotice tone="info">
              {missing} field{missing === 1 ? "" : "s"} still empty. An incomplete pitch is not
              evaluated.
            </InlineNotice>
          </div>
        ) : null}

        <div className="panel mt-6 rounded p-5 md:p-7">
          <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
            {PITCH_FIELDS.map((field, index) => {
              const value = pitch[field.key];
              const error = fieldErrors[field.key];
              const over = value.length > field.limit;
              const wide = field.key === "startupName" || field.key === "tagline";
              return (
                <div key={field.key} className={wide ? "md:col-span-2" : ""}>
                  <div className="mb-2 flex items-baseline justify-between gap-3">
                    <label htmlFor={field.key} className="flex items-baseline gap-2.5">
                      <span className="text-label font-semibold tabular-nums text-brass">
                        {String(index + 1).padStart(2, "0")}
                      </span>
                      <span className="text-small font-semibold text-cream">{field.label}</span>
                    </label>
                    <span
                      className={`text-label tabular-nums ${over ? "text-rose" : "text-muted"}`}
                    >
                      {value.length}/{field.limit}
                    </span>
                  </div>
                  {field.multiline ? (
                    <textarea
                      id={field.key}
                      className="input min-h-[8.5rem] leading-relaxed"
                      value={value}
                      onChange={(event) => updateField(field.key, event.target.value)}
                      maxLength={field.limit}
                      readOnly={locked}
                      placeholder={field.placeholder}
                    />
                  ) : (
                    <input
                      id={field.key}
                      className="input"
                      value={value}
                      onChange={(event) => updateField(field.key, event.target.value)}
                      maxLength={field.limit}
                      readOnly={locked}
                      placeholder={field.placeholder}
                    />
                  )}
                  <div className="mt-1.5 flex justify-between gap-3 text-label">
                    <span className={error ? "text-rose" : "text-muted"}>
                      {error ?? field.hint}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>

          {formError && !locked ? (
            <div className="mt-5">
              <InlineNotice tone="error">{formError}</InlineNotice>
            </div>
          ) : null}

          <div className="mt-7 flex flex-wrap items-center justify-between gap-4 border-t border-line/60 pt-6">
            <p className="max-w-md text-label leading-relaxed text-muted">
              Everyone asks for the same deal: 250,000 EUR for 10 % equity. The jury scores the
              idea, not the slide design.
            </p>
            <button
              type="submit"
              className="btn btn-gold px-7 py-3.5"
              disabled={!complete || submitting || locked}
            >
              {submitting ? "Submitting..." : submittedAt ? "Update submission" : "Submit pitch"}
              {!submitting ? <span aria-hidden="true">→</span> : null}
            </button>
          </div>
        </div>
      </form>

      <aside className="space-y-4 xl:sticky xl:top-24 xl:self-start">
        <div className="panel rounded p-5">
          <p className="label label-gold">The deal</p>
          <p className="display mt-3 text-title leading-none">250,000</p>
          <p className="mt-1 text-label text-muted">EUR for 10 % equity</p>
          <p className="mt-3 text-label leading-relaxed text-muted">
            Implied post-money valuation: 2.5 million EUR. Fixed for every team.
          </p>
        </div>

        <div className="panel rounded p-5">
          <p className="label">In the room</p>
          <div className="mt-4 space-y-3">
            {investors.map((investor) => (
              <InvestorMini key={investor.id} investor={investor} />
            ))}
          </div>
        </div>

        <div className="panel-quiet rounded p-5">
          <p className="label">What lands</p>
          <ul className="mt-3 space-y-2.5 text-label leading-relaxed text-muted">
            <li className="flex gap-2">
              <span className="shrink-0 tabular-nums text-gold">01</span>
              A customer with a pain they already pay to solve.
            </li>
            <li className="flex gap-2">
              <span className="shrink-0 tabular-nums text-gold">02</span>
              A solution that is meaningfully different, not just faster.
            </li>
            <li className="flex gap-2">
              <span className="shrink-0 tabular-nums text-gold">03</span>
              A revenue model where the numbers can work.
            </li>
            <li className="flex gap-2">
              <span className="shrink-0 tabular-nums text-gold">04</span>
              AI at the core of an advantage a competitor cannot copy in a quarter.
            </li>
          </ul>
        </div>
      </aside>
    </div>
  );
}
