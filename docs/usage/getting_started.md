# Getting Started

!!! note
    The `config.yaml` and environment variable examples on this page use examples from Tranqu Server.

## Prerequisites

Before installing Python Project Template, ensure that the following tools are installed.

### Development Environment

| Tool                                        | Version | Description                        |
| ------------------------------------------- | ------- | ---------------------------------- |
| [Python](https://www.python.org/downloads/) | >=3.14  | Python programming language        |
| [uv](https://docs.astral.sh/uv/)            | >=0.10  | Python package and project manager |

Clone the repository:

```shell
git clone https://github.com/oqtopus-team/python-project-template.git
cd python-project-template
```

!!! info
    To use [ouqu-tp](https://github.com/Qulacs-Osaka/ouqu-tp) as a transpiler,
    [staq](https://github.com/softwareQinc/staq/blob/main/INSTALL.md) is required.
    If staq is not installed, it will be automatically installed the first time you use ouqu-tp.
    The installation of staq takes several minutes.

### Setting Up the Python Environment

To install dependencies:

```shell
uv sync
```

## Configurations

Tranqu Server uses two configuration files:

- [config.yaml](#configyaml)
- [logging.yaml](#loggingyaml)

!!! info
    You can use environment variables as values in the above YAML files.

### config.yaml

This is the main configuration file for Tranqu Server.

```yaml
proto: # Settings for Tranqu Server as a gRPC server
  max_workers: 10 # Maximum number of workers (default: 10)
  address: "[::]:50051" # Address and port for RPCs (default: "[::]:50051")
```

### logging.yaml

This is the logging configuration file for Tranqu Server.
It is written in YAML format.
Within Tranqu Server, it is loaded as a `dict`, and then the [logging.config.dictConfig function](https://docs.python.org/3/library/logging.config.html#logging.config.dictConfig) is called to apply the configuration.

If you use the default settings of `config.yaml`, the `logs` directory is required.

```shell
mkdir logs
```

## Hello World Application

To start Hello World Application, run the following command:

```shell
uv run python -m python_project_template.app -c config/config.yaml -l config/logging.yaml
```

- `-c` or `--config`: Specifies the path to the main configuration file.
- `-l` or `--logging`: Specifies the path to the logging configuration file.

When cloned from GitHub, the `worker` in `config.yaml` uses the environment variable `${WORKERS}`,
and the `address` uses the environment variable `${ADDRESS}`.
In this case, the Tranqu Server is started with the following command.

```shell
WORKERS=10 ADDRESS="localhost:50051" uv run python src/tranqu_server/proto/service.py -c config/config.yaml -l config/logging.yaml
```
