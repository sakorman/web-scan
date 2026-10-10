import argparse
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Web Scan local studio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--data", type=Path, default=Path(".data"))
    parser.add_argument("--cert", help="HTTPS certificate path")
    parser.add_argument("--key", help="HTTPS private key path")
    args = parser.parse_args()
    if bool(args.cert) != bool(args.key):
        parser.error("--cert 和 --key 需要同时传入")
    os.environ["WEBSCAN_DATA"] = str(args.data.resolve())
    import uvicorn
    from .api import create_app

    uvicorn.run(
        create_app(),
        host=args.host,
        port=args.port,
        ssl_certfile=args.cert,
        ssl_keyfile=args.key,
    )


if __name__ == "__main__":
    main()
