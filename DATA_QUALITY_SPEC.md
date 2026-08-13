# DATA_QUALITY_SPEC

Bu doküman, Faz 3 — Data Quality + Feature Foundation katmanının kodlanmasından önce gereksinimleri ve veri bütünlüğü kurallarını kesinleştirir. Bu bir tasarım dokümanıdır; kod, bağımlılık veya storage backend içermez.

## 1. Amaç ve Kapsam

Bu spec, canonical NORMALIZED historical store'un (bkz. HISTORICAL_DATA_SPEC.md) üzerine iki yeni sorumluluk ekler:

- **Historical ingestion** — gerçek Binance geçmiş verisini, yalnızca finalized candle'ları, mevcut `write_batch()` üzerinden canonical store'a atomik/idempotent şekilde yazmak.
- **Data quality** — store'da fiilen bulunan veri için tamlık/cadence/gap denetimi yapan, makine-okunur bir rapor üreten bir katman.

Ayrıca gelecekteki feature-hesaplama kodunun uyacağı zaman/lookahead kontratlarını (feature foundation) bağlayıcı olarak belgeler — **feature'ların kendisini implement etmez.**

**Bu dokümanın YAPMADIĞI şeyler (bkz. Bölüm 17):** strateji, sinyal, backtest motoru, feature hesaplama/persistence, RAW arşiv persistence.

## 2. HISTORICAL_DATA_SPEC.md ile İlişki

`DATA_QUALITY_SPEC.md`, `HISTORICAL_DATA_SPEC.md`'nin **yerine geçmez** ve onu **değiştirmez**. Historical storage contract'ı (canonical key, Decimal/timestamp precision, atomicity, idempotency, duplicate/conflict politikası, `[start,end)` range semantiği, backend-neutral abstraction) aynen korunur ve bu dokümanda **tekrar implement edilmez.**

Bu spec yalnızca şunu ekler: ingestion (storage'a veri nasıl ulaşır) + quality (store'daki veri nasıl denetlenir) + feature timing contract'ı (gelecekteki feature kodu için bağlayıcı kural).

**Çelişki politikası:** Bu dokümanın herhangi bir maddesi `HISTORICAL_DATA_SPEC.md` ile çelişiyor görünüyorsa, eski spec **sessizce değiştirilmez** — çelişki açıkça raporlanır ve çözülene kadar spec'in ilgili maddesi askıda kalır. (Bu yazım turunda böyle bir çelişki **tespit edilmedi** — bkz. SON RAPOR.)

## 3. Desteklenen Timeframe'lar (MUST)

Faz 3 MVP yalnızca şu iki timeframe'i destekler:

| timeframe | duration |
|---|---|
| `1h` | `timedelta(hours=1)` |
| `4h` | `timedelta(hours=4)` |

Bu ikisi dışındaki her timeframe string'i (`1M`, `1w`, `3d`, `15m` dahil) `candle_duration()` tarafından **açık `ValueError`** ile reddedilir.

Bu, **geçici ve kasıtlı bir MVP sınırıdır**: repository'de fiilen kullanılan tek iki değer bunlardır, her ikisi de fixed-duration'dır ve UTC grid semantiği belirsizlik taşımaz. **Yeni bir timeframe, dedike duration + grid-alignment testleri olmadan eklenemez** — bu, `candle_duration` fonksiyonuna yalnızca bir satır eklemekle karşılanmış sayılmaz.

`candle_duration(timeframe)`, hem expected-candle-count hem gap-detection hem finalized-boundary hesaplaması için kullanılan **tek** cadence primitive'idir. İkinci, bağımsız bir süre hesabı yapılmaz.

## 4. Grid Kuralları (MUST)

- `1h` ve `4h` grid'i **UTC epoch'a** (1970-01-01T00:00:00Z) göre tanımlanır. Epoch'un kendisi UTC midnight olduğu ve bir gün (`86,400,000,000 µs`) hem `3,600,000,000 µs` (1h) hem `14,400,000,000 µs` (4h)'e tam bölündüğü için, bu iki timeframe için "epoch-aligned" ile "UTC-midnight-aligned" matematiksel olarak özdeştir.
- Hizalılık kontrolü **tam sayı epoch-microsecond aritmetiğiyle** yapılır: `epoch_us % duration_us(timeframe) == 0`. `epoch_us`, mevcut `sqlite_codec.datetime_to_epoch_us()` ile elde edilir (bu fonksiyon zaten naive/pseudo-naive reddi ve UTC-instant normalizasyonunu sağlıyor — burada tekrar yazılmaz).
- **Float yasak. `datetime.timestamp()` yasak. `round()` yasak.**
- `requested_start` ve `requested_end`: timezone-aware, UTC instant'a normalize edilebilir, **ve** desteklenen timeframe'ın grid'ine hizalı olmalıdır. Hizasız bir boundary → **açık `ValueError`.** Sessiz floor/ceil ile kullanıcının aralığı değiştirilmez.
- `as_of_time`: keyfi bir timezone-aware instant olabilir — grid'e hizalı olmak **zorunda değildir.**

