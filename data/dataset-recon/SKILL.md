---
name: dataset-recon
description: Reconnaissance on an unfamiliar dataset from a URL or local file. Gathers its docs, metadata and specs, even when they are scattered or decades old, pulls a small sample, decodes it with the proper reader (GDAL, wgrib2, ncdump, h5dump, pandas, pyarrow), and writes a short profile covering what the data is, the variables and parameters, grid size or resolution, file sizes, update frequency, and undocumented attributes or quirks. Use when the user points at a raw CSV with no docs, a GRIB2/NetCDF/HDF5 weather or climate product (NDFD, GFS, HRRR, ERA5), a data portal or directory listing, and asks what's in it or how to read it.
---

# Dataset recon

The goal is a short, trustworthy profile of a dataset that someone can act on: what it is, how to read it, how big and how fresh it is, and where the docs are wrong or silent. Facts come from the decoded sample. Docs explain those facts, and any disagreement between docs and data gets reported, not smoothed over.

## Output location

```bash
SKILL_DIR=<directory containing this SKILL.md>
REPO=$(git -C "$SKILL_DIR" rev-parse --show-toplevel 2>/dev/null)
if [[ -n $REPO && $(git rev-parse --show-toplevel 2>/dev/null) == "$REPO" ]]; then OUT=./tmp/dataset-recon; else OUT=./dataset-recon; fi
mkdir -p "$OUT/<short-name>"
```

The sample, the decoder dumps and `profile.md` all go in `$OUT/<short-name>/`.

## 1. Find the docs

- Start at the URL given. If it's a directory listing, list it: file names, sizes and timestamps already tell you the format, the cadence and the naming scheme.
- Look for metadata next to the data: `README*`, `*.xml` (ISO 19115/FGDC), `*.idx`, `*.json` (STAC, DCAT, Frictionless `datapackage.json`), `*.ctl`, `*.def`, `inventory`, `*.md5`, or a sibling `doc/` or `docs/` directory.
- Search the producer's site for the product name plus "documentation", "parameter table", "GRIB2 template" or "data dictionary". Old products often have docs split across several pages from different eras; collect them all and note each one's date.
- If a page is blocked, needs JS or returns a bot check, use the `web-fetch` skill.
- Record every source used, with its URL and date, for the profile's Sources section.

## 2. Pull a sample, small and cheap

- **Size first:** run `curl -sIL <url>` and read `Content-Length`, `Last-Modified`, `Content-Type` and `Accept-Ranges`. Ask the user before downloading anything over 200 MB.
- **CSV and text:** take the head only, with `curl -s -r 0-2000000 <url> > sample.csv`. Drop the last, probably partial, line.
- **GRIB2 with a `.idx` sidecar:** fetch one record's byte range from the idx instead of the whole file. Otherwise download a single small file, such as one NDFD element or sector (`ds.temp.bin`, not the CONUS bundle).
- **Archives:** list the contents first (`unzip -l`, `tar tzf`) and extract one member.
- Confirm what actually arrived with `file -b sample.*` and `file -bi sample.*` for the encoding. Don't trust the extension.

## 3. Decode with the right reader

| Format | Inventory and metadata | Values |
|--------|------------------------|--------|
| GRIB1/2 | `wgrib2 -v sample.grb2`, `wgrib2 -grid sample.grb2`, `wgrib2 -V -d 1 sample.grb2` | `wgrib2 -d 1 -stats`, or `gdalinfo -stats` |
| NetCDF / HDF5 | `ncdump -h`, `h5dump -H` | `xarray.open_dataset` |
| GeoTIFF / raster | `gdalinfo -stats -mm` | rasterio |
| Shapefile / GeoJSON / GPKG | `ogrinfo -so -al` | `ogrinfo -al -fid 0` |
| Parquet | `pyarrow.parquet.read_metadata` and the schema | pandas |
| CSV / TSV / fixed width | `csv.Sniffer`, `file -bi` | `pandas.read_csv(..., nrows=50000)`, then `.dtypes`, `.describe(include='all')`, null counts, distinct counts |
| JSON / NDJSON | top-level keys and the first record | `pandas.json_normalize` |

For gridded data, extract:
- grid type and projection (Lambert conformal, lat/lon, polar stereographic), with its parameters
- dimensions (nx, ny), spacing and units, and corner or bounding coordinates
- vertical levels
- reference time, forecast hours and valid times
- the packing type and the missing or bitmap value

For tabular data, extract:
- delimiter, encoding, header row, row count (estimate it from file size divided by bytes per row when only the head was sampled)
- per-column type, unit, range, null rate and cardinality
- likely keys and the date or time columns with their time zone

## 4. Hunt for undocumented details

- **Missing-value sentinels:** -9999, 9999, 9.999e20, -32768, empty strings, "NA", 0 used as missing.
- **Scaled or offset integers:** `scale_factor`/`add_offset`, GRIB decimal and binary scale, temperatures in tenths.
- **Local and nonstandard codes:** GRIB2 local parameter numbers (192 to 254) and local tables such as NDFD's, local templates, unexpected generating-process or center IDs, and `var discipline=… center=… local_table=…` entries from `wgrib2 -v` that it can't name.
- **Time traps:** local time vs UTC, reference time vs valid time, accumulation windows, end-of-period stamps.
- **Grid traps:** a grid offset from the documented one, flipped row order, a 0 to 360 vs -180 to 180 longitude convention, cell center vs corner.
- **Unit and value drift:** columns or fields that appear in the data but not the docs, or the reverse; values outside the documented range; units that disagree with the docs.

## 5. Update frequency

Infer the cadence from directory timestamps across several files, `Last-Modified`, the reference times embedded in file names or metadata, and the producer's stated schedule. Report both the stated and the observed cadence when they differ.

## 6. Write `profile.md`

Keep it to a screen or two:

```markdown
# <Dataset name> profile

**Source:** <url> · **Sampled:** <file> (<size>) on <date> · **Format:** <format and version>

## What it is
<two or three sentences: producer, purpose, coverage>

## Contents
| Variable / column | Description | Units | Levels / range | Notes |

## Structure
- Grid / rows: <projection, nx x ny, spacing, bounds>  or  <rows, columns, keys>
- Time: <reference times, forecast hours, valid time rule>
- Files: <naming scheme, typical size per file and per day>

## Freshness
- Stated cadence: <...> · Observed: <...> · Latency: <...>

## Undocumented or surprising
- <each finding, with the evidence: the command output or a sample value>

## How to read it
<the one or two commands or lines of Python that load it correctly, including the missing-value and scale handling>

## Sources
- <doc title> (<url>, <date>)
```

Show the user the profile path and a three-line summary. Keep the sample unless the user wants it removed.
