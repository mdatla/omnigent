# Omnigent on Unraid — one compose stack, fork-built images

Migration from the hand-managed setup (Unraid Docker template + `startup-shim.sh`
+ patch kit + `omnigent upgrade` inside the container) to a single compose stack
whose images are built from the fork `mdatla/omnigent` and refreshed daily from
upstream.

**Target end state:** one `docker compose` command updates everything.

```bash
docker compose pull && docker compose up -d
```

---

## 1. What changes, and what stops existing

| Before | After |
|---|---|
| Unraid Docker template `omnigent-agent` | `omnigent-unraid` service in this stack |
| `startup-shim.sh` intercepting `omnigent` on PATH | **gone** — no shim, no PATH surgery |
| Sync + backup daemons forked at boot from PID 1 | `obsidian-sync` and `vault-backup` services, supervised by `restart: unless-stopped` |
| Server on `fwrk-desktop1` (systemd + tmux + cloudflared) | `omnigent` service + `cloudflared` service, same stack |
| `omnigent upgrade` inside the container (lost on every recreate) | image tag is the version. New build → `pull` → `up -d` |
| Patch kit `0001`/`0002`/`0003` applied post-upgrade | **retired** — see §7 |
| Postgres on the old server, if any | sqlite file in the `omnigent-data` volume |

The three failure modes that motivated this are all structural, not procedural:

1. **The shim could be silently unhooked.** `uv tool install` rewrote
   `/usr/local/bin/omnigent` on every upgrade, and the boot path depended on that
   symlink pointing at the shim. A recreate also wiped `/opt`, so the "installed"
   version silently reverted. Both are gone: no symlink, no in-container install.
2. **Patches had to be re-applied by hand after every version bump.** They are
   now upstream (§7), so there is nothing to re-apply.
3. **`command:` vs `entrypoint:` argv traps.** The host image is CMD-only —
   docker execs `omnigent` directly — so `command:` always spells out the full
   argv. There is no wrapper to get confused about.

## 2. Prerequisites

- Docker + Compose v2 on the Unraid host (Unraid 6.12+ ships both).
- The fork **`mdatla/omnigent`** (public, exists today).
- Repo setting: **Settings → Actions → General → Workflow permissions** =
  *Read and write permissions*. The sync job pushes to `main`.
- GHCR packages. On first push they are created **private**. Either flip each of
  the three packages to public (Package settings → Change visibility), or log in
  on the Unraid host:

  ```bash
  echo "$GHCR_PAT" | docker login ghcr.io -u mdatla --password-stdin
  ```

  Public is simpler for a homelab; private needs a PAT with `read:packages`.

## 3. Phase 1 — the fork (no downtime)

Files in this directory are laid out to drop straight into the fork root:

```
<fork>/
├── .github/workflows/unraid-images.yml
└── deploy/unraid/
    ├── docker-compose.yaml
    ├── .env.example
    ├── Dockerfile.obsidian-sync
    └── README.md
```

Push them:

```bash
git clone git@github.com:mdatla/omnigent.git
cd omnigent
git remote add upstream https://github.com/omnigent-ai/omnigent.git 2>/dev/null || true
cp -r /path/to/these/files/. .
git add .github/workflows/unraid-images.yml deploy/unraid
git commit -m "deploy/unraid: single-stack compose + daily fork sync and image build"
git push origin main
```

Then trigger the first build and watch it:

```bash
gh workflow run unraid-images.yml --repo mdatla/omnigent
gh run watch --repo mdatla/omnigent
```

Expected output: `sync` merges upstream (or reports "nothing to merge"), then
`build` pushes six tags (three images × `latest` + `sha-<12>`).

Verify the images exist before touching the running deployment:

```bash
docker manifest inspect ghcr.io/mdatla/omnigent-server:latest >/dev/null && echo server-ok
docker manifest inspect ghcr.io/mdatla/omnigent-host:latest   >/dev/null && echo host-ok
docker manifest inspect ghcr.io/mdatla/omnigent-obsidian-sync:latest >/dev/null && echo sync-ok
```

## 4. Phase 2 — migrate the server (short downtime)

Order matters: the tunnel must keep pointing somewhere valid until the new
server is verified.

**2.1 Back up the old server** (on `fwrk-desktop1`):

