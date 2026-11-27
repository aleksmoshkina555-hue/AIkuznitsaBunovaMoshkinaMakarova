import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { useApp } from '../app/context'
import { AnswerBody, Badge } from '../components/Answer'
import { ConfidenceChip } from '../components/ConfidenceChip'
import { SourceRow } from '../components/SourceRow'
import { useTimer } from '../components/useTimer'
import type { AgentResult, Described } from '../types'

export const EXAMPLES: [string, string][] = [
  ['Срок заявки на расчёт', 'За сколько дней нужно инициировать заявку на расчёт?'],
  ['Кто назначает РП?', 'Кто назначает руководителя проекта?'],
  ['Срок сметы в ДБРЦТ', 'В какой срок РП создаёт смету в ДБРЦТ?'],
  ['Дисквалификация заявки', 'Когда сотрудник 1 линии закрывает заявку как «Дисквалифицировано»?'],
]

interface AskResponse extends Described {
  record_id: number
  decision: string
  result: AgentResult
  request_id: string | null
  email_ok: boolean
  dialog?: string
  step?: number
  chain_reset?: boolean
  understood_as?: string | null
  rewrite_method?: string
}

/** Предыдущий шаг цепочки уточнений — коротко, над текущим ответом. */
interface Step {
  asked: string
  understood_as?: string | null
  answer: string
}

const ANSWERED = ['ответ сразу', 'пункт документа']

/** Коротко об ответе предыдущего шага — для списка цепочки. */
function shortAnswer(data: AskResponse): string {
  if (data.result.withheld) return 'ответ не показан — у специалиста'
  if (data.decision === 'передано специалисту') return 'передано специалисту'
  const text = (data.result.answer || '').replace(/\s+/g, ' ')
  return text.length > 160 ? `${text.slice(0, 157)}…` : text
}