## 5. Requested vs. Effective Finalized Range (MUST)

Caller'ın verdiği `requested_start`/`requested_end` **hiçbir koşulda sessizce değiştirilmez** — rapor içinde her zaman orijinal haliyle görünür (bkz. Bölüm 13).

```
effective_end = min(
    requested_end,
    latest_closed_boundary(as_of_time, duration)
)
```

`latest_closed_boundary(as_of_time, duration)` tam sayı aritmetiğiyle hesaplanır:
```
as_of_us    = datetime_to_epoch_us(as_of_time)
boundary_us = (as_of_us // duration_us) * duration_us   # integer floor division
```

**Örnek (bağlayıcı referans senaryo):** `timeframe=1h`, `requested=[10:00,15:00)`, `as_of=14:37` → `latest_closed_boundary = 14:00` (13:00 candle'ı 14:00'da kapanmıştır, `14:00 <= 14:37`) → `effective_end = min(15:00, 14:00) = 14:00`.

- Quality katmanı yalnızca `[requested_start, effective_end)` aralığında completeness/gap arar.
- `[effective_end, requested_end)` = **incomplete tail**: henüz kapanmamış olduğu için tanım gereği beklenmez.
  - Incomplete tail **missing değildir.**
  - Incomplete tail **tek başına `overall_status`'u FAIL yapmaz.**
  - Incomplete tail raporda **görünür** olmalıdır (`incomplete_tail_excluded_count`).
- `effective_end <= requested_start` → **açık `ValueError`** ("bu aralıkta henüz hiçbir şey finalized olamaz" durumu; sessiz no-op/empty batch değil).

## 6. Core As-Of Policy (MUST)

Faz 3'ün core ingestion/finalization/quality fonksiyonları **wall-clock'u içeriden asla okumaz.** `as_of_time`, her çağrıda **zorunlu, açık, timezone-aware bir parametre** olarak enjekte edilir.

Bu fazda **yasak**:
- `datetime.now()` tabanlı gizli/örtük karar.
- Keyfi bir "magic safety-buffer" sayısı (örn. "30 saniye/60 saniye" gibi uydurulmuş bir sabit) — bu spec böyle bir sayı **belirlemez.**
- Binance server-time (`/api/v3/time`) network entegrasyonu.

Production'da `as_of_time`'ın nereden geleceği (wall-clock, wall-clock+güvenlik payı, exchange server-time) **ileriki bir orchestration/hardening katmanına ertelenmiştir** — bu spec'in kapsamı dışındadır. Bu ayrım sayesinde Faz 3 core'u **tamamen deterministic ve offline-testable** kalır.

**Clock-skew riski (dokümantasyon amaçlı, sayı önerilmeden):** core fonksiyonlar `as_of_time`'ı olduğu gibi kabul ettiği için, production'da bu parametreyi besleyen katmanın yerel saat ile borsa arasındaki olası sapmayı nasıl ele alacağı ayrı bir tasarım kararıdır ve bu spec'in kapsamının dışındadır.

## 7. Binance Transport: microsecond → millisecond (MUST)

Internal canonical sınırlar: UTC microseconds, `[start,end)`. Binance transport katmanı milisaniye kullanır.

**Safe lower bound (start):**
```
start_ms = start_us // 1000
```

**Safe upper bound (end):**
```
q, r = divmod(end_us, 1000)
end_ms = q if r == 0 else q + 1
```

Bu iki formül yalnızca **transport widening**'dir (isteği güvenle genişletir, asla daraltmaz) — **float yok, `round()` yok**, yalnızca `//` ve `divmod`. Exchange'in `endTime`'ı inclusive mi exclusive mi yorumladığı **doğruluğun source-of-truth'u DEĞİLDİR** ve buna güvenilmez.

**Mandatory client-side exact filter (canonical range membership)** — `write_batch()` çağrılmadan önce, ham response'un **tüm** satırlarına uygulanır:
```
requested_start_us <= candle.open_time_us < effective_end_us
```
Bu, µs hassasiyetindeki **tek gerçek doğruluk kaynağıdır** ve **her zaman `requested_start_us`'ı** (pagination'ın o anki sayfasının değil, ingestion çağrısının bütünü için geçerli olan **global** aralık başlangıcını) kullanır.

