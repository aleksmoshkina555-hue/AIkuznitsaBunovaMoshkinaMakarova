import { useEffect, useState } from 'react'
import { api } from '../api'
import { useApp } from '../app/context'
import type { User } from '../types'

/** Вход; при первом запуске (учётных записей нет) — создание администратора. */
export function LoginPage() {
  const { setUser } = useApp()
  const [setup, setSetup] = useState(false)
  const [login, setLogin] = useState('')
  const [name, setName] = useState('')
  const [password, setPassword] = useState('')
  const [repeat, setRepeat] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api.get<{ needs_admin: boolean }>('auth/setup').then((d) => {
      setSetup(d.needs_admin)
      if (d.needs_admin) setLogin('admin')
    })
  }, [])

  const submit = async () => {
    setError('')
    if (setup && password !== repeat) return setError('Пароли не совпадают.')
    setBusy(true)
    try {
      const data = await api.post<{ user: User }>(setup ? 'auth/setup/admin' : 'auth/login', { login, name, password })
      await api.get('auth/me') // новый csrftoken после входа
      setUser(data.user)
    } catch (e) {
      setError((e as Error).message)
      setPassword('')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login-screen">
      <form
        className="login-card"
        onSubmit={(e) => {
          e.preventDefault()
          submit()
        }}
      >
        <div className="login-head">ИИ-консультант</div>
        <div className="login-body">
          <p>
            {setup
              ? 'Первый запуск: создайте учётную запись администратора. Он добавит остальных пользователей в разделе «Администрирование».'
              : 'Войдите под своей учётной записью.'}
          </p>
          <label>
            Логин
            <input value={login} onChange={(e) => setLogin(e.target.value)} autoFocus autoComplete="username" />
          </label>
          {setup && (
            <label>
              ФИО
              <input value={name} onChange={(e) => setName(e.target.value)} />
            </label>
          )}
          <label>
            Пароль
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete={setup ? 'new-password' : 'current-password'} />
          </label>
          {setup && (
            <>
              <label>
                Пароль ещё раз
                <input type="password" value={repeat} onChange={(e) => setRepeat(e.target.value)} autoComplete="new-password" />
              </label>
              <p className="hint">Пароль — не короче 8 символов, буквы и цифры.</p>
            </>
          )}
          {error && <p className="error-text">{error}</p>}
          <button type="submit" className="primary big" disabled={busy}>
            {setup ? 'Создать и войти' : 'Войти'}
          </button>
        </div>
      </form>
    </div>
  )
}
