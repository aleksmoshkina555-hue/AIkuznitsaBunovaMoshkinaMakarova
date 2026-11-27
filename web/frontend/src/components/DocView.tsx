import { useEffect, useRef, useState } from 'react'
import { api, download } from '../api'
import { useApp } from '../app/context'
import type { Doc } from '../types'
import { Modal } from './Modal'

interface DocDetail {
  id: string
  title: string
  type: string
  date: string
  owner: string
  status: string
  reason: string
  chunks: { point: string | null; text: string }[]
  has_file: boolean
  text: string
  file_format: string
}

const FORMAT_NOTES: Record<string, string> = {
  '.docx': 'Документ Word будет сохранён заново по строкам: оформление (шрифты, таблицы, нумерация Word) не сохранится, текст — да.',
  '.pdf': 'PDF изменить на месте нельзя: документ будет сохранён как Word (.docx) с тем же номером и карточкой.',
  '.eml': 'Письмо будет сохранено как текстовый файл (.txt) с тем же номером и карточкой (отправитель и дата — в карточке).',
  '.msg': 'Письмо будет сохранено как текстовый файл (.txt) с тем же номером и карточкой (отправитель и дата — в карточке).',
}

export const STATUS_COLORS: Record<string, string> = {
  Действует: '#166534',
  Устарел: '#991b1b',
  Противоречит: '#991b1b',
  'Не вступил в силу': '#92400e',
}

/** Подпись документа как в прежней версии (agent.describe_doc): «Приказ №118-П «О …» от 10.02.2026, п. 1». */
export function docLabel(doc: Doc, withPoints = true): string {
  const title = doc.title || doc.id
  const type = doc.source_type || ''
  const name = title.toLowerCase().startsWith(type.toLowerCase())
    ? title
    : title.includes('«')
      ? `${type} ${title}`
      : `${type} «${title}»`
  const points = withPoints && doc.points?.length ? `, п. ${doc.points.join(', ')}` : ''
  return `${name}${doc.date ? ` от ${doc.date}` : ''}${points}` // у регламента может не быть даты
}

/** Окно документа: текст по пунктам, пункт-основание подсвечен; «Скачать файл»; администратору — «Изменить текст». */
export function DocModal({ doc, onClose, onChanged }: { doc: Doc; onClose: () => void; onChanged?: () => void }) {
  const { meta } = useApp()
  const [detail, setDetail] = useState<DocDetail | null>(null)
  const [error, setError] = useState('')
  const [editing, setEditing] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [note, setNote] = useState('')
  const firstCited = useRef<HTMLParagraphElement | null>(null)
  const cited = new Set(doc.points || [])

  const save = async () => {
    if (editing === null) return
    setSaving(true)
    setError('')
    try {
      const data = await api.put<DocDetail>(`documents/${encodeURIComponent(doc.id)}/text`, { text: editing })
      setDetail(data)
      setEditing(null)
      setNote('Текст сохранён — база знаний обновлена. Прежняя версия сохранена в папке data/document_versions.')
      onChanged?.()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  useEffect(() => {
    api
      .get<DocDetail>(`documents/${encodeURIComponent(doc.id)}`)
      .then(setDetail)
      .catch((e) => setError(e.message))
  }, [doc.id])

  useEffect(() => {
    firstCited.current?.scrollIntoView({ block: 'center' })
  }, [detail])

  let marked = false
  return (
    <Modal title={detail?.title || doc.title} onClose={onClose} wide>
      {error && <p className="error-text">{error}</p>}
      {detail && (
        <>
          <p className="muted">
            {detail.type} · {detail.date} · Владелец: {detail.owner}
          </p>
          <p style={{ color: STATUS_COLORS[detail.status] || '#475569' }}>
            Статус: {detail.status}
            {detail.reason ? ` — ${detail.reason}` : ''}
          </p>
          {note && <p className="ok-text">{note}</p>}
          {editing !== null ? (
            <>
              {FORMAT_NOTES[detail.file_format] && <p className="warn-text">{FORMAT_NOTES[detail.file_format]}</p>}
              <p className="hint">Пункты — с новой строки, с номером в начале («1.2. …»): по ним система ссылается на основание.</p>
              <textarea className="doc-edit" value={editing} onChange={(e) => setEditing(e.target.value)} rows={22} />
            </>
          ) : (
            <div className="doc-text">
              {detail.chunks.map((chunk, n) => {
                const isCited = chunk.point !== null && cited.has(chunk.point)
                const ref = isCited && !marked ? firstCited : undefined
                if (isCited) marked = true
                return (
                  <p key={n} ref={ref} className={isCited ? 'cited' : ''}>
                    {chunk.point && <b>п. {chunk.point}. </b>}
                    {chunk.text}
                  </p>
                )
              })}
            </div>
          )}
          <div className="dialog-buttons spread">
            <span className="warn-text">{editing === null && cited.size ? 'Подсвечен пункт, на который ссылается ответ' : ''}</span>
            {editing !== null ? (
              <span>
                <button onClick={() => setEditing(null)} disabled={saving}>
                  Отмена
                </button>
                <button className="primary" onClick={save} disabled={saving || editing === detail.text}>
                  {saving ? 'Сохраняю…' : 'Сохранить текст'}
                </button>
              </span>
            ) : (
              <span>
                {meta?.is_admin && detail.has_file && (
                  <button
                    onClick={() => {
                      setNote('')
                      setEditing(detail.text)
                    }}
                    title="Исправить текст документа в базе знаний"
                  >
                    ✎ Изменить текст
                  </button>
                )}
                {detail.has_file && (
                  <button onClick={() => download(`documents/${encodeURIComponent(doc.id)}/file`)}>Скачать файл</button>
                )}
                <button className="primary" onClick={onClose}>
                  Закрыть
                </button>
              </span>
            )}
          </div>
        </>
      )}
    </Modal>
  )
}
