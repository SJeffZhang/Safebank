import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { BottomNav } from '../components/BottomNav';
import type { NavKey } from '../components/BottomNav';
import { TopBar } from '../components/TopBar';
import { DynamicIsland, StatusBar } from '../components/StatusBar';
import { useBack, useFocusMainOnPush, useScrollRestore } from '../app/hooks';
import { PATHS, secondaryFallbackFor } from '../app/paths';
import styles from './layout.module.css';

type Title = [string, string?];

const PRIMARY_TITLES: Record<string, Title> = {
  [PATHS.home]: ['首页', '演示会话 · 概览'],
  [PATHS.assistant]: ['小安', '计划、解释与预演'],
  [PATHS.profile]: ['个人', '设置与偏好'],
};

const SECONDARY_TITLES: Record<string, Title> = {
  [PATHS.transfer]: ['转账', '向你认识的人转出'],
  [PATHS.bills]: ['账单与异常', '本月概况'],
  [PATHS.cards]: ['卡片', '卡片与安全'],
  [PATHS.subscriptions]: ['订阅与提醒', '管理订阅'],
  [PATHS.wealth]: ['理财', '持仓与产品'],
  [PATHS.wealthHoldings]: ['全部持仓', '投资组合明细'],
};

const NAV_PATHS: Record<NavKey, string> = {
  home: PATHS.home,
  assistant: PATHS.assistant,
  profile: PATHS.profile,
};

function navKeyFor(pathname: string): NavKey {
  if (pathname === PATHS.assistant) return 'assistant';
  if (pathname === PATHS.profile) return 'profile';
  return 'home';
}

function titleFor(map: Record<string, Title>, pathname: string): Title {
  return map[pathname] ?? ['SafeBank'];
}

export function AppShellPrimary() {
  const location = useLocation();
  const navigate = useNavigate();
  useScrollRestore(location.pathname);
  useFocusMainOnPush();
  const [title, subtitle] = titleFor(PRIMARY_TITLES, location.pathname);
  // 首页在目标里没有额外顶栏，身份信息由页面内的头部承担
  const showTopBar = location.pathname !== PATHS.home;

  return (
    <div className={styles.page}>
      <DynamicIsland />
      <a className={styles.skip} href="#main-content">
        跳到主要内容
      </a>
      <StatusBar />
      {showTopBar ? <TopBar title={title} subtitle={subtitle} /> : null}
      <main id="main-content" className={styles.main} tabIndex={-1}>
        <Outlet />
      </main>
      <BottomNav
        value={navKeyFor(location.pathname)}
        onChange={(key) => navigate(NAV_PATHS[key], { replace: true })}
      />
    </div>
  );
}

export function AppShellSecondary() {
  const location = useLocation();
  useScrollRestore(location.pathname);
  useFocusMainOnPush();
  const [title, subtitle] = titleFor(SECONDARY_TITLES, location.pathname);
  const back = useBack(secondaryFallbackFor(location.pathname));

  return (
    <div className={styles.page}>
      <DynamicIsland />
      <a className={styles.skip} href="#main-content">
        跳到主要内容
      </a>
      <StatusBar />
      <TopBar title={title} subtitle={subtitle} onBack={back} backLabel="返回" />
      <main id="main-content" className={`${styles.main} ${styles.withoutNav}`} tabIndex={-1}>
        <Outlet />
      </main>
    </div>
  );
}