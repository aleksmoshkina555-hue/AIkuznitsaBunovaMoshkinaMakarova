import { useCallback, useEffect, useState } from 'react'
import { api, download } from '../api'
import { useApp } from '../app/context'
import { Icon } from '../components/Icon'
import { Modal } from '../components/Modal'
import { PasswordDialog } from '../components/PasswordDialog'
import { ConfidenceSettings } from './ConfidenceSettings'
import { UPDATED_KEY, UpdatePanel } from './UpdatePanel'
import type { User } from '../types'

const TABS = ['Учётные записи', 'Уверенность ответа', 'Версия', 'Перенос на другой компьютер', 'Журнал']

interface Check {
  item: string
  required: string
  found: string
  ok: boolean
}
type AccountCard = { existing?: User; login: string; name: string; role: string; password: string; repeat: string }

export function AdminPage() {
  const { user: me } = useApp()
  // После обновления страница перезагружается — показать итог и открыть «Версию»
  const [updated] = useState(() => {
    const message = sessionStorage.getItem(UPDATED_KEY)
    sessionStorage.removeItem(UPDATED_KEY)
    return message
  })
  const [tab, setTab] = useState(updated ? 'Версия' : TABS[0])
  const [users, setUsers] = useState<User[]>([])
  const [roles, setRoles] = useState<string[]>([])
  const [selected, setSelected] = useState<User | null>(null)
  const [card, setCard] = useState<AccountCard | null>(null)
  const [shownPassword, setShownPassword] = useState('')
  const [error, setError] = useState('')
  const [passwordFor, setPasswordFor] = useState<string | null>(null)
  const [ownPassword, setOwnPassword] = useState(false)
  const [audit, setAudit] = useState<{ time: string; user: string; action: string; detail: string }[]>([])
  const [checks, setChecks] = useState<Check[]>([])
  const [archiveStatus, setArchiveStatus] = useState('')

  const loadUsers = useCallback(async () => {
    const data = await api.get<{ items: User[]; roles: string[] }>('admin/users')
    setUsers(data.items)
    setRoles(data.roles)
  }, [])

  useEffect(() => {
    if (tab === 'Учётные записи') loadUsers()
    if (tab === 'Журнал') api.get<{ items: typeof audit }>('admin/audit').then((d) => setAudit(d.items))
  }, [tab, loadUsers])

  const need = () => {
    if (!selected) window.alert('Выберите учётную запись в списке.')
    return selected
  }

  const saveAccount = async () => {
    if (!card) return
    setError('')
    try {
      if (card.existing) {
        await api.patch(`admin/users/${card.existing.login}`, { name: card.name, role: card.role })
      } else {
        if (card.password !== card.repeat) return setError('Пароли не совпадают.')
        await api.post('admin/users', { login: card.login, name: card.name, role: card.role, password: card.password })
      }
      setCard(null)
      loadUsers()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const toggle = async () => {
    const u = need()
    if (!u) return
    try {
      await api.patch(`admin/users/${u.login}`, { active: !u.active })
      loadUsers()
    } catch (e) {
      window.alert((e as Error).message)
    }
  }

  const remove = async () => {
    const u = need()
    if (!u || !window.confirm(`Удалить учётную запись «${u.login}» (${u.name})? Вместо удаления можно заблокировать.`)) return
    try {
      await api.del(`admin/users/${u.login}`)
      setSelected(null)
      loadUsers()
    } catch (e) {
      window.alert((e as Error).message)
    }
  }

  const generate = async () => {
    if (!card) return
    const { password } = await api.get<{ password: string }>('admin/new-password')
    setCard({ ...card, password, repeat: password })
    setShownPassword(password)
  }

  return (
    <div className="page">
      <h1>Администрирование</h1>
      {updated && <div className="banner ok">{updated}</div>}
      <div className="tabs">
        {TABS.map((t) => (
          <button key={t} className={t === tab ? 'active' : ''} onClick={() => setTab(t)}>
            {t}
          </button>
        ))}
      </div>

      {tab === 'Учётные записи' && (
        <>
          <div className="toolbar">
            <button onClick={() => { setShownPassword(''); setError(''); setCard({ login: '', name: '', role: 'Пользователь', password: '', repeat: '' }) }}>
              <Icon name="plus" size={18} /> Добавить
            </button>
            <button onClick={() => { const u = need(); if (u) { setError(''); setCard({ existing: u, login: u.login, name: u.name, role: u.role, password: '', repeat: '' }) } }}>
              <Icon name="edit" size={18} /> Изменить
            </button>
            <button onClick={() => { const u = need(); if (u) (u.login === me?.login ? setOwnPassword(true) : setPasswordFor(u.login)) }}>
              <Icon name="key" size={18} /> Сменить пароль
            </button>
            <button onClick={toggle}>
              <Icon name="lock" size={18} /> Заблокировать / разблокировать
            </button>
            <button onClick={remove}>
              <Icon name="trash" size={18} /> Удалить
            </button>
          </div>
          <div className="table-wrap">
            <table className="list">
              <thead>
                <tr>
                  <th>Логин</th>
                  <th>ФИО</th>
                  <th>Роль</th>
                  <th>Состояние</th>
                  <th>Последний вход</th>
                </tr>
              </thead>
              <tbody>
                {users.map((u) => (
                  <tr
                    key={u.login}
                    className={[selected?.login === u.login ? 'selected' : '', u.active ? '' : 'closed'].join(' ')}
                    onClick={() => setSelected(u)}
                    onDoubleClick={() => setCard({ existing: u, login: u.login, name: u.name, role: u.role, password: '', repeat: '' })}
                  >
                    <td>{u.login}</td>
                    <td>{u.name}</td>
                    <td>{u.role}</td>
                    <td>{u.active ? 'активна' : 'заблокирована'}</td>
                    <td>{u.last_login || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="hint">
            Администратор — всё, что может пользователь, а также учётные записи, добавление документов в базу, справочник
            «Сотрудники» и архив для переноса. Пользователь — вопросы, обращения, просмотр документов и справочника.
            Последнего администратора удалить или заблокировать нельзя.
          </p>
        </>
      )}

      {tab === 'Уверенность ответа' && <ConfidenceSettings />}

      {tab === 'Версия' && <UpdatePanel />}

      {tab === 'Перенос на другой компьютер' && (
        <section className="card">
          <p className="lead">
            Архив для переноса — один .zip: папка «Проект» (программа, веб-версия, база знаний, входящие, справочники, база
            данных с обращениями, историей и учётными записями) и папка «Документы» с файлами Word: инструкция по переносу,
            роли и пользователи (без паролей), перечень файлов с контрольными суммами, инструкции.
          </p>
          <div className="row">
            <button
              className="primary"
              onClick={async () => {
                setArchiveStatus('Собираю архив…')
                try {
                  const name = await download('admin/transfer-archive', 'POST')
                  setArchiveStatus(`Архив скачан: ${name}`)
                } catch (e) {
                  setArchiveStatus(`Архив не создан: ${(e as Error).message}`)
                }
              }}
            >
              Создать архив для переноса
            </button>
            <button onClick={() => api.get<{ items: Check[] }>('admin/system-check').then((d) => setChecks(d.items))}>
              Проверить этот компьютер
            </button>
            <span className="hint">{archiveStatus}</span>
          </div>
          {!!checks.length && (
            <table className="list">
              <thead>
                <tr>
                  <th />
                  <th>Что</th>
                  <th>На этом компьютере</th>
                  <th>Нужно</th>
                </tr>
              </thead>
              <tbody>
                {checks.map((c) => (
                  <tr key={c.item} className={c.ok ? '' : 'overdue'}>
                    <td>{c.ok ? 'OK' : 'НЕТ'}</td>
                    <td>{c.item}</td>
                    <td>{c.found}</td>
                    <td>{c.required}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      )}

      {tab === 'Журнал' && (
        <div className="table-wrap">
          <table className="list">
            <thead>
              <tr>
                <th>Когда</th>
                <th>Кто</th>
                <th>Действие</th>
                <th>Подробности</th>
              </tr>
            </thead>
            <tbody>
              {audit.map((a, n) => (
                <tr key={n}>
                  <td className="nowrap">{a.time}</td>
                  <td>{a.user}</td>
                  <td>{a.action}</td>
                  <td>{a.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {card && (
        <Modal title={card.existing ? `Учётная запись: ${card.existing.login}` : 'Новая учётная запись'} onClose={() => setCard(null)}>
          <form
            className="form-grid"
            onSubmit={(e) => {
              e.preventDefault()
              saveAccount()
            }}
          >
            <label>
              Логин
              <input value={card.login} disabled={!!card.existing} onChange={(e) => setCard({ ...card, login: e.target.value })} autoFocus />
            </label>
            <label>
              ФИО
              <input value={card.name} onChange={(e) => setCard({ ...card, name: e.target.value })} />
            </label>
            <label>
              Роль
              <select value={card.role} onChange={(e) => setCard({ ...card, role: e.target.value })}>
                {roles.map((r) => (
                  <option key={r}>{r}</option>
                ))}
              </select>
            </label>
            {!card.existing && (
              <>
                <label>
                  Пароль
                  <span className="row">
                    <input type="password" value={card.password} onChange={(e) => setCard({ ...card, password: e.target.value })} autoComplete="new-password" />
                    <button type="button" onClick={generate}>Придумать пароль</button>
                  </span>
                </label>
                <label>
                  Пароль ещё раз
                  <input type="password" value={card.repeat} onChange={(e) => setCard({ ...card, repeat: e.target.value })} autoComplete="new-password" />
                </label>
                {shownPassword && (
                  <p className="ok-text">
                    Пароль: <b className="mono">{shownPassword}</b> — передайте его пользователю лично
                  </p>
                )}
                <p className="hint">Не короче 8 символов, латинские буквы и цифры. Хранится только хеш пароля.</p>
              </>
            )}
            {error && <p className="error-text">{error}</p>}
            <div className="dialog-buttons">
              <button type="submit" className="primary">Сохранить</button>
              <button type="button" onClick={() => setCard(null)}>Отмена</button>
            </div>
          </form>
        </Modal>
      )}
      {passwordFor && <PasswordDialog login={passwordFor} onClose={() => setPasswordFor(null)} />}
      {ownPassword && <PasswordDialog own onClose={() => setOwnPassword(false)} />}
    </div>
  )
}
