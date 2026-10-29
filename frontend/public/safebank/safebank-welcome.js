/* SafeBank welcome component · 玻璃银行卡 × C2 石墨黑 / 香槟金
   保留 674×1270 舞台等比缩放、safebank-navigate 事件、玻璃材质与按压反馈。 */
(function () {
  'use strict';
  if (customElements.get('safebank-welcome')) return;

  const CSS = `
    :host {
      display: grid;
      place-items: center;
      width: 100%;
      height: 100%;
      min-width: 0;
      min-height: 0;
      overflow: hidden;
      background:
        radial-gradient(680px 480px at 16% 4%, rgba(231, 215, 164, 0.2), transparent 62%),
        radial-gradient(620px 460px at 92% 22%, rgba(231, 215, 164, 0.12), transparent 64%),
        var(--sb-canvas, #f1f2ef);
      color: var(--sb-ink, #111318);
      font-family: var(--sb-font-sans, -apple-system, BlinkMacSystemFont, "SF Pro Display", "PingFang SC", "Helvetica Neue", sans-serif);
      -webkit-font-smoothing: antialiased;
    }

    * { box-sizing: border-box; }

    .viewport { position: relative; width: 674px; height: 1270px; flex: none; }
    .stage {
      position: relative; width: 674px; height: 1270px; overflow: hidden;
      transform-origin: top left;
      padding: 38px 48px 42px;
      display: flex; flex-direction: column;
      background: transparent;
    }

    /* ---------- 系统状态行 ---------- */
    .status {
      display: flex; align-items: center; justify-content: space-between;
      height: 34px; font-size: 15px; font-weight: 650; letter-spacing: 0.01em;
      color: var(--sb-ink, #111318); position: relative; z-index: 3;
    }
    .status-icons { display: flex; align-items: center; gap: 7px; }
    .sig { width: 18px; height: 12px; opacity: 0.85;
      background:
        linear-gradient(currentColor, currentColor) left bottom / 4px 4px no-repeat,
        linear-gradient(currentColor, currentColor) center bottom / 4px 7px no-repeat,
        linear-gradient(currentColor, currentColor) right bottom / 4px 11px no-repeat; }
    .wifi { width: 13px; height: 11px; border: 2px solid currentColor; border-radius: 50%;
      border-bottom-color: transparent; border-left-color: transparent; border-right-color: transparent;
      position: relative; top: 3px; opacity: 0.8; }
    .batt { width: 23px; height: 10px; border: 1.5px solid currentColor; border-radius: 3px; position: relative; opacity: 0.85; }
    .batt::after { content: ""; position: absolute; inset: 2px; width: 13px; background: currentColor; border-radius: 1px; }

    /* ---------- 品牌 ---------- */
    .brand { display: flex; align-items: center; gap: 13px; margin-top: 26px; position: relative; z-index: 3; }
    .mark { position: relative; width: 46px; height: 46px; border-radius: 15px;
      background: var(--sb-ink, #111318); flex: none;
      box-shadow: 0 10px 20px -12px rgba(17, 19, 24, 0.7); }
    .mark::after { content: ""; position: absolute; inset: 11px; border: 3px solid var(--sb-gold, #e7d7a4); border-radius: 50%; }
    .brand-name { font-size: 27px; font-weight: 650; letter-spacing: -0.02em; line-height: 1; }
    .brand-sub { display: block; margin-top: 5px; font-size: 11px; letter-spacing: 0.22em;
      text-transform: uppercase; color: var(--sb-muted, #6e6c66); }

    /* ---------- 三张玻璃银行卡 ---------- */
    .art { position: relative; flex: 0 0 700px; margin-top: 12px; }
    .glow {
      position: absolute; left: -14%; top: 6%; width: 128%; height: 78%;
      background:
        radial-gradient(closest-side at 30% 60%, rgba(231, 215, 164, 0.72), transparent 70%),
        radial-gradient(closest-side at 76% 40%, rgba(185, 162, 104, 0.42), transparent 72%);
      filter: blur(38px); pointer-events: none;
    }
    .card {
      position: absolute; overflow: hidden;
      border-radius: 26px;
      border: 1px solid rgba(231, 215, 164, 0.72);
      background:
        linear-gradient(152deg, rgba(250, 250, 247, 0.46) 0%, rgba(250, 250, 247, 0.26) 58%, rgba(231, 215, 164, 0.22) 100%);
      background:
        linear-gradient(152deg,
          color-mix(in srgb, var(--sb-surface, #fafaf7) 46%, transparent) 0%,
          color-mix(in srgb, var(--sb-surface, #fafaf7) 26%, transparent) 58%,
          color-mix(in srgb, var(--sb-gold, #e7d7a4) 22%, transparent) 100%);
      -webkit-backdrop-filter: blur(20px) saturate(1.1);
      backdrop-filter: blur(20px) saturate(1.1);
      box-shadow:
        0 26px 48px -28px rgba(17, 19, 24, 0.38),
        inset 0 1px 0 rgba(255, 255, 255, 0.6),
        inset 0 0 0 1px rgba(255, 255, 255, 0.18);
      padding: 22px 24px;
      display: flex; flex-direction: column; justify-content: space-between;
      transition: transform 240ms cubic-bezier(0.2, 0.7, 0.2, 1);
    }
    .card::after {
      content: ""; position: absolute; left: -30%; top: -60%; width: 90%; height: 160%;
      background: radial-gradient(closest-side, rgba(255, 255, 255, 0.5), transparent 72%);
      transform: rotate(18deg); pointer-events: none;
    }
    .card-a { width: 380px; height: 240px; left: 0; top: calc(50% + 54px); transform: translateY(-50%) rotate(-15deg); z-index: 3; }
    .card-b { width: 300px; height: 192px; left: 244px; top: calc(50% - 128px); transform: translateY(-50%) rotate(-6deg); z-index: 2; }
    .card-c { width: 300px; height: 192px; left: 318px; top: calc(50% - 64px); transform: translateY(-50%) rotate(12deg); z-index: 1; }

    .card-top { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
    .card-bank { font-size: 12px; letter-spacing: 0.16em; text-transform: uppercase; color: var(--sb-muted, #6e6c66); }
    .chip { width: 42px; height: 30px; border-radius: 7px; flex: none;
      border: 1px solid rgba(122, 101, 50, 0.5);
      background:
        linear-gradient(90deg, transparent 46%, rgba(122, 101, 50, 0.42) 46% 54%, transparent 54%),
        linear-gradient(180deg, transparent 46%, rgba(122, 101, 50, 0.42) 46% 54%, transparent 54%),
        linear-gradient(135deg, rgba(231, 215, 164, 0.85), rgba(185, 162, 104, 0.55)); }
    .card-mid { display: flex; flex-direction: column; gap: 10px; }
    .card-no { font-family: var(--sb-font-mono, ui-monospace, Menlo, monospace);
      font-size: 17px; letter-spacing: 0.12em; color: var(--sb-ink, #111318); }
    .card-tag { font-size: 11px; letter-spacing: 0.2em; text-transform: uppercase; color: var(--sb-gold-ink, #7a6532); font-weight: 650; }

    /* 两张中性玻璃小牌（替代原营销数字） */
    .pill { position: absolute; z-index: 4; padding: 12px 18px; border-radius: 999px;
      font-size: 12px; letter-spacing: 0.16em; text-transform: uppercase; font-weight: 650;
      color: var(--sb-gold-ink, #7a6532);
      border: 1px solid rgba(231, 215, 164, 0.62);
      background: rgba(250, 250, 247, 0.6);
      background: color-mix(in srgb, var(--sb-surface, #fafaf7) 58%, transparent);
      -webkit-backdrop-filter: blur(12px);
      backdrop-filter: blur(12px);
      box-shadow: 0 16px 30px -24px rgba(17, 19, 24, 0.4); }
    .pill-connected { left: 322px; top: calc(50% + 150px); }

    /* ---------- 文案 ---------- */
    .copy { position: relative; z-index: 3; margin-top: 24px; }
    .eyebrow { display: flex; align-items: center; gap: 12px;
      font-size: 12px; font-weight: 650; letter-spacing: 0.2em; text-transform: uppercase;
      color: var(--sb-gold-ink, #7a6532); }
    .eyebrow::before { content: ""; width: 34px; height: 1px; background: var(--sb-gold-deep, #b9a268); opacity: 0.7; }
    .title { margin: 20px 0 0; font-size: 55px; line-height: 1.08; font-weight: 650;
      letter-spacing: -0.035em; color: var(--sb-ink, #111318); }
    .desc { margin: 18px 0 0; max-width: 520px; font-size: 17px; line-height: 1.6;
      color: var(--sb-muted, #6e6c66); }

    /* ---------- 底部双入口 ---------- */
    .actions { position: relative; z-index: 3; display: flex; align-items: center; gap: 14px; margin-top: auto; padding-top: 34px; }
    .btn { position: relative; overflow: hidden; border: 0; cursor: pointer;
      font-family: inherit; font-weight: 650; display: inline-flex; align-items: center; justify-content: center;
      transform: scale(1); transition: transform 140ms ease, background 140ms ease, box-shadow 140ms ease; }
    .btn:focus-visible { outline: 2px solid var(--sb-gold-deep, #b9a268); outline-offset: 3px; }
    .btn-primary { flex: 1.15; height: 52px; border-radius: 17px;
      background: var(--sb-ink, #111318); color: var(--sb-surface, #fafaf7); font-size: 18px;
      box-shadow: 0 16px 26px -18px rgba(17, 19, 24, 0.75); }
    .btn-primary::after { content: ""; position: absolute; inset: 0;
      background: linear-gradient(180deg, rgba(255, 255, 255, 0.14), transparent 46%); pointer-events: none; }
    .btn-secondary { flex: 1; height: 48px; border-radius: 16px; font-size: 16px;
      color: var(--sb-ink, #111318);
      background: rgba(250, 250, 247, 0.54);
      background: color-mix(in srgb, var(--sb-surface, #fafaf7) 54%, transparent);
      border: 1px solid rgba(231, 215, 164, 0.62);
      -webkit-backdrop-filter: blur(12px);
      backdrop-filter: blur(12px); }
    .btn[data-pressed="true"] { transform: scale(0.97); }
    :host([mode="authenticated"]) .btn-secondary { display: none; }
    :host([mode="authenticated"]) .btn-primary { flex: 1; }

    /* 指针高光：幅度极小 */
    .glint { position: absolute; inset: -1px; pointer-events: none; opacity: var(--glint-opacity, 0);
      background: radial-gradient(120px 80px at var(--glint-x, 40%) var(--glint-y, 0%),
        rgba(255, 255, 255, 0.5), transparent 70%);
      transition: opacity 180ms ease; }
    .btn-primary .glint { opacity: calc(var(--glint-opacity, 0) * 0.55); }

    @media (prefers-reduced-motion: reduce) {
      .btn, .card { transition: none; }
      .btn[data-pressed="true"] { transform: none; }
      .glint { display: none; }
    }
  `;

  const HTML = `
    <div class="viewport" id="viewport">
      <div class="stage" id="stage">
        <div class="status" aria-hidden="true">
          <span>9:41</span>
          <span class="status-icons"><i class="sig"></i><i class="wifi"></i><i class="batt"></i></span>
        </div>

        <div class="brand">
          <span class="mark" aria-hidden="true"></span>
          <span>
            <span class="brand-name">SafeBank</span>
            <span class="brand-sub">Banking</span>
          </span>
        </div>

        <div class="art" aria-hidden="true">
          <span class="glow"></span>
          <div class="card card-c">
            <div class="card-top"><span class="card-bank">SafeBank</span><span class="chip"></span></div>
            <div class="card-mid"><span class="card-no">•••• 4932</span><span class="card-tag">Connected</span></div>
          </div>
          <div class="card card-b">
            <div class="card-top"><span class="card-bank">SafeBank</span><span class="chip"></span></div>
            <div class="card-mid"><span class="card-no">•••• 7153</span><span class="card-tag">Everyday</span></div>
          </div>
          <div class="card card-a">
            <div class="card-top"><span class="card-bank">SafeBank</span><span class="chip"></span></div>
            <div class="card-mid"><span class="card-no">•••• 2680</span><span class="card-tag">Secure</span></div>
          </div>
          <span class="pill pill-connected">Connected</span>
        </div>

        <div class="copy">
          <p class="eyebrow">Welcome to Safe Bank</p>
          <h1 class="title">Take control of<br>your money</h1>
          <p class="desc">账户、转账与理财，在同一个安静、清晰的空间里完成。每一步都可回退，每一次确认都有记录。</p>
        </div>

        <div class="actions">
          <button type="button" class="btn btn-primary" data-target="login">
            <span class="glint"></span><span class="btn-label">开始使用</span>
          </button>
          <button type="button" class="btn btn-secondary" data-target="login">
            <span class="glint"></span><span class="btn-label">已有账户？登录</span>
          </button>
        </div>
      </div>
    </div>
  `;

  class SafeBankWelcome extends HTMLElement {
    static get observedAttributes() {
      return ['mode'];
    }

    constructor() {
      super();
      this.attachShadow({ mode: 'open' });
      this._controller = null;
      this._observer = null;
      this._resizeFrame = 0;
      this._buttons = [];
    }

    connectedCallback() {
      const root = this.shadowRoot;
      root.innerHTML = '';
      const style = document.createElement('style');
      style.textContent = CSS;
      root.append(style);

      const holder = document.createElement('div');
      holder.innerHTML = HTML;
      root.append(holder.firstElementChild);

      this._viewport = root.getElementById('viewport');
      this._stage = root.getElementById('stage');
      this._buttons = Array.from(root.querySelectorAll('.btn'));

      this._motion = window.matchMedia('(prefers-reduced-motion: reduce)');
      this._controller = new AbortController();
      const { signal } = this._controller;

      this._buttons.forEach((button) => {
        this._bindButton(button, signal);
      });

      this._syncLinks();
      this._syncMode();

      this._observer = new ResizeObserver(() => {
        cancelAnimationFrame(this._resizeFrame);
        this._resizeFrame = requestAnimationFrame(() => this._fit());
      });
      this._observer.observe(this);
      this._fit();
    }

    _bindButton(button, signal) {
      const glint = button.querySelector('.glint');
      const settle = () => {
        button.dataset.pressed = 'false';
        button.style.setProperty('--glint-opacity', '0');
        button.style.setProperty('--glint-x', '40%');
        button.style.setProperty('--glint-y', '0%');
      };

      button.addEventListener('pointermove', (event) => {
        if (this._motion.matches || event.pointerType === 'touch') return;
        const rect = button.getBoundingClientRect();
        if (!rect.width || !rect.height) return;
        // 高光幅度限制在 2px 以内，避免炫技
        const dx = Math.max(-2, Math.min(2, event.clientX - (rect.left + rect.width / 2)));
        const dy = Math.max(-2, Math.min(2, event.clientY - (rect.top + rect.height / 2)));
        if (glint) {
          glint.style.transform = `translate(${dx}px, ${dy}px)`;
        }
        button.style.setProperty('--glint-x', `${((event.clientX - rect.left) / rect.width) * 100}%`);
        button.style.setProperty('--glint-y', `${((event.clientY - rect.top) / rect.height) * 100}%`);
        button.style.setProperty('--glint-opacity', '0.5');
      }, { signal });

      button.addEventListener('pointerleave', settle, { signal });
      button.addEventListener('blur', settle, { signal });
      button.addEventListener('pointercancel', settle, { signal });

      button.addEventListener('pointerdown', () => {
        if (!this._motion.matches) button.dataset.pressed = 'true';
      }, { signal });
      button.addEventListener('pointerup', () => {
        button.dataset.pressed = 'false';
      }, { signal });

      button.addEventListener('click', (event) => {
        if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        const target = 'login';
        const href = this._resolve(target);
        const navigation = new CustomEvent('safebank-navigate', {
          detail: { target, href },
          bubbles: true,
          composed: true,
          cancelable: true,
        });
        if (!this.dispatchEvent(navigation)) return;
        window.location.assign(href);
      }, { signal });
    }

    _resolve(target) {
      const candidate = this.getAttribute(target + '-url') || '/' + target;
      try {
        const url = new URL(candidate, document.baseURI);
        return /^(https?:|file:)$/.test(url.protocol) ? url.href : new URL('/' + target, document.baseURI).href;
      } catch (_) {
        return new URL('/' + target, document.baseURI).href;
      }
    }

    _syncLinks() {
      this._buttons.forEach((button) => {
        button.dataset.href = this._resolve('login');
      });
    }

    _syncMode() {
      const authenticated = this.getAttribute('mode') === 'authenticated';
      const label = this.shadowRoot && this.shadowRoot.querySelector('.btn-primary .btn-label');
      if (label) label.textContent = authenticated ? '继续使用' : '开始使用';
    }

    _fit() {
      const width = this.clientWidth;
      const height = this.clientHeight;
      if (!width || !height) return;
      const scale = Math.min(width / 674, height / 1270, 1);
      this._viewport.style.width = `${674 * scale}px`;
      this._viewport.style.height = `${1270 * scale}px`;
      this._stage.style.transform = `scale(${scale})`;
    }

    attributeChangedCallback() {
      if (this._buttons.length) this._syncLinks();
      this._syncMode();
    }

    disconnectedCallback() {
      if (this._controller) this._controller.abort();
      this._controller = null;
      if (this._observer) this._observer.disconnect();
      this._observer = null;
      cancelAnimationFrame(this._resizeFrame);
    }
  }

  customElements.define('safebank-welcome', SafeBankWelcome);
})();
