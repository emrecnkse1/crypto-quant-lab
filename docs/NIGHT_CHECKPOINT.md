# Gece Çalışması Checkpoint'i (2026-09-23/24)

Kesinti sonrası devam için tek kaynak. Tarihsel bir kayıttır; güncel durum için `ROADMAP.md` ve `FUNDING_RESEARCH_SPEC.md` §17'ye bakın.

## Başlangıç

- Branch `master`, başlangıç HEAD `1ef1d30` (origin ile 0/0).
- Başlangıç kalite durumu: `pytest -q` 2439 passed; `ruff check` temiz; `ruff format --check` 139 dosya temiz; `git diff --check` temiz.
- `AGENTS.md` takip dışı, kullanıcıya ait; okunur, değiştirilmez, stage edilmez.

## Paket durumu

| Paket | Durum | Kanıt |
|---|---|---|
| A — Faz 7 regression denetimi | Tamamlandı | `tests/test_faz7_regression_audit.py` (20 test); production bug bulunmadı; commit `4dc0487` |
| E — "Neden işlem yok?" teşhisi | Tamamlandı | `research/diagnostics.py`; policy aynı `decide_funding_carry` kuralını kullanır (eşdeğerlik testi) |
| C — Rapor/manifest sözleşmesi | Tamamlandı | `research/report.py` |
| B — CLI komutları | Tamamlandı | `python -m crypto_quant_lab.research ...` (`research/cli.py`) |
| D — Offline fixture | Tamamlandı | `research/offline_fixture.py`; beklenen değerler elle türetildi |
| F — Doctor | Tamamlandı | `doctor` alt komutu, salt okunur |
| G — Dokümantasyon | Bekliyor | runbook + ROADMAP uzlaştırması |
| Public smoke (opt-in) | Bekliyor | en fazla BTCUSDT + ETHUSDT, 1 çalıştırma |

## Testler (gerçek sonuçlar)

- Paket A sonrası: hedefli 20 passed.
- B–F sonrası: `tests/test_research_cli.py` 24 passed; tam suite 2483 passed; ruff/format temiz.
- Offline smoke (CLI): `succeeded`, iki bağımsız çalıştırmada aynı `deterministic_sha256`
  (`3f9fd721…5d57`); aynı çıktı dizinine ikinci çalıştırma reddedildi (exit 2).

## Kritik karar gerektiren bulgular

1. Motorun muhasebe/maliyet aritmetiği process-global Decimal context'ini kullanır (COST_MODEL_SPEC.md "konfigüre edilmiş Decimal context"). Basis katmanı ve metrikler özel context kullanır. CLI context'i değiştirmez ve raporun `run_metadata.decimal_context` alanına yazar. Motoru özel context'e bağlamak kilitli sözleşme değişikliğidir — yapılmadı.

## Sonraki güvenli adım

`python -m crypto_quant_lab.research offline-smoke --output <yeni-dizin>` ile doğrula, sonra Paket G'ye devam et.

## Artifact konumları

- Offline smoke denemeleri ve gece çıktıları: oturum scratchpad'i `night/` (repo dışı, commit edilmez).
- Önceki basis smoke (§15.9): scratchpad `smoke3/` (config, script, hash'ler, `out/` DB'leri ve rapor) — mevcut.
