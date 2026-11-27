import { useEffect, useState } from 'react'
import type { Confidence } from '../types'

/** Небольшая плашка у ответа: зелёная «Уверен», жёлтая «Сомневается», красная «Нужно проверить».
 *  Щелчок — из чего сложилась оценка (критерии меняет администратор). */
export function ConfidenceChip({ confidence }: { confidence?: Confidence }) {
  const [open, setOpen] = useState(false)
  useEffect(() => setOpen(false), [confidence])  // новый ответ — пояснение закрыто
  if (!confidence) return null
  return (
    <span className="confidence-wrap">
      <button
        type="button"
        className={`confidence confidence-${confidence.color}`}
        title={confidence.color === 'none' ? 'Ответа нет. Щёлкните — почему.' : `Уверенность ${confidence.score} из 100. Щёлкните — почему.`}
        onClick={() => setOpen(!open)}
      >
        <span className="confidence-dot" />
        {confidence.label}
        {confidence.color !== 'none' && <span className="confidence-score">{confidence.score}</span>}
      </button>
      {open && (
        <span className="confidence-pop" role="tooltip">
          <b>{confidence.color === 'none' ? 'Ответа нет' : `Уверенность ${confidence.score} из 100`}</b>
          <ul>
            {confidence.reasons.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
          <span className="muted">Пороги и поправки меняет администратор: «Администрирование» → «Уверенность ответа».</span>
        </span>
      )}
    </span>
  )
}
