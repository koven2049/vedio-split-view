import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Video } from 'lucide-react'
import { useLogin } from '../hooks/useAuth'
import { useT } from '../i18n'

export default function LoginPage() {
  const t = useT()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')

  const navigate = useNavigate()
  const login = useLogin()
  const error = login.error?.message || ''

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    login.mutate({ username, password }, {
      onSuccess: () => navigate('/'),
    })
  }

  return (
    <div className="min-h-screen flex items-center justify-center" style={{ background: 'var(--color-bg-secondary)' }}>
      <div
        className="w-full max-w-sm p-8 rounded-2xl shadow-lg"
        style={{ background: 'var(--color-bg)', border: '1px solid var(--color-border)' }}
      >
        <div className="flex items-center justify-center gap-2 mb-8">
          <Video size={28} style={{ color: 'var(--color-primary)' }} />
          <h1 className="text-2xl font-bold">VideoSplit</h1>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium mb-1.5" style={{ color: 'var(--color-text-secondary)' }}>
              {t('login.username')}
            </label>
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
              className="w-full px-3 py-2.5 rounded-lg text-sm outline-none transition-colors"
              style={{
                background: 'var(--color-bg-secondary)',
                border: '1px solid var(--color-border)',
                color: 'var(--color-text)',
              }}
              placeholder={t('login.usernamePlaceholder')}
            />
          </div>

          <div>
            <label className="block text-sm font-medium mb-1.5" style={{ color: 'var(--color-text-secondary)' }}>
              {t('login.password')}
            </label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              className="w-full px-3 py-2.5 rounded-lg text-sm outline-none transition-colors"
              style={{
                background: 'var(--color-bg-secondary)',
                border: '1px solid var(--color-border)',
                color: 'var(--color-text)',
              }}
              placeholder={t('login.passwordPlaceholder')}
            />
          </div>

          {error && (
            <p className="text-sm" style={{ color: 'var(--color-danger)' }}>{error}</p>
          )}

          <button
            type="submit"
            disabled={login.isPending}
            className="w-full py-2.5 rounded-lg text-sm font-medium text-white transition-colors disabled:opacity-50"
            style={{ background: 'var(--color-primary)' }}
          >
            {login.isPending ? t('login.loading') : t('login.submit')}
          </button>
        </form>

        <p className="text-center text-xs mt-6" style={{ color: 'var(--color-text-secondary)' }}>
          {t('login.contactAdmin')}
        </p>
      </div>
    </div>
  )
}
