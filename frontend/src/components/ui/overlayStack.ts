export interface OverlayRegistration {
  elements: HTMLElement[];
  focusRoot: HTMLElement;
  modal: boolean;
  onEscape?: () => void;
}

interface OverlayEntry extends OverlayRegistration {
  restoreFocus: HTMLElement | null;
}

interface ElementState {
  inert: boolean;
  ariaHidden: string | null;
}

const entries: OverlayEntry[] = [];
const isolatedElements = new Map<HTMLElement, ElementState>();
let originalBodyOverflow: string | null = null;

const focusableSelector = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

function restoreIsolation(): void {
  isolatedElements.forEach((state, element) => {
    element.inert = state.inert;
    if (state.ariaHidden === null) element.removeAttribute('aria-hidden');
    else element.setAttribute('aria-hidden', state.ariaHidden);
  });
  isolatedElements.clear();
}

function lastModalIndex(): number {
  for (let index = entries.length - 1; index >= 0; index -= 1) {
    if (entries[index].modal) return index;
  }
  return -1;
}

function syncPageState(): void {
  restoreIsolation();
  const modalIndex = lastModalIndex();
  if (modalIndex < 0) {
    if (originalBodyOverflow !== null) {
      document.body.style.overflow = originalBodyOverflow;
      originalBodyOverflow = null;
    }
    return;
  }

  if (originalBodyOverflow === null) originalBodyOverflow = document.body.style.overflow;
  document.body.style.overflow = 'hidden';
  const allowedElements = entries.slice(modalIndex).flatMap((entry) => entry.elements);
  Array.from(document.body.children).forEach((child) => {
    if (!(child instanceof HTMLElement)) return;
    const containsAllowedElement = allowedElements.some(
      (allowed) => child === allowed || child.contains(allowed),
    );
    if (containsAllowedElement) return;
    isolatedElements.set(child, {
      inert: Boolean(child.inert),
      ariaHidden: child.getAttribute('aria-hidden'),
    });
    child.inert = true;
    child.setAttribute('aria-hidden', 'true');
  });
}

function focusableElements(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(focusableSelector)).filter(
    (element) => element.getAttribute('aria-hidden') !== 'true',
  );
}

function focusInitial(entry: OverlayEntry): void {
  const explicit = entry.focusRoot.querySelector<HTMLElement>('[data-overlay-initial-focus]');
  const target = explicit ?? focusableElements(entry.focusRoot)[0] ?? entry.focusRoot;
  target.focus();
}

function handleKeydown(event: KeyboardEvent): void {
  const entry = entries.at(-1)!;
  if (event.key === 'Escape') {
    event.preventDefault();
    event.stopImmediatePropagation();
    entry.onEscape?.();
    return;
  }
  if (event.key !== 'Tab') return;

  const focusables = focusableElements(entry.focusRoot);
  event.preventDefault();
  event.stopImmediatePropagation();
  if (focusables.length === 0) {
    entry.focusRoot.focus();
    return;
  }
  const current = document.activeElement;
  const currentIndex = focusables.indexOf(current as HTMLElement);
  const targetIndex = event.shiftKey
    ? currentIndex <= 0
      ? focusables.length - 1
      : currentIndex - 1
    : currentIndex < 0 || currentIndex === focusables.length - 1
      ? 0
      : currentIndex + 1;
  focusables[targetIndex].focus();
}

export function registerOverlay(registration: OverlayRegistration): () => void {
  const activeElement = document.activeElement;
  const entry: OverlayEntry = {
    ...registration,
    restoreFocus: activeElement instanceof HTMLElement ? activeElement : null,
  };
  if (entries.length === 0) document.addEventListener('keydown', handleKeydown, true);
  entries.push(entry);
  syncPageState();
  focusInitial(entry);

  let registered = true;
  return () => {
    if (!registered) return;
    registered = false;
    const index = entries.indexOf(entry);
    const wasTop = index === entries.length - 1;
    entries.splice(index, 1);
    if (entries.length === 0) document.removeEventListener('keydown', handleKeydown, true);
    syncPageState();
    if (wasTop && entry.restoreFocus?.isConnected) {
      queueMicrotask(() => entry.restoreFocus?.focus());
    }
  };
}
