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
  // Font-size preference (design: 视觉改版 §2.1): small | standard | large,
  // stored separately from the palette and applied before paint.
  const fontKey = "chengxin.fontscale.v1";
  const fontSizes = ["small", "standard", "large"];
  let fontScale = "standard";
  try {
    const savedFont = localStorage.getItem(fontKey);
    if (fontSizes.includes(savedFont)) fontScale = savedFont;
  } catch {
    // Restricted storage: the dialog still applies for this window.
  }
  document.documentElement.dataset.fontScale = fontScale;
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
    // Appearance now lives in the full-area settings view
    // (design: docs/设置视图改造方案.md); the controls stay in the DOM,
    // so the wiring attaches to the view instead of a modal dialog.
    const dialog = document.querySelector("#view-settings");
    const status = document.querySelector("#appearance-status");
    if (!dialog || !status) return;
    sync();
    const syncFontScale = () => {
      document.querySelectorAll('[name="fontscale"]').forEach((input) => {
        input.checked = input.value === fontScale;
      });
    };
    syncFontScale();
    dialog.addEventListener("change", (event) => {
      const input = event.target;
      if (input.name !== "fontscale" || !fontSizes.includes(input.value))
        return;
      fontScale = input.value;
      document.documentElement.dataset.fontScale = fontScale;
      try {
        localStorage.setItem(fontKey, fontScale);
        status.textContent = "字号已调整，下次打开会保留。";
      } catch {
        status.textContent = "字号已调整，本次窗口内有效。";
      }
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
