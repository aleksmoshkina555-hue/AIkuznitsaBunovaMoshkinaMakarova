import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'
import { api } from '../api'
import type { Meta, User } from '../types'

interface AppState {
  user: User | null
  meta: Meta | null
  loading: boolean
  setUser: (user: User | null) => void
  refreshMeta: () => Promise<void>
  sources: string[]
  setSources: (groups: string[]) => void
  fontScale: number
  setFontScale: (scale: number) => void
}

const AppContext = createContext<AppState | null>(null)

export function useApp(): AppState {
  const value = useContext(AppContext)
  if (!value) throw new Error('useApp вне AppProvider')
  return value
}

const SOURCES_KEY = 'konsultant.sources'
const FONT_KEY = 'konsultant.fontScale'

export function AppProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [meta, setMeta] = useState<Meta | null>(null)
  const [loading, setLoading] = useState(true)
  const [sources, setSourcesState] = useState<string[]>(() => JSON.parse(localStorage.getItem(SOURCES_KEY) || 'null') ?? [])
  const [fontScale, setFontScaleState] = useState<number>(() => Number(localStorage.getItem(FONT_KEY) || '1'))

  const refreshMeta = useCallback(async () => {
    const data = await api.get<Meta>('meta')
    setMeta(data)
    // Отметки «Искать в» по умолчанию — все источники
    setSourcesState((current) => (current.length || localStorage.getItem(SOURCES_KEY) ? current : data.source_groups))
  }, [])

  useEffect(() => {
    api
      .get<{ user: User | null }>('auth/me')
      .then((data) => setUser(data.user))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    if (user) refreshMeta().catch(() => undefined)
    else setMeta(null)
  }, [user, refreshMeta])

  useEffect(() => {
    document.documentElement.style.fontSize = `${15 * fontScale}px`
  }, [fontScale])

  const setSources = (groups: string[]) => {
    setSourcesState(groups)
    localStorage.setItem(SOURCES_KEY, JSON.stringify(groups))
  }
  const setFontScale = (scale: number) => {
    const value = Math.min(1.4, Math.max(0.8, Math.round(scale * 10) / 10))
    setFontScaleState(value)
    localStorage.setItem(FONT_KEY, String(value))
  }

  return (
    <AppContext.Provider
      value={{ user, meta, loading, setUser, refreshMeta, sources, setSources, fontScale, setFontScale }}
    >
      {children}
    </AppContext.Provider>
  )
}
