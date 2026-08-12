# Python Project Template

Template repository for building Python projects in OQTOPUS ecosystem.  
This template provides a practical and well-structured development environment with modern Python tooling, including dependency management, linting, testing, documentation, and CI integration.

Projects created from this template should follow the structure and conventions defined here, customizing project-specific elements as needed.

## Template Notice

This `README.md` is intended for the **python-project-template** repository itself.  
When creating a new project from this template, please use `README.template.md`
as the starting point for your project's README.

Before releasing your project:

1. Delete this `README.md`.
2. Rename `README.template.md` to `README.md`.

Some Markdown files in this project template contain warning blocks titled
**"Template Note"**. If you encounter such a section, follow the instructions
described there when customizing the template.

## Features

This template includes a modern Python development environment:

- `pyproject.toml` based project configuration
- dependency management with **uv**
- dependency groups managed by **uv** (`dev`, `test`, `docs`)
- linting and formatting with **ruff**
- static type checking with **mypy**
- testing with **pytest**
- documentation using **MkDocs + Material**
- automatic API reference generation with **mkdocstrings**
- markdown linting with **pymarkdownlint**
- recommended VSCode settings (`.vscode/settings.json`)
- pull request-based development workflow
- GitHub Actions CI workflow
- coverage reporting with **Codecov**
- documentation hosting via **Read the Docs**
- citation metadata via **CITATION.cff**
- optional DOI generation through **Zenodo**

## Project Structure

```text
<your-project>/
├─ src/               # Python package source code
├─ tests/             # Test suite
├─ docs/              # Documentation sources
├─ docs_scripts/      # Helper scripts for building MkDocs documentation (optional)
├─ config/            # Example configuration files (optional)
├─ logs/              # Application log files (optional)
├─ .vscode/           # VSCode settings (optional)
├─ .github/           # GitHub workflows and repository settings
├─ pyproject.toml     # Project configuration and dependencies
├─ Makefile           # Development commands
├─ uv.lock            # Locked dependency versions
├─ Dockerfile         # Container image build definition (optional)
├─ LICENSE            # Project license (e.g., Apache License 2.0)
├─ CITATION.cff       # Citation metadata (optional)
├─ mkdocs.yml         # MkDocs configuration (optional)
├─ README.md          # Project overview
└─ README.template.md # Template README for new projects
```

## Using This Template

This repository is intended to be used as a **GitHub Template Repository**.

To create a new project:

1. Click **"Use this template"** on the GitHub repository page.
2. Create a new repository from this template.
3. Clone your new repository locally.

```shell
git clone https://github.com/oqtopus-team/<your-project>
cd <your-project>
```

You can now start developing your project using this template.

## Development

Development setup and workflow are documented in the following guides:

- [Development Flow](docs/developer_guidelines/development_flow.md)
- [Setup Development Environment](docs/developer_guidelines/setup.md)

When starting a new project from this template:

- Decide whether the project should use a `develop` branch and update **Development Flow** accordingly.
- If additional tools or setup steps are required, update **Setup Development Environment** to reflect them.

## Development Conventions

The following sections describe how each part of the template should be customized.

### Language

All deliverables, including source code (comments, variable names, etc.) and documentation, must be written in **English**.
The only exceptions are for specific requirements such as multi-language user interfaces.

### Source Code Layout (`src/`)

- Place all Python source code under the `src/` directory.
- Repository names typically use **kebab-case**, while Python package names
  use **snake_case**.
- The top-level Python package name should match the repository name
  converted to **snake_case**.
- If the project provides type hints, include a `py.typed` file in the
  package directory to indicate that the package supports type checking
  (PEP 561).

Example:

```text
<your-project>/
└─ src/
   └─ <your_project>/
      ├─ __init__.py
      ├─ py.typed
      └─ ...
```

### Test Layout (`tests/`)

The test structure should mirror the structure of `src/`.

- The directory layout under `tests/` should match the layout under `src/`.
- Tests for `xxx.py` should be written in `test_xxx.py`.

Example:

```text
src/
└─ <your_project>/
   ├─ __init__.py
   ├─ py.typed
   ├─ module_a.py
   └─ module_b.py

tests/
├─ __init__.py
└─ <your_project>/
   ├─ __init__.py
   ├─ test_module_a.py
   └─ test_module_b.py
```

Additional test files such as `test_xxx_yyy.py` are allowed when needed.  
An `__init__.py` file is placed under `tests/` so that test utility modules
can be created and imported from within the `tests` directory.

