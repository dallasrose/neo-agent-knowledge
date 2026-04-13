# Contributing to Neo

Thanks for considering a contribution. Neo is early-stage software, so small,
focused changes with tests are easiest to review.

## Development Setup

```bash
uv sync --extra dev
uv run neo setup --provider ollama --model llama3.2 --non-interactive
uv run pytest
```

For local MCP testing:

```bash
uv run neo serve --agent-name hermes
```

## Contribution Terms

By submitting a contribution to this repository, you agree that your
contribution is licensed under the Apache License, Version 2.0, the same license
as this project, unless you clearly state otherwise in writing before the
contribution is merged.

Do not submit code that you do not have the right to license under Apache-2.0.

## Pull Requests

- Keep changes scoped to one behavior or documentation topic.
- Add or update tests for behavior changes.
- Run `uv run pytest` before opening a pull request.
- Update the README or specs when changing public tools, install behavior, or
  data model semantics.

## Project Direction

Neo's core is local-first semantic memory for agents. Sources guide discovery
and provide provenance; the knowledge graph itself should model concepts,
findings, theories, syntheses, and their relationships.
