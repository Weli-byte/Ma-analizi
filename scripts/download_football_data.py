"""Download football-data.co.uk CSVs into data/raw/football_data/ (TLS verification ON).

    python scripts/download_football_data.py [--insecure]

--insecure disables certificate verification (explicit opt-in; the site's cert has failed
verification before). Checksums are recorded by the pipeline manifest either way.
Alternatively download manually: https://www.football-data.co.uk/mmz4281/<YYSS>/<DIV>.csv
and save as <DIV>_<YYSS>.csv (e.g. E0_2324.csv, SP1_2324.csv).
"""

import argparse
import ssl
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "data" / "raw" / "football_data"
SEASONS = ["1920", "2021", "2122", "2223", "2324"]
DIVS = ["E0", "SP1"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--insecure", action="store_true")
    args = ap.parse_args()
    ctx = ssl.create_default_context()
    if args.insecure:
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    OUT.mkdir(parents=True, exist_ok=True)
    for s in SEASONS:
        for d in DIVS:
            url = f"https://www.football-data.co.uk/mmz4281/{s}/{d}.csv"
            data = urllib.request.urlopen(url, timeout=60, context=ctx).read()
            (OUT / f"{d}_{s}.csv").write_bytes(data)
            print(f"ok {d}_{s}.csv {len(data)} bytes")


if __name__ == "__main__":
    main()
