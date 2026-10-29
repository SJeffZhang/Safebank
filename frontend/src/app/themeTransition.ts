/** Reveal a newly rendered theme from the upper-right theme switch. */
export async function revealTheme(commit: () => void) {
  if (!document.startViewTransition || window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) {
    commit(); return;
  }
  const root = document.documentElement;
  const button = document.querySelector('[data-theme-toggle]');
  const bounds = button?.getBoundingClientRect();
  const x = bounds ? bounds.left + bounds.width / 2 : window.innerWidth - 32;
  const y = bounds ? bounds.top + bounds.height / 2 : 32;
  const radius = Math.hypot(Math.max(x, window.innerWidth - x), Math.max(y, window.innerHeight - y)) + 24;
  root.style.setProperty('--sb-theme-origin-x', `${x}px`);
  root.style.setProperty('--sb-theme-origin-y', `${y}px`);
  root.classList.add('theme-reveal-active');
  let committed = false;
  const update = () => { if (!committed) { committed = true; commit(); } };
  let transition: ReturnType<Document['startViewTransition']> | undefined;
  try {
    transition = document.startViewTransition(update);
    // Consume early rejection as well as failure during capture/animation.
    const finished = transition.finished.catch(() => {});
    await transition.ready;
    const animation = root.animate(
      { '--sb-theme-radius': ['0px', `${radius}px`] },
      { duration: 800, easing: 'cubic-bezier(.22,.61,.36,1)', fill: 'both', pseudoElement: '::view-transition-new(root)' },
    );
    await Promise.all([animation.finished, finished]);
  } catch {
    transition?.skipTransition();
    update();
  } finally {
    root.classList.remove('theme-reveal-active');
    root.style.removeProperty('--sb-theme-origin-x');
    root.style.removeProperty('--sb-theme-origin-y');
  }
}
