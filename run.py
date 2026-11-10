import os

from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5001, debug=os.environ.get("FLASK_DEBUG") == "1")
