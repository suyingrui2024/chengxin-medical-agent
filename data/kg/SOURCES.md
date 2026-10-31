# 知识库数据来源

本目录下的文件均为**第三方数据**，逐份记录来源、许可与获取时间。
`kg.db` / `kg.json` 由 `scripts/build/build_kg.py` 从这些源文件装配生成。

| 文件 | 来源 | 许可 | 获取日期 | 内容 |
|---|---|---|---|---|
| `disease.csv` | [chaseliu/ICD-10-CN](https://github.com/chaseliu/ICD-10-CN) | **MIT** | 2026-10-28 | ICD-10 中文，1,586 条编码-病名 |
| `diseases.json` | 同上 | **MIT** | 2026-10-28 | ICD-10 中文层级树，1,819 节点 |
| `wikidata_p780_symptoms.csv` | [Wikidata](https://www.wikidata.org/) P780 | **CC0** | 2026-10-28 | 疾病→症状，694 对（简体中文标签） |
| `diakg/*.json` | [OpenKG DiaKG](http://data.openkg.cn/dataset/diakg) | **CC BY** | 2026-10-28 | 41 篇中文糖尿病指南的实体与关系标注：22,050 实体、8,643 关系 |
| `LICENSE-ICD-10-CN` | 同上 | MIT | 2026-10-28 | 原始许可文本 |

## 未采用的数据及原因

| 数据 | 原因 |
|---|---|
| 《常用临床医学名词（2023年版）》 | 卫健委 PDF **385 MB 纯扫描件**（`/Font` 0、`/Image` 3878、`/ToUnicode` 0），无文本层，只能 OCR；医学术语的 OCR 错字不可接受 |
| ICD-10 国家临床版 2.0 | 仅能从 CSDN 等需登录的镜像获取，无法确认分发授权 |
| SNOMED CT / UMLS | 中国需商业授权（神州医疗代理），与"要免费"矛盾 |
| CMeKG | 数据许可条款不明确，以学术用途为主 |
| OpenKG 中文症状库 | 站点 HTTPS 不可达（HTTP 000），HTTP 下该数据集页面已 404，结构变更 |
| OpenKG 家庭常见疾病图谱 | 仅提供 Neo4j dump（`medicalgraphversion2.dump`），需 Neo4j 实例才能取出三元组 |
| Wikidata P2176（药品适应症） | 导出的 CSV **表头与数据方向矛盾**，无法确认哪一侧是药品，不进库 |
| 中文医疗问答数据集 | 爬自问诊平台，许可与患者知情同意不清晰，且无临床审核 |
| THUOCL 医学词表 | 18,749 词但**无类型标注**，无法映射到概念类型；作为词表保留未导入 |

## 审核状态

所有导入概念标记为 `imported_pending_review`。仓库自有内容继承其原有审核状态。

**这份知识库不能直接支撑临床决策**：Wikidata 是社区维护，ICD-10 编码本身也不等于诊断标准。它的用途是术语归一与候选提示，任何结论仍需临床评审。
