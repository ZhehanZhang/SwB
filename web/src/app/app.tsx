import { useEffect } from "react"
import { Route, Routes } from "react-router-dom"
import { Toaster } from "sonner"
import { AppShell } from "@/app/app-shell"
import { sections } from "@/app/navigation"
import { useResolvedTheme } from "@/lib/appearance"

function Placeholder({ title }: { title: string }) {
  useEffect(() => {
    document.title = `${title} | Switchboard`
  }, [title])

  return <h1 className="font-display text-3xl font-extrabold tracking-tight">{title}</h1>
}

export function App() {
  const theme = useResolvedTheme()

  return (
    <>
      <Routes>
        <Route element={<AppShell />}>
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
