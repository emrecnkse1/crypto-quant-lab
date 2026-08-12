# HISTORICAL_DATA_SPEC

Bu doküman, Faz 2 — Historical Data & Storage katmanının kodlanmasından önce gereksinimleri ve veri bütünlüğü kurallarını kesinleştirir. Bu bir tasarım dokümanıdır; kod, bağımlılık veya storage backend içermez.

> Bu doküman bir önceki audit'te (Overall: FAIL, 6 gap) tespit edilen boşlukları kapatmak için güncellenmiştir. Aşağıdaki kurallar **bağlayıcı MUST kurallarıdır**; implementasyon bunlardan sapamaz.

## 1. Amaç

Historical store, projenin tüm ileri fazları için tek, güvenilir **canonical veri kaynağı** olacaktır:

- **Backtest** — stratejilerin geçmiş veri üzerinde maliyetler dahil test edilmesi bu katmandan okunan veriye dayanır.
- **Research** — hipotez üretimi ve keşifsel analiz bu katmandaki veriyi kullanır.
- **Validation** — walk-forward/out-of-sample doğrulama, tutarlı ve tekrarlanabilir veri gerektirir.
- **Feature engineering (ileride)** — Feature Store, bu katmanın üzerine inşa edilecektir.

Historical store'da bulunan veri, backtest sonuçlarının güvenilirliğinin temelidir. Bu nedenle veri bütünlüğü (duplicate yok, precision kaybı yok, timezone tutarlılığı, atomicity, finalized-only) her şeyden önce gelir.

## 2. Canonical Candle Kimliği

Bir candle'ın benzersiz kimliği (canonical key) en az şu alanlardan oluşur:

- `exchange`
- `market_type`
- `symbol`
- `timeframe`
- `open_time`

Bu beşlinin birleşimi tekildir. Aynı canonical key'e sahip iki farklı kayıt kabul edilemez — duplicate candle kesinlikle üretilmemelidir.

## 3. Canonical Alanlar

Mevcut `Candle` domain modeli (`src/crypto_quant_lab/market_data/models.py`) temel alınır:

- `symbol`
- `timeframe`
- `open_time`
- `open`
- `high`
- `low`
- `close`
- `volume`

Historical storage için ek metadata alanları gereklidir:

- `exchange` — verinin kaynağı (örn. `binance`). Şu an tek exchange (Binance) kullanılsa da, canonical key'in bir parçası olarak ileride başka exchange'lerle çakışmayı önler.
- `market_type` — örn. `spot`. İleride futures/perpetual gibi farklı piyasa tiplerinin aynı symbol+timeframe ile çakışmasını önler.

Fiyat/hacim hassasiyeti (Decimal) storage katmanında hiçbir aşamada kaybedilmemelidir — somut backend kuralları için bkz. Bölüm 5 ve Bölüm 14.

## 4. Zaman Kuralları

- Tüm zamanlar **UTC** olmalıdır.
- **Naive datetime yasaktır** — mevcut `Candle` modeli bunu zaten domain seviyesinde reddediyor; storage katmanı da bu kısıtı bozmamalıdır.
- `open_time`, canonical zaman alanıdır (candle'ın açılış zamanı, candle'ın kimliğinin parçası).
- Storage'a yazarken timezone anlamı (UTC offset bilgisi) kaybolmamalıdır — sadece "naive" bir sayı/string olarak saklanıp offset varsayımına bırakılamaz.
- Okuma sonrası, yazılan ile **birebir aynı UTC zaman noktası** geri elde edilmelidir (round-trip garantisi), **microsecond hassasiyeti dahil**.
- Somut SQLite storage representation'ı (INTEGER UTC epoch microseconds) için bkz. Bölüm 14.

## 5. Decimal / Precision Kuralları

