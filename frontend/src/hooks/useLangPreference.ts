import { useState, useCallback } from 'react'
import { useAuthStore } from '../stores/authStore'
import { api } from '../lib/api'

export function useLangPreference() {
  const storedLang = useAuthStore((s) => s.langPreference) as 'zh' | 'en'
  const setLangStore = useAuthStore((s) => s.setLang)
  const [lang, setLangLocal] = useState<'zh' | 'en'>(storedLang)

  const setLang = useCallback((newLang: 'zh' | 'en') => {
    setLangLocal(newLang)
    setLangStore(newLang)
    api.put('/auth/lang', { lang: newLang }).catch(() => {})
  }, [setLangStore])

  return { lang, setLang }
}
