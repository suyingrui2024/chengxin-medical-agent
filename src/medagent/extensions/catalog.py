"""Single source of truth for the outpatient calculation catalog.

**Frozen at 154 items / 128 delivered as of 2026-10-28.** No item is added
until the first clinical review batch
(``docs/review-packets/explicit-calculation-v1/``) comes back. Adding
content now only grows the unreviewed surface: every one of the 130
registered capabilities still carries
``engineering_verified_not_clinical``, and spot checks of our own work
found roughly one scoring error per fifteen items. Widen after the review
process is validated, not before.

Every department item the product intends to offer lives here, with its
department, input kind, audience and delivery status. Two things derive
from this table instead of being hand-maintained:

- ``builtin.load_registry`` admits a pack only when its id appears here
  with ``status == "delivered"`` — replacing the old hand-written
  ``SCALE_IDS`` tuple that had already drifted (29 ids, 4 packs).
- ``docs/门诊显式计算科目与项目总表.md`` is generated from the same
  rows, and ``tests/test_catalog.py`` asserts the three agree.

Status values:
- ``delivered``        registered, executable, covered by tests
- ``planned``          expressible today, content not written yet
- ``clinician_pending`` clinician-only; no entry point exists yet, so the
                        pack is deliberately NOT registered
- ``rejected``         listed only to record why it will not be built

Adding an item is a code change on purpose: the table is the allow-list.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

STATUSES = {"delivered", "planned", "clinician_pending", "rejected"}
AUDIENCES = {"patient", "clinician"}
KINDS = {"scale", "formula", "table"}

# code|department|name|kind|audience|status|id|note
_ROWS = """
CV-01|心血管内科|CHA2DS2-VASc 卒中风险|scale|clinician|delivered|calculate.cha2ds2vasc|房颤抗凝决策参考，不给停药建议
CV-02|心血管内科|HAS-BLED 出血风险|scale|clinician|delivered|calculate.hasbled|仅风险分层
CV-03|心血管内科|NYHA 心功能分级|scale|patient|delivered|calculate.nyha|
CV-04|心血管内科|平均动脉压 MAP|formula|patient|delivered|calculate.map|
CV-05|心血管内科|休克指数|formula|patient|delivered|calculate.shock_index|
CV-06|心血管内科|心率血压乘积 RPP|formula|patient|delivered|calculate.rpp|
CV-07|心血管内科|TIMI 风险评分|scale|clinician|delivered|calculate.timi|
CV-08|心血管内科|GRACE 评分|scale|clinician|rejected||预后概率，不做
CV-09|心血管内科|Killip 分级|scale|clinician|delivered|calculate.killip|
CV-10|心血管内科|6 分钟步行预计距离|formula|patient|delivered|calculate.six_min_walk|
RES-01|呼吸内科|mMRC 呼吸困难分级|scale|patient|delivered|calculate.mmrc|
RES-02|呼吸内科|CAT 慢阻肺评估|scale|patient|delivered|calculate.cat|
RES-03|呼吸内科|ACT 哮喘控制|scale|patient|delivered|calculate.act|
RES-04|呼吸内科|氧合指数 P/F|formula|patient|delivered|calculate.pf_ratio|需血气结果
RES-05|呼吸内科|CURB-65|scale|clinician|delivered|calculate.curb65|血压为析取条件，需双字段
RES-06|呼吸内科|BODE 指数|scale|clinician|delivered|calculate.bode|
RES-07|呼吸内科|A-a 氧梯度|formula|clinician|delivered|calculate.aa_gradient|
RES-08|呼吸内科|PSI 肺炎严重指数|scale|clinician|rejected||需 20 项变量与年龄性别分值，简化即失真
GIT-01|消化内科|GerdQ 胃食管反流|scale|patient|delivered|calculate.gerdq|
GIT-02|消化内科|IBS-SSS 肠易激|scale|patient|delivered|calculate.ibssss|
GIT-03|消化内科|Wexner 便秘评分|scale|patient|delivered|calculate.wexner|
GIT-04|消化内科|FIB-4 肝纤维化|formula|patient|delivered|calculate.fib4|需化验值
GIT-05|消化内科|Child-Pugh 分级|scale|clinician|delivered|calculate.childpugh|
GIT-06|消化内科|MELD-Na|formula|clinician|rejected||移植排序，不做
GIT-07|消化内科|Rockall 再出血风险|scale|clinician|delivered|calculate.rockall|
GIT-08|消化内科|APRI 指数|formula|clinician|delivered|calculate.apri|
END-01|内分泌科|BMI 体质指数|formula|patient|delivered|calculate.bmi|孕妇/儿童/运动员不适用
END-02|内分泌科|体表面积 BSA|formula|patient|delivered|calculate.bsa|
END-03|内分泌科|腰高比|formula|patient|delivered|calculate.waist_height|
END-04|内分泌科|HOMA-IR 胰岛素抵抗|formula|clinician|delivered|calculate.homa_ir|需胰岛素化验
END-05|内分泌科|eGFR CKD-EPI 2021|formula|clinician|delivered|calculate.egfr_ckdepi2021|患者易读成肾衰诊断
END-06|内分泌科|肌酐清除率 Cockcroft-Gault|formula|clinician|delivered|calculate.crcl_cg|
END-07|内分泌科|FINDRISC 糖尿病风险|scale|patient|delivered|calculate.findrisc|
END-08|内分泌科|甲状腺结节 TI-RADS|scale|clinician|rejected||影像分级，不做；分级型
NEP-01|肾内科|尿蛋白肌酐比 UPCR|formula|patient|delivered|calculate.upcr|
NEP-02|肾内科|白蛋白校正血钙|formula|patient|delivered|calculate.corrected_calcium|
NEP-03|肾内科|阴离子间隙|formula|patient|delivered|calculate.anion_gap|不判断酸碱失衡
NEP-04|肾内科|血浆渗透压|formula|patient|delivered|calculate.osmolality|不判断高渗状态
NEP-05|肾内科|自由水缺失|formula|clinician|delivered|calculate.free_water_deficit|
NEP-06|肾内科|尿钠排泄分数 FeNa|formula|clinician|delivered|calculate.fena|
NEP-07|肾内科|肾衰指数 RFI|formula|clinician|delivered|calculate.rfi|
NEU-01|神经内科|mRS 改良 Rankin|scale|patient|delivered|calculate.mrs|
NEU-02|神经内科|ABCD2 TIA 风险|scale|patient|delivered|calculate.abcd2|
NEU-03|神经内科|HIT-6 头痛影响|scale|patient|delivered|calculate.hit6|只给分数不给分档
NEU-04|神经内科|DHI 眩晕残障|scale|patient|rejected||25 条目需授权译本，措辞不可自拟
NEU-05|神经内科|NIHSS 卒中量表|scale|clinician|delivered|calculate.nihss|需查体
NEU-06|神经内科|GCS 格拉斯哥昏迷|scale|clinician|delivered|calculate.gcs|需查体
NEU-07|神经内科|Essen 卒中复发风险|scale|clinician|delivered|calculate.essen|
NEU-08|神经内科|MRC 肌力分级|scale|clinician|delivered|calculate.mrc_grade|
HEM-01|血液科|网织红细胞校正|formula|patient|delivered|calculate.retic_corrected|
HEM-02|血液科|中性粒细胞绝对计数|formula|clinician|delivered|calculate.anc|
HEM-03|血液科|ISTH DIC 评分|scale|clinician|delivered|calculate.isth_dic|
HEM-04|血液科|4T 肝素血小板减少|scale|clinician|delivered|calculate.4t|
HEM-05|血液科|IPSS 骨髓纤维化|scale|clinician|delivered|calculate.ipss_mf|
HEM-06|血液科|转铁蛋白饱和度|formula|clinician|delivered|calculate.tsat|
RHE-01|风湿免疫|DAS28 类风湿活动度|scale|clinician|rejected||为计算公式而非量表，需按公式引擎单独实现
RHE-02|风湿免疫|BASDAI 强直性脊柱炎|scale|patient|rejected||BASDAI 需将末两项取平均再除以 5，当前引擎不支持除法
RHE-03|风湿免疫|HAQ 健康评估|scale|patient|delivered|calculate.haq|
RHE-04|风湿免疫|ESSDAI 干燥综合征|scale|clinician|rejected||12 个结构域加权，权重需原文核对
RHE-05|风湿免疫|痛风尿酸目标值|formula|patient|rejected||尿酸目标是治疗目标而非计算公式，不给
RHE-06|风湿免疫|SLEDAI 狼疮活动度|scale|clinician|rejected||24 项加权，权重需原文核对
RHE-07|风湿免疫|OSTA 骨质疏松风险|scale|patient|delivered|calculate.osta|
INF-01|感染科|qSOFA 脓毒症筛查|scale|clinician|delivered|calculate.qsofa|需生命体征
INF-02|感染科|NEWS2 早期预警|scale|clinician|delivered|calculate.news2|需生命体征
INF-03|感染科|降钙素原判读|scale|clinician|delivered|calculate.pct|分级型
INF-04|感染科|发热持续时间|formula|patient|delivered|calculate.fever_days|
INF-05|感染科|症状日记评分|scale|patient|delivered|calculate.symptom_diary|
GS-01|普外科|腹壁疝 EHS 分级|scale|clinician|delivered|calculate.ehs|分级型
GS-02|普外科|Alvarado 阑尾炎评分|scale|clinician|delivered|calculate.alvarado|
GS-03|普外科|VAS 疼痛评分|scale|patient|delivered|calculate.vas|
GS-04|普外科|伤口 ASEPSIS 评分|scale|clinician|rejected||采用加权积分表，非 0–3 计分
ORT-01|骨科|ODI 腰痛功能障碍|scale|patient|delivered|calculate.odi|
ORT-02|骨科|NDI 颈痛功能障碍|scale|patient|delivered|calculate.ndi|
ORT-03|骨科|Lysholm 膝关节|scale|clinician|delivered|calculate.lysholm|
ORT-04|骨科|Harris 髋关节|scale|clinician|rejected||功能项为七个子项复合评分，需逐项核对原文
ORT-05|骨科|JOA 腰椎|scale|clinician|delivered|calculate.joa|
ORT-06|骨科|骨密度 T 值判读|scale|clinician|delivered|calculate.bone_density|分级型
NS-01|神经外科|Hunt-Hess 分级|scale|clinician|delivered|calculate.hunt_hess|
NS-02|神经外科|WFNS 分级|scale|clinician|delivered|calculate.wfns|
NS-03|神经外科|脑室外引流容量|formula|clinician|rejected||无公认公式，定义未定
URO-01|泌尿外科|IPSS 前列腺症状|scale|patient|delivered|calculate.ipss|
URO-02|泌尿外科|OABSS 膀胱过度活动|scale|patient|delivered|calculate.oabss|
URO-03|泌尿外科|前列腺体积估算|formula|clinician|delivered|calculate.prostate_volume|
URO-04|泌尿外科|PSA 密度|formula|clinician|delivered|calculate.psa_density|
URO-05|泌尿外科|肾结石 STONE 评分|scale|clinician|rejected||五项权重需原文核对，凭记忆实现会失真
THO-01|胸外科|肺癌 TNM 分期|scale|clinician|rejected||分期标准，不做；分级型
THO-02|胸外科|术后预计 FEV1|formula|clinician|delivered|calculate.ppofev1|
THO-03|胸外科|胸腔积液 Light 标准|formula|clinician|delivered|calculate.light_criteria|
OBG-01|妇产科|孕周与预产期|formula|patient|delivered|calculate.edd|
OBG-02|妇产科|PUQE 妊娠恶心呕吐|scale|patient|delivered|calculate.puqe|
OBG-03|妇产科|EPDS 产后抑郁|scale|patient|rejected||第 10 题直接询问自伤念头，缺安全网
OBG-04|妇产科|Kupperman 更年期|scale|patient|rejected||仅有教材转引，无原始文献可核对
OBG-05|妇产科|Bishop 宫颈评分|scale|clinician|delivered|calculate.bishop|
OBG-06|妇产科|产后出血量估算|formula|clinician|delivered|calculate.pph_volume|
OBG-07|妇产科|AMH 卵巢储备判读|scale|clinician|delivered|calculate.amh|分级型
PED-01|儿科|儿童体重估算|formula|patient|delivered|calculate.child_weight|
PED-02|儿科|儿童身高估算|formula|patient|delivered|calculate.child_height|
PED-03|儿科|C-ACT 儿童哮喘控制|scale|patient|delivered|calculate.cact|只给分数不给分档
PED-04|儿科|Apgar 评分|scale|clinician|delivered|calculate.apgar|
PED-05|儿科|FLACC 疼痛|scale|clinician|delivered|calculate.flacc|
PED-06|儿科|小儿脱水程度|scale|clinician|delivered|calculate.dehydration|
PED-07|儿科|4-2-1 补液法则|formula|clinician|rejected||补液量属剂量，不做
PED-08|儿科|新生儿黄疸风险评估|scale|clinician|rejected||需光疗曲线插值；分级型
EM-01|急诊/重症|RTS 创伤生理评分|scale|clinician|delivered|calculate.rts|
EM-02|急诊/重症|MEWS 改良早期预警|scale|clinician|delivered|calculate.mews|
EM-03|急诊/重症|SOFA 器官衰竭|scale|clinician|delivered|calculate.sofa|
EM-04|急诊/重症|APACHE II|scale|clinician|rejected||12 项生理变量加权，简化即失真
EM-05|急诊/重症|GAP 创伤评分|scale|clinician|delivered|calculate.gap|
EM-06|急诊/重症|中毒严重度 PSS|scale|clinician|delivered|calculate.pss|
EM-07|急诊/重症|烧伤面积九分法|formula|patient|delivered|calculate.burn_area|
EM-08|急诊/重症|ISS 创伤严重度|scale|clinician|delivered|calculate.iss|
PSY-01|精神心理|PHQ-8 抑郁自评|scale|patient|delivered|calculate.phq8|不含自杀意念条目
PSY-02|精神心理|GAD-7 焦虑自评|scale|patient|delivered|calculate.gad7|
PSY-03|精神心理|ISI 失眠严重度|scale|patient|delivered|calculate.isi|
PSY-04|精神心理|GDS-15 老年抑郁|scale|patient|delivered|calculate.gds15|含反向计分
PSY-05|精神心理|SAS 焦虑自评|scale|patient|delivered|calculate.sas|
PSY-06|精神心理|SDS 抑郁自评|scale|patient|delivered|calculate.sds|
PSY-07|精神心理|MMSE 简易智能|scale|clinician|rejected||30 条目需授权译本，措辞不可自拟
PSY-08|精神心理|CAM 谵妄评估|formula|clinician|delivered|calculate.cam|条件逻辑，改用公式引擎
DER-01|皮肤科|EASI 湿疹面积|formula|clinician|delivered|calculate.easi|面积×严重度加权求和，改用公式引擎
DER-02|皮肤科|PASI 银屑病面积|formula|clinician|delivered|calculate.pasi|面积×严重度加权求和，改用公式引擎
DER-03|皮肤科|瘙痒 NRS|scale|patient|delivered|calculate.pruritus_nrs|
DER-04|皮肤科|UAS7 荨麻疹活动度|scale|patient|delivered|calculate.uas7|
OPH-01|眼科|LogMAR 视力换算|formula|clinician|delivered|calculate.logmar|
OPH-02|眼科|眼压校正|formula|clinician|rejected||眼压校正无统一公式，不同研究结论冲突
OPH-03|眼科|白内障 LOCS 分级|scale|clinician|delivered|calculate.locs|分级型
ENT-01|耳鼻喉|THI 耳鸣致残|scale|patient|delivered|calculate.thi|
ENT-02|耳鼻喉|Lund-Mackay 鼻窦|scale|clinician|delivered|calculate.lund_mackay|分级型
ENT-03|耳鼻喉|听力损失程度分级|scale|clinician|delivered|calculate.hearing_loss|分级型
DEN-01|口腔科|DMFT 龋失补指数|formula|clinician|delivered|calculate.dmft|
DEN-02|口腔科|牙周 CPI 指数|formula|clinician|delivered|calculate.cpi|六分区取最大值，改用公式引擎
REH-01|康复科|Barthel 指数|scale|patient|delivered|calculate.barthel|
REH-02|康复科|FIM 功能独立性|scale|clinician|delivered|calculate.fim|
REH-03|康复科|握力判读|scale|patient|delivered|calculate.grip_strength|分级型
REH-04|康复科|Berg 平衡量表|scale|patient|delivered|calculate.berg|
REH-05|康复科|MMT 徒手肌力|scale|clinician|delivered|calculate.mmt|
TCM-01|中医科|中医体质辨识|scale|patient|rejected||需九种体质分别计分并取最高，非单一总分
TCM-02|中医科|失眠中医证候|scale|patient|delivered|calculate.tcm_insomnia|
TCM-03|中医科|舌象记录|scale|clinician|delivered|calculate.tongue|分级型
PAI-01|疼痛科|DN4 神经病理性疼痛|scale|patient|delivered|calculate.dn4|
PAI-02|疼痛科|PCS 疼痛灾难化|scale|clinician|delivered|calculate.pcs|
PAI-03|疼痛科|ORT 阿片风险|scale|clinician|delivered|calculate.ort|
ANE-01|麻醉科|ASA 分级|scale|clinician|delivered|calculate.asa|分级型
ANE-02|麻醉科|Mallampati 气道分级|scale|clinician|delivered|calculate.mallampati|分级型
ANE-03|麻醉科|RCRI 心脏风险|scale|clinician|delivered|calculate.rcri|
GP-01|全科/健康管理|China-PAR 心血管风险|scale|clinician|rejected||十年概率，不做
GP-02|全科/健康管理|健康生活方式评分|scale|patient|delivered|calculate.hls|
GP-03|全科/健康管理|Morse 跌倒风险|scale|clinician|delivered|calculate.morse|
GP-04|全科/健康管理|老年综合评估摘要|scale|patient|rejected||跨量表摘要，非单一量表
GP-05|全科/健康管理|疫苗接种到期提醒|formula|patient|delivered|calculate.vaccine_due|
VAS-01|血管外科|Rutherford 下肢缺血分级|scale|clinician|delivered|calculate.rutherford|分级型
VAS-02|血管外科|CEAP 静脉分级|scale|clinician|rejected||需 C/E/S/A 四维同时输出，非单一分数
VAS-03|血管外科|Wells DVT 评分|scale|clinician|delivered|calculate.wells_dvt|
VAS-04|血管外科|踝肱指数 ABI|formula|patient|delivered|calculate.abi|
"""


@dataclass(frozen=True)
class Entry:
    code: str
    department: str
    name: str
    kind: str
    audience: str
    status: str
    id: Optional[str]
    note: str = ""


def _parse() -> List[Entry]:
    entries: List[Entry] = []
    seen_codes: set = set()
    seen_ids: set = set()
    for line in _ROWS.strip().splitlines():
        if not line.strip():
            continue
        parts = line.split("|")
        if len(parts) != 8:
            raise ValueError(f"目录行字段数不正确：{line!r}")
        code, department, name, kind, audience, status, identity, note = parts
        if kind not in KINDS:
            raise ValueError(f"{code} 的类型未注册：{kind}")
        if audience not in AUDIENCES:
            raise ValueError(f"{code} 的受众未注册：{audience}")
        if status not in STATUSES:
            raise ValueError(f"{code} 的状态未注册：{status}")
        if code in seen_codes:
            raise ValueError(f"目录编号重复：{code}")
        seen_codes.add(code)
        identity = identity.strip() or None
        if identity is not None:
            if identity in seen_ids:
                raise ValueError(f"注册 id 重复：{identity}")
            seen_ids.add(identity)
        if status == "delivered" and identity is None:
            raise ValueError(f"{code} 标记为已交付但没有注册 id")
        if status != "delivered" and identity is not None and status == "rejected":
            raise ValueError(f"{code} 已拒绝却仍带注册 id")
        entries.append(
            Entry(code, department, name, kind, audience, status, identity, note)
        )
    return entries


CATALOG: List[Entry] = _parse()

# Ids the registry is allowed to bind an implementation to.
DELIVERED_IDS = frozenset(
    entry.id for entry in CATALOG if entry.status == "delivered" and entry.id
)

# Ids that are planned (content not written yet). Kept separate so a pack
# appearing before its status flips fails the drift test instead of
# silently going live.
PLANNED_IDS = frozenset(
    entry.id for entry in CATALOG if entry.status == "planned" and entry.id
)


def by_department() -> Dict[str, List[Entry]]:
    grouped: Dict[str, List[Entry]] = {}
    for entry in CATALOG:
        grouped.setdefault(entry.department, []).append(entry)
    return grouped


def summary() -> Dict[str, int]:
    counts = {"total": len(CATALOG), "patient": 0, "clinician": 0}
    for entry in CATALOG:
        counts[entry.audience] += 1
    for status in STATUSES:
        counts[status] = sum(1 for entry in CATALOG if entry.status == status)
    return counts


def entry_for(identity: str) -> Optional[Entry]:
    for entry in CATALOG:
        if entry.id == identity:
            return entry
    return None
