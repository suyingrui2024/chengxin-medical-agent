# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: location-layout.spec.mjs >> location controls have separate readable targets on short
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
                  - heading "指出最明显的不适部位" [active] [level=3] [ref=e79]
                  - generic [ref=e80]: 待临床审核
                - generic [ref=e81]:
                  - figure [ref=e82]:
                    - group "选择主观不适部位，不需要按压身体" [ref=e83]:
                      - generic [ref=e84]:
                        - img [aria-hidden] [ref=e85]:
                          - generic [ref=e89]:
                            - generic [ref=e90]: 正面
                            - generic [ref=e91]: 背面
                        - button "头/面部" [ref=e92] [cursor=pointer]
                        - button "胸部" [ref=e93] [cursor=pointer]
                        - button "腹部" [ref=e94] [cursor=pointer]
                        - button "背/腰部" [ref=e95] [cursor=pointer]
                        - button "四肢" [ref=e96] [cursor=pointer]
                      - paragraph [ref=e97]: 点击部位，再确认记录
                      - button "先选择部位" [disabled] [ref=e98]
                  - generic [ref=e99]:
                    - generic [ref=e100]: 1 / 2
                    - paragraph [ref=e104]: 看图找到最明显的位置。
                    - generic [ref=e105]:
                      - button "上一步" [disabled] [ref=e106]
                      - button "下一步" [ref=e107] [cursor=pointer]
                - paragraph [ref=e108]: 停止 / 跳过：无法确定位置时可跳过
                - group [ref=e109]:
                  - generic "为什么观察 · 使用限制" [ref=e110] [cursor=pointer]
                - paragraph [ref=e111]: 选一个部位，或用自己的话描述位置。
            - generic [ref=e112]:
              - generic "快捷回答" [ref=e113]:
                - button "其他部位" [ref=e114] [cursor=pointer]
                - button "说不清/跳过" [ref=e115] [cursor=pointer]
              - generic [ref=e116]: 按图示完成后记录结果；做不到也可以跳过。
              - generic [ref=e117]:
                - generic [ref=e118]: 输入你的描述
                - textbox "输入你的描述" [ref=e119]:
                  - /placeholder: 按自己的话说，也可以补充或更正…
                - button "发送回答" [ref=e120] [cursor=pointer]
              - paragraph [ref=e123]:
                - generic [ref=e124]: 0 / 2000
          - generic [ref=e125]: 研究原型用于信息采集与流程评审。请勿输入姓名、证件号等身份信息；紧急情况请立即拨打 120。
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