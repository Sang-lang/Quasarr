# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

import base64
import json
import pickle
import re
import time
import urllib.parse

import requests
from bs4 import BeautifulSoup

from quasarr.downloads.linkcrypters.al import decrypt_content, solve_captcha
from quasarr.providers.log import info, debug

hostname = "al"


def create_and_persist_session(shared_state):
    cfg = shared_state.values["config"]("Hostnames")
    host = cfg.get(hostname)
    credentials_cfg = shared_state.values["config"](hostname.upper())
    user = credentials_cfg.get("user")
    pw = credentials_cfg.get("password")

    flaresolverr_url = shared_state.values["config"]('FlareSolverr').get('url')

    sess = requests.Session()

    # Prime cookies via FlareSolverr
    try:
        info(f'Priming "{hostname}" session via FlareSolverr...')
        fs_headers = {"Content-Type": "application/json"}
        fs_payload = {
            "cmd": "request.get",
            "url": f"https://www.{host}/",
            "maxTimeout": 60000
        }

        fs_resp = requests.post(flaresolverr_url, headers=fs_headers, json=fs_payload, timeout=30)
        fs_resp.raise_for_status()

        fs_json = fs_resp.json()
        # Check if FlareSolverr actually solved the challenge
        if fs_json.get("status") != "ok" or "solution" not in fs_json:
            info(f"{hostname}: FlareSolverr did not return a valid solution")
            return None

        solution = fs_json["solution"]
        # store FlareSolverr’s UA into our requests.Session
        fl_ua = solution.get("userAgent")
        if fl_ua:
            sess.headers.update({'User-Agent': fl_ua})

        # Extract any cookies returned by FlareSolverr and add them into our session
        for ck in solution.get("cookies", []):
            name = ck.get("name")
            value = ck.get("value")
            domain = ck.get("domain")
            path = ck.get("path", "/")
            # Set cookie on the session (ignoring expires/secure/httpOnly)
            sess.cookies.set(name, value, domain=domain, path=path)

    except Exception as e:
        debug(f'Could not prime "{hostname}" session via FlareSolverr: {e}')
        return None

    if user and pw:
        data = {
            "identity": user,
            "password": pw,
            "remember": "1"
        }
        encoded_data = urllib.parse.urlencode(data)

        login_headers = {
            "Content-Type": "application/x-www-form-urlencoded"
        }

        r = sess.post(f'https://www.{host}/auth/signin',
                      data=encoded_data,
                      headers=login_headers,
                      timeout=30)

        if r.status_code != 200 or "invalid" in r.text.lower():
            info(f'Login failed: "{hostname}" - {r.status_code} - {r.text}')
            return None
        info(f'Login successful: "{hostname}"')
    else:
        info(f'Missing credentials for: "{hostname}" - skipping login')
        return None

    blob = pickle.dumps(sess)
    token = base64.b64encode(blob).decode("utf-8")
    shared_state.values["database"]("sessions").update_store(hostname, token)
    return sess


def retrieve_and_validate_session(shared_state):
    db = shared_state.values["database"]("sessions")
    token = db.retrieve(hostname)
    if not token:
        return create_and_persist_session(shared_state)

    try:
        blob = base64.b64decode(token.encode("utf-8"))
        sess = pickle.loads(blob)
        if not isinstance(sess, requests.Session):
            raise ValueError("Not a Session")
    except Exception as e:
        debug(f"{hostname}: session load failed: {e}")
        return create_and_persist_session(shared_state)

    return sess


def invalidate_session(shared_state):
    db = shared_state.values["database"]("sessions")
    db.delete(hostname)
    debug(f'Session for "{hostname}" marked as invalid!')


def _persist_session_to_db(shared_state, sess):
    """
    Serialize & store the given requests.Session into the database under `hostname`.
    """
    blob = pickle.dumps(sess)
    token = base64.b64encode(blob).decode("utf-8")
    shared_state.values["database"]("sessions").update_store(hostname, token)


def _load_session_cookies_for_flaresolverr(sess):
    """
    Convert a requests.Session's cookies into FlareSolverr‐style list of dicts.
    """
    cookie_list = []
    for ck in sess.cookies:
        cookie_list.append({
            "name": ck.name,
            "value": ck.value,
            "domain": ck.domain,
            "path": ck.path or "/",
        })
    return cookie_list


