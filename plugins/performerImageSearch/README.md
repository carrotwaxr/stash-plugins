# Performer Image Search

Search several image sources from a performer's page and set a performer image with one click. It works for female, male, trans and JAV performers.

## Requirements

- Python 3.9 or newer. There are no packages to install.
- The Stash server needs internet access. The searches run on the server, not in your browser.

## Install

Copy the `performerImageSearch` folder into your Stash `plugins` folder and reload plugins under **Settings > Plugins**.

## Use

1. Open a performer's page and click the image search button.
2. Click **Search**. Every enabled source searches at the same time and its results appear as it finishes.
3. Pick an image to preview it. Confirm to set it as the performer image.

Arrow keys move between images in the preview and Escape closes it. These keys do not trigger Stash's own hotkeys while the preview is open.

If the full-size image fails to load, the preview shows the thumbnail with a notice. Confirm is disabled if neither loads.

Only images hosted by the source that found them can be set as a performer image. DuckDuckGo results must be on a public host.

## Sources

| Source | Good for | Covers |
|---|---|---|
| Babepedia | Curated photos | Female performers |
| PornPics | Mainstream galleries | Mainstream performers, including male |
| FreeOnes | A large database. Full-size images, not square crops. | Female, male and trans performers |
| EliteBabes | The gallery's own photos only | Female performers |
| Boobpedia | Wiki-style database | Female performers |
| JavDatabase | The performer's own images only. Covers are thumbnail size because full-size covers do not exist. Similar-idol thumbnails and sponsored ads are left out. | Japanese adult video performers |
| DuckDuckGo | Web image search with SafeSearch off. Works for any performer. | Anyone |

### What each source searches for

Every site source searches by the performer's name. Editing the query box does not change their results.

Only DuckDuckGo uses the text in the query box. The box starts as the performer's name plus your search suffix. Change it there to narrow or widen the DuckDuckGo results.

## Settings

Set these under **Settings > Plugins > Performer Image Search**.

| Setting | Default | What it does |
|---|---|---|
| Default Search Suffix | `pornstar` | Added after the performer's name in the query box. Only DuckDuckGo uses it. |
| Default Layout | `All` | The layout filter the modal starts with. Values are `All`, `Portrait`, `Landscape` and `Square`. |
| Enable Babepedia | on | Search Babepedia. |
| Enable PornPics | on | Search PornPics. |
| Enable FreeOnes | on | Search FreeOnes. |
| Enable EliteBabes | on | Search EliteBabes. |
| Enable Boobpedia | on | Search Boobpedia. |
| Enable JavDatabase | on | Search JavDatabase. |
| Enable DuckDuckGo Images | **off** | Search DuckDuckGo. |

DuckDuckGo is off by default because it often rate-limits searches. Since 1.5.0 this applies to everyone, including people who never touched the toggle. Turn it on if you want it.

## Layout filter

The filter uses each image's width divided by its height.

| Layout | Ratio |
|---|---|
| Any (`All`) | No filter |
| Portrait | below 0.9 |
| Square | 0.9 to 1.1, both included |
| Landscape | above 1.1 |

The filter uses the real size where it is known: the source's own data first, then the loaded full image, then the thumbnail. Images whose size is not known yet pass every filter.

## Source status chips

Each enabled source shows a chip above the results. Hover over a chip to see the error message.

| Chip | Meaning |
|---|---|
| pending | The source has not answered yet. |
| ok (n) | It found n images (at least 1) and every page loaded. If some results came from unexpected hosts, they were dropped; hover to see how many. |
| empty | It found no images and nothing failed: the source has nothing for this performer. |
| partial (n) | It found n images (at least 1), but some of its pages failed or timed out. Hover to see which. |
| error | The source failed, or it found no images and some of its pages failed, or every image it found came from an unexpected host. Hover for the reason. |
| blocked | The site refused the request (HTTP 403 or 429, or a Cloudflare challenge). For DuckDuckGo this means it is rate-limiting you. |
| timeout | The site did not answer in time, or none of its pages loaded in time. |

Each source gets 25 seconds on the server. Gallery pages are fetched in parallel. The browser gives up on a source after 45 seconds.

DuckDuckGo retries once when it is blocked, if at least 5 of its 25 seconds are left. If the retry is blocked too, or there is no time for one, it reports that it is rate-limited.

## Network requirements

Some sources sit behind Cloudflare. Babepedia does, for one. They may block requests from cloud hosts, VPNs and datacenter addresses. If your Stash runs on one of those, expect those sources to show `blocked`. A home connection works best.

## Troubleshooting

- **A source shows `blocked`.** The site refused your server's address. Try again later, or run Stash from a different network. Turning that source off removes the chip.
- **DuckDuckGo shows `blocked` or rate-limited.** Wait a few minutes and search again.
- **A source shows `empty`.** It has no page for that name. Check the performer's name and aliases, or try another source.
- **A source shows `timeout`.** The site is slow or unreachable. Search again.
- **A source shows `partial`.** Some pages failed. Search again to try the missing pages.
- **Every source fails.** Check that the Stash server can reach the internet.
- **Editing the query does nothing for a site.** Site sources use the performer's name. Only DuckDuckGo uses the query box.
- **DuckDuckGo is missing.** It is off by default. Turn on **Enable DuckDuckGo Images** in the plugin settings.
- **No images match the filters.** Set the layout back to Any.
- **Confirm is disabled.** Neither the full image nor its thumbnail loaded. Pick another image.

## Changelog

### 1.5.0
- DuckDuckGo is now off by default, even if you never changed the toggle. Turn it on in the plugin settings.
- Each source shows a status chip: ok, empty, partial, error, blocked or timeout. Hover to see the error.
- Each source has a 25 second limit and gallery pages load in parallel. The browser waits up to 45 seconds.
- FreeOnes returns full-size images instead of square crops.
- EliteBabes returns only the gallery's own photos.
- JavDatabase returns only the performer's own images, without similar-idol thumbnails or sponsored ads.
- DuckDuckGo retries once when blocked, then reports that it is rate-limited.
- The layout filter uses real image sizes. Portrait is below 0.9, Square is 0.9 to 1.1 and Landscape is above 1.1. Unknown sizes pass every filter.
- Preview: arrow keys and Escape no longer trigger Stash's hotkeys. A failed full image falls back to the thumbnail with a notice, and Confirm is disabled when neither loads.

### 1.4.1
- Only images hosted by the source that found them can be set as a performer image.
