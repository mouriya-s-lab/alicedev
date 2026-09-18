# alicedev infrastructure facts

Read-only investigation for `nekoringo2`, Cloudflare/IaC, local tooling, and existing public subdomains. No server/IaC changes, DNS writes, Telegram sends, Docker pulls/builds, or OrbStack resource starts were performed.

## 1. `nekoringo2` host and ingress

### Identity, OS, capacity

Command:

```sh
ssh -o BatchMode=yes -o ConnectTimeout=10 nekoringo2 'bash -s' <<'REMOTE'
hostname; id; uname -a; uname -m; cat /etc/os-release
ip -br addr; ip route
curl -sS --connect-timeout 10 --max-time 20 https://api.ipify.org
REMOTE
```

Observed output (secret-free):

```text
hostname: Nekoringo-Test
uid=0(root) gid=0(root) groups=0(root)
PRETTY_NAME="Debian GNU/Linux 13 (trixie)"
DEBIAN_VERSION_FULL=13.7
Linux Nekoringo-Test 6.12.90+deb13-cloud-amd64 #1 SMP PREEMPT_DYNAMIC Debian 6.12.90-1 (2026-05-22) x86_64 GNU/Linux
arch=x86_64
eth0 UP 160.191.41.242/28
public egress=160.191.41.242
default via 160.191.41.241 dev eth0
```

CPU/RAM/disk command:

```sh
ssh nekoringo2 'lscpu | sed -n -E "s/^(Architecture|CPU\\(s\\)|Model name|Vendor ID|Thread\\(s\\) per core|Core\\(s\\) per socket|Socket\\(s\\)):/\\1=/p"; free -h; df -hP / /var/lib/docker'
```

Observed output:

```text
Architecture=x86_64
CPU(s)=4
Vendor ID=GenuineIntel
Model name=Intel(R) Xeon(R) Gold 6138 CPU @ 2.00GHz
Thread(s) per core=1; Core(s) per socket=4; Socket(s)=1
Mem: total 7.8Gi, used 1.8Gi, free 1.7Gi, buff/cache 4.7Gi, available 6.0Gi
Swap: 0B total
/dev/sda1 197G total, 12G used, 178G available, 7% used (for / and /var/lib/docker)
```

### Docker, Compose, and containers

Command:

```sh
ssh nekoringo2 'command -v docker; docker version --format "client={{.Client.Version}} server={{.Server.Version}}"; docker compose version; docker info --format "root={{.DockerRootDir}} storage={{.Driver}}"; docker compose ls; docker ps; docker ps -a; docker system df'
```

Observed output:

```text
/usr/bin/docker
client=29.8.1 server=29.8.1
Docker Compose version v5.5.1
root=/var/lib/docker storage=overlayfs
NAME STATUS CONFIG FILES
(no compose projects)
(no running containers; `docker ps -a` also returned no container rows)
Images: 1 total / 0 active / 1.485GB; image=agent-chrome:test153
Containers: 0 total / 0 active / 0B
```

The host has a working Docker Engine and Compose plugin, but no running container or Compose project at probe time.

### Listening ports

Command: `ssh nekoringo2 'ss -tlnp'`.

Observed listeners, grouped without changing the `ss` result:

```text
0.0.0.0:22 and [::]:22       sshd
0.0.0.0:5355 and [::]:5355   systemd-resolve
*:4222                        autobio-hub
127.0.0.54:53                systemd-resolve
127.0.0.53%lo:53             systemd-resolve
127.0.0.1:3300              MainThread (Node web server)
127.0.0.1:4232,4242         autobio-hub
127.0.0.1:8222,8223         autobio-hub
127.0.0.1:8232,8233         autobio-hub
127.0.0.1:8242,8243         autobio-hub
127.0.0.1:7301,7302         Paseo Daemon
127.0.0.1:7801,7802         Paseo Daemon
127.0.0.1:37363,37959        Paseo Daemon
127.0.0.1:41043             Paseo Daemon
127.0.0.1:43391              Paseo Daemon
127.0.0.1:43619,43621        Paseo Daemon
```

