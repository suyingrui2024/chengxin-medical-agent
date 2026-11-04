# 对话全文 · 孙-界面

> 会话 `3c77512d-4b4c-4e77-ad45-150924aaf9dd` · 3 轮 · 生成于 2026-11-04T16:45:15.599Z
> 来源：`GET /api/sessions/3c77512d-4b4c-4e77-ad45-150924aaf9dd/snapshot` 的事件流（患者原话与助手回复逐字，未经 UI 截断）

## 轮 1 · phase=opening · consult_for

**患者**：（无输入，系统主动发起）
**助手**：先确认一下：这次是为谁咨询？

- 可选项：我自己 / 孩子 / 其他家人

## 轮 2 · phase=opening · sex

**患者**：我自己
**助手**：请问是男是女？

- 可选项：男 / 女
- 事件：fact.recorded(key=consult_for value=self assertion=present source=patient)；triage.assessed(level=undetermined triggers:{} assessed_at=2026-11-04T16:44:54.683390+00:00)

## 轮 3 · phase=opening · age

**患者**：后脑勺有点胀
**助手**：你多大？可直接写年龄，例如 32 岁。

- 事件：fact.recorded(key=sex value=后脑勺有点胀 assertion=present source=patient)；triage.assessed(level=undetermined triggers:{} assessed_at=2026-11-04T16:44:55.102366+00:00)
