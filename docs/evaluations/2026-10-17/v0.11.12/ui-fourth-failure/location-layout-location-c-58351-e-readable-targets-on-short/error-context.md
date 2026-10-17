# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: location-layout.spec.mjs >> location controls have separate readable targets on short
- Location: tests/ui/location-layout.spec.mjs:10:3

# Error details

```
Error: expect(received).toEqual(expected) // deep equality

- Expected  - 1
+ Received  + 6

- Array []
+ Array [
+   Object {
+     "selector": ".body-map-canvas",
+     "visible": false,
+   },
+ ]
```

# Page snapshot

```yaml
- generic [ref=e1]:
  - link "跳到主要内容" [ref=e2] [cursor=pointer]:
    - /url: "#main"
  - generic [ref=e3]:
    - complementary "主导航" [ref=e4]:
      - link "澄心 CHENGXIN HEALTH" [ref=e5] [cursor=pointer]:
        - /url: "#consultation"
        - generic [ref=e6]:
          - strong [ref=e7]: 澄心
          - generic [ref=e8]: CHENGXIN HEALTH
      - button "新的问诊 ↗" [ref=e9] [cursor=pointer]:
        - text: 新的问诊
        - generic [ref=e12]: ↗
      - generic [ref=e13]:
        - text: 界面模式
        - combobox "界面模式" [ref=e14]:
          - option "患者视图" [selected]
          - option "研发视图"
      - paragraph [ref=e15]: 工作空间
      - navigation [ref=e16]:
        - button "问诊" [ref=e17] [cursor=pointer]
        - button "会谈记录 3" [ref=e21] [cursor=pointer]:
          - text: 会谈记录
          - generic [ref=e24]: "3"
      - generic [ref=e25]:
        - generic [ref=e26]: 最近问诊
        - generic [ref=e27]: "3"
      - generic [ref=e28]:
        - button "·本人 · 头疼" [ref=e29] [cursor=pointer]
        - button "·本人 · 头疼" [ref=e30] [cursor=pointer]
        - button "·本人 · 头疼" [ref=e31] [cursor=pointer]
      - generic [ref=e32]:
        - generic [ref=e35]:
          - text: 本机工作空间
          - generic [ref=e36]: 会谈仅保留到服务关闭
        - generic [ref=e37]:
          - generic [ref=e38]: 研究原型
          - generic [ref=e39]: 能力协议 v1
    - generic [ref=e40]:
      - banner [ref=e41]:
        - generic [ref=e42]:
          - generic [ref=e43]: 工作空间
          - generic [ref=e44]: /
          - strong [ref=e45]: 问诊
        - generic [ref=e46]:
          - generic [ref=e47]: 本机服务已连接
          - generic [ref=e49]: 离线演示
      - main [ref=e50]:
        - region "问诊" [ref=e51]:
          - generic [ref=e52]:
            - heading "观察与记录" [level=1] [ref=e54]
            - button "问诊记录 8" [ref=e55] [cursor=pointer]:
              - text: 问诊记录
              - generic [ref=e58]: "8"
          - region "问诊对话" [ref=e60]:
            - generic [ref=e61]:
              - generic [ref=e64]:
                - strong [ref=e65]: 澄心问诊助手
                - generic "按用户原话报告的就诊对象，未核验身份。" [ref=e66]: 本人 · 本次问诊 · 离线演示
              - generic [ref=e67]:
                - generic [ref=e68]: 定向观察
                - button "紧急求助" [ref=e69] [cursor=pointer]
            - generic [ref=e72]:
              - status [ref=e73]: 按图观察 · 可以跳过
              - button "展开对话记录" [ref=e74] [cursor=pointer]
            - generic "当前图示任务" [ref=e75]:
              - article [ref=e76]:
                - generic [ref=e77]:
                  - heading "指出最明显的不适部位" [level=3] [ref=e79]
                  - generic [ref=e80]: 待临床审核
                - generic [ref=e81]:
                  - figure [ref=e82]:
                    - group "选择主观不适部位，不需要按压身体" [ref=e83]:
                      - generic [ref=e84]: 正面示意
                      - generic [ref=e91]:
                        - generic [ref=e92]:
                          - button "头/面部" [active] [pressed] [ref=e93] [cursor=pointer]
                          - button "胸部" [ref=e94] [cursor=pointer]
                          - button "腹部" [ref=e95] [cursor=pointer]
                          - button "背/腰部" [ref=e96] [cursor=pointer]
                          - button "四肢" [ref=e97] [cursor=pointer]
                        - button "记录：头/面部" [ref=e98] [cursor=pointer]
                      - paragraph [ref=e99]: 已选择：头/面部（主观位置）
                  - group [ref=e100]:
                    - generic "▸ 分步说明 · 1 / 2" [ref=e101] [cursor=pointer]
                - paragraph [ref=e102]: 停止 / 跳过：无法确定位置时可跳过
                - group [ref=e103]:
                  - generic "为什么观察 · 使用限制" [ref=e104] [cursor=pointer]
                - paragraph [ref=e105]: 选一个部位，或用自己的话描述位置。
            - generic [ref=e106]:
              - generic "快捷回答" [ref=e107]:
                - button "其他部位" [ref=e108] [cursor=pointer]
                - button "说不清/跳过" [ref=e109] [cursor=pointer]
              - generic [ref=e110]:
                - generic [ref=e111]: 输入你的描述
                - textbox "输入你的描述" [ref=e112]:
                  - /placeholder: 按自己的话说，也可以补充或更正…
                - button "发送回答" [ref=e113] [cursor=pointer]
              - paragraph [ref=e116]:
                - generic [ref=e117]: 0 / 2000
          - generic [ref=e118]: 研究原型用于信息采集与流程评审。请勿输入姓名、证件号等身份信息；紧急情况请立即拨打 120。
```

