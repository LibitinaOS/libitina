# PixelNews feed algorithm

PixelNews is the app’s “Feed” experience. In this repo, the *actual* feed backend currently lives inside the Windows Central widget module:

- Backend implementation: `widgets/windows_central/main.py`
- Feed API endpoint: `GET /widgets/windows_central/stories.json` (registered via `register(app, url_prefix)`)
- Personalization state: `userfeedconfig.json`
- Default source lists: `default_news.json`

This document describes how stories are selected, ordered, and served.

## High-level pipeline

1. **Load settings**
   - Read `userfeedconfig.json` (region/language + personalization).
   - Read `config.json` (optional overrides for sources/settings).
   - Read `default_news.json` (region/language defaults).

2. **Choose sources**
   - Start from defaults for the user’s `region_language`.
   - Optionally override with `config.json` widget settings.
   - Apply user hidden-source filters.

3. **Fetch each source** (RSS over HTTP(S))
   - Download RSS/Atom bytes.
   - Parse items into a normalized story dict.
   - Apply per-source “ignore fields” fixes.

4. **Merge sources into one feed**
   - If `use_weighted_sources` is on: mix sources using a weighted selection algorithm.
   - Otherwise: merge all items and sort by newest.

5. **Cache + paginate**
   - Cache up to `MAX_STORIES` items.
   - Slice with `offset`/`limit` for the UI.

## Inputs and configuration

### `userfeedconfig.json` (personalization)

Parsed by `_get_userfeed_config_cached()`.

Current keys used by the algorithm:

- `region_language` (string)
  - Example: `"United States (English)"`
  - Used to pick default sources from `default_news.json`.

- `hidden_sources` (list of strings)
  - List of source ids to exclude.
  - Matching is case-insensitive.

- `followed_channels` (list of strings)
  - List of channel names/ids the user “follows”.
  - Each entry is normalized via `_channel_id()` (lowercased, non `[a-z0-9_-]` collapsed to `_`, max length 64).
  - Used only to **boost sources** whose configured channels overlap.

Notes:

- The UI in `main.py` calls this “Personalize PixelNews”. At the moment several tabs are labeled “dummy”, but the backend already supports `hidden_sources` + `followed_channels`.

### `default_news.json` (default sources by language/region)

Read by `_get_default_news_cached()` and selected via `_default_sources_for_region_language()`.

Supported schemas (new + back-compat):

- New schema:
  - `global`: list of sources always included
  - `language`: `{ "English": [...] }`
  - `language_region`: `{ "English": { "United States": [...] } }`

- Older draft schema (still supported):
  - `default`: list
  - `languages`: `{ "English": [...] }`
  - `regions`: `{ "United States (English)": [...] }`

Selection details:

- `region_language` is parsed as `"Region (Language)"`.
- Combined sources are collected in this order:
  1. `global`
  2. `language[Language]`
  3. `language_region[Language][Region]` (or the full `region_language` string as a fallback key)
  4. Back-compat blocks (`default`, `languages`, `regions`)
- If multiple entries share an `id`, the **last one wins** (deduped by `id`).

### `config.json` (optional overrides)

If `config.json` contains:

```json
{
  "widgets": {
    "windows_central": {
      "use_weighted_sources": true,
      "sources": [ ... ]
    }
  }
}
```

then `_get_news_settings()` will use those values instead of `default_news.json`.

If `widgets.windows_central` is missing (the default in this repo), the feed uses:

- `use_weighted_sources = true`
- `sources = defaults derived from default_news.json`

### Source object schema

Sources are normalized by `_normalize_sources()`.

Required:

- `url` (string): RSS/Atom feed URL

Key fields:

- `id` (string)
  - Lowercased and sanitized to `[a-z0-9_-]{1,64}`.

- `name` (string)
- `tagline` (string, optional)
- `channels` (list or comma-separated string)
  - Stored as list of `{ id, name }` after normalization.

Feed-mixing knobs:

- `max_stories` (int)
  - Caps the number of items *kept* from this source after fetching.
  - Alias: `allocate` (back-compat).

- `priority` (int)
  - Influences selection only when randomness is enabled (see algorithm below).

- `randomness` (float in `[0,1]`)
  - Alias: `spread` (back-compat).

UI metadata (optional):

- `ownership_category` (string)
- `owner_name` (string)
- `owner_url` (string)
  - If present, the PixelNews “Source” dialog renders the owner name as a clickable link.

Per-source parsing fixes:

- `ignore` (dict) / `ignore_fields` (list)
  - Supported keys: `categories`, `author`, `thumb_url`
  - When enabled, those fields are cleared for all items from that source.

Metadata for UI dialogs (not used for ranking except channel overlap):

- `ownership_category`, `owner_name`, `political_lean`, `logo_path`, `description`

## Fetch and parse

### Fetch

Each source is fetched in `_fetch_source_stories()`:

- Opens the feed URL with an 8 second timeout.
- Uses a redirect guard that can raise `LightspeedBlockedError` when a “Lightspeed Filter Agent” is installed and a redirect targets `localhost`.

### Parse RSS/Atom into normalized story dicts

`_parse_rss()` produces a list of dicts with these keys:

- `title` (string)
- `link` (string)
- `author` (string or `None`)
- `categories` (list of strings)
- `thumb_url` (string or `None`)
- `publication` (string; taken from source name/id)
- `published_at` (float timestamp seconds or `None`)
- `source_id` (string; added by `_fetch_source_stories()`)

Implementation notes:

