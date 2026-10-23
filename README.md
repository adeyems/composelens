# ComposeLens

A CLI for developers reviewing changes to Docker Compose applications. Compare the effective configuration, not YAML formatting: Docker Compose resolves merges, interpolation and short syntax; ComposeLens compares the resulting JSON models. Python 3.10+ standard library plus Docker CLI with Compose supporting `config --format json` are required.

## Run

```sh
python3 composelens.py -b examples/base.yaml -c examples/base.yaml -c examples/override.yaml
python3 -m unittest discover -s tests -v
```

The synthetic example reports changes at `/services/web/image` and `/services/web/environment/MODE` and exits 1. Comparing the base file with itself exits 0. Repeat `-b` or `-c` for ordered layers on that side; Compose's merge rules apply. Input filenames are relative to the invoking working directory. `--project-directory` sets a **shared** base directory for both models (default current directory), including Compose path resolution. Both resolutions use project name `composelens` to avoid accidental project-name differences.

Exit 0 means equal models, 1 means differences, and 2 means input/resolver errors. JSON reports go to stdout, errors to stderr. Changes include JSON Pointer paths (escaped `~` and `/`) and `added`, `removed` or `changed`. Object order is ignored, lists are compared positionally, and added/removed objects are reported once at their parent path. Reports omit configuration values. Resolver stderr is not printed because it can contain secrets; `resolver_warnings` flags its presence on each side. Inspect warnings locally before relying on a match. Raw resolver failure text is likewise withheld.

## Scope and safety

Only `docker compose config --format json` is invoked: no `up`, build, pull or container execution. This is configuration comparison, not runtime validation or a risk/security verdict. Use trusted local synthetic Compose files only. Compose can read referenced files, interpolate the inherited shell environment and load `.env` files; input includes may also access external resources. This tool is not a sandbox. Do not feed it credentials or customer data. Although values are omitted, paths and names can still be sensitive: reports are **not** guaranteed anonymized. Resolved models exist in process memory and are not saved.

Each resolver call has a 30-second timeout. Output is currently buffered in memory without a size cap. Inputs/environment are not snapshotted between the two calls; changing them during a comparison can affect results. Use the same Compose version and environment to reproduce a report. Integration tests require no running application containers; they test the real resolver and cannot silently skip in CI.

Milestone 1 deliberately has no saved snapshots, side-specific environments, profile-selection UI, ignore rules, semantic list matching or packaging. See `ROADMAP.md`.

Resolver semantics: [official Docker Compose config documentation](https://docs.docker.com/reference/cli/docker/compose/config/).
