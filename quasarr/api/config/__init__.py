# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

from quasarr.providers.html_templates import render_form
from quasarr.providers.html_templates import render_button
from quasarr.storage.setup import hostname_form_html, save_hostnames


def setup_config(app, shared_state):
    @app.get('/hostnames/')
    def hostnames_ui():
        message = """<p>
            At least one hostname must be kept.
        </p>"""
        back_button = f'''<p>
                        {render_button("Back", "secondary", {"onclick": "location.href='/'"})}
                    </p>'''
        return render_form("Update your Hostnames", hostname_form_html(shared_state, message) + back_button)

    @app.post("/api/hostnames")
    def hostnames_api():
        return save_hostnames(shared_state, timeout=1, first_run=False)
