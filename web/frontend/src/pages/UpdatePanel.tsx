import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api'

interface Backup {
  id: string
  version: string
  created: string
  note: string
}
interface State {
  version: string
  backups: Backup[]
  history: { time: string; user: string; action: string; detail: string }[]
}
interface Inspected {
  token: string
  file: string
  from_version: string
  to_version: string
  newer: boolean
  changed: string[]
  added: string[]
  new_org_files: string[]
}

export const UPDATED_KEY = 'konsultant.updated'

/** Ждать, пока сервер перезапустится (прежний завершается, новый поднимается), и перезагрузить страницу. */
async function waitForRestart(onTick: (seconds: number) => void, message: string) {
  sessionStorage.setItem(UPDATED_KEY, message)
  const start = Date.now()
  await new Promise((r) => setTimeout(r, 3000)) // прежний сервер ещё отвечает — дать ему завершиться
  while (Date.now() - start < 180_000) {
    onTick(Math.round((Date.now() - start) / 1000))
    try {
      const response = await fetch('/api/ping', { cache: 'no-store' })
      if (response.ok) {
        window.location.reload()
        return
      }
    } catch {
      /* сервер ещё не поднялся */
    }
    await new Promise((r) => setTimeout(r, 1500))
  }
  sessionStorage.removeItem(UPDATED_KEY)
  throw new Error('Сервер не поднялся за 3 минуты. Посмотрите окно «Сервер работает» или запустите ярлык «ИИ-консультант (веб)».')
}

