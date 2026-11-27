import { useEffect, useState } from 'react'
import { api } from '../api'
import { AnswerBody, Badge } from '../components/Answer'
import { ConfidenceChip } from '../components/ConfidenceChip'
import { Modal } from '../components/Modal'
import type { AgentResult, Described } from '../types'

export const SOURCE_NAMES: Record<string, string> = {
  process_map: 'готовый ответ по теме',
  actuality: 'граф версий документов',
  quality: 'ответ службы качества',
  model: 'языковая модель',
  sources: 'текст документа',
  escalation: 'передано специалисту',
  blocked: 'фильтр безопасности',
}

interface Row {
  id: number
  time: string
  sender: string
  body: string
  answer_source: string | null
  answer: string
  dialog?: string
  step?: number
  understood_as?: string
}

type Opened = Described & {
  result: AgentResult | null
  body: string
  understood_as?: string
  chain?: { id: number; step: number; body: string; understood_as: string }[]
}

/** Цепочки уточнений — вместе: сначала вопрос, под ним уточнения по порядку; цепочки — от новых к старым. */
function grouped(items: Row[]): Row[] {
  const order: string[] = []
  const groups: Record<string, Row[]> = {}
  for (const row of items) {
    const key = row.dialog || `single-${row.id}`
    if (!groups[key]) {
      groups[key] = []
      order.push(key)
    }
    groups[key].push(row)
  }
  return order.flatMap((key) => [...groups[key]].sort((a, b) => (a.step || 0) - (b.step || 0)))
}

export function HistoryPage() {
  const [items, setItems] = useState<Row[]>([])
  const [opened, setOpened] = useState<Opened | null>(null)
  useEffect(() => {
    api.get<{ items: Row[] }>('history').then((d) => setItems(grouped(d.items)))
  }, [])
  return (
    <div className="page">
      <h1>История запросов</h1>
      <p className="lead">Все вопросы — с экрана «Задать вопрос» и из обращений («Передано специалисту», «＋ Новое обращение»). Двойной щелчок — открыть ответ.</p>
      <div className="table-wrap">
        <table className="list">
          <thead>
            <tr>
              <th>Время</th>
              <th>Вопрос</th>
              <th>Откуда ответ</th>
              <th>Ответ</th>
            </tr>
          </thead>
          <tbody>
            {items.map((h) => (
              <tr key={h.id} className={h.step ? 'follow-up' : ''} onDoubleClick={() => api.get<Opened>(`history/${h.id}`).then(setOpened)}>
                <td className="nowrap">{h.time}</td>
                <td>
                  {h.step ? <span className="muted">↳ уточнение: </span> : null}
                  {h.body.slice(0, 120)}
                  {h.understood_as && <div className="hint">понят как: {h.understood_as.slice(0, 140)}</div>}
                </td>
                <td className="nowrap">{SOURCE_NAMES[h.answer_source || ''] || h.answer_source || '—'}</td>
                <td>{h.answer.replace(/\n/g, ' ').slice(0, 160)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!items.length && <p className="empty">Пока нет вопросов. Задайте первый на экране «Задать вопрос».</p>}
      </div>
      {opened && (
        <Modal title={opened.body.slice(0, 80)} onClose={() => setOpened(null)} wide>
          {(opened.chain?.length || 0) > 1 && (
            <ol className="chain">
              {opened.chain!.map((c) => (
                <li key={c.id}>
                  {c.step ? 'Уточнение: ' : 'Вопрос: '}
                  {c.body}
                  {c.understood_as && <span className="hint"> — понят как: {c.understood_as}</span>}
                </li>
              ))}
            </ol>
          )}
          {opened.understood_as && <p className="hint">Система поняла вопрос так: «{opened.understood_as}»</p>}
          {opened.result ? (
            <>
              <Badge level={opened.level} text={opened.label} />
              <div className="answer-box">
                <ConfidenceChip confidence={opened.confidence} />
                <AnswerBody result={opened.result} />
              </div>
            </>
          ) : (
            <p className="muted">Ответ этого вопроса сохранён только кратко (перенесён из прежней версии).</p>
          )}
        </Modal>
      )}
    </div>
  )
}
