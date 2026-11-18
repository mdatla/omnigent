# Downstream patches — the manifest

Patch files in this directory are applied to the checkout during the image
build, before `docker build`. The apply step is in
`.github/workflows/unraid-images.yml` (`Apply downstream patches`).

Policy, selection rules, and the retire procedure: `../PATCHES.md` in the lab
repo (`machines/home-server/omnigent/PATCHES.md`).

The apply step has three outcomes:

- applies → the image carries the fix;
- applies in reverse → the patch is already upstream, the build warns and
  continues; delete the patch;
- does not apply → the build fails. Rebase the patch against upstream.

## Active patches

| File | Fixes | Upstream | Why we carry it | Delete when | Added | Last rebased |
|---|---|---|---|---|---|---|
| `0002-host-openssh-client.patch` | `ssh` is missing from the host image, so every automation that reads the Unraid host over SSH fails with exit 127 | not filed — this is a local deployment need, not an upstream bug | The lab automations (`unraid-maintenance`, `omnigent-stack-watch`) reach the host with `ssh unraid`. The service was installed into the running container and died at the first recreate | the automations no longer need ssh, or the host image ships an ssh client | 2026-11-13 | 2026-11-13 |
| `0003-pi-native-context-tokens.patch` | The web context-occupancy ring never renders for `pi-native` sessions: the extension posts only `cumulative_*`, so the server never writes the `last_context_tokens` label and `lastTotalTokens` stays null | issue [#12431](https://github.com/omnigent-ai/omnigent/issues/12431), PR [#12433](https://github.com/omnigent-ai/omnigent/pull/12433) (open, stalled since 2026-11-02) | The ring is the only live cue for how full the window is, and pi-native is the last native harness without it. Carried because #12433 has sat unmerged for over two weeks | A **release** contains #12433. The build then warns `already upstream` — delete this patch | 2026-11-18 | 2026-11-18 |

## Retired

| File | Retired | Why |
|---|---|---|
| `0001-binary-preview-gzip.patch` | 2026-11-18 | Upstream now honours the `skip_gzip` opt-out on `http.response.start` in `_StateAwareGZipResponder` (`omnigent/server/routes/_gzip_route.py`), which is the whole fix. The patch no longer applied — and could no longer reverse-apply, because upstream's shape differs from ours — so the build failed on it. Deleted rather than rebased: the fix is in. Confirmed by a binary read with `Accept-Encoding: gzip` returning 200 on the rebuilt image. |

## Related

- `../PATCHES.md` — the lab copy of the policy, the options, and the lifecycle.
- `ops/omnigent-starlette-gzip/` in the lab repo — the patch source, the runtime
  applier (fallback for a local venv), and the release watcher.
