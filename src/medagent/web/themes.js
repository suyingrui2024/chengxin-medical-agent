// Apply the saved appearance before CSS paints; clinical state is never touched.
(() => {
  const key = "chengxin.appearance.v1";
  const themes = ["forest", "ocean", "iris", "sand", "slate", "night"];
  const valid = (value) => value === "system" || themes.includes(value);
  const media = window.matchMedia("(prefers-color-scheme: dark)");
  let preference = "forest";
  try {
    const saved = window.chengxinDesktop?.appearance
      ? window.chengxinDesktop.appearance.initial
      : localStorage.getItem(key);
    if (valid(saved)) preference = saved;
  } catch {
    // A restricted browser can still change appearance for this window.
  }
  function apply() {
    const resolved =
      preference === "system"
        ? media.matches
          ? "night"
          : "forest"
        : preference;
    document.documentElement.dataset.theme = resolved;
    document.documentElement.dataset.appearance = preference;
    document.querySelector('meta[name="theme-color"]')?.setAttribute(
      "content",
      {
        forest: "#f5f6f2",
        ocean: "#f2f6fa",
        iris: "#f6f4fa",
        sand: "#f8f4ed",
        slate: "#f3f4f5",
        night: "#111722",
      }[resolved],
    );
  }
  apply();
  media.addEventListener("change", () => {
    if (preference === "system") apply();
  });
  window.addEventListener("storage", (event) => {
    if (event.key !== key && event.key !== null) return;
    preference = valid(event.newValue) ? event.newValue : "forest";
    apply();
    sync();
  });
  function sync() {
    document.querySelectorAll('[name="appearance"]').forEach((input) => {
      input.checked = input.value === preference;
    });
  }
  document.addEventListener("DOMContentLoaded", () => {
    const dialog = document.querySelector("#appearance-dialog");
    const trigger = document.querySelector("#open-appearance");
    const status = document.querySelector("#appearance-status");
    if (!dialog || !trigger || !status) return;
    trigger.addEventListener("click", () => {
      sync();
      dialog.showModal();
      dialog.querySelector("input:checked")?.focus();
    });
    dialog.querySelector(".appearance-close").addEventListener("click", () => {
      dialog.close();
    });
    dialog.addEventListener("click", (event) => {
      if (event.target !== dialog) return;
      const rect = dialog.getBoundingClientRect();
      if (
        event.clientX < rect.left ||
        event.clientX > rect.right ||
        event.clientY < rect.top ||
        event.clientY > rect.bottom
      )
        dialog.close();
    });
    let saveSequence = 0;
    dialog.addEventListener("change", async (event) => {
      const input = event.target;
      if (input.name !== "appearance" || !valid(input.value)) return;
      preference = input.value;
      apply();
      sync();
      const name = input.closest("label").querySelector("strong").textContent;
      const sequence = ++saveSequence;
      try {
        if (window.chengxinDesktop?.appearance)
          await window.chengxinDesktop.appearance.save(preference);
        else localStorage.setItem(key, preference);
        if (sequence !== saveSequence) return;
        status.textContent = `已应用「${name}」，下次打开会保留。`;
      } catch {
        if (sequence !== saveSequence) return;
        status.textContent = `已应用「${name}」。暂时无法保存偏好，本次窗口内有效。`;
      }
    });
    sync();
    if (window.chengxinDesktop?.appearance)
      status.textContent = "外观偏好保存在本机应用中。";
  });
})();
