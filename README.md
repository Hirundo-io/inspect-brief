# inspect-brief

Generate standardized concise metric summaries from [Inspect AI](https://inspect.aisi.org.uk/) evaluation logs and append them to a CSV.

> [!IMPORTANT]
> Inspect Brief currently supports Inspect `.eval` logs only. JSON-formatted
> Inspect evaluation logs are not supported as input.

## Features

- Load Inspect `.eval` logs recursively or from explicit file paths
- Optionally filter by task name and select target metrics per task
- Append results to a CSV (rewrites the header when columns change, preserving existing rows)
- Skip runs already present in the CSV via `--skip-existing`
- Optionally export each task automatically through an Inspect Hook

## Installation

```bash
uv add inspect-brief
```

Installing the package provides the `inspect-brief` command and registers the
Inspect extension entry point.

For local development from a repository checkout, see
[`CONTRIBUTING.md`](CONTRIBUTING.md).

## Choose a workflow

Inspect Brief can run in two separate ways:

- **Inspect Hook:** automatically export a summary whenever an Inspect task finishes.
- **CLI:** manually process existing `.eval` logs after an evaluation has completed.

The hook is the recommended flow for automatic export during normal Inspect runs.
Use the CLI for existing logs, one-off exports, or regenerating a summary CSV.

## Automatic export with the Inspect Hook

The opt-in hook receives each completed task directly from Inspect and appends its
summary rows to the configured CSV. You continue running `inspect eval` normally;
there is no separate `inspect-brief` command in this flow.

### 1. Configure the output CSV

Set the required output path in the shell that will run Inspect:

```bash
export INSPECT_BRIEF_CSV_PATH=results/brief_results.csv
```

The hook is enabled when `INSPECT_BRIEF_CSV_PATH` is set. Set
`INSPECT_BRIEF_ENABLED=0` to disable it explicitly without removing the output
configuration.

For persistent local configuration, put the variables in an untracked `.env`
file in the project directory. Inspect Brief loads this file when Inspect imports
the hook:

```dotenv
INSPECT_BRIEF_CSV_PATH=results/brief_results.csv
# Optional examples:
INSPECT_BRIEF_TASKS=inspect_evals/gpqa_diamond
INSPECT_BRIEF_TARGET_METRICS=target_metrics.json
INSPECT_BRIEF_SKIP_EXISTING=true
```

### 2. Run Inspect normally

```bash
inspect eval inspect_evals/gpqa_diamond --model ollama/llama3.2
```

After every task completes, the hook appends the selected metric rows to the CSV
and logs the number of rows exported. At the end of the Inspect run, it logs a
summary containing the handled task count, failed task count, exported row count,
and output path.

CSV export is synchronous within an Inspect process. Inspect Brief does not use
cross-process file locking, so each output CSV must have a single process writing
to it. Do not point concurrent Inspect runs or CLI processes at the same CSV.

### Hook configuration

| Variable | Required | Description |
| --- | --- | --- |
| `INSPECT_BRIEF_CSV_PATH` | Yes | Output CSV path. The hook is disabled when it is unset. |
| `INSPECT_BRIEF_ENABLED` | No | Set to `0`, `false`, `no`, or `off` to disable the hook. When unset, the hook is enabled if `INSPECT_BRIEF_CSV_PATH` is set. |
| `INSPECT_BRIEF_TASKS` | No | Tasks to include (comma-separated). |
| `INSPECT_BRIEF_TARGET_METRICS` | No | Target-metrics JSON object or path to a JSON file. |
| `INSPECT_BRIEF_SKIP_EXISTING` | No | Set to `1`, `true`, `yes`, or `on` to skip Run IDs already in the CSV. |

## Manual export with the CLI

Use this flow when the hook was not enabled during the Inspect run, or when you
need to process existing logs again.

### 1. Choose the input logs

At least one of `--log-dir` or `--log-files` is required:

- `--log-dir` recursively discovers `.eval` logs under a directory.
- `--log-files` accepts one or more explicit `.eval` paths or filesystem URIs.
  Repeat the option, separate sources with commas, or combine both forms.
- Supplying both combines the discovered and explicit logs and removes duplicates.

JSON-formatted Inspect logs are not supported by either CLI input option.

### 2. Run the export

After installation, invoke the console script:

```bash
inspect-brief [OPTIONS]
```

### CLI options

| Option | Description |
| --- | --- |
| `--log-dir` | Directory containing Inspect logs; recursively finds `*.eval` files and combines them with `--log-files` when both are supplied |
| `--log-files` | One or more explicit `.eval` paths or filesystem URIs (repeatable or comma-separated); combines them with logs found by `--log-dir` when both are supplied |
| `--tasks` | Tasks to include (comma-separated); others are skipped |
| `--target-metrics` | JSON object (or path to a JSON file) mapping task → list of `InspectScore` objects |
| `--csv-path` | Output CSV path (default: `brief_results.csv` under `--log-dir`, or the current directory) |
| `--skip-existing` | Skip task runs whose Run ID is already in the CSV |

### CLI examples

Summarize every `.eval` under a log tree:

```bash
inspect-brief --log-dir /path/to/inspect/logs
```

Summarize specific files and write to a chosen CSV:

```bash
inspect-brief \
  --log-files /path/to/a.eval \
  --log-files /path/to/b.eval \
  --csv-path results.csv
```

Provider-backed logs can be supplied directly:

```bash
inspect-brief --log-files s3://bucket/path/run.eval
```

Filter tasks and skip runs already recorded:

```bash
inspect-brief \
  --log-dir /path/to/inspect/logs \
  --tasks inspect_evals/gpqa_diamond,inspect_harbor/gorilla_bfcl_parity \
  --csv-path results.csv \
  --skip-existing
```

### Target metrics

When `--target-metrics` is omitted, every metric present in the log scores is exported (with a scorer prefix when a log has multiple scorers).

When provided, pass a JSON object (inline or as a file path) mapping each task name to a list of `InspectScore` objects. Each object must have exactly these keys:

- `name` — metric name as it appears in the Inspect log
- `is_percentage` — whether to treat the value as a percentage (appends ` (%)` to the metric label)
- `is_higher_better` — appends `⬆️` or `⬇️` to the metric label
- `is_normalized` — if `is_percentage` is true and this is true, multiply the value by `100`

Example (quote the JSON for the shell):

```bash
inspect-brief --log-dir /path/to/logs --target-metrics '{
  "inspect_evals/gpqa_diamond": [
    {
      "name": "accuracy",
      "is_percentage": true,
      "is_higher_better": true,
      "is_normalized": true
    }
  ]
}'
```

Or point at a file:

```bash
inspect-brief --log-dir /path/to/logs --target-metrics ./target_metrics.json
```

## Output

Results are appended to the CSV. Columns:

| Created | Run ID | Benchmark | Metric | Score | Runtime (sec) |
| --- | --- | --- | --- | --- | --- |
| 2026-08-17T16:54:14+00:00 | L67rTm5rz3wkwVdTLMGDme | inspect_evals/gpqa_diamond | accuracy | 0.3699 | 28 |

- **Created** comes from `log.eval.created`, falling back to `log.stats.started_at`
- **Score** is formatted to 2 decimal places when `> 1.0`, otherwise 4 decimal places
- Failed or incomplete runs record a status string in the Score column when applicable

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for local setup, verification steps,
and pull-request expectations.
