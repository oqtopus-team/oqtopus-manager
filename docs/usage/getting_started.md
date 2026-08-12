# Getting Started

## Prerequisites

| Tool | Version | Description |
|------|---------|-------------|
| [Python](https://www.python.org/downloads/) | >=3.14 | Python programming language |
| [uv](https://docs.astral.sh/uv/) | >=0.10 | Python package and project manager |

## Quick Start

### 1. Clone the repository

```shell
git clone https://github.com/oqtopus-team/oqtopus-manager.git
cd oqtopus-manager
```

### 2. Install dependencies and create the config file

```shell
make install
```

This command installs all dependencies and copies `config/config.yaml.example` to `config/config.yaml` if it does not already exist.
Edit `config/config.yaml` to match your environment before starting the application.
See [Configuration](configuration.md) for the full reference.

### 3. Start the application

```shell
make run
```

Open [http://localhost:38000](http://localhost:38000) in your browser.

## Configuration

OQTOPUS Manager is configured via `config/config.yaml`.
See the following pages for details:

- [Configuration](configuration.md) — server, behavior, appearance, and debug settings
- [Authentication](authentication.md) — reverse proxy authentication and Cognito setup
