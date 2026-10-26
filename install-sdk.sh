#!/bin/sh
# Builds and installs the SDK: runs tools/install_sdk.py with the Python you select.
#
#   ./install-sdk.sh [--dev] [--editable] [pip options]
#
# Contributors use --dev --editable. With no options the packages are built and
# installed as wheels, as a consumer would get them. The Python is the one named
# by the PYTHON variable, or python3 on your PATH:
#
#   PYTHON=/path/to/approved/python3 ./install-sdk.sh
here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd) || exit 1
python=${PYTHON:-python3}
if ! "$python" -c "import sys" >/dev/null 2>&1; then
    echo "error: cannot run '$python'" >&2
    echo "fix: install Python 3.11 or newer, or set PYTHON to the full path of your approved Python, then run this again" >&2
    exit 1
fi
exec "$python" "$here/tools/install_sdk.py" "$@"
