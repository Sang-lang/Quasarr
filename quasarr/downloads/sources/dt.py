# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

import requests
from bs4 import BeautifulSoup
from quasarr.providers.log import info


def get_dt_download_links(shared_state, url, mirror, title):
    headers = {"User-Agent": shared_state.values["user_agent"]}
    session = requests.Session()

    try:
        resp = session.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(resp.text, "html.parser")

        article = soup.find("article")
        if not article:
            info(f"Could not find article block on DT page for {title}")
            return False
        body = article.find("div", class_="card-body")
        if not body:
            info(f"Could not find download section for {title}")
            return False

        anchors = body.find_all("a", href=True)
    except Exception as e:
        info(f"DT site has been updated. Grabbing download links for {title} not possible! ({e})")
        return False

    download_links = []
    for a in anchors:
        href = a["href"].strip()

        if not href.lower().startswith(("http://", "https://")):
            continue

        lower_href = href.lower()
        if "imdb.com" in lower_href or "?ref=" in lower_href:
            continue

        if mirror and mirror not in href:
            continue

        download_links.append(href)

    return download_links
