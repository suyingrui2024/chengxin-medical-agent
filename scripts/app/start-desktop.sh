#!/usr/bin/env bash
set -eu
project_dir="$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)"
cd "$project_dir"
unset ELECTRON_RUN_AS_NODE
if [ -d "$HOME/.volta/bin" ]; then
    export PATH="$HOME/.volta/bin:$PATH"
fi
for node22_bin in /opt/homebrew/opt/node@22/bin /usr/local/opt/node@22/bin; do
    if [ -x "$node22_bin/node" ]; then
        export PATH="$node22_bin:$PATH"
        break
    fi
done
if [ "${1:-}" = "--live" ]; then
    shift
    PYTHONPATH="$project_dir/src" exec python3 "$project_dir/scripts/app/start_live_desktop.py" "$@"
fi
if [ -x "$project_dir/dist/desktop/Chengxin-0.11.17-linux-x64/Chengxin" ]; then
    exec "$project_dir/dist/desktop/Chengxin-0.11.17-linux-x64/Chengxin" "$@"
fi
if [ -x "$project_dir/node_modules/electron/dist/Electron.app/Contents/MacOS/Electron" ]; then
    exec "$project_dir/node_modules/electron/dist/Electron.app/Contents/MacOS/Electron" "$project_dir" "$@"
fi
if [ -x "$project_dir/node_modules/electron/dist/electron" ]; then
    exec "$project_dir/node_modules/electron/dist/electron" "$project_dir" "$@"
fi
message='桌面运行时尚未安装。请在 medical-agent 目录运行 npm ci --ignore-scripts 和 npm run desktop:setup。'
if command -v zenity >/dev/null 2>&1; then
    zenity --error --title='知微' --text="$message"
fi
printf '%s\n' "$message" >&2
exit 1
