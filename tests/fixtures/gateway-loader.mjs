// Test-only SDK substitution. The host worker and HTTP gateway remain real;
// this fixture does not start an SDK process or exercise filesystem isolation.
export function resolve(specifier, context, next) {
  if (specifier === "@deepseek-ai/dsh-sdk-client")
    return {
      url: new URL("./gateway-sdk.mjs", import.meta.url).href,
      shortCircuit: true,
    };
  return next(specifier, context);
}
