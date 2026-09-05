# Changelog

## 0.3.0 - 2026-09-05

- Add passive I3C Basic SDR decoding with CCC, DAA, parity/T-bit validation,
  legacy-I2C mode, and explicit ambiguity/HDR diagnostics.
- Add passive AVSBus controller/target decoding with CRC-3 and resynchronization.
- Add native Sigrok `.sr` ingestion and KingstVIS 3.6.x saved SPI settings.
- Add protocol plugin API v1, event/report schema 1.0, registry-derived runtime
  capabilities, and alternative-role channel binding.
- Add provenance-tracked public-domain and generated waveform regressions.
- Add Python 3.11-3.13 CI and portable process-level MCP smoke coverage.
