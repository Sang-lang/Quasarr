# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

import re

import requests
from bs4 import BeautifulSoup

from quasarr.providers.log import info
from quasarr.providers.sessions.nx import retrieve_and_validate_session


def get_filer_folder_links(shared_state, url):
    try:
        headers = {
            'User-Agent': shared_state.values["user_agent"],
            'Referer': url
        }
        response = requests.get(url, headers=headers, timeout=10)
        links = []
        if response:
            soup = BeautifulSoup(response.content, 'html.parser')
            folder_links = soup.find_all('a', href=re.compile("/get/"))
            for link in folder_links:
                link = "https://filer.net" + link.get('href')
                if link not in links:
                    links.append(link)
        return links
    except:
        pass
    return url


def get_nx_download_links(shared_state, url, title):
    nx = shared_state.values["config"]("Hostnames").get("nx")

    if f"{nx}/release/" not in url:
        info("Link is not a Release link, could not proceed:" + url)

    nx_session = retrieve_and_validate_session(shared_state)
    if not nx_session:
        info(f"Could not retrieve valid session for {nx}")
        return []

    headers = {
        'User-Agent': shared_state.values["user_agent"],
        'Referer': url
    }

    json_data = {}

    url_segments = url.split('/')
    payload_url = '/'.join(url_segments[:-2]) + '/api/getLinks/' + url_segments[-1]

    payload = nx_session.post(payload_url,
                              headers=headers,
                              json=json_data,
                              timeout=10
                              )

    if payload.status_code == 200:
        try:
            payload = payload.json()
        except:
            info("Invalid response decrypting " + str(title) + " URL: " + str(url))
            shared_state.values["database"]("sessions").delete("nx")
            return []

    try:
        decrypted_url = payload['link'][0]['url']
        if decrypted_url:
            if "filer.net/folder/" in decrypted_url:
                urls = get_filer_folder_links(shared_state, decrypted_url)
            else:
                urls = [decrypted_url]
            return urls
    except:
        pass

    info("Something went wrong decrypting " + str(title) + " URL: " + str(url))
    shared_state.values["database"]("sessions").delete("nx")
    return []
