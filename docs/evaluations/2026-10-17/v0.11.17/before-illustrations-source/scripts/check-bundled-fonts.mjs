import assert from "node:assert/strict";

export async function checkBundledFonts(page) {
  const sample = [
    ...new Set(
      (await page.locator("body").innerText()).match(/\p{Script=Han}/gu),
    ),
  ].join("");
  assert(sample.length > 0, "A Chinese UI sample is required");
  const cdp = await page.context().newCDPSession(page);
  const results = [];
  try {
    await cdp.send("DOM.enable");
    await cdp.send("CSS.enable");
    for (const [index, family] of [
      "Chengxin Sans SC",
      "Chengxin Serif SC",
    ].entries()) {
      const id = `bundled-font-probe-${index}`;
      const loaded = await page.evaluate(
        async ({ id, family, sample }) => {
          const element = document.createElement("span");
          element.id = id;
          element.textContent = sample;
          element.style.cssText = `position:fixed;left:-100000px;top:0;font:16px "${family}";white-space:nowrap`;
          document.body.append(element);
          const faces = await document.fonts.load(`16px "${family}"`, sample);
          await document.fonts.ready;
          element.getBoundingClientRect();
          return faces.map((face) => ({
            family: face.family,
            status: face.status,
          }));
        },
        { id, family, sample },
      );
      assert(
        loaded.length > 0 && loaded.every((face) => face.status === "loaded"),
        `Bundled face did not load: ${family}`,
      );
      const { root } = await cdp.send("DOM.getDocument");
      const { nodeId } = await cdp.send("DOM.querySelector", {
        nodeId: root.nodeId,
        selector: `#${id}`,
      });
      const { fonts } = await cdp.send("CSS.getPlatformFontsForNode", {
        nodeId,
      });
      const used = fonts.filter((font) => font.glyphCount > 0);
      const expectedFace = family.includes("Sans")
        ? "NotoSansCJKsc-Regular"
        : "NotoSerifCJKsc-Regular";
      assert(
        used.length > 0 && used.every((font) => font.isCustomFont),
        `Chinese glyphs fell back to system fonts: ${family}`,
      );
      assert(
        used.every((font) => font.postScriptName === expectedFace),
        `Unexpected rendered face: ${family}`,
      );
      assert(
        used.reduce((total, font) => total + font.glyphCount, 0) >=
          [...sample].length,
        `Missing rendered glyphs: ${family}`,
      );
      results.push({ family, sample, loaded, platform_fonts: used });
      await page.evaluate((id) => document.getElementById(id)?.remove(), id);
    }
  } finally {
    await page.evaluate(() =>
      document
        .querySelectorAll('[id^="bundled-font-probe-"]')
        .forEach((element) => element.remove()),
    );
    await cdp.detach();
  }
  return results;
}
