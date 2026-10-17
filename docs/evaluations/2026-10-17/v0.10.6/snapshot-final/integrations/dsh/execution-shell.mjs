// Preserve the pinned official executor, job lifecycle and sandbox semantics.
// A summarized pipeline must retain the failing command's exit status by default.
import { SandboxBashExecutor } from "@deepseek-ai/dsh-bash-sandbox";

export default class ChengxinBashExecutor extends SandboxBashExecutor {
  execute(spec) {
    return super.execute({
      ...spec,
      command: `set -o pipefail\n${spec.command}`,
    });
  }
}
