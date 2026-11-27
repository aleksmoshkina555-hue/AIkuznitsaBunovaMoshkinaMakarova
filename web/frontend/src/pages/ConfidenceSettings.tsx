import { useEffect, useState, type ReactNode } from 'react'
import { api } from '../api'

interface Param {
  key: string
  label: string
  help: string
  recommended: number
  min: number
  max: number
  step: number
  value: number
}
interface Data {
  items: Param[]
  updated: string | null
  updated_by: string | null
}

/** Группы критериев и помощь к каждой (открывается по «?»). */
const GROUPS: { title: string; keys: string[]; help: ReactNode }[] = [
  {
    title: 'Пороги цвета и скрытия ответа',
    keys: ['green_threshold', 'yellow_threshold', 'hide_threshold'],
    help: (
      <>
        <p>Счёт уверенности (0–100) сравнивается с тремя порогами:</p>
        <ul>
          <li><b>«Уверен»</b> — счёт не ниже этого порога: зелёная плашка, ответ можно использовать сразу.</li>
          <li><b>«Сомневается»</b> — не ниже этого порога: жёлтая плашка, сотрудник читает основание.</li>
          <li>Выше <b>порога скрытия</b> — красная «Нужно проверить».</li>
          <li><b>Не выше порога скрытия — ответ не показывается</b>: вопрос сам уходит специалисту, текст ответа нигде не сохраняется.</li>
        </ul>
        <p>Пороги идут по возрастанию: скрытие &lt; «Сомневается» &lt; «Уверен». Рекомендуется 90 / 75 / 65.</p>
        <p>
          <b>Когда менять.</b> Много зелёных ответов приходится исправлять — поднимите «Уверен» до 95. Специалисты перегружены
          вопросами — опустите порог скрытия до 60 (ниже не советуем: на проверке неверный ответ получил 60). Нужно
          осторожнее — поднимите порог скрытия до 70.
        </p>
      </>
    ),
  },
  {
    title: 'Базовый счёт по способу ответа',
    keys: ['base_process_map', 'base_actuality', 'base_quality', 'base_model', 'base_sources'],
    help: (
      <>
        <p>С какого счёта начинается оценка — в зависимости от того, как получен ответ:</p>
        <ul>
          <li><b>Готовый ответ по теме (95)</b> — «кто подписывает / кто отвечает» по правилу действующего приказа, без модели. Ошибиться почти негде.</li>
          <li><b>Граф версий (95)</b> — «действует ли документ», «когда вступает в силу» — по отменам и датам, без модели.</li>
          <li><b>Ответ службы качества (90)</b> — на этот же вопрос уже отвечала служба качества; её ответ взят из базы знаний без модели.</li>
          <li><b>Ответ модели (70)</b> — модель пересказала найденный пункт; ответ прошёл проверку фактов и «ответ по вопросу». Пересказ может быть неточным.</li>
          <li><b>Показан текст документа (40)</b> — ответ модели не прошёл проверку, показан сам пункт. При пороге скрытия 65 такие ответы всегда уходят специалисту.</li>
        </ul>
        <p>Дальше к базовому счёту прибавляются и вычитаются поправки из двух следующих групп.</p>
        <p><b>Когда менять.</b> Обычно не меняют. Если модель заменили на более сильную — «Ответ модели» можно поднять до 75.</p>
      </>
    ),
  },
  {
    title: 'Насколько документ подходит к вопросу',
    keys: ['similarity_high', 'similarity_bonus', 'similarity_low', 'similarity_penalty',
      'coverage_high', 'coverage_bonus', 'coverage_low', 'coverage_penalty'],
    help: (
      <>
        <p>Только для ответов, найденных поиском (модель или текст документа). Два признака документа-основания:</p>
        <ul>
          <li>
            <b>Сходство по смыслу</b> (0–1, модель bge-m3) — насколько смысл вопроса близок к пункту документа, даже если слова
            другие. Не ниже «высокого» (0,70) — прибавка +10; ниже «низкого» (0,55) — штраф −15: документ найден в основном
            по совпадению слов. Если поиск по смыслу выключен, этот признак не учитывается.
          </li>
          <li>
            <b>Совпадение слов</b> (0–1) — какая доля слов вопроса (с синонимами) есть в документе. Не ниже 0,75 — +10;
            ниже 0,40 — −15.
          </li>
        </ul>
        <p>
          Пример: ответ модели (70), сходство 0,76 и совпадение 0,80 → 70 + 10 + 10 = 90, зелёный. Сходство 0,50 и
          совпадение 0,30 → 70 − 15 − 15 = 40, ответ скрыт.
        </p>
        <p><b>Когда менять.</b> Только если заменили модель поиска по смыслу — у другой модели другой масштаб сходства.</p>
      </>
    ),
  },
  {
    title: 'Сомнения в ответе',
    keys: ['judge_penalty', 'many_sources_penalty', 'conflict_penalty', 'clarification_penalty'],
    help: (
      <>
        <p>Штрафы за признаки, при которых ответу стоит доверять меньше:</p>
        <ul>
          <li><b>Проверяющий не подтвердил (−15)</b> — вторая проверка модели «это ответ на вопрос?» не дала вердикта.</li>
          <li><b>3 и более документа (−5)</b> — модель собрала ответ из нескольких источников, выше риск смешать правила.</li>
          <li>
            <b>Противоречие по теме (−5)</b> — по теме вопроса есть документ, расходящийся с основанием (например, старое
            сообщение в чате). Приказ всё равно применяется, поэтому штраф небольшой.
          </li>
          <li><b>Нужно уточнение (−15)</b> — ответ зависит от суммы, а её нет в вопросе: ответ показывается жёлтым с просьбой указать сумму.</li>
        </ul>
        <p>
          <b>Когда менять.</b> Если противоречия в чатах часто оказываются важными — поднимите штраф за противоречие до 10–15.
          Штраф за уточнение выше 20 скроет ответы «укажите сумму».
        </p>
      </>
    ),
  },
]

