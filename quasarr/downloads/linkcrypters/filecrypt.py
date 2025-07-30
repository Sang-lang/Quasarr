# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

import base64
import json
import math
import os
import random
import re
import time
import xml.dom.minidom
from io import BytesIO
from urllib.parse import urlparse

import cv2
import dukpy
import numpy as np
import requests
from Cryptodome.Cipher import AES
from PIL import Image
from bs4 import BeautifulSoup

from quasarr.providers.log import info, debug


def solve_circlecaptcha(shared_state, session):
    captcha_url = "https://filecrypt.cc/captcha/circle.php"
    debug(f"Solving circlecaptcha from: {captcha_url}, session: {session}")

    headers = {'User-Agent': shared_state.values["user_agent"]}
    cookies = {'PHPSESSID': session}

    response = requests.get(captcha_url, headers=headers, cookies=cookies, verify=False)
    if response.status_code != 200:
        info("Failed to load circhecaptcha!")
        return None

    image = Image.open(BytesIO(response.content))
    image_array = np.array(image)
    opencv_image = cv2.cvtColor(image_array, cv2.COLOR_RGB2BGR) if image_array.ndim == 3 else image_array
    grayscale_image = cv2.cvtColor(opencv_image, cv2.COLOR_BGR2GRAY)
    height, width = grayscale_image.shape

    edges_low = cv2.Canny(grayscale_image, 30, 100)
    edges_high = cv2.Canny(grayscale_image, 50, 150)
    enhanced_grayscale = cv2.equalizeHist(grayscale_image)
    edges_enhanced = cv2.Canny(enhanced_grayscale, 30, 100)
    combined_edges = cv2.bitwise_or(edges_low, cv2.bitwise_or(edges_high, edges_enhanced))

    def detect_circles(input_image, canny_upper_threshold, accumulator_threshold):
        return cv2.HoughCircles(
            input_image, cv2.HOUGH_GRADIENT, dp=1, minDist=10,
            param1=canny_upper_threshold, param2=accumulator_threshold, minRadius=15, maxRadius=30
        )

    circles_normal = detect_circles(grayscale_image, 50, 30)
    circles_enhanced = detect_circles(enhanced_grayscale, 40, 25)

    all_detected_circles = []
    if circles_normal is not None:
        all_detected_circles.extend(circles_normal[0])
    if circles_enhanced is not None:
        for center_x, center_y, radius in circles_enhanced[0]:
            if not any(np.hypot(center_x - existing_x, center_y - existing_y) < 10
                       for existing_x, existing_y, _ in all_detected_circles):
                all_detected_circles.append((center_x, center_y, radius))

    result_image = opencv_image.copy()
    circle_analysis_data = []

    if all_detected_circles:
        num_sample_points = 120
        sample_angles = np.linspace(0, 2 * np.pi, num_sample_points, endpoint=False)

        def find_gaps(binary_data):
            data_string = ''.join(map(str, binary_data)) + ''.join(map(str, binary_data[:30]))
            return [len(match.group()) for match in __import__('re').finditer(r'0+', data_string)
                    if len(match.group()) <= num_sample_points]

        for (float_center_x, float_center_y, float_radius) in all_detected_circles:
            center_x, center_y, radius = int(float_center_x), int(float_center_y), int(float_radius)
            edge_presence = []

            for angle in sample_angles:
                edge_found = False
                for radius_offset in range(-3, 4):
                    check_x = int(center_x + (radius + radius_offset) * np.cos(angle))
                    check_y = int(center_y + (radius + radius_offset) * np.sin(angle))
                    if 0 <= check_x < width and 0 <= check_y < height and combined_edges[check_y, check_x] > 0:
                        edge_found = True
                        break
                edge_presence.append(1 if edge_found else 0)

            background_threshold = np.percentile(grayscale_image, 75)
            intensity_presence = []

            for angle in sample_angles:
                pixel_x = int(center_x + radius * np.cos(angle))
                pixel_y = int(center_y + radius * np.sin(angle))
                if 0 <= pixel_x < width and 0 <= pixel_y < height:
                    window = grayscale_image[max(0, pixel_y - 2):pixel_y + 3, max(0, pixel_x - 2):pixel_x + 3]
                    intensity_presence.append(1 if window.size > 0 and window.min() < background_threshold - 20 else 0)
                else:
                    intensity_presence.append(0)

            edge_gaps = find_gaps(edge_presence)
            intensity_gaps = find_gaps(intensity_presence)
            edge_completeness = sum(edge_presence) / num_sample_points
            intensity_completeness = sum(intensity_presence) / num_sample_points
            max_edge_gap = max(edge_gaps) if edge_gaps else 0
            max_intensity_gap = max(intensity_gaps) if intensity_gaps else 0

            if max_intensity_gap > 0:
                primary_gap = max_intensity_gap
                completeness = intensity_completeness
            else:
                primary_gap = max_edge_gap
                completeness = edge_completeness

            gap_angle_degrees = primary_gap / num_sample_points * 360

            circle_analysis_data.append({
                'center_x': center_x,
                'center_y': center_y,
                'radius': radius,
                'completeness': completeness,
                'gap_angle_degrees': gap_angle_degrees
            })

    # Rank and draw circles
    ranked_circles = sorted(circle_analysis_data, key=lambda circle: circle['gap_angle_degrees'], reverse=True)

    for rank_index, circle in enumerate(ranked_circles, start=1):
        center_x, center_y = circle['center_x'], circle['center_y']
        cv2.putText(result_image, str(rank_index), (center_x - 5, center_y + 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 255, 0), 1)

    if ranked_circles:
        most_incomplete_circle = ranked_circles[0]
        circle_center_x, circle_center_y = most_incomplete_circle['center_x'], most_incomplete_circle['center_y']
        circle_radius = most_incomplete_circle['radius']

        # Generate random target position within the circle, biased toward center
        random_angle = random.uniform(0, 2 * math.pi)
        # Use power of 2 to bias toward center (smaller radii more likely)
        random_radius_factor = random.random() ** 2
        # Scale to be closer to center than edge (max 70% of radius)
        target_radius = circle_radius * random_radius_factor * 0.7

        target_x = int(circle_center_x + target_radius * math.cos(random_angle))
        target_y = int(circle_center_y + target_radius * math.sin(random_angle))

        cv2.drawMarker(result_image, (target_x, target_y), (0, 0, 255),
                       markerType=cv2.MARKER_CROSS, markerSize=20, thickness=2)
        cv2.putText(result_image, "1", (circle_center_x - 5, circle_center_y + 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 255, 0), 1)

        debug("Top 3 solution candidates (by gap size):")
        for rank_index, circle in enumerate(ranked_circles[:3], start=1):
            debug(f"- #{rank_index} at ({circle['center_x']}, {circle['center_y']}): "
                  f"gap={circle['gap_angle_degrees']:.0f}°, "
                  f"complete={circle['completeness'] * 100:.0f}%")

        if os.getenv('DEBUG'):
            debug("Saving debug image with detected circles and target marker as debug_capcha.png")
            cv2.imwrite("debug_capcha.png", result_image)
        debug(f"Total circles detected: {len(circle_analysis_data)}")

        # Return the randomized target coordinates instead of exact center
        info(f"Found incomplete circle at: x={target_x}, y={target_y}")
        return target_x, target_y
    else:
        info("Could not find any incomplete circles in the image. Unexpected response likely.")
        return None


