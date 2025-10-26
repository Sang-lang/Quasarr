# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

import re

from bs4 import BeautifulSoup

from quasarr.providers.log import info, debug

hostname = "wcx"


def extract_links_from_page(page_html):
    """
    Extract download links from a warez.cx detail page.
    Looks for filecrypt.cc links and other link crypters.
    """
    links = []
    soup = BeautifulSoup(page_html, 'html.parser')
    
    # Find all links
    for link in soup.find_all('a', href=True):
        href = link.get('href')
        
        # Skip internal links
        if href.startswith('/') or 'warez.cx' in href:
            continue
        
        # Common link crypters and file hosters
        patterns = [
            # Link crypters (priority)
            r'filecrypt\.cc',
            r'linksnappy\.io',
            r'relink\.us',
            r'links\.snahp\.it',
            # Direct file hosters
            r'rapidgator\.net',
            r'uploaded\.net',
            r'nitroflare\.com',
            r'ddownload\.com',
            r'filefactory\.com',
            r'katfile\.com',
            r'mexashare\.com',
            r'keep2share\.cc',
            r'mega\.nz',
            r'1fichier\.com'
        ]
        
        for pattern in patterns:
            if re.search(pattern, href, re.IGNORECASE):
                if href not in links:
                    links.append(href)
                break
    
    return links


def get_wcx_download_links(shared_state, url, mirror, title):
    """
    Get download links from a warez.cx detail page.
    
    warez.cx uses a Vue.js/Quasar framework with API calls.
    The page structure requires special handling.
    
    Args:
        shared_state: Shared state object
        url: Detail page URL
        mirror: Mirror (not used)
        title: Release title
    
    Returns:
        dict with 'links', 'password', and 'title'
    """
    wcx = shared_state.values["config"]("Hostnames").get(hostname.lower())
    
    import requests
    
    headers = {
        'User-Agent': shared_state.values["user_agent"],
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
    }

    try:
        # Try to fetch the detail page
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code != 200:
            info(f"{hostname.upper()}: Failed to load page: {url} (Status: {response.status_code})")
            return {}
        
        # warez.cx uses client-side rendering with Vue.js
        # We need to check if we can extract links from the HTML
        # or if we need to use the API
        
        # First, try to extract the slug/ID from the URL
        # URL format: https://warez.cx/detail/{slug}/{title}
        slug_match = re.search(r'/detail/([^/]+)', url)
        if slug_match:
            slug = slug_match.group(1)
            
            # Try to fetch via API
            api_url = f'https://api.{wcx}/release/{slug}'
            try:
                api_response = requests.get(api_url, headers={'User-Agent': shared_state.values["user_agent"]}, 
                                           timeout=10)
                if api_response.status_code == 200:
                    data = api_response.json()
                    
                    # Extract download links from API response
                    links = []
                    if 'downloads' in data:
                        for download in data['downloads']:
                            link = download.get('url') or download.get('link')
                            if link:
                                links.append(link)
                    elif 'links' in data:
                        for link_item in data['links']:
                            link = link_item if isinstance(link_item, str) else link_item.get('url')
                            if link:
                                links.append(link)
                    
                    if links:
                        password = f"www.{wcx}"
                        debug(f"{hostname.upper()}: Found {len(links)} download link(s) via API for: {title}")
                        
                        return {
                            "links": links,
                            "password": password,
                            "title": title
                        }
            except:
                # API failed, fall back to HTML parsing
                pass
        
        # Fall back to HTML parsing
        links = extract_links_from_page(response.text)
        
        if not links:
            info(f"{hostname.upper()}: No download links found on page: {url}")
            return {}
        
        # Extract password
        password = f"www.{wcx}"
        password_patterns = [
            r'(?:Passwort|Password|Pass|PW)[\s:]*([^\s<]+)',
            r'www\.warez\.cx'
        ]
        
        for pattern in password_patterns:
            match = re.search(pattern, response.text, re.IGNORECASE)
            if match and len(match.groups()) > 0:
                password = match.group(1)
                break
        
        debug(f"{hostname.upper()}: Found {len(links)} download link(s) for: {title}")
        
        return {
            "links": links,
            "password": password,
            "title": title
        }
        
    except Exception as e:
        info(f"{hostname.upper()}: Error extracting download links from {url}: {e}")
        return {}
