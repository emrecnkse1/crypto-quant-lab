# FUNDING_DATA_SPEC

Bu doküman, Faz 5B — Funding katmanının **data / storage / ingestion** kontratını kilitler. Bu bir tasarım dokümanıdır; kod, bağımlılık veya somut implementasyon içermez.

**Bu dokümanın kapsamadığı şey:** funding'in economic formula/accounting/replay semantics'i. Bunlar `FUNDING_SPEC.md`'de zaten kilitlenmiştir ve burada **yeniden tasarlanmaz.**

## 1. Amaç

`FUNDING_SPEC.md`, Faz 5B'nin economic/accounting/timing kontratını kilitledi ve tam data/storage/ingestion kontratını açıkça bu ayrı dokümana erteledi (FUNDING_SPEC.md Bölüm 17, 20). Bu doküman o ertelenen kontratı kilitler:

- Canonical funding event domain shape ve identity
- Validation kuralları
- SQLite storage (event table + coverage table)
- Coverage manifest semantics
- Idempotency/conflict kuralları
- Query semantics
- Binance source mapping ve transport normalizasyonu
- Fail-closed pagination
- Ingestion pipeline
- Funding-specific data quality

## 2. Binding Foundation

Bu doküman şu mevcut kontratları **binding foundation** olarak referans alır ve **hiçbirini değiştirmez:**

- `FUNDING_SPEC.md` — economic sign convention, `CostModel`'den ayrım, accounting entegrasyon prensibi, tie-order (deferred), reference-price prensibi
- HISTORICAL_DATA_SPEC.md — `HistoricalCandle`/`HistoricalCandleStore` pattern, half-open range convention, idempotency/conflict prensibi
- DATA_QUALITY_SPEC.md — transport/pagination/retry prensipleri (candle-specific mekanikleri **değil**, genel disiplin)
- BACKTEST_SPEC.md — Decimal-only, UTC-aware datetime, anti-lookahead disiplini
- Faz 5B Microstep 3 pre-flight + Microstep 3.1 empirical research bulguları (bu dokümanda doğrudan kullanılır, tekrar türetilmez)

**Çelişki politikası:** Bu dokümanın herhangi bir maddesi yukarıdaki spec'lerle çelişiyor görünüyorsa, önceki spec'ler **sessizce değiştirilmez** — çelişki açıkça raporlanır.

`HistoricalCandleStore` **değişmeden** kalır. Funding, `historical_candles` tablosuna **eklenmez.**

## 3. Canonical Domain Shape (LOCKED)

İki katmanlı model, `Candle`/`HistoricalCandle` ayrımının kavramsal aynısı:

```
FundingEvent:
    event_time: datetime
    funding_rate: Decimal
    reference_price: Decimal
    rate_type: str

HistoricalFundingEvent:
    exchange: str
    market_type: str
    symbol: str
    funding: FundingEvent
```

`FundingEvent` **exchange-specific değildir** — hiçbir Binance-özel alan/isim taşımaz. Partition/identity context (`exchange`, `market_type`, `symbol`) yalnızca `HistoricalFundingEvent` seviyesinde yaşar, tıpkı `HistoricalCandle`'ın `Candle`'a bu context'i eklediği gibi.

## 4. Rate Type (LOCKED)

Canonical `rate_type: str` — **required, non-empty, opaque, source-preserved discriminator.**

**Exchange-neutral bir enum şimdi yaratılmaz** — yalnızca Binance vocabulary'si bilinmektedir (`Regular`/`Special`); tek bir kaynaktan genelleme yapmak premature abstraction olur.

Binance adapter'ı exact source değerlerini kanonik `rate_type`'a map eder: `Regular` → `"Regular"`, `Special` → `"Special"` (literal string preservation, çeviri yok).

**Diğer gelecekteki exchange'ler, kendi normal funding'lerini otomatik olarak Binance'in `"Regular"` literal'ine map edemez.** Her adapter, kendi honest, non-empty canonical discriminator'ını sağlamak zorundadır (örn. bir discriminator'ı olmayan bir kaynak için adapter kendi sentinel'ini seçer — `"default"` gibi — asla Binance'in string'ini ödünç almaz).

### 4.1 Binance Rate-Type Strictness (LOCKED)

**Core canonical layer:** herhangi bir valid non-empty `str`'i kabul eder (opak).

**Binance source parser:** yalnızca şu anda dokümante edilen `Regular`/`Special` değerlerini kabul eder.

