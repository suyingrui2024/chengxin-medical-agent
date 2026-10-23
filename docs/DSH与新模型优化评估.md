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

## 三、明确未做的两项（及理由）

- **原生工具调用 / strict 模式 / Responses API 的 json_schema 强约束**：
  本仓的「单 JSON 决策信封 + Python 侧证据校验」是有意的约束设计
  （K 契约与审计链建立其上）。迁移属架构级变更，须先有设计文档与
  live 评测对照，不在本批范围。
- **DSH 0.2.0-rc.2 → 0.2.1-rc.2 盲升**：见下节清单。本仓 README 的升级
  纪律是「CLI/SDK/协议包/锁文件同升 + 重跑真实集成测试」；当前环境
  （本 VM）无法完成该验证（见清单第 4 条），故不升。

## 四、DSH 0.2.1 升级评估清单（待具备验证环境时执行）

1. **依赖面**：三个直接依赖同升 0.2.1-rc.2 并重生成锁文件；注意 0.2 起
   `dsh-sdk-client`/`dsh-sdk-protocol` 已非上游公开依赖——先核对 0.2.1 的
   包结构再定本仓依赖声明形态。
2. **未声明的传递依赖（最大隐患）**：以下 import 不在本仓 package.json
   声明中、只靠 rc.2 依赖树 hoisting 解析，升级前逐项核对在 0.2.1 树中
   是否仍存在/改名：`@deepseek-ai/dsh-tools`、`dsh-sdk-jsonrpc-server`、
   `dsh-brand`、`dsh-web-search-deepseek`、`dsh-bash-sandbox`、
   `dsh-bash-local`（execution-*.mjs、execution-sdk.mjs、powershell-executor）。
3. **API 表面复核**：`DeepSeekHarness` 构造选项（bridge.mjs /
   execution-worker.mjs 两处拼装）、`harness.run()` 返回形态
   `{finalResponse, events}`、事件用量字段
   （`assistant/message` 的 `data.usage.{inputTokens,cacheReadTokens,
   cacheWriteTokens}` 为 backends.py 直接消费）、CLI 路径
   `lib/bin.js` 与旗标 `--profile/--patch/--dump-config`、profile 名
   `sdk-minimal`/`sdk` 与两份 patch.yml 的 schema。
4. **验证环境要求**：`npm ci` 可完整执行的机器（本 VM 有 EPERM 已知问题）、
   bubblewrap（Linux 隔离）或 Windows ProcessContainer、
   `/opt/medical-agent` 部署布局；随后跑 `npm run dsh:check` +
   `RUN_DSH_EXECUTION=1` 的执行集成测试全集 + 问诊 live 评测。
5. **顺带收益**：0.2.1 适配 V4.1-Flash-1017（模型侧已为 Harness 专项
   优化）、新增 DeepSeek Search/Fetch API 支持（注意 2026-11-01 起计费）、
   alpha 线已出现每模型推理等级配置（与本仓桥的 reasoningEffort 透传
   方向一致，升级后复核字段名是否变化）。
6. **版本标注同步**：`runtime.py` 的 runtime_descriptor `dsh_version`
   与 package.json 保持一致（当前同为 0.2.0-rc.2）。

## 五、运营提示

- live 评测等批量调用尽量排在闲时（峰谷定价闲时半价；高峰为工作日
  UTC 01:00–04:00 与 06:00–10:00，法定节假日全天闲时价）。
- 关注 `model.responded` 事件中的 `prompt_cache_hit_tokens`：静态前缀
  若被改动（agent_prompt.txt / 工具目录 / 问题目录），命中率会先于
  账单给出信号。
