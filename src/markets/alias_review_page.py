"""`python -m src.markets.alias_review_page [--open]`: a LOCAL review page (artifacts/alias_review.html) for the aliases the
assistant matched by itself (`fuzzy_approved`). Each club shows its logo next to every raw name linked to it, so a wrong
link is visible at a glance. Logos come from Highlightly's image host (identification use only, terms 6.3) and are
loaded by YOUR browser when you open the file; nothing is downloaded, stored or published by this tool.
Tick the wrong ones, type the right club, press the copy button and send the text back.
"""

import argparse
import base64
import json
import os
import subprocess
import webbrowser
from collections import defaultdict
from html import escape as e
from pathlib import Path

from src.config import config_dir_for
from src.data.teams import TeamDirectory
from src.data_repo import data_repo_url

from .stats_store import store_dir

ROOT = Path(__file__).resolve().parents[2]
LOGO = "https://highlightly.net/soccer/images/teams/{}.png"

CSS = """
body{font:15px/1.4 system-ui,sans-serif;margin:0 auto;max-width:980px;padding:16px;background:#fff;color:#1a1a1a}
@media(prefers-color-scheme:dark){body{background:#0d1117;color:#e6edf3}.club{background:#161b22;border-color:#30363d}}
.club{display:flex;gap:14px;border:1px solid #d9dce1;border-radius:8px;padding:10px 12px;margin:8px 0;background:#f6f7f9}
.logo{width:56px;height:56px;object-fit:contain;flex:none}.ph{width:56px;height:56px;flex:none;border:1px dashed #888;
border-radius:6px;display:flex;align-items:center;justify-content:center;font-size:11px;color:#888;text-align:center}
.club h3{margin:0 0 4px;font-size:16px}.alias{display:flex;gap:8px;align-items:center;font-size:14px;padding:1px 0}
.src{color:#888;font-size:12px;min-width:120px}input[type=text]{font:inherit;padding:1px 4px;width:200px}
#bar{position:sticky;top:0;background:inherit;padding:8px 0;border-bottom:1px solid #888}
button{font:inherit;padding:4px 10px;cursor:pointer}textarea{width:100%;height:120px;margin-top:6px}
"""

JS = """
function collect(){
  const out=[];
  document.querySelectorAll('.alias').forEach(a=>{
    const cb=a.querySelector('input[type=checkbox]');
    if(cb&&cb.checked){const fix=a.querySelector('input[type=text]').value.trim();
      out.push(a.dataset.src+' | '+a.dataset.raw+' | bagli oldugu kulup: '+a.dataset.club+' | dogrusu: '+(fix||'(yazmadim)'));}
  });
  document.getElementById('out').value=out.length?out.join('\\n'):'(hicbir sey isaretlenmedi: hepsi dogru)';
  document.getElementById('out').select();
}
function copyOut(){collect();document.execCommand('copy');}
"""


def sync_logos(root: Path) -> str:
    """Copy the logos the cloud helper put on the private data repo (branch review-assets) into artifacts/logos."""

    def git(*args: str) -> bytes:
        return subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, check=True, timeout=180
        ).stdout

    try:
        git("fetch", "--quiet", data_repo_url(), "review-assets")
        names = git("ls-tree", "-r", "--name-only", "FETCH_HEAD", "--", "logos").decode().split()
    except subprocess.CalledProcessError:
        return "no logos on the data repo yet (run the alias-logos workflow)"
    dest_dir = root / "artifacts" / "logos"
    dest_dir.mkdir(parents=True, exist_ok=True)
    for n in names:
        (dest_dir / Path(n).name).write_bytes(git("show", f"FETCH_HEAD:{n}"))
    return f"{len(names)} logos synced"