**Unknown Binance `rateType` → FAIL LOUDLY.** Yeni/bilinmeyen bir Binance rate type'ı **sessizce preserve edilmez** — çünkü yeni tanıtılan bir funding charge type'ının farklı ekonomik semantiği olabilir (COST_MODEL_SPEC.md'nin genel "sahte sofistikasyon'dan kaçın" disipliniyle tutarlı). Gelecekte destek, explicit adapter review/update gerektirir.

## 5. Canonical Key (LOCKED — SAFE FAIL-CLOSED v1)

```
(exchange, market_type, symbol, event_time, rate_type)
```

Bu, **SAFE FAIL-CLOSED v1 identity** olarak kilitlenir.

**ÖNEMLİ AÇIKLAMA:** Binance resmi dokümantasyonu `(event_time, rate_type)` uniqueness'ini **garanti etmez** (Microstep 3.1 empirical research, doğrulanmış). Bounded empirical sampling (1.120 kayıt, 4 symbol) da bir violation gözlemlemedi — **ama bu bir kanıt değildir.**

Bu nedenle güvenlik, source uniqueness'in var olduğunu varsaymaktan **değil**, storage'ın FAIL-CLOSED davranışından gelir: aynı key + farklı payload → `DataConflictError` (Bölüm 33). Kaynak, bu 5-tuple'a collapse olan iki ayrı ekonomik charge yayınlarsa, ingestion **sessizce yanlış bir sonuç üretmek yerine yüksek sesle başarısız olur.**

**Sentetik bir `ordinal`/`sequence`/`source_record_id` icat edilmez** — kaynak bunlar için stabil bir identity sağlamaz; fabricated bir ordinal, yalnızca "bir request'te kayıtları hangi sırayla aldığımızı" kodlar, bu da re-ingestion çalışmaları arasında değişebilecek, guard edilmeye çalışılan riskten daha kötü bir non-determinism türü olurdu.

## 6. Source Multiplicity — Storage vs Transport Ayrımı (LOCKED — KRİTİK)

**İki farklı kavram kesin olarak ayrılır:**

**(A) Storage re-ingestion idempotency:** aynı canonical key + aynı canonical payload, farklı ingestion çalıştırmaları arasında → idempotent no-op. Aynı key + farklı payload → `DataConflictError`.

**(B) Bir authoritative source traversal'ı İÇİNDE duplicate satırlar:**

**Eğer AYNI full canonical key, AYNI source page İÇİNDE birden fazla kez görünürse: RAISE.** Payload numerik olarak identical olsa bile. Gerekçe: iki identical source satırı ambiguous bir multiplicity temsil edebilir — bunları sessizce tek bir ekonomik event'e collapse etmek yanlıştır.

**Cross-page istisnası:** inclusive cursor restart'ının (Bölüm 19-20) bir page boundary'sinde neden olduğu exact full-key + exact-payload repeat, cross-page restart repeat'i olarak dedupe edilebilir. Aynı key + farklı payload, page'ler arasında da → RAISE.

**Eğer mevcut page aynı full key'i birden fazla kez içeriyorsa: boundary-dedup logic'i devreye girmeden ÖNCE RAISE edilir.**

## 7. Same-Timestamp Multiple Rate Types (LOCKED)

```
Regular at t
Special at t
```

**İKİ bağımsız `HistoricalFundingEvent` kaydı** olarak desteklenir.

**Kesinlikle YASAK:**

```
- merge etmek
- storage'a yazmadan ÖNCE toplamak (sum)
- overwrite etmek
- rate_type'ı collapse etmek
```

Replay ileride her iki ekonomik charge'ı da **ayrı ayrı** uygulayacaktır (FUNDING_SPEC.md Bölüm 7.1: "Special... additional funding rate" — replacement değil).

**Deterministic storage order:** `event_time ASC`, sonra `rate_type ASC`. Lexical `rate_type` secondary ordering yalnızca **deterministic representation** içindir, ekonomik öncelik **değildir** — aynı zamanda funding cost'ları algebraically toplanır, ama reproducibility için ordering yine de önemlidir.

## 8. Validation (LOCKED)

```
exchange:
    genuine str, non-empty after strip

market_type:
    genuine str, non-empty after strip

symbol:
    genuine str, non-empty after strip

event_time:
    genuine datetime
    true timezone-aware (pseudo-naive reddedilir)
    UTC instant semantics
    no wallclock

funding_rate:
    genuine Decimal
    finite
    negative legal
    zero legal
    positive legal

reference_price:
    genuine Decimal
    finite
    strictly > 0

rate_type:
    genuine str, non-empty after strip
```

**No float coercion** — hiçbir yerde. **Metadata string'leri sessizce normalize edilmez** (Bölüm 48).

## 9. Reference Price (LOCKED)

Canonical isim: `reference_price`, **`mark_price` DEĞİL** — `FundingModel`/domain exchange-neutral olduğu için (FUNDING_SPEC.md Bölüm 5 ile tutarlı).

Binance mapping: `markPrice` → `reference_price`.

**Kesinlikle YASAK — sessiz ikame:** candle close, candle open, next execution price.

Binance funding history endpoint'i, charge'a ait `markPrice`'ı kendisi sağladığı için, **Binance funding'i için ayrı bir mark-price pipeline'ı gerekmez.**

## 10. Decimal Precision (LOCKED)

Binance `fundingRate`/`markPrice` string olarak gelir. Parser: `Decimal(raw_string)` **doğrudan.**

**Asla:** `float(raw_string)`, `Decimal(float(...))`.

SQLite: `funding_rate`/`reference_price` için `TEXT` — mevcut lossless Decimal codec prensipleri (`decimal_to_text`/`text_to_decimal`) reuse edilir. **No REAL.**

## 11. Event Time (LOCKED)

Binance `fundingTime` = integer milliseconds. Canonical: aware UTC datetime. SQLite: `event_time_us INTEGER NOT NULL`.

Exact conversion: `fundingTime_ms * 1000 → epoch microseconds`. **No float timestamp conversion** — mevcut `datetime_to_epoch_us`/`epoch_us_to_datetime` reuse edilir.

## 12. Canonical Range (LOCKED)

Tüm core funding range API'leri: `[start_time, end_time)` — start included, end excluded. True-aware datetime, UTC-instant semantics. `start >= end` → error. Query sonuçları deterministic.

## 13. Binance Transport Range Normalization (LOCKED)

Binance transport endpoint'i: `startTime` inclusive, `endTime` inclusive. Canonical **half-open kalır.**

Transport, source-ms boundary'lerini kasıtlı olarak overfetch edebilir. Her parse edilen event, **global olarak** şu final source-of-truth ile filtrelenmelidir:

```
canonical_start <= event_time < canonical_end
```

`event_time == canonical_end` → excluded. **Repository-wide half-open semantics değiştirilmez.**

## 14. Binance Symbol (LOCKED)

Funding-history ingestion'ı **explicit `symbol` göndermek ZORUNDADIR.** Mixed-symbol historical funding pagination'ı **asla** yapılmaz. Ayrıca explicit `startTime`/`endTime`/`limit` her zaman gönderilir — Binance'in implicit default'larına **asla güvenilmez.**

## 15. Official Binance Pagination Facts (Source-Adapter Research Facts)

`GET /fapi/v1/fundingRate` (Microstep 3 pre-flight + Microstep 3.1 empirical research ile doğrulanmış):

```
startTime inclusive
endTime inclusive
max limit 1000
default limit 100
no start/end → most recent 200
response ascending
range içindeki veri limit'i aşarsa, endpoint start tarafından limit'e kadar veri döner
```

**`fundingTime` veya `(fundingTime, rateType)` için uniqueness garantisi YOK.**

## 16. Pagination — Fail-Closed Lock

Explicit `limit = 1000` kullanılır (implementasyon mikro-adımı güçlü, test edilmiş bir gerekçe bulmadıkça).

Pagination, **non-decreasing** `fundingTime`'ı destekler — strictly increasing **gerektirmez.**

Görülen tüm full canonical key'ler ve payload'lar, page'ler arasında **korunur** (tracked).

İlk cursor: `transport_start_ms`. Her request: `symbol`, `startTime=cursor`, `endTime=fixed transport_end_ms`, `limit=explicit_limit`. **Page ordering, veri kabul edilmeden ÖNCE validate edilir.**

### 16.1 Page-Local Duplicate Rule (LOCKED)

Bir response page'i **içinde** aynı full canonical key iki kez görünürse → **ERROR**, payload eşleşse bile. Intra-page duplicate'ler **asla sessizce dedupe edilmez.** Bu, ambiguous-multiplicity defense'idir (Bölüm 6).

### 16.2 Short-Page Termination — SOURCE-SPECIFIC (LOCKED)

**Yalnızca bu Binance funding endpoint'i için:** resmi dokümantasyon, requested range içindeki veri sayısı `limit`'i aşarsa response'un start tarafından `limit`'e truncate edildiğini belirtir.

Bu nedenle:

```
len(page) < explicit_limit
```

mevcut cursor→end query'si için **authoritative endpoint exhaustion** olarak kullanılabilir. Bu kısa page parse/validate/accept edildikten sonra, pagination **başarıyla sonlanır.**

**Bu SOURCE-SPECIFIC'tir.** Bu kural candle pagination'a veya başka API'lere **genelleştirilmez.**

### 16.3 Full-Page Cursor (LOCKED)

```
len(page) == explicit_limit
```

ise, pagination'ın exhausted olduğu **kanıtlanmamıştır.** `last_time = page'deki son fundingTime`. Sonraki request: `startTime = last_time` — **INCLUSIVE.**

**`last_time + 1ms` otomatik olarak asla kullanılmaz** — çünkü görülmemiş farklı bir `rate_type`, `last_time`'ı paylaşabilir (Microstep 3.1'in logical +1ms risk demonstration'ı ile tutarlı).

### 16.4 Cross-Page Boundary Dedup (LOCKED)

Restart inclusive olduğu için, sonraki page `last_time`'daki kayıtları tekrarlayabilir.

Exact **aynı full key + aynı payload** yalnızca cross-page restart repeat'i olarak discard edilebilir. **Farklı payload → ERROR.** Aynı timestamp'te görülmemiş full key → **accept.** Mevcut-page'in kendi içindeki duplicate full key, yine de **ERROR** (Bölüm 16.1).

### 16.5 Safe Progress (LOCKED)

Bir FULL page'den sonra: `last_time > previous_cursor` ise, cursor `last_time` olabilir ve devam eder. Restart page'i görülmemiş same-time key'ler ortaya çıkarırsa → accept edilir.

**Eğer bir FULL page hiçbir görülmemiş canonical key üretmezse VE cursor +1ms olmadan mevcut timestamp'ın ötesine güvenle ilerleyemezse: FAIL LOUDLY.** `+1ms`'e sırf loop'tan kaçmak için **asla** atlanmaz. Bu, API limit'inden büyük patolojik bir timestamp grubunu, veri kaybetmek yerine **availability'yi feda ederek** handle eder.

### 16.6 Pathological Exact-Timestamp Limit (LOCKED — Açık Kabul)

Eğer kaynak bir gün tek bir `fundingTime`'da `>= explicit_limit` ambiguous kayıt taşırsa ve time-only API cursor'u bunların hepsini unambiguous şekilde expose edemezse, pagination **ilerleyemeyebilir.**

**Doğru davranış: FAIL. Skip DEĞİL.** No silent data loss.

### 16.7 Termination (LOCKED)

Başarılı pagination şu durumlarda sonlanabilir:

```
A) response boş
B) response validated bir short page: len(page) < explicit_limit
   (yalnızca Bölüm 16.2'nin dokümante edilmiş Binance funding-history
   endpoint kontratı altında)
