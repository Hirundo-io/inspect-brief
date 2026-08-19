# inspect-brief

Generate standardized concise metric summaries from [Inspect AI](https://inspect.aisi.org.uk/) evaluation logs and append them to a CSV.

## Features

- Load Inspect `.eval` logs from a directory (recursive) and/or explicit file paths
- Optionally filter by task name and select target metrics per task
- Append results to a CSV (rewrites the header when columns change, preserving existing rows)
- Skip runs already present in the CSV via `--skip-existing`

## Installation

```bash
uv venv .venv
source .venv/bin/activate
uv sync
```

## Usage

Prefer the console script after install, or the module form:

```bash
inspect-brief [OPTIONS]
python -m inspect_brief [OPTIONS]
```

At least one of `--log-dir` or `--log-files` is required.

### Options

| Option | Description |
| --- | --- |
| `--log-dir` | Directory containing Inspect logs; recursively finds `*.eval` files |
| `--log-files` | One or more log paths (comma-separated) |
| `--tasks` | Tasks to include (comma-separated); others are skipped |
| `--target-metrics` | JSON object (or path to a JSON file) mapping task → list of `InspectScore` objects |
| `--csv-path` | Output CSV path (default: `brief_results.csv` under `--log-dir`, or the current directory) |
| `--skip-existing` | Skip task runs whose Run ID is already in the CSV |

### Basic examples

Summarize every `.eval` under a log tree:

```bash
inspect-brief --log-dir /path/to/inspect/logs
```

Summarize specific files and write to a chosen CSV:

```bash
inspect-brief \
  --log-files /path/to/a.eval,/path/to/b.eval \
  --csv-path results.csv
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

See [`inspect_brief/AGENTS.md`](inspect_brief/AGENTS.md) for project guidelines.
