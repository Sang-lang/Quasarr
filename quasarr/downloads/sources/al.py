# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

import base64
import json
import pickle
import time
import urllib.parse
from typing import Optional, List

import requests

from quasarr.downloads.linkcrypters.al import decrypt_content, solve_captcha
from quasarr.providers.log import info, debug

hostname = "al"

import re
from bs4 import BeautifulSoup
from dataclasses import dataclass


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


@dataclass
class ReleaseInfo:
    release_title: Optional[str]  # the real release title, if available
    audio_langs: List[str]
    subtitle_langs: List[str]
    resolution: str
    source: str
    video: str
    release_group: str
    season_part: Optional[int]  # must be appended to title if not None
    season: Optional[int]  # season number (None if not detected)
    episode_min: Optional[int]  # first episode number (None if not detected)
    episode_max: Optional[int]  # last episode number (same as episode_min if single)


def parse_info_from_feed_entry(block, raw_base_title, release_type) -> ReleaseInfo:
    """
    Parse a BeautifulSoup block from the feed entry into ReleaseInfo.
    """
    text = block.get_text(separator=" ", strip=True)

    # detect season
    season_num: Optional[int] = None
    m_season = re.search(r'(?i)\b(?:Season|Staffel)\s+(\d+|[IVX]+)\b', raw_base_title)
    if m_season:
        num = m_season.group(1)
        season_num = int(num) if num.isdigit() else roman_to_int(num)
    if not season_num and release_type == "series":
        # if no season number was detected, but the release type is series, assume season 1
        season_num = 1

    # detect episodes
    episode_min: Optional[int] = None
    episode_max: Optional[int] = None
    m_ep = re.search(r"Episode\s+(\d+)(?:-(\d+))?", text)
    if m_ep:
        episode_min = int(m_ep.group(1))
        episode_max = int(m_ep.group(2)) if m_ep.group(2) else episode_min

    # parse audio flags
    audio_langs: List[str] = []
    audio_icon = block.find("i", class_="fa-volume-up")
    if audio_icon:
        for sib in audio_icon.find_next_siblings():
            if sib.name == "i" and "fa-closed-captioning" in sib.get("class", []): break
            if sib.name == "i" and "flag" in sib.get("class", []):
                code = sib["class"][1].replace("flag-", "").lower()
                audio_langs.append({'jp': 'Japanese', 'de': 'German', 'en': 'English'}.get(code, code.title()))

    # parse subtitle flags
    subtitle_langs: List[str] = []
    subtitle_icon = block.find("i", class_="fa-closed-captioning")
    if subtitle_icon:
        for sib in subtitle_icon.find_next_siblings():
            if sib.name == "i" and "flag" in sib.get("class", []):
                code = sib["class"][1].replace("flag-", "").lower()
                subtitle_langs.append({'jp': 'Japanese', 'de': 'German', 'en': 'English'}.get(code, code.title()))

    # resolution
    m_res = re.search(r":\s*([0-9]{3,4}p)", text, re.IGNORECASE)
    resolution = m_res.group(1) if m_res else "1080p"

    # source not available in feed
    source = "WEB-DL"
    # video codec not available in feed
    video = "x264"

    # release group
    span = block.find("span")
    if span:
        grp = span.get_text().split(":", 1)[-1].strip()
        release_group = grp.replace(" ", "").replace("-", "")
    else:
        release_group = ""

    return ReleaseInfo(
        release_title=None,
        audio_langs=audio_langs,
        subtitle_langs=subtitle_langs,
        resolution=resolution,
        source=source,
        video=video,
        release_group=release_group,
        season_part=None,
        season=season_num,
        episode_min=episode_min,
        episode_max=episode_max
    )


