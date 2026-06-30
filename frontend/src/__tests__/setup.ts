import { vi } from 'vitest';

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
  value: vi.fn<typeof window.matchMedia>().mockImplementation(
    (query: string): MediaQueryList => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: vi.fn<MediaQueryList['addEventListener']>(),
      removeEventListener: vi.fn<MediaQueryList['removeEventListener']>(),
      addListener: vi.fn<MediaQueryList['addListener']>(),
      removeListener: vi.fn<MediaQueryList['removeListener']>(),
      dispatchEvent: vi.fn<MediaQueryList['dispatchEvent']>(),
    }),
  ),
});
