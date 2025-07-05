# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

import re
from typing import List, Dict, Any

import requests

from quasarr.providers.log import info, debug


def unhide_links(shared_state, url):
    try:
        links = []

        match = re.search(r"container/([a-z0-9\-]+)", url)
        if not match:
            info(f"Invalid hide.cx URL: {url}")
            return []

        container_id = match.group(1)
        info(f"Fetching hide.cx container with ID: {container_id}")

        headers = {'User-Agent': shared_state.values["user_agent"]}

        container_url = f"https://api.hide.cx/containers/{container_id}"
        response = requests.get(container_url, headers=headers)
        data = response.json()

        for link in data.get("links", []):
            link_id = link.get("id")
            if not link_id:
                continue

            debug(f"Fetching hide.cx link with ID: {link_id}")
            link_url = f"https://api.hide.cx/containers/{container_id}/links/{link_id}"
            link_data = requests.get(link_url, headers=headers).json()

            final_url = link_data.get("url")
            if final_url and final_url not in links:
                links.append(final_url)

        return links
    except Exception as e:
        info(f"Error fetching hide.cx links: {e}")
        return []


def decrypt_links_if_hide(shared_state: Any, items: List[List[str]]) -> Dict[str, Any]:
    """
    Resolve redirects and decrypt hide.cx links from a list of item lists.

    Each item list must include:
      - index 0: the URL to resolve
      - any additional metadata at subsequent indices (ignored here)

    :param shared_state: State object required by unhide_links function
    :param items: List of lists, where each inner list has the URL at index 0
    :return: Dict with 'status' and 'results' (flat list of decrypted link URLs)
    """
    if not items:
        info("No items provided to decrypt.")
        return {"status": "error", "results": []}

    session = requests.Session()
    session.max_redirects = 5

    hide_urls: List[str] = []
    for item in items:
        original_url = item[0]
        if not original_url:
            debug(f"Skipping item without URL: {item}")
            continue

        try:
            # Try HEAD first, fallback to GET
            try:
                resp = session.head(original_url, allow_redirects=True, timeout=10)
            except requests.RequestException:
                resp = session.get(original_url, allow_redirects=True, timeout=10)

            final_url = resp.url
            if "hide.cx" in final_url:
                debug(f"Identified hide.cx link: {final_url}")
                hide_urls.append(final_url)
            else:
                debug(f"Not a hide.cx link (skipped): {final_url}")

        except requests.RequestException as e:
            info(f"Error resolving URL {original_url}: {e}")
            continue

    if not hide_urls:
        debug(f"No hide.cx links found among {len(items)} items.")
        return {"status": "none", "results": []}

    info(f"Found {len(hide_urls)} hide.cx URLs; decrypting...")
    decrypted_links: List[str] = []
    for url in hide_urls:
        try:
            links = unhide_links(shared_state, url)
            if not links:
                debug(f"No links decrypted for {url}")
                continue
            decrypted_links.extend(links)
        except Exception as e:
            info(f"Failed to decrypt {url}: {e}")
            continue

    if not decrypted_links:
        info(f"Could not decrypt any links from hide.cx URLs.")
        return {"status": "error", "results": []}

    return {"status": "success", "results": decrypted_links}