```bash
systemctl --user stop omnigent-server
cp -a ~/.omnigent ~/.omnigent.bak.$(date +%Y%m%d-%H%M)
sqlite3 ~/.omnigent/chat.db ".backup $HOME/chat.db.bak.$(date +%Y%m%d-%H%M)" \
  || cp ~/.omnigent/chat.db ~/chat.db.bak.$(date +%Y%m%d-%H%M)
du -sh ~/.omnigent/artifacts 2>/dev/null
```

**2.2 Seed the volume on Unraid.** Migrations run automatically on first boot
and are forward-only, so the copy below is the rollback artifact — keep it.

```bash
cd /mnt/user/appdata/omnigent      # the directory holding deploy/unraid
docker volume create omnigent-data
docker run --rm -v omnigent-data:/data -v /mnt/user/appdata/omnigent/seed:/seed:ro \
  alpine sh -c 'mkdir -p /data/artifacts && cp /seed/chat.db /data/omnigent.db && cp -a /seed/artifacts/. /data/artifacts/ 2>/dev/null; ls -la /data'
```

Copy `chat.db.bak.*` and `~/.omnigent/artifacts` from the old server into
`/mnt/user/appdata/omnigent/seed/` first (`scp`). If you prefer a clean start
over carrying history, skip the copy — the server will create an empty sqlite
file on boot.

**2.3 Create `.env` and start only the server:**

```bash
cp deploy/unraid/.env.example .env
$EDITOR .env      # COOKIE_SECRET (openssl rand -hex 32), PUBLIC_URL, TUNNEL_TOKEN
docker compose --env-file .env -f deploy/unraid/docker-compose.yaml up -d omnigent
docker compose -f deploy/unraid/docker-compose.yaml logs -f omnigent
```

Wait for migrations to finish and the healthcheck to pass, then:

```bash
curl -s http://127.0.0.1:8000/health
curl -s http://127.0.0.1:8000/api/version      # expect the fork's upstream version
```

**2.4 Repoint the tunnel.** In Cloudflare Zero Trust → Networks → Tunnels →
your tunnel → Public Hostname → edit the service URL:

```
http://omnigent:8000
```

(the compose service name — not `localhost:8000`, which was the old
`fwrk-desktop1` ingress). Then start the connector and verify end to end:

```bash
docker compose -f deploy/unraid/docker-compose.yaml up -d cloudflared
curl -s https://omni.mdatla.us/health
curl -s https://omni.mdatla.us/api/version
```

**Rollback:** point the tunnel back at the old server's ingress, restart
`cloudflared`, and `systemctl --user start omnigent-server` on `fwrk-desktop1`.
The old `~/.omnigent` was never modified.

## 5. Phase 3 — migrate the host (the Unraid container swap)

**5.1 Stop the old container and prevent it from coming back.** Unraid's
autostart would otherwise resurrect it and fight the new one for the same
`host_id` (`b5cd3fdc9c8a4f858a70322eb29816c1`) and the same `/root` mount.

```bash
docker stop omnigent-agent
# Unraid: Docker tab -> omnigent-agent -> Edit -> Autostart: Off -> Apply
# (or delete the container after the new one is verified)
```

**5.2 Start the host service:**

```bash
docker compose -f deploy/unraid/docker-compose.yaml up -d omnigent-unraid
docker logs -f omnigent-unraid
```

Expect a clean connect, with **no** shim involvement:

```
Connecting to https://omni.mdatla.us as 'omnigent-unraid' (b5cd3fdc9c8a4f858a70322eb29816c1)
✓ Connected as ... Listening for sessions
```

`/root/.omnigent/config.yaml` is preserved by the bind mount, so the host keeps
its identity and its session history. Absolute paths inside it (`/root/...`)
remain valid because the mount path is unchanged.

**5.3 Start the sidecars:**

```bash
docker compose -f deploy/unraid/docker-compose.yaml up -d obsidian-sync vault-backup
docker compose -f deploy/unraid/docker-compose.yaml ps
docker logs --tail 20 $(docker compose -f deploy/unraid/docker-compose.yaml ps -q obsidian-sync)
```

Check that the vault actually syncs, and that `vault-backup` produces a commit
on its normal interval. Both now restart forever on their own; the old PID-1
forking and single-instance pidfile guards are unnecessary.

