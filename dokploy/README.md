# MCP Atlassian auf Dokploy (Multi-User via OAuth Proxy)

Zentraler MCP-Atlassian-Server auf Dokploy — alle Kollegen melden sich mit
ihrem eigenen Atlassian-Account an (OAuth 2.0 / 3LO über den eingebauten
MCP-OAuth-Proxy). Eine Atlassian-Cloud-Instanz, eine OAuth-App, N Nutzer.

Deployment-Typ in Dokploy: **Application** (nicht Compose).

## Architekturüberblick

```
┌────────────────────┐   HTTPS         ┌──────────────────────┐
│  MCP-Client        │ ──────────────► │  Traefik (Dokploy)   │
│  (Claude/ChatGPT)  │                 │  TLS + Domain-Router │
└────────────────────┘                 └──────────┬───────────┘
         ▲                                        │ :9000/mcp
         │ OAuth Discovery + DCR                  ▼
         │ /.well-known/oauth-authorization-server
         │ /register /authorize /token  ┌──────────────────────┐
         └──────────────────────────────┤  mcp-atlassian       │
                                        │  streamable-http     │
                                        └──────────┬───────────┘
                                                   │ OAuth 3LO
                                                   ▼
                                        ┌──────────────────────┐
                                        │  Atlassian Cloud     │
                                        │  Jira + Confluence   │
                                        └──────────────────────┘
```

## Voraussetzungen

- Dokploy-Server mit öffentlichem DNS (z. B. `mcp.firma.de`) und Traefik/TLS.
- Git-Zugriff auf diesen Fork.
- Admin-Rechte in der Atlassian-Developer-Console (für die OAuth-App).

---

## Schritt 1 — OAuth-App in Atlassian anlegen

1. Öffne https://developer.atlassian.com/console/myapps/ → **Create** → **OAuth 2.0 integration**.
2. **Permissions** → für Jira *und* Confluence die Scopes aus `.env.example`
   (`ATLASSIAN_OAUTH_SCOPE`) aktivieren — inkl. `offline_access`.
3. **Authorization** → **Add Callback URL**:
   ```
   https://mcp.firma.de/callback
   ```
   (muss später 1:1 mit `ATLASSIAN_OAUTH_REDIRECT_URI` matchen).
4. **Settings** → `Client ID` und `Secret` notieren.

## Schritt 2 — `ATLASSIAN_OAUTH_CLOUD_ID` ermitteln

Einmalig lokal den Setup-Wizard laufen lassen (liefert die Cloud-ID):

```bash
docker run --rm -it \
  -p 8080:8080 \
  -e ATLASSIAN_OAUTH_CLIENT_ID=... \
  -e ATLASSIAN_OAUTH_CLIENT_SECRET=... \
  -e ATLASSIAN_OAUTH_REDIRECT_URI=http://localhost:8080/callback \
  -e ATLASSIAN_OAUTH_SCOPE="read:jira-work ... offline_access" \
  ghcr.io/sooperset/mcp-atlassian:latest --oauth-setup -v
```

> Für diesen Schritt temporär `http://localhost:8080/callback` als
> zusätzliche Callback-URL in der Atlassian-App eintragen, dann wieder entfernen.

Der Wizard druckt `ATLASSIAN_OAUTH_CLOUD_ID=...` — diesen Wert in Dokploy eintragen.

## Schritt 3 — Dokploy-Application anlegen

In Dokploy: **New Service → Application**.

### General

| Feld | Wert |
| --- | --- |
| **Source Type** | Git |
| **Repository** | URL dieses Forks |
| **Branch** | `main` (oder euer Deployment-Branch) |

### Build

| Feld | Wert |
| --- | --- |
| **Build Type** | Dockerfile |
| **Dockerfile Path** | `Dockerfile` |
| **Build Context** | `.` |

### Command / Args

Das Dockerfile enthält bereits ein CMD-Default für `streamable-http` auf
Port 9000. **Keine Command-/Args-Override in Dokploy nötig** — Feld leer lassen.

### Environment

Inhalt aus `dokploy/.env.example` kopieren, Platzhalter (`replace_me`) mit
echten Werten füllen. Secrets (`CLIENT_SECRET`) **nur** hier setzen, nicht
ins Repo committen.

### Domain

