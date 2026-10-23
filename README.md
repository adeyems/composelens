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

Each resolver call has a 30-second timeout. Output is currently buffered in memory without a size cap. The shell environment is copied once, but input files are not snapshotted; changing files during a comparison can affect results. Use the same Compose version and environment to reproduce a report. Integration tests require no running application containers; they test the real resolver and cannot silently skip in CI.

There are no saved snapshots, profile-selection UI, ignore rules, semantic list matching or packaging yet. See `ROADMAP.md`.

## Per-side environment files and profiles

```sh
python3 composelens.py -b examples/profiles.yaml -c examples/profiles.yaml \
  --baseline-env-file examples/baseline.env \
  --candidate-env-file examples/candidate.env \
  --baseline-no-profiles --candidate-profile debug
```

With `COMPOSELENS_DEMO_TAG` unset in the shell, this exits 1 and reports an added debug service and a changed web image. Example environment files contain synthetic values only.

Repeat `--baseline-env-file` or `--candidate-env-file` for ordered layers. Later files override earlier ones; **inherited shell variables override environment-file values**, as in Compose. These options control Compose interpolation, not replacement of service-level `env_file` declarations. Paths are relative to the invoking working directory. Omitting the option retains Compose's default `.env`/environment behaviour; it does not mean an empty environment. Explicit env files replace automatic default `.env` selection under Compose's rules. Compose, not ComposeLens, parses the files.

Repeat `--baseline-profile` or `--candidate-profile` to enable profiles on that side, or quote `'*'` for all profiles. `--baseline-no-profiles` and `--candidate-no-profiles` explicitly disable inherited profiles; each conflicts with profile selections on its side. Omitting both retains Compose's profile environment/default behaviour. Unprofiled services remain active. Explicit selections override `COMPOSE_PROFILES`, including values from env files. Invalid profile names are rejected and missing env files produce an error.

Reports include `selections` with supplied env-file paths and profile choices. `null` means inherited/default behaviour; `profiles: []` means explicitly no profiles. This is selection metadata, **not** a complete provenance snapshot. File paths and profile names can be sensitive. No environment values are included.

Both sides use one copied shell-environment snapshot, and each resolver gets its own copy. No global environment variables are changed. Referenced files are still read separately and can change between calls. This is not an isolated environment: use a controlled shell plus explicit files/profiles for reproducible comparisons.

Resolver semantics: [official Docker Compose config documentation](https://docs.docker.com/reference/cli/docker/compose/config/).
