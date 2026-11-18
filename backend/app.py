# Flask application factory: registers API routes, initializes SQLite, serves the React build, and owns the Jobs controller from backend.jobs.

"""Single local web application serving the API and production React build."""
import os
import atexit
from flask import Flask, jsonify, send_from_directory
from werkzeug.exceptions import HTTPException
from worksight.config import ROOT
from worksight.storage import initialize_db
from backend.jobs import Jobs


def create_app(testing=False):
    app = Flask(__name__, static_folder=None)
    app.config.update(MAX_CONTENT_LENGTH=2 * 1024 ** 3, TESTING=testing)
    initialize_db()
    jobs = Jobs()
    if not testing:
        jobs.recover()
    app.extensions["jobs"] = jobs
    if not testing:
        atexit.register(jobs.close)
    from backend.routes import api
    app.register_blueprint(api, url_prefix="/api")

    @app.errorhandler(Exception)
    def error_response(error):
        code = error.code if isinstance(error, HTTPException) else 404 if isinstance(error, LookupError) else 400 if isinstance(error, ValueError) else 500
        if code == 500:
            app.logger.exception("Request failed")
        return jsonify(error=str(error)), code

    @app.get("/")
    @app.get("/<path:filename>")
    def frontend(filename="index.html"):
        if filename.startswith("api/"):
            return jsonify(error="Unknown API endpoint"), 404
        dist = ROOT / "dashboard" / "dist"
        return send_from_directory(dist, filename if (dist / filename).is_file() else "index.html")
    return app


if __name__ == "__main__":
    create_app().run(host=os.environ.get("WORKSIGHT_HOST", "127.0.0.1"), port=int(os.environ.get("WORKSIGHT_PORT", 5001)), debug=False, threaded=True)
