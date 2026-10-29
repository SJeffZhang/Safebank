import { useEffect, useId, useRef } from 'react';
import type { KeyboardEvent, ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { IconButton } from './Button';
import { Icon } from './Icon';
import styles from './Sheet.module.css';

export interface SheetProps {
  open: boolean;
  title: string;
  description?: string;
  onClose: () => void;
  /** When false, Escape and backdrop clicks are ignored (e.g. confirming). */
  dismissible?: boolean;
  children: ReactNode;
  footer?: ReactNode;
}

export function Sheet({
  open,
  title,
  description,
  onClose,
  dismissible = true,
  children,
  footer,
}: SheetProps) {
  const titleId = useId();
  const descriptionId = useId();
  const panelRef = useRef<HTMLDivElement>(null);
  const onCloseRef = useRef(onClose);
  const dismissibleRef = useRef(dismissible);

  onCloseRef.current = onClose;
  dismissibleRef.current = dismissible;

  useEffect(() => {
    if (!open) return undefined;
    const previouslyFocused = document.activeElement as HTMLElement | null;
    document.body.classList.add('sb-scroll-lock');
    const focusTimer = window.setTimeout(() => panelRef.current?.focus(), 0);

    const getFocusable = () => {
      const nodes = panelRef.current?.querySelectorAll<HTMLElement>(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
      );
      return nodes ? Array.from(nodes).filter((node) => !node.hasAttribute('disabled')) : [];
    };

    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') {
        if (dismissibleRef.current) {
          event.preventDefault();
          onCloseRef.current();
        }
        return;
      }
      if (event.key !== 'Tab') return;
      const focusable = getFocusable();
      if (focusable.length === 0) {
        event.preventDefault();
        panelRef.current?.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;
      if (event.shiftKey && (active === first || active === panelRef.current)) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener('keydown', onKeyDown, true);
    return () => {
      window.clearTimeout(focusTimer);
      document.removeEventListener('keydown', onKeyDown, true);
      document.body.classList.remove('sb-scroll-lock');
      previouslyFocused?.focus?.();
    };
  }, [open]);

  if (!open) return null;

  const onPanelKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    event.stopPropagation();
  };

  return createPortal(
    <div
      className={styles.scrim}
      onMouseDown={(event) => {
        if (dismissible && event.target === event.currentTarget) onClose();
      }}
      data-testid="sheet-scrim"
    >
      <div
        ref={panelRef}
        className={styles.sheet}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descriptionId : undefined}
        tabIndex={-1}
        onKeyDown={onPanelKeyDown}
        onClick={(event) => event.stopPropagation()}
      >
        <div className={styles.grab} aria-hidden="true" />
        <div className={styles.head}>
          <div className={styles.heading}>
            <h2 className={styles.title} id={titleId}>
              {title}
            </h2>
            {description ? (
              <p className={styles.description} id={descriptionId}>
                {description}
              </p>
            ) : null}
          </div>
          <IconButton label="关闭" onClick={onClose} disabled={!dismissible}>
            <Icon name="close" size={18} />
          </IconButton>
        </div>
        <div className={styles.body}>{children}</div>
        {footer ? <div className={styles.footer}>{footer}</div> : null}
      </div>
    </div>,
    document.body,
  );
}
