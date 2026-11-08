// Deterministic providers must wait for the ID actually issued by the SDK.
export function waitForChild(body) {
  const ids = [
    ...JSON.stringify(body.messages).matchAll(/started subagent ([\w-]+)/g),
  ];
  if (!ids.length)
    throw new Error("Missing actual SDK child activation receipt");
  return {
    name: "wait_subagent",
    input: { agent_id: ids.at(-1)[1], timeout_ms: 10000 },
  };
}
