# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from quasarr.providers.log import info
from quasarr.search.sources.dd import dd_search
from quasarr.search.sources.dt import dt_feed, dt_search
from quasarr.search.sources.dw import dw_feed, dw_search
from quasarr.search.sources.fx import fx_feed, fx_search
from quasarr.search.sources.nx import nx_feed, nx_search
from quasarr.search.sources.sf import sf_feed, sf_search
from quasarr.search.sources.sl import sl_feed, sl_search
from quasarr.search.sources.wd import wd_feed, wd_search


def get_search_results(shared_state, request_from, search_string="", mirror=None, season="", episode=""):
    results = []

    dd = shared_state.values["config"]("Hostnames").get("dd")
    dt = shared_state.values["config"]("Hostnames").get("dt")
    dw = shared_state.values["config"]("Hostnames").get("dw")
    fx = shared_state.values["config"]("Hostnames").get("fx")
    nx = shared_state.values["config"]("Hostnames").get("nx")
    sf = shared_state.values["config"]("Hostnames").get("sf")
    sl = shared_state.values["config"]("Hostnames").get("sl")
    wd = shared_state.values["config"]("Hostnames").get("wd")

    start_time = time.time()

    functions = []
    if search_string:
        # Remove trailing year (e.g., 1999, 2021) if present
        search_string = re.sub(r'\s*(19|20)\d{2}$', '', search_string)

        if dd:
            functions.append(lambda: dd_search(shared_state, start_time, request_from, search_string,
                                               mirror=mirror,
                                               season=season, episode=episode)
                             )
        if dt:
            functions.append(lambda: dt_search(shared_state, start_time, request_from, search_string,
                                               mirror=mirror,
                                               season=season, episode=episode)
                             )
        if dw:
            functions.append(lambda: dw_search(shared_state, start_time, request_from, search_string,
                                               mirror=mirror,
                                               season=season, episode=episode)
                             )
        if fx:
            functions.append(lambda: fx_search(shared_state, start_time, request_from, search_string,
                                               mirror=mirror,
                                               season=season, episode=episode)
                             )
        if nx:
            functions.append(lambda: nx_search(shared_state, start_time, request_from, search_string,
                                               mirror=mirror,
                                               season=season, episode=episode)
                             )
        if sf:
            functions.append(lambda: sf_search(shared_state, start_time, request_from, search_string,
                                               mirror=mirror,
                                               season=season, episode=episode)
                             )
        if sl:
            functions.append(lambda: sl_search(shared_state, start_time, request_from, search_string,
                                               mirror=mirror,
                                               season=season, episode=episode)
                             )
        if wd:
            functions.append(lambda: wd_search(shared_state, start_time, request_from, search_string,
                                               mirror=mirror,
                                               season=season, episode=episode)
                             )
    else:
        if dd:
            functions.append(lambda: dd_search(shared_state, start_time, request_from, mirror=mirror))
        if dt:
            functions.append(lambda: dt_feed(shared_state, start_time, request_from, mirror=mirror))
        if dw:
            functions.append(lambda: dw_feed(shared_state, start_time, request_from, mirror=mirror))
        if fx:
            functions.append(lambda: fx_feed(shared_state, start_time, request_from, mirror=mirror))
        if nx:
            functions.append(lambda: nx_feed(shared_state, start_time, request_from, mirror=mirror))
        if sf:
            functions.append(lambda: sf_feed(shared_state, start_time, request_from, mirror=mirror))
        if sl:
            functions.append(lambda: sl_feed(shared_state, start_time, request_from, mirror=mirror))
        if wd:
            functions.append(lambda: wd_feed(shared_state, start_time, request_from, mirror=mirror))

    stype = f'search phrase "{search_string}"' if search_string else "feed search"
    info(f'Starting {len(functions)} search functions for {stype}... This may take some time.')

    with ThreadPoolExecutor() as executor:
        futures = [executor.submit(func) for func in functions]
        for future in as_completed(futures):
            try:
                result = future.result()
                results.extend(result)
            except Exception as e:
                info(f"An error occurred: {e}")

    elapsed_time = time.time() - start_time
    info(f"Providing {len(results)} releases to {request_from} for {stype}. Time taken: {elapsed_time:.2f} seconds")

    return results
