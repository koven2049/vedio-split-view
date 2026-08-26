import { createContext, useContext } from 'react';
import zh, { type TranslationKey } from './locales/zh';
import en from './locales/en';

export type Locale = 'zh' | 'en';

const locales: Record<Locale, Record<TranslationKey, string>> = { zh, en };

export type TFunction = (key: TranslationKey, vars?: Record<string, string | number>) => string;

function createT(locale: Locale): TFunction {
  const messages = locales[locale];
  return (key, vars) => {
    let text = messages[key] ?? zh[key] ?? key;
    if (vars) {
      for (const [k, v] of Object.entries(vars)) {
        text = text.replace(new RegExp(`\\{${k}\\}`, 'g'), String(v));
      }
    }
    return text;
  };
}

export interface I18nContextValue {
  locale: Locale;
  t: TFunction;
  setLocale: (l: Locale) => void;
}

export const I18nContext = createContext<I18nContextValue>({
  locale: 'zh',
  t: createT('zh'),
  setLocale: () => {},
});

export function useT(): TFunction {
  return useContext(I18nContext).t;
}

export function useLocale(): I18nContextValue {
  return useContext(I18nContext);
}

export { createT };
export type { TranslationKey };