There was no listener on TCP 80 or 443. The direct process scan showed several host-side Paseo daemons/supervisors and Node workers, not an HTTP reverse proxy.

### GPU

Command: `ssh nekoringo2 'lspci | grep -Ei "vga|display|3d"; ls -la /dev/dri'`.

Observed output:

```text
lspci_no_vga_display_3d
/dev/dri=absent
```

No visible PCI VGA/display/3D device and no `/dev/dri` device directory were present.

### Firewall and public 80/443 reachability

Command:

```sh
ssh nekoringo2 'ufw status verbose; iptables -S; ip6tables -S; nft list ruleset'
for p in 80 443; do nc -vz -w 5 160.191.41.242 "$p"; done
curl -k -sS --connect-timeout 10 --max-time 20 -o /dev/null -w 'http_code=%{http_code}\n' http://160.191.41.242/
curl -k -sS --connect-timeout 10 --max-time 20 -o /dev/null -w 'http_code=%{http_code}\n' https://160.191.41.242/
```

Observed output:

```text
ufw=absent; systemd units ufw/firewalld/nftables=inactive
iptables: -P INPUT ACCEPT; -P FORWARD DROP; -P OUTPUT ACCEPT
ip6tables: -P INPUT ACCEPT; -P FORWARD ACCEPT; -P OUTPUT ACCEPT
nftables contained only Docker NAT/filter chains; no host 80/443 allow/listener rule
nc port 80: Connection refused
nc port 443: Connection refused
curl http://160.191.41.242/: http_code=000 (connect failed)
curl https://160.191.41.242/: http_code=000 (connect failed)
```

The host firewall does not reject inbound packets via a UFW/firewalld policy, but there is no public web listener, and both external TCP probes were refused. Therefore public HTTP(S) ingress is not currently usable.

### Existing reverse proxy

Command: `ssh nekoringo2 'command -v caddy nginx traefik haproxy; systemctl is-active caddy nginx traefik haproxy; pgrep -a -f "(caddy|nginx|traefik|haproxy)"'`.

Observed output: all four `command -v` lookups were empty, all four systemd units reported `inactive`, and the process scan found no proxy process. No Caddy, Nginx, Traefik, or HAProxy was detected.

### Registry reachability and pull readiness

Command:

```sh
ssh nekoringo2 'getent hosts registry.237575.xyz; curl -sS --connect-timeout 10 --max-time 20 -o /dev/null -w "https_code=%{http_code} remote_ip=%{remote_ip} tls=%{ssl_verify_result}\n" https://registry.237575.xyz/v2/; test -f /root/.docker/config.json && jq ... /root/.docker/config.json || echo none'
```

Observed output:

```text
192.99.9.212 registry.237575.xyz
https_code=401 remote_ip=192.99.9.212 tls=0
docker config=none
```

The HTTPS registry endpoint is reachable and its TLS certificate verified (`tls=0`), but `/v2/` correctly requires authentication (`401`). No root/user Docker config with registry credentials was found, and no registry image was present to run a read-only `docker manifest inspect`. The repository's registry convention states that pulls require `docker login registry.237575.xyz` with the Keycloak service-account token (`pve-vctcn/.claude/skills/docker-baseline/SKILL.md:95-108`). Thus network reachability is proven, while an authenticated private-image pull is **not** currently proven and is expected to fail until credentials are injected.

### Netbird/Tailscale

Command: `ssh nekoringo2 'command -v netbird tailscale; systemctl is-active netbird tailscaled tailscale'`.

Observed output: both binaries were absent; `netbird`, `tailscaled`, and `tailscale` units were `inactive`. No Netbird or Tailscale client is present on this host.

## 2. Cloudflare tokens, zones, and DNS authority

### Secret locations and conventions

`homelab-tf/AGENTS.md:22-30` says `_shared/ansible/secrets.yml` is SOPS+age encrypted and should be verified with `sops -d ... > /dev/null`; `pve-vctcn/AGENTS.md:1-18` identifies the repo's SOPS secret convention. The encrypted field locations are:

