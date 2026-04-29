# 🃏 Poker CLI

Poker CLI is an AI security agent CLI for auditing LLM applications, agent projects, prompt workflows, RAG systems, and tool-calling code.

The project is intentionally small at this stage. The current codebase focuses on a clean, separable foundation instead of keeping the previous demo-style `ping`, `run`, and `version` commands.

## Positioning

Poker CLI is not meant to be a generic Claude Code clone.

Its long-term goal is to become a local security-focused agent that helps developers and security engineers answer questions such as:

- Does this LLM app expose secrets?
- Do these prompts lack trust-boundary guidance?
- Are agent tools too powerful or missing confirmation controls?
- Could a RAG pipeline be vulnerable to prompt injection?
- Could an MCP server expose risky local capabilities?
- What should be fixed first, and why?

## Current Scope

The current implementation is a minimal first step toward that goal.

It includes:

- A `scan` command.
- A small scan orchestrator.
- A shared finding model.
- Workspace traversal helpers.
- A secret detector.
- A prompt-safety detector.
- A LangChain-style agent tool risk detector.
- Table and JSON output.

It does not currently include:

- LLM integration.
- Interactive agent chat.
- Automatic code modification.
- Shell command execution.
- MCP runtime connection.
- Full RAG analysis.

Those features should be added later only after the local scanning foundation is stable.

## Install

Using Poetry:

```bash
poetry install
```

Or using pip in editable mode:

```bash
pip install -e .
```

## Usage

Scan the current directory:

```bash
poetry run poker scan .
```

Scan a single file:

```bash
poetry run poker scan README.md
```

Output JSON:

```bash
poetry run poker scan . --format json
```

## Project Structure

```text
poker-cli/
├── poker/
│   ├── __init__.py
│   ├── cli.py              # Typer command entrypoint
│   ├── models.py           # Shared finding models
│   ├── reporter.py         # Table / JSON rendering
│   ├── scanner.py          # Scan orchestration
│   ├── workspace.py        # File discovery and reading
│   └── detectors/
│       ├── __init__.py
│       ├── agent_tools.py  # LangChain-style tool risk detector
│       ├── prompts.py      # Prompt safety detector
│       └── secrets.py      # Secret detector
├── tests/
├── AI_SECURITY_CLI_TODO.md
├── pyproject.toml
├── README.md
└── LICENSE
```

## Design Principles

### 1. Small core

The core should stay easy to understand. Avoid placing all behavior in one large file.

### 2. Separable modules

Each module has a narrow responsibility:

- `cli.py` handles command-line input.
- `scanner.py` coordinates detectors.
- `workspace.py` handles file discovery.
- `models.py` defines shared data structures.
- `reporter.py` handles output.
- `detectors/` contains independent security checks.

### 3. Security-first defaults

The scanner should be read-only by default. Future write, shell, network, or fix operations should require explicit policy and user confirmation.

### 4. Extensible detector model

A detector should be easy to add without changing the CLI or output layer. A detector receives a file path and content, then returns a list of findings.

## Adding a Detector

Create a new detector under `poker/detectors/`, then register it in `poker/scanner.py`.

A detector should:

- Keep one focused responsibility.
- Return structured `Finding` objects.
- Avoid printing directly.
- Avoid modifying files.
- Include clear recommendations.

## Near-term Roadmap

- Improve secret scanning accuracy.
- Add detector tests.
- Add prompt injection examples.
- Improve LangChain `@tool` risk detection.
- Add JSON schema stability tests.
- Add Markdown report output.
- Add CI-friendly exit code options.
- Add baseline / ignore support.

## Development

Run tests:

```bash
poetry run pytest
```

Format code:

```bash
poetry run black poker tests
```

Lint code:

```bash
poetry run ruff check poker tests
```

Type check:

```bash
poetry run mypy poker
```

## License

MIT License. See `LICENSE` for details.
