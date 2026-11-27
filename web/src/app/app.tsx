import { useQuery } from "@tanstack/react-query"
import { Navigate, Route, Routes, useLocation } from "react-router-dom"
import { Toaster } from "sonner"
import { AppShell } from "@/app/app-shell"
import { AboutPage, AgentsPage, AuditPage, ConnectionsPage, InboxPage, MemoryPage, OverviewPage, ProjectPage, SettingsPage, TokensPage } from "@/app/route-pages"
import { DataState } from "@/components/data-state"
import { sessionQuery, SignedOutError } from "@/lib/api"
import { LoginPage } from "@/pages/login"

function RequireSession() {
  const session = useQuery(sessionQuery)
  const { pathname, search } = useLocation()
  if (session.data?.signed_in === false || session.error instanceof SignedOutError) {
    return <Navigate to={`/login?${new URLSearchParams({ next: pathname + search })}`} replace />
  }
  if (session.data?.signed_in) return <AppShell />
  return (
    <main className="mx-auto max-w-lg p-8">
      {session.isError
        ? <DataState kind="error" message="Session unavailable." onRetry={() => void session.refetch()} />
        : <DataState kind="loading" message="Checking session…" />}
    </main>
  )
}

export function App() {
  return (
    <>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route element={<RequireSession />}>
          <Route index element={<OverviewPage />} />
          <Route path="/agents/*" element={<AgentsPage />} />
          <Route path="/tokens" element={<TokensPage />} />
          <Route path="/inbox" element={<InboxPage />} />
          <Route path="/inbox/:handle" element={<InboxPage />} />
          <Route path="/projects" element={<Navigate to="/" replace />} />
          <Route path="/projects/:slug" element={<ProjectPage />} />
          <Route path="/memory/*" element={<MemoryPage />} />
          <Route path="/audit" element={<AuditPage />} />
          <Route path="/about" element={<AboutPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/connections/:platform" element={<ConnectionsPage />} />
          <Route path="/canvas/*" element={<Navigate to="/" replace />} />
          <Route path="/provision/*" element={<Navigate to="/" replace />} />
          <Route path="*" element={<DataState kind="empty" message="Page not found." />} />
        </Route>
      </Routes>
      <Toaster position="bottom-right" />
    </>
  )
}