### Configuration (`config/`)

For user convenience, place configuration files under `config/`
so that the application can be started simply by running:

```shell
make run
```

Logging configuration such as format, layout, and handlers should **not be
hard-coded in the application**. Instead, define them in a configuration file
such as:

```text
config/logging.yaml
```

### Log Directory (`logs/`)

The `logs/.gitkeep` file is included to ensure that the `logs`
directory exists so that the application does not fail when started
with `make run`. Remove it if file-based logging is not needed.

Log files should use **structured logging**, assuming that they will be
consumed by observability tools. Structured logs allow integration with
observability platforms such as log aggregation and monitoring systems.

### VSCode settings (.vscode/) (optional)

The `.vscode/` directory provides recommended Visual Studio Code settings
to improve the development experience.

These settings may include:

- automatic linting and formatting
- automatic Markdown linting

Using these settings is optional, but recommended for a smoother and more
consistent development workflow. If you use a different editor, you can
ignore or remove this directory.

### Project Metadata (`pyproject.toml`)

- Release the project starting from **version 1.0.0 or higher**.
- Configuration for development tools should be centralized in
  `pyproject.toml` whenever possible.

Examples include configuration for:

- `ruff`
- `mypy`
- `pytest`
- `pymarkdown`

### Development Commands (`Makefile`)

The `Makefile` provides common commands required for development.

Typical commands include:

- dependency installation
- linting
- running tests
- starting the application

Add new commands as needed for your project.  
The `Makefile` also supports a `help` command to display available targets.

### Dependency Lock (`uv.lock`)

This project uses **uv dependency groups**.

The recommended way to set up the development environment is by running:

```shell
make install
```

This command executes `uv sync --all-groups`, which installs the application along with all dependency groups (`dev`, `test`, `docs`).  
If you prefer running the uv command directly:

- Running `uv sync --all-groups` installs the application and all development dependencies.
- Running `uv sync` installs **only** the dependencies required for the application itself.

### Docker (`Dockerfile`) (optional)

For applications (rather than libraries), providing a `Dockerfile` is recommended.

Use a `.dockerignore` file to prevent unnecessary files from being sent to the
Docker build context.

For fast and reproducible builds, the OQTOPUS ecosystem recommends using:

- **multi-stage builds**
- **uv cache mounts**

This combination significantly reduces build time while keeping the final image
small.

Typical pattern:

1. **Build stage** – run `uv sync` with cache mounts to install dependencies.
2. **Runtime stage** – copy the built environment or application into a minimal
   base image.

### License (`LICENSE`)

OQTOPUS projects generally use the **Apache License 2.0**. When starting a new project, ensure you update the **Copyright** section in the `LICENSE` file to reflect your project's details.

## Citation

### Citation (`CITATION.cff`) (optional)

Update `CITATION.cff` if you want to:

- provide citation metadata
- integrate with **Zenodo** to generate DOIs for releases

For more information on the Citation File Format, refer to the following resources:

