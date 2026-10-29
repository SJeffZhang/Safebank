import { useMemo, useState } from 'react';
import type { CSSProperties, ReactNode } from 'react';
import { AmountText } from '../../components/AmountText';
import { ApprovalSheet } from '../../components/ApprovalSheet';
import type { ApprovalMode, ApprovalState, ApprovalSummary } from '../../components/ApprovalSheet';
import { BottomNav } from '../../components/BottomNav';
import type { NavKey } from '../../components/BottomNav';
import { Button, IconButton } from '../../components/Button';
import { Card, List, ListItem } from '../../components/Card';
import { Icon } from '../../components/Icon';
import { SearchField } from '../../components/SearchField';
import { SegmentedControl } from '../../components/SegmentedControl';
import { Sheet } from '../../components/Sheet';
import { StateView } from '../../components/StateView';
import type { ViewState } from '../../components/StateView';
import { TextField } from '../../components/TextField';
import { useToast } from '../../components/Toast';
import { TopBar } from '../../components/TopBar';
import { useTheme } from '../../app/ThemeProvider';
import { useDocumentTitle } from '../../app/hooks';
import styles from '../../App.module.css';

const products = [
  { name: '稳健增利', risk: 'R3', rate: '3.40%', min: '¥1,000 起购' },
  { name: '现金管理', risk: 'R1', rate: '2.10%', min: '¥1 起购' },
  { name: '优选债基', risk: 'R2', rate: '3.85%', min: '¥100 起购' },
  { name: '中证红利', risk: 'R3', rate: '4.20%', min: '¥1,000 起购' },
];

const tokens: { label: string; value: string }[] = [
  { label: 'canvas', value: 'var(--sb-canvas)' },
  { label: 'surface', value: 'var(--sb-surface)' },
  { label: 'ink', value: 'var(--sb-ink)' },
  { label: 'ink-2', value: 'var(--sb-ink-2)' },
  { label: 'muted', value: 'var(--sb-muted)' },
  { label: 'brand', value: 'var(--sb-brand)' },
  { label: 'copper', value: 'var(--sb-copper)' },
  { label: 'gold', value: 'var(--sb-gold)' },
  { label: 'danger', value: 'var(--sb-danger)' },
  { label: 'success', value: 'var(--sb-success)' },
  { label: 'line', value: 'var(--sb-line-strong)' },
  { label: 'sunken', value: 'var(--sb-surface-sunken)' },
];

const typeScale = [
  { label: '展示 30 / 650', size: 'var(--sb-text-3xl)', weight: 'var(--sb-weight-bold)', sample: '安心，是可以被看见的' },
  { label: '标题 20 / 650', size: 'var(--sb-text-xl)', weight: 'var(--sb-weight-bold)', sample: '本月账单与异常' },
  { label: '正文 14 / 400', size: 'var(--sb-text-md)', weight: 'var(--sb-weight-regular)', sample: '资金变动以服务端结果为准。' },
  { label: '辅助 12 / 500', size: 'var(--sb-text-sm)', weight: 'var(--sb-weight-medium)', sample: '尾号 1234 · 活期账户' },
  { label: '说明 10 / 500', size: 'var(--sb-text-2xs)', weight: 'var(--sb-weight-medium)', sample: '演示数据 · 不构成投资建议' },
];

const approvalSummary: ApprovalSummary = {
  title: '向王五转账',
  subtitle: '本行账户 · 尾号 8891',
  amountText: '¥3,000.00',
  rows: [
    { label: '收款人', value: '王五' },
    { label: '手续费', value: '¥0.00' },
    { label: '转后可用', value: '¥3,000.00', emphasis: true },
  ],
  riskNotes: ['单笔超过 1,000 元需要强验证', '确认后 5 分钟内可撤销'],
};

const stateExamples: ViewState[] = [
  'loading',
  'empty',
  'error',
  'offline',
  'disabled',
  'failed',
  'unknown',
  'success',
];

function Section({ title, meta, children }: { title: string; meta: string; children: ReactNode }) {
  return (
    <section className={styles.section}>
      <div className={styles.sectionHead}>
        <h2 className={styles.sectionTitle}>{title}</h2>
        <span className={styles.sectionMeta}>{meta}</span>
      </div>
      {children}
    </section>
  );
}

