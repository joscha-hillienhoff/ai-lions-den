import type { InvestorId } from "./investors";
import type {
  JuryCallStatus,
  PhaseId,
  Pitch,
} from "./session-state";

export type SessionView = {
  phase: PhaseId;
  label: string;
  tagline: string;
  screenHint: string;
  /** Phase length in minutes, or null for an untimed phase (finale, winner). */
  minutes: number | null;
  phaseStartedAt: number;
  /** Countdown deadline in epoch ms, or null for an untimed phase. */
  phaseEndsAt: number | null;
  /** Orientation deadline in epoch ms of the spotlighted finale pitch (about 3 min), or null. */
  pitchEndsAt: number | null;
  /** True in the finale while the moderator has not started the pitch clock yet. */
  pitchNotStarted: boolean;
  serverNow: number;
  /** Server deadline in epoch ms of the jury round's automatic advance, or null. */
  autoAdvanceAt: number | null;
  /** The moderator held the countdown or stepped back here: advance manually. */
  autoAdvanceHeld: boolean;
  revision: number;
  /** A final score at or above this value means the lion invests. */
  investmentThreshold: number;
  /** The top of the per-investor score scale. */
  scoreMax: number;
};

export type PublicInvestor = {
  id: InvestorId;
  name: string;
  role: string;
  title: string;
  /** The one short lens tag shown next to the name everywhere. */
  focus: string;
  /** One short line that introduces this investor in the projector lobby. */
  intro: string;
  investorType: string;
  avatar: string;
  image: string;
  accent: string;
  perspective: string;
  typicalConcern: string;
};

export type TeamRound1View = Record<
  InvestorId,
  {
    status: JuryCallStatus;
    score: number | null;
    assessment: string | null;
    question: string | null;
    error: string | null;
  }
>;

export type TeamVerdictView = Record<
  InvestorId,
  {
    status: JuryCallStatus;
    statement: string | null;
    score: number | null;
    previousScore: number | null;
    decision: "in" | "out" | null;
    error: string | null;
  }
>;

export type RankedView = {
  rank: number;
  teamId: string;
  teamName: string;
  startupName: string;
  tagline: string;
  total: number;
  inCount: number;
  isTopThree: boolean;
};

export type RankedListView = Pick<
  RankedView,
  "rank" | "teamId" | "teamName" | "startupName" | "total" | "inCount"
>;

/** Why the audience winner won, derived by the backend at the reveal. */
export type WinnerBasis =
  | "audienceVotes"
  | "juryInCount"
  | "juryTotal"
  | "juryRanking";

/** A finalist; the jury figures are only present at the reveal (and for the moderator). */
export type FinalistView = {
  teamId: string;
  teamName: string;
  startupName: string;
  tagline: string;
  rank?: number;
  total?: number;
  inCount?: number;
  audienceVotes?: number;
  audienceShare?: number;
  isWinner?: boolean;
};

export type TeamStateView = {
  session: SessionView;
  investors: PublicInvestor[];
  team: {
    id: string;
    name: string;
    pitch: Pitch;
    pitchUpdatedAt: number | null;
    submittedAt: number | null;
    /** The jury already read this pitch, so it can no longer be changed. */
    pitchLocked: boolean;
    eligible: boolean | null;
    round1: TeamRound1View;
    defense: {
      answers: string[];
      oneChange: string;
      submittedAt: number | null;
      /** The final verdict already read these answers, so they can no longer be changed. */
      locked: boolean;
    };
    verdict: TeamVerdictView;
    verdictCompletedAt: number | null;
  } | null;
  ranking: RankedListView[];
  placement: RankedListView | null;
  finalists: FinalistView[];
  winnerBasis: WinnerBasis | null;
  votesCast: number;
  selectedFinalistIndex: number;
};

export type ScreenStateView = {
  session: SessionView;
  investors: PublicInvestor[];
  joinUrl: string;
  joinShortUrl: string;
  qrDataUrl: string;
  voteUrl: string;
  voteQrDataUrl: string;
  votesCast: number;
  selectedFinalistIndex: number;
  maxTeams: number;
  counts: {
    joined: number;
    submitted: number;
    eligible: number;
    round1Done: number;
    defenseSubmitted: number;
    verdictDone: number;
  };
  round1ByInvestor: Record<InvestorId, number>;
  verdictByInvestor: Record<InvestorId, number>;
  teams: Array<{
    id: string;
    name: string;
    submitted: boolean;
    eligible: boolean;
    round1Done: boolean;
    defenseSubmitted: boolean;
    verdictDone: boolean;
  }>;
  ranking: RankedListView[];
  finalists: FinalistView[];
  winnerBasis: WinnerBasis | null;
};

export type VoteStateView = {
  session: SessionView;
  open: boolean;
  finalists: FinalistView[];
  winnerBasis: WinnerBasis | null;
  votesCast: number;
  hasVoted: boolean;
  votedTeamId: string | null;
};

export type ModeratorRound1Entry = {
  status: JuryCallStatus;
  score?: number | null;
  assessment?: string | null;
  question?: string | null;
  error?: string | null;
  startedAt?: number | null;
  finishedAt?: number | null;
};

export type ModeratorVerdictEntry = {
  status: JuryCallStatus;
  statement?: string | null;
  score?: number | null;
  previousScore?: number | null;
  decision?: "in" | "out" | null;
  error?: string | null;
  startedAt?: number | null;
  finishedAt?: number | null;
};

export type ModeratorTeamView = {
  id: string;
  name: string;
  createdAt: number;
  pitch: Pitch;
  submittedAt: number | null;
  eligible: boolean | null;
  pitchComplete: boolean;
  round1: Record<InvestorId, ModeratorRound1Entry>;
  defense: {
    answers: string[];
    oneChange: string;
    submittedAt: number | null;
  };
  verdict: Record<InvestorId, ModeratorVerdictEntry>;
  verdictCompletedAt: number | null;
  inCount: number | null;
  total: number | null;
  rank: number | null;
  errorCount: number;
};

export type ModeratorStateView = {
  session: SessionView;
  maxTeams: number;
  mode: {
    demo: boolean;
    provider: "demo" | "openai";
    model: string;
    baseUrl: string | null;
    publicBaseUrl: string | null;
    moderatorPasswordConfigured: boolean;
  };
  investors: PublicInvestor[];
  teams: ModeratorTeamView[];
  ranking: RankedListView[];
  votesCast: number;
  finale: {
    selectedIndex: number;
    finalists: Array<{
      teamId: string;
      teamName: string;
      startupName: string;
      tagline: string;
    }>;
  };
};

/** One archived run in the moderator's "Past runs" list. */
export type ArchiveSummary = {
  id: string;
  /** Epoch milliseconds when Reset saved the run. */
  archivedAt: number;
  phase: PhaseId;
  teamCount: number;
  winner: string | null;
  votesCast: number;
};

export type ArchivedTeamView = ModeratorTeamView & {
  audienceVotes: number;
};

/** A read-only past run, rendered with the same team shape as the live view. */
export type ArchivedRunView = {
  id: string;
  archivedAt: number;
  teamCount: number;
  votesCast: number;
  phase: PhaseId;
  session: {
    phase: PhaseId;
    label: string;
    createdAt: number;
    phaseStartedAt: number;
  };
  investors: PublicInvestor[];
  teams: ArchivedTeamView[];
  ranking: Array<RankedListView & { audienceVotes: number }>;
  winner: {
    teamId: string;
    teamName: string;
    startupName: string;
    audienceVotes: number;
    audienceShare: number;
  } | null;
};