- `/Users/mouriya/Ext/code/homelab-tf/_shared/ansible/secrets.yml:38` — `cloudflare_api_token: ENC[...]`.
- `/Users/mouriya/Ext/code/pve-vctcn/_shared/secrets/secrets.yml:8` — `cloudflare_api_token: ENC[...]`.

Safe extraction/query command used (the token was held only in a shell variable and never printed):

```sh
for file in \
  /Users/mouriya/Ext/code/homelab-tf/_shared/ansible/secrets.yml \
  /Users/mouriya/Ext/code/pve-vctcn/_shared/secrets/secrets.yml; do
  TOKEN=$(sops -d "$file" | yq -r '.cloudflare_api_token')
  curl -sS -H "Authorization: Bearer $TOKEN" \
    'https://api.cloudflare.com/client/v4/user/tokens/verify'
  curl -sS -H "Authorization: Bearer $TOKEN" \
    'https://api.cloudflare.com/client/v4/zones?per_page=100' \
    | jq '{success,errors,result:[.result[]|{name,id,status,account_id:(.account.id//null),account_name:(.account.name//null)}]}'
  unset TOKEN
done
```

Both decryptions reported `decrypt=ok token=present (value redacted)`. An in-process comparison reported `token_values_equal=yes`; both SOPS files contain the same underlying Cloudflare API token.

### Zone coverage and IDs

Both `/user/tokens/verify` calls returned HTTP 200 with `status=active` and `This API Token is valid and active`.

Both `/zones?per_page=100` calls returned HTTP 200 and the same four active zones under account `e19fce161213be38816bcceb414414bf` (`Dai.ariose@hotmail.com's Account`):

| Zone | Zone ID | API result |
|---|---|---|
| `237575.xyz` | `80d0986be685a00555a5189b94f8f80d` | active, visible |
| `237676.xyz` | `923736ffc17fb96115d1ecf13451d4a0` | active, visible |
| `mouriya.moe` | `1997789b0a1c5d921ee292ffba88eb79` | active, visible |
| `vctcn.sbs` | `33adab9a3b88d7bd38a97f1d2e28e698` | active, visible |

Read-only DNS checks were also made against both candidate zones (and the two other returned zones):

```text
SOURCE=homelab; 237575.xyz DNS_GET=HTTP=200 success=true total_count=15
SOURCE=homelab; 237676.xyz DNS_GET=HTTP=200 success=true total_count=0
SOURCE=pve;     237575.xyz DNS_GET=HTTP=200 success=true total_count=15
SOURCE=pve;     237676.xyz DNS_GET=HTTP=200 success=true total_count=0
```

This definitively establishes current token validity and Zone read visibility for **both** requested zones. It also shows that the token can enumerate/read more zones than the narrow scope described by the IaC documentation.

### Permission scope (separate declared policy from observed read access)

Authoritative repo policy says:

- `pve-vctcn/.claude/rules/provider-env.md:48-50` and `pve-vctcn/apps/dns/README.md:40-43`: minimum intended scope is `Zone.Zone Read` + `Zone.DNS Edit` for **`237575.xyz` only**; no Account-level permission is required by that workspace.
- `homelab-tf/.claude/rules/r2-backend.md:14-16` and `homelab-tf/cf-ingress/providers.tf:27-32`: the same token is used by `cf-ingress` and additionally needs `Account.Cloudflare Tunnel: Edit`, plus `Zone.DNS: Edit` and `Zone.Zone: Read` on **`237575.xyz`**. The rule records a historical POST/DELETE tunnel round-trip as the edit-scope verification.

Definitive architecture boundary:

- `237575.xyz`: DNS read and the intended DNS edit scope are declared; homelab tunnel work additionally uses Cloudflare Tunnel Edit. This is the only zone for which either repo declares DNS edit authority.
- `237676.xyz`: current token has observed Zone visibility/read (HTTP 200 for zone and DNS-record GET), but **neither repo declares DNS edit authority for this zone**. The Cloudflare read-only verify endpoint does not expose the token's full permission policy, and no write probe was attempted because this assignment is read-only. Treat `237676.xyz` as read-visible but not IaC-approved for DNS writes; do not infer edit permission from zone listing.

### DNS records and where a new subdomain belongs

