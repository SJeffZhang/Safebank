import { useNavigate } from 'react-router-dom';
import { Button } from '../components/Button';
import { useDocumentTitle } from '../app/hooks';
import { PATHS } from '../app/paths';
import styles from '../layout/layout.module.css';

export function NotFoundPage() {
  const navigate = useNavigate();
  useDocumentTitle('页面不存在 · SafeBank');

  return (
    <div className={styles.page}>
      <main id="main-content" className={styles.main}>
        <div className={styles.content}>
          <div className={styles.intro}>
            <p className={styles.sectionLabel}>404</p>
            <h1 className={styles.introTitle}>页面不存在</h1>
            <p className={styles.introText}>你访问的地址不存在，或已被移动。可以回到首页重新开始。</p>
          </div>
          <Button onClick={() => navigate(PATHS.home, { replace: true })}>返回首页</Button>
        </div>
      </main>
    </div>
  );
}