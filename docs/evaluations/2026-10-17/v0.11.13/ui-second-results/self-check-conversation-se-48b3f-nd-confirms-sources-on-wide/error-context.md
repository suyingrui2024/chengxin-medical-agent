# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: self-check-conversation.spec.mjs >> self-check conversation resumes and confirms sources on wide
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
        - button "会谈记录 1" [ref=e21] [cursor=pointer]:
          - text: 会谈记录
          - generic [ref=e24]: "1"
      - generic [ref=e25]:
        - generic [ref=e26]: 最近问诊
        - generic [ref=e27]: "1"
      - button "·本人 · 头疼" [ref=e29] [cursor=pointer]
      - generic [ref=e30]:
        - generic [ref=e33]:
          - text: 本机工作空间
          - generic [ref=e34]: 会谈仅保留到服务关闭
        - generic [ref=e35]:
          - generic [ref=e36]: 研究原型
          - generic [ref=e37]: 能力协议 v1
    - generic [ref=e38]:
      - banner [ref=e39]:
        - generic [ref=e40]:
          - generic [ref=e41]: 工作空间
          - generic [ref=e42]: /
          - strong [ref=e43]: 问诊
        - generic [ref=e44]:
          - generic [ref=e45]: 本机服务已连接
          - generic [ref=e47]: 直接模型
      - main [ref=e48]:
        - region "问诊" [ref=e49]:
          - generic [ref=e50]:
            - heading "观察与记录" [level=1] [ref=e52]
            - button "问诊记录 5" [ref=e53] [cursor=pointer]:
              - text: 问诊记录
              - generic [ref=e56]: "5"
          - generic [ref=e57]:
            - region "问诊对话" [ref=e58]:
              - generic [ref=e59]:
                - generic [ref=e62]:
                  - strong [ref=e63]: 澄心问诊助手
                  - generic "按用户原话报告的就诊对象，未核验身份。" [ref=e64]: 本人 · 本次问诊 · 直接模型
                - generic [ref=e65]:
                  - generic [ref=e66]: 定向观察
                  - button "紧急求助" [ref=e67] [cursor=pointer]
              - generic [ref=e70]:
                - status [ref=e71]: 按图观察 · 可以跳过
                - button "展开对话记录" [ref=e72] [cursor=pointer]
              - generic "当前图示任务" [ref=e73]:
                - article [ref=e74]:
                  - generic [ref=e75]:
                    - heading "指出最明显的不适部位" [active] [level=3] [ref=e77]
                    - generic [ref=e78]: 待临床审核
                  - generic [ref=e79]:
                    - figure [ref=e80]:
                      - group "选择主观不适部位，不需要按压身体" [ref=e81]:
                        - generic [ref=e82]: 正面示意
                        - generic [ref=e89]:
                          - paragraph [ref=e90]: 请核对部位 · “右太阳穴附近疼”
                          - button "修改部位" [ref=e91] [cursor=pointer]
                          - button "核对并记录：头/面部" [ref=e92] [cursor=pointer]
                        - paragraph [ref=e93]: 已选择：头/面部（主观位置）
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
            - complementary "问诊记录" [ref=e112]:
              - generic [ref=e113]:
                - generic [ref=e114]: 本次问诊
                - generic [ref=e115]: 定向观察
              - strong [ref=e117]: 记录一次定向观察
              - generic [ref=e118]:
                - heading "已记录的信息" [level=2] [ref=e119]
                - generic [ref=e120]: 5 项
              - generic [ref=e121]:
                - generic [ref=e122]:
                  - generic [ref=e123]:
                    - generic [ref=e124]: 咨询对象
                    - button "更正" [ref=e125] [cursor=pointer]
                  - paragraph [ref=e126]: 本人
                - generic [ref=e127]:
                  - generic [ref=e128]:
                    - generic [ref=e129]: 年龄
                    - button "更正" [ref=e130] [cursor=pointer]
                  - paragraph [ref=e131]: 32 岁
                - generic [ref=e132]:
                  - generic [ref=e133]:
                    - generic [ref=e134]: 主要问题
                    - button "更正" [ref=e135] [cursor=pointer]
                  - paragraph [ref=e136]: 头疼
                - generic [ref=e137]:
                  - generic [ref=e138]: 紧急信号筛查
                  - paragraph [ref=e140]: 用户否认上述紧急信号
                - generic [ref=e141]:
                  - generic [ref=e142]:
                    - generic [ref=e143]: 用药与处理
                    - button "更正" [ref=e144] [cursor=pointer]
                  - paragraph [ref=e145]: 昨晚用过药物甲
              - generic [ref=e146]:
                - generic [ref=e147]: 已提供
                - generic [ref=e149]: 待确认 / 未知
              - paragraph [ref=e151]: 记录可以更正。没有提到的信息，不会自动记为“没有”。
          - generic [ref=e152]: 研究原型用于信息采集与流程评审。请勿输入姓名、证件号等身份信息；紧急情况请立即拨打 120。
```