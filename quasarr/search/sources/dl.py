# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

import re
import time
from base64 import urlsafe_b64encode
from html import unescape
from urllib.parse import quote_plus, urlencode

from bs4 import BeautifulSoup

from quasarr.providers.imdb_metadata import get_localized_title
from quasarr.providers.log import info, debug
from quasarr.providers.sessions.dl import retrieve_and_validate_session, invalidate_session, fetch_via_requests_session

hostname = "dl"
supported_mirrors = []  # data-load.me doesn't use mirrors


def normalize_title_for_sonarr(title):
    """
    Normalize title for Sonarr by replacing spaces with dots.
    This is done AFTER we get the properly formatted title from data-load.me.
    """
    # Replace spaces with dots
    title = title.replace(' ', '.')
    
    # Fix group separator: " - " or ".-." -> "-"
    title = re.sub(r'\s*-\s*', '-', title)
    title = re.sub(r'\.\-\.', '-', title)
    
    # Remove multiple consecutive dots
    title = re.sub(r'\.{2,}', '.', title)
    
    # Remove leading/trailing dots
    title = title.strip('.')
    
    return title


def dl_feed(shared_state, start_time, request_from, mirror=None):
    """
    Parse the RSS feed from data-load.me and return releases.
    
    Args:
        shared_state: Shared state object
        start_time: Start time for performance measurement
        request_from: Name of requesting service (Sonarr/Radarr/LazyLibrarian)
        mirror: Mirror (not used for data-load.me)
    
    Returns:
        list: List of release dictionaries
    """
    releases = []
    host = shared_state.values["config"]("Hostnames").get(hostname)
    
    if not host:
        debug(f"{hostname}: hostname not configured")
        return releases

    try:
        # Fetch RSS feed with authentication
        sess = retrieve_and_validate_session(shared_state)
        if not sess:
            info(f"Could not retrieve valid session for {host}")
            return releases

        rss_url = f'https://www.{host}/forums/-/index.rss'
        response = sess.get(rss_url, timeout=30)
        
        if response.status_code != 200:
            info(f"{hostname}: RSS feed returned status {response.status_code}")
            return releases
        
        # Parse RSS feed with BeautifulSoup (XML parser)
        soup = BeautifulSoup(response.content, 'xml')
        
        # Find all items in the RSS feed
        items = soup.find_all('item')
        
        if not items:
            debug(f"{hostname}: No entries found in RSS feed")
            return releases
        
        for item in items:
            try:
                # Get title
                title_tag = item.find('title')
                if not title_tag:
                    continue
                    
                title = title_tag.get_text(strip=True)
                if not title:
                    continue
                
                # Clean up title - remove HTML entities and CDATA
                title = unescape(title)
                title = title.replace(']]>', '').replace('<![CDATA[', '')
                
                # Normalize for Sonarr (spaces -> dots)
                title = normalize_title_for_sonarr(title)
                
                # Get thread URL
                link_tag = item.find('link')
                if not link_tag:
                    continue
                    
                thread_url = link_tag.get_text(strip=True)
                if not thread_url:
                    continue
                
                # Get publication date
                date_str = ""
                pub_date = item.find('pubDate')
                if pub_date:
                    date_str = pub_date.get_text(strip=True)
                
                # Size is typically not available in RSS feed
                mb = 0
                imdb_id = None
                
                # data-load.me doesn't require a password, but we need the field for payload compatibility
                password = ""
                
                # Create payload with 6 elements to match API expectations:
                # title|url|mirror|size|password|imdb_id
                payload = urlsafe_b64encode(
                    f"{title}|{thread_url}|{mirror}|{mb}|{password}|{imdb_id or ''}".encode("utf-8")
                ).decode("utf-8")
                link = f"{shared_state.values['internal_address']}/download/?payload={payload}"
                
                releases.append({
                    "details": {
                        "title": title,
                        "hostname": hostname,
                        "imdb_id": imdb_id,
                        "link": link,
                        "mirror": mirror,
                        "size": mb * 1024 * 1024,
                        "date": date_str,
                        "source": thread_url
                    },
                    "type": "protected"
                })
                
            except Exception as e:
                debug(f"{hostname}: error parsing RSS entry: {e}")
                continue
        
    except Exception as e:
        info(f"{hostname}: RSS feed error: {e}")
        invalidate_session(shared_state)
    
    elapsed = time.time() - start_time
    debug(f"Time taken: {elapsed:.2f}s ({hostname})")
    return releases