def unwrap_flaresolverr_body(raw_text: str) -> str:
    """
    Use BeautifulSoup to remove any HTML tags and return the raw text.
    If raw_text is:
        <html><body>{"foo":123}</body></html>
    or:
        <html><body><pre>[...array...]</pre></body></html>
    or even just:
        {"foo":123}
    this will return the inner JSON string in all cases.
    """
    soup = BeautifulSoup(raw_text, "html.parser")
    text = soup.get_text().strip()
    return text


def fetch_via_flaresolverr(shared_state,
                           method: str,
                           target_url: str,
                           post_data: dict = None,
                           timeout: int = 60):
    """
    Load (or recreate) the requests.Session from DB.
    Package its cookies into FlareSolverr payload.
    Ask FlareSolverr to do a request.get or request.post on target_url.
    Replace the Session’s cookies with FlareSolverr’s new cookies.
    Re-persist the updated session to the DB.
    Return a dict with “status_code”, “headers”, “json” (parsed - if available), “text” and “cookies”.

    – method: "GET" or "POST"
    – post_data: dict of form‐fields if method=="POST"
    – timeout: seconds (FlareSolverr’s internal maxTimeout = timeout*1000 ms)
    """
    flaresolverr_url = shared_state.values["config"]('FlareSolverr').get('url')

    sess = retrieve_and_validate_session(shared_state)

    cmd = "request.get" if method.upper() == "GET" else "request.post"
    fs_payload = {
        "cmd": cmd,
        "url": target_url,
        "maxTimeout": timeout * 1000,
        # Inject every cookie from our Python session into FlareSolverr
        "cookies": _load_session_cookies_for_flaresolverr(sess)
    }

    if method.upper() == "POST":
        # FlareSolverr expects postData as urlencoded string
        encoded = urllib.parse.urlencode(post_data or {})
        fs_payload["postData"] = encoded

    # Send the JSON request to FlareSolverr
    fs_headers = {"Content-Type": "application/json"}
    try:
        resp = requests.post(
            flaresolverr_url,
            headers=fs_headers,
            json=fs_payload,
            timeout=timeout + 10
        )
        resp.raise_for_status()
    except Exception as e:
        raise RuntimeError(f"Could not reach FlareSolverr: {e}")

    fs_json = resp.json()
    if fs_json.get("status") != "ok" or "solution" not in fs_json:
        raise RuntimeError(f"FlareSolverr did not return a valid solution: {fs_json.get('message', '<no message>')}")

    solution = fs_json["solution"]

    # Extract the raw HTML/JSON body that FlareSolverr fetched
    raw_body = solution.get("response", "")
    # Get raw body as text, since it might contain JSON
    unwrapped = unwrap_flaresolverr_body(raw_body)

    # Attempt to parse it as JSON
    try:
        parsed_json = json.loads(unwrapped)
    except ValueError:
        parsed_json = None

    # Replace our requests.Session cookies with whatever FlareSolverr solved
    sess.cookies.clear()
    for ck in solution.get("cookies", []):
        sess.cookies.set(
            ck.get("name"),
            ck.get("value"),
            domain=ck.get("domain"),
            path=ck.get("path", "/")
        )

    # Persist the updated Session back into your DB
    _persist_session_to_db(shared_state, sess)

    # Return a small dict containing status, headers, parsed JSON, and cookie list
    return {
        "status_code": solution.get("status"),
        "headers": solution.get("headers", {}),
        "json": parsed_json,
        "text": raw_body,
        "cookies": solution.get("cookies", [])
    }


