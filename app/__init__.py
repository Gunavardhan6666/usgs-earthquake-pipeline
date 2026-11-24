"""
app/__init__.py
---------------
Flask application factory for the USGS Earthquake Analytics Dashboard.
"""

from flask import Flask


def create_app() -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")

    from app.routes import bp
    app.register_blueprint(bp)

    return app
