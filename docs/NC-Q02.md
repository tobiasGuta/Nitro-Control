# NC-Q02 RGB helper transaction finalization

Physical qualification of `release/0.4.0-qualification` is pending independent review of this correction. The reproduced baseline is `0eb74cceba1e2791bc14632d7f8273475f5caa71`.

## Evidence

On the Acer Nitro AN515-58 with stock `acer_wmi`, the physical installer reported success. The six installed sources matched the reviewed checkout, and the published manifest advertised protocol 2, Static and Dynamic capabilities, and `auth_admin`. The `installing` marker remained and its target digests matched the manifest. `RGBClient.capabilities()` returned `interrupted` and suppressed both capabilities.

The baseline installer uses membership in `os.listdir(base_fd)` to decide whether to unlink the marker. An omitted entry therefore lets installation return success and leave the marker untouched. Deterministic fault injection reproduced this on the baseline. It also showed that the privileged guard could miss an existing marker and that a stale list could hide files from provenance checks and removal.

A separate nonprivileged scratch-directory check reproduced the same listing behavior on the workspace filesystem with Fedora 45, Python 3.15.2 and Btrfs. The first descriptor listing after publishing the marker and manifest through descriptor-relative `os.replace` still showed only `nitro_control`. A direct descriptor-relative `lstat` found the marker immediately. The second descriptor listing and a fresh path listing showed all three entries. This establishes a plausible mechanism for the reported installation. The underlying CPython, kernel and filesystem behavior has not been isolated.

## Enumeration audit

| Baseline call sites in `module_staging.py` | Disposition |
| --- | --- |
| RGB helper artifact reads, provenance classification, install finalization, uninstall and privileged runtime verification | Named entries use descriptor-relative `lstat` and verified no-follow opens. Only `ENOENT` means absent. Uninstall removes the verified payload and metadata and confirms each removal by name. |
| Generated RGB helper launcher | The marker is checked by descriptor-relative `lstat`. The manifest and each payload are opened by name with existing non-following ownership and integrity checks before the privileged import. |
| Module staging inspection, interrupted transactions, legacy migration and uninstall | Check the module, checksum, manifest, journal and transaction directory by name. Verify each expected transaction member independently of the directory scan. Stage publication and removal use the verified descriptors. |
| Driver controller, systemd unit and driver library manifest and sources | Replace membership snapshots with direct lookups in the existing verified directories. Continue applying ownership markers and library digest validation. |
| Unexpected transaction contents, stray module temporary names and bytecode candidates | These require discovery. Scan from a fresh directory description anchored to the verified descriptor. Preserve foreign names and bytecode whose Nitro provenance cannot be verified. Transaction cleanup also requires the staging directory to be empty and removed before clearing its journal. |
| Installed kernel discovery in clearance, target collection, qualification, index refresh, migration, uninstall and compatibility reporting | Retain fresh `Path.iterdir()` scans to discover kernel release names across the installed and private trees. Named artifacts are checked separately, and modifications still require verified directory chains. The optional empty-directory sweep remains best effort. |

The installer holds the existing helper lock through publication and verification. After publishing the manifest, it verifies the installed manifest against the target and checks the required sources, launcher, policy, modes and digests while the marker still blocks use. It reads back the exact marker, unlinks it relative to the verified metadata directory, checks absence and syncs the directory before reporting success. An unlink error or ineffectual unlink returns a controlled error. The accepted protocol, Polkit request semantics, hardware-facing code and private lock paths retain their existing contracts.

## Regression evidence

The six new regression methods were run against a temporary archive of the baseline, with only the new test source copied in. They produced 19 failed subtests and three errors. The same methods pass on the corrected tree.

| Check | Baseline observation | Corrected observation |
| --- | --- | --- |
| Fresh install, legacy adoption, coherent reinstall and completed-manifest recovery | A hidden marker could survive a successful return or be missed by the runtime guard. | All install paths finish with the marker absent, protocol 2 and both capabilities visible. A completed but interrupted install is blocked until recovery. |
| Coherent and interrupted helper removal | Known files could be missed by the provenance and removal scans. | Verified artifacts and any interruption marker are removed despite omitted names. Foreign Python and text files remain. |
| Marker unlink denied or ineffectual, incorrect published manifest, altered installed source and wrong launcher mode | A raw unlink exception escaped, or installation returned success without the required postcondition. | Each fault produces `ModuleStagingError`, leaves the marker in place and blocks runtime use. A subsequent installer invocation recovers after the injected fault is cleared and any damaged artifact is restored. |
| Generated launcher guard and provenance protections | The guard failed with missing names, and provenance decisions could miss known objects. | A coherent guard passes with an empty listing, and a marker still blocks it. Foreign, symlinked, hardlinked, untrusted-owner and locally modified helper artifacts are refused and preserved. |
| Module lifecycle | An omitted legacy artifact was left unresolved. | Old-kernel migration, interruption recovery, staged digest rejection and uninstall work with the module, checksum, manifest and transaction names missing from the scans. |

## NC-Q01 documented shell entry points

The documented direct invocations of the native RGB installer and uninstaller, tray autostart enable and disable scripts, and GUI rollback smoke test now have executable mode `100755`. The experimental `restore-after-boot.sh` documentation invokes it through `bash`, which uses its existing `100644` mode. The regression checks executable mode for direct `./scripts/*.sh` invocations in the README and documentation, including command tables and `sudo ./scripts/...` examples.

## Offline qualification

- Strict release gate passed all 269 tests with zero failures or skips. It exercised private GTK4 application and GTK3 tray sessions, generated desktop entries, the strict C11 protocol harness, and default and experimental module builds in disposable directories.
- Mandatory module lifecycle gate passed all 37 tests with zero failures or skips, including the nine Bubblewrap lifecycle tests.
- Python syntax passed for 51 files. Bash syntax passed for 16 scripts. Targeted Ruff lint, Demo JSON, desktop-entry validation, isolated preview.4 upgrade and rollback smoke, and `git diff --check` passed. ShellCheck was unavailable locally. The five changed shell scripts have mode-only diffs.
- SHA-256 verification passed for all 15 preexisting untracked kernel build artifacts.

The correction covers coherent on-disk installation and lookup behavior. It does not measure SELinux or Polkit behavior on the physical host, settings application, stock-driver WMI behavior, or crash durability after a reported filesystem sync failure. Discovery scans still depend on the filesystem to return unknown names. The lock coordinates participating Nitro Control processes. Hosted CI and physical qualification of this correction remain pending.

## Proposed supervised recovery

1. After independent acceptance, record the existing marker, manifest, installed hashes, ownership and modes, installer log, and unprivileged capability result. Compare them against the qualified baseline and preserve the interruption evidence. Stop if provenance or artifact hashes conflict.
2. Have the supervisor rerun the reviewed corrected RGB helper installer through its normal authorized installation path while other helper operations are idle. A verified transaction with a completed manifest can recover through the existing installer lock and transaction checks. Use `--adopt-legacy` only if a separately inspected legacy installation requires it. Leave the marker in place for the installer to verify and clear.
3. Confirm successful exit, direct absence of the marker, and a manifest and artifacts matching the corrected build, with protocol 2, both capabilities, and the intended `auth_admin` policy. From the regular user session, confirm `RGBClient().capabilities()` reports `coherent`, protocol 2, and both capability flags. Stop on a residual marker, a verification error, or a provenance conflict and preserve the new evidence.
4. Resume the physical qualification checklist only after these checks and the supervisor's approval. Use a separate approved physical test for any hardware writes.
