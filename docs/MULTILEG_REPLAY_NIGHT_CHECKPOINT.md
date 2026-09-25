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
| F — son denetim + docs | NOT STARTED |

## Bilinen sınır

`dataclasses.replace` ile bir state'in `event_ordering` alanı elle değiştirilebilir (frozen dataclass'lar yeniden kurulabilir; ledger'lar da öyle). API modu yalnız `new_hedged_portfolio`'da belirler, geçişler modu korur; kayıtlı olay geçmişi `last_event_time` ile tutarsızsa sıra tahmin edilmez, reddedilir. Keyfi elle kurulmuş state'e karşı tam koruma iddia edilmez.

## Sonraki tek adım

Paket F: bağımsız diff/ekonomik denetim, spec §19.12 + ROADMAP güncellemesi.
