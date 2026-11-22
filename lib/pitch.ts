import type { Pitch, PitchFieldKey } from "./session-state";

export type PitchField = {
  key: PitchFieldKey;
  label: string;
  hint: string;
  limit: number;
  multiline: boolean;
  placeholder: string;
};

export const PITCH_FIELDS: readonly PitchField[] = [
  {
    key: "startupName",
    label: "Startup name",
    hint: "Make it memorable.",
    limit: 60,
    multiline: false,
    placeholder: "e.g. Loopline AI",
  },
  {
    key: "tagline",
    label: "One-line tagline",
    hint: "The idea in one breath.",
    limit: 140,
    multiline: false,
    placeholder: "e.g. The AI that turns online fashion returns into local resale.",
  },
  {
    key: "problem",
    label: "Problem",
    hint: "Who suffers, how often, and what does it cost them today?",
    limit: 2000,
    multiline: true,
    placeholder: "Describe the concrete pain you are solving.",
  },
  {
    key: "solution",
    label: "Solution",
    hint: "What exactly do you build, and why is it meaningfully better?",
    limit: 2000,
    multiline: true,
    placeholder: "Describe the AI product and its unfair advantage.",
  },
  {
    key: "customers",
    label: "Target customers",
    hint: "Be specific. A narrow customer beats everyone.",
    limit: 1200,
    multiline: true,
    placeholder: "Who pays, who uses, and how many of them are there?",
  },
  {
    key: "businessModel",
    label: "Business model",
    hint: "How money comes in and why the numbers can work.",
    limit: 2000,
    multiline: true,
    placeholder: "Pricing, revenue model, unit economics.",
  },
];

export function pitchIsComplete(pitch: Pitch): boolean {
  return PITCH_FIELDS.every((field) => {
    const value = pitch[field.key] ?? "";
    return value.trim().length > 0 && value.length <= field.limit;
  });
}
