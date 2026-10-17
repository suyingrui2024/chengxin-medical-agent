const { contextBridge, ipcRenderer } = require("electron");

// No paths, shell commands, credentials or generic IPC are exposed here.
// The main process obtains the path from the native picker itself.
contextBridge.exposeInMainWorld("chengxinDesktop", {
  openProject: () => ipcRenderer.invoke("execution:open-project"),
  appearance: {
    initial: ipcRenderer.sendSync("appearance:read"),
    save: (value) => ipcRenderer.invoke("appearance:save", value),
  },
});
