# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

from bottle import Bottle

import quasarr.providers.html_images as images
from quasarr.api.arr import setup_arr_routes
from quasarr.api.captcha import setup_captcha_routes
from quasarr.api.config import setup_config
from quasarr.api.sponsors_helper import setup_sponsors_helper_routes
from quasarr.providers import shared_state
from quasarr.providers.html_templates import render_button, render_centered_html
from quasarr.providers.web_server import Server
from quasarr.storage.config import Config


def get_api(shared_state_dict, shared_state_lock):
    shared_state.set_state(shared_state_dict, shared_state_lock)

    app = Bottle()

    setup_arr_routes(app)
    setup_captcha_routes(app)
    setup_config(app, shared_state)
    setup_sponsors_helper_routes(app)

    @app.get('/')
    def index():
        protected = shared_state.get_db("protected").retrieve_all_titles()
        api_key = Config('API').get('key')

        captcha_hint = ""
        if protected:
            plural = 's' if len(protected) > 1 else ''
            captcha_hint += f"""
            <h2>Link{plural} waiting for CAPTCHA solution</h2>
            """

            if not shared_state.values.get("helper_active"):
                captcha_hint += f"""
                <p>
                    <a href="https://github.com/rix1337/Quasarr?tab=readme-ov-file#sponsorshelper" target="_blank">
                        Sponsors get automated CAPTCHA solutions!
                    </a>
                </p>
                """

            plural = 's' if len(protected) > 1 else ''
            captcha_hint += f"""
            <p>{render_button(f"Solve CAPTCHA{plural}", 'primary', {'onclick': "location.href='/captcha'"})}</p>
            """

        info = f"""
        <h1><img src="{images.logo}" type="image/png" alt="Quasarr logo" class="logo"/>Quasarr</h1>
        {captcha_hint}
        <h2>Setup Instructions</h2>
        <p>
        <a href="https://github.com/rix1337/Quasarr?tab=readme-ov-file#instructions" target="_blank">
            Refer to the README for detailed instructions.
        </a>
        <br>
            Use the <strong>URL</strong> and <strong>API Key</strong> to set up
            a <strong>Newznab Indexer</strong> and <strong>SABnzbd Download Client</strong> in Radarr/Sonarr:
        </p>
        
        <h3>URL</h3>
        <div class="url-wrapper">
          <input id="urlInput" class="copy-input" type="text" readonly value="{shared_state.values['internal_address']}" />
          <button id="copyUrl" class="btn-primary small">Copy</button>
        </div>
        
        <script>
          const urlInput = document.getElementById('urlInput');
          const copyUrlBtn = document.getElementById('copyUrl');
        
          copyUrlBtn.onclick = () => {{
            urlInput.select();
            document.execCommand('copy');
            copyUrlBtn.innerText = 'Copied!';
            setTimeout(() => {{ copyUrlBtn.innerText = 'Copy'; }}, 2000);
          }};
        </script>

        <h3>API Key</h3>
        <div class="api-key-wrapper">
          <input id="apiKeyInput" class="copy-input" type="password" readonly value="{api_key}" />
          <button id="toggleKey" class="btn-secondary small">Show</button>
          <button id="copyKey" class="btn-primary small">Copy</button>
        </div>

        <p>{render_button("Regenerate API key", "secondary", {"onclick": "if(confirm('Regenerate API key?')) location.href='/regenerate-api-key';"})}</p>

        <script>
          const apiInput = document.getElementById('apiKeyInput');
          const toggleBtn = document.getElementById('toggleKey');
          const copyBtn = document.getElementById('copyKey');

          toggleBtn.onclick = () => {{
            const isHidden = apiInput.type === 'password';
            apiInput.type = isHidden ? 'text' : 'password';
            toggleBtn.innerText = isHidden ? 'Hide' : 'Show';
          }};
          copyBtn.onclick = () => {{
            apiInput.type = 'text';
            apiInput.select();
            document.execCommand('copy');
            copyBtn.innerText = 'Copied!';
            toggleBtn.innerText = 'Hide';
            setTimeout(() => {{ copyBtn.innerText = 'Copy'; }}, 2000);
          }};
        </script>
        
        <h3>Hostnames</h3>
        <div class="api-key-wrapper">
          <button class="btn-primary small" onclick="location.href='/hostnames/'">Update Hostnames</button>
        </div>
        """
        return render_centered_html(info)

    @app.get('/regenerate-api-key')
    def regenerate_api_key():
        api_key = shared_state.generate_api_key()
        return f"""
        <script>
          alert('API key replaced with: {api_key}');
          window.location.href = '/';
        </script>
        """

    Server(app, listen='0.0.0.0', port=shared_state.values["port"]).serve_forever()
