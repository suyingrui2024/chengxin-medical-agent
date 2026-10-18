// Per-runtime control handlers, shared by first-party Cordis plugins only.
const handlers = new WeakMap();

export function registerControl(root, handler) {
  if (handlers.has(root))
    throw new Error("Execution control already registered");
  handlers.set(root, handler);
  return () => handlers.delete(root);
}

export async function dispatchControl(root, parameters) {
  const handler = handlers.get(root);
  if (!handler) throw new Error("Execution control is not ready");
  if (
    !parameters ||
    !["GET", "POST"].includes(parameters.method) ||
    typeof parameters.path !== "string" ||
    parameters.path.length > 2048 ||
    !parameters.path.startsWith("/")
  )
    throw new Error("Invalid execution control request");
  if (
    parameters.scope !== undefined &&
    (parameters.method !== "POST" || parameters.path !== "/cancel")
  )
    throw new Error("Cancellation scope requires the stop endpoint");
  return handler(parameters.method, parameters.path, parameters.scope);
}
