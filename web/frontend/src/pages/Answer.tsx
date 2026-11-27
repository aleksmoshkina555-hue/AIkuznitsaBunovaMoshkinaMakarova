import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useLocation, useSearchParams } from 'react-router-dom'
import { api, ApiError } from '../api'
import { useApp } from '../app/context'
import { AnswerBody, Badge, warningsFor } from '../components/Answer'
import { ConfidenceChip } from '../components/ConfidenceChip'
import { DepartmentsSelect } from '../components/DepartmentsSelect'
import { Modal } from '../components/Modal'
import { SourceRow } from '../components/SourceRow'
import { useTimer } from '../components/useTimer'
import type { AgentResult, Described, RequestItem } from '../types'
import { EXAMPLES } from './Ask'

type Answered = Described & { result: AgentResult }

const VERDICTS: [string, string, string][] = [
  ['верно', 'Верно', 'good'],
  ['неверно', 'Неверно', 'bad'],
  ['устарело', 'Устарело', 'warn'],
]

function senderName(sender: string): string {
  return sender.split('<')[0].trim().replace(/^"|"$/g, '') || sender
}

/** Обращение («Передано специалисту» → открыть, или «＋ Новое обращение»): от кого вопрос, ответ, черновик письма
 *  и кому ответить. Бывшая «Главная». */
