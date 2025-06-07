# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

import re
import time
from base64 import urlsafe_b64encode
from datetime import datetime, timedelta
from html import unescape
from urllib.parse import urljoin, quote_plus

from bs4 import BeautifulSoup

from quasarr.downloads.sources.al import retrieve_and_validate_session, invalidate_session
from quasarr.providers.imdb_metadata import get_localized_title
from quasarr.providers.log import info, debug

hostname = "al"
supported_mirrors = ["rapidgator", "ddownload"]


def convert_to_rss_date(date_str: str) -> str:
    parsed = datetime.strptime(date_str, "%d.%m.%Y - %H:%M")
    return parsed.strftime("%a, %d %b %Y %H:%M:%S +0000")


def parse_relative_date(raw: str) -> datetime | None:
    m = re.match(r"vor\s+(\d+)\s+(\w+)", raw)
    if not m:
        return None
    num = int(m.group(1))
    unit = m.group(2).lower()
    if unit.startswith("sekunde"):
        delta = timedelta(seconds=num)
    elif unit.startswith("minute"):
        delta = timedelta(minutes=num)
    elif unit.startswith("stunde"):
        delta = timedelta(hours=num)
    elif unit.startswith("tag"):
        delta = timedelta(days=num)
    elif unit.startswith("woche"):
        delta = timedelta(weeks=num)
    elif unit.startswith("monat"):
        delta = timedelta(days=30 * num)
    elif unit.startswith("jahr"):
        delta = timedelta(days=365 * num)
    else:
        return None
    return datetime.utcnow() - delta


def roman_to_int(r: str) -> int:
    roman_map = {'I': 1, 'V': 5, 'X': 10}
    total = 0
    prev = 0
    for ch in r.upper()[::-1]:
        val = roman_map.get(ch, 0)
        if val < prev:
            total -= val
        else:
            total += val
        prev = val
    return total


def extract_size(text):
    match = re.match(r"(\d+(\.\d+)?) ([A-Za-z]+)", text)
    if match:
        size = match.group(1)
        unit = match.group(3)
        return {"size": size, "sizeunit": unit}
    else:
        raise ValueError(f"Invalid size format: {text}")


