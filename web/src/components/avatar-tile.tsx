import { LogoTile } from "@/components/logo-tile"

export function AvatarTile({ kind, avatarUrl }: { kind: string; avatarUrl?: string | null }) {
  return <LogoTile kind={kind} avatarUrl={avatarUrl} className="size-5 rounded-sm" />
}
