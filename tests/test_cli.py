import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nftbackup.cli import (EvmChain, TezosChain, pick_media, resolve_url,
                           run_backup, run_verify, validate_address)

FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"fake-image-bytes"
FAKE_MP4 = b"\x00\x00\x00\x18ftypmp42" + b"fake-video-bytes"


class FakeHttp:
    """Stands in for the network. Routes are exact URL prefixes."""

    def __init__(self, json_routes=None, bytes_routes=None):
        self.json_routes = json_routes or {}
        self.bytes_routes = bytes_routes or {}

    def get_json(self, url):
        for prefix, payload in self.json_routes.items():
            if url.startswith(prefix):
                return payload
        raise RuntimeError(f"no json route for {url}")

    def get_bytes(self, url):
        for prefix, payload in self.bytes_routes.items():
            if url.startswith(prefix):
                return payload, "image/png" if payload is FAKE_PNG else "video/mp4"
        raise RuntimeError(f"404 for {url}")


def test_validate_addresses():
    assert validate_address("evm", "0x" + "a" * 40)
    assert validate_address("tezos", "tz1" + "a" * 33)
    with pytest.raises(SystemExit):
        validate_address("evm", "0x123")
    with pytest.raises(SystemExit):
        validate_address("tezos", "tz1short")


def test_private_key_is_refused():
    with pytest.raises(SystemExit):
        validate_address("evm", "b" * 64)
    with pytest.raises(SystemExit):
        validate_address("evm", "0x" + "b" * 64)


def test_pick_media_flavors():
    evm = {"image": "ipfs://QmX/1.png", "animation_url": "https://cdn.example/1.mp4"}
    kinds = {(m["kind"], m["url"]) for m in pick_media(evm)}
    assert ("image", "ipfs://QmX/1.png") in kinds
    assert ("animation", "https://cdn.example/1.mp4") in kinds
    tez = {"artifactUri": "ipfs://QmY/2.mp4", "displayUri": "ipfs://QmY/2.png",
           "thumbnailUri": "ipfs://QmY/thumb.png"}
    urls = {m["url"] for m in pick_media(tez)}
    assert "ipfs://QmY/2.mp4" in urls and "ipfs://QmY/2.png" in urls
    assert "ipfs://QmY/thumb.png" not in urls  # thumbnails are not originals


def test_resolve_ipfs_gateways():
    urls = resolve_url("ipfs://QmX/1.png", ["https://gw1.example", "https://gw2.example"])
    assert urls == ["https://gw1.example/ipfs/QmX/1.png",
                    "https://gw2.example/ipfs/QmX/1.png"]
    assert resolve_url("ar://abc123", []) == ["https://arweave.net/abc123"]


def _tezos_fake():
    payload = [
        {"token": {"contract": {"address": "KT1FakeContract00000000000000000"},
                   "tokenId": "7",
                   "metadata": {"name": "Fake Token Seven",
                                "displayUri": "ipfs://QmFake/img.png",
                                "artifactUri": "ipfs://QmFake/clip.mp4"}}},
        {"token": {"contract": {"address": "KT1FakeContract00000000000000000"},
                   "tokenId": "8",
                   "metadata": {"name": "Broken Token",
                                "displayUri": "ipfs://QmGone/none.png"}}},
    ]
    http = FakeHttp(
        json_routes={"https://api.tzkt.io/v1/tokens/balances": payload},
        bytes_routes={"https://ipfs.io/ipfs/QmFake/img.png": FAKE_PNG,
                      "https://ipfs.io/ipfs/QmFake/clip.mp4": FAKE_MP4})
    return TezosChain(http), http


def test_backup_and_verify(tmp_path):
    chain, http = _tezos_fake()
    address = "tz1" + "a" * 33
    summary = run_backup(chain, address, tmp_path,
                         http, ["https://ipfs.io"])
    assert summary["tokens"] == 2
    assert summary["media_ok"] == 2
    assert summary["media_failed"] == 1  # the gone image fails on every gateway

    folder = tmp_path / "tezos" / address
    inv = (folder / "inventory.csv").read_text()
    assert hashlib.sha256(FAKE_PNG).hexdigest() in inv
    assert (folder / "REPORT.md").exists()
    assert len(list((folder / "metadata").glob("*.json"))) == 2

    result = run_verify(folder)
    assert result["verified"] is True
    assert result["checked"] == 2

    # Corrupt a file and verify must catch it.
    victim = next((folder / "media").glob("*.png"))
    victim.write_bytes(b"tampered")
    result = run_verify(folder)
    assert result["verified"] is False
    assert result["hash_mismatch"] == 1


def test_plan_downloads_nothing(tmp_path):
    chain, http = _tezos_fake()
    address = "tz1" + "a" * 33
    summary = run_backup(chain, address, tmp_path, http,
                         ["https://ipfs.io"], plan_only=True)
    assert summary["tokens"] == 2
    assert not (tmp_path / "tezos").exists()


def test_evm_normalization():
    payload = {"ownedNfts": [
        {"contract": {"address": "0x" + "c" * 40},
         "id": {"tokenId": "0x2a"},
         "metadata": {"name": "Fake Forty Two",
                      "image": "ipfs://QmZ/42.png"}}]}
    http = FakeHttp(json_routes={"https://indexer.example/getNFTsForOwner": payload})
    chain = EvmChain(http, base="https://indexer.example")
    tokens = chain.list_tokens("0x" + "a" * 40)
    assert tokens[0]["token_id"] == "42"  # hex id decoded
    assert tokens[0]["media"][0]["url"] == "ipfs://QmZ/42.png"
