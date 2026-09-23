# Araştırma Runbook'u (Faz 7 araçları)

Bu komutlar araştırma altyapısını tekrar çalıştırılabilir hâle getirir. Hepsi **varsayılan olarak offline** çalışır; yalnız `public-smoke --allow-network` ağa çıkar. Hiçbiri emir göndermez, paper/live başlatmaz, API anahtarı kullanmaz.

Aşağıdaki PowerShell örnekleri repo kökünde (`C:\Users\em80r\crypto-quant-lab`) çalıştırılmak üzere yazıldı ve 2026-09-23 gecesi bu ortamda doğrulandı.

## 0. Hazırlık

Çıktıları repo dışında tutun (repo içindeki `research\` klasörü git'e girebilir):

```powershell
New-Item -ItemType Directory -Force "$env:TEMP\cql" | Out-Null
.\.venv\Scripts\python.exe -m crypto_quant_lab.research --help
```

Her çalıştırma **yeni** bir `--output` dizini ister; var olan dizin asla ezilmez (exit 2).

Çıkış kodları: `0` başarılı · `1` rapor `failed` (hata veya başarısız kontrol) ya da doctor'da FAIL · `2` kullanım hatası, var olan çıktı dizini veya eksik `--allow-network`.

## 1. Offline uçtan uca smoke

```powershell
.\.venv\Scripts\python.exe -m crypto_quant_lab.research offline-smoke --output "$env:TEMP\cql\offline-smoke-1"
```

Ne yapar: `<output>\fixture\` altında sentetik üç store'u (contract, index, funding) **production ingestion yoluyla** kurar, `fixture\config.json` yazar; inspect + basis + funding araştırmasını çalıştırır; sonuçları `research/offline_fixture.py` docstring'inde elle türetilmiş beklentilerle karşılaştırır (`expectation.*` kontrolleri). Başarılı bir çalıştırma yalnız sentetik veride boru hattı bağlantısını kanıtlar.

Çıktı: `report.json` (makine), `report.md` (okunabilir). Aynı girdilerle her çalıştırmada `deterministic_sha256` aynıdır; saat ve git bilgisi yalnız `run_metadata` içindedir.

## 2. Doctor (salt okunur ön kontrol)

```powershell
.\.venv\Scripts\python.exe -m crypto_quant_lab.research doctor --config "$env:TEMP\cql\offline-smoke-1\fixture\config.json" --output "$env:TEMP\cql\basis-1"
```

Python sürümü, config geçerliliği, store dosyalarının varlığı (yoksa **oluşturmaz**), şema tabloları (eksikse **migration yapmaz**), contract/index provenance'ı, ayrı fiziksel dosyalar, `[start, end)` coverage'ı, funding coverage'ı ve planlanan `--output`'un güvenliğini kontrol eder. Her satır `[PASS]`/`[FAIL]` ve somut neden verir.

## 3. Dataset inceleme

```powershell
.\.venv\Scripts\python.exe -m crypto_quant_lab.research inspect --config "$env:TEMP\cql\offline-smoke-1\fixture\config.json" --output "$env:TEMP\cql\inspect-1"
```

Her store için: kayıtlı provenance, `[start, end)` ile çakışan coverage aralıkları, mum sayısı, eksik open_time listesi, mantıksal fingerprint; funding için olay sayısı, kalite durumu ve coverage boşlukları.

## 4. Close-basis betimsel raporu

```powershell
.\.venv\Scripts\python.exe -m crypto_quant_lab.research basis-report --config "$env:TEMP\cql\offline-smoke-1\fixture\config.json" --output "$env:TEMP\cql\basis-1"
```

Contract ve index store'larından tam zaman eşleşmeli close basis; eşleşmeyen slot'lar (contract_only / index_only / both_missing), min/max/ortalama, premium/discount/sıfır sayıları ve her gözlem (`available_at` dahil). Close basis bir **araştırma feature'ıdır**: index trade edilebilir bir bacak değildir, hedge/PnL yoktur.

## 5. Funding araştırması (sabit konfigürasyon)

```powershell
.\.venv\Scripts\python.exe -m crypto_quant_lab.research funding-research --config "$env:TEMP\cql\offline-smoke-1\fixture\config.json" --output "$env:TEMP\cql\funding-1"
```

Config'teki **tek, önceden sabitlenmiş** candidate'ı (ve istenirse no-trade kontrolünü) mevcut provenance-kontrollü runner ile değerlendirir. Pencere başına: final equity, PnL, maliyet (funding dahil), fill/trade sayısı, Stage-1 getiri/drawdown, Stage-2 Sharpe (tanımsızsa nedeni) ve **"neden işlem yok?" teşhisi**:

| Alan | Anlamı |
|---|---|
| `decisions_evaluated` | penceredeki karar sayısı (her mum kapanışı) |
| `signal_visible` | settled funding görülebilen kararlar |
| `fresh_signal` | görülebilen ve `max_funding_age`'den genç |
| `threshold_met` | SHORT/LONG eşiğini karşılayan kararlar |
| `reason_counts` | `no_settled_signal`, `stale_signal`, `neutral_band`, `short_threshold_met`, `long_threshold_met` (kontrol kolu: `control_always_flat`) |
| `target_changes` / `executable_target_changes` | hedef değişimleri / bir sonraki mumda uygulanabilir olanlar |
| `final_decision_unexecuted` | son mumun kararı hedef değiştirdi ama motor son mumda fill yapmaz |
| `consistent_with_engine_fill_count` | uygulanabilir değişim sayısı motorun fill sayısına eşit mi |

Bu sayımlar policy'nin kendi kural dallarıdır; repoda risk filtresi veya Risk Engine yoktur, uydurulmaz.

## 6. İsteğe bağlı public-data doğrulaması (ağ, opt-in)

```powershell
.\.venv\Scripts\python.exe -m crypto_quant_lab.research public-smoke --allow-network --symbol BTCUSDT --symbol ETHUSDT --output "$env:TEMP\cql\public-1"
```

Son tamamen kapanmış 7 UTC günü (çalıştırma saatine göre), 1h: contract + index klines ingestion (çıktı dizininde `<SYMBOL>\contract.db`, `index.db`), close basis, resmî `/futures/data/basis` cebirsel kontrolü ve karşılaştırma. Sabitler: tolerans `0.0001`, HTTP timeout 10 sn, en fazla 2 deneme, sembol başına 3 istek. Yalnız `BTCUSDT`/`ETHUSDT` kabul edilir.

`within_rate_tolerance` kontrolünün `failed` çıkması beklenebilir: resmî kayıt T anındaki snapshot'tır (T'deki mum açılışlarına eşit), close basis `[T−1h, T)` kapanışıdır. Tolerans sonuçtan sonra gevşetilmez; rapor bu durumda `failed` olur (bkz. FUNDING_RESEARCH_SPEC.md §15.9, §17.7).

Üretilen `contract.db`/`index.db` dosyaları kendi config'inizle `basis-report` için kullanılabilir.

## 7. Config şeması (v1)

`research/offline_fixture.py` içindeki `fixture_config()` tam bir örnektir; `offline-smoke` onu `fixture\config.json` olarak yazar.

| Alan | Kural |
|---|---|
| `config_version` | `1` |
| `symbol` | büyük harf alfanümerik, örn. `"BTCUSDT"` |
| `timeframe` | `"1h"` veya `"4h"` |
| `start`, `end`, `as_of` | ISO-8601, açık offset (`Z`); `start`/`end` timeframe ızgarasına hizalı; `as_of >= end` |
| `stores.contract` / `stores.index` / `stores.funding` | config dosyasına göre göreli ya da mutlak yol; iki rol aynı dosyayı gösteremez |
| `funding_research.funding_coverage_start/_end` | funding history kapsamı |
| `funding_research.publication_lag_seconds` | tam sayı ≥ 0 |
| `funding_research.windows` | `[[start, end], ...]`, hepsi `[start, end)` içinde |
| `funding_research.initial_cash`, `position_quantity` | ondalık **string**, > 0 |
| `funding_research.candidate` | `candidate_id`, `short_entry_rate`, `long_entry_rate` (string), `max_funding_age_hours` (tam sayı ≥ 1) |
| `funding_research.include_no_trade_control` | `true`/`false` |
| `funding_research.cost` | `commission_rate`, `half_spread_rate`, `slippage_rate` (string, ≥ 0; komisyon değeri bir varsayımdır) |

Ondalıklar JSON sayısı olarak yazılırsa (`0.0002`) config reddedilir — float'a hiç dönüştürülmez.

## 8. Sık hatalar

| Belirti | Neden | Çözüm |
|---|---|---|
| `output directory already exists` (exit 2) | çıktı asla ezilmez | yeni bir `--output` dizini verin |
| `store file does not exist: X.db` | yol yanlış ya da ingestion yapılmadı | `stores.*` yolunu düzeltin; doctor ile kontrol edin |
| `X.db lacks tables [...]` | eski/yabancı SQLite (provenance tabloları yok) | araçlar onu açmaz (değiştirmemek için); veriyi provenance-aware ingestion ile yeni bir store'a alın |
| `... registered as index_price ...` / `contract.provenance FAIL` | roller karışmış (contract ↔ index) | `stores.contract` contract-trade, `stores.index` index-price store'unu göstermeli |
| `coverage does NOT contain [start, end)` | aralık ingestion ile kapsanmamış ya da kapanmamış | `start`/`end`'i coverage içine alın veya eksik aralığı ingest edin |
| `JSON numbers with a fraction/exponent are not allowed` | ondalık JSON sayısı | tırnak içinde yazın: `"0.0002"` |
| `must carry an explicit offset` | naive zaman | `Z` ekleyin: `"2026-01-05T00:00:00Z"` |
| `as_of must be >= end` | aralık `as_of`'ta henüz kapanmamış | `as_of`'u `end` veya sonrasına alın |
| public-smoke exit 2 | `--allow-network` yok | bilinçli olarak ekleyin |

## 9. Sınırlar (değişmedi)

- FAZ6C tamamlanmadı (CPCV, geçerli p-değeri üretimi, parameter stability, efektif-N, çok pencereli DSR, PBO yan istatistikleri açık); FAZ6D başlamadı; Faz 7'nin tamamı bitmedi.
- Motor tek bacaklıdır; gerçek basis/carry hedge'i yoktur; index trade edilebilir bir bacak değildir.
- Test sayısı veya başarılı smoke kârlılık kanıtı değildir.
- Kullanıcının 1–10 risk profili, Türkiye saatiyle 00:00 önerisi ve işlem açılmama nedenlerinin kullanıcıya açıklanması ROADMAP'te planlı gereksinimlerdir; bu araçlar onları uygulamaz. Buradaki teşhis yalnızca araştırma görünürlüğüdür.