def guess_title(shared_state, raw_base_title, release_type, block):
    """
    This is required as AL often does not provide proper titles in the feed or even details page.
    Reconstructed titles will rarely match the original release names, as:
    - The Video Source is not included in the feed.
    - The Audio Quality / Source is not included in the feed.
    - The Season is not included in the feed and must be reconstructed from the title, if present.
    - The Release Group is optional and may not be present.
    """

    # Detect and extract “Season X” or “Staffel X” (Arabic or Roman)
    base_title = raw_base_title
    season_str = ""
    m_season = re.search(r'(?i)\b(?:Season|Staffel)\s+(?P<num>\d+|[IVX]+)\b', base_title)
    if m_season:
        num = m_season.group('num')
        if num.isdigit():
            season_num = int(num)
        else:
            season_num = roman_to_int(num)
        season_str = f"S{season_num:02d}"
        base_title = re.sub(
            r'(?i)\b(?:Season|Staffel)\s+(?:\d+|[IVX]+)\b',
            '',
            base_title
        ).strip()
    elif release_type == "series":
        # Default to Season 1 if it's a series and no season is mentioned
        season_str = "S01"
        if "rebellion r2" in base_title.lower():
            season_str = "S02"

    # Replace spaces → dots (colons removed later by sanitize_title)
    formatted_base = base_title.replace(" ", ".")

    # Re-insert season if found
    if season_str:
        formatted_base = f"{formatted_base}.{season_str}"

    # Extract episode range, if present (accepts multi-digit ranges)
    ep_text = ""
    m_ep = re.search(r"Episode\s+(\d+(?:-\d+)?)", block.get_text())
    if m_ep:
        ep_range = m_ep.group(1)  # e.g. "001-260" or "09"
        if "-" in ep_range:
            start, end = ep_range.split("-")
            start = start.zfill(2)
            end = end.zfill(2)
            ep_text = f"E{start}-{end}"
        else:
            ep_text = f"E{ep_range.zfill(2)}"

    # Extract resolution, e.g. "720p" or "1080p"
    res_text = ""
    m_res = re.search(r":\s*([0-9]{3,4}p)", block.get_text(), re.IGNORECASE)
    if m_res:
        res_text = m_res.group(1)
    else:
        # Default to 1080p if no resolution is found
        res_text = "1080p"

    # Extract release group, if present
    span = block.find("span")
    if span:
        raw_grp_full = span.get_text()  # e.g. "Release Group: GroupName"
        if ":" in raw_grp_full:
            name = raw_grp_full.split(":", 1)[1].strip()
        else:
            name = raw_grp_full.strip()
        # Remove spaces and hyphens in the group name
        grp_text = name.replace(" ", "").replace("-", "")
    else:
        grp_text = "Quasarr"

    # Determine audio languages (icons before the closed-captioning icon)
    audio_langs = []
    audio_icon = block.find("i", class_="fa-volume-up")
    if audio_icon:
        for sib in audio_icon.find_next_siblings():
            if sib.name == "i" and "fa-closed-captioning" in sib.get("class", []):
                break
            if sib.name == "i" and "flag" in sib.get("class", []):
                code = sib["class"][1].replace("flag-", "").lower()
                if code == "jp":
                    audio_langs.append("Japanese")
                elif code == "de":
                    audio_langs.append("German")
                elif code == "en":
                    audio_langs.append("English")
                else:
                    audio_langs.append(code.title())

    # Determine subtitle languages (icons after the closed-captioning icon)
    subtitle_langs = []
    subtitle_icon = block.find("i", class_="fa-closed-captioning")
    if subtitle_icon:
        for sib in subtitle_icon.find_next_siblings():
            if sib.name == "i" and "flag" in sib.get("class", []):
                code = sib["class"][1].replace("flag-", "").lower()
                if code == "de":
                    subtitle_langs.append("German")
                elif code == "jp":
                    subtitle_langs.append("Japanese")
                elif code == "en":
                    subtitle_langs.append("English")
                else:
                    subtitle_langs.append(code.title())

    # Build language prefix per rules:
    #  - If audio has German AND Japanese → "German.DL"
    #  - Else if >2 audio languages and German is one → "German.ML"
    #  - Else if any subtitle is German → "German.Subbed"
    lang_prefix = ""
    if len(audio_langs) > 2 and "German" in audio_langs:
        lang_prefix = "German.ML"
    elif "German" in audio_langs and "Japanese" in audio_langs:
        lang_prefix = "German.DL"
    elif "German" in subtitle_langs:
        lang_prefix = "German.Subbed"

    # Assemble final title parts:
    # If both season_str and ep_text exist, merge into "SXXEYY"
    if season_str and ep_text:
        se_token = f"{season_str}{ep_text}"
        base_and_se = formatted_base.rsplit(season_str, 1)[0] + se_token
        parts = [base_and_se]
    else:
        parts = [formatted_base]
        if ep_text:
            parts.append(ep_text)

    if lang_prefix:
        parts.append(lang_prefix)
    if res_text:
        parts.append(res_text)

    parts.append("WEB-DL.x264")  # default to most common source

    # Join with dots
    candidate = ".".join(parts)

    # Append the sanitized release group (dash + group) if present
    if grp_text:
        candidate = f"{candidate}-{grp_text}"

    # Finally, sanitize the entire title: replace umlauts/ß, then strip invalid chars
    sanitized_title = shared_state.sanitize_title(candidate)
    return sanitized_title


def get_release_id(tag):
    match = re.search(r"release\s+(\d+):", tag.get_text(strip=True), re.IGNORECASE)
    if match:
        return int(match.group(1))
    return 0


