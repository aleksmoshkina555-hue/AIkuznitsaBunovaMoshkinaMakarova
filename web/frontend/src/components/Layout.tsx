import { useState } from 'react'
import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { api } from '../api'
import { useApp } from '../app/context'
import { Icon } from './Icon'
import { PasswordDialog } from './PasswordDialog'

const NAV_ASK = [{ to: '/', icon: 'ask', label: 'Задать вопрос', end: true }]
const NAV_MAIN = [
  // «Главная» объединена с «Передано специалисту»: обращение открывается из списка, новый вопрос — «＋ Новое обращение»
  { to: '/inbox', icon: 'inbox', label: 'Журнал вопросов' },
  { to: '/inbox/ready', icon: 'ready', label: 'Ответ готов' },
  { to: '/inbox/escalated', icon: 'flag', label: 'Передано специалисту' },
  // «Отправленные» и список «Процессы» убраны из меню: отправленные видны в «Журнале вопросов» (статус «Отправлено»)
]
const NAV_KB = [
  { to: '/attention', icon: 'alert', label: 'Требует внимания' },
  { to: '/documents', icon: 'doc', label: 'База знаний' },
  { to: '/employees', icon: 'people', label: 'Сотрудники' },
  { to: '/history', icon: 'history', label: 'История запросов' },
]

function shortName(name: string): string {
  const parts = name.split(/\s+/).filter(Boolean)
  return parts.length >= 2 ? `${parts[0]} ${parts.slice(1, 3).map((p) => p[0] + '.').join('')}` : name
}

export function Layout() {
  const { user, meta, setUser, fontScale, setFontScale } = useApp()
  const [sidebar, setSidebar] = useState(true)
  const [search, setSearch] = useState('')
  const [passwordOpen, setPasswordOpen] = useState(false)
  const navigate = useNavigate()

  const signOut = async () => {
    if (!window.confirm('Выйти из системы?')) return
    await api.post('auth/logout')
    setUser(null)
    navigate('/')
  }

  const item = (nav: { to: string; icon: string; label: string; end?: boolean }) => (
    <NavLink key={nav.to} to={nav.to} end={nav.end ?? nav.to === '/inbox'} className="nav-item">
      <Icon name={nav.icon} />
      <span>{nav.label}</span>
    </NavLink>
  )

  return (
    <div className="shell">
      <header className="topbar">
        <button className="icon-button" title="Скрыть или показать меню" onClick={() => setSidebar(!sidebar)}>
          <Icon name="menu" size={22} />
        </button>
        <div className="brand">
          <strong>ИИ-консультант</strong> <span>по внутренним процессам</span>
        </div>
        <button className="topbar-new" onClick={() => navigate('/answer', { state: { fresh: Date.now() } })}>
          <Icon name="plus" size={18} /> Новое обращение
        </button>
        <form
          className="topbar-search"
          onSubmit={(e) => {
            e.preventDefault()
            navigate(`/inbox?q=${encodeURIComponent(search)}`)
          }}
        >
          <Icon name="search" size={18} />
          <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Искать обращения…" />
          <button type="submit">Найти</button>
        </form>
        <div className="topbar-right">
          <button className="text-button" title="Мельче" onClick={() => setFontScale(fontScale - 0.1)}>A−</button>
          <button className="text-button" title="Крупнее" onClick={() => setFontScale(fontScale + 0.1)}>A+</button>
          <button className="icon-button" title="Сменить свой пароль" onClick={() => setPasswordOpen(true)}>
            <Icon name="key" />
          </button>
          <button className="user-chip" title="Сменить свой пароль" onClick={() => setPasswordOpen(true)}>
            {shortName(user?.name || user?.login || '')} · {user?.role}
          </button>
          <button className="icon-button" title="Выйти" onClick={signOut}>
            <Icon name="power" />
          </button>
        </div>
      </header>
      <div className="body">
        {sidebar && (
          <nav className="sidebar">
            {NAV_ASK.map(item)}
            <hr />
            {NAV_MAIN.map(item)}
            <hr />
            {NAV_KB.map(item)}
            {meta?.is_admin && item({ to: '/topics', icon: 'flag', label: 'Темы и ответственные' })}
            {meta?.is_admin && item({ to: '/admin', icon: 'admin', label: 'Администрирование' })}
          </nav>
        )}
        <main className="main">
          <Outlet />
        </main>
      </div>
      {passwordOpen && <PasswordDialog own onClose={() => setPasswordOpen(false)} />}
    </div>
  )
}
