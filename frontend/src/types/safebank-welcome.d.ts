import type { DetailedHTMLProps, HTMLAttributes } from 'react';

/**
 * 交付包里的欢迎页以自定义元素 safebank-welcome 提供，
 * 这里为 React 的 JSX 补充元素与属性类型。
 */
declare global {
  namespace JSX {
    interface IntrinsicElements {
      'safebank-welcome': DetailedHTMLProps<HTMLAttributes<HTMLElement>, HTMLElement> & {
        'login-url'?: string;
        'register-url'?: string;
        mode?: 'anonymous' | 'authenticated';
      };
    }
  }
}

export {};