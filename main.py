import logging
import threading
import webbrowser

from webapp import app

URL = "http://127.0.0.1:5000"


def main():
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    threading.Timer(1.0, lambda: webbrowser.open(URL)).start()
    app.run(host="127.0.0.1", port=5000, threaded=True)


if __name__ == "__main__":
    main()
