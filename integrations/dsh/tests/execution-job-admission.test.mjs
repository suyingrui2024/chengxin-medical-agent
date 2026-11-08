import test from "node:test";
import assert from "node:assert/strict";
import { JobAdmission } from "../execution-job-admission.mjs";
import { RootAdmission } from "../execution-admission.mjs";

const oldTask = "11111111-1111-4111-8111-111111111111";
const newTask = "22222222-2222-4222-8222-222222222222";
const notice = (id = "job-notice") => ({
  id,
  source: { kind: "tool-jobs", form: "notice" },
  content: [
    {
      type: "text",
      text: "background job bash-known (bash: test) finished [status: completed]. Read its output with job_output.",
    },
  ],
});

function recordJob(jobs, session, turn = 1) {
  jobs.observe(session, {
    type: "tool/call",
    data: { turn, callId: "root-call" },
  });
  jobs.observe(session, {
    type: "job/registered",
    data: {
      jobId: "bash-known",
      owner: session.id,
      toolCall: { callId: "inner-call", rootCallId: "root-call" },
    },
  });
}

test("a job notice uses the creating root tool call, including nested PTC calls", () => {
  const jobs = new JobAdmission(),
    session = { id: "root" };
  jobs.admitted(session, 1, oldTask);
  recordJob(jobs, session);
  jobs.admitted(session, 2, newTask);
  assert.deepEqual(jobs.reference(session, notice()), { task_id: oldTask });
  recordJob(jobs, session, 2);
  assert.deepEqual(jobs.reference(session, notice()), { task_id: oldTask });
});

test("printed text, unknown jobs, foreign registrations and copied sessions confer no authority", () => {
  const jobs = new JobAdmission(),
    session = { id: "root" };
  jobs.admitted(session, 1, oldTask);
  recordJob(jobs, session);
  assert.deepEqual(jobs.reference({ ...session }, notice()), {});
  assert.deepEqual(
    jobs.reference(session, { ...notice(), source: { kind: "user" } }),
    {},
  );
  const unknown = notice();
  unknown.content[0].text = "background job bash-unknown finished";
  assert.deepEqual(jobs.reference(session, unknown), {});
  const foreign = { id: "foreign" };
  jobs.admitted(foreign, 1, newTask);
  jobs.observe(foreign, {
    type: "tool/call",
    data: { turn: 1, callId: "root-call" },
  });
  jobs.observe(foreign, {
    type: "job/registered",
    data: {
      jobId: "bash-known",
      owner: "root",
      toolCall: { rootCallId: "root-call" },
    },
  });
  assert.deepEqual(jobs.reference(foreign, notice()), {});
});

test("unadmitted calls and a changed SDK notice envelope fail closed", () => {
  const jobs = new JobAdmission(),
    session = { id: "root" };
  recordJob(jobs, session);
  assert.deepEqual(jobs.reference(session, notice()), {});
  jobs.admitted(session, 1, oldTask);
  recordJob(jobs, session);
  const truncated = notice();
  truncated.content[0].text = "[notice truncated]";
  assert.deepEqual(jobs.reference(session, truncated), {});
});

test("an old job wakeup mixed with a new prompt is deferred and retains its old task", async () => {
  const deferred = [],
    admitted = [],
    session = { id: "root" };
  const admission = new RootAdmission({
    resolveOwner: async () => null,
    admit: async (value) => {
      admitted.push(value);
      return true;
    },
  });
  const agent = {
    id: "root",
    session,
    followup: (message) => deferred.push(message.id),
  };
  admission.root = agent.id;
  admission.jobs.admitted(session, 1, oldTask);
  recordJob(admission.jobs, session);
  const job = notice(),
    user = { id: "user", source: { kind: "user" } };
  admission.register(newTask);
  admission.inserted(agent, job);
  admission.inserted(agent, user);
  const signal = new AbortController().signal;
  const decision = await admission.decide(
    { agent, messages: [job, user], turn: 2, signal },
    async () => ({ kind: "enter", messages: [job, user] }),
  );
  assert.deepEqual(decision.messages, [user]);
  assert.deepEqual(deferred, [job.id]);
  assert.equal(admitted[0].task_id, newTask);
  await admission.decide(
    { agent, messages: [job], turn: 3, signal },
    async () => ({ kind: "enter", messages: [job] }),
  );
  assert.equal(admitted[1].task_id, oldTask);
});

test("a stopped task's late job notice is removed without waking a new task", () => {
  const admission = new RootAdmission({});
  const session = { id: "root" },
    removed = [];
  const agent = {
    id: "root",
    session,
    inbox: { remove: (id) => removed.push(id), nextTurn: [], nextStep: [] },
  };
  admission.root = agent.id;
  admission.jobs.admitted(session, 1, oldTask);
  recordJob(admission.jobs, session);
  admission.revoke({ task_id: oldTask, sessions: [] }, [agent]);
  admission.inserted(agent, notice());
  assert.deepEqual(removed, ["job-notice"]);
});
