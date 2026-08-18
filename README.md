# inspect-brief
Standardized concise metric summaries for Inspect evaluations.

## Features

- A common CLI for launching model evaluations across supported adapters.
- Optional managed local vLLM server for frameworks that can use an OpenAI-compatible endpoint.
- Per-run raw framework logs plus an appended summary CSV.

## Installation

```bash
uv venv .venv
source .venv/bin/activate
uv sync
```

## Usage

```bash
inspect-brief MODEL FRAMEWORK TASK[,TASK...] [OPTIONS] [FRAMEWORK_OPTIONS]
```

Prefer the console script after editable install, or use:

```bash
python -m hirundo_evals MODEL FRAMEWORK TASK[,TASK...] [OPTIONS] [FRAMEWORK_OPTIONS]
```

### Basic Run

```bash
hirundo-evals MODEL FRAMEWORK TASK --framework-option value
```

Multiple tasks are comma-separated:

```bash
hirundo-evals MODEL FRAMEWORK TASK_A,TASK_B --framework-option value
```

### Outputs

By default, outputs are written under `logs/<model>/<framework>/<run_timestamp>/`, with a summary CSV at `logs/<model>/results.csv`.

```bash
hirundo-evals MODEL FRAMEWORK TASK --output-dir eval_outputs
```

The summary CSV is appended across runs and includes fields such as framework, run ID, benchmark, metric, score, and runtime.

Example `results.csv` output:

| Run ID | Framework | Benchmark | Metric | Score | Runtime (sec) |
| --- | --- | --- | --- | --- | --- |
| 20260624_233538 | inspect-ai | ifeval | final_acc (%) ⬆️ | 67.00 | 600 |
| 20260624_233538 | inspect-ai | scicode | percentage_main_problems_solved (%) ⬆️ | 45.00 | 1200 |
| 20260624_233538 | inspect-ai | scicode | percentage_subproblems_solved (%) ⬆️ | 56.67 | 1200 |

## Contributing

See [`AGENTS.md`](AGENTS.md) for project guidelines, test suite setup, and PR practices.
