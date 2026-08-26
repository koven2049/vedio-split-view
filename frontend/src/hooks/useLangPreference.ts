import { useLocale } from '../i18n';

export function useLangPreference() {
  const { locale, setLocale } = useLocale();
  return { lang: locale, setLang: setLocale };
}