**Bu filtre, pagination'ın `current_cursor` invariant'ıyla (bkz. Bölüm 15) karıştırılmaz — iki ayrı, birbirinin yerine geçmeyen kavramdır:**
- **(A) Canonical range membership** — bu bölümdeki filtre: bir candle'ın nihai olarak `write_batch()`'e/rapora dahil edilip edilmeyeceğine karar verir; sabit sınır `[requested_start_us, effective_end_us)`'dır, sayfa sayfa değişmez.
- **(B) Pagination page-progress validation** (Bölüm 15) — bir raw sayfanın kendi içinde tutarlı/ilerleyici olup olmadığını doğrular; sınır her sayfada değişen `current_cursor_us`'dır.

`current_cursor`, hiçbir koşulda (A)'nın yerine geçmez; (A) her zaman `requested_start_us` ile çalışır.

**Not:** Faz 3'ün normal ingestion sınırları (`requested_start`, `effective_end`) her zaman grid-aligned olduğu için, pratikte sub-millisecond bir boundary **oluşmaz.** Yine de generic transport helper'ının underfetch yapmadığını kanıtlayan bir sub-millisecond regression testi, **defense-in-depth** olarak acceptance criteria'da tutulur (Bölüm 16) — bu, gerçek bir ingestion boundary gereksinimi gibi yanlış tanımlanmamalıdır; yalnızca yardımcı fonksiyonun genel doğruluğunu kanıtlar.

## 8. Raw Binance Historical Envelope (MUST)

- Mevcut `Candle` domain modeli **değişmez** (`close_time` eklenmez).
- Mevcut `parse_binance_kline()` **bozulmaz**, aynen yeniden kullanılır.
- Canonical SQLite şeması **değişmez.**

Yalnızca ingestion sınırına özel, minimal, yeni bir yapı:

```
BinanceHistoricalKline:
    candle: Candle                  # parse_binance_kline() ile üretilir, değişmeden
    close_time: datetime            # raw[6]'dan, UTC-aware, tam sayı milisaniye aritmetiğiyle
```

`close_time` dönüşümü **tam sayı milisaniye aritmetiğiyle** yapılır — float yasak. Bu yapı **yalnızca** Binance historical ingestion sınırında yaşar; `market_data.models`'a, `storage.base`'e veya canonical şemaya sızmaz, kalıcı hale getirilmez.

## 9. Finalized Contract (MUST)

**PRIMARY finalized sinyali: raw Binance `close_time` + enjekte edilmiş `as_of_time`.**

```
finalized  ⟺  as_of_time >= (raw_close_time + 1 millisecond)
```

(Binance `close_time`, interval'ın son milisaniyesini temsil eder; candle ancak `close_time + 1ms`'den itibaren tam anlamıyla kapalıdır.)

