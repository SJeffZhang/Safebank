import { useEffect, useState } from "react";
import { Subscriptions } from "./Services";
import { request, getPlan } from "./api";
import { useLoad } from "./hooks";
import {
  uid,
  type ApprovalItem,
  type Audit,
  type Notice,
  type PlanStatus,
} from "./types";
import type { PageProps } from "./Banking";
import { Panel, Segments } from "./Banking";
import {
  Icon,
  Button,
  Alert,
  Empty,
  Loading,
  Badge,
  dateTime,
  Modal,
} from "./ui";
export function Security(
  p: PageProps & { openApproval: (id: string) => void },
) {
  const [section, setSection] = useState("subscriptions");
  const [tab, setTab] = useState("approvals");
  useEffect(() => {
    const sync = () => {
      const requested = new URLSearchParams(location.hash.split("?")[1] ?? "").get("tab");
      if (requested && ["approvals", "notifications", "audit"].includes(requested)) {
        setSection("activity"); setTab(requested);
      } else setSection("subscriptions");
    };
    sync(); window.addEventListener("hashchange", sync);
    return () => window.removeEventListener("hashchange", sync);
  }, []);
  function selectSection(next: string) {
    setSection(next);
    history.replaceState(null, "", next === "subscriptions" ? "#security" : `#security?tab=${tab}`);
  }
  function selectActivity(next: string) {
    setTab(next);
    history.replaceState(null, "", `#security?tab=${next}`);
  }
  const [plan, setPlan] = useState<PlanStatus>();
  const [planId, setPlanId] = useState("");
  const q = useLoad(async () => {
    const [approvals, audit, notifications] = await Promise.allSettled([
      request<{ items: ApprovalItem[] }>("/approvals?status=PENDING&limit=50"),
      request<{ items: Audit[] }>("/audit?limit=50"),
      request<{ items: Notice[] }>("/notifications?unread_only=false&limit=50"),
    ]);
    return { approvals, audit, notifications };
  }, [p.sid, p.revision]);
  return (
    <>
      <div className="my-profile-header">
        <span className="my-profile-avatar" role="img" aria-label={`${p.profile.display_name}的头像`}>
          {Array.from(p.profile.display_name.trim())[0] ?? "我"}
        </span>
        <div><span className="eyebrow">MY SAFEBANK</span><h1>{p.profile.display_name}</h1><p>{p.profile.phone_masked}</p></div>
        <Badge><Icon name="shield" size={12}/>个人账户</Badge>
      </div>
      <Segments items={[{id:"subscriptions",label:"订阅管理"},{id:"activity",label:"安全记录"}]}
        value={section} onChange={selectSection}/>
      {section === "subscriptions" ? <Subscriptions {...p} embedded/> : <>
      <div className="section-head my-activity-heading"><h2>审批、通知与操作记录</h2>
        <Button variant="ghost" onClick={q.reload}><Icon name="repeat" size={15}/>刷新</Button>
      </div>
      <Segments
        items={[
          { id: "approvals", label: "待我确认" },
          { id: "notifications", label: "通知中心" },
          { id: "audit", label: "操作记录" },
        ]}
        value={tab}
        onChange={selectActivity}
      />
      {q.loading ? (
        <Loading />
      ) : (
        <Panel
          title={
            tab === "approvals"
              ? "待确认计划"
              : tab === "audit"
                ? "操作记录"
                : "消息通知"
          }
        >
          {tab === "approvals" ? (
            q.value?.approvals.status === "fulfilled" ? (
              q.value.approvals.value.items.length ? (
                q.value.approvals.value.items.map((a) => (
                  <div className="data-row" key={a.approval_id}>
                    <span className="soft-icon">
                      <Icon name="shield" />
                    </span>
                    <div>
                      <strong>
                        {a.risk === "RED"
                          ? "需要短信验证的计划"
                          : "一份操作计划等待核对"}
                      </strong>
                      <small>有效至 {dateTime(a.expires_at_wall)}</small>
                      <code>{a.plan_id}</code>
                    </div>
                    <Badge tone={a.risk === "RED" ? "danger" : "amber"}>
                      {a.risk === "RED" ? "高风险" : "待确认"}
                    </Badge>
                    <Button onClick={() => p.openApproval(a.approval_id)}>
                      查看
                    </Button>
                  </div>
                ))
              ) : (
                <Empty
                  title="没有待确认的计划"
                  detail="需要您授权的操作会显示在这里。"
                />
              )
            ) : (
              <Alert tone="error">审批列表暂不可用，请重试。</Alert>
            )
          ) : tab === "audit" ? (
            q.value?.audit.status === "fulfilled" ? (
              q.value.audit.value.items.length ? (
                <div className="audit-list">
                  {q.value.audit.value.items.map((a) => (
                    <div className="audit-row" key={a.id}>
                      <span className="audit-dot" />
                      <div>
                        <strong>{a.message}</strong>
                        <small>
                          {a.event_type}
                          {a.code ? ` · ${a.code}` : ""}
                        </small>
                        <code>{a.plan_id ?? a.id}</code>
                      </div>
                      <time>{dateTime(a.created_at_wall)}</time>
                      {a.plan_id && (
                        <Button
                          variant="ghost"
                          onClick={async () => {
                            try {
                              setPlan(await getPlan(a.plan_id!));
                            } catch (e) {
                              p.notify((e as Error).message);
                            }
                          }}
                        >
                          查看计划
                        </Button>
                      )}
                    </div>
                  ))}
                </div>
              ) : (
                <Empty
                  title="暂无操作记录"
                  detail="计划评估与执行记录将保留在这里。"
                />
              )
            ) : (
              <Alert tone="error">操作记录服务暂不可用。</Alert>
            )
          ) : q.value?.notifications.status === "fulfilled" ? (
            q.value.notifications.value.items.length ? (
              q.value.notifications.value.items.map((n) => (
                <div className="data-row" key={n.id}>
                  <span className="soft-icon">
                    <Icon name="bell" />
                  </span>
                  <div>
                    <strong>{n.title}</strong>
                    <p>{n.body}</p>
                    <small>{dateTime(n.created_at)}</small>
                  </div>
                  {!n.read_at_wall && (
                    <Button
                      variant="ghost"
                      onClick={async () => {
                        try {
                          await request(`/notifications/${n.id}/read`, {
                            request_id: uid(),
                          });
                          q.reload();
                        } catch (e) {
                          p.notify((e as Error).message);
                        }
                      }}
                    >
                      标为已读
                    </Button>
                  )}
                </div>
              ))
            ) : (
              <Empty
                title="暂时没有新通知"
                detail="扣费提醒与业务状态变化会在这里通知您。"
              />
            )
          ) : (
            <Alert tone="error">通知服务暂不可用。</Alert>
          )}
        </Panel>
      )}
      <Panel title="查询计划状态">
        <form
          className="plan-search"
          onSubmit={async (e) => {
            e.preventDefault();
            try {
              setPlan(await getPlan(planId.trim()));
            } catch (e) {
              p.notify((e as Error).message);
            }
          }}
        >
          <input
            aria-label="计划编号"
            placeholder="输入 pln_ 开头的计划编号"
            value={planId}
            onChange={(e) => setPlanId(e.target.value)}
            pattern="pln_.+"
            required
          />
          <Button type="submit">查询实际状态</Button>
        </form>
      </Panel>
      </>}
      {plan && (
        <Modal title="计划实际状态" onClose={() => setPlan(undefined)}>
          <div className="modal-body">
            <Badge
              tone={
                plan.status === "EXECUTED"
                  ? "success"
                  : plan.status === "FAILED"
                    ? "danger"
                    : "amber"
              }
            >
              {plan.status === "UNKNOWN" ? "结果确认中" : plan.status}
            </Badge>
            <code>{plan.plan_id}</code>
            {plan.status === "UNKNOWN" || plan.status === "EXECUTING" ? (
              <Alert>结果尚未确定，请勿重复发起。稍后刷新原计划状态。</Alert>
            ) : null}
            <dl>
              <div>
                <dt>安全决策</dt>
                <dd>{plan.decision.decision}</dd>
              </div>
              <div>
                <dt>执行状态</dt>
                <dd>{plan.execution?.status ?? "尚无执行结果"}</dd>
              </div>
              {plan.execution?.committed_at && (
                <div>
                  <dt>提交时间</dt>
                  <dd>{dateTime(plan.execution.committed_at)}</dd>
                </div>
              )}
            </dl>
            <Button
              variant="secondary"
              onClick={async () => setPlan(await getPlan(plan.plan_id))}
            >
              刷新状态
            </Button>
          </div>
        </Modal>
      )}
    </>
  );
}
