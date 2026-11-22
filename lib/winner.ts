import type { WinnerBasis } from "./views";

/** The general rule, short enough to read on the projector from the back of the room. */
export const WINNER_RULE =
  "The audience picks the winner. On a vote tie the jury ranking decides: more lions in first, then more points.";

/** Plain words for how this session's winner was decided. */
export function winnerExplanation(basis: WinnerBasis | null, votesCast: number): string {
  if (basis === "audienceVotes") {
    return "Won with the most audience votes.";
  }
  if (basis === "juryInCount") {
    return "An audience-vote tie. More lions invested in this team.";
  }
  if (basis === "juryTotal") {
    return "An audience-vote tie and the same number of lions in. The higher jury total decided it.";
  }
  if (basis === "juryRanking") {
    return votesCast === 0
      ? "No audience votes. The jury ranking decided it."
      : "An audience-vote tie with the same lions in and the same points. The jury ranking decided it.";
  }
  return "The audience decided.";
}