def parse_info_from_download_item(tab, page_title=None, release_type=None, requested_episode=None) -> ReleaseInfo:
    """
    Parse a BeautifulSoup 'tab' from a download item into ReleaseInfo.
    """
    # notes
    notes_td = tab.select_one("tr:has(th>i.fa-info) td")
    notes_text = notes_td.get_text(strip=True) if notes_td else ""
    notes_lower = notes_text.lower()

    release_title = None
    if notes_text:
        rn_with_dots = notes_text.replace(" ", ".").replace(".-.", "-")
        rn_no_dot_duplicates = re.sub(r'\.{2,}', '.', rn_with_dots)
        if "." in rn_with_dots and "-" in rn_with_dots:
            # Check if string ends with Group tag (word after dash) - this should prevent false positives
            if re.search(r"-[\s.]?\w+$", rn_with_dots):
                release_title = rn_no_dot_duplicates

    # resolution
    res_td = tab.select_one("tr:has(th>i.fa-desktop) td")
    resolution = "1080p"
    if res_td:
        match = re.search(r"(\d+)\s*x\s*(\d+)", res_td.get_text(strip=True))
        if match:
            h = int(match.group(2))
            resolution = '2160p' if h >= 2000 else '1080p' if h >= 1000 else '720p'

    # audio and subtitles
    audio_codes = [icon["class"][1].replace("flag-", "") for icon in
                   tab.select("tr:has(th>i.fa-volume-up) i.flag")]
    audio_langs = [{'jp': 'Japanese', 'de': 'German', 'en': 'English'}.get(c, c.title())
                   for c in audio_codes]
    sub_codes = [icon["class"][1].replace("flag-", "") for icon in
                 tab.select("tr:has(th>i.fa-closed-captioning) i.flag")]
    subtitle_langs = [{'jp': 'Japanese', 'de': 'German', 'en': 'English'}.get(c, c.title())
                      for c in sub_codes]

    # source
    if re.search(r"(web-dl|webdl|webrip)", notes_lower):
        source = "WEB-DL"
    elif re.search(r"(blu-ray|\bbd\b|bluray)", notes_lower):
        source = "BluRay"
    elif re.search(r"(hdtv|tvrip)", notes_lower):
        source = "HDTV"
    else:
        source = "WEB-DL"

    # video codec
    if re.search(r"(265|hevc)", notes_lower):
        video = "x265"
    elif re.search(r"av1", notes_lower):
        video = "AV1"
    elif re.search(r"avc", notes_lower):
        video = "AVC"
    elif re.search(r"xvid", notes_lower):
        video = "Xvid"
    else:
        video = "x264"

    # release group
    grp_td = tab.select_one("tr:has(th>i.fa-child) td")
    if grp_td:
        grp = grp_td.get_text(strip=True)
        release_group = grp.replace(" ", "").replace("-", "")
    else:
        release_group = ""

    # determine season or fallback
    season_num: Optional[int] = None
    if release_title:
        match = re.search(r'\.(?:S(\d{1,4})|R([1-9]))(?:E\d{1,4})?', release_title, re.IGNORECASE)
        if match:
            if match.group(1) is not None:
                season_num = int(match.group(1))
            elif match.group(2) is not None:
                season_num = int(match.group(2))
    if season_num is None:
        if not page_title:
            page_title = ""
        if "staffel" in page_title.lower() or "season" in page_title.lower() or release_type == "series":
            match = re.search(r'\b(?:Season|Staffel)\s+(\d+|[IVX]+)\b|\bR(2)\b', page_title, re.IGNORECASE)
            if match:
                if match.group(1) is not None:
                    num = match.group(1)
                    season_num = int(num) if num.isdigit() else roman_to_int(num)
                elif match.group(2) is not None:
                    season_num = int(match.group(2))
            else:
                season_num = 1  # fallback default if keywords are present but no number found

    # check if part in title
    part: Optional[int] = None
    if page_title:
        match = re.search(r'(?i)\b(?:Part|Teil)\s+(\d+|[IVX]+)\b', page_title, re.IGNORECASE)
        if match:
            num = match.group(1)
            part = int(num) if num.isdigit() else roman_to_int(num)
            part_string = f"Part.{part}"
            if release_title and part_string not in release_title:
                release_title = re.sub(r"\.(German|Japanese|English)\.", f".{part_string}.\\1.", release_title, 1)

    # determine if optional episode exists on release page
    episode_min: Optional[int] = None
    episode_max: Optional[int] = None
    if requested_episode:
        episodes_div = tab.find("div", class_="episodes")
        if episodes_div:
            episode_links = episodes_div.find_all("a", attrs={"data-loop": re.compile(r"^\d+$")})
            total_episodes = len(episode_links)
            if total_episodes > 0:
                ep = int(requested_episode)
                if ep <= total_episodes:
                    episode_min = 1
                    episode_max = total_episodes
                    if release_title:
                        release_title = re.sub(
                            r'(?<=\.)S(\d{1,4})(?=\.)',
                            lambda m: f"S{int(m.group(1)):02d}E{ep:02d}",
                            release_title,
                            count=1,
                            flags=re.IGNORECASE
                        )

    return ReleaseInfo(
        release_title=release_title,
        audio_langs=audio_langs,
        subtitle_langs=subtitle_langs,
        resolution=resolution,
        source=source,
        video=video,
        release_group=release_group,
        season_part=part,
        season=season_num,
        episode_min=episode_min,
        episode_max=episode_max
    )