```

**Asla** "muhtemelen yeterince fetch ettik" heuristiğiyle sonlanmaz.

## 17. Storage Abstraction Direction

`HistoricalCandleStore` **değişmeden** kalır. Ayrı bir gelecek `HistoricalFundingStore` tanıtılır.

**Funding, `historical_candles`'a eklenmez.**

Funding store, event records + coverage records'u **tek bir atomic storage domain'i** olarak sahiplenir.

### 17.1 Responsibilities (Narrow)

```
- atomic ingestion write: events + coverage
- event range query
- coverage interval query
```

**Yok:** network sorumluluğu, Binance sorumluluğu, replay sorumluluğu, funding formula sorumluluğu.

## 18. Event Table (LOCKED)

```sql
-- historical_funding_events
exchange TEXT NOT NULL
market_type TEXT NOT NULL
symbol TEXT NOT NULL
event_time_us INTEGER NOT NULL
rate_type TEXT NOT NULL
funding_rate TEXT NOT NULL
reference_price TEXT NOT NULL

PRIMARY KEY (exchange, market_type, symbol, event_time_us, rate_type)
```

No REAL. No nullable columns. No sentetik row identity gerekir. Başlangıçta ekstra performans index'i yok.

## 19. Coverage Manifest (LOCKED)

Coverage, canonical bir correctness kavramıdır.

**Anlam:** `exchange`/`market_type`/`symbol`/`[start_time, end_time)` için, authoritative historical funding endpoint'i o range için başarıyla exhaustively paginate edildi, ve döndürülen her event başarıyla parse/validate/normalize/durably persist edildi.

**Coverage, event sayısından bağımsızdır.**

## 20. Coverage Table (LOCKED — Minimal v1)

```sql
-- historical_funding_coverage
exchange TEXT NOT NULL
market_type TEXT NOT NULL
symbol TEXT NOT NULL
start_time_us INTEGER NOT NULL
end_time_us INTEGER NOT NULL

