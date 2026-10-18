export function resolve(specifier, context, next) {
  if (specifier === '@deepseek-ai/dsh-sdk-client' && context.parentURL?.endsWith('/integrations/dsh/execution-worker.mjs'))
    return {url: new URL('./native-sdk.mjs', import.meta.url).href, shortCircuit: true};
  return next(specifier, context);
}
