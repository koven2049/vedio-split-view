import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { I18nContext, createT } from './i18n';
import { useAuthStore } from './stores/authStore';
import App from './App';

const apiMock = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), delete: vi.fn(), put: vi.fn() }));
vi.mock('./lib/api', () => ({ api: apiMock, ApiError: class extends Error {} }));

const DOCS = {
  auth_header: 'X-API-Key: <your-api-key>',
  base_url: '/api',
  groups: [
    {
      group: 'Videos',
      endpoints: [
        { method: 'GET', path: '/api/videos/ids', description: 'ids', params: [], response: '' },
      ],
    },
  ],
};

function renderAt(path: string) {
  const t = createT('en');
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <I18nContext.Provider value={{ locale: 'en', t, setLocale: () => {} }}>
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[path]}>
          <App />
        </MemoryRouter>
      </QueryClientProvider>
    </I18nContext.Provider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  apiMock.get.mockImplementation((p: string) => Promise.resolve(p === '/docs-data' ? DOCS : []));
});

describe('public API docs', () => {
  it('renders without login, with a sign-in link instead of the app nav', async () => {
    useAuthStore.getState().logout();
    renderAt('/api-docs');
    expect(await screen.findByText('/api/videos/ids')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Login/ })).toHaveAttribute('href', '/login');
    expect(apiMock.get).toHaveBeenCalledWith('/docs-data');
  });

  it('other pages still require login', async () => {
    useAuthStore.getState().logout();
    renderAt('/library');
    expect(await screen.findByRole('button', { name: 'Login' })).toBeInTheDocument();
    expect(screen.queryByText('/api/videos/ids')).not.toBeInTheDocument();
    expect(apiMock.get).not.toHaveBeenCalledWith('/docs-data');
  });

  it('logged-in viewers see the docs inside the app layout', async () => {
    useAuthStore.getState().setAuth('jwt', 'viewer1', 'viewer');
    renderAt('/api-docs');
    expect(await screen.findByText('/api/videos/ids')).toBeInTheDocument();
    expect(screen.getByText('viewer1 (viewer)')).toBeInTheDocument();
    useAuthStore.getState().logout();
  });
});
