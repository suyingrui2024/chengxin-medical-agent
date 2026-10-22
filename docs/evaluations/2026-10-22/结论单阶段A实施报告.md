# 结论单阶段 A 实施报告（2026-10-22）

依据：《问诊结论单设计文档》v1.1（docs/问诊结论单设计文档.md）§9 阶段 A。
范围：骨架与非鉴别区块。鉴别方向（D2）按设计以影子模式生成（患者不可见），
等待临床审定（D1）与阶段 B 评测门。

## 交付物

- `src/medagent/conclusion.py`：ConclusionComposer。纯确定性，无模型调用；
  关闭时推理一遍（RuleBasedProposer + validate_workspace），去向映射、
  方向选取与 certainty 映射、缺口排序、居家内容匹配、禁用语 lint、
  fail-open 语义（异常不阻塞关闭，I3）。
- 挂载：`ConsultationEngine._record_reply` 单点后处理（双引擎共用，
  且位于 turn.agent_reply 事件序列化之前——事件重放能带回结论单，
  不变量 I5）；仅 publication gate 放行且 closed_reason ∈
  {confirmed_by_user, urgent_route} 时触发；急症路径不生成（I/§1.4）。
- 序列化贯通：AgentReply.conclusion / CaseState.conclusion 正式字段
  （声明 + to_dict），与 follow_up 完全同构；presentation 新增
  conclusion 块，顺序为 conclusion → summary → follow_up；
  conclusion.generated 事件留痕（含内容版本与弃权标记）。
- 内容资产：`content/self_care.json` v1.0（generic / respiratory /
  chest × routine / urgent 六组，带 reviewed_by/at 元数据，
  标注 engineering-v1）；`content/differentials.json` 为 6 个假设补
  patient_label，并为呼吸道路径触发词补口语词（嗓子/流鼻涕/鼻塞）、
  为 uri 的 support 补"嗓子"。
- 前端：structured-ui 新增 conclusion 块渲染器（校验器同 follow_up
  严格程度）；摘要卡"记录模式"（结论单存在时隐藏其 route/safety，
  §7 去重）；**聚焦关闭视图把最新结论单与随访卡标为 current-outcome
  一并呈现**——否则两者会被聚焦视图的 display:none 规则藏进折叠的
  对话记录（这正是 1cb56b4 后随访卡在聚焦视图实际不可见的原因，
  本次一并修复）；结论单标题用独立 12px 样式（未复用 8px 的
  task-kicker）。

## 评测与测试

- 新增 `scripts/evaluation/medical/evaluate_conclusion_sheet.py` 与
  工程病例集 `tests/fixtures/conclusion_cases_v0.json`（16 例，
  review_status: unreviewed）：去向一致率 1.000、must-not-miss 召回
  1.000、有害内容 0、16/16 通过；结论硬门已并入
  `evaluate_release_gate.py`，发布门整体 PASS
  （报告：docs/evaluations/2026-10-22/conclusion-cases-v0.json）。
- 新增 `tests/test_conclusion.py` 12 例：composer 单位行为（急症
  排除/弃权/逐字引用/名称门/版本递增/lint 常量）、return_now_if 与
  摘要同源漂移守卫、确定性引擎三形态、自适应引擎平价。
- 全量：458 passed, 72 skipped（基线 446 + 12），无回归。
- 实机走查（本地无头 Chromium）：普通关闭页结论单居首（去向横幅、
  缺口含体温影响、居家三组、安全警示展开且全页仅一份、随访卡可见、
  摘要卡为纯记录）；急症输入仍在主诉轮直达 120 急救屏，结论单不介入；
  截图 viz-review/after/B2-closed-conclusion.png、B3-closed-urgent.png。

## 与设计文档的偏差（均已回写设计文档 v1.2 §12）

1. **弃权阈值再校准**：设计 §5.4 的"证据合计 < 2 则弃权"在真实
   完整问诊状态下几乎必然触发——提议器对"仅凭主诉进入、判别问题
   已答完"的假设统一降为 unlikely，证据列表天然稀疏。改为：
   无可选方向才弃权（serious 的 unlikely 仍不进 D2，common 的
   unlikely 且有逐字证据可作 possible 呈现）。理由：门槛应由
   评测集而非想象的证据密度定标；现口径下 16 例全过且 must-not-miss
   不受影响。
2. **开关拆分**：§8.3 的单一 mode 开关拆为 MEDAGENT_CONCLUSION_SHEET
   （off/shadow/on，默认 on）与 MEDAGENT_CONCLUSION_DIRECTIONS
   （默认 off = D2 影子），对应 §9 阶段 A"D2 影子、其余可见"的
   分层上线方式。
3. **D6 升级句**由前端渲染器据 sheet.follow_up 生成（数据同源，
   文案模板在渲染层，与 follow_up 卡的既有做法一致）。

## 未做（阶段 B/C，依赖开放决策）

- D2 患者可见：待 D1 临床审定 + 临床版 OSCE 病例集（D4）+ 影子抽审。
- D4 的 avoid_if 冲突过滤、自理内容的专科细化：待内容审定逐条定。
- 先行句（§3.4 分类器）、科室提示（D3）：阶段 C。
- 咨询对象称谓在 D2 依据句的适配已预埋（subject_kind 驱动
  你/您），D4 执行者称谓的完整模板化随阶段 B 内容审定一并处理。
