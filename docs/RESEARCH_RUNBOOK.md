# Araştırma Runbook'u (Faz 7 araçları)

Bu komutlar araştırma altyapısını tekrar çalıştırılabilir hâle getirir. Hepsi **varsayılan olarak offline** çalışır; yalnız `public-smoke --allow-network` ağa çıkar. Hiçbiri emir göndermez, paper/live başlatmaz, API anahtarı kullanmaz.

Aşağıdaki PowerShell örnekleri repo kökünde (`C:\Users\em80r\crypto-quant-lab`) çalıştırılmak üzere yazıldı; 2026-09-23 gecesi ve 2026-09-24 güncellemesinden sonra bu ortamda yeniden doğrulandı.

## 0. Hazırlık

Çıktıları repo dışında tutun (repo içindeki `research\` klasörü git'e girebilir):

```powershell
New-Item -ItemType Directory -Force "$env:TEMP\cql" | Out-Null
.\.venv\Scripts\python.exe -m crypto_quant_lab.research --help
```

Her çalıştırma **yeni** bir `--output` dizini ister; var olan dizin asla ezilmez (exit 2).

Çıkış kodları: `0` başarılı (uyarı olabilir: çıktı `succeeded with N warning(s)` der ve rapor `warning_count` taşır — uyarıları okuyun) · `1` rapor `failed` (hata veya başarısız kontrol) ya da doctor'da FAIL · `2` kullanım hatası, var olan çıktı dizini veya eksik `--allow-network`.

Girdi DB'leri yalnız gerçek salt okunur bağlantıyla (`mode=ro`, `query_only`) açılır: dosya, tablo veya migration asla oluşturulmaz; her store'un sorguları tek bir okuma snapshot'ında yapılır. Hesaplamalar config'teki `decimal_context`'ten (yoksa belgelenmiş varsayılandan) kurulan taze bir Decimal context'inde çalışır; çağıran süreçteki context etkisizdir (FUNDING_RESEARCH_SPEC.md §18).

Rapor şeması `crypto-quant-lab/research-report/v2`: her kontrolün bir `category`'si (execution, data_integrity, formula, descriptive, anomaly, general) vardır; `failed` raporu düşürür, `warning` düşürmez ama sayılır. `run_input_sha256` girdileri (etkin config + mantıksal girdi parmak izleri), `deterministic_sha256` çıktıyı tanımlar. 2026-09-23 gecesi üretilmiş raporlar v1'dir ve olduğu gibi geçerlidir.

## 1. Offline uçtan uca smoke

```powershell
.\.venv\Scripts\python.exe -m crypto_quant_lab.research offline-smoke --output "$env:TEMP\cql\offline-smoke-1"
```

Ne yapar: `<output>\fixture\` altında sentetik üç store'u (contract, index, funding) **production ingestion yoluyla** kurar, `fixture\config.json` yazar; inspect + basis + funding araştırmasını çalıştırır; sonuçları `research/offline_fixture.py` docstring'inde elle türetilmiş beklentilerle karşılaştırır (`expectation.*` kontrolleri). Başarılı bir çalıştırma yalnız sentetik veride boru hattı bağlantısını kanıtlar. Fixture store'ları 2026-09-25'ten beri sentetik kaynak etiketi taşır (`synthetic:offline-fixture/...`) ve `config.json` aynı etiketleri `sources` altında beyan eder; daha önce üretilmiş fixture dizinleri Binance etiketlidir ve yeni config'le açılırsa provenance hatası verir — yeniden üretin (FUNDING_RESEARCH_SPEC §19.14).

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

Son tamamen kapanmış 7 UTC günü (çalıştırma saatine göre), 1h: contract + index klines ingestion (çıktı dizininde `<SYMBOL>\contract.db`, `index.db`), close basis, resmî `/futures/data/basis` cebirsel kontrolü ve karşılaştırma. Sabitler: tolerans `0.0001`, HTTP timeout 10 sn, en fazla 2 deneme, sembol başına normalde 3 istek (bütçe 6). Yalnız `BTCUSDT`/`ETHUSDT` kabul edilir.

Durum sözleşmesi (rapor v2, FUNDING_RESEARCH_SPEC.md §18.3), sembol başına:

| Kontrol | Kategori | Sonuç |
|---|---|---|
| `execution` | execution | HTTP/API hatası (429 dahil), timeout sonrası retry tükenmesi, parse, istek bütçesi, provenance/coverage → **failed** |
| `candles_complete`, `official_records_complete` | data_integrity | eksik veri → **failed** |
| `official_algebra` | formula | `basis ≠ futuresPrice − indexPrice` veya oran gösterim artığı > 0.00005 → **failed** |
| `official_open_snapshot` | anomaly | resmî fiyat T'deki mum açılışına eşit değil → **warning** (açıklanamayan) |
| `close_vs_snapshot_tolerance` | descriptive | 1 bp aşımı → **warning**; tüm aşım zamanları listelenir |

Resmî kayıt T anındaki snapshot'tır, close basis `[T−1h, T)` kapanışıdır; bu yüzden tolerans aşımı tek başına veri hattının bozuk olduğu anlamına gelmez, ama gizlenmez ve tolerans gevşetilmez. Bir sembol başarısız olursa diğerinin sonuçları raporda kalır, rapor yine `failed` olur. Sembol başına istek bütçesi 6'dır (3 uç nokta × 2 deneme).

Üretilen `contract.db`/`index.db` dosyaları kendi config'inizle `basis-report` için kullanılabilir.

## 6b. Çok bacaklı replay sentetik demo (offline)

```powershell
.\.venv\Scripts\python.exe -m crypto_quant_lab.research.multileg_offline --output "$env:TEMP\cql\multileg-demo-1"
```

SENTETİK VERİ · SCRIPTED INTENT · STRATEJİ DEĞİL. Dört sabit senaryoyu (funding'siz kapanış, kapanış anında funding, oransal maliyet + funding, sonda açık pozisyon) üretim `run_multileg_replay` fonksiyonundan geçirir ve modül docstring'indeki elle türetilmiş değerlerle karşılaştırır. Ağ, store veya var olan DB kullanmaz; çıktı yeni bir dizine `report.json` + `report.md` (rapor v2) olarak yazılır, var olan dizin ezilmez (exit 2); beklenti tutmazsa veya senaryo hata verirse rapor `failed`, exit 1. Kapsam ve sınırlar: FUNDING_RESEARCH_SPEC.md §19.12.8.

## 6c. Store üzerinden çok bacaklı replay demosu (offline, sentetik store'lar)

```powershell
.\.venv\Scripts\python.exe -m crypto_quant_lab.research.multileg_store_demo --output "$env:TEMP\cql\multileg-store-demo-1"
```

SENTETİK VERİ · GERÇEK PİYASA VERİSİ YOK · SCRIPTED INTENT · STRATEJİ DEĞİL. Çıktı dizininin `fixture\` alt klasörüne gerçek yazıcılarla yeni sentetik store'lar yazar (spot: provenance'lı spot ingestion + sahte transport, kaynak etiketi `synthetic:...`; perpetual: candle store `write_ingestion_batch`; funding: funding store `write_ingestion_batch`), yazıcıları kapatır, sonra bu store'ları **salt okunur** açıp provenance/coverage doğrulamasıyla `run_multileg_replay`'e verir (dört `multileg_offline` senaryosu). Var olan hiçbir DB açılmaz; var olan çıktı dizini ezilmez (exit 2); doğrulanamayan kaynakta rapor `failed` (exit 1). Store'lar arası atomik snapshot yoktur (her store kendi okuma işleminde). Programatik kullanım: `crypto_quant_lab.research.multileg_store.run_store_backed_multileg_replay`. Ayrıntı: FUNDING_RESEARCH_SPEC.md §19.13.

## 6d. Config tabanlı çok bacaklı replay (offline, mevcut store'lar)

İki adım: önce örnek store'lar + örnek config yazılır, sonra AYRI bir komut config'i okuyup store'ları salt okunur açar. Aşağıdaki komutlar bu depoda PowerShell'de çalıştırıldı (2026-09-25; exit 0, 0, 0; aynı çıktı dizinine ikinci kez yazma exit 2).

```powershell
.\.venv\Scripts\python.exe -m crypto_quant_lab.research multileg-example --output "$env:TEMP\cql\multileg-example-1"
.\.venv\Scripts\python.exe -m crypto_quant_lab.research multileg-replay --config "$env:TEMP\cql\multileg-example-1\config.json" --output "$env:TEMP\cql\multileg-run-1"
```

`multileg-example` yeni dizine gerçek yazıcılarla SENTETİK store'lar (`spot.db`, `perpetual.db`, `funding_none.db`, `funding_t3.db`; kaynak etiketleri `synthetic:multileg-store-demo/...`) ve yanına `config.json` yazar; yazıcılar kapatılır. Bu store'lar olmadan örnek config çalışmaz — önce bu adım gerekir. Örnek config oransal maliyet senaryosudur: beklenen final equity `401.7076` (elle türetilmiş, FUNDING_RESEARCH_SPEC §19.15.6).

Kontrollü değişiklik örneği (kapanış intent'ini kaldır → açık final durum, beklenen `401.8591`, `position.at_end` warning):

```powershell
$c = Get-Content "$env:TEMP\cql\multileg-example-1\config.json" -Raw | ConvertFrom-Json
$c.intents = @($c.intents[0])
$c | ConvertTo-Json -Depth 10 | Out-File -Encoding utf8 "$env:TEMP\cql\multileg-example-1\config_open_end.json"
.\.venv\Scripts\python.exe -m crypto_quant_lab.research multileg-replay --config "$env:TEMP\cql\multileg-example-1\config_open_end.json" --output "$env:TEMP\cql\multileg-run-open-end-1"
```

(`-Depth 10` gereklidir; PowerShell 5.1'in `Out-File -Encoding utf8` ile yazdığı BOM kabul edilir.) Kendi store'larınızla: config'teki `stores` yollarını (config dosyasına göre göreli) ve `sources` etiketlerini store'ların kayıtlı provenance'ına göre yazın; önceden `doctor` yoktur — hata raporu hangi alanın/kaynağın neden reddedildiğini söyler. Exit: 0 succeeded (warning'ler sayılır ve yazdırılır), 1 failed (rapor yine yazılır), 2 çıktı dizini var / üst dizin yok. SCRIPTED INTENTS · STRATEJİ DEĞİL · gerçek piyasa verisi indirilmez.

### Config şeması (`multileg_replay`, sürüm 1)

| Alan | Kural |
|---|---|
| `config_kind`, `config_version` | tam olarak `"multileg_replay"` ve tam sayı `1` |
| `pair` | `pair_id`, `exchange`, `spot_symbol`, `perpetual_symbol`, `quote_asset` (boş olmayan string); hedge oranı sabit 1:1 |
| `timeframe`, `run_start`, `run_end`, `as_of` | açık offset'li ISO-8601; pencere ızgaraya hizalı; `as_of >= run_end` |
| `stores.spot` / `.perpetual` / `.funding` | config dosyasına göre göreli ya da mutlak yol; üç rol ayrı fiziksel dosya olmalı; dosya yoksa oluşturulmaz, run failed |
| `sources.spot` / `.perpetual` | ZORUNLU; store'da kayıtlı kaynak etiketiyle birebir karşılaştırılır. `sources.funding` reddedilir (funding store kaynak kaydetmez) |
| `wallets.spot_cash`, `wallets.perpetual_collateral` | ondalık string, ≥ 0 |
| `costs.spot` / `costs.perpetual` | `{"model": "zero"}`, `proportional_commission` (`rate`), `proportional_spread` (`half_spread_rate`), `proportional_slippage` (`rate`), `composite` (`components`: bu modellerin listesi) |
| `funding_model` | `{"model": "linear"}` |
| `intents` | `[]`, `[OPEN]` veya `[OPEN, CLOSE]`; her biri `action`, `decision_time` (mum kullanılabilirlik anı, `(run_start, run_end]`), `quantity` (> 0 string); CLOSE miktarı OPEN'a eşit |
| `decimal_context` (opsiyonel) | §7'deki kurallar; yoksa belgelenmiş varsayılan, rapora çözülmüş hâli yazılır |

Reddedilir: bilinmeyen alan (ör. `warmup`), tekrarlanan JSON anahtarı, kesirli/üslü JSON sayısı, NaN/Infinity, beklenen yerde null/boolean, geçersiz ondalık/zaman, desteklenmeyen model.

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
| `sources` (opsiyonel) | `{"contract": "...", "index": "..."}`: store'ların kayıtlı olması BEKLENEN kaynak etiketi, birebir karşılaştırılır. Verilmeyen rol kanonik Binance uç noktasıdır (`binance:GET https://fapi.binance.com/fapi/v1/klines` / `.../indexPriceKlines`); eski config'ler aynen çalışır. Sentetik fixture `synthetic:offline-fixture/contract-trade/v1` ve `synthetic:offline-fixture/index-price/v1` kullanır (FUNDING_RESEARCH_SPEC §19.14). Bilinmeyen rol veya boş/str olmayan değer reddedilir. |
| `decimal_context` (opsiyonel) | tam olarak `prec`, `rounding` (örn. `"ROUND_HALF_EVEN"`), `Emin`, `Emax`, `capitals`, `clamp`, `traps` (sinyal adları listesi). Yoksa: prec 28, ROUND_HALF_EVEN, Emin −999999, Emax 999999, capitals 1, clamp 0, traps `["DivisionByZero", "InvalidOperation", "Overflow"]`. Rapora her zaman çözülmüş hâli yazılır. |

Ondalıklar JSON sayısı olarak yazılırsa (`0.0002`) config reddedilir — float'a hiç dönüştürülmez.

## 8. Sık hatalar

| Belirti | Neden | Çözüm |
|---|---|---|
| `output directory already exists` (exit 2) | çıktı asla ezilmez | yeni bir `--output` dizini verin |
| `store file does not exist: X.db` | yol yanlış ya da ingestion yapılmadı | `stores.*` yolunu düzeltin; doctor ile kontrol edin |
| `X.db lacks tables [...]; read-only access never creates or migrates them` | eski/yabancı SQLite (provenance tabloları yok) | araçlar onu değiştirmez; veriyi provenance-aware ingestion ile yeni bir store'a alın |
| `schema mismatch` | tablolar var ama kolonlar farklı | dosya bu projenin store'u değil; doğru dosyayı gösterin |
| `stores: two roles point to the same physical file` | iki rol aynı dosyayı (yol alias'ı/hard link) gösteriyor | contract ve index için ayrı dosyalar kullanın |
| `decimal_context...` | `decimal_context` eksik/fazla anahtar ya da geçersiz değer | yedi anahtarın hepsini verin veya alanı tamamen kaldırın |
| `could not start a consistent read snapshot` | başka bir süreç DB'yi 5 sn'den uzun süre kilitledi | yazan süreç bitince tekrar çalıştırın |
| `... registered as index_price ...` / `contract.provenance FAIL` | roller karışmış (contract ↔ index) | `stores.contract` contract-trade, `stores.index` index-price store'unu göstermeli |
| `... registered as ... source='synthetic:...'; expected ... source='binance:GET ...'` (veya tersi) | store'un kaynak etiketi config'teki `sources` ile aynı değil (örn. 2026-09-25 öncesi üretilmiş fixture Binance etiketlidir) | etiketi tahmin etmeyin/DB'yi düzeltmeyin: `sources`'u store'un gerçek beyanıyla eşleyin veya fixture'ı yeni bir dizinde yeniden üretin |
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