| Feld | Wert |
| --- | --- |
| **Host** | `mcp.firma.de` |
| **Container Port** | `9000` |
| **HTTPS** | on (Let's Encrypt via Traefik) |

### Volume (PFLICHT für Multi-User-OAuth)

Damit DCR-registrierte MCP-Clients und refresh tokens einen Redeploy
überleben, ein persistentes Volume mounten:

| Feld | Wert |
| --- | --- |
| **Host Path / Volume Name** | z. B. `mcp-atlassian-data` (Dokploy erstellt den Mount automatisch) |
| **Container Path** | `/data` |

Das Image setzt `FASTMCP_HOME=/data` als Default — FastMCP speichert dann
OAuth-Proxy-State dort ab. Ohne dieses Volume müssen sich alle Kollegen nach
jedem Redeploy **neu registrieren**.

### Deploy

→ **Deploy**-Button.

## Schritt 4 — Health prüfen

```bash
curl https://mcp.firma.de/healthz
# {"status":"ok"}

curl https://mcp.firma.de/.well-known/oauth-authorization-server
# JSON mit issuer, authorization_endpoint, token_endpoint, registration_endpoint
```

## Schritt 5 — Clients konfigurieren

Beim ersten Aufruf leitet der Client via Discovery + DCR durch den OAuth-Flow.
Jeder Kollege loggt sich mit seinem Atlassian-Account ein — die Tokens werden
pro Session vom OAuth-Proxy verwaltet.

> **Ersetze** in den folgenden Snippets `https://mcp.firma.de/mcp` durch die
> produktive URL eurer Instanz (z. B. `https://jira-mcp.dokploy.q23.de/mcp`).

### One-Liner für den Kollegen-Rollout

#### Claude Desktop / Cowork

Fügt den Server zur Desktop-Config hinzu (merged — bestehende MCP-Server bleiben erhalten).
Voraussetzung: `jq` installiert (macOS: `brew install jq`, Debian/Ubuntu: `apt install jq`).

**macOS (Terminal / zsh / bash):**
```bash
MCP_URL="https://mcp.firma.de/mcp"
CFG="$HOME/Library/Application Support/Claude/claude_desktop_config.json"
mkdir -p "$(dirname "$CFG")" && [ -f "$CFG" ] || echo '{}' > "$CFG"
tmp=$(mktemp) && jq --arg url "$MCP_URL" '.mcpServers.atlassian = {url:$url}' "$CFG" > "$tmp" && mv "$tmp" "$CFG"
```

**Linux (bash):**
```bash
MCP_URL="https://mcp.firma.de/mcp"
CFG="$HOME/.config/Claude/claude_desktop_config.json"
mkdir -p "$(dirname "$CFG")" && [ -f "$CFG" ] || echo '{}' > "$CFG"
tmp=$(mktemp) && jq --arg url "$MCP_URL" '.mcpServers.atlassian = {url:$url}' "$CFG" > "$tmp" && mv "$tmp" "$CFG"
```

**Windows (PowerShell):**
```powershell
$McpUrl = "https://mcp.firma.de/mcp"
$Cfg = "$env:APPDATA\Claude\claude_desktop_config.json"
New-Item -ItemType Directory -Force -Path (Split-Path $Cfg) | Out-Null
if (-not (Test-Path $Cfg)) { '{}' | Set-Content $Cfg -Encoding UTF8 }
$j = Get-Content $Cfg -Raw | ConvertFrom-Json
if (-not $j.mcpServers) { $j | Add-Member mcpServers ([pscustomobject]@{}) -Force }
$j.mcpServers | Add-Member atlassian ([pscustomobject]@{url=$McpUrl}) -Force
$j | ConvertTo-Json -Depth 10 | Set-Content $Cfg -Encoding UTF8
```

Danach **Claude Desktop komplett beenden** (Cmd+Q / rechter Mausklick auf Tray-Icon → Quit) und neu starten. Beim ersten Tool-Call öffnet sich der Atlassian-Login.

---

#### Claude Code (CLI)

Einfachster Weg — mitgelieferter `claude`-CLI (plattform-unabhängig):

```bash
claude mcp add --transport http --scope user atlassian https://mcp.firma.de/mcp
```

Schreibt in `~/.claude/settings.json` (user-scope). Projekt-scope: `--scope project` → legt `.mcp.json` im aktuellen Projekt an.

**Alternativ per jq direkt in `~/.claude/settings.json` (macOS / Linux):**
```bash
MCP_URL="https://mcp.firma.de/mcp"
CFG="$HOME/.claude/settings.json"
mkdir -p "$(dirname "$CFG")" && [ -f "$CFG" ] || echo '{}' > "$CFG"
tmp=$(mktemp) && jq --arg url "$MCP_URL" '.mcpServers.atlassian = {type:"http", url:$url}' "$CFG" > "$tmp" && mv "$tmp" "$CFG"
```

**Windows (PowerShell) direkter Weg:**
```powershell
$McpUrl = "https://mcp.firma.de/mcp"
$Cfg = "$env:USERPROFILE\.claude\settings.json"
New-Item -ItemType Directory -Force -Path (Split-Path $Cfg) | Out-Null
if (-not (Test-Path $Cfg)) { '{}' | Set-Content $Cfg -Encoding UTF8 }
$j = Get-Content $Cfg -Raw | ConvertFrom-Json
if (-not $j.mcpServers) { $j | Add-Member mcpServers ([pscustomobject]@{}) -Force }
$j.mcpServers | Add-Member atlassian ([pscustomobject]@{type="http"; url=$McpUrl}) -Force
$j | ConvertTo-Json -Depth 10 | Set-Content $Cfg -Encoding UTF8
```

Danach in der aktiven Claude-Code-Session neu starten (`/exit`, `claude`).

---

#### Cursor

Config-Pfad: `~/.cursor/mcp.json` (macOS/Linux) bzw. `%USERPROFILE%\.cursor\mcp.json` (Windows) — gleiche Struktur wie Claude Desktop. Beispiel-Inhalt:

```json
{
  "mcpServers": {
    "atlassian": {
      "url": "https://mcp.firma.de/mcp"
    }
  }
}
```

---

#### ChatGPT Connector

URL eintragen: `https://mcp.firma.de/mcp` — Discovery läuft automatisch.

---

## Betrieb

| Aktion | Ort |
| --- | --- |
| Logs | Dokploy UI → Application → Logs |
| Redeploy nach Upstream-Sync | Dokploy UI → Application → **Deploy** |
| Health | `GET /healthz` (regelmäßig im Log-Monitoring sichtbar) |
| Read-only schalten | `READ_ONLY_MODE=true` in Env + Redeploy |
| Tool-Scope einschränken | `TOOLSETS=` oder `ENABLED_TOOLS=` in Env |

## Sicherheit

- **Secrets** (`CLIENT_SECRET`, Access-Tokens): ausschließlich in Dokploy-Env,
  niemals in `.env` commiten.
- `ATLASSIAN_OAUTH_ALLOWED_CLIENT_REDIRECT_URIS` einschränken (Standard im
  `.env.example` erlaubt lokale Clients + ChatGPT-Connector).
- `ATLASSIAN_OAUTH_REQUIRE_CONSENT=true` lassen → Nutzer sieht Scope-Zustimmung.
- Bei sensiblen Projekten: `JIRA_PROJECTS_FILTER` / `CONFLUENCE_SPACES_FILTER`
  setzen, damit Suchen nur in erlaubten Projekten/Spaces laufen.

## Upstream-Sync (Fork)

Diese Verzeichnis-Struktur (`dokploy/`) ist additiv — keine Upstream-Dateien
werden geändert (außer einem `.gitignore`-Zusatz). Ein
`git fetch upstream && git merge upstream/main` sollte konfliktfrei laufen.

## Troubleshooting

| Symptom | Ursache / Fix |
| --- | --- |
| `invalid_redirect_uri` bei OAuth-Login | Callback-URL in Atlassian-App ≠ `ATLASSIAN_OAUTH_REDIRECT_URI` |
| 401 trotz korrektem Login | `ATLASSIAN_OAUTH_CLOUD_ID` falsch oder Scopes fehlen in der Atlassian-App |
| Client findet keinen OAuth-Endpoint | `PUBLIC_BASE_URL` fehlt, hat trailing slash, oder ist nicht HTTPS |
| Health schlägt fehl | Start-Phase abwarten; Logs in Dokploy prüfen |
| Tools fehlen | `TOOLSETS` / `ENABLED_TOOLS` zu restriktiv gesetzt |

Weiterführend:
- Upstream-Doku HTTP-Transport: `docs/http-transport.mdx`
- Auth-Methoden im Detail: `docs/authentication.mdx`
- Alle Env-Variablen: `.env.example` im Repo-Root
