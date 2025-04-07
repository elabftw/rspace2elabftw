# rspace2elabftw

## Description

This python program imports RSpace data into eLabFTW.

From RSpace, you can export your data as `.eln` and use this script to import its content into eLabFTW.

## Usage

Requires `uv`: [Installation instructions](https://github.com/astral-sh/uv?tab=readme-ov-file#installation)

~~~bash
git clone https://github.com/elabftw/rspace2elabftw
cd rspace2elabftw
# install dependencies
uv sync --frozen
# configure it
export API_HOST_URL=https://elab.example.org/api/v2
export API_KEY=5-abc123...
# run program
uv run main.py /path/to/export.eln
~~~

# License

MIT
