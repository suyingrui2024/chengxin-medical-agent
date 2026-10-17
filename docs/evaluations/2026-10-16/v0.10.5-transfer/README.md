# 0.10.5 新增公开任务：冻结对照与复现

四题、两次重复、产品与独立官方SDK两条路径，共16次运行、313次实际上游请求。全部预检合格后才启动付费对照；模型调用期间137个冻结源文件保持一致。本记录不被后续0.10.6修复或反事实重放覆盖。

| 维度 | 产品 | 独立官方SDK |
| --- | ---: | ---: |
| 运行次数 | 8 | 8 |
| 上游实现检查通过 | 3 | 0 |
| 文件约束通过 | 7 | 4 |
| 根任务正常结束 | 3 | 0 |
| 实现、文件约束、新增测试共同通过 | 2 | 0 |
| 再加根任务正常结束的联合交付 | 1 | 0 |
| 实际请求 | 153 | 160 |
| 执行耗时合计，毫秒 | 1,422,766 | 733,497 |

实现配对：产品单独通过3、SDK单独通过0、共同通过0、共同失败5。产品更慢，未计token费用，不能由请求略少推断省钱或效率更高。没有新增测试时该项不适用，不能算作生成测试覆盖。逐题、逐次结果见[result-summary.json](result-summary.json)，原始事件、检查和账本见[complete.json](complete.json)。

## 范围与条件

从固定哈希的SWE-bench Lite公开TEST共300行中，按既有四个环境适配器分层，排除先前记录的14个实例ID（其中6个属于该TEST），剩113个候选；种子2026101605抽到Matplotlib-25498、Flask-4045、Sphinx-8474、SymPy-20049。没有按预检或成功率换题。这是支持仓库的分层子样本，不是全部300题等概率抽样；公开历史题可能已出现在模型训练中。

两边均使用DSH 0.2.0-rc.2、同一模型与任务、同一准备目录、最多20次请求及600秒；先后顺序种子2026101606。源码来自确切基础提交的tarball，**两边均没有Git历史**。产品保留自身persona、工具扩展、动态预算、完成门与等待策略；SDK使用独立入口和默认行为，系统提示和完整工具集合不相同。本轮不评价无限宿主权限或其它配置下的DSH。

原始缺陷／参考修复预检全部通过，F2P/P2P标识数分别1/69、2/50、4/436、4/9；实际参数化节点映射保存于[preflight.json](preflight.json)。固定依赖与历史Python用于兼容，Matplotlib使用基础提交Python源码和记录哈希的3.7.1预编译伴随扩展，并非从本题完整构建wheel，也不是官方SWE-bench容器评分。

模型不能看到隐藏检查和参考补丁。主实现检查重建后恢复原测试、配置和依赖，再应用上游测试补丁；新增回归另行检查真实收集和结果。网络对两边开放，任务禁止获取答案，但没有硬阻断终端联网，不能仅凭未见搜索工具声称网络隔离。

## 失败审阅

Matplotlib产品第一次实现通过，但新增测试有一项错误前置断言，20次预算耗尽；第二次实现及3项新增测试通过，却因旧控制器将一次超过120秒上限、未执行的提议登记为必需项，改正并实际退出0后仍判定`delivery_checks_failed`。原始失败保留。0.10.6的修复验证单独归档，不追改本表。

Flask的原始问题只明确要求蓝图名称带点时报错，隐藏验收同时要求既有endpoint/view-function的带点错误从AssertionError变成ValueError。两边第一次候选通过`test_dotted_name_not_allowed`，却未通过`test_route_decorator_custom_endpoint_with_dots`。这是规格与验收解释的局限，原始评分不改成成功。

其余运行的实现失败、文件契约失败、超预算及未正常结束均保留。SDK报告中的预算网关429与真实提供商响应分别记录；它是本轮限额执行行为，不等于DeepSeek线上服务发生了同样的故障。不能将这4题结果推广为总体达到或超过DSH／Codex，更不能视作医疗有效性验证。

## 独立重建与负对照

55份冻结环境输入包含4份基础源码归档、50个wheel和1份历史Python归档；137份冻结源文件包括控制器、驱动、评分及任务。新目录从归档离线重建后，四份准备目录清单全部一致；16份实际候选覆盖层的字节、删除和链接文本均复核，并得到相同上游逐节点结果、新增测试收集／结果与文件约束。4个原始缺陷负对照全部被拒绝。这是产物与评分重验，不重放模型思考或重新测量根任务表现。

验证记录见[verification.json](verification.json)、[transfer-all-candidate-replay.json](transfer-all-candidate-replay.json)、[transfer-original-defects-negative.json](transfer-original-defects-negative.json)。本地确定模型还覆盖参考修复正例、未修复但自然结束的负例，以及实现正确但附带错误新增测试的负例；均不计入313次真实请求。

在项目根目录执行以下命令，输出位置必须未使用，不需要`.env`，复用本机已安装的锁定Node依赖，不调用模型或下载：

```bash
python3 docs/evaluations/2026-10-16/v0.10.5-transfer/verify_selection.py
python3 docs/evaluations/2026-10-16/v0.10.5-transfer/restore_environment.py \
  --project /tmp/chengxin-transfer-replay --dependencies "$PWD/node_modules" \
  --output /tmp/chengxin-transfer-restore.json
python3 docs/evaluations/2026-10-16/v0.10.5-transfer/verify_candidates.py \
  --project /tmp/chengxin-transfer-replay --output /tmp/chengxin-transfer-candidates.json
python3 docs/evaluations/2026-10-16/v0.10.5-transfer/verify_candidates.py \
  --project /tmp/chengxin-transfer-replay --negative --output /tmp/chengxin-transfer-negative.json
```

环境依赖当前Linux x64、系统运行库和bubblewrap，不是可移植容器镜像。数据Parquet与JSON逐行一致性证明单独保存；抽样重放使用当时冻结的排除集合，不读取后续新增夹具重新抽题。调用前设计见[design.md](design.md)。所有归档文件除清单自身外纳入`archive-manifest.json`，密钥扫描结果另存。
