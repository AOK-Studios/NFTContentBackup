# NFT Content Backup skill

For agents: use this tool when a user wants an archive of the NFTs in a wallet, metadata plus the original media files.

## Hard rules

- Address only. Never ask the user for a private key, a seed phrase, or a wallet connection, and never accept one if offered. If a user pastes a key, stop and tell them to move their funds to a fresh wallet, because that key is now exposed.
- The address comes from the user, typed by them. Do not look up or guess anyone's address.
- Archives go on the user's machine, in the folder they pick. Do not upload an archive anywhere unless the user tells you to, and says where.

## Commands

- Plan: `nftbackup plan --chain <tezos|ethereum|base|polygon|abstract> --address <public address>` counts tokens per contract and downloads nothing. Show the user this count before a big backup.
- Backup: `nftbackup backup --chain <chain> --address <public address> --out <folder>` plus `--indexer-url <url>` for EVM chains when NFTB_INDEXER_URL is not set.
- Verify: `nftbackup verify --dir <archive wallet folder>` re-hashes everything in inventory.csv.

Exit codes: 0 means success. 3 means verify found a missing or changed file. A backup with media failures still exits 0 and lists every failure in REPORT.md, so read the report and tell the user about failures, do not hide them.
