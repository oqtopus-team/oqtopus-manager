
# Development Environment Setup

This guide explains how to set up the development environment for contributing to OQTOPUS Manager.  
The project provides a **Makefile** to simplify common development tasks.

## Prerequisites

Install the following tools before starting development.

| Tool                                        | Version | Description                        |
| ------------------------------------------- | ------- | ---------------------------------- |
| [Python](https://www.python.org/downloads/) | >=3.14  | Python programming language        |
| [uv](https://docs.astral.sh/uv/)            | >=0.10  | Python package and project manager |

Clone the repository:

```shell
git clone https://github.com/oqtopus-team/oqtopus-manager.git
cd oqtopus-manager
```

## Project Structure

The repository is organized as follows:

```text
oqtopus-manager/
├─ src/oqtopus_manager/  # Application source code (FastAPI app, routers, templates)
├─ tests/                # Test suite
├─ docs/                 # Documentation sources (MkDocs)
├─ docs_scripts/         # Documentation generation helpers (mkdocs-gen-files)
├─ config/               # config.yaml.example and logging.yaml (config.yaml is generated locally, not tracked)
├─ assets/               # Operator-supplied icons served at /assets (generated locally, not tracked)
├─ .vscode/              # VSCode settings (optional)
├─ .github/              # GitHub workflows and repository settings
├─ pyproject.toml        # Project configuration and dependencies
├─ Makefile              # Development commands
├─ mkdocs.yml            # MkDocs configuration
├─ uv.lock               # Locked dependency versions
└─ README.md             # Project overview
```

## Installing Dependencies

Install the project dependencies and set up the local development environment:

```shell
make install
```

This command performs the following:

- Installs all dependencies (including the dev, test, and docs groups) via `uv`.
- Configures the Git commit message template (see [Commit Message Format](development_flow.md#commit-message-format)).
- Creates `config/config.yaml` from `config/config.yaml.example` if it does not already exist.
- Installs the [OQTOPUS CLI](https://github.com/oqtopus-team/oqtopus-cli), used to manage environments.

## Running the Application

Start the application:

```shell
make run
```

Open [http://localhost:38000](http://localhost:38000) in your browser.
See [Configuration](../usage/configuration.md) to customize server settings, appearance, and access control before running in a shared environment.

## Linting and Testing

### Format Code

Format the code:

```shell
make format
```

### Lint Code

Run linting and static type checking:

```shell
make lint
```

### Run Tests

Run the test suite:

```shell
make test
```

### Verify Code

Run all verification steps (formatting, linting, and tests):

```shell
make verify
```

## Documentation

### Lint Documentation

Run documentation linting:

```shell
make docs-lint
```

### Build Documentation

Build the documentation:

```shell
make docs-build
```

### Start the Documentation Server

This project uses [MkDocs](https://www.mkdocs.org/) to generate the HTML documentation and
[mkdocstrings-python](https://mkdocstrings.github.io/python/) to generate the Python API reference.  
Start the documentation server with:

```shell
make docs-serve
```

Open the documentation in your browser at [http://localhost:8000](http://localhost:8000).
