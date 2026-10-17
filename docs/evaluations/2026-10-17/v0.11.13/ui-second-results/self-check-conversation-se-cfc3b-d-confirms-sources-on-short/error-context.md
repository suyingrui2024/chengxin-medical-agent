# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: self-check-conversation.spec.mjs >> self-check conversation resumes and confirms sources on short
- Location: tests/ui/self-check-conversation.spec.mjs:12:3

# Error details

```
Error: page.evaluate: TypeError: Cannot read properties of null (reading 'getBoundingClientRect')
    at eval (eval at evaluate (:370:30), <anonymous>:5:28)
    at Array.map (<anonymous>)
    at eval (eval at evaluate (:370:30), <anonymous>:3:117)
    at UtilityScript.evaluate (<anonymous>:372:16)
    at UtilityScript.<anonymous> (<anonymous>:1:44)
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
          - generic [ref=e49]: 直接模型
      - main [ref=e50]:
        - region "问诊" [ref=e51]:
          - generic [ref=e52]:
            - heading "观察与记录" [level=1] [ref=e54]
            - button "问诊记录 5" [ref=e55] [cursor=pointer]:
              - text: 问诊记录
              - generic [ref=e58]: "5"
          - region "问诊对话" [ref=e60]:
            - generic [ref=e61]:
              - generic [ref=e64]:
                - strong [ref=e65]: 澄心问诊助手
                - generic "按用户原话报告的就诊对象，未核验身份。" [ref=e66]: 本人 · 本次问诊 · 直接模型
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
                      - generic [ref=e84]: 正面示意
                      - generic [ref=e89]:
                        - paragraph [ref=e90]: 请核对部位 · “说不清具体部位”
                        - button "修改部位" [ref=e91] [cursor=pointer]
                        - button "核对并记录：其他部位" [ref=e92] [cursor=pointer]
                      - paragraph [ref=e93]: 已选择：其他部位（主观位置）
                  - group [ref=e94]:
                    - generic "▸ 分步说明 · 2 / 2" [ref=e95] [cursor=pointer]
                - paragraph [ref=e96]: 停止 / 跳过：无法确定位置时可跳过
                - group [ref=e97]:
                  - generic "为什么观察 · 使用限制" [ref=e98] [cursor=pointer]
                - paragraph [ref=e99]: 选一个部位，或用自己的话描述位置。
            - generic [ref=e100]:
              - generic "快捷回答" [ref=e101]:
                - button "其他部位" [ref=e102] [cursor=pointer]
                - button "说不清/跳过" [ref=e103] [cursor=pointer]
              - generic [ref=e104]:
                - generic [ref=e105]: 输入你的描述
                - textbox "输入你的描述" [ref=e106]:
                  - /placeholder: 按自己的话说，也可以补充或更正…
                - button "发送回答" [ref=e107] [cursor=pointer]
              - paragraph [ref=e110]:
                - generic [ref=e111]: 0 / 2000
          - generic [ref=e112]: 研究原型用于信息采集与流程评审。请勿输入姓名、证件号等身份信息；紧急情况请立即拨打 120。
```