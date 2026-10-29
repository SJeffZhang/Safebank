/** Presentation only. These labels never change a gateway decision or authorize an action. */
const labels: Record<string, string> = {
  INSUFFICIENT_FUNDS: "当前可用余额不足",
  ASSESSMENT_MISSING: "请先完成有效的风险测评",
  SUITABILITY_MISMATCH: "产品风险等级高于您的风险承受等级",
  SCHEDULED_SHORTFALL: "执行后可能无法覆盖未来扣费，请调整金额",
  NEW_PAYEE_FIRST_TRANSFER: "这是向新收款人的首次转账，请仔细核对目标账户",
  FIRST_TRANSFER_TO_PAYEE: "您尚未成功向此收款人转账",
  AMOUNT_ABOVE_YELLOW_CAP: "本笔转账金额较高，需要短信验证",
  CUMULATIVE_OUTFLOW: "近期累计对外转出较高，需要短信验证",
  CARD_SINGLE_LIMIT: "金额超过卡片单笔限额",
  CARD_DAILY_LIMIT: "金额超过卡片日累计剩余额度",
  CARD_STATE_CONFLICT: "卡片当前状态不允许此操作",
  DAILY_OUTFLOW_LIMIT: "金额超过当日对外转出上限",
  INTENT_SCOPE_EXCEEDED: "计划超出您的授权范围，请重新说明需求",
  UNKNOWN_SENSITIVE_ARG: "关键信息来源无法确认，请在业务页面补充",
  RISKY_SEQUENCE: "连续操作触发了工作流风险检查",
  ANOMALY_NIGHT_LARGE: "深夜大额交易",
  ANOMALY_DUPLICATE: "短时间内的重复交易",
  ANOMALY_NEW_MERCHANT_LARGE: "新商户大额交易",
  ANOMALY_CATEGORY_OUTLIER: "高于同类消费基线",
  HIGH_RISK_OPERATION: "本次操作需要短信验证码",
};
export const explainCode = (code: string) => labels[code] ?? code;
