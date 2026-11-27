import { useState } from 'react'
import type { AgentResult, Doc } from '../types'
import { DocModal, docLabel } from './DocView'

export const LEVEL_COLORS: Record<string, [string, string]> = {
  high: ['#dcfce7', '#166534'], // правило или граф версий
  checked: ['#dbeafe', '#1e40af'], // модель прошла проверки
  document: ['#fef9c3', '#854d0e'], // показан текст документа
  escalation: ['#ffedd5', '#9a3412'], // передано специалисту
  blocked: ['#fee2e2', '#991b1b'], // фильтр безопасности
}

export function Badge({ level, text }: { level: string; text: string }) {
  const [bg, fg] = LEVEL_COLORS[level] || LEVEL_COLORS.document
  return (
    <div className="badge" style={{ background: bg, color: fg }}>
      ● {text}
    </div>
  )
}

function DocList({ title, docs, note, onOpen }: { title: string; docs: Doc[]; note?: (d: Doc) => string; onOpen: (d: Doc) => void }) {
  if (!docs.length) return null
  return (
    <>
      <h4>{title}</h4>
      <ul className="doc-list">
        {docs.map((doc) => (
          <li key={doc.id + (doc.points || []).join()}>
            <button className="link" onClick={() => onOpen(doc)}>
              {docLabel(doc)}
            </button>
            {!!doc.departments?.length && <span className="dept-tag">для: {doc.departments.join(', ')}</span>}
            {note && <span className="muted"> — {note(doc)}</span>}
          </li>
        ))}
      </ul>
    </>
  )
}

/** Текст ответа и источники: основание, другие найденные, «Обратите внимание», «Не применяются». */
export function AnswerBody({
  result,
  showOthers = true,
  showConflicts = true,
  text,
}: {
  result: AgentResult
  showOthers?: boolean
  showConflicts?: boolean
  text?: string
}) {
  const [opened, setOpened] = useState<Doc | null>(null)
  const basis = result.basis_docs?.length ? result.basis_docs : (result.active_docs || []).slice(0, 1)
  const basisIds = new Set(basis.map((d) => d.id))
  const others = (result.active_docs || []).filter((d) => !basisIds.has(d.id))
  const inactive = result.inactive_docs || []
  return (
    <div className="answer-text">
      <p className="answer-main">{text ?? result.answer}</p>
      {result.department && !!basis.length && (
        <p className="hint">
          {result.department.includes(', ')
            ? `Ответ подобран для подразделений «${result.department}»: по документам для всех и для них.`
            : `Ответ подобран для подразделения «${result.department}»: по документам для всех и для него.`}
        </p>
      )}
      <DocList title="Основание:" docs={basis} onOpen={setOpened} />
      {showOthers && <DocList title="Другие найденные источники:" docs={others} onOpen={setOpened} />}
      {showConflicts && !!result.conflicts?.length && (
        <>
          <h4>Обратите внимание:</h4>
          <ul className="doc-list warn">
            {result.conflicts.map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ul>
        </>
      )}
      <DocList
        title="Не применяются (устарели или ещё не действуют):"
        docs={inactive}
        note={(d) => d.status_reason || d.status || 'устарел'}
        onOpen={setOpened}
      />
      {opened && <DocModal doc={opened} onClose={() => setOpened(null)} />}
    </div>
  )
}

/** Предупреждения над ответом оператора (как в прежней версии): противоречия, причина передачи, проверка фактов. */
export function warningsFor(result: AgentResult): string[] {
  const warnings: string[] = []
  const basis = result.basis_docs?.length ? result.basis_docs : (result.active_docs || []).slice(0, 1)
  if (result.inactive_docs?.length && !basis.length) warnings.push('По теме найдены только недействующие документы (см. «Не применяются»).')
  for (const c of result.conflicts || []) warnings.push(`ПРОТИВОРЕЧИЕ: ${c}`)
  if (result.escalation_reason && result.escalation_reason !== 'Ответ в базе не найден')
    warnings.push(`Передано специалисту: ${result.escalation_reason} (${result.relevance || 'см. «Подробнее»'}).`)
  if (result.fact_issues?.length)
    warnings.push(`ПРОВЕРКА ФАКТОВ: в ответе модели были данные, которых нет в указанном источнике (${result.fact_issues.join(', ')}). Показан текст самого документа.`)
  return warnings
}
