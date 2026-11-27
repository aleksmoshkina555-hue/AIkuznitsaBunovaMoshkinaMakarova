import { useState } from 'react'
import { api } from '../api'
import { Modal } from './Modal'

/** own — «Сменить свой пароль» (нужен текущий); иначе администратор назначает пароль пользователю login. */
export function PasswordDialog({ own, login, onClose }: { own?: boolean; login?: string; onClose: () => void }) {
  const [old, setOld] = useState('')
  const [password, setPassword] = useState('')
  const [repeat, setRepeat] = useState('')
  const [shown, setShown] = useState('')
  const [error, setError] = useState('')
  const [done, setDone] = useState(false)

  const generate = async () => {
    const data = await api.get<{ password: string }>('admin/new-password')
    setPassword(data.password)
    setRepeat(data.password)
    setShown(data.password)
  }

  const save = async () => {
    setError('')
    if (password !== repeat) return setError('Новые пароли не совпадают.')
    try {
      if (own) await api.post('auth/password', { old, new: password })
      else await api.post(`admin/users/${login}/password`, { password })
      setDone(true)
    } catch (e) {
      setError((e as Error).message)
    }
  }

  return (
    <Modal title={own ? 'Сменить свой пароль' : `Новый пароль: ${login}`} onClose={onClose}>
      {done ? (
        <>
          <p className="ok-text">
            {own ? 'Ваш пароль изменён. В следующий раз входите с новым паролем.' : 'Новый пароль сохранён. Сообщите его пользователю лично.'}
          </p>
          <div className="dialog-buttons">
            <button className="primary" onClick={onClose}>Готово</button>
          </div>
        </>
      ) : (
        <form
          className="form-grid"
          onSubmit={(e) => {
            e.preventDefault()
            save()
          }}
        >
          {own && (
            <label>
              Текущий пароль
              <input type="password" autoFocus value={old} onChange={(e) => setOld(e.target.value)} autoComplete="current-password" />
            </label>
          )}
          <label>
            Новый пароль
            <span className="row">
              <input type="password" autoFocus={!own} value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" />
              {!own && (
                <button type="button" onClick={generate}>
                  Придумать пароль
                </button>
              )}
            </span>
          </label>
          <label>
            Ещё раз
            <input type="password" value={repeat} onChange={(e) => setRepeat(e.target.value)} autoComplete="new-password" />
          </label>
          {shown && <p className="ok-text">Пароль: <b className="mono">{shown}</b> — передайте его пользователю лично</p>}
          <p className="hint">Не короче 8 символов, латинские буквы и цифры.</p>
          {error && <p className="error-text">{error}</p>}
          <div className="dialog-buttons">
            <button type="submit" className="primary">Сохранить</button>
            <button type="button" onClick={onClose}>Отмена</button>
          </div>
        </form>
      )}
    </Modal>
  )
}
