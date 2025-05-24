# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

import traceback
import xml.sax.saxutils as sax_utils
from base64 import urlsafe_b64decode
from functools import wraps
from xml.etree import ElementTree

from bottle import abort, request

from quasarr.downloads import download
from quasarr.downloads.packages import get_packages, delete_package
from quasarr.providers import shared_state
from quasarr.providers.log import info, debug
from quasarr.search import get_search_results
from quasarr.storage.config import Config


def require_api_key(func):
    @wraps(func)
    def decorated(*args, **kwargs):
        api_key = Config('API').get('key')
        if not request.query.apikey:
            return abort(401, "Missing API key")
        if request.query.apikey != api_key:
            return abort(403, "Invalid API key")
        return func(*args, **kwargs)

    return decorated


def setup_arr_routes(app):
    @app.get('/download/')
    def fake_nzb_file():
        payload = request.query.payload
        decoded_payload = urlsafe_b64decode(payload).decode("utf-8").split("|")
        title = decoded_payload[0]
        url = decoded_payload[1]
        mirror = decoded_payload[2]
        size_mb = decoded_payload[3]
        password = decoded_payload[4]
        imdb_id = decoded_payload[5]
        return f'<nzb><file title="{title}" url="{url}" mirror="{mirror}" size_mb="{size_mb}" password="{password}" imdb_id="{imdb_id}"/></nzb>'

    @app.post('/api')
    @require_api_key
    def download_fake_nzb_file():
        downloads = request.files.getall('name')
        nzo_ids = []  # naming structure for package IDs expected in newznab

        for upload in downloads:
            file_content = upload.file.read()
            root = ElementTree.fromstring(file_content)

            title = sax_utils.unescape(root.find(".//file").attrib["title"])

            url = root.find(".//file").attrib["url"]
            mirror = None if (mirror := root.find(".//file").attrib.get("mirror")) == "None" else mirror

            size_mb = root.find(".//file").attrib["size_mb"]
            password = root.find(".//file").attrib.get("password")
            imdb_id = root.find(".//file").attrib.get("imdb_id")

            info(f'Attempting download for "{title}"')
            request_from = request.headers.get('User-Agent')
            downloaded = download(shared_state, request_from, title, url, mirror, size_mb, password, imdb_id)
            try:
                success = downloaded["success"]
                package_id = downloaded["package_id"]

                if success:
                    info(f'"{title}" added successfully!')
                else:
                    info(f'"{title}" added unsuccessfully! See log for details.')
                nzo_ids.append(package_id)
            except KeyError:
                info(f'Failed to download "{title}" - no package_id returned')

        return {
            "status": True,
            "nzo_ids": nzo_ids
        }

    @app.get('/api')
    @app.get('/api/<mirror>')
    @require_api_key
    def quasarr_api(mirror=None):
        api_type = 'arr_download_client' if request.query.mode else 'arr_indexer' if request.query.t else None

        if api_type == 'arr_download_client':
            # This builds a mock SABnzbd API response based on the My JDownloader integration
            try:
                mode = request.query.mode
                if mode == "version":
                    return {
                        "version": "4.3.2"
                    }
                elif mode == "get_config":
                    return {
                        "config": {
                            "misc": {
                                "quasarr": True,
                                "complete_dir": "/tmp/"
                            },
                            "categories": [
                                {
                                    "name": "*",
                                    "order": 0,
                                    "dir": "",
                                },
                                {
                                    "name": "movies",
                                    "order": 1,
                                    "dir": "",
                                },
                                {
                                    "name": "tv",
                                    "order": 2,
                                    "dir": "",
                                }
                            ]
                        }
                    }
                elif mode == "fullstatus":
                    return {
                        "status": {
                            "quasarr": True
                        }
                    }
                elif mode == "queue" or mode == "history":
                    if request.query.name and request.query.name == "delete":
                        package_id = request.query.value
                        deleted = delete_package(shared_state, package_id)
                        return {
                            "status": deleted,
                            "nzo_ids": [package_id]
                        }

                    packages = get_packages(shared_state)
                    if mode == "queue":
                        return {
                            "queue": {
                                "paused": False,
                                "slots": packages["queue"]
                            }
                        }
                    elif mode == "history":
                        return {
                            "history": {
                                "paused": False,
                                "slots": packages["history"]
                            }
                        }
            except Exception as e:
                info(f"Error loading packages: {e}")
                info(traceback.format_exc())
            return {
                "status": False
            }

        elif api_type == 'arr_indexer':
            # this builds a mock Newznab API response based on Quasarr search
            try:
                if mirror:
                    debug(f'Search will only return releases that match this mirror: "{mirror}"')

                mode = request.query.t
                request_from = request.headers.get('User-Agent')

                if mode == 'caps':
                    info(f"Providing indexer capability information to {request_from}")
                    return '''<?xml version="1.0" encoding="UTF-8"?>
                                <caps>
                                  <server 
                                    version="1.33.7" 
                                    title="Quasarr" 
                                    url="https://quasarr.indexer/" 
                                    email="support@quasarr.indexer" 
                                  />
                                  <limits max="999" default="999" />
                                  <registration available="no" open="no" />
                                  <searching>
                                    <search available="yes" supportedParams="q" />
                                    <tv-search available="yes" supportedParams="q,imdbid,season,ep" />
                                    <movie-search available="yes" supportedParams="imdbid" />
                                  </searching>
                                  <categories>
                                    <category id="5000" name="TV" />
                                    <category id="2000" name="Movies" />
                                  </categories>
                                </caps>'''
                elif mode in ['movie', 'tvsearch', 'search']:
                    releases = []

                    if mode == 'movie':
                        # supported params: imdbid
                        search_param = f"tt{getattr(request.query, 'imdbid', '')}" \
                            if getattr(request.query, 'imdbid', '') else ""

                        releases = get_search_results(shared_state, request_from,
                                                      search_string=search_param,
                                                      mirror=mirror
                                                      )

                    elif mode == 'search':
                        # supported params: q
                        search_param = getattr(request.query, 'q', '')
                        if search_param:
                            releases = get_search_results(shared_state, request_from,
                                                          search_string=search_param,
                                                          mirror=mirror
                                                          )
                        else:
                            debug(f'No search parameter provided. Ignoring request: {dict(request.query)}')

                    elif mode == 'tvsearch':
                        # supported params: q, imdbid, season and ep
                        if getattr(request.query, 'imdbid', ''):
                            search_param = getattr(request.query, 'imdbid', '')
                            if not search_param.startswith("tt"):
                                search_param = f"tt{search_param}"
                        else:
                            search_param = getattr(request.query, 'q', "")
                        season = getattr(request.query, 'season', None)
                        episode = getattr(request.query, 'ep', None)

                        offset = getattr(request.query, 'offset', "")  # ignoring offset higher than 0 on purpose
                        if int(offset) == 0:
                            releases = get_search_results(shared_state, request_from,
                                                          search_string=search_param,
                                                          mirror=mirror,
                                                          season=season,
                                                          episode=episode
                                                          )
                        else:
                            debug(f'Offset higher than 0 is not supported. Ignoring request: {dict(request.query)}')

                    items = ""
                    for release in releases:
                        release = release["details"]

                        prefix = f'[{release["hostname"].upper()}]'

                        items += f'''
                        <item>
                            <title>{prefix} {sax_utils.escape(release["title"])}</title>
                            <guid isPermaLink="True">{release["link"]}</guid>
                            <link>{release["link"]}</link>
                            <comments>{release["source"]}</comments>
                            <pubDate>{release["date"]}</pubDate>
                            <enclosure url="{release["link"]}" length="{release["size"]}" type="application/x-nzb" />
                        </item>'''

                    return f'''<?xml version="1.0" encoding="UTF-8"?>
                                <rss>
                                    <channel>
                                        {items}
                                    </channel>
                                </rss>'''
            except Exception as e:
                info(f"Error loading search results: {e}")
                info(traceback.format_exc())

        info(f"Unknown request: {dict(request.query)}")
        return {"error": True}
