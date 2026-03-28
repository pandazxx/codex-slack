# Cloud Workspace Authentication Analysis

**Status:** draft
**Author:** Codex architect
**Date:** 2026-03-28
**Related ADRs:** ADR-0001

## Problem Statement

The project needs an authentication model for optional cloud-backed agent workspaces. The runtime is primarily headless and container-managed, so authentication methods that depend on a browser inside the agent, device-bound desktop login flows, or human interaction from inside the container are poor fits.

## Goals

- Identify which provider auth methods work in a headless master/agent environment
- Keep master as the primary trust boundary and token broker
- Avoid placing broad or long-lived credentials directly in agent containers
- Prefer least-privilege access patterns that still let agents read and write user files
- Preserve a path to multi-provider support without changing the core runtime model

## Non-Goals

- Implementing provider authentication in this phase
- Defining the final secret storage backend for master
- Solving document conflict resolution in this document

## Headless Constraints

These constraints materially affect the design:

- The agent container should not rely on an embedded browser login flow.
- The agent container should not require human approval steps such as OTP entry, mobile push approval, or hardware key interaction.
- Desktop sync clients are not a suitable auth bridge for containerized agents.
- Any provider flow that needs user interaction should terminate at master, not inside the agent.

## Recommended Cross-Provider Pattern

For all providers, the default pattern should be:

1. Master initiates or brokers the authentication flow.
2. User completes any required browser login on their own device.
3. Master stores the long-lived credential material, if any, in its own secret store.
4. Master injects only the minimum runtime credential needed by an agent sync job.
5. Agent receives either:
   - a short-lived access token, or
   - a provider-specific app password or session token with reduced blast radius
6. Agent syncs files to a local workspace mirror and does not own the primary refresh process.

This keeps the agent disposable and makes provider support an extension of master rather than a new trust domain.

## Option-by-Option Authentication Review

### 1. Nextcloud

#### Viable methods

- App passwords for WebDAV and client access
- Nextcloud Login Flow v2 to mint a unique app password for a device or client
- Session cookies for WebDAV, though this is weaker for a headless integration than app-password-based auth

#### Headless fit

Strong.

The best fit is for master to initiate Nextcloud Login Flow and store the resulting app password, or to accept a user-generated app password created in the Nextcloud security UI. Nextcloud explicitly documents app passwords for client applications and requires them when 2FA is enabled. WebDAV requests can then use Basic Auth with that app password.

#### Main limitations

- App passwords are long-lived secrets and should be treated like bearer credentials.
- Scoping is coarse. The app password authenticates as the user, not as a narrow folder-specific token.
- Least privilege may require a dedicated Nextcloud integration user, shared-folder boundaries, or per-agent workspace folders.

#### Practical master/agent design

- Master performs the login-flow handoff or accepts a pre-created app password.
- Master stores the app password and WebDAV base URL.
- Agent receives the app password only at sync time through a temporary file or environment mount.
- Master should rotate or revoke app passwords if an agent is decommissioned.

#### Verdict

Best overall fit for a headless managed environment.

### 2. Microsoft OneDrive / SharePoint

#### Viable methods

- OAuth 2.0 device code flow for delegated user access
- OAuth 2.0 authorization code flow with PKCE or server-side code exchange
- OAuth 2.0 client credentials flow for app-only access

#### Headless fit

Moderate to strong, depending on the file-access model.

Microsoft explicitly supports the device authorization grant, which is a strong fit for headless master environments because the user signs in on another device while master polls for completion. Microsoft Graph also supports delegated and app-only access patterns.

For user-owned workspaces, delegated auth is the safer default because it preserves user context. For tightly scoped storage, Microsoft Graph supports `Files.ReadWrite.AppFolder` in delegated and application-only patterns, which is appealing if the product can accept an app-owned folder instead of arbitrary user folder access.

#### Main limitations

- App-only Graph permissions can become too broad very quickly in enterprise tenants.
- Arbitrary user-folder access usually implies delegated auth and careful scope review.
- Tenant admin consent and identity policy can add operational friction.

#### Practical master/agent design

- Preferred v1: master runs device code flow for delegated auth and stores refresh-token-backed credentials.
- Master refreshes access tokens server-side and injects short-lived access tokens to the agent sync path.
- If the product can constrain storage to an app folder, evaluate `Files.ReadWrite.AppFolder` to reduce privilege.

#### Verdict

Best managed-cloud auth story for a headless environment, but more enterprise-heavy than Nextcloud.

### 3. Google Drive

#### Viable methods

- OAuth 2.0 user consent with an OAuth client ID
- Service accounts
- Service accounts with domain-wide delegation in Google Workspace organizations

#### Headless fit

Weak for general user storage, moderate for Google Workspace admin-controlled deployments.

Google documents OAuth client IDs for user consent flows and service accounts for application access. Service accounts can impersonate users only when a Google Workspace super administrator grants domain-wide delegation. That is workable for enterprise Workspace tenants, but it is not a general solution for consumer Drive or unmanaged organizational accounts.

I did not find an official Google Drive device-code flow in the current docs reviewed here, so in a headless deployment the general user path appears to require a master-hosted browser handoff rather than a cleaner device-code pattern. This is an inference from the reviewed Google auth documentation, not a direct Google statement that device code is unsupported.

#### Main limitations

- Broad automation against arbitrary user files is awkward without Workspace admin involvement.
- Native Google Docs, Sheets, and Slides are not normal files, which complicates both access control and sync behavior.
- Sensitive or restricted Drive scopes may trigger additional verification requirements.

#### Practical master/agent design

- For general public users: master would need a browser-based OAuth handoff and secure refresh-token storage.
- For Workspace enterprise environments: service account plus domain-wide delegation is possible, but only with admin approval and careful scope restriction.
- If Google Drive is ever supported, prefer the narrowest scopes such as `drive.file` where the product model allows it.

