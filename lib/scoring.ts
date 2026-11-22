/** Plain words for one investor score against the investment threshold. */
export function scoreReading(
  score: number,
  threshold: number,
  options: { scoreMax?: number; provisional?: boolean } = {},
): string {
  const scoreMax = options.scoreMax ?? 10;
  const provisional = options.provisional ?? false;
  if (score >= threshold) {
    return `${score}/${scoreMax}, ${provisional ? "would invest" : "invests"}`;
  }
  const missing = threshold - score;
  const points = missing === 1 ? "point" : "points";
  return provisional
    ? `${score}/${scoreMax}, would be ${missing} ${points} short of I'm in`
    : `${score}/${scoreMax}, ${missing} ${points} short of I'm in`;
}

/** The short tie-break note shown under the number of lions who invest. */
export function pointsTieBreak(total: number, max: number): string {
  return `${total}/${max} points, breaks ties`;
}
