const { contextBridge, ipcRenderer } = require("electron");

// No paths, shell commands, credential reads or generic IPC are exposed here.
// Credentials can only be submitted for native encryption or cleared; saved
// plaintext is never returned to the renderer.
contextBridge.exposeInMainWorld("chengxinDesktop", {
  openProject: () => ipcRenderer.invoke("execution:open-project"),
  // Due-center click-through from main-process notifications. The
  // renderer only receives a target string; no patient data crosses IPC.
  dueNotifications: {
    onOpen: (callback) =>
      ipcRenderer.on("due-notifications:open", (_event, target) =>
        callback(target),
      ),
  },
  appearance: {
    initial: ipcRenderer.sendSync("appearance:read"),
    save: (value) => ipcRenderer.invoke("appearance:save", value),
  },
  modelCredentials: {
    status: () => ipcRenderer.invoke("model-credentials:status"),
    save: (config) => ipcRenderer.invoke("model-credentials:save", config),
    clear: () => ipcRenderer.invoke("model-credentials:clear"),
  },
});