A repository-wide search for `237676.xyz` returned `No matches found` in both IaC repos. Existing `237575.xyz` authority is split intentionally:

1. **Vctcn/NPM public services — `pve-vctcn/apps/dns/main.tf`**
   - `pve-vctcn/apps/dns/main.tf:31-40` sets `local.zone_name = "237575.xyz"` and derives `zone_id` from that zone.
   - `pve-vctcn/apps/dns/main.tf:128-135` declares the proxied `*.237575.xyz` wildcard CNAME to the apex.
   - `pve-vctcn/apps/dns/main.tf:172-179` declares the DNS-only `registry.237575.xyz` A record.
   - `pve-vctcn/apps/dns/README.md:24-34` documents that NPM-fronted services including `img` inherit wildcard routing, while `registry` has an explicit DNS-only record.
   - `pve-vctcn/apps/dns/README.md:60-65` says records must be added to `main.tf` and applied with OpenTofu; the dashboard is read-only territory.

2. **Homelab CF Tunnel services — `homelab-tf/cf-ingress/main.tf`**
   - `homelab-tf/cf-ingress/main.tf:25-40` also derives the `237575.xyz` zone ID.
   - `homelab-tf/cf-ingress/main.tf:104-114`, `:163-170`, `:223-230`, and `:273-280` declare tunnel-egress CNAMEs for `hapi`, `runner-canary`, `hello`, and `paseo` respectively.
   - `homelab-tf/cf-ingress/main.tf:5-15` explicitly says tunnel records belong in this workspace while apex/wildcard/MX/gray-cloud vctcn records stay in `pve-vctcn/apps/dns`.

Decision for a later implementer:

- New **vctcn-hosted/NPM** subdomain under `237575.xyz`: use the existing wildcard if possible (usually add only the manual NPM proxy host); if a dedicated A/AAAA record is required, add it declaratively in `pve-vctcn/apps/dns/main.tf`, not by ad-hoc API.
- New **homelab/Paseo tunnel** subdomain under `237575.xyz`: add the tunnel object/config/CNAME in `homelab-tf/cf-ingress` and follow its SOPS/connector path; do not add the tunnel CNAME in `pve-vctcn/apps/dns`.
- New record under **`237676.xyz`**: no existing TF declaration or ownership was found. The only currently available path is a separately authorized/manual Cloudflare API/dashboard write, or first establishing a new owning OpenTofu workspace/token scope; no change was made here.

## 3. Local Telegram/GitHub/Docker tooling

### Telegram CLI

Command: `which tg tdl telegram-cli tgcli` (implemented with `command -v`) plus each installed binary's `--help`.

Observed output:

```text
tg -> /Users/mouriya/.local/bin/tg
/usr/local/bin? tdl -> absent; telegram-cli -> absent; tgcli -> absent
```

`tg` is a symlink to `/Users/mouriya/.local/share/uv/tools/kabi-tg-cli/bin/tg`; its script imports `tg_cli.cli.main`. Matching Telegram-named paths under `~/.config`, `~/.local/share`, and `~/.local/state` found no session/config file (the only matching path was the installed uv tool directory).

Exact command help observed:

```text
Usage: tg send [OPTIONS] CHAT MESSAGE
Usage: tg history [OPTIONS] CHAT
Usage: tg recent [OPTIONS]
Usage: tg status [OPTIONS]
Usage: tg whoami [OPTIONS]
```

Relevant options from `--help`:

```text
tg send: -r/--reply INTEGER, --no-preview, --json, --yaml
tg history: -n/--limit INTEGER, --json, --yaml
tg recent: -c/--chat TEXT, -s/--sender TEXT, --hours INTEGER,
            --sync-first, --sync-limit INTEGER, -n/--limit INTEGER,
            --json, --yaml
tg status/whoami: --json, --yaml
```

Verbatim commands for later testing (not run because this assignment is read-only):

```sh
tg send 8838419390 '/需求 <text>'
tg history -n 20 8838419390
tg recent --chat 8838419390 --limit 20
```

Authentication check command and output:

