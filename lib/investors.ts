/**
 * Investor identifiers used by the user interface.
 *
 * The personas themselves (names, avatars, portrait paths, perspectives and speaking styles)
 * live in the Python backend at backend/personas.py and reach the UI through the API.
 */
export type InvestorId = "growth" | "finance" | "customer" | "technology";

export const INVESTOR_IDS: readonly InvestorId[] = [
  "growth",
  "finance",
  "customer",
  "technology",
];