class CNL:
    def __init__(self, crypted_data):
        self.crypted_data = crypted_data

    def jk_eval(self, f_def):
        js_code = f"""
        {f_def}
        f();
        """

        result = dukpy.evaljs(js_code).strip()

        return result

    def aes_decrypt(self, data, key):
        try:
            encrypted_data = base64.b64decode(data)
        except Exception as e:
            raise ValueError("Failed to decode base64 data") from e

        try:
            key_bytes = bytes.fromhex(key)
        except Exception as e:
            raise ValueError("Failed to convert key to bytes") from e

        iv = key_bytes
        cipher = AES.new(key_bytes, AES.MODE_CBC, iv)

        try:
            decrypted_data = cipher.decrypt(encrypted_data)
        except ValueError as e:
            raise ValueError("Decryption failed") from e

        try:
            return decrypted_data.decode('utf-8').replace('\x00', '').replace('\x08', '')
        except UnicodeDecodeError as e:
            raise ValueError("Failed to decode decrypted data") from e

    def decrypt(self):
        crypted = self.crypted_data[2]
        jk = "function f(){ return \'" + self.crypted_data[1] + "';}"
        key = self.jk_eval(jk)
        uncrypted = self.aes_decrypt(crypted, key)
        urls = [result for result in uncrypted.split("\r\n") if len(result) > 0]

        return urls