export default function ComponentsPage() {
  const toast = useToast();
  const { theme, toggleTheme } = useTheme();
  useDocumentTitle('组件展示 · SafeBank');

  const [nav, setNav] = useState<NavKey>('home');
  const [wealthTab, setWealthTab] = useState('holdings');
  const [hidden, setHidden] = useState(false);
  const [search, setSearch] = useState('');
  const [sheetOpen, setSheetOpen] = useState(false);
  const [otp, setOtp] = useState('');
  const [approval, setApproval] = useState<{ open: boolean; mode: ApprovalMode; state: ApprovalState }>({
    open: false,
    mode: 'yellow',
    state: 'ready',
  });

  const filtered = useMemo(() => {
    const keyword = search.trim();
    if (!keyword) return products;
    const lower = keyword.toLowerCase();
    return products.filter(
      (product) => product.name.includes(keyword) || product.risk.toLowerCase().includes(lower),
    );
  }, [search]);

  const openApproval = (mode: ApprovalMode, state: ApprovalState) => {
    setOtp('');
    setApproval({ open: true, mode, state });
  };

  const confirmApproval = () => {
    setApproval((current) => ({ ...current, state: 'confirming' }));
    window.setTimeout(() => {
      setApproval((current) => (current.open ? { ...current, state: 'success' } : current));
    }, 1200);
  };

  return (
    <div className={styles.page}>
      <TopBar
        title="SafeBank UI"
        subtitle="设计变量与基础组件 · 375 × 812 · /dev/components"
        actions={
          <IconButton
            label={theme === 'dark' ? '切换为浅色主题' : '切换为深色主题'}
            aria-pressed={theme === 'dark'}
            onClick={toggleTheme}
          >
            <Icon name={theme === 'dark' ? 'sun' : 'moon'} size={18} />
          </IconButton>
        }
      />

      <main className={styles.main}>
        <div className={styles.intro}>
          <p className={styles.introTitle}>设计变量与基础组件</p>
          <p className={styles.introText}>
            颜色、字体、圆角、间距与状态全部来自 SafeBank 界面简报 C3。所有数据均为演示，不连接真实接口；
            写操作的确认统一由 ApprovalSheet 承担。
          </p>
          <div className={styles.badgeRow}>
            <span className={styles.badge}>React 18</span>
            <span className={styles.badge}>TypeScript</span>
            <span className={styles.badge}>Vite</span>
            <span className={styles.badge}>CSS Modules</span>
          </div>
        </div>

        <Section title="设计变量" meta="01 / tokens">
          <div className={styles.panel}>
            <div className={styles.swatches}>
              {tokens.map((token) => (
                <div className={styles.swatch} key={token.label}>
                  <div className={styles.swatchChip} style={{ background: token.value }} />
                  <div className={styles.swatchName}>{token.label}</div>
                </div>
              ))}
            </div>
            <div className={styles.stack}>
              {typeScale.map((item) => (
                <div className={styles.typeRow} key={item.label}>
                  <span
                    style={{
                      fontSize: item.size,
                      fontWeight: item.weight as CSSProperties['fontWeight'],
                      lineHeight: 1.3,
                    }}
                  >
                    {item.sample}
                  </span>
                  <span className={styles.typeMeta}>{item.label}</span>
                </div>
              ))}
            </div>
            <div className={styles.spacingRow}>
              {[4, 8, 12, 16, 24, 32].map((space) => (
                <div className={styles.spacingItem} key={space}>
                  <span className={styles.spacingBar} style={{ width: space * 2, height: space }} />
                  <span>{space}</span>
                </div>
              ))}
            </div>
            <p className={styles.note}>
              圆角：6 / 10 / 14 / 18 / 24 / 胶囊；阴影只用两级；金额统一 tabular-nums；深色主题单独定义而非反色。
            </p>
          </div>
        </Section>

        <Section title="按钮" meta="02 / actions">
          <div className={styles.panel}>
            <div className={styles.row}>
              <Button>主操作</Button>
              <Button variant="secondary">次操作</Button>
              <Button variant="ghost">幽灵</Button>
              <Button variant="danger">危险操作</Button>
            </div>
            <div className={styles.row}>
              <Button loading>提交中</Button>
              <Button disabled>不可用</Button>
              <IconButton label="新增收款人">
                <Icon name="plus" size={18} />
              </IconButton>
              <IconButton label="刷新" variant="secondary">
                <Icon name="refresh" size={18} />
              </IconButton>
            </div>
            <p className={styles.note}>加载中会自动禁用并拦截重复点击；图标按钮必须提供可访问名称。</p>
          </div>
        </Section>

        <Section title="输入与搜索" meta="03 / fields">
          <div className={styles.panel}>
            <TextField label="收款人" placeholder="姓名、别名或手机号" hint="同名或近名时会先追问，不会自动选中。" />
            <TextField
              label="金额"
              inputMode="decimal"
              placeholder="0.00"
              defaultValue="12,000"
              error="金额超过单笔限额，请调整后重试。"
            />
            <TextField label="备注" defaultValue="演示环境不可编辑" disabled />
            <SearchField
              label="搜索产品"
              value={search}
              onChange={setSearch}
              placeholder="搜索产品名称或 R1–R4"
              hint="支持名称与风险等级；清空后恢复全部产品。"
            />
            {filtered.length > 0 ? (
              <List ariaLabel="产品列表">
                {filtered.map((product) => (
                  <ListItem
                    key={product.name}
                    title={product.name}
                    subtitle={`${product.risk} · ${product.min}`}
                    value={product.rate}
                    chevron
                    onClick={() => toast.notify(`已选择 ${product.name}（演示）`)}
                  />
                ))}
              </List>
            ) : search.trim() ? (
              <StateView
                state="empty"
                compact
                title={`没有找到「${search.trim()}」`}
                description="试试产品名称或 R1–R4 风险等级，也可以清除搜索查看全部。"
                actionLabel="清除搜索"
                onAction={() => setSearch('')}
              />
            ) : null}
          </div>
        </Section>

        <Section title="卡片与列表" meta="04 / content">
          <div className={styles.stack}>
            <Card
              title="稳健增利"
              meta="R3 · 近 7 日年化 3.40%"
              value={<AmountText amountCents={4200000} />}
              footer={
                <>
                  <span className={styles.note}>占比 35% · 持有 180 天</span>
                  <Button variant="ghost" onClick={() => toast.notify('查看产品详情（演示）')}>
                    查看
                  </Button>
                </>
              }
            >
              <p className={styles.note}>净值型产品，收益随市场波动；本卡为组件示例，不构成投资建议。</p>
            </Card>
            <div className={styles.panel} style={{ padding: 0 }}>
              <List ariaLabel="最近动态">
                <ListItem title="山野咖啡" subtitle="今天 9:12 · 餐饮" value="−¥6.80" onClick={() => toast.notify('打开交易详情（演示）')} chevron />
                <ListItem title="Studio North" subtitle="昨天 · 项目款" value="+¥1,850.00" />
                <ListItem
                  title="这是一个很长很长的商户名称，用来验证两行截断不会破坏布局"
                  subtitle="长文本会占用两行并自动省略，金额与箭头保持对齐。"
                  value="−¥42.36"
                  onClick={() => toast.notify('打开交易详情（演示）')}
                  chevron
                />
                <ListItem title="已冻结的卡片操作" subtitle="禁用态不响应点击" value="不可用" disabled onClick={() => undefined} />
              </List>
            </div>
          </div>
        </Section>

        <Section title="分段与切换" meta="05 / tabs">
          <div className={styles.panel}>
            <SegmentedControl
              label="理财视图"
              options={[
                { value: 'holdings', label: '持仓' },
                { value: 'discover', label: '发现' },
              ]}
              value={wealthTab}
              onChange={setWealthTab}
              panelId="wealth-panel"
            />
            <div id="wealth-panel" role="tabpanel" aria-labelledby={`理财视图-${wealthTab}`}>
              {wealthTab === 'holdings' ? (
                <p className={styles.note}>持仓视图：只出现一次总资产，下面是持仓列表与趋势入口。</p>
              ) : (
                <p className={styles.note}>发现视图：分类、板块与新客活动，不与持仓上下拼接。</p>
              )}
            </div>
            <p className={styles.note}>支持方向键切换，Home / End 定位首尾；选中态同步 aria-selected 与 tabIndex。</p>
          </div>
        </Section>

        <Section title="金额" meta="06 / amount">
          <div className={styles.panel}>
            <div className={styles.previewCard}>
              <span className={styles.panelLabel}>总资产</span>
              <AmountText
                amountCents={12856030}
                size="xl"
                hidden={hidden}
                onToggleHidden={() => setHidden((value) => !value)}
                label="总资产"
              />
            </div>
            <div className={styles.row}>
              <AmountText amountCents={23618} signed />
              <AmountText amountCents={-8720} signed />
              <AmountText amountCents={0} />
            </div>
            <p className={styles.note}>隐藏后统一显示占位符，原始金额不会出现在可见文本或可访问名称里；切换不改变布局宽度。</p>
          </div>
        </Section>

        <Section title="状态" meta="07 / states">
          <div className={styles.stateGrid}>
            {stateExamples.map((state) => {
              const action =
                state === 'empty'
                  ? '去创建'
                  : state === 'error' || state === 'offline' || state === 'failed'
                    ? '重试'
                    : state === 'unknown'
                      ? '查看状态'
                      : undefined;
              return (
                <div className={styles.stateBox} key={state}>
                  <StateView
                    state={state}
                    compact
                    actionLabel={action}
                    onAction={() => toast.notify(`已触发「${state}」的恢复动作（演示）`)}
                  />
                </div>
              );
            })}
          </div>
          <p className={styles.note}>
            unknown 使用中性图标与「结果待确认」文案，不复用 success 的勾选与绿色；error / offline 同时提供重试。
          </p>
        </Section>

        <Section title="确认面板" meta="08 / approval">
          <div className={styles.panel}>
            <div className={styles.row}>
              <Button onClick={() => openApproval('yellow', 'ready')}>黄级确认</Button>
              <Button variant="danger" onClick={() => openApproval('red', 'ready')}>
                红级 OTP
              </Button>
            </div>
            <div className={styles.row}>
              <Button variant="secondary" onClick={() => openApproval('yellow', 'confirming')}>
                confirming
              </Button>
              <Button variant="secondary" onClick={() => openApproval('yellow', 'failed')}>
                failed
              </Button>
              <Button variant="secondary" onClick={() => openApproval('yellow', 'unknown')}>
                unknown
              </Button>
              <Button variant="secondary" onClick={() => openApproval('yellow', 'success')}>
                success
              </Button>
            </div>
            <p className={styles.note}>
              面板只展示操作摘要与状态，不计算安全结果；确认中禁止关闭与重复提交；红级没有重新发送 OTP 的入口。
            </p>
          </div>
        </Section>

        <Section title="轻提示" meta="09 / feedback">
          <div className={styles.panel}>
            <div className={styles.row}>
              <Button variant="secondary" onClick={() => toast.notify('已复制账户信息（演示）')}>
                普通提示
              </Button>
              <Button variant="secondary" onClick={() => toast.notify('设置已保存（演示）', 'success')}>
                成功提示
              </Button>
              <Button variant="secondary" onClick={() => toast.notify('网络不稳定，正在重试（演示）', 'warning')}>
                警告提示
              </Button>
              <Button variant="secondary" onClick={() => toast.notify('操作失败（演示）', 'danger')}>
                失败提示
              </Button>
            </div>
            <p className={styles.note}>交易结果与安全警告不使用自动消失的 Toast，必须用 StateView 或 ApprovalSheet 停驻展示。</p>
          </div>
        </Section>

        <Section title="普通 Sheet" meta="10 / sheet">
          <div className={styles.panel}>
            <Button onClick={() => setSheetOpen(true)}>打开普通 Sheet</Button>
            <p className={styles.note}>打开后焦点进入面板，Escape 或点击遮罩关闭，关闭后焦点回到触发按钮；内容超高时面板内部滚动。</p>
          </div>
        </Section>
      </main>

      <BottomNav
        value={nav}
        onChange={(value) => {
          setNav(value);
          if (value !== 'home') toast.notify(`「${value === 'assistant' ? '小安' : '个人'}」将在后续路由阶段接入`);
        }}
      />

      <Sheet
        open={sheetOpen}
        title="选择付款账户"
        description="演示 Sheet：内容过长时内部滚动，底部操作始终可见。"
        onClose={() => setSheetOpen(false)}
        footer={
          <>
            <Button variant="secondary" onClick={() => setSheetOpen(false)}>
              取消
            </Button>
            <Button
              onClick={() => {
                setSheetOpen(false);
                toast.notify('已选择活期账户（演示）', 'success');
              }}
            >
              使用此账户
            </Button>
          </>
        }
      >
        <List ariaLabel="付款账户">
          <ListItem title="活期账户" subtitle="尾号 1234 · 可用 ¥6,000.00" value="推荐" chevron onClick={() => undefined} />
          <ListItem title="储蓄账户" subtitle="尾号 8891 · 可用 ¥20,000.00" chevron onClick={() => undefined} />
          {Array.from({ length: 6 }).map((_, index) => (
            <ListItem key={index} title={`其他账户 ${index + 1}`} subtitle="用于验证长列表的滚动与底部操作可达性。" />
          ))}
        </List>
      </Sheet>

      <ApprovalSheet
        open={approval.open}
        approvalId={approval.mode === 'red' ? 'apv_demo_red' : 'apv_demo_yellow'}
        mode={approval.mode}
        state={approval.state}
        summary={approvalSummary}
        riskAckRequired={approval.mode === 'red'}
        otpValue={otp}
        onOtpChange={setOtp}
        onConfirm={confirmApproval}
        onReject={() => {
          setApproval((current) => ({ ...current, open: false }));
          toast.notify('已拒绝本次操作（演示）', 'warning');
        }}
        onClose={() => setApproval((current) => ({ ...current, open: false }))}
        onViewStatus={() => toast.notify('已请求查看最新状态（演示）')}
      />
    </div>
  );
}