PRIMARY KEY (exchange, market_type, symbol, start_time_us, end_time_us)
```

**Requirement:** `start_time_us < end_time_us`.

**v1'de correctness-irrelevant alanlar EKLENMEZ:** `ingested_at`, wallclock timestamp, `record_count`, API request count. Bunlar gelecekteki observability metadata'sı olabilir, canonical coverage truth'u **değildir.**

**v1'de canonical coverage key'inde source endpoint adı gerekmez.** Coverage statement, canonical seviyede source-neutral'dır: authoritative exchange historical source'u tamamen ingest edildi.

## 21. Coverage Physical DB Placement (LOCKED — Yalnızca Şu Kadarı)

**Yalnızca şu kilitlenir:** `historical_funding_events` + `historical_funding_coverage`, **bir** ingestion write için **AYNI SQLite transaction/connection**'a katılmalıdır.

**Bunların `historical_candles` ile aynı fiziksel DB dosyasını paylaşması GEREKMEZ.**

`FUNDING_DATA_SPEC`, shared project DB file ile dedicated funding DB file arasında **agnostic kalır.** Gereksiz candle-storage coupling'i yok (Microstep 3.1'in netleştirmesiyle tutarlı — atomicity gereksinimi yalnızca funding tablolarının birbiriyle aynı connection'ı paylaşmasını gerektirir, candle tablosuyla değil).

## 22. Atomic Write Contract (LOCKED)

Gelecekteki funding storage, events + coverage range'ı **birlikte TEK terminal ingestion transaction'ında** desteklemelidir.

Kavramsal operasyon:

```
write_ingestion_batch(
    events,
    *,
    covered_start,
    covered_end,
)
```

Exact metod adı implementasyona ertelenebilir, ama **davranış bağlayıcıdır.** `events == ()` desteklenmelidir, coverage yine de valid şekilde yazılırken.

## 23. Atomic Failure (LOCKED)

Herhangi bir event malformed/conflicting/corrupt/invalid ise, **veya** coverage'ın kendisi invalid ise: **tüm transaction rollback olur.**

**"Coverage complete" asla, karşılık gelen TÜM event yazmaları başarılı olmadıkça belirtilmez.** No event partial write. No coverage partial write.

## 24. Idempotency (LOCKED)

Başarılı re-ingestion boyunca: aynı event canonical key + aynı payload → no-op. Aynı event canonical key + farklı payload → `DataConflictError`.

Coverage: exact aynı interval → idempotent. Overlapping/adjacent farklı coverage interval'ları → **legal.**

**Conflicting event verisi asla overwrite edilmez.**

## 25. Coverage Interval Union (LOCKED)

Coverage satırları write sırasında **eagerly merge edilmez** — storage mekanik interval'ları saklar.

**Quality layer**, overlapping/adjacent coverage interval'larının deterministic union'ını hesaplar.

Örnek: `[00,12)` + `[12,24)` → `[00,24)`'ü tam olarak kapsar, gap yok.

Eğer `[00,12)` + `[13,24)` mevcutsa ve `[00,24)` requested ise: `[12,13)` uncovered olduğu için **FAIL.**

## 26. Funding Quality (LOCKED)

Dedicated funding quality path. **Candle dense-grid logic reuse EDİLMEZ:** expected timestamp grid, fixed cadence, synthetic gap, interpolation, fill-forward — hiçbiri yok.

**PASS koşulu:**

```
requested canonical range coverage-union tarafından tam olarak covered
VE
queried stored funding event'lerinin tümü structurally valid
VE
metadata/order invariant'ları geçerli
```

`event_count` **SIFIR olabilir ve yine PASS olabilir.**

## 27. Zero Event vs Missing (LOCKED)

```
Covered range + zero funding events = known authoritative zero-event history → PASS-eligible
No coverage = unknown/missing history → FAIL
```

**Missing range asla sessizce sıfır funding'e dönüştürülmez.**

## 28. No Fixed Funding Schedule (LOCKED)

Global `8h` veya başka sabit bir interval **varsayılmaz.** Faz 5B v1 quality correctness'i bir funding schedule'ı **gerektirmez** — çünkü authoritative coverage completeness kanıtı sağlar.

`fundingInfo`/`fundingIntervalHours`: şimdilik yalnızca **metadata/diagnostic.** v1 quality gate tarafından **gerekli değildir.**

## 29. Query Contract (LOCKED)

Funding event query: `exchange`/`market_type`/`symbol`/`[start,end)` girdisiyle, canonical event'leri şu sırayla döner: `event_time_us ASC, rate_type ASC`.

Store query'nin kendisi canonical deterministic ordering döner — replay boundary'sinde malformed input'un sessiz sıralanması **yoktur.**

Return collection type, repository storage convention'ını izler; exact implementasyon `HistoricalCandleStore`'un mevcut dönüş şeklini inceleyip parity tercih etmeli, immutability somut bir fayda sağlamadıkça. **`HistoricalCandleStore` API'si değiştirilmez.**

## 30. Coverage Query Contract (LOCKED)

`HistoricalFundingStore`, saf bir coverage-union quality fonksiyonu için yeterli coverage verisi expose etmelidir.

**Tercih edilen yön:** `exchange`/`market_type`/`symbol`/`[start,end)` ile overlap eden coverage interval'larını sorgula; **quality layer union'ı hesaplar.** Domain-quality reasoning'i SQL içinde gizlemekten kaçının — basit, saf Python logic'i daha test edilebilir/açık olduğunda tercih edilir. Exact metod adı ertelenebilir.

## 31. Schema Validation (LOCKED)

Funding SQLite store, mevcut candle store ile analog kesinlikte **exact schema validation** yapmalıdır. Beklenmeyen/eksik kolon → fail. Yanlış schema → `StorageError` / uygun mevcut generic storage failure. **Backtest sırasında arbitrary malformed schema sessizce migrate edilmez.**

## 32. Data Corruption (LOCKED)

Stored malformed `funding_rate`/`reference_price` (TEXT), `event_time_us`, `rate_type`, veya metadata → **yüksek sesle fail eder.** Semantik olarak uygun olduğunda generic `DataCorruptionError` reuse edilir. **Corrupted satırlar asla skip edilmez.**

## 33. Ingestion Pipeline (LOCKED)

```
1. metadata/range/as_of validate et
2. source transport inclusive ms bounds türet
3. explicit symbol/start/end/limit ile request yap
4. bounded ConnectionError-only retry
5. locked fail-closed pagination ile exhaustively paginate et
6. source string'lerini doğrudan Decimal'e parse et
7. source page ordering/multiplicity'sini validate et
8. source record → canonical funding event map et
9. canonical [start,end) ile globally filtrele
10. settled/as_of validate et
11. TÜM page'ler başarılı olduktan SONRA: TEK atomic events+coverage write
12. deterministic summary/result döndür
```

**Pagination tamamen başarılı olmadan hiçbir DB yazımı yapılmaz.**

## 34. Partial Failure (LOCKED)

Eğer page 1 başarılı, page 2 başarılı, page 3 başarısız olursa: bu ingestion attempt'inden **HİÇBİR event yazılmaz.** Requested range için **HİÇBİR coverage yazılmaz.**

Terminal batch memory'de toplanır. Funding event'leri Faz 5B v1 için yeterince sparse'tır. **No incremental commit.**

## 35. Empty Response Ingestion (LOCKED)

Eğer authoritative pagination sıfır canonical event ile başarıyla tamamlanırsa, `events=()` ile atomic coverage write yapılır. **Bu SUCCESS'tir.**

Candle quality'nin aksine, boş funding event set'i **inherently bir failure değildir.**

## 36. Settled / As-Of (LOCKED)

Yalnızca settled historical endpoint kayıtları canonical normalized funding storage'a girer.

Gelecekteki ingestion, explicit bir `source_as_of`/`ingestion_as_of` (true-aware datetime) gerektirir. `event_time > source_as_of` → defense-in-depth olarak **reddedilir.**

**Normalization/testing içinde gizli `datetime.now()` yok.** Predicted/current-next-funding endpoint verisi **yok.**

**Storage identity'nin kendisi `as_of` İÇERMEZ.**

## 37. Exchange Info / Funding Info (LOCKED)

`exchangeInfo`/`fundingInfo`, historical funding ingestion v1 için **required dependency yapılmaz.** Historical source (`/fapi/v1/fundingRate`) gerekli event verisini sağlar. `fundingInfo` ileride diagnostic metadata olabilir.

## 38. Market Type (LOCKED)

Canonical storage `market_type: str`'i korur — non-empty, exact, **global enum yok.**

```
market_type != "spot" => funding
```

kuralı **implement edilmez.** Exact funding-enabled instrument classification, bir integration boundary concern'i olarak kalır. `FUNDING_DATA_SPEC`, Faz 2 storage metadata semantics'ini **yeniden yazmaz.**

## 39. Symbol / Metadata Normalization (LOCKED)

**Sessiz canonical normalization yok.** Symbol otomatik uppercase edilmez, exchange otomatik lowercase edilmez, market_type otomatik rename edilmez — gelecekteki source adapter'ının explicit contract'ı bunu canonical identity oluşturulmadan ÖNCE yapmadıkça. Stored metadata karşılaştırması **exact'tır.**

## 40. No Fake Repair (MUST NOT)

```
- eksik funding event'i fabricate etmek
- funding rate'leri interpolate etmek
- rate'leri fill-forward yapmak
- absent event = zero varsaymak
- 8h cadence varsaymak
- Special/Regular sentezlemek
- rate type'ları storage'dan ÖNCE toplamak
- markPrice'ı candle price ile değiştirmek
- pagination ambiguity'sini sessizce skip etmek
- aynı-page duplicate canonical key'i sessizce dedupe etmek
- conflicting history'yi overwrite etmek
- predicted rate'i historically kullanmak
```

## 41. Reusable Existing Primitives (Kayıt Amaçlı)

```
decimal_to_text / text_to_decimal
datetime_to_epoch_us / epoch_us_to_datetime
StorageError
DataConflictError
DataCorruptionError
ConnectionError-only retry principle (with_connection_retry)
```

Bu API'ler bu görevde **mutate edilmez.**

**Funding, kline-style fixed-duration pagination'ı doğrudan reuse EDEMEZ** — `paginate_historical_klines`'ın cursor-advance mantığı (`+ candle_duration(timeframe)`) sabit cadence varsayar; funding'in kendi tie-safe, sparse-event pagination'ı gerekir (Bölüm 16).

## 42. Raw Binance Type Direction

Gelecekteki source-specific raw parsed representation:

```
BinanceHistoricalFundingRecord:
    symbol
    funding_rate
    funding_time
    mark_price
    rate_type
