import { useState } from 'react'
import { login, register, setAuthToken } from '../api'
import type { UserResp } from '../types'

export function AuthModal({ onSuccess }: {
  onSuccess: (token: string, user: UserResp, refreshToken?: string) => void
}) {
  const [tab, setTab] = useState<'login' | 'register'>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      const resp = tab === 'login'
        ? await login(email.trim(), password)
        : await register(email.trim(), password)
      setAuthToken(resp.access_token)
      localStorage.setItem('jwt_token', resp.access_token)
      if (resp.refresh_token) localStorage.setItem('refresh_token', resp.refresh_token)
      const user: UserResp = { id: resp.user_id, email: resp.email, created_at: '', has_keys: false }
      onSuccess(resp.access_token, user, resp.refresh_token)
    } catch (e: unknown) {
      setError(String((e as Error)?.message ?? e))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="modal-overlay">
      <div className="modal-card">
        <div className="modal-title">Content Generator</div>
        <div className="modal-subtitle">Sign in to generate and manage your content</div>

        <div className="tab-row">
          <button
            className={`tab-btn ${tab === 'login' ? 'tab-active' : ''}`}
            onClick={() => { setTab('login'); setError('') }}
            type="button"
          >Sign In</button>
          <button
            className={`tab-btn ${tab === 'register' ? 'tab-active' : ''}`}
            onClick={() => { setTab('register'); setError('') }}
            type="button"
          >Create Account</button>
        </div>

        <form onSubmit={submit} className="auth-form">
          <div>
            <label>Email</label>
            <input
              type="email"
              placeholder="you@example.com"
              value={email}
              onChange={e => setEmail(e.target.value)}
              required
              autoFocus
            />
          </div>
          <div>
            <label>Password {tab === 'register' && <span className="small">(min 8 chars)</span>}</label>
            <input
              type="password"
              placeholder={tab === 'register' ? 'Create a password' : 'Your password'}
              value={password}
              onChange={e => setPassword(e.target.value)}
              required
              minLength={tab === 'register' ? 8 : undefined}
            />
          </div>
          {error && <div className="error-text">{error}</div>}
          <button type="submit" disabled={loading} style={{ width: '100%', marginTop: 4 }}>
            {loading ? '…' : tab === 'login' ? 'Sign In' : 'Create Account'}
          </button>
        </form>
      </div>
    </div>
  )
}
