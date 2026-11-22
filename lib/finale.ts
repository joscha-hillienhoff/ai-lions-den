export function clampFinalistIndex(index: number, count: number): number {
  const safeCount = Math.max(0, Math.floor(count));
  if (safeCount === 0) return 0;
  const numeric = Number.isFinite(index) ? Math.trunc(index) : 0;
  return Math.max(0, Math.min(safeCount - 1, numeric));
}

/** Move one finalist forward or back, staying inside the finalist list. */
export function stepFinalistIndex(
  current: number,
  direction: -1 | 1,
  count: number,
): number {
  return clampFinalistIndex(clampFinalistIndex(current, count) + direction, count);
}
