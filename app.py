"""Compatibility launcher for the React + Flask local application."""
from backend.app import create_app

if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=5001, debug=False, threaded=True)