def al_feed(shared_state, start_time, request_from, mirror=None):
    releases = []
    host = shared_state.values["config"]("Hostnames").get(hostname)

    headers = {
        'User-Agent': shared_state.values["user_agent"],
    }

    if "Radarr" in request_from:
        wanted_type = "movie"
    else:
        wanted_type = "series"

    if mirror and mirror not in supported_mirrors:
        debug(f'Mirror "{mirror}" not supported by {hostname}.')
        return releases

    sess = retrieve_and_validate_session(shared_state)
    if not sess:
        return releases

    try:
        r = sess.get(f'https://www.{host}/', timeout=10, headers=headers)
        r.raise_for_status()
    except Exception as e:
        info(f"{hostname}: could not fetch feed: {e}")
        invalidate_session(shared_state)
        return releases

    soup = BeautifulSoup(r.content, 'html.parser')

    # 1) New “Releases”
    release_rows = soup.select("#releases_updates_list table tbody tr")
    # 2) New “Episodes”
    episode_rows = soup.select("#episodes_updates_list table tbody tr")
    # 3) “Upgrades” Releases
    upgrade_rows = soup.select("#releases_modified_updates_list table tbody tr")

    for tr in release_rows + episode_rows + upgrade_rows:
        try:
            p_tag = tr.find("p")
            if not p_tag:
                continue
            a_tag = p_tag.find("a", href=True)
            if not a_tag:
                continue

            url = a_tag["href"].strip()
            # Prefer data-original-title, fall back to title, then to inner text
            if a_tag.get("data-original-title"):
                raw_base_title = a_tag["data-original-title"]
            elif a_tag.get("title"):
                raw_base_title = a_tag["title"]
            else:
                raw_base_title = a_tag.get_text(strip=True)

            release_type = None
            label_div = tr.find("div", class_="label-group")
            if label_div:
                for lbl in label_div.find_all("a", href=True):
                    href = lbl["href"].rstrip("/").lower()
                    if href.endswith("/anime-series"):
                        release_type = "series"
                        break
                    elif href.endswith("/anime-movies"):
                        release_type = "movie"
                        break

            if release_type is None or release_type != wanted_type:
                continue

            date_converted = ""
            small_tag = tr.find("small", class_="text-muted")
            if small_tag:
                raw_date_str = small_tag.get_text(strip=True)
                if raw_date_str.startswith("vor"):
                    dt = parse_relative_date(raw_date_str)
                    if dt:
                        date_converted = dt.strftime("%a, %d %b %Y %H:%M:%S +0000")
                else:
                    try:
                        date_converted = convert_to_rss_date(raw_date_str)
                    except Exception as e:
                        debug(f"{hostname}: could not parse date '{raw_date_str}': {e}")

            # Each of these signifies an individual release block
            mt_blocks = tr.find_all("div", class_="mt10")
            for block in mt_blocks:
                release_id = get_release_id(block)
                final_title = guess_title(shared_state, raw_base_title, release_type, block)

                # Build payload using final_title
                mb = 0  # size not available in feed
                raw = f"{final_title}|{url}|{mirror}|{mb}|{release_id}|".encode("utf-8")
                payload = urlsafe_b64encode(raw).decode("utf-8")
                link = f"{shared_state.values['internal_address']}/download/?payload={payload}"

                # Append only unique releases
                if final_title not in [r["details"]["title"] for r in releases]:
                    releases.append({
                        "details": {
                            "title": final_title,
                            "hostname": hostname,
                            "imdb_id": None,
                            "link": link,
                            "mirror": mirror,
                            "size": mb * 1024 * 1024,
                            "date": date_converted,
                            "source": url
                        },
                        "type": "protected"
                    })

        except Exception as e:
            info(f"{hostname}: error parsing feed item: {e}")

    elapsed = time.time() - start_time
    debug(f"Time taken: {elapsed:.2f}s ({hostname})")
    return releases


def _build_guess_block_from_tab(soup, tab):
    """
    Given a BeautifulSoup 'tab' for one <div id="download_X">…</div>, construct
    and return a <div> containing exactly:
      1) “: <resolution>” text so guess_title’s resolution regex can match
      2) <i class="fa-volume-up">, then all audio <i class="flag"> icons
      3) <i class="fa-closed-captioning">, then all subtitle <i class="flag"> icons
      4) <span>Release Group: <grp_name></span> so guess_title picks up the group

    This helper does not call guess_title itself; it merely builds the block.
    """
    fake_block = soup.new_tag("div")

    # Resolution
    res_td = tab.select_one("tr:has(th>i.fa-desktop) td")
    if res_td:
        res_val = res_td.get_text(strip=True)
        resolution = "480p"  # Default fallback

        match = re.search(r'(\d+)\s*x\s*(\d+)', res_val)
        if match:
            width, height = match.groups()
            height = height.lstrip('0')  # Remove leading zeros
            if height.isdigit():
                height_int = int(height)
                if 2000 <= height_int < 3000:
                    resolution = "2160p"
                elif 1000 <= height_int < 2000:
                    resolution = "1080p"
                elif 690 <= height_int < 800:
                    resolution = "720p"
        else:
            resolution = "1080p"  # Fallback if no resolution found

        fake_block.append(soup.new_string(f": {resolution}"))

    # Audio languages
    fake_block.append(soup.new_tag("i", **{"class": "fa-volume-up"}))
    lang_tr = tab.select_one("tr:has(th>i.fa-volume-up)")
    if lang_tr:
        for icon in lang_tr.select("i.flag"):
            fake_block.append(icon)

    # Subtitles
    fake_block.append(soup.new_tag("i", **{"class": "fa-closed-captioning"}))
    sub_tr = tab.select_one("tr:has(th>i.fa-closed-captioning)")
    if sub_tr:
        for icon in sub_tr.select("i.flag"):
            fake_block.append(icon)

    # Release Group
    grp_td = tab.select_one("tr:has(th>i.fa-child) td")
    if grp_td:
        grp_name = grp_td.get_text(strip=True).replace(" ", "").replace("-", "")
        span = soup.new_tag("span")
        span.string = f"Release Group: {grp_name}"
        fake_block.append(span)

    return fake_block