- Finansal fiyat ve hacim değerleri için **float canonical storage formatı olarak kullanılamaz** — float'ın ikili temsili ondalık kesirlerde (örn. `0.1`) hassasiyet kaybına yol açar.
- `Decimal` hassasiyeti, yaz → oku (round-trip) döngüsü boyunca korunmalıdır.
- Bir değer storage'a yazıldıktan sonra okunduğunda, orijinal `Decimal` değeriyle **birebir eşit** olmalıdır (basamak sayısı dahil, ör. `"50000.12345678"` → `"50000.12345678"`, `"50000.1"` değil).
- Bu kural backend-agnostik bir ilkedir. **SQLite için bağlayıcı, somut kural Bölüm 14'tedir** — bu bölümdeki genel ilke, Bölüm 14'teki `REAL` yasağı ve TEXT storage kuralıyla desteklenir, onun yerine geçmez.

## 6. Idempotency

Aynı historical batch (örn. aynı sembol/timeframe/zaman aralığı için tekrar çalıştırılan bir ingestion) birden fazla kez işlendiğinde:

- **Duplicate üretilmemelidir.**
- Storage'daki mevcut doğru veri **gereksiz yere çoğaltılmamalıdır.**

**Idempotent ingestion zorunludur.** Aynı veri kaynağından aynı veri iki kez çekilip yazılırsa, storage'ın son hali tek seferlik yazımdan farksız olmalıdır. Somut davranış için bkz. Bölüm 8 ("IDEMPOTENT NO-OP").

## 7. Ordering

Range query sonuçları her zaman **`open_time` ascending (artan)** sırada dönmelidir.

Bu garanti, storage katmanının kendi sorumluluğundadır — **çağıranın verdiği input sırasına güvenilemez** (örn. paralel ingest, üst üste binen batch'ler, farklı kaynaklardan gelen veri sırasız olabilir).

## 8. Duplicate Politikası (MUST)

Aynı canonical candle kimliği (bkz. Bölüm 2) iki kez geldiğinde uygulanacak politika **bağlayıcıdır**:

### 8.1 Aynı key + aynı OHLCV değeri → IDEMPOTENT NO-OP

- Yeni bir duplicate row **oluşturulmaz.**
- Mevcut doğru veri **overwrite edilmez** (gereksiz yazma işlemi yapılmaz).
- Bu, zararsız bir tekrar (örn. idempotent re-ingestion) olarak ele alınır ve hatasız sonuçlanır.

### 8.2 Aynı key + farklı OHLCV değeri → DATA CONFLICT

- **Sessiz overwrite YASAK.**
- **Sessiz ignore YASAK.**
- **İkinci bir row oluşturmak YASAK.**
- Açık bir **conflict exception/error** üretilmelidir.
- Bu conflict, o anki batch'i geçersiz kılar: batch işlemi Bölüm 10'daki atomicity kuralı gereği **tamamen rollback olur** (conflict'e neden olan kayıt dahil, batch'teki hiçbir satır kalıcı hale gelmez).

Sessizce iki ayrı kayıt oluşturmak her koşulda kesinlikle yasaktır.

## 9. Finalized-Only Candle Policy (MUST — Değişmez Invariant)

**NORMALIZED canonical historical store yalnızca FINALIZED / CLOSED candle içerebilir.**

- Henüz kapanmamış (current/incomplete) candle, canonical historical store'a **yazılamaz.**
- Bu, bir öneri değil, **değişmez bir veri bütünlüğü invariant'ıdır.**

**Not:** Mevcut `Candle` domain modelinde (`src/crypto_quant_lab/market_data/models.py`) bir `is_closed` alanı **bulunmamaktadır.**

**Faz 2 kararı:** Bu boşluğu kapatmak için storage şemasına rastgele/ad-hoc bir `is_closed` alanı **eklenmez.** Bunun yerine:

- **Historical ingestion contract'ı**, storage'a yalnızca finalized (kapanmış) candle'ların teslim edilmesinden sorumludur. Bu bir çağıran (caller) sorumluluğudur, storage şemasının bir alanı değildir.
- İleride yazılacak downloader/ingestion implementasyonunda, current/incomplete candle'ın storage'a ulaşmadan önce **filtrelenmesi zorunludur** (bkz. Bölüm 16, "Incomplete/current candle handling").
- Canonical store'un finalized-only olması, ileride şema değişse bile korunması gereken **değişmez (immutable) bir invariant** olarak kabul edilir.

**Faz 2 storage katmanının sınırı:** Storage katmanına teslim edilen `Candle` nesnesinde finalized/kapanmış olduğuna dair bir alan/bilgi **bulunmaz** (yukarıdaki not). Bu nedenle storage katmanının, sahip olmadığı bir bilgiyi (finalized durumu) doğrulamasını bekleyen bir executable unit test **Faz 2'de yazılmaz** — böyle bir test sahte/yanıltıcı bir garanti verir. Finalized-only kuralı storage'ın değil **ingestion contract'ının** sorumluluğudur; bu sorumluluk, gerekli sinyalin (candle'ın kapanıp kapanmadığı bilgisi) gerçekten var olduğu **ingestion sınırında** yürütülebilir ve test edilebilir hale gelir (bkz. Bölüm 18, madde 16).

