// Page state and per-session drafts. Feature UIs own their internal state.

export const taskProgress = new Map();
export const calculationDrafts = new Map();
export const composerDrafts = new Map();
export const interactionDrafts = new Map();
export const subjectDrafts = new Map();
export const state = {
  sessionId: null,
  case: null,
  reply: null,
  events: [],
  sessions: [],
  followUps: [],
  dueReminders: [],
  tasks: [],
  settings: { backend: "demo" },
  subagentMetrics: null,
  modelCredentials: { available: false, has_saved_config: false },
  view: "consultation",
  busy: false,
  starting: false,
  priorityBusy: false,
  messageEpoch: 0,
  pendingMessages: new Map(),
  filter: "all",
  editing: null,
  workspaceMode: "patient",
  subagentAdvancedOpen: false,
  catalog: null,
  capabilityBusy: false,
  taskHistory: false,
  lastCorrection: null,
  shownCalculations: new Map(),
};

export const followUpState = { target: null, retriageId: null };
