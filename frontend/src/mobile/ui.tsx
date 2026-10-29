import { createPortal } from "react-dom";
import { useEffect, useRef, useState, useId, type ReactNode } from "react";
import { formatCents } from "../services/transfer/money";
export { formatCents as money };
export function Icon({
  name,
  size = 20,
  ...props
}: {
  name: string;
  size?: number;
  className?: string;
}) {
  const paths: Record<string, ReactNode> = {
    user: (<><circle cx="12" cy="8" r="3.5"/><path d="M4.5 21v-2a7.5 7.5 0 0 1 15 0v2"/></>),
    home: (
      <>
        <path d="m3 10 9-7 9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1z" />
      </>
    ),
    arrow: (
      <>
        <path d="M5 12h14m-6-6 6 6-6 6" />
      </>
    ),
    transfer: (
      <>
        <path d="M4 7h16m-4-4 4 4-4 4M20 17H4m4-4-4 4 4 4" />
      </>
    ),
    chart: (
      <>
        <path d="M4 3v17h17M8 14l4-5 4 3 5-7" />
      </>
    ),
    card: (
      <>
        <rect x="2.5" y="5" width="19" height="14" rx="3" />
        <path d="M3 10h18M6 15h4" />
      </>
    ),
    repeat: (
      <>
        <path d="m18 2 4 4-4 4M2 11V8a2 2 0 0 1 2-2h18M6 22l-4-4 4-4m16-1v3a2 2 0 0 1-2 2H2" />
      </>
    ),
    wealth: (
      <>
        <path d="M4 20V10m5 10V5m6 15v-8m5 8V2" />
      </>
    ),
    shield: (
      <>
        <path d="m12 2 9 4v6c0 5-9 10-9 10S3 17 3 12V6z" />
        <path d="m8 12 3 3 5-6" />
      </>
    ),
    assistant: (
      <>
        <path
          d="M12 2.5 20 5.7v5.5c0 4.5-3.2 7.8-8 10.3-4.8-2.5-8-5.8-8-10.3V5.7L12 2.5Z"
          strokeWidth="1.6"
        />
        <path
          d="m7.8 14.7 3.25-6.2a1.05 1.05 0 0 1 1.9 0l3.25 6.2M9.3 12.2h5.4"
          strokeWidth="1.6"
        />
        <circle cx="12" cy="16.4" r="1.05" fill="currentColor" stroke="none" />
      </>
    ),
    spark: (
      <>
        <path d="m12 2 2.8 7.2L22 12l-7.2 2.8L12 22l-2.8-7.2L2 12l7.2-2.8z" />
      </>
    ),
    bell: (
      <>
        <path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4" />
      </>
    ),
    plus: <path d="M12 5v14M5 12h14" />,
    close: <path d="m6 6 12 12M6 18 18 6" />,
    check: <path d="m5 12 4 4L19 6" />,
    back: <path d="m15 5-7 7 7 7" />,
    chevron: <path d="m9 5 7 7-7 7" />,
    down: <path d="m6 9 6 6 6-6" />,
    search: (
      <>
        <circle cx="10.5" cy="10.5" r="6.5" />
        <path d="m16 16 5 5" />
      </>
    ),
    eye: (
      <>
        <path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12" />
        <circle cx="12" cy="12" r="3" />
      </>
    ),
    logout: (
      <>
        <path d="M9 4H4v16h5m5-14 6 6-6 6M8 12h12" />
      </>
    ),
    clock: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="M12 6v6l4 2" />
      </>
    ),
    lock: (
      <>
        <rect x="5" y="10" width="14" height="11" rx="2" />
        <path d="M8 10V6a4 4 0 0 1 8 0v4" />
      </>
    ),
    globe: (
      <>
        <circle cx="12" cy="12" r="9" />
        <ellipse cx="12" cy="12" rx="4" ry="9" />
        <path d="M3 12h18" />
      </>
    ),
    download: (
      <>
        <path d="M12 3v12m-5-5 5 5 5-5M4 17v4h16v-4" />
      </>
    ),
    menu: <path d="M4 6h16M4 12h16M4 18h16" />,
    help: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="M9 9a3 3 0 0 1 6 0c0 2-3 2-3 5m0 3v.1" />
      </>
    ),
    wallet: (
      <>
        <path d="M20 7H5a2 2 0 0 1 0-4h13v4M4 7v13h17V7M21 12h-6v4h6" />
      </>
    ),
    dots: (
      <>
        <circle cx="5" cy="12" r="1" />
        <circle cx="12" cy="12" r="1" />
        <circle cx="19" cy="12" r="1" />
      </>
    ),
  };
  return (
    <svg
      {...props}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.65"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {paths[name] ?? paths.spark}
    </svg>
  );
}
export function Button({
  children,
  onClick,
  variant = "",
  disabled = false,
  type = "button",
  className = "",
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: string;
  disabled?: boolean;
  type?: "button" | "submit";
  className?: string;
}) {
  return (
    <button
      type={type}
      className={`button ${variant} ${className}`}
      disabled={disabled}
      onClick={onClick}
    >
      {children}
    </button>
  );
}
export function Empty({
  title = "暂无记录",
  detail = "新的记录会显示在这里。",
  children,
}: {
  title?: string;
  detail?: string;
  children?: ReactNode;
}) {
  return (
    <div className="empty">
      <span className="empty-icon">
        <Icon name="wallet" size={28} />
      </span>
      <h3>{title}</h3>
      <p>{detail}</p>
      {children}
    </div>
  );
}
export function Loading() {
  return (
    <div className="loading" role="status">
      <span className="spinner" />
      正在获取最新信息…
    </div>
  );
}
export function Alert({
  children,
  tone = "warning",
}: {
  children: ReactNode;
  tone?: string;
}) {
  return (
    <div
      className={`alert ${tone}`}
      role={tone === "error" ? "alert" : undefined}
    >
      <Icon name="shield" size={18} />
      <div>{children}</div>
    </div>
  );
}
export function Badge({
  children,
  tone = "",
}: {
  children: ReactNode;
  tone?: string;
}) {
  return <span className={`badge ${tone}`}>{children}</span>;
}
export function Money({
  value,
  className = "",
  hide = false,
}: {
  value: number;
  className?: string;
  hide?: boolean;
}) {
  return (
    <span className={`money ${className}`}>
      <small className="money-currency">¥</small>
      <span className="money-value">
        {hide ? "••••••" : formatCents(value)}
      </span>
    </span>
  );
}
export function Modal({
  title,
  children,
  onClose,
  wide = false,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  wide?: boolean;
}) {
  const titleId = useId();
  const ref = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    const before = document.activeElement as HTMLElement;
    const node = ref.current!;
    const focusable = () =>
      Array.from(
        node.querySelectorAll<HTMLElement>(
          'button:not(:disabled),input:not(:disabled),select,textarea,a[href],[tabindex="0"]',
        ),
      );
    focusable()[0]?.focus();
    const key = (e: KeyboardEvent) => {
      const openSheets = document.querySelectorAll("[data-sb-modal]");
      if (openSheets[openSheets.length - 1] !== node) return;
      if (e.key === "Escape") close.current();
      if (e.key === "Tab") {
        const items = focusable();
        if (!items.length) return;
        const first = items[0],
          last = items[items.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", key);
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", key);
      document.body.style.overflow = overflow;
      before?.focus();
    };
  }, []);
  return createPortal(
    <div
      className="modal-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className={`modal ${wide ? "wide" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        data-sb-modal="true"
        ref={ref}
      >
        <div className="modal-head">
          <h2 id={titleId}>{title}</h2>
          <button
            className="icon-button"
            aria-label="关闭弹窗"
            onClick={onClose}
          >
            <Icon name="close" />
          </button>
        </div>
        {children}
      </div>
    </div>,
    document.querySelector(".safebank-app") ?? document.body,
  );
}
export function LineChart({
  values,
  labels = [],
}: {
  values: number[];
  labels?: string[];
}) {
  if (!values.length)
    return <Empty title="暂无余额曲线" detail="有账户历史后即可查看。" />;
  const low = Math.min(...values) * 0.95,
    high = Math.max(...values) * 1.03;
  const points = values
    .map(
      (v, i) =>
        `${(i * 700) / Math.max(values.length - 1, 1)},${135 - ((v - low) / (high - low || 1)) * 120}`,
    )
    .join(" ");
  return (
    <div className="line-chart">
      <svg
        viewBox="0 0 700 160"
        role="img"
        aria-label={`账户余额趋势，期末 ${formatCents(values[values.length - 1])} 元`}
      >
        <defs>
          <linearGradient id="chart-fill" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="var(--sb-copper)" stopOpacity=".15" />
            <stop offset="100%" stopColor="var(--sb-copper)" stopOpacity="0" />
          </linearGradient>
        </defs>
        {[20, 70, 120].map((y) => (
          <line
            key={y}
            x1="0"
            y1={y}
            x2="700"
            y2={y}
            stroke="var(--sb-line-strong)"
            strokeDasharray="4 6"
          />
        ))}
        <polygon points={`0,160 ${points} 700,160`} fill="url(#chart-fill)" />
        <polyline
          points={points}
          fill="none"
          stroke="var(--sb-copper)"
          strokeWidth="2.5"
          vectorEffect="non-scaling-stroke"
          strokeLinejoin="round"
        />
      </svg>
      <div className="chart-labels">
        {labels.map((l) => (
          <span key={l}>{l}</span>
        ))}
      </div>
    </div>
  );
}
export const date = (s: string) =>
  new Date(s).toLocaleDateString("zh-CN", {
    timeZone: "Asia/Shanghai",
    month: "2-digit",
    day: "2-digit",
  });
