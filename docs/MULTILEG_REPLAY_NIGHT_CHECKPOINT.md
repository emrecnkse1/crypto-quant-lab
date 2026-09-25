# Çok Bacaklı Replay Gece Checkpoint'i (2026-09-25)

Kesinti sonrası devam için tek kaynak. Güncel durum için ayrıca `ROADMAP.md` ve `FUNDING_RESEARCH_SPEC.md` §19.12'ye bakın.

## Başlangıç

- Branch `master`, başlangıç HEAD `bf08a8b` (origin ile 0/0), yorumlayıcı `.venv/Scripts/python.exe`.
- Baseline: `pytest -q` 2535 passed; `ruff check` temiz; `ruff format --check` 155 dosya; `git diff --check` temiz.
- `AGENTS.md` takip dışı, kullanıcıya ait; SHA-256 `c88c11fd2e07361266e878e22749f6a042ca27b7d95f9315280eec9508e25b05`; değiştirilmez/stage edilmez.

## Başlangıç karşılaştırması

| Sınıf | İçerik |
|---|---|
| Var, korunur/yeniden kullanılır | `backtest/multileg.py` (§19.10.1), `replay.py` `_validate_dataset` ve `_validate_funding_events`, `feature_availability_time`, `LinearFundingModel`, `CostModel`, `decimal_policy`, `research/report.py` (`OutputBundle`, `build_report`) |
| Yeni ve yetkili | E1 (opt-in sıralama), `backtest/multileg_replay.py`, S/N acceptance, adversarial/oracle testleri, sentetik offline demo |
| Çakışan/belirsiz | strict-time kontrolü MS9 ile çakışıyordu → E1 opt-in ile çözüldü (varsayılan değişmedi) |
| Kapsam dışı | store-backed runner, ingestion, strateji, warmup, liquidation, legging, metrics adapter, FAZ6C/6D |

## Kararlar (yalnız ilk replay için, kullanıcı tarafından verildi)

R1 tam eşit open_time ızgarası · R2 MS9 sırası + E1 (opt-in) · R3 sonda açık pozisyon açık kalır, son trade CLOSE ile değerlenir · R4 FLAT funding sıfır kayıt, API çağrılmaz · R5 warmup yok. K1–K7 ilk dilim sınırları aynen.

## Paket durumu

| Paket | Durum |
|---|---|
| A — E1 + legacy uyumluluk | VERIFIED — 22 eski test değiştirilmeden geçti; `tests/test_backtest_multileg_ordering.py` 8 test; tam suite 2543 passed |
| B — in-memory replay | VERIFIED — `backtest/multileg_replay.py` (`run_multileg_replay`); eski motor bu modülü import etmez |
| C — S1–S12 + N1–N7 | VERIFIED — `tests/test_backtest_multileg_replay.py` 23 test fonksiyonu (N1–N7, S1–S12); tam suite 2566 passed |
| D — adversarial / oracle | VERIFIED — `tests/test_backtest_multileg_replay_adversarial.py` 20 test (seed 20260925, 40 rastgele senaryo bağımsız oracle ile birebir; oracle 3 bilinen kusuru ayırt eder); tam suite 2586 passed |
| E — offline demo | VERIFIED — `python -m crypto_quant_lab.research.multileg_offline --output <yeni dizin>`; 4 senaryo elle türetilmiş beklentilerle geçti; iki temiz çalıştırmada aynı `deterministic_sha256` ve `run_input_sha256`; var olan çıktı ezilmez (exit 2); `tests/test_research_multileg_offline.py` 4 test; PowerShell'de doğrulandı; tam suite 2590 passed |
| F — son denetim + docs | VERIFIED — ayrı denetim turu (harici reviewer yok): 2 sertleştirme (funding cursor tamlık denetimi, FLAT duplicate + yaşam döngüsü kayıt testi); ekonomik hata bulunmadı; spec §19.12.8, ROADMAP, runbook güncel; tam suite 2591 passed, ruff/format/diff temiz |

## Bilinen sınır

`dataclasses.replace` ile bir state'in `event_ordering` alanı elle değiştirilebilir (frozen dataclass'lar yeniden kurulabilir; ledger'lar da öyle). API modu yalnız `new_hedged_portfolio`'da belirler, geçişler modu korur; kayıtlı olay geçmişi `last_event_time` ile tutarsızsa sıra tahmin edilmez, reddedilir. Keyfi elle kurulmuş state'e karşı tam koruma iddia edilmez.

## Sonraki tek adım

Gece paketi tamamlandı. Sonraki kontrollü görev önerisi: store-backed çok bacaklı runner sözleşmesi (spot_trade + contract_trade store'larından provenance/coverage kontrollü okuma) — kullanıcı onayı gerekir.

## Doğrulanmış kod checkpoint'leri

`7d68102` (E1) · `146775a` (replay + N/S acceptance) · `755ae85` (adversarial/oracle) · `793561a` (offline demo) · son denetim + dokümanlar: bu dosyayı içeren commit (hash'i `git log` ile doğrulanır).

## Çalışma ağacı ayrımı

Bana ait değişiklikler commit edildi; kullanıcıya ait takip dışı `AGENTS.md` dokunulmadan duruyor (hash başlangıçtakiyle aynı).

---

## Store-backed dilim oturumu (2026-09-25, devam)

Başlangıç: HEAD `7fa2775` (origin 0/0), `pytest -q` 2591 passed, ruff/format temiz (162 dosya), `AGENTS.md` hash değişmedi.

Doğrulanan bulgu: `data_quality/ingestion.py::ingest_binance_historical_range` spot mumlarını yalnız `store.write_batch` ile yazar; `candle_datasets` provenance'ı ve `candle_coverage` yazmaz, boş yanıtta hiçbir şey yazmaz (eksik olan metadata yazımı + coverage kanıtı; okuyucu tarafı `query_dataset`/`query_coverage` zaten var). Funding store şemasında kaynak alanı yoktur (yalnız partition + coverage).

| Paket | Durum |
|---|---|
| A — sözleşme | IN PROGRESS (kanıt tablosu spec §19.13'e yazılacak) |
| B — spot provenance | VERIFIED — additive `ingest_binance_spot_klines_with_provenance` + `binance_spot_trade_dataset`; eski fonksiyon değişmedi; `tests/test_spot_provenance_ingestion.py` 8 test; tam suite 2599 passed |
| C — salt okunur store runner | VERIFIED — `research/multileg_store.py` (`run_store_backed_multileg_replay`) |
| D — parity/ret/snapshot testleri | VERIFIED — `tests/test_research_multileg_store.py` 21 test öğesi (ST1–ST11 + demo); tam suite 2620 passed |
| E — store üzerinden offline gösterim | VERIFIED — `python -m crypto_quant_lab.research.multileg_store_demo --output <yeni dizin>`; PowerShell'de exit 0 (`%TEMP%\cql\multileg-store-demo-1`) |
| F — denetim + docs | NOT STARTED |