class DLC:
    def __init__(self, shared_state, dlc_file):
        self.shared_state = shared_state
        self.data = dlc_file
        self.KEY = b"cb99b5cbc24db398"
        self.IV = b"9bc24cb995cb8db3"
        self.API_URL = "http://service.jdownloader.org/dlcrypt/service.php?srcType=dlc&destType=pylo&data="

    def parse_packages(self, start_node):
        return [
            (
                base64.b64decode(node.getAttribute("name")).decode("utf-8"),
                self.parse_links(node)
            )
            for node in start_node.getElementsByTagName("package")
        ]

    def parse_links(self, start_node):
        return [
            base64.b64decode(node.getElementsByTagName("url")[0].firstChild.data).decode("utf-8")
            for node in start_node.getElementsByTagName("file")
        ]

    def decrypt(self):
        if not isinstance(self.data, bytes):
            raise TypeError("data must be bytes.")

        all_urls = []

        try:
            data = self.data.strip()

            data += b"=" * (-len(data) % 4)

            dlc_key = data[-88:].decode("utf-8")
            dlc_data = base64.b64decode(data[:-88])

            headers = {'User-Agent': self.shared_state.values["user_agent"]}

            dlc_content = requests.get(self.API_URL + dlc_key, headers=headers, timeout=10).content.decode("utf-8")

            rc = base64.b64decode(re.search(r"<rc>(.+)</rc>", dlc_content, re.S).group(1))[:16]

            cipher = AES.new(self.KEY, AES.MODE_CBC, self.IV)
            key = iv = cipher.decrypt(rc)

            cipher = AES.new(key, AES.MODE_CBC, iv)
            xml_data = base64.b64decode(cipher.decrypt(dlc_data)).decode("utf-8")

            root = xml.dom.minidom.parseString(xml_data).documentElement
            content_node = root.getElementsByTagName("content")[0]

            packages = self.parse_packages(content_node)

            for package in packages:
                urls = package[1]
                all_urls.extend(urls)

        except Exception as e:
            info("DLC Error: " + str(e))
            return None

        return all_urls


