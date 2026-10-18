// Reuse the pinned official job/output/cancellation implementation with a native shell.
import { LocalBashExecutor } from "@deepseek-ai/dsh-bash-local";
import { join } from "node:path";
import { nativePowerShellSource } from "./powershell-command.mjs";
import { taskBoundSpec } from "../dsh/execution-deadline.mjs";

export default class NativePowerShellExecutor extends LocalBashExecutor {
  async execute(spec) {
    const bounded = await taskBoundSpec(spec);
    const shell = join(
      process.env.SystemRoot,
      "System32/WindowsPowerShell/v1.0/powershell.exe",
    );
    // Each managed command uses a fresh PowerShell process. Avoid sharing an
    // asynchronously written analysis cache across overlapping, killable jobs.
    // Windows PowerShell 5.1 documents NUL as the supported cache opt-out.
    return this.executeArgv(
      {
        ...bounded,
        env: { ...bounded.env, PSModuleAnalysisCachePath: "NUL" },
      },
      [
        shell,
        "-NoLogo",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-NonInteractive",
        "-EncodedCommand",
        Buffer.from(nativePowerShellSource(spec), "utf16le").toString("base64"),
      ],
    );
  }
}