export const dateTime = (s: string) =>
  new Date(s).toLocaleString("zh-CN", {
    timeZone: "Asia/Shanghai",
    hour12: false,
  });

export interface ChoiceOption {
  value: string;
  label: string;
  description?: string;
  disabled?: boolean;
}
/** App selection sheet: choices only change through an explicit user click. */
export function ChoiceSelect({
  label,
  value,
  onChange,
  options,
  placeholder = "请选择",
  compact = false,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: ChoiceOption[];
  placeholder?: string;
  compact?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const selected = options.find((option) => option.value === value);
  return (
    <>
      <button
        type="button"
        className={`choice-trigger ${compact ? "compact" : ""} ${!selected ? "placeholder" : ""}`}
        aria-label={label}
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={(event) => {
          event.preventDefault();
          setOpen(true);
        }}
      >
        <span>
          <strong>{selected?.label ?? placeholder}</strong>
          {!compact && selected?.description && (
            <small>{selected.description}</small>
          )}
        </span>
        <Icon name="down" size={16} />
      </button>
      {open && (
        <Modal title={label} onClose={() => setOpen(false)}>
          <div className="choice-list" role="listbox" aria-label={label}>
            {options.length ? (
              options.map((option) => (
                <button
                  type="button"
                  role="option"
                  aria-selected={option.value === value}
                  disabled={option.disabled}
                  key={option.value}
                  onClick={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    onChange(option.value);
                    setOpen(false);
                  }}
                >
                  <span>
                    <strong>{option.label}</strong>
                    {option.description && <small>{option.description}</small>}
                  </span>
                  <span className="choice-check">
                    {option.value === value && <Icon name="check" size={15} />}
                  </span>
                </button>
              ))
            ) : (
              <Empty
                title="暂无可选项目"
                detail="请先补充对应的账户或业务信息。"
              />
            )}
          </div>
          <p className="choice-footer">选择后应用到当前页面</p>
        </Modal>
      )}
    </>
  );
}
export function MonthPicker({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [year, setYear] = useState(Number(value.slice(0, 4)));
  return (
    <>
      <button
        type="button"
        className="choice-trigger compact month-trigger"
        aria-label="账单月份"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => {
          setYear(Number(value.slice(0, 4)));
          setOpen(true);
        }}
      >
        <strong>
          {value.slice(0, 4)}年{Number(value.slice(5))}月
        </strong>
        <Icon name="down" size={15} />
      </button>
      {open && (
        <Modal title="选择账单月份" onClose={() => setOpen(false)}>
          <div className="month-picker">
            <div className="month-year">
              <button
                className="icon-button"
                aria-label="上一年"
                onClick={() => setYear((y) => y - 1)}
              >
                <Icon name="back" />
              </button>
              <strong>{year} 年</strong>
              <button
                className="icon-button"
                aria-label="下一年"
                onClick={() => setYear((y) => y + 1)}
              >
                <Icon name="chevron" />
              </button>
            </div>
            <div className="month-grid" role="listbox" aria-label="月份">
              {Array.from({ length: 12 }, (_, i) => {
                const month = `${year}-${String(i + 1).padStart(2, "0")}`;
                return (
                  <button
                    type="button"
                    role="option"
                    aria-selected={value === month}
                    key={month}
                    onClick={() => {
                      onChange(month);
                      setOpen(false);
                    }}
                  >
                    {i + 1}月
                    {value === month && <Icon name="check" size={13} />}
                  </button>
                );
              })}
            </div>
          </div>
        </Modal>
      )}
    </>
  );
}