export function AnswerPage() {
  const { meta, sources, user } = useApp()
  const [params, setParams] = useSearchParams()
  const location = useLocation()
  const [request, setRequest] = useState<RequestItem | null>(null)
  const [sender, setSender] = useState('')
  const [recipient, setRecipient] = useState('') // «Кому ответить»: по умолчанию — тот, от кого вопрос
  const [people, setPeople] = useState<string[]>([]) // подсказки «ФИО <e-mail>» из справочника «Сотрудники»
  // Подразделения для поиска: по умолчанию — подразделение того, кто вошёл; можно изменить или добавить
  const ownDepartments = useMemo(() => (user?.department ? [user.department] : []), [user?.department])
  const [departments, setDepartments] = useState<string[]>(ownDepartments)
  const [subject, setSubject] = useState('')
  const [body, setBody] = useState('')
  const [answered, setAnswered] = useState<Answered | null>(null)
  const [saved, setSaved] = useState(false)
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState('Готов к работе')
  const [error, setError] = useState('')
  const [verdict, setVerdict] = useState('')
  const [amount, setAmount] = useState('')
  const [trace, setTrace] = useState(false)
  const [knowledge, setKnowledge] = useState(false) // окно «Добавить ответ в базу знаний»
  const [knowledgeNote, setKnowledgeNote] = useState('')
  const elapsed = useTimer(busy)
  const fileInput = useRef<HTMLInputElement>(null)
  const requestId = params.get('request')

  useEffect(() => {
    api
      .get<{ departments: { name: string; employees: { fio: string; email: string }[] }[] }>('employees')
      .then((d) => setPeople(d.departments.flatMap((dep) => dep.employees.map((e) => (e.email ? `${e.fio} <${e.email}>` : e.fio)))))
      .catch(() => setPeople([]))
  }, [])

  /** «От кого» меняется — «Кому ответить» следует за ним, пока его не поправили вручную. */
  const changeSender = (value: string) => {
    setRecipient((current) => (current === '' || current === sender ? value : current))
    setSender(value)
  }

  const reset = useCallback(() => {
    setRequest(null)
    setSender('')
    setRecipient('')
    // выбранные подразделения не сбрасываются: оператор выбрал их для своей работы (по умолчанию — своё)
    setSubject('')
    setBody('')
    setAnswered(null)
    setDraft('')
    setVerdict('')
    setError('')
    setStatus('Готов к работе')
  }, [])

  const search = useCallback(
    async (text: string, id: string | null = requestId, forDepartments: string[] = departments) => {
      if (!text.trim()) return setError('Напишите вопрос или вставьте письмо в поле «Текст вопроса / письма»')
      if (!sources.length) return setError('Отметьте хотя бы один источник в строке «Искать в»')
      setBusy(true)
      setError('')
      setVerdict('')
      setStatus('Ищу ответ…')
      try {
        const data = await api.post<Answered & { request: RequestItem }>('answer', {
          body: text, sender, subject, sources, department: forDepartments, request_id: id,
        })
        setRequest(data.request)
        setRecipient((current) => current || data.request.sender)
        setAnswered(data)
        setSaved(false)
        setDraft(data.result.email_draft || '')
        setStatus(data.result.blocked ? 'Запрос отклонён фильтром безопасности' : 'Готово')
        if (!id) setParams({ request: data.request.id }, { replace: true })
      } catch (e) {
        setError((e as Error).message)
        setStatus('Ошибка')
      } finally {
        setBusy(false)
      }
    },
    [requestId, sender, subject, sources, departments, setParams],
  )

  // Открыть обращение из «Журнала вопросов»: сохранённый ответ — сразу, без нового поиска
  useEffect(() => {
    if (!requestId) {
      reset()
      return
    }
    let cancelled = false
    api.patch<RequestItem & Partial<Described>>(`requests/${requestId}`, { read: true }).then((item) => {
      if (cancelled) return
      setRequest(item)
      setSender(item.sender)
      setRecipient(item.sender)
      setSubject(item.subject)
      setBody(item.body || '')
      if (item.result) {
        setAnswered({ result: item.result, level: item.level!, label: item.label!, contacts: item.contacts || '', escalation_contacts: item.escalation_contacts || '', confidence: item.confidence })
        setDraft(item.result.email_draft || '')
        setSaved(true)
        setStatus('Готово')
      } else {
        setAnswered(null)
        setDraft('')
        if (!item.closed) search(item.body || '', item.id)
      }
    })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requestId, location.state])

  const pasteLetter = async () => {
    let text = ''
    try {
      text = await navigator.clipboard.readText()
    } catch {
      text = window.prompt('Вставьте письмо (вместе со строками «От:» и «Тема:»)') || ''
    }
    if (!text) return
    const letter = await api.post<{ sender: string; subject: string; body: string }>('parse-letter', { text })
    setParams({}, { replace: true })
    reset()
    setSender(letter.sender)
    setRecipient(letter.sender)
    setSubject(letter.subject)
    setBody(letter.body)
    setStatus('Письмо вставлено — отправитель и тема в заголовке')
  }

  const openEml = async (file: File) => {
    const form = new FormData()
    form.append('file', file)
    const letter = await api.post<{ sender: string; subject: string; body: string }>('parse-letter', form)
    setParams({}, { replace: true })
    reset()
    setSender(letter.sender)
    setRecipient(letter.sender)
    setSubject(letter.subject)
    setBody(letter.body)
    setStatus('Письмо загружено — отправитель и тема в заголовке')
  }

  const send = async (asked?: string) => {
    if (!request) return
    const to = (asked || recipient).trim()
    if (!to) return setError('Укажите, кому ответить: e-mail в поле «Кому ответить»')
    const action = meta?.smtp ? 'Отправить' : 'Сохранить письмо с'
    if (!asked && !window.confirm(`${action} ответ${meta?.smtp ? '' : 'ом'} для ${to}?`)) return
    try {
      const data = await api.post<{ mode: string; destination: string; request: RequestItem }>(`requests/${request.id}/send`, { draft, recipient: to })
      setRequest(data.request)
      window.alert(data.mode === 'smtp' ? `Ответ отправлен через ${data.destination}.` : `Почтовый сервер не настроен — письмо сохранено в файл:\n${data.destination}`)
    } catch (e) {
      if (e instanceof ApiError && e.status === 422) {
        const address = window.prompt('E-mail адресата, например: ivanov@company.ru')
        if (address) send(address)
      } else window.alert((e as Error).message)
    }
  }

  // «Передать другому»: другой специалист; тема (необязательно) — вопрос станет примером темы
  const [reassign, setReassign] = useState<{ to: string; topic: string } | null>(null)
  const doReassign = async () => {
    if (!request || !reassign) return
    try {
      const item = await api.patch<RequestItem & Partial<Described>>(`requests/${request.id}`, {
        assignee: reassign.to.trim(),
        topic: reassign.topic,
      })
      setRequest(item)
      setReassign(null)
      setStatus(`Вопрос передан: ${item.result?.assignee ?? reassign.to}`)
    } catch (e) {
      setError((e as Error).message)
    }
  }

  // Специалист поправил черновик и сохранил, не отправляя: при повторном открытии обращения правка на месте
  const [draftNote, setDraftNote] = useState('')
  const saveDraft = async () => {
    if (!request) return
    try {
      const item = await api.patch<RequestItem & Partial<Described>>(`requests/${request.id}`, { draft })
      setRequest(item)
      setDraftNote(`Черновик сохранён ${item.result?.draft_saved_at ?? ''}`.trim())
    } catch (e) {
      setDraftNote((e as Error).message)
    }
  }

  const rate = async (value: string) => {
    if (!answered) return
    const comment = value === 'верно' ? '' : window.prompt('Коротко: что неверно или устарело (можно оставить пустым)') ?? ''
    await api.post('feedback', { verdict: value, comment, sender, subject, body, result: answered.result })
    setVerdict(value)
  }

  const result = answered?.result
  const warnings = result && !result.blocked ? warningsFor(result) : []
  const title = request?.subject || subject || (request ? request.preview : 'Новый вопрос')
  const minutesSaved =
    result?.found && result.latency !== undefined ? Math.max(0, Math.round(30 - result.latency / 60)) : null

  return (
    <div className="page">
      <div className="card-head">
        {request &&
          (request.status === 'Передано специалисту' ? (
            <Link to="/inbox/escalated" className="back">
              ← Передано специалисту
            </Link>
          ) : (
            <Link to="/inbox" className="back">
              ← Журнал вопросов
            </Link>
          ))}
        <h1>{title}</h1>
        <span className={`meta-line ${request?.overdue ? 'danger' : ''}`}>
          {[
            sender || request?.sender ? `от ${senderName(sender || request?.sender || '')}` : '',
            request ? `${request.status}${request.overdue ? ' · просрочено' : ''}` : '',
            request?.id,
            request ? `получено ${request.created}` : '',
            request ? `срок ${request.due}` : '',
          ]
            .filter(Boolean)
            .join(' · ')}
        </span>
      </div>

      {request?.result?.assignee && (
        <div className="assignee-row">
          <span>
            <b>Ответственный:</b> {request.result.assignee}
            {request.result.assignee_reason && <span className="hint"> — {request.result.assignee_reason}</span>}
          </span>
          {request.status !== 'Отправлено' && (
            <button className="small" onClick={() => setReassign({ to: '', topic: request.result?.topic || '' })}>
              Передать другому
            </button>
          )}
        </div>
      )}
      {reassign && request && (
        <Modal title="Передать вопрос другому специалисту" onClose={() => setReassign(null)}>
          <div className="form-grid">
          <label>
            Кому передать
            <input
              list="people"
              autoFocus
              value={reassign.to}
              onChange={(e) => setReassign({ ...reassign, to: e.target.value })}
              placeholder="ФИО или e-mail из справочника «Сотрудники»"
            />
          </label>
          <label>
            Тема вопроса (необязательно)
            <select value={reassign.topic} onChange={(e) => setReassign({ ...reassign, topic: e.target.value })}>
              <option value="">— не указывать —</option>
              {(meta?.processes || []).filter((p) => p !== 'Прочие вопросы').map((p) => (
                <option key={p}>{p}</option>
              ))}
            </select>
          </label>
          </div>
          <p className="hint">
            Если указать тему, вопрос станет её примером: похожие вопросы система дальше будет сразу передавать эксперту этой
            темы.
          </p>
          <div className="dialog-buttons">
            <button onClick={() => setReassign(null)}>Отмена</button>
            <button className="primary" disabled={!reassign.to.trim()} onClick={doReassign}>
              Передать
            </button>
          </div>
        </Modal>
      )}

      <section className="card">
        <label className="inline who-row">
          <b>От кого вопрос:</b>
          <input
            list="people"
            value={sender}
            onChange={(e) => changeSender(e.target.value)}
            placeholder="ФИО или e-mail — начните вводить, система подскажет из справочника «Сотрудники»"
          />
        </label>
        <datalist id="people">
          {people.map((p) => (
            <option key={p} value={p} />
          ))}
        </datalist>
        <SourceRow />
        <DepartmentsSelect value={departments} onChange={setDepartments} />
        <b>Текст вопроса / письма:</b>
        <textarea
          rows={3}
          value={body}
          onChange={(e) => setBody(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && e.ctrlKey) {
              e.preventDefault()
              search(body)
            }
          }}
        />
        <div className="row spread">
          <span className="row">
            <button className="primary big" disabled={busy} onClick={() => search(body)}>
              {busy ? 'Ищу ответ…' : 'Найти ответ и подготовить письмо'}
            </button>
            <span className="status-text">
              {status} {elapsed}
            </span>
            {error && <span className="error-text">{error}</span>}
          </span>
          <span className="row">
            <button onClick={pasteLetter}>Вставить письмо</button>
            <button onClick={() => fileInput.current?.click()}>Открыть .eml</button>
            <button
              onClick={() => {
                setParams({}, { replace: true })
                reset()
              }}
            >
              Очистить
            </button>
            <input
              ref={fileInput}
              type="file"
              accept=".eml"
              hidden
              onChange={(e) => {
                if (e.target.files?.[0]) openEml(e.target.files[0])
                e.target.value = ''
              }}
            />
          </span>
        </div>
        <div className="row spread examples">
          <span className="row">
            <span className="hint">Примеры:</span>
            {EXAMPLES.map(([label, text]) => (
              <button
                key={label}
                className="chip"
                onClick={() => {
                  setParams({}, { replace: true })
                  reset()
                  setBody(text)
                  search(text, null)
                }}
              >
                {label}
              </button>
            ))}
          </span>
          <span className="hint">Ctrl+Enter — найти</span>
        </div>
      </section>

      {answered && result && (
        <>
          <Badge
            level={answered.level}
            text={answered.label + (saved ? '  ·  сохранённый ответ по обращению' : '')}
          />
          {result.blocked && (
            <div className="banner danger">
              ФИЛЬТР БЕЗОПАСНОСТИ: {result.reason}. Запрос не передан модели, документы не раскрыты.
            </div>
          )}
          {!!warnings.length && <div className="banner warn">{warnings.map((w) => <div key={w}>{w}</div>)}</div>}
          {!!result.other_departments?.length && (
            <div className="card row">
              <b>Ответ есть в документах других подразделений. Искать также в:</b>
              {result.other_departments
                .filter((name) => !departments.includes(name))
                .map((name) => (
                  <button
                    key={name}
                    className="chip"
                    disabled={busy}
                    onClick={() => {
                      // подразделение добавляется к выбранным, вопрос ищется заново (то же обращение)
                      const next = [...departments, name]
                      setDepartments(next)
                      search(body, request?.id ?? requestId, next)
                    }}
                  >
                    ＋ {name}
                  </button>
                ))}
            </div>
          )}
          {result.needs_clarification && result.clarify_by === 'department' && (
            <div className="card row">
              <b>Ответ зависит от подразделения:</b>
              {(result.clarify_options ?? []).map((name) => (
                <button
                  key={name}
                  className="chip"
                  onClick={() => {
                    // подразделение, названное в вопросе, важнее выбранных в «Подразделение»
                    const text = `${body.trim().replace(/[?.\s]+$/, '')} в ${name}?`
                    setBody(text)
                    search(text)
                  }}
                >
                  {name}
                </button>
              ))}
            </div>
          )}
          {result.needs_clarification && result.clarify_by !== 'department' && (
            <form
              className="card row"
              onSubmit={(e) => {
                e.preventDefault()
                const text = `${body.trim()} Сумма договора: ${amount.trim()} руб.`
                setBody(text)
                setAmount('')
                search(text)
              }}
            >
              <b>Подписант зависит от суммы. Сумма договора, руб.:</b>
              <input value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="300 000, 750 тыс., 1,2 млн" />
              <button type="submit">Пересчитать</button>
            </form>
          )}
          <div className="two-columns">
            <section className="card">
              <h3>Найденный ответ и источники</h3>
              <div className="answer-box">
                <ConfidenceChip confidence={answered.confidence} />
                {result.blocked ? (
                  <p>Запрос отклонён фильтром безопасности. Если это обычное письмо сотрудника — ответьте вручную.</p>
                ) : (
                  <AnswerBody result={result} showConflicts={false} />
                )}
              </div>
              <div className="row spread">
                <span className="row">
                  <span className="hint">Ответ:</span>
                  {VERDICTS.map(([value, label, cls]) => (
                    <button key={value} className={cls} disabled={!!verdict || result.blocked} onClick={() => rate(value)}>
                      {label}
                    </button>
                  ))}
                  {verdict && <span className="hint">Спасибо: отметка «{verdict}» сохранена</span>}
                </span>
                <span className="hint">Щёлкните по документу, чтобы открыть пункт</span>
              </div>
            </section>
            <section className="card">
              <h3>Черновик ответа</h3>
              <p className="responsible">
                {result.blocked
                  ? 'Ответственный: Служба ИБ'
                  : `${result.responsible_label || 'Ответственный'}: ${result.responsible || '—'}`}
                {answered.contacts && !result.blocked && <><br />{answered.contacts}</>}
              </p>
              <label className="inline who-row">
                <b>Кому ответить:</b>
                <input
                  list="people"
                  value={recipient}
                  onChange={(e) => setRecipient(e.target.value)}
                  disabled={request?.status === 'Отправлено'}
                  placeholder="e-mail, например ivanov@company.ru"
                />
              </label>
              <textarea
                className="draft"
                value={draft}
                onChange={(e) => {
                  setDraft(e.target.value)
                  setDraftNote('')
                }}
                rows={12}
              />
              {request && request.status !== 'Отправлено' && !result.blocked && (
                <div className="row">
                  <button
                    onClick={saveDraft}
                    disabled={draft === (request.result?.email_draft ?? '')}
                    title="Сохранить правку черновика, не отправляя: её увидят при следующем открытии обращения"
                  >
                    💾 Сохранить черновик
                  </button>
                  {draftNote ? (
                    <span className="ok-text">{draftNote}</span>
                  ) : draft !== (request.result?.email_draft ?? '') ? (
                    <span className="hint">Есть несохранённые правки</span>
                  ) : request.result?.draft_saved_by ? (
                    <span className="hint">
                      Черновик правил(а) {request.result.draft_saved_by}, {request.result.draft_saved_at}
                    </span>
                  ) : null}
                </div>
              )}
              <button
                className="send"
                disabled={!request || request.status === 'Отправлено' || !!result.blocked}
                onClick={() => send()}
              >
                {request?.status === 'Отправлено'
                  ? 'Ответ отправлен'
                  : meta?.smtp
                    ? 'Подтвердить и отправить по почте'
                    : 'Подтвердить и сохранить письмо'}
              </button>
              {meta?.is_admin && request && !result.blocked && (
                <div className="to-knowledge">
                  <button onClick={() => setKnowledge(true)} title="Ответ станет документом базы знаний — на такой же вопрос система ответит сама">
                    📚 Добавить ответ в базу знаний
                  </button>
                  {knowledgeNote && <span className="ok-text">{knowledgeNote}</span>}
                  {!knowledgeNote && result.answer_source === 'escalation' && (
                    <span className="hint">В документах ответа не было — добавьте ответ службы качества в базу, и в следующий раз система ответит сама.</span>
                  )}
                </div>
              )}
            </section>
          </div>
          <section className="card metrics">
            <div className="row spread">
              <b>
                Метрики: ответ — {result.answer_source} | время {result.latency ?? 0} сек
                {minutesSaved !== null && ` | оценка экономии ~${minutesSaved} мин (ручной поиск от 30 мин)`}
              </b>
              <button onClick={() => setTrace(!trace)}>{trace ? 'Скрыть ▾' : 'Подробнее ▸'}</button>
            </div>
            {trace && <p className="muted">Трассировка: {(result.logs || []).join('  →  ')}</p>}
          </section>
        </>
      )}
      {knowledge && request && (
        <KnowledgeDialog
          requestId={request.id}
          question={request.body || body}
          draft={draft}
          defaultDepartments={[] /* ответ службы качества обычно касается всех; при необходимости — выбрать */}
          onClose={() => setKnowledge(false)}
          onSaved={(note) => {
            setKnowledge(false)
            setKnowledgeNote(note)
          }}
        />
      )}
    </div>
  )
}

