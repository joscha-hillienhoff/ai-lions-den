/**
 * UI-side phase list and shared field limits.
 *
 * These are presentation constants used by the React components. The phase
 * rules, minutes and labels that drive behavior live in backend/state.py; the
 * server also validates every field limit itself. A null `minutes` marks an
 * untimed phase.
 */
export const PHASES = [
  {
    id: "lobby",
    label: "Lobby",
    tagline: "Take your seats. The investors are arriving.",
    minutes: 5,
    screenHint: "Join the session",
  },
  {
    id: "ideation",
    label: "Ideation",
    tagline: "Build the idea. Fill in the pitch.",
    minutes: 20,
    screenHint: "Pitch in progress",
  },
  {
    id: "jury",
    label: "Jury round 1",
    tagline: "Four investors. Four independent verdicts.",
    minutes: 5,
    screenHint: "The jury is reading",
  },
  {
    id: "defense",
    label: "Defense",
    tagline: "Answer the questions. The pitch stays locked.",
    minutes: 10,
    screenHint: "Teams are answering",
  },
  {
    id: "verdict",
    label: "Final verdict",
    tagline: "The jury discusses. The room listens.",
    minutes: 5,
    screenHint: "The jury is deliberating",
  },
  {
    id: "leaderboard",
    label: "Leaderboard",
    tagline: "The stage is set.",
    minutes: 3,
    screenHint: "The final three",
  },
  {
    id: "finale",
    label: "Finale",
    tagline: "The top three pitch live.",
    minutes: null,
    screenHint: "Live from the stage",
  },
  {
    id: "voting",
    label: "Voting",
    tagline: "Scan the code. Pick the winner.",
    minutes: 3,
    screenHint: "Audience vote",
  },
  {
    id: "winner",
    label: "Winner",
    tagline: "The room has decided.",
    minutes: null,
    screenHint: "The reveal",
  },
] as const;

export type PhaseId = (typeof PHASES)[number]["id"];

export const PHASE_IDS = PHASES.map((phase) => phase.id);

export function phaseIndex(phase: PhaseId): number {
  return PHASE_IDS.indexOf(phase);
}

export function isAtLeast(phase: PhaseId, target: PhaseId): boolean {
  return phaseIndex(phase) >= phaseIndex(target);
}

export type JuryCallStatus = "pending" | "running" | "done" | "failed";

export type Pitch = {
  startupName: string;
  tagline: string;
  problem: string;
  solution: string;
  customers: string;
  businessModel: string;
};

export type PitchFieldKey = keyof Pitch;

export const DEFENSE_ANSWER_LIMIT = 1000;
export const DEFENSE_CHANGE_LIMIT = 1000;

export const TEAM_NAME_LIMIT = 40;