def fetch_via_requests_session(shared_state, method: str, target_url: str, post_data: dict = None, timeout: int = 30):
    """
    – method: "GET" or "POST"
    – post_data: for POST only (will be sent as form-data unless you explicitly JSON-encode)
    – timeout: seconds
    """
    sess = retrieve_and_validate_session(shared_state)

    # Execute request
    if method.upper() == "GET":
        resp = sess.get(target_url, timeout=timeout)
    else:  # POST
        resp = sess.post(target_url, data=post_data, timeout=timeout)

    # Re-persist cookies, since the site might have modified them during the request
    _persist_session_to_db(shared_state, sess)

    return resp


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
    base_title = raw_base_title.replace("Anime Serie", "")
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

    # If both season_str and ep_text exist, merge into "SXXEYY"
    if season_str and ep_text:
        se_token = f"{season_str}{ep_text}"
        base_and_se = formatted_base.rsplit(season_str, 1)[0] + se_token
        parts = [base_and_se]
    else:
        parts = [formatted_base]
        if ep_text:
            parts.append(ep_text)

    # Check notes for hints of video source
    notes = block.find("b")
    if notes:
        notes_text = notes.get_text(strip=True).lower()
    else:
        notes_text = ""

    audio = ""
    if re.search(r'\bflac\b', notes_text, re.IGNORECASE):
        audio = "FLAC"
    elif re.search(r'\baac\b', notes_text, re.IGNORECASE):
        audio = "AAC"
    elif re.search(r'\bopus\b', notes_text, re.IGNORECASE):
        audio = "Opus"
    elif re.search(r'\bmp3\b', notes_text, re.IGNORECASE):
        audio = "MP3"
    elif re.search(r'\bpcm\b', notes_text, re.IGNORECASE):
        audio = "PCM"
    elif re.search(r'\bdts\b', notes_text, re.IGNORECASE):
        audio = "DTS"
    elif re.search(r'\b(ac3|eac3)\b', notes_text, re.IGNORECASE):
        audio = "AC3"

    # Build language prefix per rules:
    #  - Normalize audio type string to insert it into the title
    #  - If audio has German AND Japanese → "German.DL"
    #  - Else if >2 audio languages and German is one → "German.ML"
    #  - Else if only German audio → "German"
    #  - Else if any subtitle is German → "<language>.Subbed"
    audio = audio.strip()
    audio_type = f".{audio}" if audio else ""

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

    lang_prefix = ""
    if len(audio_langs) > 2 and "German" in audio_langs:
        # e.g. German.5.1.ML or German.ML
        lang_prefix = f"German{audio_type}.ML"
    elif "German" in audio_langs and "Japanese" in audio_langs:
        # e.g. German.2.0.DL or German.DL
        lang_prefix = f"German{audio_type}.DL"
    elif "German" in audio_langs and len(audio_langs) == 1:
        # e.g. German.2.0 or German
        lang_prefix = f"German{audio_type}"
    elif audio_langs and "German" in subtitle_langs:
        lang_prefix = f"{audio_langs[0]}.Subbed"

    if lang_prefix:
        parts.append(lang_prefix)

    # Extract resolution, e.g. "720p" or "1080p"
    m_res = re.search(r":\s*([0-9]{3,4}p)", block.get_text(), re.IGNORECASE)
    if m_res:
        res_text = m_res.group(1)
    else:
        # Default to 1080p if no resolution is found
        res_text = "1080p"

    if res_text:
        parts.append(res_text)

    source = "WEB-DL"  # default to most common source
    if re.search(r'\b(web-dl|webdl|webrip)\b', notes_text, re.IGNORECASE):
        source = "WEB-DL"
    elif re.search(r'\b(blu-ray|bd|bluray)\b', notes_text, re.IGNORECASE):
        source = "BluRay"
    elif re.search(r'\b(hdtv|tvrip)\b', notes_text, re.IGNORECASE):
        source = "HDTV"
    parts.append(source)

    video = "x264"
    if re.search(r'\b(265|hevc)\b', notes_text, re.IGNORECASE):
        video = "x265"
    elif re.search(r'\bav1\b', notes_text, re.IGNORECASE):
        video = "AV1"
    elif re.search(r'\bavc\b', notes_text, re.IGNORECASE):
        video = "AVC"
    elif re.search(r'\bxvid\b', notes_text, re.IGNORECASE):
        video = "Xvid"
    parts.append(video)

    # Join with dots
    candidate = ".".join(parts)

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
        grp_text = ""
    # Append the sanitized release group (dash + group) if present
    if grp_text:
        candidate = f"{candidate}-{grp_text}"

    # Finally, sanitize the entire title: replace umlauts/ß, then strip invalid chars
    sanitized_title = shared_state.sanitize_title(candidate)
    return sanitized_title


