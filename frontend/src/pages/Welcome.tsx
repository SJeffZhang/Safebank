import { useCallback, useEffect, useRef } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { useSession } from '../app/SessionProvider';
import { useDocumentTitle } from '../app/hooks';
import { PATHS } from '../app/paths';
import { markWelcomeSeenThisDocument } from '../app/welcomeLaunch';
import styles from './welcome.module.css';

interface NavigateDetail {
  target: 'login' | 'register';
  href: string;
}

interface LaunchLocationState {
  from?: unknown;
}

/**
 * 欢迎页使用交付包里的 safebank-welcome Web Component（Shadow DOM，玻璃银行卡 + 香槟金体系）。
 *
 * - 每次整页加载或刷新都会先展示一次；SPA 内跳转不会重复出现。
 * - 未登录进入登录页；已登录显示“继续使用”并直接回到首页。
 * - 组件派发可取消的 safebank-navigate，由这里接进 React Router，保持 SPA 跳转。
 */
export function WelcomePage() {
  const navigate = useNavigate();
  const location = useLocation();
  const { status, markAppVisited } = useSession();
  const hostRef = useRef<HTMLDivElement | null>(null);
  const authenticated = status === 'authenticated';
  useDocumentTitle('欢迎 · SafeBank');

  useEffect(() => {
    markWelcomeSeenThisDocument();
  }, []);

  const enterApp = useCallback(() => {
    markWelcomeSeenThisDocument();
    markAppVisited();
    if (authenticated) {
      navigate(PATHS.home, { replace: true });
      return;
    }
    const from = (location.state as LaunchLocationState | null)?.from;
    navigate(PATHS.login, { replace: true, state: from ? { from } : undefined });
  }, [authenticated, location.state, markAppVisited, navigate]);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

    const onNavigate = (event: Event) => {
      const custom = event as CustomEvent<NavigateDetail>;
      event.preventDefault();
      enterApp();
      void custom.detail;
    };

    host.addEventListener('safebank-navigate', onNavigate as EventListener);
    return () => host.removeEventListener('safebank-navigate', onNavigate as EventListener);
  }, [enterApp]);

  return (
    <div className={styles.page}>
      <main id="main-content" className={styles.screen}>
        <div ref={hostRef} className={styles.host}>
          <safebank-welcome
            login-url="/login"
            register-url="/login"
            mode={authenticated ? 'authenticated' : 'anonymous'}
          >
            {/* 组件未加载时的降级内容（有 Shadow DOM 后不显示） */}
            <div className={styles.fallback}>
              <p className={styles.fallbackBrand}>SafeBank</p>
              <p className={styles.fallbackEyebrow}>WELCOME TO SAFE BANK</p>
              <h1 className={styles.fallbackTitle}>
                Take control of
                <br />
                your money
              </h1>
              <p className={styles.fallbackDesc}>
                账户、转账与理财，在同一个安静、清晰的空间里完成。每一步都可回退，每一次确认都有记录。
              </p>
              <div className={styles.fallbackActions}>
                <button type="button" className={styles.fallbackPrimary} onClick={enterApp}>
                  {authenticated ? '继续使用' : '开始使用'}
                </button>
                {authenticated ? null : (
                  <button type="button" className={styles.fallbackSecondary} onClick={enterApp}>
                    已有账户？登录
                  </button>
                )}
              </div>
            </div>
          </safebank-welcome>
        </div>
      </main>
    </div>
  );
}
