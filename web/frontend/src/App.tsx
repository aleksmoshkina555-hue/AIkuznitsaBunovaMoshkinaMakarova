import { Navigate, Route, Routes } from 'react-router-dom'
import { useApp } from './app/context'
import { Layout } from './components/Layout'
import { AdminPage } from './pages/Admin'
import { AnswerPage } from './pages/Answer'
import { AskPage } from './pages/Ask'
import { AttentionPage } from './pages/Attention'
import { DocumentsPage } from './pages/Documents'
import { EmployeesPage } from './pages/Employees'
import { HistoryPage } from './pages/History'
import { InboxPage } from './pages/Inbox'
import { LoginPage } from './pages/Login'
import { SummaryPage } from './pages/Summary'
import { TopicsPage } from './pages/Topics'

export default function App() {
  const { user, meta, loading } = useApp()
  if (loading) return <div className="splash">Загрузка…</div>
  if (!user) return <LoginPage />
  if (!meta) return <div className="splash">Загрузка…</div> // права (администратор или нет) — до построения маршрутов
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<AskPage />} />
        <Route path="answer" element={<AnswerPage />} />
        <Route path="inbox" element={<InboxPage />} />
        <Route path="inbox/process/:process" element={<InboxPage />} />
        <Route path="inbox/:scope" element={<InboxPage />} />
        <Route path="summary" element={<SummaryPage />} />
        <Route path="attention" element={<AttentionPage />} />
        <Route path="documents" element={<DocumentsPage />} />
        <Route path="employees" element={<EmployeesPage />} />
        <Route path="history" element={<HistoryPage />} />
        {meta?.is_admin && <Route path="admin" element={<AdminPage />} />}
        {meta?.is_admin && <Route path="topics" element={<TopicsPage />} />}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}
