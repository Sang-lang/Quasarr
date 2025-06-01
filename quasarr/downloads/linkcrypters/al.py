# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

import base64
from io import BytesIO

from Cryptodome.Cipher import AES
from PIL import Image, ImageChops

from quasarr.providers.log import info, debug


class CNL:
    """
    Given a dict with the same structure as your `chosen_data` (i.e.
    {
      "links": [...],
      "cnl": {
        "jk": "<obfuscated_hex_string>",
        "crypted": "<base64_ciphertext>"
      }
    }),
    this class will decrypt the Base64 payload, strip padding, and return a list of URLs.
    """

    def __init__(self, chosen_data: dict):
        """
        chosen_data should contain at least:
          - "cnl": {
                "jk": "<hex‐encoded string, length > 16>",
                "crypted": "<Base64‐encoded ciphertext>"
            }
        """
        self.cnl_info = chosen_data.get("cnl", {})
        self.jk = self.cnl_info.get("jk")
        self.crypted_blob = self.cnl_info.get("crypted")

        if not self.jk or not self.crypted_blob:
            raise KeyError("Missing 'jk' or 'crypted' fields in JSON.")

        # Swap positions 15 and 16 in the hex string
        k_list = list(self.jk)
        if len(k_list) <= 16:
            raise ValueError("Invalid 'jk' string length; must be > 16 characters.")
        k_list[15], k_list[16] = k_list[16], k_list[15]
        self.fixed_key_hex = "".join(k_list)

    def _aes_decrypt(self, data_b64: str, key_hex: str) -> bytes:
        """
        Decode the Base64‐encoded payload, interpret key_hex as hex,
        then use AES-CBC with IV=key_bytes to decrypt.
        Returns raw bytes (still possibly containing padding).
        """
        try:
            encrypted_data = base64.b64decode(data_b64)
        except Exception as e:
            raise ValueError("Failed to decode base64 data") from e

        try:
            key_bytes = bytes.fromhex(key_hex)
        except Exception as e:
            raise ValueError("Failed to convert key to bytes (invalid hex)") from e

        iv = key_bytes
        cipher = AES.new(key_bytes, AES.MODE_CBC, iv)

        try:
            decrypted = cipher.decrypt(encrypted_data)
        except Exception as e:
            raise ValueError("AES decryption failed") from e

        return decrypted

    def decrypt(self) -> list[str]:
        """
        Runs the full decryption pipeline and returns a list of non‐empty URLs.
        Strips out null and backspace padding bytes, decodes to UTF-8, and
        splits on CRLF.
        """
        raw_plain = self._aes_decrypt(self.crypted_blob, self.fixed_key_hex)

        # Remove any 0x00 or 0x08 bytes
        try:
            cleaned = raw_plain.replace(b"\x00", b"").replace(b"\x08", b"")
            text = cleaned.decode("utf-8")
        except Exception as e:
            raise ValueError("Failed to decode decrypted data to UTF-8") from e

        # Split on CRLF, discard any empty lines
        urls = [line for line in text.splitlines() if line.strip()]
        return urls


def decrypt_content(content_items: list[dict], mirror: str | None) -> list[str]:
    """
    Go through every item in `content_items`, but if `mirror` is not None,
    only attempt to decrypt those whose "hoster" field contains `mirror`.
    If no items match that filter, falls back to decrypting every single item.

    Returns a flat list of all decrypted URLs.
    """
    if mirror:
        filtered = [item for item in content_items if mirror in item.get("hoster", "")]
    else:
        filtered = []

    if mirror and not filtered:
        info(f"No items found for mirror='{mirror}'. Falling back to all content_items.")
        filtered = content_items.copy()

    if not mirror:
        filtered = content_items.copy()

    decrypted_links: list[str] = []

    for idx, item in enumerate(filtered):
        hoster_name = item.get("hoster", "<unknown>")
        cnl_info = item.get("cnl", {})
        jnk = cnl_info.get("jk")
        crypted = cnl_info.get("crypted")

        if not jnk or not crypted:
            info(f"[Item {idx} | hoster={hoster_name}] Missing 'jk' or 'crypted' → skipping")
            continue

        try:
            decryptor = CNL(item)
            urls = decryptor.decrypt()
            decrypted_links.extend(urls)
            debug(f"[Item {idx} | hoster={hoster_name}] Decrypted {len(urls)} URLs")
        except Exception as e:
            # Log and keep going; one bad item won’t stop the rest.
            info(f"[Item {idx} | hoster={hoster_name}] Error during decryption: {e}")

    return decrypted_links