- Author parsing is “best effort”: if the author string contains `(...)`, the text inside parentheses is used.
- `published_at` is parsed from RSS `pubDate` via `email.utils.parsedate_to_datetime`.
- Thumbnails are extracted by looking for common RSS media tags (`thumbnail`, `content`, `enclosure`).

## Merge / ranking algorithm

The merge step happens in `get_stories_cached()`.

There are two modes:

### Mode A: classic merge (newest-first)

If `use_weighted_sources` is false:

1. Extend one list with all per-source items.
2. Sort descending by `published_at` (missing timestamps sort as `0.0`).

### Mode B: weighted multi-source mixing (default)

If `use_weighted_sources` is true:

- `combined = _mix_sources_weighted(sources, per_source_map, limit, followed_channels)`

This algorithm is designed to:

- Prefer newer stories
- Prefer sources with higher `priority` (when randomness is enabled)
- Avoid long streaks from the same source
- Prefer stories with richer metadata (image, categories, author, date)
- Boost sources whose channels overlap the user’s followed channels

#### Step 1: per-source queues

For each source, stories are sorted by an **effective** time value:

- `effective_published_at = published_at - penalty_seconds`

Where `penalty_seconds` increases when metadata is missing:

- Missing image: +6 hours
- Missing categories: +1 hour
- Missing author: +1 hour
- Missing date: +2 hours

So a story with missing metadata is treated as “older” than it really is.

#### Step 2: candidate weights

On each iteration, consider the *head* story from each non-empty source queue.

Compute a candidate weight $w$:

- Time decay:
  - If `published_at` missing: `w_time = 0.25`
  - Else: $w_{time} = e^{-age_{hours}/24}$

- Priority weight (only matters if randomness is non-zero):
  - Clamp priority to `[-20, 20]`.
  - Let `rnd = clamp(randomness, 0..1)`.
  - $w_{pri} = e^{priority * rnd * 0.45}$

- Story quality weight (multiplicative):
  - Missing image: `* 0.35`
  - Missing categories: `* 0.85`
  - Missing author: `* 0.90`
  - Missing date: `* 0.80`
  - Minimum: `0.01` (to avoid collapsing weights to 0)

- Channel boost (if user follows channels):
  - If a source has $k$ overlapping channels:
    - boost $= 1.60 * 1.15^{(k-1)}$

- “Spread out” penalty (discourage repeats):
  - If the last chosen source is the same as this candidate’s source and `rnd > 0`:
    - multiply by $(\max(0.05, 1-rnd))^{runLen}$

Overall:

$$
 w = w_{time} * w_{pri} * w_{quality} * w_{channel} * w_{spread}
$$

#### Step 3: choose which source contributes the next item

Let `max_rnd` be the maximum `randomness` across all sources.

- If `max_rnd <= 0.0`: fall back to “classic merge” behavior (using `effective_published_at`).

Otherwise, each iteration chooses a source in one of two ways:

- With probability $(1 - max\_rnd)$:
  - pick deterministically: the candidate with the best weight (tie-broken by `effective_published_at`).

- With probability $max\_rnd$:
  - pick randomly, weighted by $w$.
  - If total weight is 0, fall back to newest-by-`effective_published_at`.

After a source is chosen, the head story from that source queue is popped and appended to the combined feed.

#### What the algorithm does *not* do

- No semantic deduplication of stories across sources (e.g., same link from multiple feeds).
- No per-category ranking of individual stories (channels are per-source metadata today).

## Caching and pagination

### Story caching

`get_stories_cached()` caches the merged feed in `_stories_cache`.

Default cache TTL: 10 minutes (`ttl_seconds=10*60`).

The cache is considered valid only if:

- It is younger than `ttl_seconds`, and
- `config.json` mtime matches the last refresh, and
- `userfeedconfig.json` mtime matches the last refresh.

### Pagination

`/stories.json` always hydrates the cache with `limit=MAX_STORIES` (currently 200) so offset-based paging works.

Query params:

- `limit` (capped to `MAX_STORIES`)
- `offset` (0..`MAX_STORIES`)

Response includes:

- `stories`: sliced list
- `total`, `has_more`, `next_offset`
- `sources`: normalized sources
- `channels`: unique channels across all sources (for UI)
- `use_weighted_sources`
- `updated_at`
- optional `error`

## Error handling

- If *all* sources fail, the endpoint returns:
  - Cached stories if available, with an `error` like `"... (showing cached)"`, else
  - An empty list and an `error` message.

- Per-source status (success/failure, last error, last success time) is tracked in `_source_status` and exposed via `/status.json`.

## Practical tuning guide

- Increase variety:
  - Raise a source’s `randomness` toward `1.0`.
  - Ensure multiple sources have non-zero randomness; `max_rnd` controls how often the algorithm “rolls dice”.

- Increase a source’s presence:
  - Raise `priority` (only matters when randomness is non-zero).
  - Add followed channels that overlap the source’s `channels`.

- Prefer richer items:
  - The algorithm already demotes items missing images/categories/authors/dates.
  - If a feed’s metadata is consistently broken, consider using `ignore` to remove misleading fields (but note that missing fields then become penalized in ranking).

## Extension points

If you want PixelNews to be its own module (instead of living in the Windows Central widget), the main seams are:

- `get_stories_cached()` – orchestration + caching + personalization
- `_fetch_source_stories()` / `_parse_rss()` – ingestion
- `_mix_sources_weighted()` – ranking + interleaving
- `default_news.json` + `userfeedconfig.json` – data-driven configuration