## 10. Atomicity / Crash Safety (MUST)

Yarım yazılmış (partial) bir batch nedeniyle historical store bozulmamalıdır.

Bir batch yazımı:

- **Tamamen başarılı** olmalı,
- veya **tamamen başarısız (rollback)** olmalı,

ara bir durumda (bazı candle'lar yazılmış, bazıları yazılmamış, storage tutarsız bir durumda) **kesinlikle kalmamalıdır.**

**Batch'in tamamının rollback olmasını tetikleyen durumlar (bağlayıcı liste):**

- Batch içindeki herhangi bir kayıt **malformed** ise (schema/tip/değer olarak geçersizse),
- Batch içindeki herhangi bir kayıt mevcut veriyle **conflicting duplicate** (Bölüm 8.2) oluşturuyorsa,
- Storage validation'ı herhangi bir kayıt için hata üretirse,

→ **partial commit YASAKTIR.** Bu durumların herhangi biri oluştuğunda batch'in **hiçbir** satırı kalıcı hale gelmez; transaction/atomic davranış bu garantiyi sağlar.

## 11. Range Query Semantics (MUST)

Historical store en az şu parametrelerle sorgulanabilmelidir:

- `exchange`
- `market_type`
- `symbol`
- `timeframe`
- `start_time`
- `end_time`

**Zaman aralığı semantiği kesin ve deterministiktir:**

```
[start_time, end_time)
```

- `start_time` **inclusive** (dahil) — `open_time == start_time` olan candle sonuca dahildir.
- `end_time` **exclusive** (hariç) — `open_time == end_time` olan candle sonuca dahil **değildir.**

**Geçersiz aralık:** `start_time >= end_time` olan bir query **geçersiz kabul edilir** ve sessizce boş sonuç döndürmek yerine **açık bir hata** (örn. `ValueError`) üretmelidir.

Sonuçlar Bölüm 7'deki sıralama garantisine (ascending `open_time`) uymalıdır.

## 12. Empty / Invalid / Corrupt Data (MUST)

Aşağıdaki durumların davranışı net tanımlanmıştır:

- **Boş batch** — hiçbir hata üretmemeli, no-op olmalı (yazılacak bir şey yok).
- **Geçersiz Candle** — `Candle` domain modeli zaten geçersiz veriyi (ValueError ile) oluşum aşamasında reddeder; storage katmanına geçersiz bir `Candle` nesnesi asla ulaşmamalıdır. Ulaşırsa bu bir programlama hatasıdır ve açıkça patlamalıdır (sessizce yutulmamalıdır).
- **Duplicate / Conflict** — Bölüm 8 ve Bölüm 10'daki politika uygulanır.
- **Timezone problemi** (naive datetime) — `Candle` seviyesinde zaten engellenir; storage seviyesinde bu invariant'ın bozulmadığı ayrıca doğrulanmalıdır.
- **Kapanmamış (incomplete/current) candle** — Bölüm 9 gereği canonical store'a kabul edilmez.
- **Bozuk stored data** (storage'da zaten var olan, okunamayan/parse edilemeyen kayıt — örn. TEXT OHLCV değeri `Decimal`'a çevrilemiyorsa, veya stored timestamp/schema bozuksa):
  - **Sessiz skip YASAK.**
  - **Default/fallback değer kullanmak YASAK** (örn. çevrilemeyen değeri `0` veya `None` ile değiştirmek yasaktır).
  - Açık bir **corruption/data-integrity error** üretilmesi zorunludur.
- **Beklenmeyen schema** (storage formatının beklenenden farklı olması, örn. eksik kolon, eski versiyon) — sessizce yanlış yorumlanmamalı, açık bir hata ile durdurulmalıdır.

Ortak ilke: **sessiz veri kaybı, sessiz veri bozulması veya sessiz fallback/default değer ikamesi yoktur.** Belirsiz durumlarda sistem susmak yerine açıkça hata verir.

## 13. Veri Katmanları

Üç kavram birbirinden ayrılır:

- **RAW** — Exchange'den geldiği biçime mümkün olduğunca yakın veri (örn. Binance'in ham kline dizisi).
- **NORMALIZED** — Bizim canonical `Candle` şemamıza dönüştürülmüş, doğrulanmış, **finalized-only** veri.
- **FEATURE** — İleride feature engineering katmanı (göstergeler, türetilmiş sinyaller vb.).

