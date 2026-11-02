export const isPlanTerminal = (status: string) => ['EXECUTED','FAILED','DENIED','REJECTED','EXPIRED','SUPERSEDED','CANCELLED','LOCKED'].includes(status);
export function planPresentation(status: string) {
  const labels: Record<string,string> = {
    EXECUTED:'操作已完成',FAILED:'执行失败',DENIED:'操作已被拦截',REJECTED:'已拒绝',EXPIRED:'授权已过期',
    SUPERSEDED:'计划已被替代',CANCELLED:'已取消',LOCKED:'审批已锁定',REPLAN:'计划需要调整',
    PENDING_APPROVAL:'等待您确认',PENDING:'等待您确认',CONFIRMED:'已确认，等待执行结果',
    USED:'正在查询执行结果',UNKNOWN:'结果确认中',EXECUTING:'结果确认中',RECONCILING:'结果确认中',
    FENCING:'结果确认中',PAUSED:'结果确认中',CHECKING:'正在查询计划状态',UNAVAILABLE:'状态暂不可用',
  };
  return {
    label:labels[status] ?? '请查询计划状态',
    tone: status === 'EXECUTED' ? 'success' as const : ['FAILED','DENIED'].includes(status) ? 'danger' as const : 'amber' as const,
    message: status === 'EXECUTED' ? '已核实原计划完成，正在同步账户与业务状态。'
      : isPlanTerminal(status) ? '请查看计划详情，核对实际状态；如需办理，请重新发起。'
      : '请勿重复发起。请查询原计划的实际状态。',
  };
}