/** «Администрирование» → «Версия»: установить пакет обновления, вернуть прежнюю версию, перезапустить сервер. */
export function UpdatePanel() {
  const [state, setState] = useState<State | null>(null)
  const [inspected, setInspected] = useState<Inspected | null>(null)
  const [allowOlder, setAllowOlder] = useState(false)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [showFiles, setShowFiles] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)

  const load = useCallback(() => api.get<State>('admin/update').then(setState), [])
  useEffect(() => {
    load()
  }, [load])

  const restartWait = async (message: string) => {
    try {
      await waitForRestart((s) => setBusy(`Сервер перезапускается… ${s} с (обычно до 30 секунд)`), message)
    } catch (e) {
      setBusy('')
      setError((e as Error).message)
    }
  }

  const inspect = async (file: File) => {
    setError('')
    setInspected(null)
    setAllowOlder(false)
    setBusy(`Проверяю пакет «${file.name}»…`)
    const form = new FormData()
    form.append('file', file)
    try {
      setInspected(await api.post<Inspected>('admin/update/inspect', form))
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy('')
    }
  }

  const install = async () => {
    if (!inspected) return
    if (!window.confirm(`Установить версию ${inspected.to_version}? Сервер перезапустится — около 30 секунд система будет недоступна.`)) return
    setError('')
    setBusy('Делаю резервную копию и заменяю файлы программы…')
    try {
      const result = await api.post<{ to_version: string; replaced: number; backup: string }>('admin/update/apply', {
        token: inspected.token,
        allow_older: allowOlder,
      })
      const changed = inspected.changed.length + inspected.added.length
      setInspected(null)
      await restartWait(
        `Система обновлена с версии ${inspected.from_version} до ${result.to_version}: изменено файлов программы — ${changed}. ` +
          `Резервная копия прежней версии — ${result.backup}.`,
      )
    } catch (e) {
      setBusy('')
      setError((e as Error).message)
    }
  }

  const rollback = async (backup: Backup) => {
    if (!window.confirm(`Вернуть версию ${backup.version} (копия от ${backup.created})? Текущая версия тоже сохранится в резервную копию. Сервер перезапустится.`)) return
    setError('')
    setBusy('Возвращаю прежнюю версию…')
    try {
      const result = await api.post<{ version: string }>('admin/update/rollback', { backup: backup.id })
      await restartWait(`Возвращена версия ${result.version}.`)
    } catch (e) {
      setBusy('')
      setError((e as Error).message)
    }
  }

  const restart = async () => {
    if (!window.confirm('Перезапустить сервер? Около 30 секунд система будет недоступна.')) return
    setBusy('Перезапуск…')
    try {
      await api.post('admin/restart')
      await restartWait('Сервер перезапущен.')
    } catch (e) {
      setBusy('')
      setError((e as Error).message)
    }
  }

  if (!state) return <p>Загрузка…</p>
  const changedCount = inspected ? inspected.changed.length + inspected.added.length : 0
  return (
    <section className="card">
      <p>
        <b>Установлена версия {state.version}</b>
      </p>

      <h3>Обновить систему</h3>
      <p className="lead">
        Пакет обновления — файл «ИИ-консультант_обновление_&lt;версия&gt;.zip» от разработчика. Программа покажет, что
        изменится, сделает резервную копию, заменит файлы программы и сама перезапустится. Данные — база знаний,
        обращения, учётные записи, справочники, журналы — не меняются.
      </p>
      <div className="row">
        <button className="primary" disabled={!!busy} onClick={() => fileInput.current?.click()}>
          Выбрать пакет обновления…
        </button>
        <button disabled={!!busy} onClick={restart} title="Если что-то работает не так — перезапуск сервера без обновления">
          Перезапустить сервер
        </button>
        <input
          ref={fileInput}
          type="file"
          accept=".zip"
          hidden
          onChange={(e) => {
            if (e.target.files?.[0]) inspect(e.target.files[0])
            e.target.value = ''
          }}
        />
      </div>
      {busy && <p className="status-text">{busy}</p>}
      {error && <p className="error-text">{error}</p>}

      {inspected && (
        <div className="update-box">
          <p>
            Пакет «{inspected.file}»: версия <b>{inspected.from_version}</b> → <b>{inspected.to_version}</b>. Изменится файлов
            программы: {changedCount} (изменённых {inspected.changed.length}, новых {inspected.added.length}).
            {!!inspected.new_org_files.length && ` Добавятся справочники: ${inspected.new_org_files.join(', ')}.`}
          </p>
          {!changedCount && <p className="warn-text">Файлы пакета совпадают с установленными — обновлять нечего.</p>}
          {!inspected.newer && (
            <label className="check warn-text">
              <input type="checkbox" checked={allowOlder} onChange={(e) => setAllowOlder(e.target.checked)} />
              Версия пакета не новее установленной — всё равно установить
            </label>
          )}
          {!!changedCount && (
            <button className="link" onClick={() => setShowFiles(!showFiles)}>
              {showFiles ? 'скрыть список файлов' : 'показать список файлов'}
            </button>
          )}
          {showFiles && (
            <ul className="file-list">
              {[...inspected.changed.map((f) => `изменён: ${f}`), ...inspected.added.map((f) => `новый: ${f}`)].map((f) => (
                <li key={f}>{f}</li>
              ))}
            </ul>
          )}
          <div className="row">
            <button className="primary" disabled={!!busy || !changedCount || (!inspected.newer && !allowOlder)} onClick={install}>
              Установить обновление
            </button>
            <button disabled={!!busy} onClick={() => setInspected(null)}>
              Отмена
            </button>
          </div>
        </div>
      )}

      <h3>Резервные копии</h3>
      <p className="hint">Перед каждым обновлением и откатом программа сохраняет текущую версию — к ней можно вернуться.</p>
      {state.backups.length ? (
        <table className="list">
          <thead>
            <tr>
              <th>Версия</th>
              <th>Когда</th>
              <th>Почему</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {state.backups.map((b) => (
              <tr key={b.id}>
                <td>{b.version}</td>
                <td className="nowrap">{b.created}</td>
                <td>{b.note}</td>
                <td>
                  <button className="small" disabled={!!busy} onClick={() => rollback(b)}>
                    Вернуть эту версию
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="hint">Резервных копий пока нет — они появятся после первого обновления.</p>
      )}

      {!!state.history.length && (
        <>
          <h3>Последние обновления</h3>
          <ul className="file-list">
            {state.history.map((h, i) => (
              <li key={i}>
                {h.time} · {h.user} · {h.action}: {h.detail}
              </li>
            ))}
          </ul>
        </>
      )}
      <p className="hint">
        Если после обновления программа не запускается: в PowerShell в папке программы выполните{' '}
        <span className="mono">python admin.py --rollback-last</span> — вернётся предыдущая версия.
      </p>
    </section>
  )
}
