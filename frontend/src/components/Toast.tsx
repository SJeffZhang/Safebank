import { createContext, useCallback, useContext, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { IconButton } from './Button';
import { Icon } from './Icon';
import type { IconName } from './Icon';
import styles from './Toast.module.css';

export type ToastTone = 'info' | 'success' | 'warning' | 'danger';

interface ToastItem {
  id: number;
  message: string;
  tone: ToastTone;
}

interface ToastContextValue {
  notify: (message: string, tone?: ToastTone) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

const toneIcon: Record<ToastTone, IconName> = {
  info: 'info',
  success: 'check',
  warning: 'alert',
  danger: 'alert',
};

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const nextId = useRef(1);
  const timers = useRef(new Map<number, number>());

  const remove = useCallback((id: number) => {
    const timer = timers.current.get(id);
    if (timer) window.clearTimeout(timer);
    timers.current.delete(id);
    setItems((current) => current.filter((item) => item.id !== id));
  }, []);

  const notify = useCallback(
    (message: string, tone: ToastTone = 'info') => {
      const id = nextId.current++;
      setItems((current) => [...current, { id, message, tone }]);
      const timer = window.setTimeout(() => remove(id), 4000);
      timers.current.set(id, timer);
    },
    [remove],
  );

  const value = useMemo(() => ({ notify }), [notify]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      {createPortal(
        <div className={styles.stack} aria-live="polite" aria-atomic="false">
          {items.map((item) => (
            <div key={item.id} className={`${styles.toast} ${styles[item.tone]}`} role="status">
              <span className={styles.icon} aria-hidden="true">
                <Icon name={toneIcon[item.tone]} size={15} />
              </span>
              <span className={styles.message}>{item.message}</span>
              <IconButton label="关闭提示" className={styles.close} onClick={() => remove(item.id)}>
                <Icon name="close" size={14} />
              </IconButton>
            </div>
          ))}
        </div>,
        document.body,
      )}
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextValue {
  const value = useContext(ToastContext);
  if (!value) throw new Error('useToast must be used inside ToastProvider');
  return value;
}
