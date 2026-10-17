# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: self-check-conversation.spec.mjs >> self-check conversation resumes and confirms sources on mobile
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
    - text: · ·
    - generic [ref=e4]:
      - banner [ref=e5]:
        - generic [ref=e6]:
          - button "打开导航" [ref=e7] [cursor=pointer]
          - strong [ref=e10]: 问诊
        - generic [ref=e11]:
          - generic [ref=e12]: 本机服务已连接
          - generic [ref=e14]: 直接模型
      - main [ref=e15]:
        - region "问诊" [ref=e16]:
          - generic [ref=e17]:
            - heading "观察与记录" [level=1] [ref=e19]
            - button "问诊记录 5" [ref=e20] [cursor=pointer]:
              - text: 问诊记录
              - generic [ref=e21]: "5"
          - region "问诊对话" [ref=e23]:
            - generic [ref=e24]:
              - generic [ref=e27]:
                - strong [ref=e28]: 澄心问诊助手
                - generic "按用户原话报告的就诊对象，未核验身份。" [ref=e29]: 本人 · 本次问诊 · 直接模型
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
                      - generic [ref=e46]: 正面示意
                      - generic [ref=e53]:
                        - paragraph [ref=e54]: 请核对部位 · “右太阳穴附近疼”
                        - button "修改部位" [ref=e55] [cursor=pointer]
                        - button "核对并记录：头/面部" [ref=e56] [cursor=pointer]
                      - paragraph [ref=e57]: 已选择：头/面部（主观位置）
                  - group [ref=e58]:
                    - generic "▸ 分步说明 · 2 / 2" [ref=e59] [cursor=pointer]
                - paragraph [ref=e60]: 停止 / 跳过：无法确定位置时可跳过
                - group [ref=e61]:
                  - generic "为什么观察 · 使用限制" [ref=e62] [cursor=pointer]
                - paragraph [ref=e63]: 选一个部位，或用自己的话描述位置。
            - generic [ref=e64]:
              - generic "快捷回答" [ref=e65]:
                - button "其他部位" [ref=e66] [cursor=pointer]
                - button "说不清/跳过" [ref=e67] [cursor=pointer]
              - generic [ref=e68]:
                - generic [ref=e69]: 输入你的描述
                - textbox "输入你的描述" [ref=e70]:
                  - /placeholder: 按自己的话说，也可以补充或更正…
                - button "发送回答" [ref=e71] [cursor=pointer]
              - paragraph [ref=e74]:
                - generic [ref=e75]: 0 / 2000
          - generic [ref=e76]: 研究原型用于信息采集与流程评审。请勿输入姓名、证件号等身份信息；紧急情况请立即拨打 120。
```