def dl_search(shared_state, start_time, request_from, search_string,
              mirror=None, season=None, episode=None):
    """
    Search data-load.me with pagination to find the best quality releases.
    
    Strategy: Search up to 5 pages (100 results) to ensure we find 4K/UHD releases.
    data-load.me search is global (not forum-specific), so pagination ensures
    we get high-quality releases that may not be on the first page.
    
    Sonarr's Quality Profile will then select the best release based on user preferences.
    """
    releases = []
    host = shared_state.values["config"]("Hostnames").get(hostname)

    # Handle IMDb ID
    imdb_id = shared_state.is_imdb_id(search_string)
    if imdb_id:
        title = get_localized_title(shared_state, imdb_id, 'de')
        if not title:
            info(f"{hostname}: no title for IMDb {imdb_id}")
            return releases
        search_string = title

    search_string = unescape(search_string)

    # PAGINATION: Search up to 5 pages to find the best quality releases
    # data-load.me returns 20 results per page, so 5 pages = 100 results total
    # This ensures we find 4K/UHD releases even if they're not on page 1
    max_pages = 5
    total_processed = 0
    total_valid = 0
    total_skipped = 0
    
    info(f"{hostname}: Starting paginated search for '{search_string}' (Season: {season}, Episode: {episode}) - up to {max_pages} pages")

    try:
        # Perform search
        sess = retrieve_and_validate_session(shared_state)
        if not sess:
            info(f"Could not retrieve valid session for {host}")
            return releases

        # Paginate through search results
        search_id = None  # Will be extracted from first search response
        
        for page_num in range(1, max_pages + 1):
            info(f"{hostname}: [Page {page_num}/{max_pages}] Searching...")

            if page_num == 1:
                # FIRST SEARCH: Use /search/search endpoint to get search ID
                search_params = {
                    'keywords': search_string,
                    'c[title_only]': 1  # Title-only search
                }
                search_url = f'https://www.{host}/search/search'
            else:
                # PAGINATION: Use the search ID from first page
                if not search_id:
                    info(f"{hostname}: No search ID found, stopping pagination")
                    break
                
                search_params = {
                    'page': page_num,
                    'q': search_string,
                    'o': 'relevance'
                }
                # Use the search ID URL: /search/{search_id}/
                search_url = f'https://www.{host}/search/{search_id}/'
            
            # Execute search with GET
            search_response = fetch_via_requests_session(shared_state, method="GET",
                                                         target_url=search_url,
                                                         get_params=search_params,
                                                         timeout=10)

            if search_response.status_code != 200:
                info(f"{hostname}: [Page {page_num}] returned status {search_response.status_code}, stopping pagination")
                break  # Stop if page fails
            
            # EXTRACT SEARCH ID from first page response URL
            # URL format: https://www.data-load.me/search/42213917/?q=gangs+of+london&o=relevance
            if page_num == 1 and not search_id:
                import re
                # Extract search ID from URL
                match = re.search(r'/search/(\d+)/', search_response.url)
                if match:
                    search_id = match.group(1)
                    info(f"{hostname}: [Page 1] Extracted search ID: {search_id}")
                else:
                    info(f"{hostname}: [Page 1] Could not extract search ID from URL: {search_response.url}")

            soup = BeautifulSoup(search_response.text, 'html.parser')
            
            # Parse search results
            result_items = soup.select('li.block-row')
            
            if not result_items:
                info(f"{hostname}: [Page {page_num}] found 0 results, stopping pagination")
                break  # No more results
            
            info(f"{hostname}: [Page {page_num}] found {len(result_items)} results, processing...")
            
            page_valid = 0
            page_skipped = 0
        
            for item in result_items:
                try:
                    total_processed += 1
                    
                    # Get title and link
                    title_elem = item.select_one('h3.contentRow-title a')
                    if not title_elem:
                        page_skipped += 1
                        total_skipped += 1
                        continue
                    
                    # Get the raw title from data-load.me
                    # CRITICAL: Use separator=' ' to preserve spaces when removing highlight tags!
                    # <em>Gangs</em> <em>of</em> <em>London</em> -> "Gangs of London"
                    title = title_elem.get_text(separator=' ', strip=True)
                    
                    # Clean up multiple spaces that might result from tag removal
                    title = re.sub(r'\s+', ' ', title)
                    
                    # Basic HTML entity cleanup
                    title = unescape(title)
                    
                    # Normalize for Sonarr (spaces -> dots)
                    # "My Hero Academia S01" -> "My.Hero.Academia.S01"
                    title_normalized = normalize_title_for_sonarr(title)
                    
                    thread_url = title_elem.get('href')
                    if thread_url.startswith('/'):
                        thread_url = f"https://www.{host}{thread_url}"
                    
                    # Validate release with normalized title
                    if not shared_state.is_valid_release(title_normalized, request_from, search_string, season, episode):
                        page_skipped += 1
                        total_skipped += 1
                        continue

                    # This is a valid release!
                    page_valid += 1
                    total_valid += 1

                    # Get metadata
                    date_str = ""
                    minor_info = item.select_one('div.contentRow-minor')
                    if minor_info:
                        # Extract date
                        date_elem = minor_info.select_one('time.u-dt')
                        if date_elem:
                            date_str = date_elem.get('datetime', '')
                    
                    # Size is typically not available in search results, set to 0
                    mb = 0
                    
                    # data-load.me doesn't require a password, but we need the field for payload compatibility
                    password = ""
                    
                    # Create payload with 6 elements to match API expectations:
                    # title|url|mirror|size|password|imdb_id
                    payload = urlsafe_b64encode(
                        f"{title_normalized}|{thread_url}|{mirror}|{mb}|{password}|{imdb_id or ''}".encode("utf-8")
                    ).decode("utf-8")
                    link = f"{shared_state.values['internal_address']}/download/?payload={payload}"
                    
                    releases.append({
                        "details": {
                            "title": title_normalized,
                            "hostname": hostname,
                            "imdb_id": imdb_id,
                            "link": link,
                            "mirror": mirror,
                            "size": mb * 1024 * 1024,
                            "date": date_str,
                            "source": thread_url
                        },
                        "type": "protected"
                    })

                except Exception as e:
                    info(f"{hostname}: [Page {page_num}] error parsing item: {e}")
            
            # Page summary
            info(f"{hostname}: [Page {page_num}] SUMMARY - Valid: {page_valid}, Skipped: {page_skipped}")

    except Exception as e:
        info(f"{hostname}: search error: {e}")
        invalidate_session(shared_state)

    # Final summary
    if releases:
        info(f"{hostname}: FINAL - Processed {total_processed} results, found {total_valid} valid releases - providing to {request_from}")
    else:
        info(f"{hostname}: FINAL - No valid releases found after searching {max_pages} pages")
    
    elapsed = time.time() - start_time
    debug(f"Time taken: {elapsed:.2f}s ({hostname})")
    
    return releases
