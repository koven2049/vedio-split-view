/** New typed error codes must exist in every locale and map to an i18n key —
 *  otherwise the UI falls back to raw error text. */
import { describe, expect, it } from 'vitest';

import { X_ERROR_KEYS, XIAOYUZHOU_ERROR_KEYS } from '../../pages/analyzeHelpers';
import { default as en } from './en';
import { default as zh } from './zh';

describe('xiaoyuzhou audio-validation error codes', () => {
  it('maps the new codes to i18n keys defined in zh and en', () => {
    for (const code of ['audio_undecodable', 'audio_truncated'] as const) {
      const key = XIAOYUZHOU_ERROR_KEYS[code];
      expect(key, `${code} must have a mapping`).toBeTruthy();
      expect(zh[key as keyof typeof zh], `${code} zh entry missing`).toBeTruthy();
      expect(en[key as keyof typeof en], `${code} en entry missing`).toBeTruthy();
    }
  });
});

describe('x platform error codes', () => {
  it('maps x_no_video and x_unavailable to i18n keys defined in zh and en', () => {
    for (const code of ['x_no_video', 'x_unavailable'] as const) {
      const key = X_ERROR_KEYS[code];
      expect(key, `${code} must have a mapping`).toBeTruthy();
      expect(zh[key as keyof typeof zh], `${code} zh entry missing`).toBeTruthy();
      expect(en[key as keyof typeof en], `${code} en entry missing`).toBeTruthy();
    }
  });
});
