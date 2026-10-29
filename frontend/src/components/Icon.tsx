import type { ReactNode, SVGProps } from 'react';

export type IconName =
  | 'home'
  | 'assistant'
  | 'agent-mark'
  | 'person'
  | 'chevron-left'
  | 'chevron-right'
  | 'check'
  | 'close'
  | 'alert'
  | 'clock'
  | 'search'
  | 'eye'
  | 'eye-off'
  | 'lock'
  | 'wifi-off'
  | 'refresh'
  | 'plus'
  | 'card'
  | 'bill'
  | 'send'
  | 'chart'
  | 'info'
  | 'shield'
  | 'arrow-right'
  | 'sun'
  | 'moon';

const paths: Record<IconName, ReactNode> = {
  home: <path d="M4 10.5 12 4l8 6.5V20a1 1 0 0 1-1 1h-4v-6H9v6H5a1 1 0 0 1-1-1z" />,
  assistant: (
    <>
      <path d="M12 3.5l1.8 4.7 4.7 1.8-4.7 1.8L12 16.5l-1.8-4.7L5.5 10l4.7-1.8z" />
      <circle cx="18" cy="18" r="2.2" />
    </>
  ),
  /** 目标简报 i-an：四角星 + 右上加号 + 左下小圆点 */
  'agent-mark': (
    <>
      <path d="M12 4.125C12.75 8.625 14.25 10.5 18.75 12C14.25 13.5 12.75 15.375 12 19.875C11.25 15.375 9.75 13.5 5.25 12C9.75 10.5 11.25 8.625 12 4.125Z" />
      <path d="M17.625 4.125v3M15.375 5.625h4.5" />
      <circle cx="6" cy="17.625" r="1.275" />
    </>
  ),
  person: (
    <>
      <circle cx="12" cy="8" r="3.4" />
      <path d="M5 20c0-3.4 3.1-5.6 7-5.6s7 2.2 7 5.6" />
    </>
  ),
  'chevron-left': <path d="M15 5l-7 7 7 7" />,
  'chevron-right': <path d="M9 5l7 7-7 7" />,
  check: <path d="M4.5 12.5l4.8 4.8L19.5 7" />,
  close: <path d="M6 6l12 12M18 6L6 18" />,
  alert: (
    <>
      <path d="M12 4.5l8.5 15h-17z" />
      <path d="M12 10v4.5M12 17.2v.3" />
    </>
  ),
  clock: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 7.5V12l3 2" />
    </>
  ),
  search: (
    <>
      <circle cx="11" cy="11" r="6.5" />
      <path d="M16 16l4 4" />
    </>
  ),
  eye: (
    <>
      <path d="M3 12s3.4-6 9-6 9 6 9 6-3.4 6-9 6-9-6-9-6z" />
      <circle cx="12" cy="12" r="2.6" />
    </>
  ),
  'eye-off': (
    <>
      <path d="M4 4l16 16" />
      <path d="M9.5 6.4A9.9 9.9 0 0 1 12 6c5.6 0 9 6 9 6a16 16 0 0 1-3 3.6" />
      <path d="M6.1 8.4C4.2 9.8 3 12 3 12s3.4 6 9 6c1 0 2-.2 2.9-.5" />
    </>
  ),
  lock: (
    <>
      <rect x="4.5" y="10" width="15" height="10" rx="2.5" />
      <path d="M8 10V7.5a4 4 0 0 1 8 0V10" />
    </>
  ),
  'wifi-off': (
    <>
      <path d="M3 3l18 18" />
      <path d="M5 10a13 13 0 0 1 5-2.7" />
      <path d="M2.5 6.5A17 17 0 0 1 7 4.2" />
      <path d="M12 19.5v.2" />
    </>
  ),
  refresh: (
    <>
      <path d="M20 12a8 8 0 1 1-2.6-5.9" />
      <path d="M20 4v5h-5" />
    </>
  ),
  plus: <path d="M12 5v14M5 12h14" />,
  card: (
    <>
      <rect x="3" y="6" width="18" height="12" rx="2.5" />
      <path d="M3 10h18" />
    </>
  ),
  bill: (
    <>
      <path d="M5 3.5h14v17l-2.3-1.6-2.35 1.6L12 18.9l-2.35 1.6L7.3 18.9 5 20.5z" />
      <path d="M8.5 8h7M8.5 12h7M8.5 16h4" />
    </>
  ),
  send: <path d="M7 17 17 7M9 7h8v8" />,
  chart: (
    <>
      <path d="M4 19V5" />
      <path d="M4 19h16" />
      <path d="M8 15l3.5-4 3 2.5L20 8" />
    </>
  ),
  info: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 11v5M12 8v.2" />
    </>
  ),
  shield: (
    <>
      <path d="M12 3.5l7 3v5.2c0 4.2-2.8 7.1-7 8.8-4.2-1.7-7-4.6-7-8.8V6.5z" />
      <path d="M9 12l2.2 2.2L15.5 10" />
    </>
  ),
  'arrow-right': <path d="M4 12h15M13 6l6 6-6 6" />,
  sun: (
    <>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6l1.4 1.4M17 17l1.4 1.4M18.4 5.6L17 7M7 17l-1.4 1.4" />
    </>
  ),
  moon: <path d="M20 14.5A8.5 8.5 0 0 1 9.5 4 8.5 8.5 0 1 0 20 14.5z" />,
};

export interface IconProps extends Omit<SVGProps<SVGSVGElement>, 'name'> {
  name: IconName;
  size?: number;
  /** When provided the icon becomes meaningful and is announced. */
  title?: string;
}

export function Icon({ name, size = 20, title, ...rest }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.7}
      strokeLinecap="round"
      strokeLinejoin="round"
      role={title ? 'img' : undefined}
      aria-label={title}
      aria-hidden={title ? undefined : true}
      focusable="false"
      {...rest}
    >
      {title ? <title>{title}</title> : null}
      {paths[name]}
    </svg>
  );
}
