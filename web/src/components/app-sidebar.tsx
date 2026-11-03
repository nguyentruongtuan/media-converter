import { Clapperboard, KeyRound, Library, Upload, type LucideIcon } from "lucide-react"

import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuBadge,
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from "@/components/ui/sidebar"
import { routeHref, type Route } from "@/hooks/use-route"

export const MENU: { route: Route; title: string; icon: LucideIcon }[] = [
  { route: "videos", title: "Uploaded videos", icon: Library },
  { route: "upload", title: "Upload video", icon: Upload },
]

type Props = {
  route: Route
  uploadBadge: string | null
  onChangeKey: (() => void) | null
}

export function AppSidebar({ route, uploadBadge, onChangeKey }: Props) {
  const { setOpenMobile } = useSidebar()

  return (
    <Sidebar>
      <SidebarHeader>
        <div className="flex items-center gap-2 px-2 py-1.5 font-semibold">
          <Clapperboard className="size-5" />
          Media Converter
        </div>
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupContent>
            <SidebarMenu>
              {MENU.map((item) => (
                <SidebarMenuItem key={item.route}>
                  <SidebarMenuButton
                    isActive={route === item.route}
                    render={<a href={routeHref(item.route)} onClick={() => setOpenMobile(false)} />}
                  >
                    <item.icon />
                    <span>{item.title}</span>
                  </SidebarMenuButton>
                  {item.route === "upload" && uploadBadge && <SidebarMenuBadge>{uploadBadge}</SidebarMenuBadge>}
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>
      {onChangeKey && (
        <SidebarFooter>
          <SidebarMenu>
            <SidebarMenuItem>
              <SidebarMenuButton onClick={onChangeKey}>
                <KeyRound />
                <span>Change API key</span>
              </SidebarMenuButton>
            </SidebarMenuItem>
          </SidebarMenu>
        </SidebarFooter>
      )}
    </Sidebar>
  )
}
