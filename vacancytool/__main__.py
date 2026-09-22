from pathlib import Path
from waitress import serve

from .web import create_app


if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    app = create_app(root, start_scheduler=True)
    serve(app, host="127.0.0.1", port=5090, threads=4)
