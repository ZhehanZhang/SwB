import { useState } from "react"
import { ArrowDown, ArrowDownUp, ArrowUp } from "lucide-react"
import {
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type SortingState,
  type TableOptions,
} from "@tanstack/react-table"
import { DataState, type DataStateProps } from "@/components/data-state"
import { cn } from "@/lib/utils"

type DataTableProps<T> = {
  caption: string
  columns: TableOptions<T>["columns"]
  rows?: T[]
  getRowId?: TableOptions<T>["getRowId"]
  state?: DataStateProps
  className?: string
}

const emptyRows: never[] = []

export function DataTable<T>({ caption, columns, rows = emptyRows, getRowId, state, className }: DataTableProps<T>) {
  const [sorting, setSorting] = useState<SortingState>([])
  const table = useReactTable({
    data: rows,
    columns,
    getRowId,
    state: { sorting },
    onSortingChange: setSorting,
    enableMultiSort: false,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  })
  const rowModel = table.getRowModel().rows
  const dataState = state ?? (rowModel.length === 0 ? { kind: "empty" as const } : undefined)

  return (
    <div
      className={cn("overflow-x-auto rounded-md border bg-card", className)}
      role="region"
      aria-label={caption}
      tabIndex={0}
    >
      <table className="w-full text-left text-body-sm">
        <caption className="sr-only">{caption}</caption>
        <thead className="border-b text-muted-foreground">
          {table.getHeaderGroups().map((group) => (
            <tr key={group.id}>
              {group.headers.map((header) => {
                const { column } = header
                const sort = column.getIsSorted()
                const SortIcon = sort === "asc" ? ArrowUp : sort === "desc" ? ArrowDown : ArrowDownUp
                const content = flexRender(column.columnDef.header, header.getContext())
                return (
                  <th
                    key={header.id}
                    colSpan={header.colSpan}
                    scope={header.isPlaceholder ? undefined : header.subHeaders.length ? "colgroup" : "col"}
                    aria-sort={sort === "asc" ? "ascending" : sort === "desc" ? "descending" : undefined}
                    className="px-4 py-2 font-medium whitespace-nowrap"
                  >
                    {!header.isPlaceholder && (column.getCanSort() && !dataState ? (
                      <button
                        type="button"
                        className="inline-flex min-h-8 items-center gap-1.5 rounded-sm hover:text-foreground"
                        onClick={column.getToggleSortingHandler()}
                      >
                        {content}<SortIcon aria-hidden className="size-3.5" />
                      </button>
                    ) : content)}
                  </th>
                )
              })}
            </tr>
          ))}
        </thead>
        <tbody>
          {dataState ? (
            <tr>
              <td className="px-4" colSpan={table.getVisibleLeafColumns().length || 1}>
                <DataState {...dataState} />
              </td>
            </tr>
          ) : rowModel.map((row) => (
            <tr key={row.id} className="border-b last:border-b-0">
              {row.getVisibleCells().map((cell) => (
                <td key={cell.id} className="px-4 py-3">
                  {flexRender(cell.column.columnDef.cell, cell.getContext())}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
