# ContractIQ vendor provenance — Proposal Studio Exchange Contract V1

Jason explicitly authorized the recovered bundle at
`/home/bowen/dev/projects/proposal-studio-exchange-contract-v1` as the authoritative frozen V1
source for OPS-09. The original Proposal Studio Git repository and source commit are unavailable;
no commit provenance is claimed. The normative and example files in this directory are byte-for-byte
copies made on 2026-09-10. The incomplete upstream reference test is retained under `reference/` as
non-executable evidence and is outside ContractIQ's collected test tree.

| File | Raw SHA-256 |
|---|---|
| `proposal-package-v1.schema.json` | `3416365b51abedf3dcc7f17996ecb08467997b660679ae890c5624761d472462` |
| `proposal-generation-manifest-v1.schema.json` | `da5c24e75371498109ba28d81d8840753a30c28bbfb9554fc2a5f288649ef408` |
| `examples/proposal-package-v1.example.json` | `8943882932419d3c8ffc03e1b1552433d5c6b807d5fa69dea039275ac245c3fc` |
| `examples/proposal-generation-manifest-v1.example.json` | `ef30b682e058039b8eab9d8e98c564785b657bc5dc1b518635300e8868304c80` |
| `CONTRACTIQ_EXCHANGE_CONTRACT_V1.md` | `864e578a33e8fd17c6f753bb92d6333c9d83ae82728d10746221d74dea27ea30` |
| `reference/test_exchange_contract_v1.py` | `5e16ba335763de0cd03a76546467a597310aaa3180d217ec4568053257bdbec0` |

The canonical V1 SHA-256 of the supplied example package is
`f087af73dc3306d0ca174ffaa6df464f5c6102a86929926a038797e9b71b310b`.

The reference test names five negative fixtures that were not supplied. ContractIQ does not
reconstruct them or claim that the upstream suite ran; independent adapter tests provide equivalent
negative coverage.
