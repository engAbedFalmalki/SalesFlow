"""Start the API from any working directory: python backend/serve.py"""

from salesflow.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main(["api"]))
