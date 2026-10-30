// Structured UI entry point. Implementation is split into primitives
// (helpers/validators/envelopes), a renderer core and per-family installers.
export {
  UI_VERSION,
  replyEnvelope,
  calculationEnvelope,
} from "./structured-ui-primitives.js";
export { createRenderer } from "./structured-ui-renderer.js";
