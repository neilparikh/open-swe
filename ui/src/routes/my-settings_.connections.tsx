import { createFileRoute } from "@tanstack/react-router"
import { Switch } from "@langchain/macaw-components/Switch"

import {
  SettingsPage,
  SettingsRow,
  SettingsSection,
} from "@/components/AppShell"
import { ConnectionsSection } from "@/features/settings/components/ConnectionsSection"
import { MCPConnectionsSection } from "@/features/settings/components/MCPConnectionsSection"
import { ManagedToolsSection } from "@/features/settings/components/ManagedToolsSection"
import { ProfileSwitchRow } from "@/features/settings/components/ProfileSwitchRow"
import { pageTitle } from "@/lib/pageTitle"

export const Route = createFileRoute("/my-settings_/connections")({
  component: ConnectionsPage,
  head: () => ({ meta: [{ title: pageTitle("Connections") }] }),
})

function ConnectionsPage() {
  return (
    <SettingsPage
      title="Connections"
      description="Accounts and tools Open SWE can use on your behalf in your private threads."
    >
      {(user) => (
        <>
          <ConnectionsSection user={user} />
          <ManagedToolsSection />
          <SettingsSection title="Slack">
            <ProfileSwitchRow
              field="concierge_mode"
              label="Concierge mode"
              description="Your whole DM with Open SWE becomes one private thread it always answers in, instead of a new thread per message."
            />
            <ProfileSwitchRow
              field="pr_review_links"
              label="Open pull requests in Open SWE"
              description="Pull request links Open SWE posts for you open its review page instead of GitHub."
            />
            <ProfileSwitchRow
              field="pr_failure_reactions"
              label="React to failing checks"
              description="Add ❌ to watched pull request posts when checks fail on a pull request you own."
            />
          </SettingsSection>
          {user.microsoft_oauth_enabled && (
            <SettingsSection title="Microsoft Teams">
              <SettingsRow
                label="Concierge mode"
                htmlFor="teams_concierge_mode"
                badge="Always on"
                description="Your whole DM with Open SWE in Teams is one private thread it always answers in. This can't be turned off: Teams chats have no reply threads to start a new one per message, so send “new” or “start over” to begin a fresh thread instead."
                control={
                  <Switch
                    id="teams_concierge_mode"
                    aria-label="Concierge mode"
                    checked
                    disabled
                    onChange={() => undefined}
                  />
                }
              />
            </SettingsSection>
          )}
          <MCPConnectionsSection scope="user" />
        </>
      )}
    </SettingsPage>
  )
}
