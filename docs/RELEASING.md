# Release checklist

## Before v0.1.0

1. Review the license and third-party code attribution. MIT covers this project's code;
   dependencies keep their own licenses.
2. Run `uv sync --locked --extra dev`, `uv run pytest -q`, the offline demo and `uv build`.
3. Install the wheel in a clean environment outside the source checkout. Check both
   `zx-code --help` and the compatibility command `agent --help`.
4. Require successful CI results on macOS/Linux with Python 3.11 and 3.12. Do not replace
   actual CI results with the local test report.
5. Review README, demo output, package metadata and CHANGELOG. Record remaining failures.

## GitHub Release

After merging the reviewed changes, create tag `v0.1.0` at the verified commit and a
GitHub Release titled `zx-code v0.1.0 (alpha)`. Use the 0.1.0 CHANGELOG entry as release
notes, attach `dist/zx_code-0.1.0.tar.gz` and `dist/zx_code-0.1.0-py3-none-any.whl`, and
mark this first alpha release as a pre-release. Check that assets download and install.
Before tagging, confirm this tag does not already exist.

## PyPI (optional, separate publication)

GitHub source installation works without PyPI. The name `zx-code` is not claimed by a
local package rename. Confirm name ownership and configure a publishing identity before
uploading. Do not add an install-from-PyPI badge or claim PyPI availability until the
published project and version have been verified. This repository does not store API tokens.