**Faz 2 implementasyon kapsamı:** Bu fazda yalnızca **NORMALIZED historical storage** implement edilir.

- **RAW** — Bu fazda implement edilmez. Yalnızca gelecekteki bir arşivleme gereksiniminin var olabileceği not edilir; RAW archive tasarımı/kodu bu fazın kapsamı dışındadır.
- **FEATURE** — **Kesinlikle Faz 2 kapsamı dışındadır**, bu fazda implement edilmez.

## 14. Storage Backend Kararı (MUST)

Değerlendirilen seçenekler:

- **CSV** — Basit, insan-okunabilir bir dosya formatı. Ancak: native transaction desteği yoktur, canonical key üzerinde native bir uniqueness/constraint mekanizması yoktur (duplicate prevention tamamen elle yazılan uygulama koduna kalır), atomic batch write kendi başına garanti edilmez (yarım yazım riski), range query dosya taraması gerektirir. **Canonical transactional store için uygun değildir, bu amaçla seçilmez.**
- **Parquet** — Kolon bazlı, analitik okuma için hızlı, disk açısından verimli. Ancak: native transaction/unique-constraint desteği yok, bu davranışlar uygulama kodunda elle inşa edilmeli; `pyarrow`/`pandas` gibi yeni bir bağımlılık gerektirir. **Canonical transactional store olarak seçilmez.**
- **SQLite** — Python stdlib'in bir parçası (`sqlite3`), yeni bağımlılık gerektirmez. ACID transaction desteği var (atomic batch write doğal olarak sağlanır, bkz. Bölüm 10). `UNIQUE` constraint ile canonical key üzerinden duplicate prevention veritabanı seviyesinde garanti edilebilir. Tek dosya, Windows'ta lokal kullanım için sorunsuz. Test edilebilirlik yüksek. Düşük başlangıç operasyon maliyeti.
- **PostgreSQL / TimescaleDB** — Üretim ölçeğinde zaman serisi verisi için güçlü, ancak ayrı bir sunucu/servis çalıştırmayı ve (PROJECT_RULES.md gereği bu fazda yasak olan) altyapı kurulumunu gerektirir. Şu anki tek-kullanıcı, lokal, düşük veri hacimli aşama için operasyonel overkill.

### 14.1 Karar

**İlk backend kararı: SQLite.**

