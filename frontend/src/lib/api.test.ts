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
