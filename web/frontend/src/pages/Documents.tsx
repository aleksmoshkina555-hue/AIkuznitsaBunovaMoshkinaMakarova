import { useCallback, useEffect, useRef, useState } from 'react'
import { api, ApiError } from '../api'
import { useApp } from '../app/context'
import { DocModal, STATUS_COLORS } from '../components/DocView'
import { Modal } from '../components/Modal'
import type { Doc } from '../types'

interface DocRow {
  id: string
  status: string
  type: string
  title: string
  date: string
  owner: string
  reason: string
  departments: string[]
  placed?: string
}

interface Card {
  id: string
  fields: Record<string, string>
  departments: string[]
  isNew: boolean
}

const STATUSES = ['Все', 'Действует', 'Устарел', 'Не вступил в силу', 'Противоречит']
const FOR_ALL = 'Все документы'
const CARD_KEYS = ['type', 'number', 'title', 'date', 'placed', 'edition']
const EDITIONS = ['Актуальная редакция', 'Утратил силу']

/** ДД.ММ.ГГГГ (так хранится в карточке) ↔ ГГГГ-ММ-ДД (поле с календарём). */
const toInput = (d: string) => (/^\d{2}\.\d{2}\.\d{4}$/.test(d) ? `${d.slice(6)}-${d.slice(3, 5)}-${d.slice(0, 2)}` : '')
const fromInput = (d: string) => (d ? `${d.slice(8)}.${d.slice(5, 7)}.${d.slice(0, 4)}` : '')
const today = () => {
  const now = new Date()
  return `${String(now.getDate()).padStart(2, '0')}.${String(now.getMonth() + 1).padStart(2, '0')}.${now.getFullYear()}`
}

interface Deleted {
  id: string
  file: string
  deleted: string
}

/** «Для кого» в карточке: для всех (по умолчанию) или только для отмеченных подразделений. */
function DepartmentsPicker({ value, onChange }: { value: string[]; onChange: (value: string[]) => void }) {
  const { meta } = useApp()
  const known = meta?.departments || []
  const all = [...known, ...value.filter((d) => !known.includes(d))] // устаревшие привязки тоже видны — их можно снять
  const [only, setOnly] = useState(value.length > 0)
  const toggle = (d: string) => onChange(value.includes(d) ? value.filter((x) => x !== d) : [...value, d])
  return (
    <fieldset className="dept-field">
      <legend>Для кого действует документ</legend>
      <label className="check">
        <input
          type="radio"
          checked={!only}
          onChange={() => {
            setOnly(false)
            onChange([])
          }}
        />{' '}
        Для всех подразделений <span className="hint">— обычно так: приказы и регламенты компании</span>
      </label>
      <label className="check">
        <input type="radio" checked={only} onChange={() => setOnly(true)} /> Только для подразделений:
      </label>
      {only && (
        <div className="dept-pick">
          {all.map((d) => (
            <label key={d}>
              <input type="checkbox" checked={value.includes(d)} onChange={() => toggle(d)} />
              {d}
              {!known.includes(d) && <span className="warn-text">(нет в справочнике)</span>}
            </label>
          ))}
          {!all.length && <span className="hint">Справочник «Сотрудники» пуст — сначала заполните его.</span>}
        </div>
      )}
      {only && !value.length && <p className="warn-text">Отметьте хотя бы одно подразделение — иначе документ будет для всех.</p>}
      <p className="hint">
        Сотрудники других подразделений не получат ответов по этому документу; если ответ есть только в нём, вопрос
        уйдёт специалисту с пометкой, где искать.
      </p>
    </fieldset>
  )
}