`backup.sh` needs `git` and `flock`; both are present in the host image
(`git` is installed explicitly, `flock` ships in `util-linux`, which is a
required-priority package of the image's Debian slim base). Confirm on first
boot rather than assuming:

```bash
docker compose -f deploy/unraid/docker-compose.yaml run --rm --entrypoint sh vault-backup \
  -c 'command -v git flock && echo deps-ok'
```

**5.4 Clean up the old machinery** (optional but recommended):

```bash
# Nothing invokes the shim any more; deleting it prevents future confusion.
rm -f /usr/local/bin/omnigent     # inside the OLD container only — it is gone
                                  # with the container anyway
# On the Unraid host:
#   - remove the base backup if you want: /mnt/user/.../omnigent-agent-old
```

`/root/obsidian-sync/startup-shim.sh` and `container-entry.sh` stay on disk
(they are inside the persistent `/root` share) but are inert. Delete them once
you are confident, and remove the `--entrypoint` Extra Parameter from the old
template — compose does not read Unraid template settings at all.

## 6. Updating, pinning, rolling back

**Normal update** (the entire ritual):

```bash
cd /mnt/user/appdata/omnigent
docker compose pull && docker compose up -d
docker compose ps
```

**Pin for reproducibility / rollback** — every build also publishes
`sha-<12>`:

```bash
sed -i 's/^OMNIGENT_TAG=.*/OMNIGENT_TAG=sha-1a2b3c4d5e6f/' .env
docker compose pull && docker compose up -d
```

One tag drives server + host + sidecar on purpose: the host's usage-baseline
contract and the server's baseline plumbing must match. Mixing versions across
that boundary is what produced the missing-message and cost-reset symptoms in
the old system.

**Optional: automatic pulls.** Compose has no scheduler, so if you want
pull-on-a-timer add Watchtower scoped to this stack:

```yaml
  watchtower:
    image: containrrr/watchtower
    restart: unless-stopped
    volumes: ["/var/run/docker.sock:/var/run/docker.sock"]
    environment:
      WATCHTOWER_CLEANUP: "true"
      WATCHTOWER_LABEL_ENABLE: "true"
      WATCHTOWER_SCHEDULE: "0 0 4 * * *"
```

and label each service with `com.centurylinklabs.watchtower.enable=true`. This
is the only component that watches anything; the fork workflow is what makes new
`latest` digests appear.

## 7. The patch kit is retired

The old kit existed to fix `$0.00` cost on this host. Upstream has since taken
those fixes:

| Old patch | Status |
|---|---|
| `0001` Fireworks catalog pricing | **merged upstream** (marker present in pristine upstream at `ad495601`) |
| `0002` pi-native forwards Pi's cost | **merged upstream** |
| `0003` pi-native restores cost baseline | **no longer needed once the server is current** |

`0003` existed because the old server (0.12) sent no usage baseline, so a Pi
relaunch reset the cumulative cost to zero. A current server sends
`NativeUsageBaseline.cumulative_cost_usd` whenever the harness reports complete
cost, which the fork's server image now does. Verified in the installed package:
`server/schemas.py: NativeUsageBaseline.cumulative_cost_usd`, populated in
`server/routes/_sessions/orchestration.py`.

Consequently the fork carries **no source customization** — it is upstream plus
this additive `deploy/unraid/**` directory and the workflow. That is what makes
the daily merge conflict-free. If you later need a source-level patch, add it as
a normal commit on fork `main`; the sync job will merge upstream under it and
**fail loudly** instead of guessing if a conflict appears.

## 8. Known limits

- **amd64 only.** The workflow builds `linux/amd64` to stay inside the free CI
  budget. Add `linux/arm64` to the three `platforms:` lines if an arm64 host
  joins.
- **Not validated by Docker here.** These files were written and YAML-parsed in
  a container without a Docker socket; `docker compose config` on the Unraid box
  is the first real validation. Run it before `up -d`:

  ```bash
  docker compose -f deploy/unraid/docker-compose.yaml config >/dev/null && echo config-ok
  ```

- **git history in the vault share.** `vault-backup` commits inside
  `/root/workspace/...`; running it as its own container is equivalent to the
  old in-container daemon, but if you ever run two copies the repo lock will
  complain. Only one stack should mount `/root`.
- **Tunnel ingress is manual.** The public hostname → `http://omnigent:8000`
  mapping is a Cloudflare-side change this repo cannot make for you.