/** «Администрирование» → «Уверенность ответа»: пороги и поправки плашки у ответа. */
export function ConfidenceSettings() {
  const [data, setData] = useState<Data | null>(null)
  const [values, setValues] = useState<Record<string, string>>({})
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [openHelp, setOpenHelp] = useState<Record<string, boolean>>({})

  const apply = (d: Data) => {
    setData(d)
    setValues(Object.fromEntries(d.items.map((p) => [p.key, String(p.value)])))
  }
  useEffect(() => {
    api.get<Data>('admin/confidence').then(apply)
  }, [])

  const save = async () => {
    setError('')
    setMessage('')
    try {
      const body = Object.fromEntries(Object.entries(values).map(([k, v]) => [k, Number(String(v).replace(',', '.'))]))
      apply(await api.put<Data>('admin/confidence', body))
      setMessage('Сохранено. Новые критерии применяются к следующим ответам.')
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const reset = async () => {
    if (!window.confirm('Вернуть рекомендуемые значения всех критериев?')) return
    apply(await api.del<Data>('admin/confidence'))
    setMessage('Возвращены рекомендуемые значения.')
  }

  if (!data) return <p>Загрузка…</p>
  const byKey = Object.fromEntries(data.items.map((p) => [p.key, p]))
  return (
    <section className="card">
      <p className="lead">
        У каждого ответа — небольшая плашка: <span className="confidence confidence-green static"><span className="confidence-dot" />Уверен</span>{' '}
        <span className="confidence confidence-yellow static"><span className="confidence-dot" />Сомневается</span>{' '}
        <span className="confidence confidence-red static"><span className="confidence-dot" />Нужно проверить</span>. Счёт 0–100 =
        базовое значение по способу ответа ± поправки. Если счёт не выше порога скрытия — ответ{' '}
        <b>не показывается</b>{' '}
        <span className="confidence confidence-hidden static"><span className="confidence-dot" />Ответ скрыт</span>, вопрос
        уходит специалисту. Ответа в документах нет или запрос отклонён —{' '}
        <span className="confidence confidence-none static"><span className="confidence-dot" />Ответа нет</span>.
      </p>
      <p className="hint">
        Сейчас: зелёная — от {values.green_threshold} и выше, жёлтая — от {values.yellow_threshold} до{' '}
        {Number(values.green_threshold) - 1}, красная — от {Number(values.hide_threshold) + 1} до {Number(values.yellow_threshold) - 1},
        не выше {values.hide_threshold} — ответ не показывается.
      </p>
      {data.updated && (
        <p className="hint">
          Изменено {data.updated}, {data.updated_by}
        </p>
      )}
      {GROUPS.map(({ title, keys, help }) => (
        <div key={title}>
          <h3>
            {title}
            <button
              type="button"
              className={`help-btn ${openHelp[title] ? 'open' : ''}`}
              aria-expanded={!!openHelp[title]}
              title={openHelp[title] ? 'Скрыть пояснение' : 'Что это и когда менять'}
              onClick={() => setOpenHelp((current) => ({ ...current, [title]: !current[title] }))}
            >
              ?
            </button>
          </h3>
          {openHelp[title] && <div className="help-box">{help}</div>}
          <table className="list settings">
            <colgroup>
              <col className="c-label" />
              <col className="c-value" />
              <col className="c-rec" />
              <col />
            </colgroup>
            <thead>
              <tr>
                <th>Критерий</th>
                <th>Значение</th>
                <th>Рекомендуется</th>
                <th>Что значит</th>
              </tr>
            </thead>
            <tbody>
              {keys.map((key) => {
                const p = byKey[key]
                const changed = Number(String(values[key]).replace(',', '.')) !== p.recommended
                return (
                  <tr key={key} className={changed ? 'changed' : ''}>
                    <td>{p.label}</td>
                    <td>
                      <input
                        type="number"
                        min={p.min}
                        max={p.max}
                        step={p.step}
                        value={values[key] ?? ''}
                        onChange={(e) => setValues({ ...values, [key]: e.target.value })}
                      />
                    </td>
                    <td className="nowrap">{String(p.recommended).replace('.', ',')}</td>
                    <td className="hint">
                      {p.help} (от {p.min} до {p.max})
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      ))}
      {error && <p className="error-text">{error}</p>}
      {message && <p className="ok-text">{message}</p>}
      <div className="row">
        <button className="primary" onClick={save}>
          Сохранить
        </button>
        <button onClick={reset}>Вернуть рекомендуемые</button>
      </div>
    </section>
  )
}