**Gerekçe:**
- stdlib `sqlite3` — sıfır yeni dependency.
- ACID transaction — Bölüm 10'daki atomicity gereksinimini doğrudan karşılar.
- `UNIQUE` constraint — canonical key üzerinden duplicate prevention veritabanı seviyesinde garanti edilir.
- Windows'ta lokal kullanım — tek dosya, sunucu/servis kurulumu gerektirmez.
- Offline test edilebilirlik — hızlı, izole unit testler mümkün.
- Düşük başlangıç operasyon maliyeti.
- Backend abstraction (Bölüm 15) sayesinde ileride PostgreSQL/TimescaleDB migration mümkün.

Bu fazda **hiçbir backend kurulmaz** — bu sadece bir karardır, implementasyon ayrı bir görevdir.

### 14.2 Decimal Storage Kuralı (MUST)

- Canonical OHLCV alanları (`open`, `high`, `low`, `close`, `volume`) için **SQLite `REAL` kullanımı KESİNLİKLE YASAKTIR.** `REAL`, IEEE-754 binary float'tır ve Bölüm 5'teki precision ilkesini ihlal eder.
- Bu beş alan SQLite'ta **lossless TEXT representation** olarak saklanır (`open TEXT NOT NULL` vb.).
- **Yazarken:** `Decimal`'ın string representation'ı (`str(decimal_value)`) kayıpsız olarak TEXT kolona yazılır.
- **Okurken:** TEXT değer doğrudan `Decimal(text)` ile geri oluşturulur.
- **Float ara dönüşümü kesinlikle yasaktır** — yazma veya okuma yolunun hiçbir adımında değer bir `float`'a çevrilip geri dönüştürülemez.

### 14.3 Timestamp Storage Kuralı (MUST)

- Canonical `open_time`, SQLite'ta **UTC Unix epoch microseconds** olarak, **INTEGER** kolonda saklanır:

  ```
  open_time_us INTEGER NOT NULL
  ```

- `open_time_us`, UTC epoch'tan (1970-01-01T00:00:00Z) itibaren geçen mikrosaniye sayısıdır.
- Bu alan **UTC olmak zorundadır** — storage'a yazılmadan önce naive datetime kesinlikle reddedilir (Bölüm 4).
- **Dönüşüm float hassasiyetine bağımlı olamaz** — `datetime.timestamp()` gibi float-saniye döndüren API'lere veya float çarpma/bölme ile mikrosaniyeye çevirmeye dayanılamaz; dönüşüm tam sayı aritmetiğiyle (örn. epoch'tan farkın doğrudan mikrosaniye biriminde tam sayı olarak hesaplanması) yapılmalıdır.
- **Round-trip garantisi:** `datetime → open_time_us → datetime` dönüşümü, Python `datetime` nesnesinin **microsecond hassasiyetini** birebir korumalıdır (saniyenin altındaki mikrosaniye basamağı dahil, kayıpsız).
- Unique canonical key (Bölüm 2) ve range query (Bölüm 11) bu integer alan üzerinden çalışır — `INTEGER` olması sıralamanın (Bölüm 7) ve `[start_time, end_time)` filtrelemesinin (Bölüm 11) **deterministic ve sortable** olmasını garanti eder (string/TEXT timestamp karşılaştırmasının olası locale/format belirsizliklerinden kaçınılır).

## 15. Storage Abstraction (MUST — Faz 2 Kapsamında) & Migration Stratejisi