def build_guess_block_from_tab(tab, episode=None):
    """
    Given a BeautifulSoup 'tab' for one <div id="download_X">…</div>, construct
    and return a <div> containing exactly:
      1) “: <resolution>” text so guess_title’s resolution regex can match
      2) <i class="fa-volume-up">, then all audio <i class="flag"> icons
      3) <i class="fa-closed-captioning">, then all subtitle <i class="flag"> icons
      4) <span>Release Group: <grp_name></span> so guess_title picks up the group

    This helper does not call guess_title itself; it merely builds the block.
    """
    # generate new soup from scratch
    soup = BeautifulSoup("", "html.parser")

    fake_block = soup.new_tag("div")

    # Resolution
    res_td = tab.select_one("tr:has(th>i.fa-desktop) td")
    if res_td:
        res_val = res_td.get_text(strip=True)
        resolution = "1080p"  # Default fallback

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

    notes_td = tab.select_one("tr:has(th>i.fa-info) td")
    if notes_td:
        notes_text = notes_td.get_text(strip=True)
        bold = soup.new_tag("b")
        if notes_text:
            bold.string = notes_text
            fake_block.append(bold)

    if episode:
        fake_block.append(
            soup.new_string(f": Episode {episode}")
        )

    return fake_block


def check_release(shared_state, details_html, release_id, title, episode_in_title):
    soup = BeautifulSoup(details_html, "html.parser")

    if int(release_id) == 0:
        info("Feed download detected, hard-coding release_id to 1 to achieve successful download")
        release_id = 1
        # The following logic works, but the highest release ID sometimes does not have the desired episode
        #
        # If download was started from the feed, the highest download id is typically the best option
        # panes = soup.find_all("div", class_="tab-pane")
        # max_id = None
        # for pane in panes:
        #     pane_id = pane.get("id", "")
        #     match = re.match(r"download_(\d+)$", pane_id)
        #     if match:
        #         num = int(match.group(1))
        #         if max_id is None or num > max_id:
        #             max_id = num
        # if max_id:
        #     release_id = max_id

    tab = soup.find("div", class_="tab-pane", id=f"download_{release_id}")
    if tab:
        try:
            # Attempt to get real release title from Release Notes (if it exists)
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

            if release_notes_td:
                raw_rn = release_notes_td.get_text(strip=True)
                rn_with_dots = raw_rn.replace(" ", ".")
                if "." in rn_with_dots and "-" in rn_with_dots:
                    # Check if string ends with Group tag (word after dash) - this should prevent false positives
                    if re.search(r"-[\s.]?\w+$", rn_with_dots):
                        real_title = shared_state.sanitize_title(rn_with_dots)
                        if real_title and real_title.lower() != title.lower():
                            info(f'Identified true release title "{real_title}" on details page')
                            return real_title, release_id
        except Exception as e:
            info(f"Error grabbing real title from release: {e}")

        try:
            # We re-guess the title from the details page
            # This ensures, that downloads initiated by the feed (which has limited/incomplete data) yield
            # the best possible title for the download (including resolution, audio, video, etc.)
            page_title_info = soup.find("title").text.strip().rpartition(" (")
            page_title = page_title_info[0].strip()
            release_type_info = page_title_info[2].strip()
            if "serie" in release_type_info.lower():
                release_type = "series"
            else:
                release_type = "movie"
            guess_tab = build_guess_block_from_tab(tab, episode=episode_in_title)

            guessed_title = guess_title(shared_state, page_title, release_type, guess_tab)
            if guessed_title and guessed_title.lower() != title.lower():
                info(f'Adjusted guessed release title to "{guessed_title}" from details page')
                return guessed_title, release_id
        except Exception as e:
            info(f"Error guessing release title from release: {e}")

    return title, release_id


def extract_episode(title: str) -> int | None:
    match = re.search(r'\bS\d{2,4}E(\d+)\b(?![\-E\d])', title)
    if match:
        return int(match.group(1))

    if not re.search(r'\bS\d{2,4}\b', title):
        match = re.search(r'\.E(\d+)\b(?![\-E\d])', title)
        if match:
            return int(match.group(1))

    return None


