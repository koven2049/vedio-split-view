import { create } from 'zustand'

export type Role = 'admin' | 'viewer'

interface AuthState {
  token: string | null
  username: string | null
  role: Role | null
  langPreference: string
  setAuth: (token: string, username: string, role: string, lang?: string) => void
  setLang: (lang: string) => void
  logout: () => void
  isLoggedIn: () => boolean
  isAdmin: () => boolean
  isViewer: () => boolean
  canAnalyze: () => boolean
}

function normalizeRole(role: string | null): Role | null {
  return role === 'admin' || role === 'viewer' ? role : null
}

export const useAuthStore = create<AuthState>((set, get) => ({
  token: localStorage.getItem('vsplit_token'),
  username: localStorage.getItem('vsplit_username'),
  role: normalizeRole(localStorage.getItem('vsplit_role')),
  langPreference: localStorage.getItem('vsplit_lang') || 'zh',

  setAuth: (token, username, role, lang = 'zh') => {
    localStorage.setItem('vsplit_token', token)
    localStorage.setItem('vsplit_username', username)
    localStorage.setItem('vsplit_role', role)
    localStorage.setItem('vsplit_lang', lang)
    set({ token, username, role: normalizeRole(role), langPreference: lang })
  },

  setLang: (lang) => {
    localStorage.setItem('vsplit_lang', lang)
    set({ langPreference: lang })
  },

  logout: () => {
    localStorage.removeItem('vsplit_token')
    localStorage.removeItem('vsplit_username')
    localStorage.removeItem('vsplit_role')
    localStorage.removeItem('vsplit_lang')
    set({ token: null, username: null, role: null, langPreference: 'zh' })
  },

  isLoggedIn: () => !!get().token,
  isAdmin: () => get().role === 'admin',
  isViewer: () => get().role === 'viewer',
  canAnalyze: () => get().role === 'admin',
}))
