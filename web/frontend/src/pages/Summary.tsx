import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'

interface SummaryData {
  self_service: {
    total: number
    closed: number
    closed_share: number
    handed: number
    handed_share: number
    helped_share: number | null
    rated: number
    hours_saved: number
    avg_latency: number | null
    blocked: number
  }
  inbox: { total: number; sent: number; ready: number; escalated: number; overdue: number; open: number }
  baseline_minutes: number
  review: { time: string; employee: string; question: string; decision: string; helped: boolean | null; comment: string; request_id: string | null }[]
}

const PERIODS = ['Неделя', 'Месяц', 'Всё время']
const num = (x: number) => String(x).replace('.', ',')

function Tile({ value, caption, note, danger }: { value: string | number; caption: string; note?: string; danger?: boolean }) {
  return (
    <div className="tile">
      <div className={`tile-value ${danger ? 'danger' : ''}`}>{value}</div>
      <div className="tile-caption">{caption}</div>
      {note && <div className="tile-note">{note}</div>}
    </div>
  )
}

export function SummaryPage() {
  const [period, setPeriod] = useState('Месяц')
  const [data, setData] = useState<SummaryData | null>(null)
  const navigate = useNavigate()

  useEffect(() => {
    api.get<SummaryData>(`summary?period=${encodeURIComponent(period)}`).then(setData)
  }, [period])

  if (!data) return <div className="page">Загрузка…</div>
  const s = data.self_service
  const i = data.inbox
  return (
    <div className="page">
      <div className="row spread">
        <h1>Сводка</h1>
        <div className="quick">
          {PERIODS.map((p) => (
            <button key={p} className={p === period ? 'active' : ''} onClick={() => setPeriod(p)}>
              {p}
            </button>
          ))}
        </div>
      </div>
      <h3>Сотрудники спрашивают сами</h3>
      <div className="tiles">
        <Tile value={s.total} caption="Вопросов задано" note={s.blocked ? `отклонено защитой: ${s.blocked}` : ''} />
        <Tile value={s.closed} caption="Закрыто сразу, без специалиста" note={`${s.closed_share} % вопросов`} />
        <Tile value={s.handed} caption="Передано специалисту" note={`${s.handed_share} % вопросов`} />
        <Tile
          value={s.helped_share === null ? '—' : `${s.helped_share} %`}
          caption="Отметили «Помогло»"
          note={s.rated ? `из ${s.rated} отмеченных ответов` : 'отметок пока нет'}
        />
        <Tile value={`${num(s.hours_saved)} ч`} caption="Сэкономлено времени сотрудников" note={`по ${data.baseline_minutes} мин на вопрос`} />
        <Tile value={s.avg_latency === null ? '—' : `${num(s.avg_latency)} с`} caption="Среднее время ответа" note="вместо 30 мин – 2 ч" />
      </div>
      <h3>Обращения по почте (работа оператора)</h3>
      <div className="tiles">
        <Tile value={i.total} caption="Обращений" />
        <Tile value={i.sent} caption="Ответ отправлен" />
        <Tile value={i.open} caption="Ещё не закрыто" note={i.ready ? `ответ готов: ${i.ready}` : ''} />
        <Tile value={i.escalated} caption="Ждут специалиста" />
        <Tile value={i.overdue} caption="Просрочено" note={i.overdue ? 'срок ответа — 24 ч' : ''} danger={i.overdue > 0} />
      </div>
      <p className="lead">
        Цель проекта — вернуть сотрудникам 50–300 человеко-часов в месяц, которые уходят на поиск, и сократить долю вопросов без
        ответа (было до 40 %). Сейчас без ответа сразу остаются {s.handed_share} % вопросов — они переданы специалисту. Экономия —
        оценка: {data.baseline_minutes} мин ручного поиска на каждый вопрос, закрытый без специалиста.
      </p>
      <h3>Разобрать: ответы «Не помогло» и вопросы, переданные специалисту</h3>
      <div className="table-wrap short">
        <table className="list">
          <thead>
            <tr>
              <th>Когда</th>
              <th>Кто</th>
              <th>Вопрос</th>
              <th>Что произошло</th>
            </tr>
          </thead>
          <tbody>
            {data.review.map((r, n) => (
              <tr key={n} onDoubleClick={() => r.request_id && navigate(`/answer?request=${r.request_id}`)}>
                <td className="nowrap">{r.time}</td>
                <td>{(r.employee || '—').split('<')[0].trim()}</td>
                <td>{r.question.slice(0, 150)}</td>
                <td>
                  {r.helped === false ? `Не помогло${r.comment ? `: ${r.comment}` : ''}` : r.decision[0].toUpperCase() + r.decision.slice(1)}
                  {r.request_id ? ` → ${r.request_id}` : ''}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!data.review.length && <p className="empty">Пока разбирать нечего: все вопросы закрыты без специалиста.</p>}
      </div>
    </div>
  )
}
