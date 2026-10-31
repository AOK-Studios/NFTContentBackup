"""NFT Content Backup: archive NFT metadata and media from a wallet address.

Address in, archive out. The tool reads public chain data for the address
you give it. It never connects a wallet, never asks for a private key or a
seed phrase, and never signs anything. If anything ever asks you for those,
it is not this tool. Close it.

What lands on disk, per wallet:

    <out>/<chain>/<address>/
        metadata/   one JSON file per token, the raw metadata as served
        media/      the original image and video files, bytes untouched
        inventory.csv   every item, its source URL, size and SHA-256
        REPORT.md   counts, failures, and how to verify the archive

Chains:
  tezos     Uses the public TzKT API. No key needed.
  ethereum  Uses an NFT indexer API in the Alchemy getNFTsForOwner shape.
            Point --indexer-url (or the NFTB_INDEXER_URL env var) at your
            own indexer account. The tool ships with no keys and no shared
            endpoints, on purpose.

Media on IPFS is fetched through public gateways, with fallback. ar://
links resolve through arweave.net. Plain https links download as-is.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

DEFAULT_GATEWAYS = [
    "https://ipfs.io",
    "https://dweb.link",
    "https://cloudflare-ipfs.com",
    "https://gateway.pinata.cloud",
]

MEDIA_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".avif",
              ".mp4", ".webm", ".mov", ".mp3", ".wav", ".glb", ".gltf", ".html"}

CTYPE_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif",
             "image/webp": ".webp", "image/svg+xml": ".svg",
             "video/mp4": ".mp4", "video/webm": ".webm",
             "audio/mpeg": ".mp3", "model/gltf-binary": ".glb"}


# ---------------------------------------------------------------- http

class Http:
    """Small HTTP client. Swap it out in tests, or subclass it to add keys."""

    def __init__(self, timeout: int = 60):
        self.timeout = timeout

    def _open(self, url: str):
        req = urllib.request.Request(url, headers={"User-Agent": "nft-content-backup/0.1"})
        return urllib.request.urlopen(req, timeout=self.timeout)

    def get_json(self, url: str):
        with self._open(url) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def get_bytes(self, url: str) -> tuple[bytes, str]:
        with self._open(url) as resp:
            return resp.read(), resp.headers.get_content_type()


# ---------------------------------------------------------------- safety

EVM_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
TEZOS_RE = re.compile(r"^(tz1|tz2|tz3|KT1)[a-zA-Z0-9]{33}$")
KEY_LIKE_RE = re.compile(r"^(0x)?[a-fA-F0-9]{64}$")


def validate_address(chain: str, address: str) -> str:
    address = address.strip()
    if KEY_LIKE_RE.match(address) and not EVM_RE.match(address):
        raise SystemExit(
            "That looks like a private key, not an address. Stop. "
            "This tool only ever takes a public wallet address.")
    if chain == "tezos":
        if not TEZOS_RE.match(address):
            raise SystemExit(f"not a Tezos address: {address}")
    else:
        if not EVM_RE.match(address):
            raise SystemExit(f"not an EVM address: {address}")
    return address


# ---------------------------------------------------------------- chains

def pick_media(metadata: dict) -> list[dict]:
    """Pull the original media links out of a metadata blob, any flavor."""
    found = []
    fields = [("image", "image"), ("image_url", "image"),
              ("animation_url", "animation"), ("artifactUri", "animation"),
              ("displayUri", "image")]
    seen = set()
    for key, kind in fields:
        url = metadata.get(key)
        if isinstance(url, str) and url and url not in seen:
            seen.add(url)
            found.append({"kind": kind, "url": url})
    return found


class TezosChain:
    name = "tezos"

    def __init__(self, http: Http, base: str = "https://api.tzkt.io"):
        self.http = http
        self.base = base.rstrip("/")

    def list_tokens(self, address: str) -> list[dict]:
        tokens = []
        offset = 0
        while True:
            url = (f"{self.base}/v1/tokens/balances?account={address}"
                   f"&balance.gt=0&limit=1000&offset={offset}")
            page = self.http.get_json(url)
            if not page:
                break
            for row in page:
                token = row.get("token", {})
                contract = (token.get("contract") or {}).get("address", "unknown")
                metadata = token.get("metadata") or {}
                tokens.append({
                    "chain": self.name,
                    "contract": contract,
                    "token_id": str(token.get("tokenId", "0")),
                    "name": metadata.get("name", ""),
                    "metadata": metadata,
                    "media": pick_media(metadata),
                })
            if len(page) < 1000:
                break
            offset += len(page)
        return tokens


class EvmChain:
    """Alchemy-shaped indexer: GET {base}/getNFTsForOwner?owner=... """

    def __init__(self, http: Http, base: str, chain: str = "ethereum"):
        self.http = http
        self.base = base.rstrip("/")
        self.name = chain

    def list_tokens(self, address: str) -> list[dict]:
        tokens = []
        page_key = None
        while True:
            url = f"{self.base}/getNFTsForOwner?owner={address}&withMetadata=true"
            if page_key:
                url += f"&pageKey={page_key}"
            data = self.http.get_json(url)
            for nft in data.get("ownedNfts", []):
                contract = (nft.get("contract") or {}).get("address", "unknown")
                raw_id = ((nft.get("id") or {}).get("tokenId") or "0")
                try:
                    token_id = str(int(raw_id, 16)) if str(raw_id).startswith("0x") else str(raw_id)
                except ValueError:
                    token_id = str(raw_id)
                metadata = nft.get("metadata") or nft.get("rawMetadata") or {}
                tokens.append({
                    "chain": self.name,
                    "contract": contract,
                    "token_id": token_id,
                    "name": metadata.get("name", "") or nft.get("title", ""),
                    "metadata": metadata,
                    "media": pick_media(metadata),
                })
            page_key = data.get("pageKey")
            if not page_key:
                break
        return tokens


def get_chain(args, http: Http):
    if args.chain == "tezos":
        return TezosChain(http, base=args.tzkt_url)
    base = args.indexer_url or os.environ.get("NFTB_INDEXER_URL", "")
    if not base:
        raise SystemExit(
            "EVM chains need an indexer URL. Pass --indexer-url with your own "
            "indexer account URL (Alchemy getNFTsForOwner shape), or set "
            "NFTB_INDEXER_URL. This tool ships with no keys, on purpose.")
    return EvmChain(http, base=base, chain=args.chain)


# ---------------------------------------------------------------- media

def resolve_url(url: str, gateways: list[str]) -> list[str]:
    """Turn a metadata URL into a list of URLs to try, in order."""
    if url.startswith("ipfs://"):
        path = url[len("ipfs://"):]
        if path.startswith("ipfs/"):
            path = path[len("ipfs/"):]
        return [f"{gw}/ipfs/{path}" for gw in gateways]
    if url.startswith("ar://"):
        return [f"https://arweave.net/{url[len('ar://'):]}"]
    return [url]


def fetch_media(url: str, http: Http, gateways: list[str]) -> tuple[bytes, str, str]:
    """Returns (bytes, extension, url that worked)."""
    if url.startswith("data:"):
        header, _, payload = url.partition(",")
        if ";base64" in header:
            return base64.b64decode(payload), CTYPE_EXT.get(header[5:].split(";")[0], ".bin"), url
        return payload.encode(), ".txt", url
    last_error = None
    for candidate in resolve_url(url, gateways):
        try:
            data, ctype = http.get_bytes(candidate)
            suffix = Path(candidate.split("?")[0]).suffix.lower()
            ext = suffix if suffix in MEDIA_EXTS else CTYPE_EXT.get(ctype, ".bin")
            return data, ext, candidate
        except Exception as exc:  # gateway down, 404, timeout: try the next one
            last_error = exc
    raise RuntimeError(f"all gateways failed for {url}: {last_error}")


# ---------------------------------------------------------------- backup

def safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(text))[:80]


def run_backup(chain, address: str, out_root: Path, http: Http,
               gateways: list[str], plan_only: bool = False) -> dict:
    tokens = chain.list_tokens(address)
    dest = out_root / chain.name / address
    summary = {"chain": chain.name, "address": address, "tokens": len(tokens),
               "media_ok": 0, "media_failed": 0, "failures": []}
    if plan_only:
        contracts = {}
        for t in tokens:
            contracts[t["contract"]] = contracts.get(t["contract"], 0) + 1
        summary["contracts"] = contracts
        return summary

    meta_dir = dest / "metadata"
    media_dir = dest / "media"
    meta_dir.mkdir(parents=True, exist_ok=True)
    media_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for t in tokens:
        stem = f"{safe_name(t['contract'])}_{safe_name(t['token_id'])}"
        envelope = {"chain": t["chain"], "contract": t["contract"],
                    "token_id": t["token_id"], "name": t["name"],
                    "metadata": t["metadata"]}
        (meta_dir / f"{stem}.json").write_text(json.dumps(envelope, indent=2))
        for item in t["media"]:
            row = {"chain": t["chain"], "wallet": address,
                   "contract": t["contract"], "token_id": t["token_id"],
                   "name": t["name"], "media_kind": item["kind"],
                   "source_url": item["url"], "file": "", "bytes": 0,
                   "sha256": "", "status": ""}
            try:
                data, ext, _used = fetch_media(item["url"], http, gateways)
                fname = f"{stem}_{item['kind']}{ext}"
                (media_dir / fname).write_bytes(data)
                row.update({"file": f"media/{fname}", "bytes": len(data),
                            "sha256": hashlib.sha256(data).hexdigest(),
                            "status": "ok"})
                summary["media_ok"] += 1
            except Exception as exc:
                row["status"] = f"failed: {exc}"
                summary["media_failed"] += 1
                summary["failures"].append(
                    {"token": stem, "url": item["url"], "error": str(exc)})
            rows.append(row)
    with open(dest / "inventory.csv", "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else
                                ["chain", "wallet", "contract", "token_id", "name",
                                 "media_kind", "source_url", "file", "bytes", "sha256", "status"])
        writer.writeheader()
        writer.writerows(rows)
    report = [f"# Backup report", "",
              f"Chain: {summary['chain']}",
              f"Wallet: {address}",
              f"Tokens: {summary['tokens']}",
              f"Media saved: {summary['media_ok']}",
              f"Media failed: {summary['media_failed']}", ""]
    if summary["failures"]:
        report.append("## Failures")
        for f in summary["failures"]:
            report.append(f"- {f['token']} {f['url']} ({f['error']})")
        report.append("")
    report += ["## Verify this archive",
               "Run: `nftbackup verify --dir <this folder>`",
               "It re-hashes every file in inventory.csv and reports any mismatch.", ""]
    (dest / "REPORT.md").write_text("\n".join(report))
    summary["folder"] = str(dest)
    return summary


def run_verify(folder: Path) -> dict:
    inv = folder / "inventory.csv"
    if not inv.exists():
        raise SystemExit(f"no inventory.csv in {folder}")
    checked = bad = missing = 0
    problems = []
    with open(inv) as fh:
        for row in csv.DictReader(fh):
            if row["status"] != "ok" or not row["file"]:
                continue
            checked += 1
            path = folder / row["file"]
            if not path.exists():
                missing += 1
                problems.append(f"missing: {row['file']}")
                continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != row["sha256"]:
                bad += 1
                problems.append(f"hash mismatch: {row['file']}")
    return {"folder": str(folder), "checked": checked, "ok": checked - bad - missing,
            "hash_mismatch": bad, "missing": missing, "problems": problems,
            "verified": bad == 0 and missing == 0}


# ---------------------------------------------------------------- cli

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nftbackup", description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)

    def chain_args(p):
        p.add_argument("--chain", required=True,
                       choices=["tezos", "ethereum", "base", "polygon", "abstract"])
        p.add_argument("--address", required=True,
                       help="public wallet address. Never a private key.")
        p.add_argument("--tzkt-url", default="https://api.tzkt.io")
        p.add_argument("--indexer-url", default=None,
                       help="your own EVM indexer URL (or set NFTB_INDEXER_URL)")
        p.add_argument("--gateway", action="append", default=None,
                       help="extra IPFS gateway, repeatable")

    p_plan = sub.add_parser("plan", help="count what a backup would pull, download nothing")
    chain_args(p_plan)
    p_back = sub.add_parser("backup", help="download metadata and media into an archive")
    chain_args(p_back)
    p_back.add_argument("--out", default="nft-backup")
    p_ver = sub.add_parser("verify", help="re-hash an archive against its inventory.csv")
    p_ver.add_argument("--dir", required=True)

    args = parser.parse_args(argv)
    http = Http()
    gateways = (args.gateway or []) + DEFAULT_GATEWAYS if hasattr(args, "gateway") else DEFAULT_GATEWAYS

    if args.mode == "verify":
        result = run_verify(Path(args.dir))
        print(json.dumps(result, indent=2))
        return 0 if result["verified"] else 3

    address = validate_address("tezos" if args.chain == "tezos" else "evm", args.address)
    chain = get_chain(args, http)
    if args.mode == "plan":
        result = run_backup(chain, address, Path("."), http, gateways, plan_only=True)
    else:
        result = run_backup(chain, address, Path(args.out), http, gateways)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