export function DocumentsPage() {
  const { meta } = useApp()
  const [items, setItems] = useState<DocRow[]>([])
  const [mode, setMode] = useState('')
  const [status, setStatus] = useState('Все')
  const [forWhom, setForWhom] = useState(FOR_ALL)
  const [needle, setNeedle] = useState('')
  const [opened, setOpened] = useState<Doc | null>(null)
  const [card, setCard] = useState<Card | null>(null)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [uploading, setUploading] = useState('')
  const [vectors, setVectors] = useState<{ running: boolean; missing: number }>({ running: false, missing: 0 })
  const fileInput = useRef<HTMLInputElement>(null)
  const departments = meta?.departments || []

  const load = useCallback(async () => {
    const data = await api.get<{ items: DocRow[]; search_mode: string; vectors?: { running: boolean; missing: number } }>('documents')
    setItems(data.items)
    setMode(data.search_mode)
    setVectors(data.vectors || { running: false, missing: 0 })
  }, [])
  useEffect(() => {
    load()
  }, [load])
  // Пока векторы новых документов считаются в фоне — обновлять строку состояния раз в 5 секунд
  useEffect(() => {
    if (!vectors.missing && !vectors.running) return
    const timer = window.setInterval(load, 5000)
    return () => window.clearInterval(timer)
  }, [vectors.missing, vectors.running, load])

  const openCard = (detail: Record<string, unknown>, isNew: boolean) => {
    const text = (key: string) => String(detail[key] ?? '')
    setError('')
    const fields = Object.fromEntries(CARD_KEYS.map((key) => [key, text(key)]))
    // Дата документа и дата размещения по умолчанию — сегодня; статус — актуальная редакция
    fields.date ||= today()
    fields.placed ||= today()
    fields.edition ||= EDITIONS[0]
    setCard({ id: text('id'), isNew, departments: (detail.departments as string[]) || [], fields })
  }

  const [deleted, setDeleted] = useState<Deleted[]>([])
  const [showDeleted, setShowDeleted] = useState(false)
  const loadDeleted = useCallback(() => {
    if (meta?.is_admin) api.get<{ items: Deleted[] }>('documents/deleted').then((d) => setDeleted(d.items))
  }, [meta?.is_admin])
  useEffect(() => {
    loadDeleted()
  }, [loadDeleted])

  const remove = async (id: string, title: string) => {
    if (!window.confirm(`Удалить из базы документ «${title}»?\n\nОн перестанет использоваться в ответах. Вернуть можно в разделе «Удалённые документы» внизу страницы.`)) return
    try {
      await api.del(`documents/${encodeURIComponent(id)}/delete`)
      setCard(null)
      setMessage(`Документ «${title}» удалён из базы — его можно вернуть в «Удалённых документах»`)
      load()
      loadDeleted()
    } catch (e) {
      setMessage((e as Error).message)
    }
  }

  const restore = async (item: Deleted) => {
    try {
      await api.post(`documents/deleted/${encodeURIComponent(item.id)}/restore`)
      setMessage(`Документ «${item.file}» возвращён в базу`)
      load()
      loadDeleted()
    } catch (e) {
      setMessage((e as Error).message)
    }
  }

  const purge = async (item: Deleted) => {
    if (!window.confirm(`Полностью удалить документ «${item.file}» из системы?\n\nФайл и карточка будут стёрты с диска. Вернуть документ будет нельзя.`)) return
    try {
      await api.del(`documents/deleted/${encodeURIComponent(item.id)}`)
      setMessage(`Документ «${item.file}» полностью удалён из системы`)
      loadDeleted()
    } catch (e) {
      setMessage((e as Error).message)
    }
  }

  const upload = async (file: File, overwrite = false) => {
    const form = new FormData()
    form.append('file', file)
    if (overwrite) form.append('overwrite', '1')
    setMessage('')
    setUploading(`Загружаю «${file.name}» и читаю документ…`)
    try {
      openCard(await api.post<Record<string, unknown>>('documents/upload', form), true)
      load()
    } catch (e) {
      if (e instanceof ApiError && e.status === 409 && window.confirm(`В базе уже есть файл «${file.name}». Заменить его новой версией?`)) {
        upload(file, true)
      } else if (!(e instanceof ApiError && e.status === 409)) setMessage((e as Error).message)
    } finally {
      setUploading('')
    }
  }

  const editCard = async (id: string) => openCard(await api.get<Record<string, unknown>>(`documents/${encodeURIComponent(id)}`), false)

  const saveCard = async () => {
    if (!card) return
    try {
      await api.put(`documents/${encodeURIComponent(card.id)}/card`, { ...card.fields, departments: card.departments })
    } catch (e) {
      return setError((e as Error).message)
    }
    const whom = card.departments.length ? `для: ${card.departments.join(', ')}` : 'для всех подразделений'
    setMessage(`${card.isNew ? 'Документ добавлен в базу' : 'Карточка сохранена'} — ${whom}`)
    setCard(null)
    load()
  }

  const shown = items.filter(
    (d) =>
      (status === 'Все' || d.status === status) &&
      (forWhom === FOR_ALL || !d.departments.length || d.departments.includes(forWhom)) &&
      (!needle || `${d.title} ${d.type} ${d.owner} ${d.departments.join(' ')}`.toLowerCase().includes(needle.toLowerCase())),
  )
  return (
    <div className="page">
      <h1>База знаний</h1>
      <p className="lead">
        Документы, по которым система отвечает: приказы, регламенты, письма, чаты и ответы службы качества.
        {meta?.is_admin
          ? ' «Открыть» — прочитать и изменить текст, «Карточка» — вид, даты, «Для кого», «Удалить» — убрать из базы.'
          : ' «Открыть» — прочитать текст документа.'}
      </p>
      <div className="row spread">
        <span className="row">
          <label className="inline">
            Статус:
            <select value={status} onChange={(e) => setStatus(e.target.value)}>
              {STATUSES.map((s) => (
                <option key={s}>{s}</option>
              ))}
            </select>
          </label>
          {!!departments.length && (
            <label className="inline">
              Для кого:
              <select value={forWhom} onChange={(e) => setForWhom(e.target.value)} title="Документы, которые действуют для подразделения">
                <option>{FOR_ALL}</option>
                {departments.map((d) => (
                  <option key={d}>{d}</option>
                ))}
              </select>
            </label>
          )}
          <label className="inline">
            Поиск:
            <input value={needle} onChange={(e) => setNeedle(e.target.value)} />
          </label>
        </span>
        {meta?.is_admin && (
          <span className="row">
            <button className="primary" disabled={!!uploading} onClick={() => fileInput.current?.click()}>
              {uploading ? 'Загружаю…' : '＋ Добавить документ'}
            </button>
            <input
              ref={fileInput}
              type="file"
              accept=".docx,.pdf,.eml,.msg,.txt,.md"
              hidden
              onChange={(e) => {
                if (e.target.files?.[0]) upload(e.target.files[0])
                e.target.value = ''
              }}
            />
          </span>
        )}
      </div>
      <p className="hint">Поиск: {mode}</p>
      {uploading && <p className="status-text">{uploading}</p>}
      {(vectors.missing > 0 || vectors.running) && (
        <div className="banner warn">
          Новые документы уже в базе и ищутся по словам. Поиск по смыслу для них досчитывается
          {vectors.missing ? ` (осталось фрагментов: ${vectors.missing})` : ''} — большой документ обрабатывается несколько
          минут, работать можно как обычно.
        </div>
      )}
      {message && <p className="ok-text">{message}</p>}
      <div className="table-wrap">
        <table className="list">
          <thead>
            <tr>
              <th>Статус</th>
              <th>Тип</th>
              <th>Название</th>
              <th>Дата</th>
              <th>Размещён</th>
              <th>Для кого</th>
              <th>Причина статуса</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {shown.map((d) => (
              <tr key={d.id} style={{ color: STATUS_COLORS[d.status] }} onDoubleClick={() => setOpened({ id: d.id, title: d.title, source_type: d.type, date: d.date })}>
                <td className="nowrap">{d.status}</td>
                <td className="nowrap">{d.type}</td>
                <td>{d.title}</td>
                <td className="nowrap">{d.date}</td>
                <td className="nowrap">{d.placed || '—'}</td>
                <td>
                  {d.departments.length ? d.departments.map((x) => <span key={x} className="dept-tag">{x}</span>) : <span className="dept-all">все</span>}
                </td>
                <td>{d.reason}</td>
                <td className="row-actions">
                  <button
                    className="small"
                    onClick={() => setOpened({ id: d.id, title: d.title, source_type: d.type, date: d.date })}
                    title={meta?.is_admin ? 'Открыть документ: прочитать текст, изменить текст' : 'Открыть документ и прочитать текст'}
                  >
                    Открыть
                  </button>
                  {meta?.is_admin && (
                    <>
                      {' '}
                      <button className="small" onClick={() => editCard(d.id)} title="Изменить карточку: вид, даты, статус, «Для кого»">
                        Карточка
                      </button>{' '}
                      <button className="small danger" onClick={() => remove(d.id, d.title)} title="Убрать документ из базы (можно вернуть)">
                        Удалить
                      </button>
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="hint">
        Двойной щелчок — открыть документ. Новые документы добавляет администратор («＋ Добавить документ» или файл в папке базы) —
        база обновляется сама. «Для кого: все» — документ действует для всех подразделений.
      </p>
      {meta?.is_admin && !!deleted.length && (
        <section className="deleted-docs">
          <button className="link" onClick={() => setShowDeleted(!showDeleted)}>
            {showDeleted ? '▾' : '▸'} Удалённые документы ({deleted.length})
          </button>
          {showDeleted && (
            <table className="list">
              <tbody>
                {deleted.map((item) => (
                  <tr key={item.id}>
                    <td>{item.file}</td>
                    <td className="nowrap">удалён {item.deleted}</td>
                    <td>
                      <button className="small" onClick={() => restore(item)}>
                        Вернуть в базу
                      </button>{' '}
                      <button className="small danger" onClick={() => purge(item)}>
                        Полностью удалить из системы
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      )}
      {opened && <DocModal doc={opened} onClose={() => setOpened(null)} onChanged={load} />}
      {card && (
        <Modal title={card.isNew ? 'Новый документ: карточка' : 'Карточка документа'} onClose={() => setCard(null)}>
          {card.isNew && <p className="hint">Система определила данные документа сама. Проверьте и при необходимости поправьте.</p>}
          <div className="card-grid">
            <label>
              Вид документа
              <select value={card.fields.type} onChange={(e) => setCard({ ...card, fields: { ...card.fields, type: e.target.value } })}>
                {[...new Set([...(meta?.document_types || []), card.fields.type].filter(Boolean))].map((t) => (
                  <option key={t}>{t}</option>
                ))}
              </select>
            </label>
            <label>
              Номер
              <input value={card.fields.number} placeholder="например, 118-П" onChange={(e) => setCard({ ...card, fields: { ...card.fields, number: e.target.value } })} />
            </label>
            <label className="wide">
              Название
              <input value={card.fields.title} onChange={(e) => setCard({ ...card, fields: { ...card.fields, title: e.target.value } })} />
            </label>
            <label>
              Дата документа
              <input type="date" value={toInput(card.fields.date)} onChange={(e) => setCard({ ...card, fields: { ...card.fields, date: fromInput(e.target.value) } })} />
            </label>
            <label>
              Дата размещения
              <input type="date" value={toInput(card.fields.placed)} onChange={(e) => setCard({ ...card, fields: { ...card.fields, placed: fromInput(e.target.value) } })} />
            </label>
            <label className="wide">
              Статус
              <select value={card.fields.edition} onChange={(e) => setCard({ ...card, fields: { ...card.fields, edition: e.target.value } })}>
                {EDITIONS.map((s) => (
                  <option key={s}>{s}</option>
                ))}
              </select>
              <span className="hint">
                {card.fields.edition === EDITIONS[1]
                  ? 'Документ не будет использоваться в ответах; в ответах по теме он будет отмечен как недействующий.'
                  : 'Действует, пока его не отменит новый документ (система следит за этим сама).'}
              </span>
            </label>
          </div>
          <DepartmentsPicker value={card.departments} onChange={(value) => setCard({ ...card, departments: value })} />
          {error && <p className="error-text">{error}</p>}
          <div className="dialog-buttons">
            {!card.isNew && (
              <button className="danger left" onClick={() => remove(card.id, card.fields.title)}>
                Удалить документ
              </button>
            )}
            <button className="primary" onClick={saveCard}>Сохранить</button>
            <button
              onClick={() => {
                setCard(null)
                if (card.isNew) setMessage('Документ добавлен в базу — для всех подразделений')
              }}
            >
              {card.isNew ? 'Оставить как определено' : 'Отмена'}
            </button>
          </div>
        </Modal>
      )}
    </div>
  )
}
