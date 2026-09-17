# Fork workflows

- `ci.yml`: push / pull request checks on `komari-agent-1.2.60`.
- `build.yml`: reusable Linux amd64/arm64 installer tests, Go tests, image smoke checks and package export.
- `release.yml`: manual `Publish release`; requires an unused semantic version. Publishes the same tested packages from both architectures.
- Assets: Linux binaries, `install.sh`, `SHA256SUMS`, `build-manifest.json`, and `ghcr.io/mghts/komari-agent:<version>`.
- Prerelease versions do not update `latest`. There is no active Snapshot, Windows or macOS publishing workflow.

See [FORK.md](../../FORK.md) for the release and migration procedure. Old workflow notes and definitions are retained in `../legacy-workflows/` and are not active workflows. Go module and linker symbol paths intentionally retain the upstream namespace.
