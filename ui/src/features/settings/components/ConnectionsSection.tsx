import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { Badge } from "@langchain/macaw-components/Badge"
import { Button } from "@langchain/macaw-components/Button"
import type { IconComponent } from "@langchain/macaw-components/Icon"
import { MicrosoftTeamsLogoIcon } from "@phosphor-icons/react/dist/ssr/MicrosoftTeamsLogo"
import { SlackLogoIcon } from "@phosphor-icons/react/dist/ssr/SlackLogo"

import type { LangSmithConnectionStatus, SessionUser } from "@/lib/api"
import { SettingsRow, SettingsSection } from "@/components/AppShell"
import { api, connectService } from "@/lib/api"
import { optimisticUpdate } from "@/lib/optimistic"
import { sessionQueryOptions } from "@/lib/session"

function StatusPill({ connected }: { connected: boolean }) {
  return (
    <Badge color={connected ? "primary" : "secondary"} size="xs">
      {connected ? "Connected" : "Not connected"}
    </Badge>
  )
}

/** The session field naming each provider's linked account. */
const LINKED_ACCOUNT_FIELD = {
  slack: "slack_user_id",
  microsoft: "microsoft_login",
} as const satisfies Record<"slack" | "microsoft", keyof SessionUser>

function AccountRow({
  label,
  icon,
  provider,
  enabled,
  linkedAs,
  unlinkedDescription,
}: {
  label: string
  icon: IconComponent
  provider: keyof typeof LINKED_ACCOUNT_FIELD
  enabled: boolean
  /** How the linked account reads after "Linked to", or null while unlinked. */
  linkedAs: string | null
  unlinkedDescription: string
}) {
  const qc = useQueryClient()
  const [connecting, setConnecting] = useState(false)
  const connected = linkedAs !== null

  const connect = () => {
    setConnecting(true)
    // The link lands on the session's user row; refresh it when the OAuth redirect returns.
    void qc.invalidateQueries({ queryKey: ["session"] })
    void connectService(provider)?.finally(() => {
      setConnecting(false)
      void qc.invalidateQueries({ queryKey: ["session"] })
    })
  }
  const disconnect = useMutation({
    meta: { errorTitle: `Couldn't disconnect ${label}` },
    mutationFn: () => api.disconnectAccount(provider),
    onMutate: async () => ({
      undo: await optimisticUpdate<SessionUser | null>(
        qc,
        sessionQueryOptions.queryKey,
        (current) =>
          current && { ...current, [LINKED_ACCOUNT_FIELD[provider]]: null }
      ),
    }),
    onError: (_e, _v, ctx) => ctx?.undo(),
    onSettled: () =>
      void qc.invalidateQueries({ queryKey: sessionQueryOptions.queryKey }),
  })

  return (
    <SettingsRow
      label={label}
      description={connected ? `Linked to ${linkedAs}.` : unlinkedDescription}
      control={
        <div className="flex items-center gap-space-2">
          <StatusPill connected={connected} />
          {connected ? (
            <Button
              size="xs"
              color="secondary"
              variant="outlined"
              onClick={() => disconnect.mutate()}
              disabled={disconnect.isPending}
            >
              Disconnect
            </Button>
          ) : enabled ? (
            <Button
              size="xs"
              color="primary"
              variant="normal"
              leftDecorator={icon}
              onClick={connect}
              disabled={connecting}
            >
              {connecting ? "Redirecting…" : "Connect"}
            </Button>
          ) : (
            <span className="text-xxs text-secondary">
              Sign in with {label} unavailable
            </span>
          )}
        </div>
      }
    />
  )
}

export const LANGSMITH_CONNECTION_KEY = ["myLangSmith"]

/** The caller's own LangSmith connection, shared by every feature that calls LangSmith as them. */
export function useLangSmithConnection() {
  return useQuery({
    queryKey: LANGSMITH_CONNECTION_KEY,
    queryFn: api.getMyLangSmithStatus,
  })
}

export function ConnectLangSmithButton({
  size = "xs",
}: {
  size?: "xs" | "sm"
}) {
  const qc = useQueryClient()
  const status = useLangSmithConnection()
  const [connecting, setConnecting] = useState(false)
  return (
    <Button
      size={size}
      color="primary"
      onClick={() => {
        setConnecting(true)
        void connectService("langsmith", window.location.href)?.finally(() => {
          setConnecting(false)
          void qc.invalidateQueries({ queryKey: LANGSMITH_CONNECTION_KEY })
        })
      }}
      disabled={connecting || status.isLoading}
    >
      {connecting ? "Redirecting…" : "Connect LangSmith"}
    </Button>
  )
}

function LangSmithRow() {
  const qc = useQueryClient()
  const status = useLangSmithConnection()
  const disconnect = useMutation({
    meta: { errorTitle: "Couldn't disconnect LangSmith" },
    mutationFn: () => api.disconnectLangSmith(),
    onMutate: async () => ({
      undo: await optimisticUpdate<LangSmithConnectionStatus>(
        qc,
        LANGSMITH_CONNECTION_KEY,
        (current) => ({ ...current, connected: false, email: null })
      ),
    }),
    onError: (_e, _v, ctx) => ctx?.undo(),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: LANGSMITH_CONNECTION_KEY })
      void qc.invalidateQueries({ queryKey: ["myManagedTools"] })
    },
  })
  if (!status.data?.available) return null
  const connected = status.data.connected
  return (
    <SettingsRow
      label="LangSmith"
      description={
        connected
          ? `Signed in${status.data.email ? ` as ${status.data.email}` : ""}. Open SWE can call LangSmith as you in your private threads.`
          : "Sign in with LangSmith so Open SWE can call LangSmith as you in your private threads."
      }
      control={
        <div className="flex items-center gap-space-2">
          <StatusPill connected={connected} />
          {connected ? (
            <Button
              color="secondary"
              variant="outlined"
              size="xs"
              onClick={() => disconnect.mutate()}
              disabled={disconnect.isPending}
            >
              Disconnect
            </Button>
          ) : (
            <ConnectLangSmithButton />
          )}
        </div>
      }
    />
  )
}

export function ConnectionsSection({ user }: { user: SessionUser }) {
  return (
    <SettingsSection title="Accounts">
      <AccountRow
        label="Slack"
        icon={SlackLogoIcon}
        provider="slack"
        enabled={!!user.slack_oauth_enabled}
        linkedAs={
          user.slack_user_id
            ? `Slack member ${user.slack_user_id}${user.email ? ` · ${user.email}` : ""}`
            : null
        }
        unlinkedDescription="Sign in with Slack so Open SWE resolves your GitHub account when you tag it — the verified email also resolves Linear mentions."
      />
      {/* A linked account stays listed so it can be disconnected after Teams is turned off. */}
      {(user.microsoft_oauth_enabled || user.microsoft_login) && (
        <AccountRow
          label="Microsoft Teams"
          icon={MicrosoftTeamsLogoIcon}
          provider="microsoft"
          enabled={!!user.microsoft_oauth_enabled}
          linkedAs={
            user.microsoft_login
              ? `Microsoft account ${user.microsoft_login}`
              : null
          }
          unlinkedDescription="Sign in with Microsoft so Open SWE knows who you are when you message it in Teams."
        />
      )}
      <LangSmithRow />
    </SettingsSection>
  )
}
