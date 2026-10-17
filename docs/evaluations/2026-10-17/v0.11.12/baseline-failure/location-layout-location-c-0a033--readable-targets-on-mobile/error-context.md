# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: location-layout.spec.mjs >> location controls have separate readable targets on mobile
- Location: tests/ui/location-layout.spec.mjs:9:3

# Error details

```
Error: expect(received).toEqual(expected) // deep equality

- Expected  - 1
+ Received  + 6

- Array []
+ Array [
+   Array [
+     "胸部",
+     "腹部",
+   ],
+ ]
```

# Page snapshot

```yaml
- generic [ref=e1]:
  - link "跳到主要内容" [ref=e2] [cursor=pointer]:
    - /url: "#main"
  - generic [ref=e3]:
    - text: · ·
    - generic [ref=e4]:
      - banner [ref=e5]:
        - generic [ref=e6]:
          - button "打开导航" [ref=e7] [cursor=pointer]
          - strong [ref=e10]: 问诊
        - generic [ref=e11]:
          - generic [ref=e12]: 本机服务已连接
          - generic [ref=e14]: 离线演示
      - main [ref=e15]:
        - region "问诊" [ref=e16]:
          - generic [ref=e17]:
            - heading "观察与记录" [level=1] [ref=e19]
            - button "问诊记录 8" [ref=e20] [cursor=pointer]:
              - text: 问诊记录
              - generic [ref=e21]: "8"
          - region "问诊对话" [ref=e23]:
            - generic [ref=e24]:
              - generic [ref=e27]:
                - strong [ref=e28]: 澄心问诊助手
                - generic "按用户原话报告的就诊对象，未核验身份。" [ref=e29]: 本人 · 本次问诊 · 离线演示
              - button "紧急求助" [ref=e31] [cursor=pointer]
            - generic [ref=e34]:
              - status [ref=e35]: 按图观察 · 可以跳过
              - button "展开对话记录" [ref=e36] [cursor=pointer]
            - generic "当前图示任务" [ref=e37]:
              - article [ref=e38]:
                - generic [ref=e39]:
                  - heading "指出最明显的不适部位" [active] [level=3] [ref=e41]
                  - generic [ref=e42]: 待临床审核
                - generic [ref=e43]:
                  - figure [ref=e44]:
                    - group "选择主观不适部位，不需要按压身体" [ref=e45]:
                      - generic [ref=e46]:
                        - img [aria-hidden] [ref=e47]:
                          - generic [ref=e51]:
                            - generic [ref=e52]: 正面
                            - generic [ref=e53]: 背面
                        - button "头/面部" [ref=e54] [cursor=pointer]
                        - button "胸部" [ref=e55] [cursor=pointer]
                        - button "腹部" [ref=e56] [cursor=pointer]
                        - button "背/腰部" [ref=e57] [cursor=pointer]
                        - button "四肢" [ref=e58] [cursor=pointer]
                      - paragraph [ref=e59]: 点击部位，再确认记录
                      - button "先选择部位" [disabled] [ref=e60]
                  - generic [ref=e61]:
                    - generic [ref=e62]: 1 / 2
                    - paragraph [ref=e66]: 看图找到最明显的位置。
                    - generic [ref=e67]:
                      - button "上一步" [disabled] [ref=e68]
                      - button "下一步" [ref=e69] [cursor=pointer]
                - paragraph [ref=e70]: 停止 / 跳过：无法确定位置时可跳过
                - group [ref=e71]:
                  - generic "为什么观察 · 使用限制" [ref=e72] [cursor=pointer]
                - paragraph [ref=e73]: 选一个部位，或用自己的话描述位置。
            - generic [ref=e74]:
              - generic "快捷回答" [ref=e75]:
                - button "其他部位" [ref=e76] [cursor=pointer]
                - button "说不清/跳过" [ref=e77] [cursor=pointer]
              - generic [ref=e78]: 按图示完成后记录结果；做不到也可以跳过。
              - generic [ref=e79]:
                - generic [ref=e80]: 输入你的描述
                - textbox "输入你的描述" [ref=e81]:
                  - /placeholder: 按自己的话说，也可以补充或更正…
                - button "发送回答" [ref=e82] [cursor=pointer]
              - paragraph [ref=e85]:
                - generic [ref=e86]: 0 / 2000
          - generic [ref=e87]: 研究原型用于信息采集与流程评审。请勿输入姓名、证件号等身份信息；紧急情况请立即拨打 120。
```

# Test source

```ts
  1  | import { test, expect } from "@playwright/test";
  2  | import { reachSelfCheck } from "../fixtures/self-check-drafts.mjs";
  3  | 
  4  | for (const [label, width, height] of [
  5  |   ["wide", 1440, 1080],
  6  |   ["mobile", 390, 844],
  7  |   ["short", 900, 648],
  8  | ])
  9  |   test(`location controls have separate readable targets on ${label}`, async ({
  10 |     page,
  11 |     request,
  12 |   }) => {
  13 |     await request.post("/api/settings", { data: { backend: "demo" } });
  14 |     await page.setViewportSize({ width, height });
  15 |     await page.goto("/");
  16 |     await reachSelfCheck(page);
  17 |     const targets = await page
  18 |       .locator("#active-task .body-pin")
  19 |       .evaluateAll((nodes) =>
  20 |         nodes.map((node) => {
  21 |           const r = node.getBoundingClientRect();
  22 |           return {
  23 |             text: node.textContent,
  24 |             x: r.x,
  25 |             y: r.y,
  26 |             width: r.width,
  27 |             height: r.height,
  28 |           };
  29 |         }),
  30 |       );
  31 |     const overlaps = [];
  32 |     for (let i = 0; i < targets.length; i++)
  33 |       for (let j = i + 1; j < targets.length; j++) {
  34 |         const a = targets[i],
  35 |           b = targets[j];
  36 |         if (
  37 |           Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x) > 0 &&
  38 |           Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y) > 0
  39 |         )
  40 |           overlaps.push([a.text, b.text]);
  41 |       }
  42 |     await page.screenshot({
  43 |       path: `.runtime/location-layout-20261017/baseline-${label}.png`,
  44 |       fullPage: true,
  45 |     });
  46 |     await test
  47 |       .info()
  48 |       .attach("location-targets", {
  49 |         body: JSON.stringify({ targets, overlaps }, null, 2),
  50 |         contentType: "application/json",
  51 |       });
  52 |     expect(targets).toHaveLength(5);
  53 |     expect(targets.every((t) => t.width >= 44 && t.height >= 44)).toBe(true);
> 54 |     expect(overlaps).toEqual([]);
     |                      ^ Error: expect(received).toEqual(expected) // deep equality
  55 |   });
  56 | 
```