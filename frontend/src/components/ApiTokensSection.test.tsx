import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { I18nContext, createT } from '../i18n';
import ApiTokensSection, { type ApiTokenItem } from './ApiTokensSection';

const apiMock = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), delete: vi.fn(), put: vi.fn() }));
vi.mock('../lib/api', () => ({ api: apiMock }));

const TOKEN: ApiTokenItem = {
  id: 7,
  name: 'obsidian-sync',
  key_prefix: 'vsk_1234abcd...',
  is_active: true,
  last_used_at: null,
  created_at: '2026-11-01T04:00:00',
};

function renderSection() {
  const t = createT('en');
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <I18nContext.Provider value={{ locale: 'en', t, setLocale: () => {} }}>
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <ApiTokensSection />
        </MemoryRouter>
      </QueryClientProvider>
    </I18nContext.Provider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  Object.assign(navigator, { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } });
});

describe('ApiTokensSection', () => {
  it('lists tokens with created / never-used info and only the prefix', async () => {
    apiMock.get.mockResolvedValue([TOKEN]);
    renderSection();
    expect(await screen.findByText('obsidian-sync')).toBeInTheDocument();
    expect(screen.getByText('vsk_1234abcd...')).toBeInTheDocument();
    expect(screen.getByText(/never/)).toBeInTheDocument();
  });

  it('reveals the full key once with a warning and a ready curl', async () => {
    apiMock.get.mockResolvedValue([]);
    apiMock.post.mockResolvedValue({ ...TOKEN, full_key: 'vsk_FULLSECRET' });
    renderSection();
    fireEvent.change(await screen.findByLabelText(/Token name/), {
      target: { value: 'obsidian-sync' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Issue token/ }));

    expect(await screen.findByText('vsk_FULLSECRET')).toBeInTheDocument();
    expect(screen.getByText(/Shown only once/)).toBeInTheDocument();
    expect(screen.getByText(/X-API-Key: vsk_FULLSECRET.*\/api\/videos\/ids/)).toBeInTheDocument();
    expect(apiMock.post).toHaveBeenCalledWith('/settings/tokens', { name: 'obsidian-sync' });

    fireEvent.click(screen.getByRole('button', { name: /Copy token/ }));
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith('vsk_FULLSECRET');

    fireEvent.click(screen.getByRole('button', { name: /saved it/ }));
    expect(screen.queryByText('vsk_FULLSECRET')).not.toBeInTheDocument();
  });

  it('revoking needs an explicit confirmation', async () => {
    apiMock.get.mockResolvedValue([TOKEN]);
    apiMock.delete.mockResolvedValue(undefined);
    renderSection();
    fireEvent.click(await screen.findByRole('button', { name: /Revoke/ }));
    expect(apiMock.delete).not.toHaveBeenCalled();
    expect(screen.getByText(/Scripts using it stop working/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(apiMock.delete).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: /Revoke/ }));
    const buttons = screen.getAllByRole('button', { name: 'Revoke' });
    fireEvent.click(buttons[buttons.length - 1]);
    await waitFor(() => expect(apiMock.delete).toHaveBeenCalledWith('/settings/tokens/7'));
  });
});
