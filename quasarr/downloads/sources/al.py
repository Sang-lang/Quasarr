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
    – params: for GET only
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


def get_release_title_by_id(shared_state, details_html, release_id, title):
    soup = BeautifulSoup(details_html, "html.parser")
    tab = soup.find("div", class_="tab-pane", id=f"download_{release_id}")
    if tab:
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
                    if final_title and final_title.lower() != title.lower():
                        info(f'Identified true release title "{final_title}" on details page')
                        return final_title
    return title


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

    title = get_release_title_by_id(shared_state, details_html, release_id, title)

    episode_in_title = extract_episode(title)
    if episode_in_title:
        selection = episode_in_title - 1  # Convert to zero-based index
    else:
        selection = "cnl"

    anime_identifier = url.rstrip("/").split("/")[-1]

    if int(release_id) < 1:
        release_id = 1
        info(f"No valid release ID found for {title} at {url}, falling back to {release_id}")

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
            else:
                tries = 0
                while tries < 3:
                    try:
                        info(
                            f"Starting attempt {tries + 1} to solve CAPTCHA for "
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

                            if code == "success" and content_items:
                                info("CAPTCHA solved successfully on attempt {}.".format(tries + 1))
                                break
                            else:
                                info(
                                    f"CAPTCHA POST returned code={code}, message={message}. Retrying... (attempt {tries + 1})")

                                if "slowndown" in str(message).lower():
                                    wait_period = 30
                                    info(
                                        f"CAPTCHAs solved too quickly. Waiting {wait_period} seconds before next attempt...")
                                    time.sleep(wait_period)

                                tries += 1
                                time.sleep(3)
                        else:
                            info(f"CAPTCHA solver returned invalid solution, retrying... (attempt {tries + 1})")
                            tries += 1

                    except RuntimeError as e:
                        info(f"Error solving CAPTCHA: {e}")
                        tries += 1
                    else:
                        info(f"CAPTCHA solver returned invalid solution, retrying... (attempt {tries + 1})")
                        tries += 1

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
