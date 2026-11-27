import { useEffect, useState } from 'react'

/** Секундомер, пока идёт поиск ответа: «3,4 с». */
export function useTimer(running: boolean): string {
  const [started, setStarted] = useState(0)
  const [now, setNow] = useState(0)
  useEffect(() => {
    if (!running) return
    const start = Date.now()
    setStarted(start)
    setNow(start)
    const id = window.setInterval(() => setNow(Date.now()), 100)
    return () => window.clearInterval(id)
  }, [running])
  return running ? `${((now - started) / 1000).toFixed(1).replace('.', ',')} с` : ''
}
