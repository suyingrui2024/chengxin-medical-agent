# DSH 与新模型优化评估（2026-10-23）

> 触发问题：DSH 近期是否专门拿新模型做了优化？本项目应如何跟进？
> 结论：**是，且为双向绑定优化**。本文记录官方证据、本仓现状、已落地的对齐项，
> 以及 DSH 0.2.1 升级的评估清单（不在无验证环境下盲升）。

## 一、官方证据（2026-10-23 查证）

- DeepSeek Harness 团队 2026-10-17 发文：新模型 **V4.1-Flash-1017**「训练中针对
  DeepSeek Harness 进行了专项优化」，在提升任务表现的同时缩短运行时长、减少
  token 费用；同日发布的 **Harness 0.2.1** 发布说明第一条即「适配
  DeepSeek-V4.1-Flash-1017 上线」，官方要求 Harness **升至 0.2.1-rc.1 或更高**
  以获得最佳体验（澎湃/新浪/红星等媒体一致转引官方发文）。
- npm：`@deepseek-ai/dsh` 最新为 **0.2.1-rc.2（2026-10-17）**，本仓锁定
  **0.2.0-rc.2**。全部 36 个版本均为预发布，至今无正式版，上游明示会有兼容性
  破坏变更。`dsh-sdk-client`/`dsh-sdk-protocol` 自 0.2 起已不再是 dsh 的公开
  依赖（职能迁入 monorepo 公开包），公开 npm 查不到独立版本线。
- 模型阵容（官方定价页 2026-10-23 核对）：API 现售仅 `deepseek-flash`
  （= V4.1-Flash-1017，1M 上下文、最大输出 384K、支持图像）与
  `deepseek-v4-pro`（0813）；`deepseek-chat`/`deepseek-reasoner` 已于
  2026-07-24 退役。本仓唯一写死的模型 id 即 `deepseek-flash`，仍现行有效。
- 新一代 API 参数面：思考模式**默认开启、默认强度 high**，强度三档
  low/high/max（OpenAI 格式 `reasoning_effort`；Anthropic 格式
  `output_config.effort`）；思考模式下 temperature 不生效；带原生 tools 的
  请求必须完整回传 `reasoning_content` 否则 400；上下文缓存自动生效
  （Flash 命中价为未命中价的 2%）；峰谷定价（闲时半价）；工具 strict 模式
  在 `/beta` 端点；Search/Fetch 服务端工具 2026-11-01 起计费。

## 二、本仓现状与已落地对齐（2026-10-23）

审计发现：配置面设计干净（模型全由注入、能力包与模型解耦），但新模型能力的
利用停在最低档。本批已落地：

1. **DSH 桥配置对齐**：`integrations/dsh/bridge.mjs` 此前把 `maxTokens: 2048`
   与 `reasoningEffort: 'off'` 写死，Python 侧的 `MEDAGENT_LLM_MAX_TOKENS` /
   `MEDAGENT_LLM_THINKING` 对 DSH 路径完全无效。现由 `DshBackend` 经 stdin
   信封透传，桥内防御性映射（thinking 关闭恒为 off；开启时取配置档位，
   缺省 high——即 API 自身的默认档）。
2. **compatible 路径思考强度**：新增 `MEDAGENT_LLM_REASONING_EFFORT`
   （low/high/max，校验后仅在 thinking 开启且官方域名时随
   `reasoning_effort` 发送），并加入 env 白名单与 `.env.example` 指引。
   默认仍为 thinking 关闭——问诊决策的确定性与时延优先，开启属部署方
   经评测后的选择。
3. **空响应有界重试**：官方 JSON 模式偶发返回空 content（已知问题）。
   `CompatibleBackend` 现对空 content 自动重试一次并落 `model.retry`
   事件，不再让一次已知瞬态丢掉整轮决策。
4. **未动且正确的部分**：静态目录前缀（agent.py 的 static-catalog-prefix）
   与官方前缀缓存机制天然对齐，缓存命中已在事件中观测；`deepseek-flash`
   预设与视觉回退仍指向现行模型（含视觉能力，与 Pro 无视觉的事实一致）。

