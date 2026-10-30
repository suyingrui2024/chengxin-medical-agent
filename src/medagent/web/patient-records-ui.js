// Patient records UI entry point. Implementation is split into
// primitives (DOM helpers / interpretation / baseline banner), a core
// section (summary, timeline, baseline, members) and a detail section
// (observations, recognition, interpretation, event binding).
export {
  createInterpretationSection,
  createBaselineBanner,
  RECOGNITION_POLL,
} from "./patient-records-primitives.js";
import { installRecordsCore } from "./patient-records-core.js";
import { installRecordsDetail } from "./patient-records-detail.js";

export function createPatientRecordsUI(api, toast, options = {}) {
  const deps = {};
  const core = installRecordsCore({ api, toast, options, deps });
  const detail = installRecordsDetail({
    api,
    toast,
    options,
    state: core.state,
    refresh: core.refresh,
    setFormBusy: core.setFormBusy,
    ensurePatient: core.ensurePatient,
    renderTimeline: core.renderTimeline,
    ensureRecordsSections: core.ensureRecordsSections,
  });
  deps.openDetail = detail.openDetail;
  detail.bind();
  return { refresh: core.refresh, openDetail: detail.openDetail };
}
