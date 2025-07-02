# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

import json

from quasarr.downloads.linkcrypters.hide import decrypt_links_if_hide
from quasarr.downloads.sources.al import get_al_download_links
from quasarr.downloads.sources.dd import get_dd_download_links
from quasarr.downloads.sources.dt import get_dt_download_links
from quasarr.downloads.sources.dw import get_dw_download_links
from quasarr.downloads.sources.mb import get_mb_download_links
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
    success = True

    if imdb_id is not None and imdb_id.lower() == "none":
        imdb_id = None

    al = shared_state.values["config"]("Hostnames").get("al")
    dd = shared_state.values["config"]("Hostnames").get("dd")
    dt = shared_state.values["config"]("Hostnames").get("dt")
    dw = shared_state.values["config"]("Hostnames").get("dw")
    mb = shared_state.values["config"]("Hostnames").get("mb")
    nx = shared_state.values["config"]("Hostnames").get("nx")
    sf = shared_state.values["config"]("Hostnames").get("sf")
    sl = shared_state.values["config"]("Hostnames").get("sl")
    wd = shared_state.values["config"]("Hostnames").get("wd")

    if al and al.lower() in url.lower():
        release_id = password
        payload = get_al_download_links(shared_state, url, mirror, title, release_id)
        links = payload.get("links", [])
        title = payload.get("title", title)
        password = payload.get("password", "")
        if links:
            info(f"Decrypted {len(links)} download links for {title}")
            send_discord_message(shared_state, title=title, case="unprotected", imdb_id=imdb_id, source=url)
            added = shared_state.download_package(links, title, password, package_id)
            if not added:
                fail(title, package_id, shared_state,
                     reason=f'Failed to add {len(links)} links for "{title}" to linkgrabber')
                success = False
        else:
            fail(title, package_id, shared_state,
                 reason=f'Offline / no links found for "{title}" on AL - "{url}"')
            success = False

    elif dd and dd.lower() in url.lower():
        links = get_dd_download_links(shared_state, mirror, title)
        if links:
            info(f"Decrypted {len(links)} download links for {title}")
            send_discord_message(shared_state, title=title, case="unprotected", imdb_id=imdb_id, source=url)
            added = shared_state.download_package(links, title, password, package_id)
            if not added:
                fail(title, package_id, shared_state,
                     reason=f'Failed to add {len(links)} links for "{title}" to linkgrabber', source=url)
                success = False
        else:
            fail(title, package_id, shared_state,
                 reason=f'Offline / no links found for "{title}" on DD - "{url}"')
            success = False

    elif dt and dt.lower() in url.lower():
        links = get_dt_download_links(shared_state, url, mirror, title)
        if links:
            info(f"Decrypted {len(links)} download links for {title}")
            send_discord_message(shared_state, title=title, case="unprotected", imdb_id=imdb_id, source=url)
            added = shared_state.download_package(links, title, password, package_id)
            if not added:
                fail(title, package_id, shared_state,
                     reason=f'Failed to add {len(links)} links for "{title}" to linkgrabber')
                success = False
        else:
            fail(title, package_id, shared_state,
                 reason=f'Offline / no links found for "{title}" on DT - "{url}"')
            success = False


    elif dw and dw.lower() in url.lower():
        links = get_dw_download_links(shared_state, url, mirror, title)
        info(f'CAPTCHA-Solution required for "{title}" at: "{shared_state.values['external_address']}/captcha"')
        send_discord_message(shared_state, title=title, case="captcha", imdb_id=imdb_id, source=url)
        blob = json.dumps({"title": title, "links": links, "size_mb": size_mb, "password": password})
        shared_state.values["database"]("protected").update_store(package_id, blob)

    elif mb and mb.lower() in url.lower():
        links = get_mb_download_links(shared_state, url, mirror, title)
        info(f'CAPTCHA-Solution required for "{title}" at: "{shared_state.values['external_address']}/captcha"')
        send_discord_message(shared_state, title=title, case="captcha", imdb_id=imdb_id, source=url)
        blob = json.dumps({"title": title, "links": links, "size_mb": size_mb, "password": password})
        shared_state.values["database"]("protected").update_store(package_id, blob)

    elif nx and nx.lower() in url.lower():
        links = get_nx_download_links(shared_state, url, title)
        if links:
            info(f"Decrypted {len(links)} download links for {title}")
            send_discord_message(shared_state, title=title, case="unprotected", imdb_id=imdb_id, source=url)
            added = shared_state.download_package(links, title, password, package_id)
            if not added:
                fail(title, package_id, shared_state,
                     reason=f'Failed to add {len(links)} links for "{title}" to linkgrabber')
                success = False
        else:
            fail(title, package_id, shared_state,
                 reason=f'Offline / no links found for "{title}" on NX - "{url}"')
            success = False

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
            send_discord_message(shared_state, title=title, case="captcha", imdb_id=imdb_id, source=url)
            blob = json.dumps(
                {"title": title, "links": [[url, "filecrypt"]], "size_mb": size_mb, "password": password,
                 "mirror": mirror})
            shared_state.values["database"]("protected").update_store(package_id, blob)
        else:
            fail(title, package_id, shared_state,
                 reason=f'Failed to get download link from SF for "{title}" - "{url}"')
            success = False

    elif sl and sl.lower() in url.lower():
        data = get_sl_download_links(shared_state, url, mirror, title)
        links = data.get("links")
        if not imdb_id:
            imdb_id = data.get("imdb_id")
        if links:
            info(f"Decrypted {len(links)} download links for {title}")
            send_discord_message(shared_state, title=title, case="unprotected", imdb_id=imdb_id, source=url)
            added = shared_state.download_package(links, title, password, package_id)
            if not added:
                fail(title, package_id, shared_state,
                     reason=f'Failed to add {len(links)} links for "{title}" to linkgrabber')
                success = False
        else:
            fail(title, package_id, shared_state,
                 reason=f'Offline / no links found for "{title}" on SL - "{url}"')
            success = False

    elif wd and wd.lower() in url.lower():
        data = get_wd_download_links(shared_state, url, mirror, title)
        links = data.get("links")
        if not imdb_id:
            imdb_id = data.get("imdb_id")
        if links:
            decrypted_links = decrypt_links_if_hide(shared_state, links)
            if decrypted_links and decrypted_links.get("status") != "none":
                status = decrypted_links.get("status", "error")
                links = decrypted_links.get("results", [])
                if status == "success":
                    info(f"Decrypted {len(links)} download links for {title}")
                    send_discord_message(shared_state, title=title, case="unprotected", imdb_id=imdb_id, source=url)
                    added = shared_state.download_package(links, title, password, package_id)
                    if not added:
                        fail(title, package_id, shared_state,
                             reason=f'Failed to add {len(links)} links for "{title}" to linkgrabber')
                        success = False
                else:
                    fail(title, package_id, shared_state,
                         reason=f'Error decrypting hide.cx links for "{title}" on WD - "{url}"')
                    success = False
            else:
                info(f'CAPTCHA-Solution required for "{title}" at: "{shared_state.values['external_address']}/captcha"')
                send_discord_message(shared_state, title=title, case="captcha", imdb_id=imdb_id, source=url)
                blob = json.dumps({"title": title, "links": links, "size_mb": size_mb, "password": password})
                shared_state.values["database"]("protected").update_store(package_id, blob)
        else:
            fail(title, package_id, shared_state,
                 reason=f'Offline / no links found for "{title}" on WD - "{url}"')
            success = False

    elif "filecrypt".lower() in url.lower():
        info(f'CAPTCHA-Solution required for "{title}" at: "{shared_state.values['external_address']}/captcha"')
        send_discord_message(shared_state, title=title, case="captcha", imdb_id=imdb_id, source=url)
        blob = json.dumps(
            {"title": title, "links": [[url, "filecrypt"]], "size_mb": size_mb, "password": password, "mirror": mirror})
        shared_state.values["database"]("protected").update_store(package_id, blob)

    else:
        info(f'Could not parse URL for "{title}" - "{url}"')
        success = False

    return {
        "success": success,
        "package_id": package_id,
        "title": title
    }


def fail(title, package_id, shared_state, reason="Offline / no links found"):
    try:
        info(f"Reason for failure: {reason}")
        blob = json.dumps({"title": title, "error": reason})
        stored = shared_state.get_db("failed").store(package_id, json.dumps(blob))
        if stored:
            info(f'Package "{title}" marked as failed!"')
            return True
        else:
            info(f'Failed to mark package "{title}" as failed!"')
            return False
    except Exception as e:
        info(f'Error marking package "{package_id}" as failed: {e}')
        return False
