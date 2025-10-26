# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

import re

from bs4 import BeautifulSoup

from quasarr.providers.log import info, debug
from quasarr.providers.sessions.dl import retrieve_and_validate_session, fetch_via_requests_session, invalidate_session

hostname = "dl"


def extract_links_from_post(post_html):
    """
    Extract download links from a forum post.
    Common patterns: direct links, base64 encoded, obfuscated links
    """
    links = []
    soup = BeautifulSoup(post_html, 'html.parser')
    
    # Find all links in the post
    for link in soup.find_all('a', href=True):
        href = link.get('href')
        
        # Skip internal forum links
        if href.startswith('/') or 'data-load.me' in href:
            continue
        
        # Common file hosters and link crypters
        hoster_patterns = [
            # Link crypters/containers (PRIORITY - these contain the actual links)
            r'filecrypt\.cc',
            r'linksnappy\.io',
            r'relink\.us',
            r'links\.snahp\.it',
            # Direct file hosters
            r'rapidgator\.net',
            r'uploaded\.net',
            r'nitroflare\.com',
            r'turbobit\.net',
            r'ddownload\.com',
            r'filefactory\.com',
            r'katfile\.com',
            r'mexashare\.com',
            r'keep2share\.cc',
            r'alfafile\.net',
            r'mega\.nz',
            r'1fichier\.com'
        ]
        
        for pattern in hoster_patterns:
            if re.search(pattern, href, re.IGNORECASE):
                if href not in links:
                    links.append(href)
                break
    
    return links


def get_dl_download_links(shared_state, url, mirror, title):
    """
    Get download links from a data-load.me thread.
    
    Args:
        shared_state: Shared state object
        url: Thread URL
        mirror: Mirror (not used for data-load.me)
        title: Release title
    
    Returns:
        dict with 'links', 'password', and 'title'
    """
    host = shared_state.values["config"]("Hostnames").get(hostname)
    
    sess = retrieve_and_validate_session(shared_state)
    if not sess:
        info(f"Could not retrieve valid session for {host}")
        return {}

    try:
        # Fetch the thread page
        response = fetch_via_requests_session(shared_state, method="GET", 
                                             target_url=url, 
                                             timeout=30)
        
        if response.status_code != 200:
            info(f"Failed to load thread page: {url} (Status: {response.status_code})")
            return {}
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Extract links from the first post (original post)
        first_post = soup.select_one('article.message--post')
        if not first_post:
            info(f"Could not find first post in thread: {url}")
            return {}
        
        post_content = first_post.select_one('div.bbWrapper')
        if not post_content:
            info(f"Could not find post content in thread: {url}")
            return {}
        
        # Extract all download links
        links = extract_links_from_post(str(post_content))
        
        if not links:
            info(f"No download links found in thread: {url}")
            return {}
        
        # Extract password if present
        password = f"www.{host}"
        password_patterns = [
            r'(?:Passwort|Password|Pass|PW)[\s:]*([^\s<]+)',
            r'www\.data-load\.me'
        ]
        
        post_text = post_content.get_text()
        for pattern in password_patterns:
            match = re.search(pattern, post_text, re.IGNORECASE)
            if match and len(match.groups()) > 0:
                password = match.group(1)
                break
        
        debug(f"Found {len(links)} download link(s) for: {title}")
        
        return {
            "links": links,
            "password": password,
            "title": title
        }
        
    except Exception as e:
        info(f"Error extracting download links from {url}: {e}")
        invalidate_session(shared_state)
        return {}