## 三、明确未做的一项（及理由）

- **原生工具调用 / strict 模式 / Responses API 的 json_schema 强约束**：
  本仓的「单 JSON 决策信封 + Python 侧证据校验」是有意的约束设计
  （K 契约与审计链建立其上）。迁移属架构级变更，须先有设计文档与
  live 评测对照，不在本批范围。

## 四、DSH 0.2.1 升级记录（2026-10-23 已执行，用户追问后实证推进）

初版评估曾以「本 VM 无法完成真实集成验证」为由暂缓。复核后发现验证面
其实可分层推进（注册表 → 隔离副本 → 基线对照），遂按下列路径完成升级：

1. **注册表探针**：三个直接依赖与全部传递包在 npm 上均有 0.2.1-rc.2
   （dist-tag `latest` 仍指 0.2.1-rc.2；`next` 已有 rc.3，未跟进未发布版）。
2. **整树原子升级**：@deepseek-ai 树内包以精确版本互为 peer 依赖，增量
   解析必然冲突（ERESOLVE 实测）；做法为全新解析生成锁文件——全树
   dsh-* 统一落 0.2.1-rc.2，非 DSH 工具链（electron 44.7.2 /
   @playwright/test 1.64.1 / prettier 3.9.10）版本未动，其余为 DSH 闭包
   内的传递小版本漂移（80 包，以 @aws-sdk/@anthropic-ai/sdk 补丁号为主）。
3. **未声明依赖已正式声明**：`@deepseek-ai/dsh-tools`、
   `dsh-sdk-jsonrpc-server`、`dsh-brand`、`dsh-web-search-deepseek`、
   `dsh-bash-sandbox`、`dsh-bash-local` 六个此前只靠 hoisting 解析的
   import 已写入 package.json dependencies（同为 0.2.1-rc.2）。
4. **唯一的行为适配——medical.patch.yml**：0.2.1 的 sdk-minimal 画像
   新增了文件系统面（filesystem / attachment-local / tool-fs /
   working-directory / tool-working-directory 五个插件默认启用），与医疗
   桥「无宿主能力」契约冲突（dsh:check 的禁用断言首先抓到 tool-fs 与
   tool-working-directory）。补丁已将五者全部禁用，恢复 0.2.0 的能力
   姿态；0.2.1 同时移除了 invariants 插件族（上游画像变更，非补丁可控，
   记录在案）。execution.patch.yml 语义经 check 全量断言未受影响。
5. **版本标注同步**：runtime.py / execution.py / execution_store.py
   （DSH_VERSION）/ execution-worker.mjs / 两个 benchmark 脚本与基线，
   与 package.json 同步为 0.2.1-rc.2。
6. **验证证据（本 VM 内可达的全部层级，基线对照）**：生产依赖树可完整
   物化；`npm run dsh:check` 通过（0.2.0 基线同样通过，属真回归门）；
   integrations 节点测试 60/60（与 0.2.0 基线一致）；桌面凭据测试 3/3；
   全量 pytest 489 passed；发布门 PASS；8 个直接 import 说明符全部可
   解析且 `DeepSeekHarness` 导出存在。
7. **仍未验证（如实标注）**：① 带真实模型密钥的桥决策 E2E（本环境无
   密钥）；② `RUN_DSH_EXECUTION=1` 执行集成套件（需 bubblewrap 或
   Windows ProcessContainer 与 /opt/medical-agent 布局，本 VM 不具备；
   该套件在 0.2.0 时代同样从未在此环境跑过）。这两项需在用户 Mac 或
   后续 CI 改造中补跑，升级提交说明中已注明。

## 五、运营提示

- live 评测等批量调用尽量排在闲时（峰谷定价闲时半价；高峰为工作日
  UTC 01:00–04:00 与 06:00–10:00，法定节假日全天闲时价）。
- 关注 `model.responded` 事件中的 `prompt_cache_hit_tokens`：静态前缀
  若被改动（agent_prompt.txt / 工具目录 / 问题目录），命中率会先于
  账单给出信号。