```sh
tg status </dev/null
# warning: default Telegram Desktop api_id=2040
# Please enter your phone (or bot token):
# ok: false; error.code=auth_error; error.message=EOF when reading a line

tg whoami </dev/null
# same auth_error/EOF result
```

Therefore the executable is installed and exposes the required send/read syntax, but the current local invocation is **not logged in/non-interactively usable**. This conflicts with the older handoff statement that the local `tg` was already logged in (`alicedev/HANDOFF.md:81-85`); the current CLI runtime probe is the fresher observed fact. No phone, bot token, or message was entered.

### GitHub auth

Command: `gh auth status`.

Observed output: `github.com` reports `Logged in ... account RiriAgent (keyring)` and `Active account: true`; `dt-activenetwork` and `Mouriya-Emma` are present but `Active account: false`. This satisfies the required active-account check without switching accounts.

### Local Docker/OrbStack build capability

Command: `which docker orb; docker version; docker compose version; docker buildx version; docker buildx ls; docker context ls; orb version; orb list; orb status`.

Observed output:

```text
docker=/usr/local/bin/docker
orb=/opt/homebrew/bin/orb
Docker client/server=29.4.0
Docker Compose=v5.1.2
buildx=v0.33.0
Buildx nodes: default and orbstack both running; linux/amd64 and linux/arm64 listed among platforms
Docker contexts: default and active orbstack
OrbStack=2.2.3; status=Running
```

Local image building is available. No build was started.

## 4. Existing `registry.237575.xyz`, `img.237575.xyz`, and subdomain conventions

The `internal-services` skill was read first (`skill://internal-services`). Its authority map says `pve-vctcn` owns the registry and Cloudflare DNS, while homelab tunnel records belong to `homelab-tf`; it also says to derive current state from repo declarations/live sources rather than a stored count.

### Registry evidence

Read-only Cloudflare API query using the SOPS token:

```text
NAME=registry.237575.xyz
success=true; type=A; content=192.99.9.212; proxied=false; ttl=300
```

This matches `pve-vctcn/apps/dns/main.tf:166-179`. From `nekoringo2`, `getent hosts registry.237575.xyz` returned `192.99.9.212`, and `curl https://registry.237575.xyz/v2/` returned `HTTP 401`, proving the endpoint exists, resolves, and requires registry auth.

### Image host evidence

The same Cloudflare API query returned no explicit `img.237575.xyz` record (`success=true; result=[]`). `pve-vctcn/apps/dns/main.tf:122-135` and `pve-vctcn/apps/dns/README.md:30-32` explain why: `*.237575.xyz` is a proxied wildcard, and `img` is an NPM-fronted service inheriting that wildcard rather than having its own A record. Remote DNS returned Cloudflare anycast IPv6 addresses for `img.237575.xyz`; local `curl -k https://img.237575.xyz/` returned `HTTP 200`.

Thus both requested public names already work:

- `registry.237575.xyz`: explicit DNS-only A → `192.99.9.212`; registry API responds `401` without credentials.
- `img.237575.xyz`: no explicit record; inherited proxied wildcard → NPM/public service; HTTPS probe returned `200`.

### Existing subdomain conventions

- `pve-vctcn/apps/dns` owns `237575.xyz` apex/wildcard/MX/gray-cloud vctcn records (`pve-vctcn/.claude/rules/state-authority-map.md:7-12,17-27`).
- NPM is the manual public ingress for vctcn services; a new vctcn hostname normally needs an NPM proxy host and only needs a separate DNS record when wildcard routing is insufficient (`pve-vctcn/.claude/rules/topology.md:54-68`).
- `registry.237575.xyz` is deliberately DNS-only to avoid Cloudflare's 100 MB request-body cap (`pve-vctcn/apps/dns/main.tf:166-179`).
- Homelab tunnel names such as `paseo.237575.xyz` are owned by `homelab-tf/cf-ingress` and route through `*.cfargotunnel.com`, not through vctcn NPM (`pve-vctcn/.claude/rules/topology.md:70-78`; `homelab-tf/cf-ingress/main.tf:233-280`).
- No `237676.xyz` declaration exists in either IaC repo; any use of that zone needs a new explicit ownership/declaration decision.
