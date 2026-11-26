import { lazy, Suspense } from "react"
import { useQuery } from "@tanstack/react-query"
import { Navigate, Route, Routes, useLocation } from "react-router-dom"
import { Toaster } from "sonner"
import { AppShell } from "@/app/app-shell"
import { DataState } from "@/components/data-state"
import { sessionQuery, SignedOutError } from "@/lib/api"
import { LoginPage } from "@/pages/login"

const AgentsPage = lazy(() => import("@/pages/agents").then(({ AgentsPage }) => ({ default: AgentsPage })))
const InboxPage = lazy(() => import("@/pages/inbox").then(({ InboxPage }) => ({ default: InboxPage })))
const MemoryPage = lazy(() => import("@/pages/memory").then(({ MemoryPage }) => ({ default: MemoryPage })))
const AuditPage = lazy(() => import("@/pages/audit").then(({ AuditPage }) => ({ default: AuditPage })))
const OverviewPage = lazy(() => import("@/pages/overview").then(({ OverviewPage }) => ({ default: OverviewPage })))
const ProjectPage = lazy(() => import("@/pages/project").then(({ ProjectPage }) => ({ default: ProjectPage })))
const TokensPage = lazy(() => import("@/pages/tokens").then(({ TokensPage }) => ({ default: TokensPage })))
const SettingsPage = lazy(() => import("@/pages/settings").then(({ SettingsPage }) => ({ default: SettingsPage })))

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
      <Suspense fallback={<main className="mx-auto max-w-lg p-8"><DataState kind="loading" /></main>}>
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
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="/canvas/*" element={<Navigate to="/" replace />} />
            <Route path="/provision/*" element={<Navigate to="/" replace />} />
            <Route path="*" element={<DataState kind="empty" message="Page not found." />} />
          </Route>
        </Routes>
      </Suspense>
      <Toaster position="bottom-right" />
    </>
  )
}