def calculate_pixel_based_difference(img1, img2):
    """Pillow-based absolute-difference % over all channels."""
    # ensure same mode and size
    diff = ImageChops.difference(img1, img2).convert("RGB")
    w, h = diff.size
    # histogram is [R0, R1, ..., R255, G0, ..., B255]
    hist = diff.histogram()
    zero_R = hist[0]
    zero_G = hist[256]
    zero_B = hist[512]
    total_elements = w * h * 3
    zero_elements = zero_R + zero_G + zero_B
    non_zero = total_elements - zero_elements
    return (non_zero * 100) / total_elements


def solve_captcha(hostname, shared_state, fetch_via_flaresolverr, fetch_via_requests_session):
    al = shared_state.values["config"]("Hostnames").get(hostname)
    captcha_base = f"https://www.{al}/files/captcha"

    result = fetch_via_flaresolverr(
        shared_state,
        method="POST",
        target_url=captcha_base,
        post_data={"cID": 0, "rT": 1},
        timeout=30
    )

    try:
        raw_ids = result["json"]
    except ValueError:
        raise RuntimeError(f"Cannot decode captcha IDs: {result['text']}")

    if not isinstance(raw_ids, list) or len(raw_ids) < 2:
        raise RuntimeError("Unexpected captcha IDs format.")

    # Download each image
    images = []
    for c in raw_ids:
        img_url = f"{captcha_base}?cid=0&hash={c}"
        r_img = fetch_via_requests_session(shared_state, method="GET", target_url=img_url, timeout=30)
        if r_img.status_code != 200:
            raise RuntimeError(f"Failed to download captcha image {c} (HTTP {r_img.status_code})")
        elif not r_img.content:
            raise RuntimeError(f"Captcha image {c} is empty or invalid.")
        images.append((c, r_img.content))

    # Calculate differences using PIL
    pil_images = []
    for cid, raw_bytes in images:
        img = Image.open(BytesIO(raw_bytes))

        # if it’s a palette (P) image with an indexed transparency, go through RGBA
        if img.mode == "P" and "transparency" in img.info:
            img = img.convert("RGBA")

        # if it has an alpha channel, composite it over white
        if img.mode == "RGBA":
            background = Image.new("RGB", img.size, (255, 255, 255))
            background.paste(img, mask=img.split()[3])
            img = background
        else:
            # for all other modes, just convert to plain RGB
            img = img.convert("RGB")

        pil_images.append((cid, img))

    diffs_pil = []
    for idx_i, (cid_i, img_i) in enumerate(pil_images):
        tot = 0.0
        for idx_j, (cid_j, img_j) in enumerate(pil_images):
            if idx_i == idx_j:
                continue
            tot += calculate_pixel_based_difference(img_i, img_j)
        diffs_pil.append((cid_i, tot))

    identified_captcha_image, cum_pct_pil = max(diffs_pil, key=lambda x: x[1])
    different_pixels_percentage = int(cum_pct_pil / len(images)) if images else int(cum_pct_pil)
    info(f'CAPTCHA image "{identified_captcha_image}.png" - difference to others: {different_pixels_percentage}%')

    result = fetch_via_flaresolverr(
        shared_state,
        method="POST",
        target_url=captcha_base,
        post_data={"cID": 0, "pC": identified_captcha_image, "rT": 2},
        timeout=60
    )

    return {
        "response": result["text"],
        "captcha_id": identified_captcha_image
    }