- [Citation File Format Official Site](https://citation-file-format.github.io/)
- [CFF Schema Guide](https://github.com/citation-file-format/citation-file-format/blob/main/schema-guide.md)

If citation metadata is not needed, you may remove the `CITATION.cff` file.

### Citation (Zenodo) (optional)

This template includes a `CITATION.cff` file so that GitHub can generate citation information automatically.
For research software, you may also enable **Zenodo** to automatically generate a DOI for each release.

To enable Zenodo:

1. Sign in at <https://zenodo.org>.
2. Connect your GitHub account.
3. Enable the repository in Zenodo.

Each GitHub release will then receive a DOI.

## Documentation

Choose the appropriate documentation structure based on the project scale and target audience. This template provides the **MkDocs** setup as the standard.

- **Root README**: For very simple repositories. Maintain all information in the root `README.md`.
- **docs/ Directory**: For small-scale projects or those with few direct users. Place documents under the `docs/` directory and provide links from the root `README.md`.
- **MkDocs (Standard)**: The recommended approach for OQTOPUS projects. Use the pre-configured MkDocs setup and link to the hosted documentation from the root `README.md`.

Update documentation in the `docs/` directory as needed.

### Documentation (Read the Docs) (optional)

This template includes configuration for **Read the Docs**.

Documentation is built using:

- **MkDocs**
- **Material for MkDocs**
- **mkdocstrings**

To enable hosted documentation:

1. Create an account at <https://readthedocs.org>.
2. Import your GitHub repository.
3. Ensure the repository contains `.readthedocs.yaml`.

After configuration, documentation will be built automatically on each push.

Helper scripts used for documentation generation are placed in `docs_scripts/`.

If your project does **not** use MkDocs or automated documentation
generation, the `docs_scripts/` directory can be removed.

The documentation for this template project is hosted on Read the Docs:  
<https://oqtopus-python-project-template.readthedocs.io/>

## CI / External Services

If you use external services, configure them for your repository:

- GitHub Actions
- Dependabot
- GitHub Copilot Agent Configuration (optional)
- Read the Docs (optional)
- Codecov (optional)
- Zenodo (optional)

## GitHub Actions (`.github/workflows/`)

This project uses GitHub Actions workflows defined in `.github/workflows/`.

- `ci.yaml`: Runs linting, type checking, tests, and coverage reporting on push and pull requests.
- `release.yaml` (optional): Includes a "Create GitHub Release" step that creates a release when a version tag is pushed (optional; if enabled, create tags via the command line, as creating a release from the GitHub web UI may cause errors).
- `labeler.yaml`: Automatically assigns labels and the author to pull requests based on commit messages; you should update the `branches` setting (e.g., `main` or `develop`) to match your repository's default branch.

## Dependabot (`.github/dependabot.yml`)

This project supports automated dependency updates using [Dependabot](https://docs.github.com/en/code-security/tutorials/secure-your-dependencies/dependabot-quickstart-guide).

Dependabot is configured via `.github/dependabot.yml`.  
You should update this file to match the package ecosystems used in your repository.

For example, depending on your project setup, you may need to include:

- `devcontainer` for development container configurations
- `docker` for container images
- `github-actions` for GitHub Actions workflows
- `uv` for Python dependencies

When creating a new project from this template:

- Remove unused package ecosystems
- Add missing ones based on your project configuration

Keeping this configuration aligned with your actual dependencies ensures that
security updates and version upgrades are handled correctly.

Dependabot works well together with GitHub Actions CI to keep dependencies up to date automatically.

## GitHub Copilot Agent Configuration (optional)

This template supports GitHub Copilot custom instructions for improving
AI-assisted development.

- `.github/instructions/commit-message.instructions.md`: Commit message instructions
- `.github/instructions/pull-request-description.instructions.md`: Pull Request title & description instructions

You can also add repository-wide instructions by creating the following file:

- `.github/copilot-instructions.md`: Repository-wide Copilot instructions

To create `.github/copilot-instructions.md`, refer to the official documentation:
<https://docs.github.com/en/copilot/how-tos/configure-custom-instructions/add-repository-instructions?tool=webui>

If necessary, add the following text to `.github/copilot-instructions.md`.

```md
## Commit and PR conventions
- Use Conventional Commits style for all commit messages.
- Follow the commit message instructions in `.github/instructions/commit-message.instructions.md`.
- Follow the Pull Request title and description instructions in `.github/instructions/pull-request-description.instructions.md`.
- When creating a Pull Request, follow the sections defined in `.github/pull_request_template.md`.
```

## Code Coverage (Codecov)

Test coverage is generated using **pytest-cov** and can be uploaded to **Codecov**.

To enable Codecov:

1. Sign in at <https://codecov.io>.
2. Connect your GitHub account.
3. Add your repository.

If GitHub Actions are configured, coverage reports can be uploaded automatically.

The coverage report for this template project is available at:  
<https://app.codecov.io/gh/oqtopus-team/oqtopus-python-project-template>

## Repository Metadata

Configure the GitHub repository metadata appropriately.

- Set the **Description** and **Topics** fields.
- It is recommended to include at least the following topics:
  - `python`
  - `quantum`
  - `quantum-computing`
- If documentation is published via **Read the Docs**, set the documentation
  URL in the **Website** field of the repository.

## Repository Protection

To maintain a stable and safe development workflow, configure **GitHub Rulesets**
in the repository settings:

```text
Settings > Rules > Rulesets
```

For OQTOPUS projects, apply the following rules to the `main` branch and
to the `develop` branch (if used).

Recommended rules:

- Restrict deletions
- Require a pull request before merging
  - Require at least 1 approval
- Block force pushes

Repository administrators may be allowed to bypass these rules when necessary.

These protections prevent accidental direct pushes and ensure that all
changes are reviewed before being merged.

Optionally, you may configure a `CODEOWNERS` file to automatically request
reviews from maintainers for changes to specific parts of the repository.

## License

This template is distributed under the [Apache License 2.0](LICENSE).
