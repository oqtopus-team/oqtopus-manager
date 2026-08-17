#!/bin/sh
# Bootstrap an OQTOPUS Manager environment:
#   1. install oqtopus-cli (unless already installed and --skip-cli-install is given)
#   2. scaffold a new environment via 'oqtopus init --template manager'
#   3. run 'oqtopus manager install' inside the new environment
#
# 'oqtopus manager start' is intentionally left for the user to run manually,
# since a 'cd' performed inside this script does not affect the caller's shell.
set -eu

SCRIPT_NAME="quickstart.sh"
TEMPLATE="manager"
CLI_INSTALL_URL="https://raw.githubusercontent.com/oqtopus-team/oqtopus-cli/main/scripts/install.sh"

usage() {
    cat <<EOF
Usage: ${SCRIPT_NAME} <env_name> [options]

Bootstrap an OQTOPUS Manager environment: install oqtopus-cli (if needed),
scaffold a new environment via 'oqtopus init', and run 'oqtopus manager install'.

Arguments:
  <env_name>                          Name of the environment directory to create (required)

Options:
  --cli-branch <branch_name>         Branch of oqtopus-cli's template to pass to 'oqtopus init --branch'
  --version <version|branch:<branch>>
                                      Version (or branch) of OQTOPUS Manager to pass to 'oqtopus manager install'
  --skip-cli-install                  Skip installing oqtopus-cli if it is already installed
  -h, --help                          Show this help message and exit

Example:
  ${SCRIPT_NAME} my-env --version 1.2.0
  ${SCRIPT_NAME} my-env --cli-branch feat/manager-template --version branch:feat/some-feature

After this script finishes, run:
  cd <env_name>
  oqtopus manager start
EOF
}

ENV_NAME=""
CLI_BRANCH=""
VERSION=""
SKIP_CLI_INSTALL=0

while [ $# -gt 0 ]; do
    case "$1" in
        -h|--help)
            usage
            exit 0
            ;;
        --cli-branch)
            if [ $# -lt 2 ] || [ -z "${2:-}" ]; then
                echo "Error: --cli-branch requires a value" >&2
                exit 1
            fi
            CLI_BRANCH="$2"
            shift 2
            ;;
        --version)
            if [ $# -lt 2 ] || [ -z "${2:-}" ]; then
                echo "Error: --version requires a value" >&2
                exit 1
            fi
            VERSION="$2"
            shift 2
            ;;
        --skip-cli-install)
            SKIP_CLI_INSTALL=1
            shift
            ;;
        -*)
            echo "Error: unknown option: $1" >&2
            usage >&2
            exit 1
            ;;
        *)
            if [ -n "$ENV_NAME" ]; then
                echo "Error: unexpected argument: $1" >&2
                usage >&2
                exit 1
            fi
            ENV_NAME="$1"
            shift
            ;;
    esac
done

if [ -z "$ENV_NAME" ]; then
    echo "Error: <env_name> is required" >&2
    usage >&2
    exit 1
fi

if [ -e "$ENV_NAME" ]; then
    echo "Error: '${ENV_NAME}' already exists" >&2
    exit 1
fi

# 1. Install oqtopus-cli
if [ "$SKIP_CLI_INSTALL" -eq 1 ] && command -v oqtopus >/dev/null 2>&1; then
    echo "oqtopus-cli is already installed. Skipping installation."
else
    echo "Installing oqtopus-cli..."
    curl -LsSf "$CLI_INSTALL_URL" | sh
fi

if ! command -v oqtopus >/dev/null 2>&1; then
    echo "Error: 'oqtopus' command not found after installation. Please check your PATH." >&2
    exit 1
fi

# 2. Scaffold the environment
echo "Initializing environment '${ENV_NAME}'..."
if [ -n "$CLI_BRANCH" ]; then
    oqtopus init "$ENV_NAME" --template "$TEMPLATE" --branch "$CLI_BRANCH"
else
    oqtopus init "$ENV_NAME" --template "$TEMPLATE"
fi

# 3. Install manager dependencies
cd "$ENV_NAME"
echo "Installing OQTOPUS Manager..."
if [ -n "$VERSION" ]; then
    oqtopus manager install "$VERSION"
else
    oqtopus manager install
fi

cat <<EOF

Setup complete.

Next steps:
  cd ${ENV_NAME}
  oqtopus manager start
EOF
