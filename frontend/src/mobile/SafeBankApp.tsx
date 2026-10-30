import { explainCode } from "./copy";
import { useEffect, useRef, useState } from "react";
import { ThemeProvider, useTheme } from "../app/ThemeProvider";
import { request, read, propose, isPreview, getPlan, stream } from "./api";
import {
  uid,
  type Account,
  type Profile,
  type Decision,
  type Fields,
  type PlanView,
} from "./types";
import { Overview, Transfers, Bills, type PageProps } from "./Banking";
import { Cards, Wealth } from "./Services";
import { Assistant } from "./Assistant";
import { Security } from "./Security";
import { Approval } from "./Approval";
import { DemoConsole } from "./DemoConsole";
import {
  ChoiceSelect,
  Icon,
  Button,
  Alert,
  Loading,
  Modal,
  Badge,
  Empty,
} from "./ui";
import "./app.css";
const navigation = [
  { id: "overview", label: "首页", icon: "home" },
  { id: "transfer", label: "转账收款", icon: "transfer" },
  { id: "bills", label: "账单分析", icon: "chart" },
  { id: "cards", label: "我的卡片", icon: "card" },
  { id: "wealth", label: "理财投资", icon: "wealth" },
  { id: "assistant", label: "小安助手", icon: "assistant" },
  { id: "security", label: "我的", icon: "user" },
];
function route() {
  const id = location.hash.slice(1).split("?")[0] || "overview";
  if (id === "subscriptions") return "security";
  return navigation.some((n) => n.id === id) ? id : "not-found";
}
export default function SafeBankApp() {
  return (
    <ThemeProvider>
      <BankApp />
    </ThemeProvider>
  );
}
function BankApp() {
  const { theme, toggleTheme } = useTheme();
  const [page, setPage] = useState(route);
  const [profile, setProfile] = useState<Profile>();
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [sid, setSid] = useState("");
  const [loading, setLoading] = useState(isPreview);
  const [loginError, setLoginError] = useState("");
  const [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState("");
  const [approval, setApproval] = useState("");
  const [decision, setDecision] = useState<Decision>();
  const [menu, setMenu] = useState(false);
  const [help, setHelp] = useState(false);
  const [demoConsole, setDemoConsole] = useState(false);
  const [customerId, setCustomerId] = useState("");
  const [pin, setPin] = useState("");
  const [customers, setCustomers] = useState<
    { customer_id: string; display_name: string }[]
  >([]);
  const resume = useRef<(id: string) => Promise<void>>();
  const toastTimer = useRef<ReturnType<typeof setTimeout>>();
  const routeTrail = useRef<string[]>(
    page === "overview" ? ["overview"] : ["overview", page],
  );
  function goBack() {
    const trail = routeTrail.current;
    location.hash = trail.length > 1 ? trail[trail.length - 2] : "overview";
  }
  useEffect(() => {
    const h = () => {
      const next = route();
      const trail = routeTrail.current;
      if (trail.length > 1 && next === trail[trail.length - 2]) trail.pop();
      else if (next !== trail[trail.length - 1]) trail.push(next);
      setPage(next);
      setMenu(false);
      document.querySelector(".workspace")?.scrollTo(0, 0);
    };
    window.addEventListener("hashchange", h);
    return () => window.removeEventListener("hashchange", h);
  }, []);
  useEffect(() => {
    document.title = `${navigation.find((n) => n.id === page)?.label ?? "SafeBank"} · SafeBank`;
  }, [page]);
  useEffect(() => {
    if (!menu) return;
    const before = document.activeElement as HTMLElement;
    const drawer = document.querySelector<HTMLElement>(".sidebar")!;
    const elements = () =>
      Array.from(
        drawer.querySelectorAll<HTMLElement>("a[href],button:not(:disabled)"),
      );
    elements()[0]?.focus();
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMenu(false);
      if (event.key === "Tab") {
        const items = elements();
        const first = items[0],
          last = items[items.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("keydown", key);
      before?.focus();
    };
  }, [menu]);
  function notify(message: string) {
    setToast(message);
    clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(""), 6000);
  }
  async function bootstrap() {
    const session = await request<{ session_id: string }>("/sessions", {
      request_id: uid(),
    });
    const [user, data] = await Promise.all([
      read<Profile>(session.session_id, "get_profile", {}),
      read<{ accounts: Account[] }>(session.session_id, "get_accounts", {
        history_days: 0,
      }),
    ]);
    setSid(session.session_id);
    setProfile(user);
    setAccounts(data.accounts);
  }
  useEffect(() => {
    if (isPreview) {
      bootstrap()
        .catch((e) => setLoginError(e.message))
        .finally(() => setLoading(false));
    } else
      request<{ items: { customer_id: string; display_name: string }[] }>(
        "/demo/customers",
      )
        .then((c) => setCustomers(c.items))
        .catch(() => setCustomers([]));
    return () => clearTimeout(toastTimer.current);
  }, []);
  async function refresh() {
    try {
      const [a, u] = await Promise.all([
        read<{ accounts: Account[] }>(sid, "get_accounts", { history_days: 0 }),
        read<Profile>(sid, "get_profile"),
      ]);
      setAccounts(a.accounts);
      setProfile(u);
      setRevision((r) => r + 1);
    } catch (e) {
      notify(`状态刷新未完成：${(e as Error).message}`);
    }
  }
  function showDecision(d: Decision) {
    setDecision(d);
    if (d.decision !== "REQUIRE_APPROVAL") setApproval("");
    if (d.decision === "REQUIRE_APPROVAL" && d.approval_id) {
      setApproval(d.approval_id);
      setDecision(undefined);
    }
  }
  async function onPropose(kind: string, fields: Fields) {
    if (busy) return;
    setBusy(true);
    try {
      const d = await propose(sid, kind, fields);
      if (d.decision === "ALLOW") {
        await request(`/plans/${d.plan_id}/execute`, { request_id: uid() });
        const s = await getPlan(d.plan_id);
        if (s.status === "EXECUTED") {
          notify(
            isPreview ? "演示操作已完成，状态已更新" : "操作已完成，状态已更新",
          );
          await refresh();
        } else {
          setDecision(d);
          notify("执行结果确认中，请在我的页面的安全记录中查询原计划。");
        }
      } else showDecision(d);
    } catch (e) {
      notify(e instanceof Error ? e.message : "暂时无法完成预演");
    } finally {
      setBusy(false);
    }
  }
  async function newSession() {
    try {
      const s = await request<{ session_id: string }>("/sessions", {
        request_id: uid(),
      });
      setSid(s.session_id);
      resume.current = undefined;
      notify("已开始新会话");
    } catch (e) {
      notify((e as Error).message);
    }
  }
  async function login() {
    setLoading(true);
    setLoginError("");
    try {
      await request("/auth/login", { customer_id: customerId, pin });
      setPin("");
      await bootstrap();
    } catch (e) {
      setLoginError((e as Error).message);
      setPin("");
    } finally {
      setLoading(false);
    }
  }
  async function logout() {
    try {
      await request("/auth/logout", { request_id: uid() });
      setProfile(undefined);
      setAccounts([]);
      setSid("");
      setApproval("");
      setDecision(undefined);
      resume.current = undefined;
      setPin("");
      setCustomerId("");
    } catch (e) {
      notify((e as Error).message);
    }
  }
  const props: PageProps | undefined =
    profile && sid
      ? { sid, profile, accounts, revision, onPropose, busy, notify }
      : undefined;
  return (
    <div className="safebank-app">
      {!props ? (
        <main className="login-scene">
          <div className="login-brand">
            <span className="brand-mark">S</span>SafeBank
          </div>
          <div className="login-composition">
            <section className="login-copy">
              <div className="eyebrow">安心，始于每一个决定</div>
              <h1>
                您的日常金融，
                <br />
                多一份安心。
              </h1>
              <p>
                账户、支付与理财，在一个熟悉的地方。
                <br />
                有小安相伴，也有您始终掌握的决定权。
              </p>
              <div className="login-art">
                <div className="bank-card large">
                  <div className="bank-card-brand">
                    <span className="brand-glyph">S</span>SafeBank
                    <span>DEBIT</span>
                  </div>
                  <div className="chip">
                    <i />
                    <i />
                  </div>
                  <div className="bank-number">••••　••••　••••　0818</div>
                  <div className="bank-card-bottom">
                    <span>
                      SafeBank<small>安心相伴，随行所需</small>
                    </span>
                    <span className="card-circles">
                      <i />
                      <i />
                    </span>
                  </div>
                </div>
              </div>
            </section>
            <section className="login-form">
              <Badge>{isPreview ? "合成数据预览" : "银行沙箱演示"}</Badge>
              <h2>欢迎回到 SafeBank</h2>
              <p>选择演示客户，使用本地配置的 PIN 登录。</p>
              {loading ? (
                <Loading />
              ) : (
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    void login();
                  }}
                >
                  <label className="field">
                    <span>客户</span>
                    {customers.length ? (
                      <ChoiceSelect
                        label="选择演示客户"
                        value={customerId}
                        onChange={setCustomerId}
                        placeholder="请选择演示客户"
                        options={customers.map((c) => ({
                          value: c.customer_id,
                          label: c.display_name,
                        }))}
                      />
                    ) : (
                      <input
                        required
                        value={customerId}
                        onChange={(e) => setCustomerId(e.target.value)}
                        autoComplete="username"
                        placeholder="演示客户 ID"
                      />
                    )}
                  </label>
                  <label className="field">
                    <span>登录 PIN</span>
                    <input
                      required
                      type="password"
                      inputMode="numeric"
                      autoComplete="current-password"
                      maxLength={6}
                      minLength={6}
                      pattern="[0-9]{6}"
                      value={pin}
                      onChange={(e) =>
                        setPin(e.target.value.replace(/\D/g, ""))
                      }
                      placeholder="输入 6 位 PIN"
                    />
                  </label>
                  {loginError && <Alert tone="error">{loginError}</Alert>}
                  <Button
                    type="submit"
                    disabled={!customerId || pin.length !== 6}
                  >
                    安全登录 <Icon name="arrow" size={17} />
                  </Button>
                  <p className="helper">
                    <Icon name="lock" size={13} />
                    登录信息通过同站服务处理
                  </p>
                </form>
              )}
            </section>
          </div>
          <footer>SafeBank · 银行 AI 智能体沙箱演示</footer>
        </main>
      ) : (
        <>
          <a
            href="#main-content"
            className="skip-link"
            onClick={(e) => {
              e.preventDefault();
              document.getElementById("main-content")?.focus();
            }}
          >
            跳转到主要内容
          </a>
          <aside
            className={`sidebar ${menu ? "open" : ""}`}
            hidden={!menu}
            aria-label="全部服务"
          >
            <a className="wordmark" href="#overview">
              <span className="brand-mark">S</span>SafeBank
              <span className="brand-period">.</span>
            </a>
            <div className="nav-label">日常金融</div>
            <nav aria-label="主导航">
              {navigation.map((n) => (
                <a
                  key={n.id}
                  href={`#${n.id}`}
                  onClick={() => setMenu(false)}
                  className={`${page === n.id ? "active" : ""} ${n.id === "assistant" ? "nav-assistant" : ""}`}
                  aria-current={page === n.id ? "page" : undefined}
                >
                  <Icon name={n.icon} size={20} />
                  <span>{n.label}</span>
                  {n.id === "assistant" && <small>AI</small>}
                  {page === n.id && <span className="nav-active-dot" />}
                </a>
              ))}
            </nav>
            <div className="sidebar-bottom">
              <button className="help-button" onClick={() => setHelp(true)}>
                <Icon name="help" size={18} />
                使用帮助
                <Icon name="chevron" size={15} />
              </button>
              <div className="customer-box">
                <span className="customer-avatar">
                  {props.profile.display_name.slice(0, 1)}
                </span>
                <div>
                  <strong>{props.profile.display_name}</strong>
                  <small>{props.profile.phone_masked}</small>
                </div>
                <button
                  className="icon-button"
                  onClick={logout}
                  aria-label="退出登录"
                >
                  <Icon name="logout" size={18} />
                </button>
              </div>
            </div>
          </aside>
          {menu && (
            <button
              className="nav-scrim"
              aria-label="关闭导航"
              onClick={() => setMenu(false)}
            />
          )}
          <div className="app-body">
            <header className="topbar">
              <div className="topbar-leading">
                {page !== "overview" && (
                  <button
                    type="button"
                    className="icon-button"
                    aria-label="返回上一页"
                    onClick={goBack}
                  >
                    <Icon name="back" size={21} />
                  </button>
                )}
                <button
                  className="icon-button mobile-menu"
                  aria-label="打开导航"
                  onClick={() => setMenu(!menu)}
                >
                  <Icon name="menu" size={19} />
                </button>
              </div>
              <div className="breadcrumb">
                {page === "overview" && <span className="brand-glyph">S</span>}
                <strong>
                  {page === "overview"
                    ? "SafeBank"
                    : (navigation.find((n) => n.id === page)?.label ??
                      "页面不存在")}
                </strong>
              </div>
              <div className="topbar-actions">
                {isPreview ? (
                  <span className="mode-indicator preview">
                    <i />
                    合成数据预览
                  </span>
                ) : (
                  <button
                    type="button"
                    className="mode-indicator"
                    title="打开演示控制台"
                    onClick={() => setDemoConsole(true)}
                  >
                    <i />
                    已连接银行沙箱
                  </button>
                )}
                <a
                  className="icon-button"
                  href="#security?tab=notifications"
                  aria-label="查看通知"
                >
                  <Icon name="bell" size={19} />
                </a>
                <button
                  className="icon-button"
                  data-theme-toggle
                  onClick={toggleTheme}
                  aria-label={
                    theme === "light" ? "切换深色模式" : "切换浅色模式"
                  }
                >
                  <Icon name={theme === "light" ? "eye" : "spark"} size={19} />
                </button>
                <span className="topbar-avatar">
                  {props.profile.display_name.slice(0, 1)}
                </span>
              </div>
            </header>
            {isPreview && (
              <div className="preview-ribbon">
                <Icon name="shield" size={13} />
                <span>
                  交互预览 · 所有金额与操作均为合成数据，不会调用真实银行服务
                </span>
                <span className="preview-date">
                  模拟日期 {props.profile.sim_now.slice(0, 10)}
                </span>
              </div>
            )}
            <main className={`workspace ${page === "assistant" ? "assistant-workspace" : ""}`} id="main-content" tabIndex={-1}>
              <div
                className="page-content"
                data-page={page}
                key={page === "assistant" ? `${page}-${sid}` : page}
              >
                {page === "overview" ? (
                  <Overview {...props} />
                ) : page === "transfer" ? (
                  <Transfers {...props} />
                ) : page === "bills" ? (
                  <Bills {...props} />
                ) : page === "cards" ? (
                  <Cards {...props} />

                ) : page === "wealth" ? (
                  <Wealth {...props} />
                ) : page === "assistant" ? (
                  <Assistant
                    {...props}
                    openApproval={setApproval}
                    onNewSession={newSession}
                    onResumeRegister={(h) => {
                      resume.current = h;
                    }}
                  />
                ) : page === "security" ? (
                  <Security {...props} openApproval={setApproval} />
                ) : (
                  <Empty title="页面不存在" detail="请通过导航选择业务页面。">
                    <a href="#overview" className="button">
                      返回首页
                    </a>
                  </Empty>
                )}
              </div>
              {page !== "assistant" && <footer className="workspace-footer">
                <span>SafeBank · 安心相伴</span>
                <span>
                  银行模拟时间 {props.profile.sim_now.slice(0, 10)} ·
                  所有金额以人民币计
                </span>
              </footer>}
            </main>
            <nav className="mobile-bottom" aria-label="快捷导航">
              {["overview", "wealth", "assistant", "cards", "security"].map(
                (id) => {
                  const n = navigation.find((n) => n.id === id)!;
                  return (
                    <a
                      key={id}
                      href={`#${id}`}
                      className={page === id ? "active" : ""}
                    >
                      <Icon name={n.icon} size={20} />
                      <span>{n.label}</span>
                    </a>
                  );
                },
              )}
            </nav>
          </div>
        </>
      )}
      {toast && (
        <div className="toast-message" role="status">
          <Icon name="shield" size={18} />
          <span>{toast}</span>
          <button onClick={() => setToast("")} aria-label="关闭提示">
            <Icon name="close" size={16} />
          </button>
        </div>
      )}
      {approval && (
        <Approval
          id={approval}
          onClose={() => setApproval("")}
          onComplete={refresh}
          onDecision={showDecision}
          onResume={async (v: PlanView) => {
            if (
              page === "assistant" &&
              resume.current &&
              v.session_id === sid
            ) {
              await resume.current(v.approval_id);
              return;
            }
            await stream(
              v.session_id,
              { approval_id: v.approval_id },
              (event) => {
                if (event.event === "error")
                  throw new Error(
                    String(event.data.message ?? "恢复未完成，请查询计划状态"),
                  );
              },
              undefined,
              true,
            );
          }}
        />
      )}
      {decision && (
        <Modal
          title={
            decision.decision === "DENY"
              ? "本次操作无法继续"
              : decision.decision === "REPLAN"
                ? "需要调整操作计划"
                : "正在确认执行结果"
          }
          onClose={() => setDecision(undefined)}
        >
          <div className="modal-body">
            <Badge tone={decision.decision === "DENY" ? "danger" : "amber"}>
              {decision.decision}
            </Badge>
            {decision.findings.map((f, i) => (
              <Alert
                key={i}
                tone={decision.decision === "DENY" ? "error" : "warning"}
              >
                {f.message || explainCode(f.code)}
              </Alert>
            ))}
            <p className="muted">
              {decision.decision === "ALLOW"
                ? "请在我的页面的安全记录中查询原计划的实际结果，避免重复操作。"
                : "请根据预演提示自行修改需求后重新发起。"}
            </p>
            <code>{decision.plan_id}</code>
            <Button
              variant="secondary"
              onClick={() => {
                setDecision(undefined);
                location.hash = "security?tab=approvals";
              }}
            >
              查看计划状态
            </Button>
          </div>
        </Modal>
      )}
      {demoConsole && <DemoConsole onClose={() => setDemoConsole(false)} />}
          {help && (
        <Modal title="使用 SafeBank" onClose={() => setHelp(false)}>
          <div className="modal-body">
            <h3>从您的日常业务开始</h3>
            <p>
              在转账、卡片、订阅或理财页面填写信息，点击「查看预演」核对资金变化。也可以向小安描述跨场景需求。
            </p>
            <h3>确认与验证码</h3>
            <p>
              需要授权的操作在独立确认面板中完成。短信验证码只在确认面板输入，请勿发送给小安。
            </p>
            <h3>结果确认中</h3>
            <p>
              看到「确认中」时请勿重复提交，可前往我的页面的安全记录查询原计划。页面刷新或退出不等于取消已提交的操作。
            </p>
            {isPreview && (
              <Alert>
                当前为本地合成数据预览。刷新将重置演示状态。真实接口请按 README
                配置 BFF。
              </Alert>
            )}
          </div>
        </Modal>
      )}
    </div>
  );
}