def extract_season(title: str) -> int | None:
    match = re.search(r'(?i)(?:^|[^a-zA-Z0-9])S(\d{1,4})(?!\d)', title)
    if match:
        return int(match.group(1))
    return None


def al_search(shared_state, start_time, request_from, search_string,
              mirror=None, season=None, episode=None):
    releases = []
    host = shared_state.values["config"]("Hostnames").get(hostname)

    headers = {
        'User-Agent': shared_state.values["user_agent"],
    }

    if "Radarr" in request_from:
        valid_type = "movie"
    else:
        valid_type = "series"

    if mirror and mirror not in supported_mirrors:
        debug(f'Mirror "{mirror}" not supported by {hostname}.')
        return releases

    imdb_id = shared_state.is_imdb_id(search_string)
    if imdb_id:
        title = get_localized_title(shared_state, imdb_id, 'de')
        if not title:
            info(f"{hostname}: no title for IMDb {imdb_id}")
            return releases
        search_string = title

    search_string = unescape(search_string)

    sess = retrieve_and_validate_session(shared_state)
    if not sess:
        return releases

    encoded_search_string = quote_plus(search_string)

    try:
        url = f'https://www.{host}/search?q={encoded_search_string}'
        r = sess.get(url, timeout=10, headers=headers)
        r.raise_for_status()
    except Exception as e:
        info(f"{hostname}: search load error: {e}")
        invalidate_session(shared_state)
        return releases

    if r.history:
        # If just one valid search result exists, AL skips the search result page
        last_redirect = r.history[-1]
        redirect_location = last_redirect.headers['Location']
        absolute_redirect_url = urljoin(last_redirect.url, redirect_location)  # in case of relative URL
        debug(f"{search_string} redirected to {absolute_redirect_url} instead of search results page")

        try:
            soup = BeautifulSoup(r.text, "html.parser")
            page_title = soup.title.string
        except:
            page_title = ""

        results = [{"url": absolute_redirect_url, "title": page_title}]
    else:
        soup = BeautifulSoup(r.text, 'html.parser')
        results = []

        for panel in soup.select('div.panel.panel-default'):
            body = panel.find('div', class_='panel-body')
            if not body:
                continue

            title_tag = body.select_one('h4.title-list a[href]')
            if not title_tag:
                continue
            url = title_tag['href'].strip()
            name = title_tag.get_text(strip=True)

            sanitized_search_string = shared_state.sanitize_string(search_string)
            sanitized_title = shared_state.sanitize_string(name)
            if not re.search(rf'\b{re.escape(sanitized_search_string)}\b', sanitized_title):
                debug(f"Search string '{search_string}' does not match '{name}'")
                continue
            debug(f"Matched search string '{search_string}' with result '{name}'")

            type_label = None
            for lbl in body.select('div.label-group a[href]'):
                href = lbl['href']
                if '/anime-series' in href:
                    type_label = 'series'
                    break
                if '/anime-movies' in href:
                    type_label = 'movie'
                    break

            if not type_label or type_label != valid_type:
                continue

            results.append({"url": url, "title": name})

    for result in results:
        try:
            url = result["url"]
            title = result.get("title") or ""

            context = "recents_al"
            threshold = 60
            recently_searched = shared_state.get_recently_searched(shared_state, context, threshold)
            entry = recently_searched.get(url, {})
            ts = entry.get("timestamp")
            use_cache = ts and ts > datetime.now() - timedelta(seconds=threshold)

            if use_cache and entry.get("html"):
                debug(f"Using cached content for '{url}'")
                data_html = entry["html"]
            else:
                entry = {"timestamp": datetime.now()}
                data_html = sess.get(url, headers=headers, timeout=10).text

            entry["html"] = data_html
            recently_searched[url] = entry
            shared_state.update(context, recently_searched)

            content = BeautifulSoup(data_html, "html.parser")

            # Find each download‐table and process it
            release_id = 0
            download_tabs = content.select("div[id^=download_]")
            for tab in download_tabs:
                release_id += 1

                # Attempt to get a release title from Release Notes
                release_notes_td = None
                for tr in tab.select("tr"):
                    th = tr.select_one("th")
                    if not th:
                        continue
                    icon = th.find("i", class_="fa-info")
                    if icon:
                        td = tr.select_one("td")
                        if td and td.get_text(strip=True):
                            release_notes_td = td
                        break

                final_title = None
                if release_notes_td:
                    raw_rn = release_notes_td.get_text(strip=True)
                    rn_with_dots = raw_rn.replace(" ", ".")
                    if "." in rn_with_dots and "-" in rn_with_dots:
                        # Check if string ends with Group tag (word after dash) - this should prevent false positives
                        if re.search(r"-[\s.]?\w+$", rn_with_dots):
                            final_title = shared_state.sanitize_title(rn_with_dots)

                # If no valid title from Release Notes, guess
                if not final_title:
                    fake_block = _build_guess_block_from_tab(content, tab)
                    final_title = guess_title(shared_state, title, valid_type, fake_block)

                # Parse date
                date_td = tab.select_one("tr:has(th>i.fa-calendar-alt) td.modified")
                if date_td:
                    raw_date = date_td.get_text(strip=True)
                    try:
                        dt = datetime.strptime(raw_date, "%d.%m.%Y %H:%M")
                        date_str = dt.strftime("%a, %d %b %Y %H:%M:%S +0000")
                    except Exception:
                        date_str = ""
                else:
                    date_str = (datetime.utcnow() - timedelta(hours=1)) \
                        .strftime("%a, %d %b %Y %H:%M:%S +0000")

                # Parse filesize from the <tr> with <i class="fa-hdd">
                size_td = tab.select_one("tr:has(th>i.fa-hdd) td")
                mb = 0
                if size_td:
                    size_text = size_td.get_text(strip=True)
                    candidates = re.findall(r'(\d+(\.\d+)?\s*[A-Za-z]+)', size_text)
                    if candidates:
                        size_string = candidates[-1][0]
                        try:
                            size_item = extract_size(size_string)
                            mb = shared_state.convert_to_mb(size_item)
                        except Exception as e:
                            debug(f"Error extracting size for {title}: {e}")

                if season:
                    try:
                        season_in_title = extract_season(final_title)
                        if season_in_title and season_in_title != int(season):
                            debug(f"Excluding {final_title} due to season mismatch: {season_in_title} != {season}")
                            continue
                    except Exception as e:
                        debug(f"Error extracting season from title '{final_title}': {e}")

                if episode:
                    try:
                        episodes_div = tab.find("div", class_="episodes")
                        if episodes_div:
                            episode_links = episodes_div.find_all("a", attrs={"data-loop": re.compile(r"^\d+$")})
                            total_episodes = len(episode_links)
                            if total_episodes > 0:
                                if mb > 0:
                                    mb = int(mb / total_episodes)

                                episode = int(episode)
                                if 1 <= episode <= total_episodes:
                                    match = re.search(r'(S\d{2,4})', final_title)
                                    if match:
                                        season = match.group(1)
                                        final_title = final_title.replace(season, f"{season}E{episode:02d}")
                    except ValueError:
                        pass

                payload = urlsafe_b64encode(
                    f"{final_title}|{url}|{mirror}|{mb}|{release_id}|{imdb_id or ''}"
                    .encode("utf-8")
                ).decode("utf-8")
                link = f"{shared_state.values['internal_address']}/download/?payload={payload}"

                releases.append({
                    "details": {
                        "title": final_title,
                        "hostname": hostname,
                        "imdb_id": imdb_id,
                        "link": link,
                        "mirror": mirror,
                        "size": mb * 1024 * 1024,
                        "date": date_str,
                        "source": url
                    },
                    "type": "protected"
                })

        except Exception as e:
            info(f"{hostname}: error parsing search item: {e}")

    elapsed = time.time() - start_time
    debug(f"Time taken: {elapsed:.2f}s ({hostname})")
    return releases