export function AskPage() {
  const { meta, sources } = useApp()
  const [question, setQuestion] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [data, setData] = useState<AskResponse | null>(null)
  const [amount, setAmount] = useState('')
  const [feedback, setFeedback] = useState('')
  const [rated, setRated] = useState(false)
  const [handedTo, setHandedTo] = useState<string | null>(null)
  const [asked, setAsked] = useState('') // как спросили в текущем шаге
  const [steps, setSteps] = useState<Step[]>([]) // предыдущие шаги цепочки уточнений
  const [followUp, setFollowUp] = useState('')
  const [chainNote, setChainNote] = useState('')
  const elapsed = useTimer(busy)
  const input = useRef<HTMLTextAreaElement>(null)
  const followInput = useRef<HTMLInputElement>(null)

  useEffect(() => input.current?.focus(), [])

  /** asFollowUp — уточнение к текущему ответу (сервер учтёт первый вопрос); иначе — новый вопрос. */
  const ask = async (text = question, asFollowUp = false) => {
    if (!text.trim()) return setError(asFollowUp ? 'Напишите уточнение' : 'Напишите вопрос')
    if (!sources.length) return setError('Отметьте хотя бы один источник в строке «Искать в»')
    setBusy(true)
    setError('')
    setFeedback('')
    setRated(false)
    setHandedTo(null)
    setChainNote('')
    try {
      // кто спрашивает и подразделение сервер берёт из учётной записи, под которой вошли
      const previous = asFollowUp ? data : null
      const response = await api.post<AskResponse>('ask', {
        question: text,
        sources,
        ...(previous ? { follow_up_of: previous.record_id } : {}),
      })
      if (previous && !response.chain_reset) {
        setSteps([...steps, { asked, understood_as: previous.understood_as, answer: shortAnswer(previous) }])
      } else {
        setSteps([])
        if (response.chain_reset) setChainNote('Прошло больше 30 минут или уточнений слишком много — вопрос задан как новый.')
      }
      setAsked(text)
      setData(response)
      setFollowUp('')
      window.setTimeout(() => followInput.current?.focus(), 50)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const startOver = () => {
    setData(null)
    setSteps([])
    setAsked('')
    setQuestion('')
    setFollowUp('')
    setChainNote('')
    input.current?.focus()
  }

  const helped = async (value: boolean) => {
    if (!data) return
    const comment = value ? '' : window.prompt('Коротко: чего не хватило в ответе (можно оставить пустым)') ?? ''
    await api.post(`ask/${data.record_id}/helped`, { helped: value, comment })
    setRated(true)
    setFeedback(value ? 'Спасибо! Отметка сохранена' : 'Отметка сохранена, ответ разберут. Нужен ответ человека — «Передать специалисту»')
  }

  const handoff = async () => {
    if (!data) return
    const comment = window.prompt('Что уточнить у специалиста? (можно оставить пустым)')
    if (comment === null) return
    const response = await api.post<{ request_id: string }>(`ask/${data.record_id}/handoff`, { comment })
    setHandedTo(response.request_id)
  }

  const clarify = () => {
    // Сумма — тоже уточнение: сервер подставит её в первый вопрос
    const value = amount.trim()
    setAmount('')
    ask(`Сумма договора: ${value}${/руб|₽|тыс|млн/i.test(value) ? '' : ' руб.'}`, true)
  }

  const sla = `в течение ${meta?.sla_hours ?? 24} ч`
  const emailHint =
    data && !data.email_ok
      ? ' В справочнике «Сотрудники» нет вашего e-mail — попросите администратора добавить его, иначе специалист не сможет ответить на почту.'
      : ''
  const banner = (() => {
    if (!data) return null
    const { decision, level, request_id } = data
    if (decision === 'ответ сразу' && data.result.answer_source === 'quality')
      return { level, text: 'Ответ службы качества из базы знаний — уже проверен специалистом' }
    if (decision === 'ответ сразу')
      return { level, text: level === 'high' ? 'Ответ по действующему документу — высокая надёжность' : 'Ответ сверен с документом — основание по ссылке ниже' }
    if (decision === 'пункт документа')
      return { level: 'document', text: 'Точный ответ не сформулирован — ниже текст подходящего пункта документа. Если не помогло — нажмите «Передать специалисту».' }
    if (decision === 'нужно уточнение')
      return data.result.clarify_by === 'department'
        ? { level: 'document', text: 'Ответ зависит от подразделения — выберите его ниже.' }
        : { level: 'document', text: 'Кто подписывает, зависит от суммы договора — укажите сумму ниже.' }
    if (decision === 'на проверку специалисту')
      return { level: 'checked', text: `Ответ подготовлен и передан специалисту на проверку (обращение ${request_id}). Ответ придёт на почту ${sla}.${emailHint}` }
    if (decision === 'передано специалисту')
      return {
        level: 'escalation',
        text: `${
          data.result.withheld
            ? 'Система не уверена в найденном ответе, поэтому ответ не показан.'
            : data.result.other_departments?.length
              ? `Для подразделения «${data.result.department}» документов по этому вопросу нет.`
              : 'В действующих документах ответа нет.'
        } Вопрос передан специалисту (обращение ${request_id}), ответ придёт на почту ${sla}.${emailHint}`,
      }
    return { level: 'blocked', text: 'Вопрос не принят: он похож на попытку обойти защиту. Сформулируйте рабочий вопрос иначе.' }
  })()

  const answered = data && ANSWERED.includes(data.decision)
  const canHandoff = data && [...ANSWERED, 'нужно уточнение'].includes(data.decision) && !handedTo

  return (
    <div className="page">
      <h1>Задать вопрос</h1>
      <p className="lead">
        Ответ приходит сразу и опирается только на действующие приказы, регламенты, письма и чаты. Если система не уверена в
        ответе, вопрос сам уйдёт специалисту.
      </p>
      <section className="card">
        {/* Кто спрашивает и подразделение не показываются и не вводятся: сервер берёт их из учётной записи */}
        <SourceRow />
        <b>Ваш вопрос:</b>
        <textarea
          ref={input}
          className="question"
          rows={3}
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && e.ctrlKey) {
              e.preventDefault()
              ask()
            }
          }}
        />
        <div className="row spread">
          <span className="row">
            <button className="primary big" disabled={busy} onClick={() => ask()}>
              {busy ? 'Ищу ответ…' : 'Спросить'}
            </button>
            {busy && <span className="status-text">Ищу в действующих документах… {elapsed}</span>}
            {!busy && data && (
              <span className="ok-text">Готово — ответ за {String(data.result.latency ?? 0).replace('.', ',')} с</span>
            )}
            {error && <span className="error-text">{error}</span>}
          </span>
          <span className="hint">Ctrl+Enter — спросить</span>
        </div>
        <div className="row examples">
          <span className="hint">Часто спрашивают:</span>
          {EXAMPLES.map(([label, text]) => (
            <button
              key={label}
              className="chip"
              onClick={() => {
                setQuestion(text)
                ask(text)
              }}
            >
              {label}
            </button>
          ))}
        </div>
      </section>

      {data && banner && (
        <section className="card result">
          {!!steps.length && (
            <ol className="chain" title="Предыдущие вопросы этой цепочки">
              {steps.map((s, i) => (
                <li key={i}>
                  <b>{i === 0 ? 'Вопрос' : 'Уточнение'}:</b> {s.asked}
                  {s.understood_as && <span className="hint"> (понят как: {s.understood_as})</span>}
                  <div className="muted">{s.answer}</div>
                </li>
              ))}
              <li>
                <b>Уточнение:</b> {asked}
              </li>
            </ol>
          )}
          {chainNote && <p className="hint">{chainNote}</p>}
          {data.understood_as && (
            <p className="understood">
              Понял вопрос так: <b>«{data.understood_as}»</b>{' '}
              <button
                className="link"
                title="Задать вопрос в такой формулировке заново и поправить, если нужно"
                onClick={() => {
                  setQuestion(data.understood_as || '')
                  input.current?.focus()
                }}
              >
                поправить
              </button>
            </p>
          )}
          <Badge level={banner.level} text={banner.text} />
          {data.decision === 'нужно уточнение' && data.result.clarify_by === 'department' && (
            <div className="row">
              <b>Подразделение:</b>
              {(data.result.clarify_options ?? []).map((name) => (
                // Уточнение в цепочке: «а в ДБРЦТ?» — сервер подставит подразделение в первый вопрос
                <button key={name} className="chip" disabled={busy} onClick={() => ask(`а в ${name}?`, true)}>
                  {name}
                </button>
              ))}
            </div>
          )}
          {data.decision === 'нужно уточнение' && data.result.clarify_by !== 'department' && (
            <form
              className="row"
              onSubmit={(e) => {
                e.preventDefault()
                clarify()
              }}
            >
              <b>Сумма договора, руб.:</b>
              <input value={amount} onChange={(e) => setAmount(e.target.value)} autoFocus placeholder="300 000, 750 тыс., 1,2 млн" />
              <button type="submit">Уточнить</button>
            </form>
          )}
          <div className="answer-box">
            <ConfidenceChip confidence={data.confidence} />
            {['ответ сразу', 'пункт документа', 'нужно уточнение'].includes(data.decision) ? (
              <AnswerBody result={data.result} showOthers={false} />
            ) : data.decision === 'на проверку специалисту' ? (
              <p>Специалист проверит подготовленный ответ и пришлёт его вам.</p>
            ) : data.decision === 'передано специалисту' ? (
              <AnswerBody
                result={data.result}
                showOthers={false}
                text={
                  data.result.withheld
                    ? `Уверенность в найденном ответе — ${data.result.withheld.score} из 100, это ниже допустимого. Чтобы не дать неверный ответ, он не показан: специалист проверит вопрос и ответит.`
                    : data.result.other_departments?.length
                      ? `Ответ есть в документе для подразделения ${data.result.other_departments.map((d) => `«${d}»`).join(', ')}, но для «${data.result.department}» он не действует — специалист уточнит, как быть вашему подразделению.`
                      : 'В действующих приказах, регламентах и переписке ответа на этот вопрос нет.'
                }
              />
            ) : (
              <p>Если это обычный рабочий вопрос — переформулируйте его или напишите специалисту: {meta?.escalation_contact}.</p>
            )}
          </div>
          <p className="responsible">
            {['ответ сразу', 'пункт документа', 'нужно уточнение'].includes(data.decision)
              ? `${data.result.responsible_label || 'Ответственный'}: ${data.result.responsible || '—'}${data.contacts ? `  ·  ${data.contacts}` : ''}`
              : ['передано специалисту', 'на проверку специалисту'].includes(data.decision)
                ? `Передано: ${meta?.escalation_contact}${data.escalation_contacts ? `  ·  ${data.escalation_contacts}` : ''}`
                : ''}
          </p>
          <div className="row spread">
            <span className="row">
              <button className="good" disabled={!answered || rated} onClick={() => helped(true)}>Помогло</button>
              <button className="bad" disabled={!answered || rated} onClick={() => helped(false)}>Не помогло</button>
              <button className="warn" disabled={!canHandoff} onClick={handoff}>Передать специалисту</button>
              <span className="hint">
                {handedTo ? `Передано специалисту: обращение ${handedTo}, ответ придёт на почту ${sla}.${emailHint}` : feedback}
              </span>
            </span>
            <span className="hint">Щёлкните по документу, чтобы открыть пункт</span>
          </div>
          {data.decision !== 'отклонено' && (
            <form
              className="follow-up-row"
              onSubmit={(e) => {
                e.preventDefault()
                ask(followUp, true)
              }}
            >
              <b>Уточнить:</b>
              <input
                ref={followInput}
                value={followUp}
                onChange={(e) => setFollowUp(e.target.value)}
                placeholder="например: «а на 800 тыс.?», «а кто его согласует?» — первый вопрос учтётся"
                disabled={busy}
              />
              <button type="submit" disabled={busy || !followUp.trim()}>
                Уточнить
              </button>
              <button type="button" onClick={startOver} disabled={busy} title="Начать новый вопрос — без учёта предыдущих">
                Новый вопрос
              </button>
            </form>
          )}
        </section>
      )}
    </div>
  )
}
