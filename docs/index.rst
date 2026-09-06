inspect-brief Documentation
===========================

``inspect-brief`` generates standardized, concise metric summaries from
`Inspect AI <https://inspect.aisi.org.uk/>`_ evaluation logs and appends them to
a CSV.

.. important::

   Inspect Brief currently supports Inspect ``.eval`` logs only. JSON-formatted
   Inspect evaluation logs are not supported as input.

Installation
------------

With ``uv``:

.. code-block:: bash

   uv add inspect-brief

Or with ``pip``:

.. code-block:: bash

   pip install inspect-brief

Installing the package provides the ``inspect-brief`` command and registers the
Inspect extension entry point.

For one-off use, ``uvx`` can run either workflow without adding Inspect Brief
to the current project.

Choose a workflow
-----------------

Inspect Brief can run in two separate ways:

- **Inspect Hook** — automatically export a summary whenever an Inspect task
  finishes.
- **CLI** — manually process existing ``.eval`` logs after an evaluation has
  completed.

Automatic export with the Inspect Hook
--------------------------------------

The hook is opt-in and reads its configuration from the environment. When
Inspect Brief is installed in the project, export the output path and run
Inspect normally:

.. code-block:: bash

   export INSPECT_BRIEF_CSV_PATH=results/brief_results.csv
   inspect eval inspect_evals/gpqa_diamond --model ollama/llama3.2

For a one-off run, include Inspect Brief in Inspect's isolated tool environment
and set the hook output path for that command. Every package the run needs must
be named explicitly, including the one that provides the task:

.. code-block:: bash

   INSPECT_BRIEF_CSV_PATH=results/brief_results.csv \
     uvx --from inspect-ai --with inspect-brief --with inspect-evals \
     inspect eval inspect_evals/gpqa_diamond --model ollama/llama3.2

In either form, the hook appends summary rows for each completed task, subject
to the hook's environment configuration.

Manual export with the CLI
--------------------------

At least one of ``--log-dir`` or ``--log-files`` is required:

- ``--log-dir`` recursively discovers ``.eval`` logs under a directory.
- ``--log-files`` accepts one or more explicit ``.eval`` paths or filesystem
  URIs. Repeat the option, separate sources with commas, or combine both forms.
- Supplying both combines the discovered and explicit logs and removes
  duplicates.

.. code-block:: bash

   inspect-brief [OPTIONS]

For a one-off export without adding Inspect Brief to the current project:

.. code-block:: bash

   uvx inspect-brief [OPTIONS]

CLI options
~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Option
     - Description
   * - ``--log-dir``
     - Directory containing Inspect logs; recursively finds ``*.eval`` files and
       combines them with ``--log-files`` when both are supplied.
   * - ``--log-files``
     - One or more explicit ``.eval`` paths or filesystem URIs (repeatable or
       comma-separated).
   * - ``--tasks``
     - Tasks to include (repeatable or comma-separated); others are skipped.
   * - ``--target-metrics``
     - JSON object, or path to a JSON file, mapping task to a list of
       :class:`~inspect_brief.core.InspectScore` objects.
   * - ``--csv-path``
     - Output CSV path (default: ``brief_results.csv`` under ``--log-dir``, or
       the current directory).
   * - ``--skip-existing``
     - Skip task runs whose Run ID is already in the CSV.

Examples
~~~~~~~~

Summarize every ``.eval`` under a log tree:

.. code-block:: bash

   inspect-brief --log-dir /path/to/inspect/logs

Summarize specific files and write to a chosen CSV:

.. code-block:: bash

   inspect-brief \
     --log-files /path/to/a.eval \
     --log-files /path/to/b.eval \
     --csv-path results.csv

Provider-backed logs can be supplied directly:

.. code-block:: bash

   inspect-brief --log-files s3://bucket/path/run.eval

Filter tasks and skip runs already recorded:

.. code-block:: bash

   inspect-brief \
     --log-dir /path/to/inspect/logs \
     --tasks inspect_evals/gpqa_diamond \
     --tasks inspect_harbor/gorilla_bfcl_parity \
     --csv-path results.csv \
     --skip-existing

Output
------

Results are appended to the CSV with the following columns:

.. list-table::
   :header-rows: 1

   * - Created
     - Run ID
     - Benchmark
     - Metric
     - Score
     - Runtime (sec)
   * - 2026-08-17T16:54:14+00:00
     - L67rTm5rz3wkwVdTLMGDme
     - inspect_evals/gpqa_diamond
     - accuracy
     - 0.3699
     - 28

- **Created** comes from ``log.eval.created``, falling back to
  ``log.stats.started_at``.
- **Score** is formatted to 2 decimal places when greater than ``1.0``,
  otherwise 4 decimal places.
- Failed or incomplete runs record a status string in the Score column when
  applicable.

Core API
--------

.. automodule:: inspect_brief.core
    :members:

Parsing API
-----------

.. automodule:: inspect_brief.parsing
    :members:

Hook API
--------

.. automodule:: inspect_brief.hooks
    :members:
