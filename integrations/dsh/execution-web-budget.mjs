// Session-level web governance for the execution workbench (design doc §3):
// search and fetch share one per-session call budget inside this process, on
// top of the per-turn gateway budget and the search provider's own maxUses.
// Counts are process-local and keyed by the initiating session id; calls with
// no initiating session are not counted and always allowed.
export const WEB_SESSION_CAP = 30;

const counts = new Map();

// Claim one web call for the session. Returns false once the session has
// reached WEB_SESSION_CAP; the caller is responsible for reporting the
// refusal (event + error) so each tool surface words it consistently.
export function claimWebCall(sessionId) {
  if (!sessionId) return true;
  const used = counts.get(sessionId) || 0;
  if (used >= WEB_SESSION_CAP) return false;
  counts.set(sessionId, used + 1);
  return true;
}
