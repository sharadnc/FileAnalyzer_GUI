# File Analyzer

PyQt5 desktop app for exploring pipe- or CSV-delimited data files with metadata-driven **Summary**, **Visualize**, **Data Grid**, and **Pivot** views. Analytics use DuckDB; charts use Plotly in an embedded web view.

## Requirements

- Python 3.10+
- Windows (primary) or Unix-like systems for development

## Quick start

1. Create and activate a virtual environment in the project root (or point `VENV_PYTHON` at an existing interpreter).

2. Install dependencies:

   ```text
   pip install -r requirements.txt
   ```

3. Copy environment defaults and edit paths for your machine:

   ```text
   copy .env.example .env
   ```

4. Launch the GUI:

   ```text
   run_file_analyzer.bat
   ```

   Debug console: `run_file_analyzer_debug.bat`

## Configuration

Settings load from `<project>/.env` (see `.env.example`). Important variables:

| Variable | Purpose |
|----------|---------|
| `WINDOW_TITLE` | Main window title |
| `VENV_PYTHON` | Python used by the `.bat` launchers |
| `DEFAULT_DATA_PATH` | Welcome screen default data file |
| `DEFAULT_META_PATH` | Metadata file (text `*_Meta`, or `.xlsx`/`.xls`) |
| `PAGE_SIZE_DEFAULT` | Grid page size |

Metadata can be derived as `<data_filename>_Meta` when `DEFAULT_META_PATH` is empty.

## Project layout

```text
src/file_analyzer/     Application package (UI, DuckDB, meta parser, pivot)
src/main.py            GUI entry point
tests/                 Pytest suite
sample/                Small example datasets
scripts/               Launcher helpers
tools/                 Dev utilities
run_file_analyzer.bat  Windows launcher
pyproject.toml         Project metadata and tool config
requirements.txt       Pip dependencies
```

## Tests

From the project root:

```text
set PYTHONPATH=src
python -m pytest
```

## Notes

- Session databases and chart HTML are created under the system temp directory by default (`TEMP_BASE_DIR` in `.env` overrides the base path).
- Do not commit `.env`; it may contain machine-specific paths.
- `sample/LoanPop.txt` and `sample/*_generated.txt` are gitignored (large generated files). Use `run_generate_pipe_data.bat` to recreate them locally.

## Git-ready layout

These files are set up for version control:

| File | Role |
|------|------|
| `.gitignore` | Excludes venv, `.env`, caches, IDE artifacts, large generated data |
| `.gitattributes` | Line endings and binary file handling |
| `.editorconfig` | Shared editor formatting |
| `.env.example` | Committable environment template (copy to `.env`) |
