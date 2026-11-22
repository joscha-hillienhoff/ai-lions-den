/**
 * The three rules of the session, in the order a team needs them.
 * One source for the team lobby, the join screen and the projector lobby.
 */
export function scoringRules(investmentThreshold: number): string[] {
  return [
    `Each lion gives 1 to 10 points and invests ("I'm in") from ${investmentThreshold}.`,
    "Teams are ranked by how many lions invest; points break ties.",
    "The top teams reach the final and the audience picks the winner.",
  ];
}

export const SCORING_RULES_TITLE = "How you're scored";

/**
 * The live finale, announced up front so it is no surprise at the end.
 * One source for the lobbies, the team ideation page and the projector ideation screen.
 */
export const FINALE_HEADLINE = "The top 3 teams pitch their idea live on stage at the end.";
export const FINALE_DETAIL = "The audience picks the winner.";
