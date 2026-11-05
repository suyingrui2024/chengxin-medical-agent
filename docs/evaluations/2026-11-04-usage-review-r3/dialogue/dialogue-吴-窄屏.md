# 对话全文 · 吴-窄屏

> 会话 `4dd73130-7ddb-450b-8407-3c0ed8e4ffdf` · 5 轮 · 生成于 2026-11-05T01:51:33.375Z
> 来源：`GET /api/sessions/4dd73130-7ddb-450b-8407-3c0ed8e4ffdf/snapshot` 的事件流（患者原话与助手回复逐字，未经 UI 截断）

## 轮 1 · phase=opening · consult_for
**患者**：（无输入，系统主动发起）
**助手**：先确认一下：这次是为谁咨询？
- 可选项：我自己 / 孩子 / 其他家人

## 轮 2 · phase=opening · sex
**患者**：我自己
**助手**：请问是男是女？
- 可选项：男 / 女
- 事件：fact.recorded(key=consult_for value=self assertion=present source=patient)；triage.assessed(level=undetermined triggers:{} assessed_at=2026-11-05T01:51:29.744885+00:00)

## 轮 3 · phase=opening · age
**患者**：男
**助手**：你多大？可直接写年龄，例如 32 岁。
- 事件：fact.recorded(key=sex value=male assertion=present source=patient)；triage.assessed(level=undetermined triggers:{} assessed_at=2026-11-05T01:51:30.645491+00:00)

## 轮 4 · phase=opening · chief_complaint
**患者**：28岁
**助手**：这次最想解决的一个问题是什么？请按自己的话说，我会逐步追问。
- 事件：fact.recorded(key=age_years value=28 assertion=present source=patient)；triage.assessed(level=undetermined triggers:{} assessed_at=2026-11-05T01:51:31.245517+00:00)

## 轮 5 · phase=emergency_screen · emergency_screen
**患者**：嗓子疼三天
**助手**：你说嗓子疼三天，再问一句：此刻有没有以下任一情况：叫不醒或意识明显异常、抽搐、嘴唇发紫、严重呼吸困难、持续压榨样胸痛，或大出血？
- 可选项：有任一项 / 都没有 / 不确定
- 事件：fact.recorded(key=chief_complaint value=嗓子疼三天 assertion=present source=patient)；triage.assessed(level=undetermined triggers:{} assessed_at=2026-11-05T01:51:31.862498+00:00)

