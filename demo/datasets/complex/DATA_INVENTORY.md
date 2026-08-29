# dataset_complex / Scrapy middleware_pipeline — Data Inventory

## Source

- **Repo**: scrapy (https://github.com/scrapy/scrapy)
- **Commit**: `3c6a62db47536df89676ab9d7347acf5c5c8a2e4`
- **Commit date**: 2026-08-18 (today's upstream HEAD)
- **Sparseness**: `git clone --depth 1 --filter=blob:none --sparse`; only 4 subdirs pulled.

## What was copied (all byte-identical, md5-verified against upstream)

### Source code under `source/` — 26 files, 4,341 lines

| Subdir | Files | Lines | Per-file breakdown |
|---|---|---|---|
| `downloadermiddlewares/` | 13 (incl. __init__.py) | 1,684 | cookies(167), defaultheaders(40), downloadtimeout(44), httpauth(88), httpcache(165), httpcompression(190), httpproxy(106), offsite(137), redirect(280), retry(202), robotstxt(141), stats(81), useragent(43) |
| `spidermiddlewares/` | 7 (incl. __init__.py) | 929 | base(112), depth(133), httperror(92), metacopy(64), referer(445), start(31), urllength(52) |
| `pipelines/` | 3 (incl. __init__.py) | 1,563 | media(345), images(277), files(782) |
| `scrapy_middleware.py` | 1 | 165 | The shared MiddlewareManager / MiddlewareMixin framework code |

### Tests under `tests/` — 30 files, 11,845 lines, 512 test functions

```
test_middleware.py                                5 tests
test_downloadermiddleware.py                     13 tests
test_downloadermiddleware_cookies.py             42 tests
test_downloadermiddleware_defaultheaders.py       2 tests
test_downloadermiddleware_downloadtimeout.py      5 tests
test_downloadermiddleware_httpauth.py            16 tests
test_downloadermiddleware_httpcache.py           22 tests
test_downloadermiddleware_httpcompression.py     58 tests
test_downloadermiddleware_httpproxy.py           28 tests
test_downloadermiddleware_offsite.py             14 tests
test_downloadermiddleware_redirect.py            24 tests
test_downloadermiddleware_redirect_metarefresh.py  8 tests
test_downloadermiddleware_retry.py               39 tests
test_downloadermiddleware_robotstxt.py           14 tests
test_downloadermiddleware_stats.py                5 tests
test_downloadermiddleware_useragent.py            3 tests
test_pipelines.py                                13 tests
test_pipeline_files.py                           56 tests
test_pipeline_images.py                          23 tests
test_pipeline_media.py                          24 tests
test_spidermiddleware.py                         27 tests
test_spidermiddleware_base.py                     4 tests
test_spidermiddleware_depth.py                    6 tests
test_spidermiddleware_httperror.py               12 tests
test_spidermiddleware_metacopy.py                 8 tests
test_spidermiddleware_output_chain.py            10 tests
test_spidermiddleware_process_start.py            5 tests
test_spidermiddleware_referer.py                 21 tests
test_spidermiddleware_start.py                    2 tests
test_spidermiddleware_urllength.py                3 tests
```

## What is the "common component" here?

Four subsystems, each with a clear interface contract and multiple implementations:

| Subsystem | Common interface | Number of impls |
|---|---|---|
| Download middlewares | `process_request` / `process_response` / `process_exception` | 13 classes |
| Spider middlewares | `process_spider_input` / `process_spider_output` / `process_spider_exception` | 6 classes |
| Item pipelines | `process_item(item, spider)` | 3 classes (media / images / files) |
| Middleware framework | `MiddlewareManager` + `MiddlewareMixin` | 1 shared file |

The "common component" a baseline extraction should be able to identify:

1. **`MiddlewareManager`** (in `scrapy_middleware.py`) — the chainer: instantiates middleware classes from settings, routes calls through them, handles `open_spider` / `close_spider` lifecycle.
2. **`MiddlewareMixin`** — the base mixin all three subsystem managers inherit from.
3. **`from_crawler(cls, crawler)` classmethod** — convention used by every middleware/pipeline to receive the crawler instance.
4. **`open_spider` / `close_spider` lifecycle hooks** — used by 11 of 13 download middlewares plus all 3 pipelines.
5. **`crawler.settings.get(...)`** pattern — settings access convention across the board.

What stays algorithm-specific:

- `RetryMiddleware`: exponential backoff, retry counts, priority-aware scheduling.
- `RedirectMiddleware`: max-redirects, meta-refresh detection, redirect URL parsing.
- `DepthMiddleware`: per-spider depth counter.
- `RefererMiddleware`: LRU cache + referrer policy.
- `FilesPipeline` / `ImagesPipeline`: media download, thumbnailing, file URI parsing.

## How to use this for the baseline

1. **Input**: the 24 non-`__init__.py` Python files under `source/` (about 4,000 lines total).
2. **Pass rate before**: install Scrapy (`pip install scrapy`), run the 30 test files against the upstream source to confirm baseline pass rate is 100%. Then locally swap in the `source/` copies (replacing the matching files in a venv or staging dir) and re-run to confirm pass rate is still 100% for our copies (it should be — md5 matches).
3. **Run the baseline** (LLM direct extraction) over the `source/` files.
4. **Compare outputs**: `before` (current `source/` files) vs `after` (baseline's refactored files). Compare pass rate, MDL, tokens, API coverage.
5. **Expected "good extraction"**: the baseline should produce
   - A `common.py` containing `MiddlewareManager` + `MiddlewareMixin` + the `from_crawler` / `open_spider` / `close_spider` patterns.
   - Each middleware/pipeline slimmed down to its algorithm-specific core (retry math, redirect parsing, depth counter, etc.).
   - Total MDL reduced by 30–50% relative to original (rough estimate based on how much of each file is framework boilerplate vs algorithm).

## Caveats for baseline interpretation

- Some files are very small (e.g. `useragent.py` 43 lines, `downloadtimeout.py` 44 lines). The "common component" extracted from these may be larger than the per-file-specific code that remains. That's fine — it's a feature of the demo, not a bug.
- `referer.py` (445 lines) and `files.py` (782 lines) are the two outliers by size. The baseline may handle these worse than the smaller files — useful signal for "where does the baseline break?"
- Total ~4K lines is at the upper end of what fits comfortably in one LLM context. If you hit context limits, run the baseline on each subsystem separately (download middlewares as one extraction task, spider middlewares as another, pipelines as a third).

## Verification

56 files (26 source + 30 test) md5-checked against upstream at `3c6a62db4` — all byte-identical.


## Prepared manifest (generated)

- Manifest: `cluster_manifest.json` — one cluster `0`, 24 member files, 4182 source lines.
- Originals: `clusters/0/original/file_*.py` (byte-identical copies of `source/`).
- Test dir: `demo/datasets/complex/tests` — includes upstream test infra (`tests/utils`, `tests/mockserver`, `tests/spiders.py`, `tests/sample_data`, `tests/keys`) fetched at commit 3c6a62db47536df89676ab9d7347acf5c5c8a2e4.
- Shared test files (cluster level): `test_downloadermiddleware.py`, `test_spidermiddleware.py`, `test_spidermiddleware_output_chain.py`, `test_pipelines.py`.

| file_id | source | rel_path | pytest files |
|---|---:|---|---|
| file_000.py | cookies.py | `downloadermiddlewares/cookies.py` | test_downloadermiddleware_cookies.py |
| file_001.py | defaultheaders.py | `downloadermiddlewares/defaultheaders.py` | test_downloadermiddleware_defaultheaders.py |
| file_002.py | downloadtimeout.py | `downloadermiddlewares/downloadtimeout.py` | test_downloadermiddleware_downloadtimeout.py |
| file_003.py | httpauth.py | `downloadermiddlewares/httpauth.py` | test_downloadermiddleware_httpauth.py |
| file_004.py | httpcache.py | `downloadermiddlewares/httpcache.py` | test_downloadermiddleware_httpcache.py |
| file_005.py | httpcompression.py | `downloadermiddlewares/httpcompression.py` | test_downloadermiddleware_httpcompression.py |
| file_006.py | httpproxy.py | `downloadermiddlewares/httpproxy.py` | test_downloadermiddleware_httpproxy.py |
| file_007.py | offsite.py | `downloadermiddlewares/offsite.py` | test_downloadermiddleware_offsite.py |
| file_008.py | redirect.py | `downloadermiddlewares/redirect.py` | test_downloadermiddleware_redirect.py, test_downloadermiddleware_redirect_metarefresh.py |
| file_009.py | retry.py | `downloadermiddlewares/retry.py` | test_downloadermiddleware_retry.py |
| file_010.py | robotstxt.py | `downloadermiddlewares/robotstxt.py` | test_downloadermiddleware_robotstxt.py |
| file_011.py | stats.py | `downloadermiddlewares/stats.py` | test_downloadermiddleware_stats.py |
| file_012.py | useragent.py | `downloadermiddlewares/useragent.py` | test_downloadermiddleware_useragent.py |
| file_013.py | base.py | `spidermiddlewares/base.py` | test_spidermiddleware_base.py |
| file_014.py | depth.py | `spidermiddlewares/depth.py` | test_spidermiddleware_depth.py |
| file_015.py | httperror.py | `spidermiddlewares/httperror.py` | test_spidermiddleware_httperror.py |
| file_016.py | metacopy.py | `spidermiddlewares/metacopy.py` | test_spidermiddleware_metacopy.py |
| file_017.py | referer.py | `spidermiddlewares/referer.py` | test_spidermiddleware_referer.py |
| file_018.py | start.py | `spidermiddlewares/start.py` | test_spidermiddleware_start.py, test_spidermiddleware_process_start.py |
| file_019.py | urllength.py | `spidermiddlewares/urllength.py` | test_spidermiddleware_urllength.py |
| file_020.py | media.py | `pipelines/media.py` | test_pipeline_media.py |
| file_021.py | images.py | `pipelines/images.py` | test_pipeline_images.py |
| file_022.py | files.py | `pipelines/files.py` | test_pipeline_files.py |
| file_023.py | scrapy_middleware.py | `scrapy_middleware.py` | test_middleware.py |