**Karar:** Minimal, backend-neutral bir **storage abstraction (repository arayüzü/protokolü) Faz 2 kapsamındadır** — bu fazda tanımlanır ve implement edilir. (Önceki taslakta bu abstraction'ın kodu "kapsam dışı" olarak işaretlenmişti; bu ifade geçersizdir ve bu bölümle değiştirilmiştir.)

**Faz 2 implementasyonu sırasında:**

- Backend-neutral bir **historical candle storage interface/protocol** tanımlanır (örn. "canonical key ile candle'ları getir", "bir batch candle yaz", "range query çalıştır").
- **SQLite implementation**, bu abstraction'ı uygular (implement eder).
- `sqlite3` / SQL / connection / cursor detayları yalnızca SQLite implementation'ının sınırları içinde kalır; abstraction'ın kendisi bu detayları dışa sızdırmaz.

**Temel ilke (MUST, değişmez mimari invariant):** İş mantığı (research, backtest, strateji, risk engine) hiçbir zaman doğrudan storage backend'ine (SQL sorgusu, `sqlite3` connection/cursor, dosya formatı, bağlantı detayı) bağlanmaz; yalnızca bu backend-neutral abstraction üzerinden çalışır.

**Kapsam sınırı — yapay katman oluşturma yasağı:** `research`, `backtest`, `strategy`, `risk` gibi katmanlar bu proje aşamasında **henüz mevcut değildir.** Faz 2, bu katmanları **sırf yukarıdaki invariant'ı test etmek için yapay olarak oluşturmaz.** Bunun yerine Faz 2'de fiilen doğrulanan şey:

- Abstraction'ın kendisinin `sqlite3` tiplerini/imzalarını dışa sızdırmadığı,
- SQLite implementation'ının abstraction contract'ının tüm davranışlarını doğru karşıladığı,

ve gelecekte `research`/`backtest`/`strategy`/`risk` katmanlarının yalnızca bu abstraction'ı kullanacağı kuralı, bu dokümanda **belgelenmiş, değişmez bir mimari invariant** olarak kayıt altına alınır — bu katmanlar ileride oluşturulduğunda uyulması zorunludur ve o katmanların kendi test suite'lerinde ayrıca doğrulanır.

İleride farklı bir backend'e (örn. PostgreSQL/TimescaleDB) geçilirse, yalnızca bu abstraction'ın implementasyonu değişir; onu çağıran hiçbir kod değişmez. Migration bu sayede iş mantığını kırmadan yapılabilir. Somut acceptance criterion için bkz. Bölüm 18, madde 15.

## 16. Historical Ingestion İlkeleri

İleride bir historical downloader yazılırken (bu fazda kod yazılmaz) şu konular ele alınmalıdır:

- **Pagination** — Binance gibi exchange'ler tek istekte sınırlı sayıda kline döner; geniş zaman aralıkları için sayfalama gerekir.
- **Deterministic time ranges** — her ingestion çağrısı, hangi `[start_time, end_time)` aralığının çekildiğini net ve tekrarlanabilir şekilde tanımlamalıdır (Bölüm 11'deki semantikle tutarlı).
- **Overlap handling** — ardışık sayfalar/batch'ler arasında zaman aralığı çakışması olabilir; bu çakışma idempotent yazım (Bölüm 6, Bölüm 8.1) ile güvenle absorbe edilmelidir.
- **Rate-limit awareness** — exchange API rate limit'lerine saygı gösterilmeli, aşırı istek gönderilmemelidir.
- **Retry sınırları** — geçici ağ hatalarında sınırlı ve öngörülebilir sayıda retry yapılmalı, sonsuz döngüden kaçınılmalıdır.
- **Incomplete/current candle handling (MUST)** — Bölüm 9'daki finalized-only invariant'ı gereği, henüz kapanmamış (şu anki) candle, downloader tarafından storage'a ulaşmadan önce **zorunlu olarak filtrelenmelidir.** Bu, ingestion contract'ının storage'a karşı sorumluluğudur.

## 17. Data Integrity Invariants — Checklist

- [ ] Unique canonical key (`exchange` + `market_type` + `symbol` + `timeframe` + `open_time`)
- [ ] Tüm timestamp'ler UTC, timezone-aware
- [ ] Decimal hassasiyeti round-trip'te korunur (yaz → oku sonrası değer birebir aynı)
- [ ] OHLCV alanları için SQLite `REAL` kullanılmaz; TEXT + `Decimal(text)` kullanılır, float ara adımı yoktur
- [ ] `open_time`, SQLite'ta `open_time_us` (UTC epoch microseconds, INTEGER) olarak saklanır; round-trip microsecond hassasiyetini korur
- [ ] Range query sonuçları `open_time` ascending sırada döner
- [ ] Range query semantiği `[start_time, end_time)` — start inclusive, end exclusive — ve deterministiktir
- [ ] `start_time >= end_time` açık hata üretir
- [ ] Yazım işlemleri idempotent'tir (aynı key + aynı değer → IDEMPOTENT NO-OP, duplicate oluşmaz)
- [ ] Çakışan duplicate'ler (aynı key, farklı OHLCV) DATA CONFLICT olarak ele alınır; sessizce yok sayılmaz/overwrite edilmez, ikinci row oluşturmaz
- [ ] Batch yazımı atomiktir (tamamen başarılı ya da tamamen rollback); malformed kayıt, conflicting duplicate veya validation hatası tüm batch'i rollback ettirir
- [ ] Geçersiz/malformed kayıtlar storage'a asla kabul edilmez
- [ ] Kapanmamış (incomplete/current) candle canonical historical store'a asla yazılmaz (finalized-only invariant)
- [ ] Sessiz veri kaybı, sessiz veri bozulması veya sessiz fallback/default değer ikamesi yoktur
- [ ] Storage'daki bozuk/okunamayan kayıt sessizce atlanmaz, açık corruption/data-integrity hatası verir
- [ ] `research`/`backtest`/`strategy`/`risk` katmanları doğrudan `sqlite3` import etmez; storage-specific detaylar sızmaz

## 18. Faz 2 Kabul Kriterleri

Faz 2'nin tamamlanmış sayılması için aşağıdaki davranışların koda ve testlere yansımış olması gerekir. (Kriter sayısı 12 ile sınırlı değildir.)

1. **Unique canonical key** — Canonical key (`exchange`, `market_type`, `symbol`, `timeframe`, `open_time`) üzerinde uniqueness'ı doğrulayan bir test vardır.
2. **Exact Decimal round-trip** — Yazılan bir `Decimal` OHLCV değerin (yüksek hassasiyetli örnekler dahil) okuma sonrası orijinaliyle basamak dahil birebir eşit olduğunu doğrulayan bir test vardır; `REAL` kullanılmadığı doğrulanır.
3. **UTC timestamp round-trip** — Yazılan bir UTC `datetime` değerin okuma sonrası orijinaliyle (tzinfo dahil) birebir eşit olduğunu doğrulayan bir test vardır.
4. **Microsecond timestamp preservation** — Mikrosaniye bileşeni sıfır olmayan bir `datetime`'ın `open_time_us` üzerinden round-trip'te mikrosaniye hassasiyetiyle korunduğunu doğrulayan bir test vardır.
5. **Deterministic `[start_time, end_time)` filtering** — `open_time == start_time` olan bir candle'ın sonuca dahil edildiğini, `open_time == end_time` olan bir candle'ın sonuca dahil edilmediğini doğrulayan bir sınır (boundary) testi vardır.
6. **Invalid range query rejection** — `start_time >= end_time` ile yapılan bir query'nin açık bir hata ürettiğini (sessizce boş sonuç dönmediğini) doğrulayan bir test vardır.
7. **Ascending open_time order** — Sırasız (unsorted) girilen bir batch'in, range query ile `open_time` ascending sırada döndüğünü doğrulayan bir test vardır.
8. **Duplicate same-data idempotency** — Aynı canonical key + aynı OHLCV değeriyle tekrar yazımın IDEMPOTENT NO-OP olduğunu (yeni row oluşmadığını, mevcut veri overwrite edilmediğini) doğrulayan bir test vardır.
9. **Conflicting duplicate detection** — Aynı canonical key + farklı OHLCV değeriyle yazımın açık bir DATA CONFLICT hatası ürettiğini (sessizce üzerine yazılmadığını, ikinci row oluşturmadığını) doğrulayan bir test vardır.
10. **Atomic rollback** — Malformed kayıt, conflicting duplicate veya storage validation hatası içeren bir batch'in storage'da hiçbir kalıcı iz bırakmadığını (partial commit olmadığını) doğrulayan bir test vardır.
11. **Empty batch** — Boş bir batch'in yazımının hatasız, no-op olarak sonuçlandığını doğrulayan bir test vardır.
12. **Corrupt stored data rejection** — Storage'da zaten var olan, `Decimal`'a çevrilemeyen bir TEXT OHLCV değerin veya bozuk timestamp/schema'nın okuma sırasında sessizce atlanmadığını/fallback değerle değiştirilmediğini, açık bir corruption hatası ürettiğini doğrulayan bir test vardır.
13. **Malformed input rejection** — Şeması/tipi geçersiz bir kaydın storage'a kabul edilmediğini doğrulayan bir test vardır.
14. **Persistence across reopen** — Gerçek bir **geçici SQLite dosyasıyla** (in-memory değil): veri yazılır, connection/store kapatılır, yeniden açılır, aynı canonical `Candle` verisinin kayıpsız geri geldiği doğrulanır.
15. **Backend abstraction isolation** — Aşağıdakilerin tümü doğrulanır:
    - Backend-neutral historical candle storage interface/protocol tanımlıdır ve `sqlite3` tiplerini/imzalarını (connection, cursor, vb.) expose etmediği bir test/statik kontrol ile doğrulanır.
    - `sqlite3` importu yalnızca SQLite storage implementation modülünde bulunur; bu sınırın dışına (örn. abstraction'ın kendisine) sızmadığı doğrulanır.
    - SQLite implementation'ının, abstraction contract'ının davranışlarını (yazma, okuma, range query, idempotency, conflict, atomicity) doğru karşıladığı testlerle doğrulanır.
    - `research`/`backtest`/`strategy`/`risk` katmanlarının yalnızca bu abstraction'ı kullanacağı kuralı, Bölüm 15'te **belgelenmiş, değişmez bir mimari invariant** olarak kayıtlıdır. Bu katmanlar bu proje aşamasında henüz mevcut olmadığından, onlara özgü executable bir test bu fazda **yazılmaz** — bu bir eksiklik değil, bilinçli bir kapsam sınırıdır; katmanlar oluşturulduğunda bu invariant'a uyum kendi test suite'lerinde doğrulanacaktır.
16. **Finalized-only canonical historical data contract** — İki ayrı parça net olarak ayrılır:
    - **Faz 2'de doğrulanan:** Bölüm 9'daki finalized-only kuralının bağlayıcı bir ingestion contract invariant'ı olarak bu dokümanda belgelenmiş olması, ve storage'a teslim edilen (finalized olduğu varsayılan) her `Candle`'ın canonical şemaya uygun şekilde saklanabildiği (bu zaten madde 1-14'teki testlerle kapsanır). Storage katmanının sahip olmadığı finalized/kapanmış bilgisini doğrulamaya çalışan sahte bir executable test **yazılmaz** (bkz. Bölüm 9).
    - **Ingestion fazına ZORUNLU olarak defer edilen (adım atlamak değildir):** Historical downloader/ingestion implementasyon fazında, current/incomplete (henüz kapanmamış) bir candle'ın storage'a ulaşmadan önce filtrelendiğini doğrulayan **executable bir test**, o fazın **zorunlu acceptance criterion'udur.** Bu test ancak ingestion fazında yazılabilir, çünkü finalized/kapanmış olma sinyali (örn. exchange'in "bu candle kapandı mı" bilgisi) yalnızca o sınırda, ham exchange verisinde mevcuttur — storage katmanında değil.
17. **Offline & lint-clean** — Tüm bu testler tamamen offline çalışır (gerçek network veya harici servis kullanmaz) ve mevcut `ruff check` / `ruff format --check` kontrollerini geçer.

Bu 17 kriterin tamamı karşılanmadan Faz 2, "tamamlandı" olarak işaretlenemez.