```

Binance source semantics'ini bu katmanda preserve eder (mevcut `BinanceHistoricalKline`'ın `close_time`'ı preserve etme prensibiyle aynı). Sonra canonical mapper, `mark_price → reference_price` mapping'i ile `HistoricalFundingEvent` üretir.

**Exact Python class implementasyonu ertelenir.**

## 43. Retry Discipline (LOCKED)

Mevcut bounded **`ConnectionError`-only** retry prensibi reuse edilir.

**Retry EDİLMEZ:** `ValueError`, parser/schema hataları, ordering hataları, multiplicity ambiguity, conflict'ler, data corruption.

**No broad catch/retry.**

## 44. Data-Contract Boundary — Bu Dokümanın Kilitlediği

```
- canonical domain model (FundingEvent / HistoricalFundingEvent)
- canonical key
- validation (rate/price/time/metadata)
- rate_type representation + Binance strictness
- separate SQLite event table
- separate SQLite coverage table
- atomic events+coverage write contract
- idempotency/conflict kuralları
- half-open query contract
- deterministic ordering
- coverage-union quality semantics
- Binance field mapping
- inclusive→half-open transport normalization
- fail-closed tie-safe pagination
- settled-only ingestion + as_of guard
- bounded retry reuse
- funding-specific quality gate (coverage-based, schedule-free)
```

## 45. Explicitly Deferred (Bu Dokümanın DIŞINDA)

```
- FundingModel implementasyonu
- funding formula implementasyonu
- accounting primitive
- replay funding entegrasyonu
- funding-vs-fill tie ordering (FUNDING_SPEC.md Bölüm 12'de LOCKED: funding before fill, pre-fill position — FUNDING-SPEC MS9/MS10)
- BacktestResult değişiklikleri
- funding'in strateji kullanımı
- Funding/Basis stratejisi (ROADMAP Faz 7)
- live execution
- API secrets
- cost-breakdown API
```

## 46. Faz 5B Data Acceptance Criteria (LOCKED — 35 Criteria)

1. Canonical `FundingEvent`/`HistoricalFundingEvent` iki-katmanlı domain shape kilitlenir.
2. `funding_rate`: genuine finite Decimal; negatif/sıfır/pozitif legal.
3. `reference_price`: genuine finite Decimal; strictly > 0.
4. `rate_type`: genuine non-empty str, core'da opaque.
5. Canonical key tam olarak `(exchange, market_type, symbol, event_time, rate_type)` 5-tuple'ıdır.
6. Same-timestamp farklı `rate_type` event'leri iki ayrı kayıt olarak desteklenir, merge edilmez.
7. `funding_rate`/`reference_price` TEXT olarak lossless Decimal preservation ile saklanır (no REAL).
8. `event_time`, integer epoch-microsecond olarak lossless preservation ile saklanır (no float).
9. Funding event'leri ayrı `historical_funding_events` tablosunda saklanır, `historical_candles`'a eklenmez.
10. Coverage, ayrı `historical_funding_coverage` tablosunda saklanır.
11. Funding SQLite store, mevcut candle store ile analog kesinlikte schema validation yapar.
12. Event'ler + coverage, tek bir atomic SQLite transaction'ında birlikte yazılır.
13. `events=()` ile başarılı coverage write desteklenir (zero-event ingestion success).
14. Aynı canonical key + aynı payload ile re-ingestion idempotent'tir.
15. Aynı canonical key + farklı payload `DataConflictError` üretir, overwrite etmez.
16. Query API'leri half-open `[start_time, end_time)` semantics'i uygular.
17. Query sonuçları `event_time_us ASC, rate_type ASC` deterministic sırayla döner.
18. Coverage-union quality logic, overlapping/adjacent interval'ları doğru şekilde birleştirir.
19. Uncovered bir sub-interval, quality FAIL üretir.
20. Covered range + zero event, quality PASS üretir.
21. Sabit/global bir funding schedule (örn. 8h) hiçbir yerde varsayılmaz.
22. Binance parser, `markPrice`→`reference_price`, `fundingTime`→`event_time`, `fundingRate`→`funding_rate` field mapping'ini exact uygular.
23. Bilinmeyen Binance `rateType` değeri parser tarafından yüksek sesle reddedilir.
24. Binance'in inclusive transport range'i, canonical half-open range'e global filtreleme ile normalize edilir.
25. Aynı source page içindeki duplicate canonical key, payload eşleşse bile reddedilir.
26. Cross-page exact aynı key+payload boundary repeat'i dedupe edilebilir; farklı payload reddedilir.
27. Full page + hiçbir görülmemiş key + güvenli ilerleme yok → pagination yüksek sesle fail eder (no silent `+1ms` skip).
28. Short page (`len(page) < limit`), dokümante edilmiş Binance-specific endpoint exhaustion kanıtı olarak kabul edilir.
29. Settled-only ingestion: `event_time > source_as_of` olan kayıtlar reddedilir.
30. Predicted/current-next-funding verisi hiçbir zaman normalized funding storage'a giremez.
31. Retry yalnızca `ConnectionError`'a bağlıdır; parser/conflict/corruption hataları retry edilmez.
32. Pagination başarısızlığı, ilgili ingestion attempt'inden hiçbir partial event veya coverage yazmaz.
33. Tüm funding data testleri tamamen offline çalışır.
34. Mevcut 846 test regresyonsuz PASS kalır.
35. `ruff check`/`ruff format --check` temiz kalır.

## 47. Next Implementation Sequence

```
Microstep 4:  canonical FundingEvent/HistoricalFundingEvent (pure)
Microstep 5:  HistoricalFundingStore Protocol (pure interface)
Microstep 6:  SQLite event+coverage schema / atomic write (I/O)
Microstep 7:  range query / coverage query / schema validation (I/O)
Microstep 8:  Binance funding raw parser + client
Microstep 9:  funding pagination + retry composition
Microstep 10: funding ingestion bridge
Microstep 11: funding quality report/gate
              → Faz 5B data/storage acceptance checkpoint
```

Bu görevde implementasyon **yapılmaz.**
