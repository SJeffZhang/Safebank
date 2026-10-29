import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { Button } from '../components/Button';
import { useAuthService } from '../services/auth/AuthService';
import type { DemoCustomer } from '../services/auth/types';
import { DEMO_PIN_LENGTH } from '../services/auth/types';
import { useSession } from '../app/SessionProvider';
import { useDocumentTitle } from '../app/hooks';
import { safeReturnPath } from '../app/paths';
import styles from './login.module.css';

const KEYS = ['1', '2', '3', '4', '5', '6', '7', '8', '9', '', '0', 'del'];

export function LoginPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const auth = useAuthService();
  const { login } = useSession();
  useDocumentTitle('登录 · SafeBank');

  const [customers, setCustomers] = useState<DemoCustomer[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string>('');
  const [pin, setPin] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [remaining, setRemaining] = useState<number | null>(null);
  const [lockedUntil, setLockedUntil] = useState<number | null>(null);
  const [offline, setOffline] = useState(false);
  const [nowTick, setNowTick] = useState(() => Date.now());

  const busyRef = useRef(false);
  const selectedRef = useRef('');

  const loadCustomers = useCallback(async () => {
    setLoadError(null);
    try {
      const list = await auth.getCustomers();
      setCustomers(list);
      setOffline(false);
      const first = list[0];
      if (first) {
        setSelectedId((cur) => cur || first.customerId);
      }
    } catch {
      setOffline(true);
      setLoadError('当前离线，无法加载演示客户');
    }
  }, [auth]);

  useEffect(() => {
    void loadCustomers();
  }, [loadCustomers]);

  useEffect(() => {
    selectedRef.current = selectedId;
  }, [selectedId]);

  // 读取权威锁定状态：刷新或切换客户都不能绕过 service 的锁定。
  useEffect(() => {
    if (!selectedId) return;
    let cancelled = false;
    void auth
      .getLockState(selectedId)
      .then((state) => {
        if (cancelled) return;
        setLockedUntil(state.locked ? (state.lockedUntilMillis ?? null) : null);
        setRemaining(state.locked ? 0 : state.remainingAttempts);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [auth, selectedId]);

  // 锁定倒计时
  useEffect(() => {
    if (!lockedUntil) return;
    const timer = window.setInterval(() => setNowTick(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [lockedUntil]);

  const locked = Boolean(lockedUntil && lockedUntil > nowTick);
  const lockSeconds = locked && lockedUntil ? Math.ceil((lockedUntil - nowTick) / 1000) : 0;

  // 锁定到期后按 service 权威状态恢复
  useEffect(() => {
    if (!lockedUntil || lockedUntil > nowTick) return;
    setLockedUntil(null);
    setError(null);
    void auth
      .getLockState(selectedRef.current)
      .then((state) => setRemaining(state.remainingAttempts))
      .catch(() => undefined);
  }, [auth, lockedUntil, nowTick]);

  const submit = useCallback(
    async (pinValue: string) => {
      if (busyRef.current || pinValue.length !== DEMO_PIN_LENGTH) return;
      if (!selectedRef.current) return;
      if (lockedUntil && lockedUntil > Date.now()) return;
      busyRef.current = true;
      setBusy(true);
      setError(null);
      try {
        const result = await auth.submitPin(selectedRef.current, pinValue);
        if (result.kind === 'ok') {
          login({ customerId: result.session.customerId, customerName: result.session.customerName });
          const from = (location.state as { from?: unknown } | null)?.from;
          navigate(safeReturnPath(from), { replace: true });
          return;
        }
        // 失败：清空 PIN，只展示 service 返回的权威信息
        setPin('');
        setError(result.message);
        setRemaining(result.remainingAttempts ?? null);
        if (result.code === 'LOCKED') {
          setLockedUntil(result.lockedUntilMillis ?? null);
          setNowTick(Date.now());
        }
        if (result.code === 'OFFLINE') setOffline(true);
      } catch {
        setPin('');
        setError('登录失败，请重试');
      } finally {
        busyRef.current = false;
        setBusy(false);
      }
    },
    [auth, login, navigate, lockedUntil, location.state],
  );

  // 满 6 位自动提交（唯一提交路径，行为一致且可测试）
  useEffect(() => {
    if (pin.length !== DEMO_PIN_LENGTH) return;
    if (busyRef.current) return;
    void submit(pin);
  }, [pin, submit]);

  const press = useCallback(
    (key: string) => {
      if (busy || locked || offline) return;
      if (key === 'del') {
        setPin((cur) => cur.slice(0, -1));
        setError(null);
        return;
      }
      if (!/^\d$/.test(key)) return;
      setError(null);
      setPin((cur) => (cur.length >= DEMO_PIN_LENGTH ? cur : cur + key));
    },
    [busy, locked, offline, submit],
  );

  const switchCustomer = (customerId: string) => {
    if (busy || customerId === selectedId) return;
    // 只清空本地输入；不动其他客户的 service 错误计数与锁定
    setSelectedId(customerId);
    setPin('');
    setError(null);
    setRemaining(null);
    setLockedUntil(null);
  };

  const selected = customers.find((c) => c.customerId === selectedId) ?? null;
  const disabled = busy || locked || offline;

  const dots = useMemo(
    () =>
      Array.from({ length: DEMO_PIN_LENGTH }, (_, i) => (
        <span key={i} className={[styles.dot, i < pin.length ? styles.dotOn : ''].filter(Boolean).join(' ')} />
      )),
    [pin.length],
  );

  return (
    <div className={styles.page}>
      <main id="main-content" className={styles.screen}>
        <div className={styles.brand}>
          <span className={styles.logo} aria-hidden="true">
            SB
          </span>
          <span className={styles.brandText}>
            <span className={styles.brandName}>SafeBank</span>
            <span className={styles.brandSub}>GATEWAY</span>
          </span>
        </div>

        <div className={styles.hello}>
          <h1 className={styles.helloTitle}>选择演示客户</h1>
          <p className={styles.helloSub}>演示世界固定 · 三位合成客户</p>
        </div>

        {offline ? (
          <div className={`${styles.banner} ${styles.danger}`} role="alert">
            <span className={styles.bannerText}>当前离线，无法登录。恢复网络后可重试。</span>
            <Button variant="secondary" onClick={() => void loadCustomers()}>
              重试
            </Button>
          </div>
        ) : null}

        {customers.length === 0 && !offline ? (
          <div className={styles.banner} role="status">
            <span className={styles.bannerText}>{loadError ?? '正在加载演示客户…'}</span>
          </div>
        ) : null}

        {customers.map((customer) => {
          const active = customer.customerId === selectedId;
          return (
            <button
              key={customer.customerId}
              type="button"
              className={[styles.cust, active ? styles.custOn : ''].filter(Boolean).join(' ')}
              aria-pressed={active}
              disabled={busy}
              onClick={() => switchCustomer(customer.customerId)}
            >
              <span className={styles.av} aria-hidden="true">
                {customer.avatarText}
              </span>
              <span className={styles.custMain}>
                <span className={styles.custName}>{customer.name}</span>
                <span className={styles.custSub}>{customer.summary}</span>
              </span>
              {active ? (
                <span className={styles.tick} aria-hidden="true">
                  ✓
                </span>
              ) : null}
            </button>
          );
        })}

        <div className={styles.pinRow}>
          <span className={styles.dots} role="img" aria-label={`PIN 已输入 ${pin.length} / ${DEMO_PIN_LENGTH} 位`}>
            {dots}
          </span>
          <span className={styles.pinCount}>
            已输入 {pin.length} / {DEMO_PIN_LENGTH} 位
          </span>
        </div>

        {error ? (
          <div className={`${styles.banner} ${styles.danger}`} role="alert">
            <span className={styles.bannerText}>
              {error}
              {typeof remaining === 'number' && !locked ? ` · 剩余 ${remaining} 次` : ''}
            </span>
          </div>
        ) : null}

        {locked ? (
          <div className={`${styles.banner} ${styles.danger}`} role="alert">
            <span className={styles.bannerText}>
              账户已临时锁定，请在 {lockSeconds} 秒后重试（锁定由演示认证服务判定）。
            </span>
          </div>
        ) : null}

        {busy ? (
          <div className={styles.banner} role="status">
            <span className={styles.bannerText}>正在登录…请勿重复提交</span>
          </div>
        ) : null}

        <div className={styles.keypad} role="group" aria-label={`${selected?.name ?? '演示客户'} 的 PIN 数字键盘`}>
          {KEYS.map((key, index) =>
            key === '' ? (
              <span key={`blank-${index}`} className={styles.keyBlank} aria-hidden="true" />
            ) : (
              <button
                key={key}
                type="button"
                className={styles.key}
                disabled={disabled}
                aria-label={key === 'del' ? '退格' : key}
                onClick={() => press(key)}
              >
                {key === 'del' ? '⌫' : key}
              </button>
            ),
          )}
        </div>

        <p className={styles.demoPin}>
          演示 PIN（仅用于本地演示）：张三 111111 · 李四 222222 · 孙八 333333
        </p>
      </main>
      <p className={styles.foot}>演示环境 · 模拟数据 · 连续 5 次错误锁定</p>
    </div>
  );
}