import { useEffect, type PropsWithChildren } from "react"
import { useQuery } from "@tanstack/react-query"
import { Navigate, Outlet, Route, Routes } from "react-router-dom"
import { Toaster } from "sonner"
import { AppShell } from "@/app/app-shell"
import { sections } from "@/app/navigation"
import { DataState } from "@/components/data-state"
import { Footer } from "@/components/footer"
import { sessionQuery, SignedOutError } from "@/lib/api"
import { useResolvedTheme } from "@/lib/appearance"

function Placeholder({ title }: { title: string }) {
  useEffect(() => {
    document.title = `${title} | Switchboard`
  }, [title])

  return <h1 className="font-display text-3xl font-extrabold tracking-tight">{title}</h1>
}

function PublicLayout({ children }: PropsWithChildren) {
  return (
    <div className="flex min-h-dvh flex-col">
      <main className="flex-1 px-4 py-6 sm:px-6">{children ?? <Outlet />}</main>
      <Footer />
    </div>
  )
}

function RequireSession() {
  const session = useQuery(sessionQuery)
  if (session.data?.signed_in === false || session.error instanceof SignedOutError) {
    return <Navigate to="/login" replace />
  }
  if (session.data?.signed_in) return <AppShell />

  return (
    <PublicLayout>
      {session.isError
        ? <DataState kind="error" message="Session unavailable." onRetry={() => void session.refetch()} />
        : <DataState kind="loading" message="Checking session…" />}
    </PublicLayout>
  )
}

export function App() {
  const theme = useResolvedTheme()

  return (
    <>
      <Routes>
        <Route element={<PublicLayout />}>
          <Route path="/login" element={<Placeholder title="Sign in" />} />
        </Route>
        <Route element={<RequireSession />}>
          <Route index element={<Placeholder title={sections[0].name} />} />
          {sections.map(({ name, to }) => (
            <Route key={to} path={`${to}/*`} element={<Placeholder title={name} />} />
          ))}
          <Route path="*" element={<Placeholder title="Page not found" />} />
        </Route>
      </Routes>
      <Toaster theme={theme} position="top-right" />
    </>
  )
}
