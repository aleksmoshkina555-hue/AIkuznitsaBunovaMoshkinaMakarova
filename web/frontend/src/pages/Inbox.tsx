import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { api } from '../api'
import { useApp } from '../app/context'
import { Icon } from '../components/Icon'
import type { RequestItem } from '../types'

const SCOPE_TITLES: Record<string, string> = {
  inbox: 'Журнал вопросов', // все вопросы сотрудников и письма со статусами (раньше — «Входящие»)
  ready: 'Ответ готов',
  escalated: 'Передано специалисту',
  sent: 'Отправленные',
}
const QUICK = ['Все', 'Мои', 'Новые', 'В работе', 'Просроченные', '30 дней'] // «Мои» — переданные мне
export const STATUS_DOT: Record<string, string> = {
  Новое: '#2563eb',
  'В работе': '#f59e0b',
  'Ответ готов': '#16a34a',
  Отправлено: '#9ca3af',
  'Передано специалисту': '#ea580c',
  Отклонено: '#dc2626',
}

interface BatchStatus {
  running: boolean
  done: number
  total: number
  error: string | null
  only: string
}

export function InboxPage() {
  const { scope: scopeParam, process } = useParams()
  const scope = process ? `proc:${process}` : scopeParam || 'inbox'
  const [params] = useSearchParams()
  const q = params.get('q') || ''
  const navigate = useNavigate()
  const { sources } = useApp()
  const [quick, setQuick] = useState('Все')
  const [items, setItems] = useState<RequestItem[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [status, setStatus] = useState('')
  const upload = useRef<HTMLInputElement>(null)

  const load = useCallback(async () => {
    const query = new URLSearchParams({ scope, quick, q })
    const data = await api.get<{ items: RequestItem[] }>(`requests?${query}`)
    setItems(data.items)
  }, [scope, quick, q])

  useEffect(() => {
    load()
  }, [load])

  // Ход «Обработать новые»: опрос раз в 1,5 с, пока идёт обработка
  const poll = useCallback(async () => {
    const data = await api.get<BatchStatus>('requests/process')
    if (data.running) {
      setStatus(`Обработка ${data.done + 1} из ${data.total}…${data.only ? ` (только: ${data.only})` : ''}`)
      load()
      window.setTimeout(poll, 1500)
    } else {
      setStatus(data.error ? `Обработка остановлена: ${data.error}` : 'Новые обращения обработаны')
      load()
    }
  }, [load])

  const processNew = async () => {
    if (!items.some((i) => i.status === 'Новое') && quick === 'Все') {
      const all = await api.get<{ items: RequestItem[] }>('requests?scope=inbox&quick=Новые')
      if (!all.items.length) return setStatus('Новых обращений нет')
    }
    if (!sources.length) return setStatus('Отметьте хотя бы один источник в строке «Искать в»')
    try {
      await api.post('requests/process', { sources })
      poll()
    } catch (e) {
      setStatus((e as Error).message)
    }
  }

  const toggleRead = async () => {
    const item = items.find((i) => i.id === selected)
    if (!item) return
    await api.patch(`requests/${item.id}`, { read: !item.read })
    load()
  }

  // Удаление записи — любой пользователь, в «Журнале вопросов» и «Ответ готов»
  const canDelete = scope === 'inbox' || scope === 'ready'
  const remove = async () => {
    const item = items.find((i) => i.id === selected)
    if (!item) return setStatus('Выберите запись в списке')
    if (!window.confirm(`Удалить запись «${item.preview}» (${item.status})?\n\nОбращение и подготовленный ответ будут удалены, вернуть нельзя.`)) return
    try {
      await api.del(`requests/${encodeURIComponent(item.id)}`)
      setSelected(null)
      setStatus(`Запись «${item.preview}» удалена`)
      load()
    } catch (e) {
      setStatus((e as Error).message)
    }
  }

  const uploadFiles = async (files: FileList) => {
    const form = new FormData()
    Array.from(files).forEach((f) => form.append('files', f))
    const data = await api.post<{ added: number }>('requests/upload', form)
    setStatus(`Загружено писем: ${data.added}`)
    load()
  }

  const open = (id: string | null) => id && navigate(`/answer?request=${encodeURIComponent(id)}`)
  const title = process || SCOPE_TITLES[scope] || 'Журнал вопросов'

  return (
    <div className="page">
      <div className="row spread">
        <h1>{q ? `${title}: поиск «${q}»` : title}</h1>
        <div className="quick">
          {QUICK.map((name) => (
            <button key={name} className={name === quick ? 'active' : ''} onClick={() => setQuick(name)}>
              {name}
            </button>
          ))}
        </div>
      </div>
      <div className="toolbar">
        <button
          onClick={async () => {
            const data = await api.post<{ added: number }>('requests/refresh')
            setStatus(data.added ? `Новых писем: ${data.added}` : 'Новых писем нет')
            load()
          }}
        >
          <Icon name="refresh" size={18} /> Обновить
        </button>
        <button onClick={() => open(selected)}>
          <Icon name="open" size={18} /> Открыть карточку
        </button>
        <button onClick={toggleRead}>
          <Icon name="read" size={18} /> Прочитано
        </button>
        <button onClick={processNew}>
          <Icon name="play" size={18} /> Обработать новые
        </button>
        <button onClick={() => upload.current?.click()}>
          <Icon name="download" size={18} /> Загрузить .eml
        </button>
        {canDelete && (
          <button className="danger" onClick={remove} disabled={!selected} title="Удалить выбранную запись">
            <Icon name="trash" size={18} /> Удалить
          </button>
        )}
        <input
          ref={upload}
          type="file"
          accept=".eml"
          multiple
          hidden
          onChange={(e) => {
            if (e.target.files?.length) uploadFiles(e.target.files)
            e.target.value = ''
          }}
        />
        <span className="toolbar-status">{status}</span>
      </div>
      <div className="table-wrap">
        <table className="list">
          <thead>
            <tr>
              <th className="dot-col" />
              <th>Тема</th>
              <th>От</th>
              <th>Ответственный</th>
              <th>Срок</th>
              <th>Создано</th>
              <th>Статус</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr
                key={item.id}
                className={[
                  item.id === selected ? 'selected' : '',
                  !item.read ? 'unread' : '',
                  item.closed ? 'closed' : item.overdue ? 'overdue' : item.status === 'Передано специалисту' ? 'escalated' : '',
                ].join(' ')}
                onClick={() => setSelected(item.id)}
                onDoubleClick={() => open(item.id)}
              >
                <td className="dot-col">
                  <span className="dot" style={{ background: STATUS_DOT[item.status] || '#2563eb' }} />
                </td>
                <td>{item.preview}</td>
                <td>{item.sender.split('<')[0].trim().replace(/^"|"$/g, '') || item.sender}</td>
                <td>{(item.assignee || '').split(/[<(]/)[0].trim()}</td>
                <td className="nowrap">{item.due}</td>
                <td className="nowrap">{item.created}</td>
                <td className="nowrap">{item.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!items.length && <p className="empty">Обращений нет.</p>}
      </div>
      <p className="hint">
        Двойной щелчок — открыть обращение. Жирным — непрочитанные, зачёркнуто — закрытые, красным — просроченные. Письма из
        папки «входящие» загружаются кнопкой «Обновить».
      </p>
    </div>
  )
}