**Zorunlu consistency invariant** (defensive, kendi cadence varsayımımızla exchange metadata'sını karşılıklı doğrular):
```
raw_close_time + 1ms  ==  candle.open_time + candle_duration(timeframe)
```

Uyuşmazsa:
- Candle **store'a yazılmaz.**
- **Sessiz düzeltme yapılmaz.**
- **Açık bir validation failure** üretilir (`ValueError` — bkz. Bölüm 11).

Feature layer, raw Binance `close_time`'a **hiçbir koşulda bağlanmaz** (bkz. Bölüm 10) — bu yalnızca ingestion sınırının bir doğrulama aracıdır, exchange-neutral feature kontratının bir parçası değildir.

## 10. Feature Availability Invariant (MUST — Bağlayıcı, Exchange-Neutral)

Historical finalized-candle pipeline için:

> Bir candle'ın **open dahil hiçbir** OHLCV alanı, `candle.open_time + candle_duration(timeframe)` anından **önce** feature/decision tarafından kullanılabilir kabul edilmez.

**Örnek:** `1h` candle, `open_time=10:00` → bu candle'ın OHLCV'si ancak `11:00`'dan itibaren usable'dır; `10:00`'da (kendi açılışında) değil.

Bu, exchange-neutral bir kuraldır — raw Binance `close_time`'a değil, yalnızca `open_time + duration`'a dayanır. Bu invariant, herhangi bir bar-tabanlı hesaplamanın "enactment"/karar zaman damgasının **asla** `t.open_time` olamayacağını, yalnızca `t.open_time + duration` olabileceğini ima eder — same-bar lookahead'ı yapısal olarak zorlaştıran temel kural budur.

Live/partial-candle feature sistemi ve current-candle-open istisnası **bu fazın kapsamı dışındadır** (bkz. Bölüm 17).

## 11. Failure Semantics (MUST)

Storage sınırındaki mevcut hiyerarşi (`StorageError` → `DataConflictError`/`DataCorruptionError`, `storage/base.py`) **aynen korunur** ve ingestion hataları için **yeniden kullanılmaz** — bu hiyerarşi yalnızca storage-boundary anlamı taşır.

Faz 3 MVP'de:

- **Geçersiz caller parametreleri** (desteklenmeyen timeframe, hizasız `requested_start`/`requested_end`, `effective_end <= requested_start`) → **`ValueError`**.
- **Malformed/kendi içinde tutarsız upstream Binance payload'ı** (non-monotonic/backward sayfa, cursor overlap/progress ihlali, `close_time` consistency ihlali) → **`ValueError`**.
- **Transport/network hataları** → **`ConnectionError`** (mevcut `binance_public.py` contract'ıyla tutarlı).

Bu fazda **yeni bir exception hiyerarşisi oluşturulmaz.** Spec, upstream validation failure ile network failure'ı semantik olarak açıkça ayırır: biri "veri/mantık tutarsız" (`ValueError`), diğeri "ağ ulaşılamaz" (`ConnectionError`) — ikisi asla karıştırılmaz.

## 12. Data Quality Responsibilities

**Zaten Candle/storage tarafından garanti edilenler — bu katmanda tekrar implement edilmez:**
finite Decimal OHLCV, OHLC consistency, non-negative volume, duplicate/conflict persistence (`DataConflictError`), ascending `query()` sıralaması, UTC persistence, Decimal exact round-trip.

**Bu katmanın eklediği yeni sorumluluklar:**
cadence (Bölüm 3), grid alignment (Bölüm 4), expected candle count, actual candle count, missing timestamps/gap detection, incomplete-tail ayrımı (Bölüm 5), empty effective dataset tespiti, requested/effective range'in raporda görünürlüğü.

**Gap policy (MUST):**
- NORMALIZED katmanda **synthetic candle üretimi yasak.**
- **Forward-fill yasak.**
- **Interpolation yasak.**
- Her genuine missing candle → `overall_status = FAIL`. (İstisna yok, eşik yok — herhangi bir eksik candle, `[requested_start, effective_end)` içindeyse rapor `FAIL` döner.)
- Quality report, downstream (gelecekteki backtest/research) bir gate için **makine-okunur** olmalıdır — serbest metin log değil, yapılandırılmış alanlar.
- **Duplicate persistence zaten canonical store'un `PRIMARY KEY`'i tarafından yapısal olarak engellendiği için** (HISTORICAL_DATA_SPEC.md Bölüm 2, 8), quality algoritması **duplicate düzeltmesi/dedupe işlemi yapmaz** — bu onun sorumluluğu değildir, storage'ın zaten sağladığı bir garantidir.

## 13. Quality Report (Minimal, Immutable, Backend-Neutral)

```
exchange, market_type, symbol, timeframe

requested_start, requested_end
as_of_time
effective_end

first_observed_open_time, last_observed_open_time

expected_count, actual_count

missing_count, missing_samples
unaligned_count, unaligned_samples

incomplete_tail_excluded_count

overall_status: PASS | FAIL
```

- `first_observed_open_time`/`last_observed_open_time`: store'da fiilen bulunan ilk/son candle'ın `open_time`'ı (bulunamazsa `None`). Bilinçli olarak `observed_start`/`observed_end` **değil** — bu isimler `[start,end)` range-boundary konvansiyonuyla (özellikle exclusive "end") karışabilir; bu alanlar range değil, tekil candle open-zaman-damgalarıdır.
- `missing_samples`/`unaligned_samples`: **bounded** — en fazla **20** örnek taşır (deterministik, sabit üst sınır). Tam missing/unaligned listesinin rapora gömülmesi **zorunlu değildir** ve büyük gap'lerde raporu şişirmemek için kasıtlı olarak yapılmaz.
- `duplicate_or_conflict_status` alanı **yoktur** — storage zaten duplicate/conflict'i yapısal olarak engellediği için bu bilgi rapor seviyesinde hiçbir zaman anlamlı değildir; modelin gereksiz şişmesini önlemek için eklenmez.
- `effective_start` ayrı bir alan **değildir** — normal durumda `effective_start == requested_start`; bu ikisinin ayrıştığı durum (`requested_start` `as_of_time`'a göre "gelecekte") zaten Bölüm 5 gereği `ValueError` ile reddedilir, dolayısıyla ayrı bir alana gerek yoktur.

### 13.1 Count Algoritması (MUST — Kesin Tanım)

`expected_count`, `actual_count`, `missing_count` ve `unaligned_count` şu **kesin** algoritmayla hesaplanır — başka bir yöntem (özellikle çıkarma-tabanlı bir kısayol) **kullanılmaz**:

**1. Expected timestamp seti** — deterministik grid-walk ile üretilir:
```
expected_timestamps = { requested_start, requested_start + duration, requested_start + 2*duration, ... }
```
`effective_end` **exclusive** olacak şekilde durur (son üretilen değer `< effective_end`, `effective_end`'in kendisi asla sete dahil edilmez). `duration = candle_duration(timeframe)`.

`expected_count = len(expected_timestamps)`.

**2. Observed rows** — mevcut store'un `query(exchange, market_type, symbol, timeframe, requested_start, effective_end)` çağrısıyla `[requested_start, effective_end)` aralığında dönen **gerçek** satırlardır (HISTORICAL_DATA_SPEC.md Bölüm 11'deki mevcut range semantiği, değiştirilmeden kullanılır).

`actual_count = len(observed_rows)` — bu, **grid'e hizalı olmayan (unaligned) satırları da içerir.** `actual_count`, hiçbir koşulda `expected_count`'tan bağımsız/filtrelenmiş bir sayı değildir; store'dan dönen her satırı sayar.

**3. Missing timestamps** — **set difference** ile belirlenir:
```
missing_timestamps = expected_timestamps  -  { row.open_time for row in observed_rows }
missing_count       = len(missing_timestamps)
```

**`missing_count`, KESİNLİKLE `expected_count - actual_count` formülüyle hesaplanmaz.** Bu formül, unaligned satır(lar) mevcut olduğunda **yanlış** sonuç üretebilir (ör. bir unaligned satır `actual_count`'u şişirip gerçek bir gap'i matematiksel olarak maskeleyebilir/negatif bir "missing" değeri üretebilir). Tek doğru yöntem, iki kümenin (expected grid vs. observed open_time'lar) **fark**ının alınmasıdır.

**4. Unaligned rows:**
```
unaligned_rows  = { row for row in observed_rows if not is_grid_aligned(row.open_time, timeframe) }
unaligned_count = len(unaligned_rows)
```

**Bu nedenle şu eşitlik ZORUNLU DEĞİLDİR ve genel olarak sağlanmaz:**
```
expected_count - missing_count == actual_count
```
Unaligned satır(lar) mevcutken bu iki taraf eşit olmayabilir — bu **beklenen, tasarım gereği** bir durumdur, bir hata belirtisi değildir. `missing_count`'un doğruluğu yalnızca set-difference algoritmasına (madde 3), `unaligned_count`'un doğruluğu yalnızca grid-alignment kontrolüne (madde 4) dayanır; ikisi birbirinden **bağımsız** hesaplanır ve toplamları `actual_count`'u tanımlamak zorunda değildir.

**`overall_status` kuralı (MUST, tam olarak):**
```
FAIL  if  missing_count > 0
      or  unaligned_count > 0
      or  ([requested_start, effective_end) için usable finalized dataset boş/invalid ise)

PASS  aksi durumda
```
`incomplete_tail_excluded_count > 0` olması **tek başına** `overall_status`'u etkilemez.

**Empty finalized dataset:** `[requested_start, effective_end)` aralığında **hiç** candle bulunmaması (`actual_count == 0` ama `expected_count > 0`) durumunda, madde 3'teki set-difference algoritması doğal olarak `missing_timestamps = expected_timestamps` (yani `missing_count == expected_count`) üretir → `overall_status = FAIL`. **Sessiz `PASS` asla üretilmez.** Bu, pagination'ın Bölüm 15'teki degenere-sayfa senaryosunun (örn. istenen aralık sembolün listelenmesinden önce olabilir) doğal, doğru sonucudur — pagination bunu bir hata olarak fırlatmaz, quality layer bunu `FAIL` olarak raporlar.

## 14. RAW / NORMALIZED / FEATURE Ayrımı

- **RAW** — Faz 3'te storage olarak implement **edilmez**. `BinanceHistoricalKline`, kalıcı bir RAW arşiv değil, yalnızca **geçici (transient)** bir ingestion envelope'udur; hiçbir yerde persist edilmez.
- **NORMALIZED** — mevcut `SQLiteHistoricalCandleStore`, değişmeden. Ingestion ve quality, yalnızca bu store'un mevcut `write_batch()`/`query()` arayüzü üzerinden çalışır.
- **FEATURE** — bu fazda **ne persistence ne hesaplama** implement edilir. Canonical NORMALIZED tabloya feature kolonu eklemek **kesinlikle yasaktır.**

## 15. Historical Ingestion — Pagination (MUST)

- Her ham sayfa **strictly ascending `open_time`** olmalıdır. Duplicate veya backward timestamp → **validation failure (`ValueError`)**, sessiz sort/dedupe **yasak.**
- Sayfadaki **her** `candle.open_time`, `>= current_cursor` olmalıdır. Sayfanın ilk candle'ı `current_cursor`'ın gerisindeyse **sessiz overlap/dedupe yapılmaz** → validation failure. (Storage'ın idempotency garantisi bu hatayı **maskelemek için kullanılmaz** — yalnızca defense-in-depth'tir, pagination doğruluğunun birincil mekanizması değildir.) Bu, **pagination page-progress validation**'dır (Bölüm 7'deki (B)) — Bölüm 7'nin canonical range-membership filtresinin (A) yerine geçmez, ondan bağımsız çalışır.

**`last_validated_raw_open_time` — kesin tanım:** Bir raw sayfa (i) strictly-ascending olduğu VE (ii) hiçbir satırı `current_cursor`'ın gerisinde olmadığı doğrulandıktan **sonra**, o sayfanın **son** (kronolojik olarak en geç) `open_time`'ıdır. Bu değerin **canonical `[requested_start, effective_end)` aralığının içinde olması şart değildir** — Bölüm 7'deki transport widening nedeniyle sayfanın son satırı `effective_end`'in dışına taşabilir; bu, `last_validated_raw_open_time`'ın tanımını değiştirmez, yalnızca o değerin range-dışı olabileceği anlamına gelir.

- `next_cursor = last_validated_raw_open_time + candle_duration(timeframe)`.
- `next_cursor > current_cursor` **zorunludur.** Değilse → validation failure / abort (sonsuz loop imkânsız hale getirilir).
- Pagination doğruluğu **`response_count == limit` varsayımına dayanmaz.**
- Bir sonraki loop adımının başında: `current_cursor >= effective_end` → pagination durur (birincil/otoriter durdurma koşulu — cursor'un range-içi veya range-dışı bir satırdan türemiş olmasından bağımsız olarak her koşulda geçerlidir).

**Degenere durum (MUST — açık tanım):** `current_cursor < effective_end` iken dönen sayfa **non-empty** olduğu halde hiçbir satırı canonical `[requested_start, effective_end)` aralığına düşmüyorsa (örn. sayfanın tamamı transport widening nedeniyle `effective_end`'in ötesinde kalıyorsa):
- Sayfa yine de **structurally valid** olabilir (strictly-ascending + cursor-ilerisi kuralları sağlanmış olabilir).
- Bu durum **synthetic data üretmek veya cursor'u geri almak için bir sebep değildir.**
- Pagination, yukarıdaki `next_cursor`/`current_cursor>=effective_end` kurallarıyla **güvenle ilerler ve sona erer.**
- Bu durum **otomatik olarak upstream corruption olarak sınıflandırılmaz** — örneğin istenen aralık, sembolün borsada henüz listelenmediği bir tarihe denk geliyor olabilir; bu meşru bir durumdur.
- Sonuç olarak ortaya çıkan eksik/boş finalized veri, **quality layer tarafından** (Bölüm 12/13) `FAIL` olarak raporlanır — pagination katmanının kendisi bunu bir hata olarak fırlatmaz.

## 16. Empty Page / Retry (MUST)

- **Bounded retry**, yalnızca **transient transport/network hatası** için kullanılır.
- Başarılı bir HTTP response gerçekten ve geçerli biçimde boş bir sayfa döndürmüşse (network hatası değil, geçerli "veri yok" yanıtı), bu **yalnızca boş olduğu için** tekrar tekrar retry edilmez → pagination durur.
- **Sonsuz retry yasaktır.**
- Bu spec, kesin retry sayısı/backoff süresini bir "magic constant" olarak **zorunlu kılmaz** — bu, implementasyon mikro-adımında seçilecek küçük, bounded bir politikadır. **Bağlayıcı olan tek şey:** retry sınırının var olması ve sonlu olmasıdır.

## 17. Kapsam Dışı (Explicit Out-of-Scope)

Şunların hiçbiri bu spec'in veya Faz 3'ün kapsamında değildir:

strategy, sinyal üretimi (LONG/SHORT), portfolio construction, risk engine, backtest engine, walk-forward, ML training, LLM trading kararları, private API keys, order execution, gerçek para trading, dashboard, Telegram, monitoring stack, RAW archive persistence, feature persistence, feature computation, `1h`/`4h` dışındaki timeframe'ler, live/partial-candle feature sistemi.

## 18. Implementasyon Mikro-Adım Sırası (Bağlayıcı Bağımlılık Sırası)

1. `candle_duration` (pure)
2. Grid/range/finalization time primitives — `is_grid_aligned`, `latest_closed_boundary`, `effective_end` (pure)
3. Transport boundary helpers (µs→ms) + mandatory client-side filter (pure)
4. Historical range fetch parametre desteği (I/O, mocked)
5. `BinanceHistoricalKline` + `close_time` parse (pure/I/O sınırı)
6. Finalized + close-time consistency validation (pure, enjekte `as_of_time`)
7. Pagination / ordering / progress guard (I/O)
8. Bounded retry (I/O)
9. Ingestion → `write_batch` köprüsü (I/O)
10. Quality report domain modeli (pure)
11. Gap/alignment quality logic (pure)
12. Store → quality entegrasyonu (I/O)
13. Feature-foundation contract dokümantasyonu + zorunlu availability-boundary executable testi (bkz. Bölüm 19, kriter 33 — opsiyonel değildir)
14. Uçtan uca ingestion+quality testleri (I/O, integration)
15. Faz 3 acceptance audit/checkpoint

Her mikro-adım küçük, bağımsız test edilebilir ve ayrı audit/commit yapılabilir olmalıdır (Faz 2'de kurulan çalışma deseniyle tutarlı).

## 19. Faz 3 Kabul Kriterleri (Acceptance Criteria)

1. `candle_duration("1h") == timedelta(hours=1)`, `candle_duration("4h") == timedelta(hours=4)`.
2. Desteklenmeyen timeframe (`candle_duration`'a) → `ValueError`.
3. Grid'e hizalı `requested_start`/`requested_end` kabul edilir.
4. Hizasız `requested_start` → `ValueError`.
5. Hizasız `requested_end` → `ValueError`.
6. Keyfi (hizasız) `as_of_time` kabul edilir.
7. Naive/pseudo-naive `as_of_time`/`requested_start`/`requested_end` reddedilir (mevcut codec kuralı yeniden kullanılarak).
8. Referans senaryo: `1h`, `requested=[10:00,15:00)`, `as_of=14:37` → `effective_end == 14:00`.
9. Incomplete tail (`[effective_end, requested_end)`) `missing_count`'a dahil edilmez.
10. `effective_end <= requested_start` → `ValueError`.
11. µs→ms transport dönüşümleri yalnızca tam sayı aritmetiğiyle yapılır (`//`, `divmod`) — float/`round()` kullanılmadığı doğrulanır.
12. Sub-millisecond exclusive-end regression testi: `effective_end=14:00:00.000500` iken `open_time=14:00:00.000000` olan candle kaybolmaz (defense-in-depth, Bölüm 7 notu).
13. Mandatory client-side `[requested_start_us, effective_end_us)` filtresi (canonical range membership, Bölüm 7'deki (A)) — her zaman global `requested_start_us`'ı kullanır, pagination'ın sayfa-bazlı `current_cursor`'ından (B) bağımsızdır — transport'un fazladan getirdiği satırları doğru eler.
14. Raw `close_time`, `BinanceHistoricalKline` üzerinden ingestion sınırına kayıpsız ulaşır.
15. Raw `close_time`'ın UTC-aware `datetime`'a dönüşümü tam sayı milisaniye aritmetiğiyle yapılır.
16. `raw_close_time + 1ms == open_time + duration` tutarlılık testi (uyumlu durum).
17. Tutarsız metadata (`raw_close_time + 1ms != open_time + duration`) → açık validation failure, sessiz düzeltme yok.
18. Finalized boundary eşitlik davranışı: `as_of_time == raw_close_time + 1ms` finalized kabul edilir (sınır dahil).
19. Henüz finalized olmayan raw candle `write_batch()`'e hiçbir koşulda ulaşmaz.
20. Sayfa içi strictly ascending `open_time` doğrulanır; ihlalde validation failure.
21. Sayfa candle'ı `current_cursor`'ın gerisinde olamaz (pagination page-progress invariant, Bölüm 7'deki (B) — Bölüm 7'nin canonical range filtresinin (A) yerine geçmez); olursa validation failure.
22. `next_cursor = last_validated_raw_open_time + duration` doğru hesaplanır — bu değerin canonical `[requested_start,effective_end)` aralığının içinde olması şart değildir (transport widening nedeniyle trailing out-of-range bir satırdan türeyebilir, bkz. Bölüm 15) — ve `next_cursor > current_cursor` doğrulanır; değilse validation failure/abort.
23. Geçerli boş sayfa → pagination durur, retry edilmez. Ayrıca: `current_cursor < effective_end` iken dönen non-empty bir sayfanın hiçbir satırı canonical `[requested_start,effective_end)` aralığına düşmüyorsa, bu tek başına upstream corruption olarak sınıflandırılmaz — pagination normal kurallarla güvenle ilerler/sona erer (Bölüm 15 degenere durum).
24. Bounded retry: transient hata sınırlı sayıda denenir, sonsuz retry yoktur.
25. Aynı aralığın tekrar ingestion'ı idempotent (yeni satır oluşmaz, hata vermez) — mevcut `write_batch` garantisiyle.
26. Decimal/UTC sadakati ingestion → SQLite → `query()` boyunca korunur (uçtan uca).
27. Sentetik/gapped sabit veri üzerinde gap (missing) detection, Bölüm 13.1'deki set-difference algoritmasıyla (expected grid MINUS observed open_time'lar) doğru çalışır — `expected_count - actual_count` çıkarma formülüyle **değil**.
28. Cadence-hizasızlığı (unaligned open_time) detection doğru çalışır **ve** observed set içinde unaligned satır(lar) bulunması, aynı senaryodaki gerçek missing candle'ların madde 27'deki set-difference algoritmasıyla doğru tespit edilmesini bozmaz (bkz. Bölüm 13.1).
29. Boş finalized dataset (`actual_count==0`, `expected_count>0`) → `overall_status=FAIL` (sessiz PASS yok).
30. Ingestion/quality kod yolunda hiçbir forward-fill/interpolation/sentetik candle üretilmediği doğrulanır.
31. Rapor içinde `requested_start`/`requested_end` ile `effective_end` her zaman ayrı ve görünür alanlar olarak yer alır.
32. Incomplete tail tek başına `overall_status`'u `FAIL` yapmaz.
33. Feature availability invariant'ı (`open_time + duration`) belgelenmiş olur **ve** en az bir executable test bunu **zorunlu olarak** doğrular (opsiyonel değildir — bu test Microstep 13'e kadar mutlaka mevcut olmalıdır). Minimum test davranışı: `1h` candle, `open_time=10:00` için (a) `10:59:59.999999`'da (boundary'den hemen önce) OHLCV kullanılabilir **değildir**, (b) `11:00:00.000000`'da (boundary'nin kendisinde) kullanılabilirdir, (c) OPEN dahil hiçbir alan için daha erken bir availability istisnası yoktur.
34. Mevcut 152 test regresyonsuz PASS kalır.
35. Tüm yeni testler tamamen offline (`tmp_path` tabanlı, gerçek network çağrısı yok).
36. `ruff check` / `ruff format --check` temiz kalır.

## 20. Data Quality & Ingestion Invariants — Checklist

- [ ] Yalnızca `1h`/`4h` desteklenir; diğerleri `ValueError`
- [ ] Grid hizalılığı tam sayı epoch-µs aritmetiğiyle kontrol edilir
- [ ] `requested_start`/`requested_end` hizasızsa `ValueError`
- [ ] `as_of_time` hizalı olmak zorunda değildir
- [ ] `requested_*` asla sessizce değiştirilmez
- [ ] `effective_end = min(requested_end, latest_closed_boundary(as_of_time, duration))`
- [ ] Incomplete tail missing değildir, tek başına FAIL değildir, raporda görünürdür
- [ ] `effective_end <= requested_start` → `ValueError`
- [ ] Core fonksiyonlar wall-clock'u içeriden okumaz; `as_of_time` her zaman enjekte edilir
- [ ] µs→ms dönüşümleri yalnızca tam sayı aritmetiğiyle (floor-start, ceil-end)
- [ ] Mandatory client-side `[requested_start_us,effective_end_us)` filtresi her zaman uygulanır — global, pagination `current_cursor`'ından bağımsız (Bölüm 7 (A) vs (B))
- [ ] `Candle`/storage şeması/`parse_binance_kline()` değişmez
- [ ] `BinanceHistoricalKline` yalnızca ingestion sınırında yaşar, persist edilmez
- [ ] Finalized kararı raw `close_time` + `as_of_time`'a dayanır
- [ ] `close_time`+1ms == `open_time`+duration consistency kontrolü zorunludur
- [ ] Tutarsızlıkta sessiz düzeltme yok, açık validation failure var
- [ ] Feature availability = `open_time + duration`, exchange-neutral, raw `close_time`'a bağlı değil
- [ ] Sayfa içi strictly ascending, sayfa candle'ı cursor'ın gerisinde olamaz
- [ ] `last_validated_raw_open_time` canonical range içinde olmak zorunda değil; `next_cursor > current_cursor` zorunlu
- [ ] Non-empty ama tamamı canonical range dışında kalan bir sayfa upstream corruption sayılmaz; pagination güvenle ilerler
- [ ] Pagination `response_count==limit` varsayımına dayanmaz
- [ ] Geçerli boş sayfa pagination'ı durdurur, retry'a girmez
- [ ] Retry yalnızca transient network hatası için, sınırlı
- [ ] NORMALIZED'de synthetic/forward-fill/interpolation yok
- [ ] Her genuine missing candle FAIL üretir
- [ ] `missing_count` set-difference (expected grid MINUS observed) ile hesaplanır, çıkarma formülüyle değil; unaligned satır varlığı bu hesabı bozmaz
- [ ] Quality report makine-okunur, sabit/bounded alanlarla
- [ ] Storage exception hiyerarşisi ingestion için yeniden kullanılmaz; `ValueError`/`ConnectionError` net ayrılır
- [ ] RAW persist edilmez, NORMALIZED değişmez, FEATURE implement edilmez
