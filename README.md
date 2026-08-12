<!-- markdownlint-disable MD041 -->
![OQTOPUS logo](./docs/asset/oqtopus-logo.png)

# OQTOPUS Manager

[![CI](https://github.com/oqtopus-team/oqtopus-manager/actions/workflows/ci.yaml/badge.svg)](https://github.com/oqtopus-team/oqtopus-manager/actions/workflows/ci.yaml)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![slack](https://img.shields.io/badge/slack-OQTOPUS-pink.svg?logo=slack&style=plastic)](https://join.slack.com/t/oqtopus/shared_invite/zt-3bpjb7yc3-Vg8IYSMY1m5wV3DR~TMSnw)

## Overview

**OQTOPUS Manager** is a local/on-prem management application for the OQTOPUS ecosystem.
It allows operators to manage multiple OQTOPUS environments running on a single host.

## Quick Start

```bash
git clone https://github.com/snuffkin/oqtopus-manager.git
cd oqtopus-manager
make install
make run
```

Open [http://localhost:38000/](http://localhost:38000/) in your browser.

## Features

- Manage multiple OQTOPUS backend environments on one host
- Web-based UI built with FastAPI + HTMX + Tailwind CSS

## Documentation

- [Documentation Home](https://oqtopus-manager.readthedocs.io/)

## Contact

You can contact us by creating an issue in this repository or by email:

- [oqtopus-team[at]googlegroups.com](mailto:oqtopus-team[at]googlegroups.com)

## License

OQTOPUS Manager is released under the [Apache License 2.0](LICENSE).