def hl_ids(root: Path, directory: TeamDirectory) -> dict[str, set[int]]:
    """canonical team id -> Highlightly team ids seen in the store (for the logos)."""
    out: dict[str, set[int]] = defaultdict(set)
    base = store_dir(root)
    for p in sorted(base.glob("*_*.jsonl")) if base.exists() else []:
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            country = {
                "EPL": "ENG",
                "LALIGA": "ESP",
                "BUNDESLIGA": "GER",
                "SERIEA": "ITA",
                "LIGUE1": "FRA",
                "SUPERLIG": "TUR",
            }[r["league"]]
            for side in ("home", "away"):
                res = directory.resolve("highlightly", r[side]["name"], country)
                if res.team_id:
                    out[res.team_id].add(r[side]["id"])
    return out


def build(root: Path, logo_dir: Path | None = None) -> str:
    d = TeamDirectory.load(config_dir_for(root) / "team_aliases.yaml")
    logos = hl_ids(root, d)
    by_club: dict[str, list] = defaultdict(list)
    for a in d.aliases:
        if a.provenance == "fuzzy_approved":
            by_club[a.team_id].append(a)
    countries = defaultdict(list)
    for tid in by_club:
        countries[d.teams[tid]["country"]].append(tid)
    parts = [
        "<h1>Alias review</h1>",
        f"<p>{sum(len(v) for v in by_club.values())} aliases of {len(by_club)} clubs were matched by the assistant. "
        "For each club check that every name listed under it really is that club (the logo is the Highlightly one). "
        "Tick the wrong ones and type the right club. Then press <b>Kopyala</b> and send me the text.</p>",
        "<div id='bar'><button onclick='copyOut()'>Kopyala</button> "
        "<button onclick='collect()'>Listeyi goster</button><textarea id='out' placeholder='isaretlediklerin burada cikar'></textarea></div>",
    ]
    for country in sorted(countries):
        parts.append(f"<h2>{e(country)}</h2>")
        for tid in sorted(countries[country], key=lambda t: d.teams[t]["canonical_name"]):
            name = d.teams[tid]["canonical_name"]
            ids = sorted(logos.get(tid, ()))
            local = next(
                (logo_dir / f"{i}.png" for i in ids if logo_dir and (logo_dir / f"{i}.png").exists()), None
            )
            if local is not None:  # embedded: works on a network that cannot reach the logo host
                uri = "data:image/png;base64," + base64.b64encode(local.read_bytes()).decode()
                img = f"<img class='logo' src='{uri}' alt=''>"
            else:
                img = None
            img = img or (
                f"<img class='logo' loading='lazy' referrerpolicy='no-referrer' src='{LOGO.format(ids[0])}' alt='' "
                f"onerror=\"this.outerHTML='<div class=ph>logo yuklenemedi</div>'\">"
                if ids
                else "<div class='ph'>logo yok</div>"
            )
            rows = "".join(
                f"<div class='alias' data-src='{e(a.source)}' data-raw='{e(a.raw_name)}' data-club='{e(name)}'>"
                f"<input type='checkbox' title='yanlis'> <span class='src'>{e(a.source)}</span> <b>{e(a.raw_name)}</b> "
                f"<input type='text' placeholder='dogru kulup (yanlissa)'></div>"
                for a in sorted(by_club[tid], key=lambda x: (x.source, x.raw_name))
            )
            parts.append(
                f"<div class='club'>{img}<div><h3>{e(name)} <small>({e(tid)})</small></h3>{rows}</div></div>"
            )
    return f"<!doctype html><html lang='tr'><head><meta charset='utf-8'><title>Alias review</title><style>{CSS}</style></head><body>{''.join(parts)}<script>{JS}</script></body></html>"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--open", action="store_true", help="open the page in your browser")
    a = ap.parse_args(argv)
    out = Path(a.root) / "artifacts" / "alias_review.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    root = Path(a.root)
    print(sync_logos(root))
    out.write_text(build(root, root / "artifacts" / "logos"), encoding="utf-8")
    print(f"written: {out}")
    if a.open and not os.environ.get("CI"):
        webbrowser.open(out.resolve().as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
