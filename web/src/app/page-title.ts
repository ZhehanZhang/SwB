import { createContext, useContext, useLayoutEffect } from "react"

export const PageTitle = createContext<(title: string | undefined) => void>(() => {})

export function usePageTitle(title?: string) {
  const setTitle = useContext(PageTitle)
  useLayoutEffect(() => {
    setTitle(title)
    return () => setTitle(undefined)
  }, [setTitle, title])
}
