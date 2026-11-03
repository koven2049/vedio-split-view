import { afterEach, describe, expect, it, vi } from 'vitest';

import { api } from './api';
import { useAuthStore } from '../stores/authStore';

describe('api.delete', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    useAuthStore.getState().logout();
  });

  it('treats 204 as success', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        status: 204,
        ok: true,
        json: async () => {
          throw new Error('empty body');
        },
        text: async () => '',
      }),
    );
    useAuthStore.setState({ token: 't' });
    await expect(api.delete('/videos/9')).resolves.toBeUndefined();
  });

  it('treats empty 200 body as success', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        status: 200,
        ok: true,
        json: async () => {
          throw new Error('empty body');
        },
        text: async () => '',
      }),
    );
    useAuthStore.setState({ token: 't' });
    await expect(api.delete('/videos/9')).resolves.toBeUndefined();
  });
});

describe('401 handling', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    useAuthStore.getState().logout();
  });

  const unauthorized = () =>
    vi.fn().mockResolvedValue({
      status: 401,
      ok: false,
      json: async () => ({ detail: 'Invalid credentials' }),
      text: async () => '',
    });

  it('tokenless 401 is a normal ApiError (public pages, login form) — no redirect', async () => {
    vi.stubGlobal('fetch', unauthorized());
    useAuthStore.getState().logout();
    const before = window.location.href;
    await expect(api.post('/auth/login', {})).rejects.toMatchObject({
      status: 401,
      message: 'Invalid credentials',
    });
    expect(window.location.href).toBe(before);
  });

  it('401 with a session token logs out', async () => {
    vi.stubGlobal('fetch', unauthorized());
    useAuthStore.setState({ token: 'expired' });
    await expect(api.get('/videos')).rejects.toThrow('Unauthorized');
    expect(useAuthStore.getState().token).toBeNull();
  });
});