# Test source

```ts
  4   | export async function verifyLocationLayout(page, id, { shot } = {}) {
  5   |   const before = await degreeSnapshot(page, id);
  6   |   const targets = await page
  7   |     .locator("#active-task .body-pin")
  8   |     .evaluateAll((nodes) =>
  9   |       nodes.map((node) => {
  10  |         const r = node.getBoundingClientRect();
  11  |         return {
  12  |           text: node.textContent,
  13  |           x: r.x,
  14  |           y: r.y,
  15  |           width: r.width,
  16  |           height: r.height,
  17  |         };
  18  |       }),
  19  |     );
  20  |   const overlaps = [];
  21  |   for (let i = 0; i < targets.length; i++)
  22  |     for (let j = i + 1; j < targets.length; j++) {
  23  |       const a = targets[i],
  24  |         b = targets[j];
  25  |       if (
  26  |         Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x) > 0 &&
  27  |         Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y) > 0
  28  |       )
  29  |         overlaps.push([a.text, b.text]);
  30  |     }
  31  |   expect(targets).toHaveLength(5);
  32  |   expect(targets.every((t) => t.width >= 44 && t.height >= 44)).toBe(true);
  33  |   expect(overlaps).toEqual([]);
  34  |   const alternatives = await page
  35  |     .locator("#quick-replies button:visible")
  36  |     .evaluateAll((nodes) =>
  37  |       nodes.map((node) => {
  38  |         const r = node.getBoundingClientRect();
  39  |         return { width: r.width, height: r.height, bottom: r.bottom };
  40  |       }),
  41  |     );
  42  |   expect(alternatives).toHaveLength(2);
  43  |   expect(
  44  |     alternatives.every((item) => item.width >= 44 && item.height >= 44),
  45  |   ).toBe(true);
  46  |   for (const [label, value] of [
  47  |     ["头/面部", "head"],
  48  |     ["胸部", "chest"],
  49  |     ["腹部", "abdomen"],
  50  |     ["背/腰部", "back"],
  51  |     ["四肢", "limbs"],
  52  |   ]) {
  53  |     const choice = page
  54  |       .locator("#active-task .body-map")
  55  |       .getByRole("button", { name: label, exact: true });
  56  |     await choice.focus();
  57  |     await page.keyboard.press("Space");
  58  |     await expect(choice).toHaveAttribute("aria-pressed", "true");
  59  |     await expect(
  60  |       page.locator("#active-task .body-pin[aria-pressed='true']"),
  61  |     ).toHaveCount(1);
  62  |     await expect(
  63  |       page.getByRole("button", { name: `记录：${label}`, exact: true }),
  64  |     ).toBeEnabled();
  65  |     await expect(page.locator(".body-silhouette")).toHaveCount(1);
  66  |     await expect(page.locator(".body-selection-marker")).toHaveAttribute(
  67  |       "data-location",
  68  |       value,
  69  |     );
  70  |     await expect(page.locator(".body-map-orientations")).toHaveText(
  71  |       value === "back" ? "背面示意" : "正面示意",
  72  |     );
  73  |     await expect(page.locator(".body-selection-marker circle")).toHaveCount(
  74  |       value === "limbs" ? 4 : 1,
  75  |     );
  76  |     const geometry = await page.evaluate(() => {
  77  |       const pane = document
  78  |         .querySelector("#active-task")
  79  |         .getBoundingClientRect();
  80  |       const selectors = [
  81  |         "#active-task h3",
  82  |         ".body-map-canvas",
  83  |         ".body-map-options",
  84  |         ".map-confirm",
  85  |         "#message-input",
  86  |         "#urgent-help",
  87  |         "#view-consultation .page-footnote",
  88  |       ];
  89  |       return selectors.map((selector) => {
  90  |         const r = document.querySelector(selector).getBoundingClientRect();
  91  |         const scoped =
  92  |           selector.startsWith(".") || selector === "#active-task h3";
  93  |         return {
  94  |           selector,
  95  |           visible:
  96  |             r.top >= 0 &&
  97  |             r.bottom <= innerHeight &&
  98  |             r.left >= 0 &&
  99  |             r.right <= innerWidth &&
  100 |             (!scoped || (r.top >= pane.top && r.bottom <= pane.bottom)),
  101 |         };
  102 |       });
  103 |     });
> 104 |     expect(geometry.filter((item) => !item.visible)).toEqual([]);
      |                                                      ^ Error: expect(received).toEqual(expected) // deep equality
  105 |   }
  106 |   const after = await degreeSnapshot(page, id);
  107 |   expect(after.events).toEqual(before.events);
  108 |   expect(after.state.facts).toEqual(before.state.facts);
  109 |   if (shot) await page.screenshot({ path: shot, fullPage: true });
  110 |   return { targets, overlaps };
  111 | }
  112 | 
```