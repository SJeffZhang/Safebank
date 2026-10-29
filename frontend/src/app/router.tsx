import { Navigate, Outlet, createBrowserRouter, useLocation } from 'react-router-dom';
import type { RouteObject } from 'react-router-dom';
import { StateView } from '../components/StateView';
import { useSession } from './SessionProvider';
import { hasSeenWelcomeThisDocument } from './welcomeLaunch';
import { PATHS } from './paths';
import { AppShellPrimary, AppShellSecondary } from '../layout/AppShell';
import { WelcomePage } from '../pages/Welcome';
import { LoginPage } from '../pages/Login';
import { HomePage } from '../pages/Home';
import { AssistantPage } from '../pages/Assistant';
import { ProfilePage } from '../pages/Profile';
import { TransferPage } from '../pages/Transfer';
import { BillsPage } from '../pages/Bills';
import { CardsPage } from '../pages/Cards';
import { SubscriptionsPage } from '../pages/Subscriptions';
import { WealthPage } from '../pages/Wealth';
import { WealthHoldingsPage } from '../pages/WealthHoldings';
import { NotFoundPage } from '../pages/NotFound';
import ComponentsPage from '../pages/dev/ComponentsPage';
import layoutStyles from '../layout/layout.module.css';

/** resolving 时的轻量加载态：不闪现欢迎/登录/首页，也不使用整页遮罩。 */
function ResolvingScreen() {
  return (
    <div className={layoutStyles.page}>
      <main id="main-content" className={layoutStyles.main}>
        <StateView state="loading" title="正在准备" description="正在恢复演示会话，请稍候。" />
      </main>
    </div>
  );
}

/** 会话恢复前只显示加载态；恢复后每次整页加载先进入一次欢迎页。 */
function SessionGate() {
  const { status } = useSession();
  const location = useLocation();
  if (status === 'resolving') return <ResolvingScreen />;
  if (!hasSeenWelcomeThisDocument() && location.pathname !== PATHS.welcome) {
    return <Navigate to={PATHS.welcome} replace state={{ from: location.pathname }} />;
  }
  return <Outlet />;
}

/** 保护一级与二级业务页：未登录 → 欢迎（首次）或登录（已访问），并记录深链接目标。 */
function RequireAuth() {
  const { status, hasVisitedApp } = useSession();
  const location = useLocation();
  if (status === 'anonymous') {
    if (!hasVisitedApp) return <Navigate to={PATHS.welcome} replace />;
    return <Navigate to={PATHS.login} replace state={{ from: location.pathname }} />;
  }
  return <Outlet />;
}

/** 欢迎页每次整页加载都会先展示；登录态由欢迎页决定“继续使用”的落点。 */
function WelcomeGate() {
  return <WelcomePage />;
}

/** 登录页：已登录则直接进首页。 */
function LoginGate() {
  const { status } = useSession();
  if (status === 'authenticated') return <Navigate to={PATHS.home} replace />;
  return <LoginPage />;
}

type ShellComponent = () => JSX.Element;
type PageComponent = () => JSX.Element;

function protectedRoute(path: string, Shell: ShellComponent, Page: PageComponent): RouteObject {
  return {
    path,
    element: <RequireAuth />,
    children: [{ element: <Shell />, children: [{ index: true, element: <Page /> }] }],
  };
}

export const routes: RouteObject[] = [
  {
    element: <SessionGate />,
    children: [
      { path: PATHS.welcome, element: <WelcomeGate /> },
      { path: PATHS.login, element: <LoginGate /> },
      { path: PATHS.devComponents, element: <ComponentsPage /> },
      protectedRoute(PATHS.home, AppShellPrimary, HomePage),
      protectedRoute(PATHS.assistant, AppShellPrimary, AssistantPage),
      protectedRoute(PATHS.profile, AppShellPrimary, ProfilePage),
      protectedRoute(PATHS.transfer, AppShellSecondary, TransferPage),
      protectedRoute(PATHS.bills, AppShellSecondary, BillsPage),
      protectedRoute(PATHS.cards, AppShellSecondary, CardsPage),
      protectedRoute(PATHS.subscriptions, AppShellSecondary, SubscriptionsPage),
      protectedRoute(PATHS.wealth, AppShellSecondary, WealthPage),
      protectedRoute(PATHS.wealthHoldings, AppShellSecondary, WealthHoldingsPage),
      { path: '*', element: <NotFoundPage /> },
    ],
  },
];

// GitHub Pages 部署在 /<repo>/ 子路径下；Vite 会把 base 注入 BASE_URL（本地为 '/'）。
// react-router 的 basename 不需要末尾斜杠，因此去掉（根路径时保持 '/'）。
const BASE_URL = import.meta.env.BASE_URL || '/';
const BASENAME = BASE_URL === '/' ? '/' : BASE_URL.replace(/\/$/, '');

export const router = createBrowserRouter(routes, {
  basename: BASENAME,
  future: { v7_relativeSplatPath: true },
});