#### Verdict

Poor default fit for the first headless implementation unless the target users are already inside a managed Google Workspace domain.

### 4. Synology Drive

#### Viable methods

- DSM 7.3 OAuth Service with OAuth 2.0 tokens and scopes
- Traditional DSM API login via `SYNO.API.Auth` and session cookies or session identifiers

#### Headless fit

Moderate.

Synology now documents an OAuth Service in DSM 7.3, which is a meaningful improvement for headless integrations. That makes a master-brokered OAuth flow viable on modern deployments. However, Synology also documents classic login APIs and session-oriented auth patterns, which are functional but less attractive in a distributed agent architecture.

#### Main limitations

- The OAuth Service is documented as not compatible with SSO.
- Capability depends on DSM version and package availability on the target NAS.
- Older session-based methods are workable but weaker and less elegant than provider-native refresh-token models.

#### Practical master/agent design

- Only support Synology through the OAuth Service path for v1, and require DSM 7.3+.
- Do not design around session-cookie login as the primary method unless forced by customer environments.
- Master should perform the OAuth dance and pass short-lived access tokens into the agent sync step.

#### Verdict

Viable for self-hosted customers already standardized on Synology, but not the best default backend.

### 5. Dropbox

#### Viable methods

- OAuth 2.0 authorization code flow
- OAuth 2.0 authorization code flow with PKCE
- Offline refresh tokens using `token_access_type=offline`
- Client credentials flow, but only for app-auth endpoints rather than user-file access

#### Headless fit

Moderate.

Dropbox has a clean server-side OAuth story and explicitly supports refresh tokens for applications that need background access. That fits a master-brokered headless environment well if master can open a browser handoff or present an authorization URL to the user.

#### Main limitations

- Dropbox user-file access still requires user OAuth; client credentials do not replace that for ordinary file access.
- There is no obvious device-code equivalent in the official docs reviewed here, so master likely needs a browser callback flow.
- Office editing relies on Dropbox and Microsoft integrations rather than a single native storage-plus-editor platform.

#### Practical master/agent design

- Master runs the OAuth code flow and requests offline access.
- Master stores the refresh token and exchanges it for short-lived access tokens.
- Agent receives only the short-lived access token plus the target path configuration.

#### Verdict

Reasonable managed-cloud option for headless deployments, but weaker than OneDrive for enterprise auth ergonomics and weaker than Nextcloud for file-system-like workspace behavior.

## Comparative Summary

| Provider | Best headless auth pattern | Least-privilege outlook | Operational friction | Headless suitability |
| --- | --- | --- | --- | --- |
| Nextcloud | App password via master-brokered login flow | Medium | Low to medium | High |
| OneDrive / SharePoint | Device code or delegated auth code flow | Medium to high | Medium to high | High |
| Google Drive | Browser OAuth or Workspace domain-wide delegation | Low to medium | High | Low to medium |
| Synology Drive | DSM 7.3 OAuth Service | Medium | Medium | Medium |
| Dropbox | OAuth code flow with offline refresh tokens | Medium | Medium | Medium |

## Recommendation

Recommendation for v1:

- Primary backend: Nextcloud
- Secondary candidate: OneDrive / SharePoint
- Self-hosted alternative: Synology Drive, but only through DSM 7.3 OAuth Service
- Defer by default: Google Drive
- Optional later managed-cloud backend: Dropbox

The auth-specific reason for keeping Nextcloud first is simple: it supports a headless-friendly model without forcing the project into heavyweight enterprise identity flows, while still allowing master to control authentication and isolate agent credentials.

## Open Questions

- Should master store provider refresh tokens directly, or should it delegate secret storage to an external vault?
- Do we want per-user credentials only, or also service-account-style integrations for enterprise tenants?
- Should provider credentials be attached to an agent definition, a human owner, or a reusable workspace profile?
- Is folder-level scoping mandatory for v1, or can dedicated remote workspace roots satisfy least-privilege requirements?

## References

- Nextcloud Authentication: https://docs.nextcloud.com/server/latest/admin_manual/configuration_user/authentication.html
- Nextcloud Login Flow: https://docs.nextcloud.com/server/stable/developer_manual/client_apis/LoginFlow/index.html
- Nextcloud WebDAV basics: https://docs.nextcloud.com/server/latest/developer_manual/client_apis/WebDAV/basic.html
- Microsoft device code flow: https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-device-code
- Microsoft Graph auth concepts: https://learn.microsoft.com/en-us/graph/auth/auth-concepts
- Microsoft Graph permissions overview: https://learn.microsoft.com/en-us/graph/permissions-overview
- Microsoft Graph app folder: https://learn.microsoft.com/en-us/graph/onedrive-sharepoint-appfolder
- Google Workspace auth overview: https://developers.google.com/workspace/guides/auth-overview
- Google credential selection: https://developers.google.com/workspace/guides/create-credentials
- Google service accounts: https://developers.google.com/identity/protocols/oauth2/service-account
- Google Drive scopes: https://developers.google.com/workspace/drive/api/guides/api-specific-auth
- Synology OAuth Service: https://www.synology.com/en-ph/dsm/7.3/software_spec/oauth
- Synology File Station API Guide: https://global.download.synology.com/download/Document/Software/DeveloperGuide/Package/FileStation/All/enu/Synology_File_Station_API_Guide.pdf
- Dropbox OAuth guide: https://developers.dropbox.com/oauth-guide
- Dropbox developer documentation: https://www.dropbox.com/developers/documentation
- Dropbox Microsoft Office FAQ: https://help.dropbox.com/integrations/microsoft-office-faq
