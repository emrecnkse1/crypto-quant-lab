# Gece Çalışması Checkpoint'i (2026-09-23/24)

Kesinti sonrası devam için tek kaynak. Tarihsel bir kayıttır; güncel durum için `ROADMAP.md` ve `FUNDING_RESEARCH_SPEC.md` §17.8'e bakın.

## Başlangıç

- Branch `master`, başlangıç HEAD `1ef1d30` (origin ile 0/0).
- Başlangıç kalite durumu: `pytest -q` 2439 passed; `ruff check` temiz; `ruff format --check` 139 dosya temiz; `git diff --check` temiz.
- `AGENTS.md` takip dışı, kullanıcıya ait; okunur, değiştirilmez, stage edilmez.

## Başlangıç sınıflandırması

| Sınıf | İçerik |
|---|---|
| Zaten var ve yeterli | funding/contract/index ingestion, provenance + coverage, close basis, resmî basis adaptörü, rolling runner, Stage-1/2 metrikler |
| Var ama tamamlanmalı | edge-case regression kapsamı (Paket A); policy kararının gerekçesi dışarıdan görünmüyordu (Paket E) |
| Gerçekten yeni | CLI, rapor/manifest sözleşmesi, offline fixture, doctor, runbook (repoda CLI/rapor formatı yoktu) |
| Çakışan/uyumsuz | FUNDING_RESEARCH_SPEC.md §1 "risk profili … roadmap'teki yerlerinde korunur" diyordu, ROADMAP'te karşılığı yoktu → ROADMAP'e "henüz uygulanmadı" bölümü eklendi |
| Kritik karar nedeniyle ertelenen | motorun Decimal context'e bağımlılığı (aşağıda); çok bacaklı muhasebe (§16) |

## Paket durumu

| Paket | Durum | Kanıt |
|---|---|---|
| A — Faz 7 regression denetimi | Tamamlandı | `tests/test_faz7_regression_audit.py` (20 test); production bug bulunmadı; commit `4dc0487` |
| E — "Neden işlem yok?" teşhisi | Tamamlandı | `research/diagnostics.py`; policy tek `decide_funding_carry` kuralını kullanır; commit `a4c9326` |
| C — Rapor/manifest | Tamamlandı | `research/report.py`; commit `a4c9326` |
| B — CLI | Tamamlandı | `python -m crypto_quant_lab.research ...`; commit `a4c9326` |
| D — Offline fixture | Tamamlandı | `research/offline_fixture.py`; commit `a4c9326` |
| F — Doctor | Tamamlandı | `doctor` alt komutu; commit `a4c9326` |
| G — Dokümantasyon | Tamamlandı | `docs/RESEARCH_RUNBOOK.md`, FUNDING_RESEARCH_SPEC §17, ROADMAP |
| Public smoke (opt-in) | Tek çalıştırma yapıldı | BTCUSDT + ETHUSDT; rapor `failed` (tolerans aşımları), hata yok — §17.7 |

## Testler (gerçek sonuçlar)

- Paket A: 20 passed (hedefli).
- B–F: `tests/test_research_cli.py` 24 passed; tam suite 2483 passed; ruff/format temiz.
- Offline smoke (CLI): `succeeded`; iki bağımsız çalıştırmada aynı `deterministic_sha256` (`3f9fd721…5d57`); aynı çıktı dizinine ikinci çalıştırma reddedildi (exit 2); fixture DB'leri baytça değişmedi.
- Runbook komutları PowerShell'de tek tek çalıştırıldı (doctor, inspect, basis-report, funding-research, offline-smoke; public-smoke'un `--allow-network` olmadan exit 2 vermesi).
- Public smoke 2026-09-23T22:54Z: BTCUSDT 4/167, ETHUSDT 7/167 tolerans aşımı; resmî kayıtlar 168/168 cebirsel tutarlı.

## Kritik karar gerektiren bulgular

