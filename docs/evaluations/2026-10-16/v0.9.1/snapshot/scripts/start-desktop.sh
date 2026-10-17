#!/usr/bin/env bash
set -eu
project_dir="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$project_dir"
if [ "${1:-}" = "--live" ]; then
    shift
    PYTHONPATH="$project_dir/src" exec python3 "$project_dir/scripts/start_live_desktop.py" "$@"
fi
unset ELECTRON_RUN_AS_NODE
if [ -d "$HOME/.volta/bin" ]; then
    export PATH="$HOME/.volta/bin:$PATH"
fi
if [ -x "$project_dir/dist/desktop/Chengxin-0.9.1-linux-x64/Chengxin" ]; then
    exec "$project_dir/dist/desktop/Chengxin-0.9.1-linux-x64/Chengxin" "$@"
fi
if [ -x "$project_dir/node_modules/electron/dist/electron" ]; then
    exec "$project_dir/node_modules/electron/dist/electron" "$project_dir" "$@"
fi
message='桌面运行时尚未安装。请在 medical-agent 目录运行 npm ci --ignore-scripts 和 npm run desktop:setup。'
if command -v zenity >/dev/null 2>&1; then
    zenity --error --title='澄心问诊' --text="$message"
fi
printf '%s\n' "$message" >&2
exit 1