def get_filecrypt_links(shared_state, token, title, url, password=None, mirror=None):
    info("Attempting to decrypt Filecrypt link: " + url)
    session = requests.Session()

    headers = {'User-Agent': shared_state.values["user_agent"]}

    password_field = None
    if password:
        try:
            output = session.get(url, headers=headers)
            soup = BeautifulSoup(output.text, 'html.parser')
            input_element = soup.find('input', placeholder=lambda value: value and 'password' in value.lower())
            password_field = input_element['name']
            info("Password field name identified: " + password_field)
            url = output.url
        except:
            info("No password field found. Skipping password entry!")

    if password and password_field:
        info("Using Password: " + password)
        output = session.post(url, data=password_field + "=" + password,
                              headers={'User-Agent': shared_state.values["user_agent"],
                                       'Content-Type': 'application/x-www-form-urlencoded'})
    else:
        output = session.get(url, headers=headers)

    url = output.url
    soup = BeautifulSoup(output.text, 'html.parser')
    if bool(soup.find_all("input", {"id": "p4assw0rt"})):
        info(f"Password was wrong or missing. Could not get links for {title}")
        return False

    no_captcha_present = bool(soup.find("form", {"class": "cnlform"}))
    if no_captcha_present:
        info("No CAPTCHA present. Skipping token!")
    else:
        circle_captcha = bool(soup.find_all("div", {"class": "circle_captcha"}))
        i = 0
        while circle_captcha and i < 3:
            random_x = str(random.randint(100, 200))
            random_y = str(random.randint(100, 200))
            output = session.post(url, data="buttonx.x=" + random_x + "&buttonx.y=" + random_y,
                                  headers={'User-Agent': shared_state.values["user_agent"],
                                           'Content-Type': 'application/x-www-form-urlencoded'})
            url = output.url
            soup = BeautifulSoup(output.text, 'html.parser')
            circle_captcha = bool(soup.find_all("div", {"class": "circle_captcha"}))

        output = session.post(url, data="cap_token=" + token, headers={'User-Agent': shared_state.values["user_agent"],
                                                                       'Content-Type': 'application/x-www-form-urlencoded'})
    url = output.url

    if "/404.html" in url:
        info("Filecrypt returned 404 - current IP is likely banned or the link is offline.")

    soup = BeautifulSoup(output.text, 'html.parser')

    solved = bool(soup.find_all("div", {"class": "container"}))
    if not solved:
        info("Token rejected by Filecrypt! Try another CAPTCHA to proceed...")
        return False
    else:
        season_number = ""
        episode_number = ""
        episode_in_title = re.findall(r'.*\.s(\d{1,3})e(\d{1,3})\..*', title, re.IGNORECASE)
        season_in_title = re.findall(r'.*\.s(\d{1,3})\..*', title, re.IGNORECASE)
        if episode_in_title:
            try:
                season_number = str(int(episode_in_title[0][0]))
                episode_number = str(int(episode_in_title[0][1]))
            except:
                pass
        elif season_in_title:
            try:
                season_number = str(int(season_in_title[0]))
            except:
                pass

        season = ""
        episode = ""
        tv_show_selector = soup.find("div", {"class": "dlpart"})
        if tv_show_selector:

            season = "season="
            episode = "episode="

            season_selection = soup.find("div", {"id": "selbox_season"})
            try:
                if season_selection:
                    season += str(season_number)
            except:
                pass

            episode_selection = soup.find("div", {"id": "selbox_episode"})
            try:
                if episode_selection:
                    episode += str(episode_number)
            except:
                pass

        if episode_number and not episode:
            info(f"Missing select for episode number {episode_number}! Expect undesired links in the output.")

        links = []

        mirrors = []
        mirrors_available = soup.select("a[href*=mirror]")
        if not mirror and mirrors_available:
            for mirror in mirrors_available:
                try:
                    mirror_query = mirror.get("href").split("?")[1]
                    base_url = url.split("?")[0] if "mirror" in url else url
                    mirrors.append(f"{base_url}?{mirror_query}")
                except IndexError:
                    continue
        else:
            mirrors = [url]

        for mirror in mirrors:
            if not len(mirrors) == 1:
                output = session.get(mirror, headers=headers)
                url = output.url
                soup = BeautifulSoup(output.text, 'html.parser')

            try:
                crypted_payload = soup.find("form", {"class": "cnlform"}).get('onsubmit')
                crypted_data = re.findall(r"'(.*?)'", crypted_payload)
                if not title:
                    title = crypted_data[3]
                crypted_data = [
                    crypted_data[0],
                    crypted_data[1],
                    crypted_data[2],
                    title
                ]
                if episode and season:
                    domain = urlparse(url).netloc
                    filtered_cnl_secret = soup.find("input", {"name": "hidden_cnl_id"}).attrs["value"]
                    filtered_cnl_link = f"https://{domain}/_CNL/{filtered_cnl_secret}.html?{season}&{episode}"
                    filtered_cnl_result = session.post(filtered_cnl_link,
                                                       headers=headers)
                    if filtered_cnl_result.status_code == 200:
                        filtered_cnl_data = json.loads(filtered_cnl_result.text)
                        if filtered_cnl_data["success"]:
                            crypted_data = [
                                crypted_data[0],
                                filtered_cnl_data["data"][0],
                                filtered_cnl_data["data"][1],
                                title
                            ]
                links.extend(CNL(crypted_data).decrypt())
            except:
                if "The owner of this folder has deactivated all hosts in this container in their settings." in soup.text:
                    info(f"Mirror deactivated by the owner: {mirror}")
                    continue

                info("Click'n'Load not found! Falling back to DLC...")
                try:
                    crypted_payload = soup.find("button", {"class": "dlcdownload"}).get("onclick")
                    crypted_data = re.findall(r"'(.*?)'", crypted_payload)
                    dlc_secret = crypted_data[0]
                    domain = urlparse(url).netloc
                    if episode and season:
                        dlc_link = f"https://{domain}/DLC/{dlc_secret}.dlc?{episode}&{season}"
                    else:
                        dlc_link = f"https://{domain}/DLC/{dlc_secret}.dlc"
                    dlc_file = session.get(dlc_link, headers=headers).content
                    links.extend(DLC(shared_state, dlc_file).decrypt())
                except:
                    info("DLC not found! Falling back to first available download Button...")

                    base_url = urlparse(url).netloc
                    phpsessid = session.cookies.get('PHPSESSID')
                    if not phpsessid:
                        info("PHPSESSID cookie not found! Cannot proceed with download links extraction.")
                        return False

                    results = []

                    for button in soup.find_all('button'):
                        # Find the correct data-* attribute (only one expected)
                        data_attrs = [v for k, v in button.attrs.items() if k.startswith('data-') and k != 'data-i18n']
                        if not data_attrs:
                            continue

                        link_id = data_attrs[0]
                        row = button.find_parent('tr')
                        mirror_tag = row.find('a', class_='external_link') if row else None
                        mirror_name = mirror_tag.get_text(strip=True) if mirror_tag else 'unknown'
                        full_url = f"https://{base_url}/Link/{link_id}.html"
                        results.append((full_url, mirror_name))

                    sorted_results = sorted(results, key=lambda x: 0 if 'rapidgator' in x[1].lower() else 1)

                    for result_url, mirror in sorted_results:
                        i = 3
                        while i > 0:
                            i -= 1
                            solution = solve_circlecaptcha(shared_state, phpsessid)
                            if not solution:
                                time.sleep(3)
                            else:
                                x, y = solution

                                cookies = {'PHPSESSID': phpsessid}

                                headers = {
                                    'User-Agent': shared_state.values["user_agent"],
                                }

                                data = {
                                    "buttonx.x": x,
                                    "buttonx.y": y,
                                }

                                response = requests.post(result_url, cookies=cookies, headers=headers, data=data)

                                if response.url.endswith("404.html"):
                                    info("Your IP has been blocked by Filecrypt. Please try again later.")
                                    return False

                                match = re.search(r"top\.location\.href\s*=\s*['\"]([^'\"]+)['\"]", response.text)
                                if match:
                                    solution = match.group(1)
                                    info(f"Redirect URL: {solution}")
                                    redirect_resp = requests.get(solution,
                                                                 headers=headers,
                                                                 cookies=cookies,
                                                                 allow_redirects=True,
                                                                 timeout=10)
                                    if "expired" in redirect_resp.text.lower():
                                        debug("Session expired while solving. This should never happen!")
                                    else:
                                        download_link = redirect_resp.url
                                        if redirect_resp.ok:
                                            info(f"Successfully resolved download link: {download_link}")
                                            return {
                                                "status": "success",
                                                "links": [download_link]
                                            }

                        info("Failed to solve circlecaptcha after multiple attempts. Your IP is likely banned!")
                        debug(f'Session "{phpsessid}" for {result_url} will not live long. Submit new CAPTCHA quickly!')
                        return {
                            "status": "replaced",
                            "replace_url": result_url,
                            "mirror": mirror,
                            "session": phpsessid
                        }

    if not links:
        info("No links found in Filecrypt response!")
        return False

    return {
        "status": "success",
        "links": links
    }