/** Текст ответа из черновика письма: без приветствия, подписи и служебных строк. */
function answerFromDraft(draft: string): string {
  const lines = draft.split('\n')
  const end = lines.findIndex((l) => /^\s*(С уважением|--)/i.test(l))
  return (end >= 0 ? lines.slice(0, end) : lines)
    .filter((l, i) => !(i === 0 && /^\s*(Здравствуйте|Добрый|Уважаем)/i.test(l)))
    .filter((l) => !/Запрос зарегистрирован|не найден в действующих|требует проверки специалистом/i.test(l))
    .join('\n')
    .trim()
}

/** «Добавить ответ в базу знаний»: вопрос, ответ, к какому документу относится, для кого. */
function KnowledgeDialog({ requestId, question, draft, defaultDepartments, onClose, onSaved }: {
  requestId: string
  question: string
  draft: string
  defaultDepartments: string[]
  onClose: () => void
  onSaved: (note: string) => void
}) {
  const [q, setQ] = useState(question.trim())
  const [a, setA] = useState(answerFromDraft(draft))
  const [related, setRelated] = useState('')
  const [docs, setDocs] = useState<{ id: string; title: string; type: string; status: string }[]>([])
  const [departments, setDepartments] = useState<string[]>(defaultDepartments)
  const [error, setError] = useState('')
  useEffect(() => {
    api.get<{ items: { id: string; title: string; type: string; status: string }[] }>('documents').then((d) =>
      setDocs(d.items.filter((x) => x.status === 'Действует' && x.type !== 'Ответ службы качества')),
    )
  }, [])
  const save = async () => {
    setError('')
    try {
      await api.post(`requests/${encodeURIComponent(requestId)}/to-knowledge`, { question: q, answer: a, related, departments })
      onSaved('Ответ добавлен в базу знаний — на такой же вопрос система ответит сама')
    } catch (e) {
      setError((e as Error).message)
    }
  }
  return (
    <Modal title="Добавить ответ в базу знаний" onClose={onClose} wide>
      <p className="hint">
        Ответ станет документом базы знаний «Ответ службы качества». Когда сотрудник спросит то же самое (даже другими словами),
        система ответит этим ответом сама и сошлётся на него.
      </p>
      <div className="card-grid">
        <label className="wide">
          Вопрос сотрудника
          <textarea rows={2} value={q} onChange={(e) => setQ(e.target.value)} />
        </label>
        <label className="wide">
          Ответ службы качества
          <textarea rows={5} value={a} onChange={(e) => setA(e.target.value)} />
        </label>
        <label className="wide">
          Дополняет документ
          <select value={related} onChange={(e) => setRelated(e.target.value)}>
            <option value="">— не относится к конкретному документу —</option>
            {docs.map((d) => (
              <option key={d.id} value={d.id}>
                {d.type}: {d.title}
              </option>
            ))}
          </select>
        </label>
      </div>
      <DepartmentsSelect value={departments} onChange={setDepartments} />
      {error && <p className="error-text">{error}</p>}
      <div className="dialog-buttons">
        <button className="primary" disabled={!q.trim() || !a.trim()} onClick={save}>
          Добавить в базу знаний
        </button>
        <button onClick={onClose}>Отмена</button>
      </div>
    </Modal>
  )
}
