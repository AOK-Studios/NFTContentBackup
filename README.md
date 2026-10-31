# NFT Content Backup

NFT Content Backup lets you back up your NFT metadata across any chain. You just provide the wallet address of the wallet you want to back up. You do not have to connect your wallet directly. It backs up the IPFS files too, the images and the video, so you have an archive even if the blockchain dies.

CLI only. Built for agents. Read only, start to finish.

## The safety rule, up front

This tool takes a public wallet address and nothing else.

- No wallet connection. No signing. No seed phrase. No private key.
- If you paste something that looks like a private key as the address, the tool refuses and tells you to stop.
- Anything that ever asks for your key or your seed phrase is not this tool. Close it.

## What you need

- Python 3.10 or newer. No packages to install for the tool itself.
- For Tezos: nothing else. It reads the public TzKT API, no key.
- For Ethereum, Base, Polygon, Abstract: an NFT indexer account of your own. The tool reads an indexer API in the Alchemy getNFTsForOwner shape. Give it your indexer URL with `--indexer-url`, or set the `NFTB_INDEXER_URL` environment variable. The tool ships with no keys and no shared endpoints, on purpose. Your indexer account is yours, your quota is yours.

## Install

```
pip install .
```

## Use it

See what a backup would pull, without downloading anything:

```
nftbackup plan --chain tezos --address tz1YourPublicAddressHere0000000
```

Run the backup:

```
nftbackup backup --chain tezos --address tz1YourPublicAddressHere0000000 --out ./backups
```

EVM chains work the same way, with your indexer:

```
nftbackup backup --chain ethereum --address 0xYourPublicAddressHere000000000000000 --indexer-url "https://your-indexer.example/v2/YOUR_KEY"
```

Check an archive later. This re-hashes every file against the inventory:

```
nftbackup verify --dir ./backups/tezos/tz1YourPublicAddressHere0000000
```

## What lands on disk

```
backups/<chain>/<address>/
  metadata/     one JSON per token, the raw metadata as the chain served it
  media/        the original image and video files, bytes untouched
  inventory.csv every item: source URL, local file, size, SHA-256, status
  REPORT.md     counts, any failures with their URLs, and how to verify
```

## How the media download works

- `ipfs://` links are fetched through public gateways, in order, with fallback when one is down: ipfs.io, dweb.link, Cloudflare, Pinata. Add your own with `--gateway`, repeatable.
- `ar://` links resolve through arweave.net.
- Plain https links download as-is.
- A file that fails on every gateway is written into the report as a failure with its URL, never silently skipped. Rerun later and get it then.

## Why bother

Metadata lives on chain or on a server. The picture usually lives on IPFS, behind a gateway that can be slow, rate limited, or gone. When the gateway dies, marketplaces show a broken box where your art was. This archive keeps the metadata and the original media together, hashed, on your own disk.

## License

MIT. See LICENSE.