def guess_title(shared_state, raw_base_title, release_info: ReleaseInfo) -> str:
    # remove labels
    clean_title = raw_base_title.rsplit('(', 1)[0].strip()
    # Remove season/staffel info
    pattern = r'(?i)\b(?:Season|Staffel)\s*\.?\s*\d+\b|\bR\d+\b'
    clean_title = re.sub(pattern, '', clean_title)

    # determine season token
    if release_info.season is not None:
        season_token = f"S{release_info.season:02d}"
    else:
        season_token = ""

    # episode token
    ep_token = ''
    if release_info.episode_min is not None:
        s = release_info.episode_min
        e = release_info.episode_max if release_info.episode_max is not None else s
        ep_token = f"E{s:02d}" + (f"-{e:02d}" if e != s else "")

    title_core = clean_title.strip().replace(' ', '.')
    if season_token:
        title_core += f".{season_token}{ep_token}"
    elif ep_token:
        title_core += f".{ep_token}"

    parts = [title_core]

    part = release_info.season_part
    if part:
        part_string = f"Part.{part}"
        if part_string not in title_core:
            parts.append(part_string)

    prefix = ''
    a, su = release_info.audio_langs, release_info.subtitle_langs
    if len(a) > 2 and 'German' in a:
        prefix = 'German.ML'
    elif 'German' in a and 'Japanese' in a:
        prefix = 'German.DL'
    elif 'German' in a and len(a) == 1:
        prefix = 'German'
    elif a and 'German' in su:
        prefix = f"{a[0]}.Subbed"
    if prefix: parts.append(prefix)

    parts.extend([release_info.resolution, release_info.source, release_info.video])
    title = '.'.join(parts)
    if release_info.release_group:
        title += f"-{release_info.release_group}"
    return shared_state.sanitize_title(title)


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

            release_info = parse_info_from_download_item(tab, page_title=page_title, release_type=release_type,
                                                         requested_episode=episode_in_title)
            real_title = release_info.release_title
            if real_title:
                if real_title.lower() != title.lower():
                    info(f'Identified true release title "{real_title}" on details page')
                    return real_title, release_id
            else:
                # Overwrite values so guessing the title only applies the requested episode
                release_info.episode_min = int(episode_in_title)
                release_info.episode_max = int(episode_in_title)

                guessed_title = guess_title(shared_state, page_title, release_info)
                if guessed_title and guessed_title.lower() != title.lower():
                    info(f'Adjusted guessed release title to "{guessed_title}" from details page')
                    return guessed_title, release_id
        except Exception as e:
            info(f"Error guessing release title from release: {e}")

    return title, release_id


def extract_episode(title: str) -> int | None:
    match = re.search(r'\bS\d{1,4}E(\d+)\b(?![\-E\d])', title)
    if match:
        return int(match.group(1))

    if not re.search(r'\bS\d{1,4}\b', title):
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
