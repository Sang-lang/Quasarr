# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

import json

from quasarr.downloads.sources.dd import get_dd_download_links
from quasarr.downloads.sources.dt import get_dt_download_links
from quasarr.downloads.sources.dw import get_dw_download_links
from quasarr.downloads.sources.nx import get_nx_download_links
from quasarr.downloads.sources.sf import get_sf_download_links, resolve_sf_redirect
from quasarr.downloads.sources.sl import get_sl_download_links
from quasarr.downloads.sources.wd import get_wd_download_links
from quasarr.providers.log import info
from quasarr.providers.notifications import send_discord_message


def download(shared_state, request_from, title, url, mirror, size_mb, password, imdb_id=None):
    if "radarr".lower() in request_from.lower():
        category = "movies"
    else:
        category = "tv"

    package_id = f"Quasarr_{category}_{str(hash(title + url)).replace('-', '')}"

    if imdb_id is not None and imdb_id.lower() == "none":
        imdb_id = None

    dd = shared_state.values["config"]("Hostnames").get("dd")
    dt = shared_state.values["config"]("Hostnames").get("dt")
    dw = shared_state.values["config"]("Hostnames").get("dw")
    nx = shared_state.values["config"]("Hostnames").get("nx")
    sf = shared_state.values["config"]("Hostnames").get("sf")
    sl = shared_state.values["config"]("Hostnames").get("sl")
    wd = shared_state.values["config"]("Hostnames").get("wd")

    if dd and dd.lower() in url.lower():
        links = get_dd_download_links(shared_state, mirror, title)
        if links:
            info(f"Decrypted {len(links)} download links for {title}")
            send_discord_message(shared_state, title=title, case="unprotected", imdb_id=imdb_id)
            added = shared_state.download_package(links, title, password, package_id)
            if not added:
                info(f"Failed to add {title} to linkgrabber")
                package_id = None
        else:
            info(f"Found 0 links decrypting {title} on DD")
            package_id = None

    elif dt and dt.lower() in url.lower():
        links = get_dt_download_links(shared_state, url, mirror, title)
        if links:
            info(f"Decrypted {len(links)} download links for {title}")
            send_discord_message(shared_state, title=title, case="unprotected", imdb_id=imdb_id)
            added = shared_state.download_package(links, title, password, package_id)
            if not added:
                info(f"Failed to add {title} to linkgrabber")
                package_id = None
        else:
            info(f"Found 0 links decrypting {title} on DT")
            package_id = None


    elif dw and dw.lower() in url.lower():
        links = get_dw_download_links(shared_state, url, mirror, title)
        info(f'CAPTCHA-Solution required for "{title}" at: "{shared_state.values['external_address']}/captcha"')
        send_discord_message(shared_state, title=title, case="captcha", imdb_id=imdb_id)
        blob = json.dumps({"title": title, "links": links, "size_mb": size_mb, "password": password})
        shared_state.values["database"]("protected").update_store(package_id, blob)

    elif nx and nx.lower() in url.lower():
        links = get_nx_download_links(shared_state, url, title)
        if links:
            info(f"Decrypted {len(links)} download links for {title}")
            send_discord_message(shared_state, title=title, case="unprotected", imdb_id=imdb_id)
            added = shared_state.download_package(links, title, password, package_id)
            if not added:
                info(f"Failed to add {title} to linkgrabber")
                package_id = None
        else:
            info(f"Found 0 links decrypting {title} on NX")
            package_id = None

    elif sf and sf.lower() in url.lower():
        if url.startswith(f"https://{sf}/external"):  # from interactive search
            url = resolve_sf_redirect(url, shared_state.values["user_agent"])
        elif url.startswith(f"https://{sf}/"):  # from feed search
            data = get_sf_download_links(shared_state, url, mirror, title)
            url = data.get("real_url")
            if not imdb_id:
                imdb_id = data.get("imdb_id")

        if url:
            info(f'CAPTCHA-Solution required for "{title}" at: "{shared_state.values['external_address']}/captcha"')
            send_discord_message(shared_state, title=title, case="captcha", imdb_id=imdb_id)
            blob = json.dumps(
                {"title": title, "links": [[url, "filecrypt"]], "size_mb": size_mb, "password": password,
                 "mirror": mirror})
            shared_state.values["database"]("protected").update_store(package_id, blob)
        else:
            info(f"Failed to get download link from SF for {title} - {url}")
            package_id = None

    elif sl and sl.lower() in url.lower():
        data = get_sl_download_links(shared_state, url, mirror, title)
        links = data.get("links")
        if not imdb_id:
            imdb_id = data.get("imdb_id")
        if links:
            info(f"Decrypted {len(links)} download links for {title}")
            send_discord_message(shared_state, title=title, case="unprotected", imdb_id=imdb_id)
            added = shared_state.download_package(links, title, password, package_id)
            if not added:
                info(f"Failed to add {title} to linkgrabber")
                package_id = None
        else:
            info(f"Found 0 links decrypting {title} on SL")
            package_id = None

    elif wd and wd.lower() in url.lower():
        data = get_wd_download_links(shared_state, url, mirror, title)
        links = data.get("links")
        if not imdb_id:
            imdb_id = data.get("imdb_id")
        if links:
            info(f'CAPTCHA-Solution required for "{title}" at: "{shared_state.values['external_address']}/captcha"')
            send_discord_message(shared_state, title=title, case="captcha", imdb_id=imdb_id)
            blob = json.dumps({"title": title, "links": links, "size_mb": size_mb, "password": password})
            shared_state.values["database"]("protected").update_store(package_id, blob)
        else:
            info(f"Found 0 links decrypting {title} on WD")
            package_id = None

    elif "filecrypt".lower() in url.lower():
        info(f'CAPTCHA-Solution required for "{title}" at: "{shared_state.values['external_address']}/captcha"')
        send_discord_message(shared_state, title=title, case="captcha", imdb_id=imdb_id)
        blob = json.dumps(
            {"title": title, "links": [[url, "filecrypt"]], "size_mb": size_mb, "password": password, "mirror": mirror})
        shared_state.values["database"]("protected").update_store(package_id, blob)

    else:
        package_id = None
        info(f"Could not parse URL for {title} - {url}")

    return package_id
