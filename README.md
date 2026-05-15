# File Analyzer

PyQt5 desktop app for exploring pipe- or CSV-delimited data files with metadata-driven **Summary**, **Visualize**, **Data Grid**, and **Pivot Data** views. Analytics use DuckDB; charts use Plotly in an embedded web view.

## Features

### Welcome / load

- **Data** path, **Browse meta** (optional Excel or text `*_Meta`), **Browse Templates** (files under `templates/`), delimiter, and **# of decimals**.
- Defaults for data and meta paths come from the project `.env` file (`DEFAULT_DATA_PATH`, `DEFAULT_META_PATH`).
- Excel metadata (`.xlsx` / `.xls`) must include: **Field Name**, **Primary Key**, **FieldType**, **Datatype**, **Field Length**, **Field Description** (any column order). A warning dialog appears if any are missing.
- Text metadata uses the legacy `FileKey` + pipe-delimited field rows format.
- **Load Data** runs ingestion and quick stats in the background. The status bar shows **Loading time - hh:mm:ss** on the left.

### Summary

- Per-field profiling: nulls, distributions, top values, bins, outliers, duplicate PK hints.
- **Copy to Clipboard** exports formatted HTML for Excel.

### Visualize

- Multi-select **Dimensions** and **Measures**, then **Generate** for Line, Pie, Bar, Stacked Bar, Histogram, or Scatter.
- Colorblind-friendly chart palette (Plotly qualitative colors with Tableau-style fallback).
- Pie charts use tight margins and show slice labels only when there is enough room; otherwise legend and hover.
- Spinner overlay while the chart is building.
- Chart table with column quick filters, sort, and pagination.
- Click a chart mark to filter the chart table; the selection is also sent to **Data Grid** and **Pivot Data** as a chart-linked filter.

### Data Grid

- Split **Dimensions** / **Measures** filter panels with searchable multi-select checklists.
- **Apply Filters** / **Clear Filters** (panel, chart-click, and column header filters).
- Pager line: **Filters - …** (e.g. `NAME in (Alabama,Texas)` or `chart-linked selection (NAME in (US))`).
- Column visibility drawer, optional sort-by-name, live header filters (including measure ranges like `100-400`).
- Pagination, **Export Filtered CSV**, **Copy to Clipboard** (TSV).

### Pivot Data

- Same filter panels and **Filters - …** display as the Data Grid (also shown above the pivot shelf).
- **Rows**, **Columns** (optional), and **Values** lists in a **resizable** horizontal splitter.
- One control row: **Aggregate**, **Update Pivot**, **Expand All**, **Collapse All**.
- **Resizable** vertical splitter between the field area and the pivot results table.
- Shelf summary: `Selected Rows: …; Columns: …; Values: …; Aggregation: …`
- Hierarchical pivot with expand/collapse; **Export Pivot CSV**.

### Status bar

- **Loading time - hh:mm:ss** (left).
- **Zoom** slider (right, Word/Excel style). Changing zoom prompts **Load Data** again; filters and tab layout state are restored when possible.

## Requirements

- Python 3.10+
- Windows (primary); Unix-like hosts supported for development
- PyQt5 + PyQtWebEngine (see `requirements.txt`)

## Quick start

1. Create a virtual environment in the project root (or set `VENV_PYTHON` in `.env` to an existing interpreter).

2. Install dependencies:

   ```text
   pip install -r requirements.txt
   ```

3. Copy environment defaults and edit paths for your machine:

   ```text
   copy .env.example .env
   ```

4. Optional: place Excel metadata under `templates/` (for example `LoanPop.xlsx`).

5. Launch the GUI:

   ```text
   run_file_analyzer.bat
   ```

   Use `run_file_analyzer_debug.bat` if the window closes with no visible error.

## Configuration

Settings load from `<project>/.env` (see `.env.example`).

| Variable | Purpose |
|----------|---------|
| `WINDOW_TITLE` | Main window title |
| `VENV_PYTHON` | Python used by the `.bat` launchers |
| `DEFAULT_DATA_PATH` | Welcome screen default data file (relative paths resolve from project root) |
| `DEFAULT_META_PATH` | Metadata file (text `*_Meta`, or `.xlsx`/`.xls`); blank derives `<data_filename>_Meta` |
| `PAGE_SIZE_DEFAULT` | Data Grid page size |
| `QUICK_STATS_TOP_N` | Top-N values in hover quick stats |
| `QUICK_STATS_MAX_WORKERS` | Thread pool size for quick stats on load |
| `DUCKDB_STORAGE_MODE` | DuckDB storage mode (default `file`) |
| `TEMP_BASE_DIR` | Base directory for per-session temp folders |

Launchers read `VENV_PYTHON` from `.env` via `scripts/read_venv_from_dotenv.bat`.

## Synthetic test data

Generate a pipe-delimited file from Excel metadata in `templates/`:

```text
run_generate_pipe_data.bat --records 10000000 --output sample\LoanPop.txt
```

Large outputs such as `sample/LoanPop.txt` are gitignored; use `sample/LoanPop_small.txt` for a small committed fixture.

## Project layout

```text
src/file_analyzer/       Application package (UI, DuckDB, meta, pivot)
src/main.py              GUI entry point
tests/                   Pytest suite
sample/                  Example datasets (see sample/README.txt)
templates/               Excel metadata workbooks (see templates/README.txt)
scripts/                 Launcher helpers (.bat, .vbs)
tools/                   Dev utilities (generate_pipe_data, verify steps)
run_file_analyzer.bat    Windows launcher (hidden console)
run_file_analyzer_debug.bat
pyproject.toml           Project metadata and tool config
requirements.txt         Pip dependencies
AI_FileAnalyze.txt       Product plan and implementation notes
```

## Tests

From the project root:

```text
set PYTHONPATH=src
python -m pytest
```

## Notes

- Each **Load Data** uses an isolated DuckDB database and temp folder under the system temp directory unless `TEMP_BASE_DIR` is set in `.env`.
- Do not commit `.env`; copy from `.env.example` instead.
- **FieldType** rules: `DISPLAY` (grid only), `YYYYMMDD` (dimension-style dates shown as eight-digit text).
- Layout splitter positions and zoom percent persist via `QSettings` when the app closes.

## Version control

The repository is prepared for git: `.gitignore`, `.gitattributes`, `.editorconfig`, and `.env.example` are included.

| Tracked | Ignored (local only) |
|---------|----------------------|
| `src/`, `tests/`, `tools/`, `scripts/`, launchers | `.venv/`, `.env` |
| `requirements.txt`, `pyproject.toml` | `.pytest_cache/`, `.ruff_cache/` |
| `sample/LoanPop_small.txt`, other small fixtures | `sample/LoanPop.txt`, `sample/*_generated.txt` |
| `templates/` (your `.xlsx` meta files) | `.cursor/`, `.specstory/`, `*.duckdb`, session temps |
