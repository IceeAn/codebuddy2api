import { beforeEach, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';

beforeEach(() => {
  const pinia = createPinia();
  pinia.state.value.session = {
    authenticated: true,
    username: 'test-user',
    source: 'session_cookie',
    ready: true,
  };
  setActivePinia(pinia);
});

class ResizeObserverMock {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

Object.defineProperty(globalThis, 'ResizeObserver', {
  configurable: true,
  value: ResizeObserverMock,
});

Object.defineProperty(window, 'matchMedia', {
  configurable: true,
  value: vi.fn<typeof window.matchMedia>().mockImplementation((query: string): MediaQueryList => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: vi.fn<MediaQueryList['addEventListener']>(),
    removeEventListener: vi.fn<MediaQueryList['removeEventListener']>(),
    addListener: vi.fn<MediaQueryList['addListener']>(),
    removeListener: vi.fn<MediaQueryList['removeListener']>(),
    dispatchEvent: vi.fn<MediaQueryList['dispatchEvent']>(),
  })),
});
