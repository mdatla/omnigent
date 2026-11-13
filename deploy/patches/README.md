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
| `0001-binary-preview-gzip.patch` | Binary file preview (`PDF`/`PNG`) returns HTTP 500 with `AssertionError` in `BaseHTTPMiddleware` | issue [#10481](https://github.com/omnigent-ai/omnigent/issues/10481), draft PR [#10585](https://github.com/omnigent-ai/omnigent/pull/10585) | Blocks the file viewer on every PDF and image. The image resolves Starlette 1.10.0; the bug appears in Starlette 1.8.0 and later | A **release** contains #10585 (`ops/omnigent-starlette-gzip/check_fix_release.py` reports `10`) | 2026-11-13 | 2026-11-13 |
| `0002-host-openssh-client.patch` | `ssh` is missing from the host image, so every automation that reads the Unraid host over SSH fails with exit 127 | not filed — this is a local deployment need, not an upstream bug | The lab automations (`unraid-maintenance`, `omnigent-stack-watch`) reach the host with `ssh unraid`. The service was installed into the running container and died at the first recreate | the automations no longer need ssh, or the host image ships an ssh client | 2026-11-13 | 2026-11-13 |

## Related

- `../PATCHES.md` — the lab copy of the policy, the options, and the lifecycle.
- `ops/omnigent-starlette-gzip/` in the lab repo — the patch source, the runtime
  applier (fallback for a local venv), and the release watcher.