def get_al_download_links(shared_state, url, mirror, title, release_id):
    al = shared_state.values["config"]("Hostnames").get(hostname)

    sess = retrieve_and_validate_session(shared_state)
    if not sess:
        info(f"Could not retrieve valid session for {al}")
        return {}

    details_page = fetch_via_flaresolverr(shared_state, "GET", url, timeout=30)
    details_html = details_page.get("text", "")
    if not details_html:
        info(f"Failed to load details page for {title} at {url}")
        return {}

    episode_in_title = extract_episode(title)
    if episode_in_title:
        selection = episode_in_title - 1  # Convert to zero-based index
    else:
        selection = "cnl"

    title, release_id = check_release(shared_state, details_html, release_id, title, episode_in_title)
    if int(release_id) == 0:
        info(f"No valid release ID found for {title} - Download failed!")
        return {}

    anime_identifier = url.rstrip("/").split("/")[-1]

    info(f'Selected "Release {release_id}" from {url}')

    links = []
    try:
        raw_request = json.dumps(
            ["media", anime_identifier, "downloads", release_id, selection]
        )
        b64 = base64.b64encode(raw_request.encode("ascii")).decode("ascii")

        post_url = f"https://www.{al}/ajax/captcha"
        payload = {"enc": b64, "response": "nocaptcha"}

        result = fetch_via_flaresolverr(
            shared_state,
            method="POST",
            target_url=post_url,
            post_data=payload,
            timeout=30
        )

        status = result.get("status_code")
        if not status == 200:
            info(f"FlareSolverr returned HTTP {status} for captcha request")
            return {}
        else:
            text = result.get("text", "")
            try:
                response_json = result["json"]
            except ValueError:
                info(f"Unexpected response when initiating captcha: {text}")
                return {}

            code = response_json.get("code", "")
            message = response_json.get("message", "")
            content_items = response_json.get("content", [])

            tries = 0
            if code == "success" and content_items:
                info('CAPTCHA not required')
            elif message == "cnl_login":
                info('Login expired, re-creating session...')
                invalidate_session(shared_state)
            else:
                tries = 0
                while tries < 3:
                    try:
                        tries += 1
                        info(
                            f"Starting attempt {tries} to solve CAPTCHA for "
                            f"{f'episode {episode_in_title}' if selection and selection != 'cnl' else 'all links'}"
                        )
                        attempt = solve_captcha(hostname, shared_state, fetch_via_flaresolverr,
                                                fetch_via_requests_session)

                        solved = (unwrap_flaresolverr_body(attempt.get("response")) == "1")
                        captcha_id = attempt.get("captcha_id", None)

                        if solved and captcha_id:
                            payload = {
                                "enc": b64,
                                "response": "captcha",
                                "captcha-idhf": 0,
                                "captcha-hf": captcha_id
                            }
                            check_solution = fetch_via_flaresolverr(shared_state,
                                                                    method="POST",
                                                                    target_url=post_url,
                                                                    post_data=payload,
                                                                    timeout=30)
                            try:
                                response_json = check_solution.get("json", {})
                            except ValueError:
                                raise RuntimeError(
                                    f"Unexpected /ajax/captcha response: {check_solution.get('text', '')}")

                            code = response_json.get("code", "")
                            message = response_json.get("message", "")
                            content_items = response_json.get("content", [])

                            if code == "success":
                                if content_items:
                                    info("CAPTCHA solved successfully on attempt {}.".format(tries))
                                    break
                                else:
                                    info(f"CAPTCHA was solved, but no links are available for the selection!")
                                    return {}
                            elif message == "cnl_login":
                                info('Login expired, re-creating session...')
                                invalidate_session(shared_state)
                            else:
                                info(
                                    f"CAPTCHA POST returned code={code}, message={message}. Retrying... (attempt {tries})")

                                if "slowndown" in str(message).lower():
                                    wait_period = 30
                                    info(
                                        f"CAPTCHAs solved too quickly. Waiting {wait_period} seconds before next attempt...")
                                    time.sleep(wait_period)
                        else:
                            info(f"CAPTCHA solver returned invalid solution, retrying... (attempt {tries})")

                    except RuntimeError as e:
                        info(f"Error solving CAPTCHA: {e}")
                    else:
                        info(f"CAPTCHA solver returned invalid solution, retrying... (attempt {tries})")

            if code != "success":
                info(
                    f"CAPTCHA solution failed after {tries} attempts. Your IP is likely banned - "
                    f"Code: {code}, Message: {message}"
                )
                invalidate_session(shared_state)
                return {}

            try:
                links = decrypt_content(content_items, mirror)
                debug(f"Decrypted URLs: {links}")
            except Exception as e:
                info(f"Error during decryption: {e}")
    except Exception as e:
        info(f"Error loading AL download: {e}")
        invalidate_session(shared_state)

    return {
        "links": links,
        "password": f"www.{al}",
        "title": title
    }
