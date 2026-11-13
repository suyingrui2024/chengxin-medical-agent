// Labels for consultation facts, sources, phases and backend modes.

export const names = {
  consult_for: "咨询对象",
  sex: "性别",
  age_years: "年龄",
  chief_complaint: "主要问题",
  emergency_screen: "紧急信号筛查",
  onset: "开始时间",
  trajectory: "变化趋势",
  severity: "影响程度",
  associated_symptoms: "伴随不适",
  resp_breathing: "呼吸情况",
  resp_cough_details: "咳嗽情况",
  resp_intake: "饮水、进食与排尿",
  medications_taken: "用药与处理",
  allergies: "过敏信息",
  relevant_history: "相关病史",
  patient_goal: "期望与担心",
  temperature_c: "报告的腋温",
  symptom_location: "主观症状部位",
  unsolicited_note: "补充说明",
  warning_意识: "意识与抽搐",
  warning_呼吸: "呼吸情况",
  warning_出血: "出血情况",
  warning_剧痛: "剧烈疼痛",
  warning_持续加重: "是否持续加重",
  warning_功能骤降: "活动能力",
};
export const values = {
  self: "本人",
  male: "男",
  female: "女",
  child: "孩子",
  other_adult: "其他家人",
  worsening: "加重",
  improving: "减轻",
  stable: "变化不大",
  intermittent: "阵发性",
};
export const sourceNames = {
  patient: "患者自述",
  caregiver: "照护者自述",
  device: "设备直连",
  camera: "相机观察",
  system_inference: "系统推断",
};
export const qualityNames = {
  not_assessed: "质量未评价",
  adequate: "质量可用",
  inadequate: "质量不足",
  not_done: "未执行",
};
export const modeNames = {
  demo: "离线演示",
  compatible: "直接模型",
  dsh: "DSH 问诊",
};
export const phaseNames = {
  opening: "明确问题",
  emergency_screen: "紧急信号",
  history: "了解经过",
  background: "补充背景",
  observation: "定向观察",
  summary: "待核对",
  closed: "已结束",
};

export const FOLLOW_UP_KIND_LABELS = {
  urgent_checkin: "紧急确认",
  routine_checkin: "例行回访",
};
