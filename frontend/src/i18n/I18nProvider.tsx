import { useMemo, type ReactNode } from 'react';
import { I18nContext, createT, type Locale } from './index';
import { useAuthStore } from '../stores/authStore';
import { api } from '../lib/api';

export default function I18nProvider({ children }: { children: ReactNode }) {
  const locale = (useAuthStore((s) => s.langPreference) || 'zh') as Locale;
  const setLangStore = useAuthStore((s) => s.setLang);

  const setLocale = useMemo(
    () => (l: Locale) => {
      setLangStore(l);
      // Logged-out visitors (public API docs) keep the choice locally; a
      // tokenless PUT would 401 and the api layer would bounce to /login.
      if (useAuthStore.getState().token) api.put('/auth/lang', { lang: l }).catch(() => {});
    },
    [setLangStore],
  );

  const t = useMemo(() => createT(locale), [locale]);

  const value = useMemo(() => ({ locale, t, setLocale }), [locale, t, setLocale]);

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}