1. Motorun muhasebe/maliyet aritmetiği process-global Decimal context'ini kullanır (COST_MODEL_SPEC.md "konfigüre edilmiş Decimal context"). Basis katmanı ve metrikler özel context kullanır. CLI context'i değiştirmez ve raporun `run_metadata.decimal_context` alanına yazar. Motoru özel context'e bağlamak kilitli sözleşme değişikliğidir — yapılmadı.
2. `public-smoke` rapor durumu, önceden sabitlenmiş 1 bp tolerans aşıldığında `failed` olur. Bu, snapshot/kapanış semantiği nedeniyle yapısal olarak beklenebilir; toleransın veya bu kontrolün rolünün (kapı mı, betimsel mi) değiştirilmesi kullanıcı kararıdır.
3. ETHUSDT'nin 7 tolerans aşımı teşhis edilmedi (BTC için §15.9 teşhisi vardı).

## Sonraki güvenli adım

`.\.venv\Scripts\python.exe -m crypto_quant_lab.research offline-smoke --output "$env:TEMP\cql\offline-smoke-N"` ile doğrula. Bir sonraki mühendislik işi FUNDING_RESEARCH_SPEC.md §16'dır (çok bacaklı muhasebe/execution sözleşmesi) ve kullanıcı onayı gerektirir.

## Artifact konumları

- Gece çıktıları (offline smoke denemeleri, public smoke paketi `public_smoke/`): oturum scratchpad'i `night/` — repo dışı, commit edilmez.
- Önceki basis smoke (§15.9): scratchpad `smoke3/` (config, script, hash'ler, `out/` DB'leri ve rapor) — mevcut.
- Commit edilmemiş iş: yok (final commit sonrası).

---

## Devam oturumu (2026-09-24) — güvenilirlik düzeltmeleri + çok bacaklı taslak

Yukarıdaki gece kaydı tarihseldir ve değiştirilmedi. Bu bölüm sonraki oturumu kaydeder.

- Başlangıç: HEAD `8f657a0`, origin ile 0/0; `pytest -q` 2483 passed; ruff/format temiz (148 dosya).
- Gece "kritik karar" maddeleri bu oturumda ele alındı:
  1. Decimal context → araştırma sınırında açık run girdisi yapıldı (FUNDING_RESEARCH_SPEC §18.1); motor/COST_MODEL_SPEC değişmedi.
  2. Public smoke tolerans kontrolü → kullanıcı kararıyla betimsel **warning** (rapor v2, §18.3); tolerans 1 bp aynen.
  3. ETH 7 aşım → post-hoc teşhis edildi (§18.4): fark index kapanış→açılış sıçramasından; mekanizma belgelenmemiş.
- Kod commit'i `644f66b` (push edildi): `decimal_policy.py`, `storage/sqlite_readonly.py`, store'lara additive `open_read_only`/`read_snapshot`, `public_smoke.py`, rapor v2; testler `test_research_reliability.py` (19) ve `test_public_smoke.py` (11); tam suite 2513 passed.
- Dokümanlar: FUNDING_RESEARCH_SPEC §18 (düzeltmeler + ETH teşhisi) ve §19 (çok bacaklı muhasebe/execution TASLAĞI — DRAFT, implementasyon yok), ROADMAP, runbook (v2 durum sözleşmesi, `decimal_context`, salt okunur erişim, yeni hata satırları).
- Runbook komutları PowerShell'de yeniden çalıştırıldı: offline-smoke, doctor, inspect, basis-report, funding-research exit 0; public-smoke opt-in olmadan exit 2; girdi dosyaları (ad, boyut, mtime) değişmedi; rapor şeması v2.
- ETH teşhisi için tek sınırlı read-only istek yapıldı (resmî basis, ETHUSDT, 2026-09-24T12:49Z); başka ağ çağrısı yok. Teşhis çıktısı: scratchpad `night/eth_diagnosis.json`.

### Açık kritik kararlar (kullanıcıda)

- FUNDING_RESEARCH_SPEC §19.11 K1–K7 (margin/likidasyon, hedge oranı, legging politikası, spot short/borrow, lot/tick yuvarlama, sonuç tipi, cüzdan transfer modeli).
- Index kapanış→açılış sıçramasının mekanizması belgelenmemiş; close basis mı open-snapshot basis mı araştırma feature'ı olarak tercih edileceği bir araştırma kararıdır (bu oturumda değiştirilmedi).

### Sonraki güvenli adım

`.\.venv\Scripts\python.exe -m crypto_quant_lab.research offline-smoke --output "$env:TEMP\cql\offline-smoke-N"`; ardından §19 kararları verilince §19.10'daki ilk dilim.
