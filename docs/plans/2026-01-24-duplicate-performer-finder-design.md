# Duplicate Performer Finder Design

A Python script that detects duplicate performers in Stash (those sharing the same stash-box ID for the same endpoint) and provides an interactive HTML interface to merge them.

## Problem

Stash allows multiple performers to have the same stash-box ID for the same endpoint. These are duplicates that should be merged, but there's no built-in UI to find or resolve them.

## Solution

A local Python web application that:
1. Fetches all performers with stash_ids from Stash
2. Detects duplicates (performers sharing the same endpoint + stash_id)
3. Serves an HTML report showing duplicate groups
4. Provides one-click merge using Stash's native `performerMerge` mutation

## Project Structure

```
scripts/
└── duplicate-performer-finder/
    ├── app.py              # Main Flask application
    ├── stash_client.py     # GraphQL client wrapper
    ├── requirements.txt    # Python dependencies
    ├── templates/
    │   └── report.html     # Jinja2 template for the UI
    ├── static/
    │   └── style.css       # Minimal styling
    └── .env.example        # Template for users
```

## Configuration

**.env file (gitignored):**
```
STASH_URL=http://localhost:9999
STASH_API_KEY=your-api-key-here
```

**Dependencies:**
- flask
- requests
- python-dotenv

## Data Fetching

Query all performers with stash_ids in a single request:

```graphql
query AllPerformersWithStashIDs {
  findPerformers(filter: { per_page: -1 }) {
    performers {
      id
      name
      alias_list
      gender
      country
      scene_count
      image_count
      gallery_count
      stash_ids {
        endpoint
        stash_id
      }
    }
  }
}
```

## Duplicate Detection

1. Build a dictionary keyed by `(endpoint, stash_id)` tuples
2. For each performer, add them to buckets based on their stash_ids
3. Filter to buckets with 2+ performers

```python
duplicates = {}

for performer in performers:
    for sid in performer['stash_ids']:
        key = (sid['endpoint'], sid['stash_id'])
        duplicates.setdefault(key, []).append(performer)

duplicates = {k: v for k, v in duplicates.items() if len(v) >= 2}
```

## HTML Report UI

**Layout:**
- Header with summary (total duplicate groups, endpoints involved)
- Groups organized by endpoint
- Each duplicate group shows the shared stash_id and all performers as cards

**Performer Card:**
- Name
- Aliases
- Gender
- Country (birth)
- Scene count
- Image count
- Gallery count
- "Keep This One" button

**Visual Hints:**
- Performer with highest content count gets a subtle green border (suggested keeper)
- Cards are equal width, horizontally scrollable if 3+ duplicates

## API Endpoints

| Route | Method | Purpose |
|-------|--------|---------|
| `/` | GET | Serve the HTML report |
| `/api/merge` | POST | Execute a performer merge |
| `/api/refresh` | GET | Re-fetch and return updated duplicates |

## Merge Flow

1. User clicks "Keep This One" on a performer card
2. JavaScript POSTs to `/api/merge`:
   ```json
   {"destination_id": "2", "source_ids": ["1", "3"]}
   ```
3. Flask calls Stash's `performerMerge` mutation:
   ```graphql
   mutation MergePerformers($source: [ID!]!, $destination: ID!) {
     performerMerge(input: { source: $source, destination: $destination }) {
       id
       name
     }
   }
   ```
4. On success, the duplicate group is removed from the DOM
5. A toast shows: "Merged into {performer name}"

## Startup Sequence

1. Load `.env` and validate required variables
2. Test connection to Stash with `systemStatus` query
3. Fetch all performers with stash_ids
4. Detect duplicates and cache in memory
5. Start Flask on `http://localhost:5000`
6. Print summary: "Found N duplicate groups. Open http://localhost:5000"

## Error Handling

- Missing `.env`: Print instructions to copy `.env.example`
- Invalid API key: "Authentication failed. Check your STASH_API_KEY"
- Stash unreachable: "Cannot connect to {STASH_URL}. Is Stash running?"
- No duplicates: Show friendly message in UI

## Usage

```bash
cd scripts/duplicate-performer-finder
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your credentials
python app.py
```

## Branch

`feature/duplicate-performer-finder`
