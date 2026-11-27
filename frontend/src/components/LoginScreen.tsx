import { useState } from 'react'
import { authRequest, AuthError } from '../api'
import { useLocale } from '../localization'
import { isLanguage } from '../messages'
import { Icon } from './Icon'
import { Brand } from './Brand'

export function LoginScreen({ onLogin, sessionError }: { onLogin: () => void; sessionError: boolean }) {
  const { language, setLanguage, storageError, t } = useLocale()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [rememberMe, setRememberMe] = useState(false)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<'invalid' | 'unavailable' | null>(null)
  const [initialError, setInitialError] = useState(sessionError)

  return <div className="login-page">
    <header className="login-header">
      <div className="brand"><Brand /></div>
      <label className="language-selector"><span>{t('language')}</span>
        <select value={language} onChange={event => {
          if (isLanguage(event.target.value)) setLanguage(event.target.value)
        }}>
          <option value="en" lang="en">English</option>
          <option value="zh-Hant" lang="zh-Hant">繁體中文</option>
          <option value="zh-Hans" lang="zh-Hans">简体中文</option>
        </select>
      </label>
    </header>
    <main className="login-main">
      <div className="login-card">
        <div className="login-symbol"><Icon name="shield" size={25} /></div>
        <p className="eyebrow">{t('operationsWorkspace')}</p>
        <h1>{t('loginTitle')}</h1>
        <p className="login-description">{t('loginDescription')}</p>
        {storageError && <div className="error-message" role="alert">{t('languageStorageError')}</div>}
        {(error || initialError) && <div className="error-message" role="alert">
          {t(error === 'invalid' ? 'loginInvalid' : 'loginServiceError')}
        </div>}
        <form className="login-form" onSubmit={event => {
          event.preventDefault()
          setPending(true); setError(null); setInitialError(false)
          void authRequest('login', { username, password, remember_me: rememberMe }).then(() => {
            setPassword(''); onLogin()
          }).catch(cause => {
            if (!(cause instanceof AuthError && cause.status === 401)) console.error('Login request failed:', cause)
            setError(cause instanceof AuthError && cause.status === 401 ? 'invalid' : 'unavailable')
          }).finally(() => setPending(false))
        }}>
          <label>{t('loginUsername')}
            <input type="text" autoComplete="username" value={username} maxLength={64} required
              onChange={event => setUsername(event.target.value)} />
          </label>
          <label>{t('loginPassword')}
            <input type="password" autoComplete="current-password" value={password} maxLength={256} required
              onChange={event => setPassword(event.target.value)} />
          </label>
          <div>
            <label className="login-remember">
              <input type="checkbox" checked={rememberMe} disabled={pending} aria-describedby="remember-me-hint"
                onChange={event => setRememberMe(event.target.checked)} />
              {t('loginRememberMe')}
            </label>
            <p className="login-remember-hint" id="remember-me-hint">{t('loginRememberMeHint')}</p>
          </div>
          <button type="submit" className="button primary" disabled={pending}>{t(pending ? 'loginSigningIn' : 'loginSignIn')}</button>
        </form>
      </div>
    </main>
    <footer className="login-footer">{t('internalOperations')} · {t('regions')}</footer>
  </div>
}
