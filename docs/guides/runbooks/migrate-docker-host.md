# Runbook: Migrate named volumes to another Docker host (offline export/import)

**Trigger:** you are moving an instance to a new Docker host and need its named volumes on the new host. The two hosts do **not** have to be reachable at the same time: you export each volume to a tarball on the source, copy the files over by any means (scp, rsync, USB disk, object storage), and import them on the target.

**Scope:** named volumes only. The **database is out of scope**. Migrate it with its own runbook first ([`migrate-sqlite-to-postgres.md`](migrate-sqlite-to-postgres.md), or `pg_dump`/restore as in the ops manual). This runbook skips `*_postgres_data` by default.

**Impact:** writes to the exported volumes are lost after the export (step 2). Stop master and the agents before exporting, and keep them stopped until the target is up.

> Never run `docker compose down -v`, `docker volume rm`, or `docker volume prune` on the source until the target has been verified and has run for a while. The source volumes are your rollback.

---

## 0. Which volumes

| Volume | Contents | Migrate? |
|---|---|---|
| `<project>_master_data` | Attachments, DB snapshots, and `master_data.db` (SQLite installs) | yes |
| `codex-claude-<workspace_id>` | Claude Code session state, one per workspace | yes |
| `codex-codex-<workspace_id>` | Codex CLI state, one per workspace | yes |
| `agent-workspace-<workspace_name>` | Cloned repo, including **uncommitted agent work** | yes |
| `<project>_postgres_data` | Bundled Postgres data dir | **no**: handled by the DB migration |

`<project>` is `codex-slack` for staging/prod singletons, or the branch slug for a dev stack. Agent containers do not need to move. When master starts on the target, it recreates one container per active workspace (`_respawn_agents` in `src/master/main.py`) and attaches it to these volumes **by name**. That is why the volume names must stay exactly the same.

---

## 1. On the source host: list the volumes

```bash
PROJECT=codex-slack
docker volume ls -q \
  | grep -E "^(${PROJECT}_master_data|codex-claude-|codex-codex-|agent-workspace-)" \
  > volumes.txt
cat volumes.txt
```

Edit `volumes.txt` if you want to leave something out, for example workspaces you have archived.

## 2. On the source host: stop writers

```bash
docker stop ${PROJECT}-master-1
docker ps -q --filter name=codex-agent- | xargs -r docker stop
```

Use `stop`, not `down`/`rm`. That way `docker start` gives you a fast rollback.

## 3. On the source host: export each volume to a tarball

GNU tar with `--numeric-owner` keeps UIDs and permissions intact. The agents run as `appuser`, so ownership matters.

```bash
mkdir -p volume-export && cd volume-export
cp ../volumes.txt .
while read -r v; do
  echo "==> exporting $v"
  docker run --rm -v "$v":/from:ro -v "$PWD":/out debian:bookworm-slim \
    tar -C /from --numeric-owner -czpf "/out/$v.tar.gz" .
  # record the file count so the import can be checked
  docker run --rm -v "$v":/from:ro alpine sh -c 'find /from | wc -l' > "$v.count"
done < volumes.txt
sha256sum *.tar.gz > SHA256SUMS
ls -lh
```

You now have `volume-export/` containing one `<volume>.tar.gz` and one `<volume>.count` per volume, plus `volumes.txt` and `SHA256SUMS`.

Each tarball holds the volume's root (`.`), not a `from/` prefix, so you can also inspect it by hand with `tar tzf <file> | head`.

## 4. Move the files

Copy the whole `volume-export/` directory to the target host however you like:

```bash
scp -r volume-export/ ubuntu@<new-host>:~/          # or rsync, USB disk, S3, ...
```

These archives contain session state and repo clones that may include tokens. Treat them as secrets in transit and at rest, and delete the copies once you are done (step 7).

## 5. On the target host: verify and import

```bash
PROJECT=codex-slack
cd ~/volume-export
sha256sum -c SHA256SUMS            # must print OK for every file

while read -r v; do
  if docker volume inspect "$v" >/dev/null 2>&1; then
    echo "!! $v already exists on this host, skipping (inspect it before overwriting)"; continue
  fi
  # Compose-owned volumes get compose labels so `docker compose up` adopts them cleanly
  case "$v" in
    "${PROJECT}"_*) docker volume create \
        --label com.docker.compose.project="$PROJECT" \
        --label com.docker.compose.volume="${v#${PROJECT}_}" "$v" >/dev/null ;;
    *) docker volume create "$v" >/dev/null ;;
  esac
  echo "==> importing $v"
  docker run --rm -v "$v":/to -v "$PWD":/in:ro debian:bookworm-slim \
    tar -C /to --numeric-owner -xzpf "/in/$v.tar.gz"
  got=$(docker run --rm -v "$v":/to:ro alpine sh -c 'find /to | wc -l')
  [ "$got" = "$(cat "$v.count")" ] && echo "OK   $v ($got entries)" || echo "DIFF $v: expected $(cat "$v.count"), got $got"
done < volumes.txt
```

Every volume must report `OK`. If one shows `DIFF`, `docker volume rm` that volume on the **target** only, then re-run the loop. The loop skips volumes that already exist.

**Different project name on the target?** The compose volume must be called `<new-project>_master_data`. Set `PROJECT` to the new name and rename the tarball before importing, for example `mv codex-slack_master_data.tar.gz newproj_master_data.tar.gz`. Do the same for the `.count` file and the entry in `volumes.txt`. Then re-run `sha256sum` or skip the check for that file. Never rename the per-workspace volumes: master looks them up by fixed names.

## 6. Bring up the target and check

1. Deploy as usual on the new host. For staging/prod, point `STAGING_DOCKER_HOST`/`PROD_DOCKER_HOST` at it and run `just deploy <env> <tag>`. Keep the same project name.
2. `docker volume ls` shows no new, empty `*_master_data` volume next to the imported one. A second one means the project name did not match.
3. `docker logs ${PROJECT}-master-1 | grep master.respawned` shows one line per active workspace, and `docker ps --filter name=codex-agent-` lists them.
4. In the UI: attachments open, and an agent in an existing topic replies while keeping its earlier session context.
5. Spot-check a workspace clone for uncommitted work:
   `docker run --rm -v agent-workspace-<name>:/w:ro alpine ls -la /w`

## 7. Clean up

- Delete `volume-export/` on both hosts and on any intermediate storage.
- After the target has run cleanly for an agreed period, remove the old stack and then, one by one, the source volumes from `volumes.txt`.

## Rollback

On the source host, run `docker start ${PROJECT}-master-1`. Master respawns the agents against the untouched source volumes. Point clients back. Anything written on the target in the meantime does not carry back, unless you repeat this runbook in the other direction.
