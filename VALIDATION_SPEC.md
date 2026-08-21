# VALIDATION_SPEC

Bu doküman, Faz 6 — Validation / Anti-overfitting katmanının kodlanmasından önce gereksinimleri ve doğruluk kurallarını kesinleştirir. Bu bir tasarım dokümanıdır; kod, bağımlılık veya somut implementasyon içermez.

**Bu dokümanın kapsamadığı şey:** Faz 6'nın TÜM alt-kapsamının tek bir microstep zincirinde implement edilmesi. Bu spec, foundation (temporal split + fixed-policy OOS evaluation + basic metrics) için yeterince desteklenen kısmı **kilitler**, ve ileri seviye tekniklerin (purging, embargo, CPCV, Deflated Sharpe, PBO, multiple-testing, parameter stability) **dependency pozisyonunu** açıkça tanımlar — onları implement etmeden veya başka bir faza sessizce taşımadan.

## 1. Amaç

`BACKTEST_SPEC.md` Bölüm 26/27/34, Faz 4'ün kapsamadığı şu konuları Faz 6'ya erteledi: walk-forward, train/test split, out-of-sample framework, purged CV/embargo/CPCV, Deflated Sharpe, PBO, multiple-testing correction, parameter search, Sharpe/Sortino/Calmar/max-drawdown metrikleri. Bu doküman o ertelenen kontratı açar:

- Faz 6'nın gerçek yeteneği ve dürüst sınırları (overfitting "önlenmez", risk azaltılır/tespit edilir/ölçülür)
- Mimari sahiplik: validation, mevcut backtest altyapısını **compose eder**, kendi replay/accounting/execution/cost/funding motorunu yazmaz
- Temporal window / IS-OOS split kontratı
- **Evaluation window vs. context/warm-up window** ayrımı — mevcut API'nin bunu net bir şekilde desteklemediğinin açık tespiti
- Metrik foundation'ının staged (kademeli) bağımlılık sırası
- İleri seviye tekniklerin (purging, embargo, CPCV, DSR, PBO, multiple-testing, parameter stability) her biri için exact prerequisite listesi
- Faz 6'nın alt-faz yapısı ve "foundation complete" ile "Faz 6 complete"'in **eşit olmadığı**

## 2. Binding Foundation

Bu doküman şu mevcut kontratları **binding foundation** olarak referans alır ve **hiçbirini değiştirmez:**

- `BACKTEST_SPEC.md` — execution modeli, anti-lookahead, `BacktestPolicy`/`PolicyContext`, `BacktestResult`, Bölüm 26 (Validation Boundary), Bölüm 27 (Performance Metrics — MVP)
- `COST_MODEL_SPEC.md` — `CostModel`/`CompositeCostModel`, fill-tetiklemeli maliyet sınırı
- `FUNDING_SPEC.md` / `FUNDING_DATA_SPEC.md` — funding'in zaman+pozisyon-tetiklemeli ekonomik etkisi, `funding_required` explicit contract
- `DATA_QUALITY_SPEC.md` — `effective_end`, `feature_availability_time()`, half-open `[start,end)`, quality gate PASS/FAIL disiplini
- `HISTORICAL_DATA_SPEC.md` — `HistoricalCandleStore` half-open range/idempotency prensipleri
- `ARCHITECTURE.md` Katman 4 — "Backtest / Validation Layer" (tek, birleşik katman)
- `PROJECT_RULES.md` — backtest → walk-forward/OOS → paper/shadow → live sırası; look-ahead/leakage/survivorship bias disiplini

**Çelişki politikası:** Bu dokümanın herhangi bir maddesi yukarıdaki spec'lerle çelişiyor görünüyorsa, önceki spec'ler **sessizce değiştirilmez** — çelişki açıkça raporlanır ve çözülene kadar bu dokümanın ilgili maddesi askıda kalır. Özellikle `BACKTEST_SPEC.md` Bölüm 26/27/34'ün Faz 6'ya atadığı hiçbir madde, bu dokümanda sessizce başka bir faza taşınmaz veya kapsam dışı bırakılmaz — yalnızca **dependency pozisyonu** (NOW / LATER IN FAZ 6) belirlenir.

## 3. Faz 6 Tanımı — Dürüst Kapsam (LOCKED)

Faz 6 tamamlandığında sistem şu yeteneğe sahip olur: tek-sembollük, geçmiş bir dataset'i kronolojik olarak overlap'siz in-sample/out-of-sample pencerelere ayırabilir, mevcut quality-gated/cost-aware/funding-aware backtest motorunu her pencere üzerinde bağımsız olarak yeniden çalıştırabilir, pencereleri kronolojik sırayla ilerletebilir (rolling fixed-policy evaluation), ve her pencere sonucundan temel getiri/drawdown metrikleri türetebilir — daha ileri seviyede, candidate/trial takibi + CPCV + Deflated Sharpe + PBO + multiple-testing correction'ı (bu dokümanın Bölüm 17'sinde tanımlanan bağımlılık sırasıyla) ekleyebilir.

**Kesinlikle iddia edilmez:**

```
- overfitting tamamen önlenir
- karlılık garanti edilir
- gelecekteki performans tahmin edilir
```

Doğru dil: overfitting/selection-bias riskini **azaltmak, tespit etmek, ölçmek.** Faz 6, tamamen **geçmiş veri üzerinde araştırma** katmanıdır — canlı/paper trading, exchange yazma, order, API key, LLM karar verme burada **yoktur** (`PROJECT_RULES.md`, `ARCHITECTURE.md` ile tutarlı).

## 4. Mimari Sahiplik (LOCKED)

Validation, mevcut backtest altyapısını **compose eder.** Aşağıdakilerin hiçbiri validation-specific olarak yeniden yazılmaz:

```
- replay
- accounting
- execution / fill transition
- transaction-cost engine (CostModel/CompositeCostModel)
- funding engine (FundingModel, funding_required contract)
- data-quality gate (candle veya funding)
```

Her ekonomik olarak değerlendirilen pencere, nihayetinde mevcut `run_backtest_from_store` (veya, offline/pure test'lerde, `run_backtest_replay`) çağrısı üzerinden yürütülür. `BacktestResult` **değişmeden** kalır — Bölüm 15'te (Metrics Foundation) türetilen metrikler `BacktestResult.equity_curve`'den **dışarıda, ayrı bir katmanda** hesaplanır.

**Recommended package boundary:** `src/crypto_quant_lab/validation/` — mevcut repo'nun flat, tek-domain-per-package convention'ıyla tutarlı (`backtest/`, `data_quality/`, `funding/`, `market_data/`, `storage/`'ın yanına eklenir; `research/validation/` gibi bir nesting yok, çünkü `research/` paketi mevcut değil ve `ARCHITECTURE.md` Katman 4'ü "Backtest / Validation Layer" olarak birleşik tanımlar).

**LOCKED:** yukarıdaki compose-not-duplicate prensibi ve paket sınırı. Exact modül/dosya isimleri bu dokümanda kilitlenmez — implementasyon mikro-adımına ertelenir.

## 5. Kapsam Sınırı (LOCKED)

Faz 6 foundation, mevcut backtest yeteneğini aşmaz:

```
tek symbol (single-symbol)
tek exchange / market_type / timeframe per run
timeframe: yalnızca 1h, 4h (mevcut candle_duration() üzerinden)
```

**Yasak:**

```
- portfolio / multi-asset validation
- validation-specific timeframe parser
- mevcut BacktestConfig/BacktestPolicy/run_backtest_from_store dışında paralel bir execution yolu
```

Faz 6'nın named scope'undaki hiçbir madde (walk-forward, CV, DSR, PBO, multiple-testing) multi-asset'i **zorunlu kılmaz** — bu nedenle foundation single-symbol kalır.

## 6. Temporal Window Primitive (LOCKED)

Genel bir zaman penceresi kontratı:

```
start: datetime   (true timezone-aware, UTC-instant semantics — datetime_to_epoch_us ile uyumlu)
end:   datetime   (aynı)

start < end        (zorunlu)
[start, end)        (half-open — repository-wide convention)
```

**Zorunlu:**

```
- genuine aware datetime (naive/pseudo-naive → ValueError)
- start < end
- pencere bir candle backtest'i için kullanılacaksa, start/end DATA_QUALITY_SPEC.md'nin
  grid-alignment kuralına (Bölüm "requested_start/requested_end grid'e hizalı olmalı")
  tabidir — hizasız boundary → açık ValueError, sessiz floor/ceil YOK
- sessiz clipping/normalization/sorting YOK
```

Yeni bir datetime/epoch primitive'i yazılmaz — mevcut `datetime_to_epoch_us`/`epoch_us_to_datetime` reuse edilir (repo-wide convention, `FundingCoverageInterval` ile aynı desen).

Exact production class adı bu dokümanda kilitlenmez — implementasyon mikro-adımına ertelenir (Bölüm 21).

## 7. IS / OOS Split Contract (LOCKED)

**Semantik roller:**

```
IS (in-sample):   research / fitting / candidate-selection'a görünür veri
OOS (out-of-sample): dondurulmuş (frozen) bir candidate/policy kararını üreten
                      seçim sürecine görünmemiş olması gereken veri
```

**Zorunlu kural:**

```
IS ve OOS zaman aralıkları OVERLAP EDEMEZ.
```

Overlap → **açık `ValueError`** — sessiz clip/trim YOK (Bölüm 6, DATA_QUALITY_SPEC.md/FUNDING_SPEC.md'nin "no silent repair" disipliniyle tutarlı).

**Gap kuralı (LOCKED — seçenek A):** IS ile OOS arasında bir zaman boşluğu (`IS.end < OOS.start`) **legal ama optional**'dır — ne zorunlu, ne yasaktır. Tam adjacency (`IS.end == OOS.start`) de legal'dir.

**Gerekçe (neden A, neden B/C değil):**
- **B (gap yasak)** gereksiz kısıtlayıcı olurdu — Bölüm 17'de tanımlanan formal embargo semantics'i (bir label/outcome-horizon kavramına bağlı) ileride tam olarak bu tür bir gap'i kullanacaktır; şimdiden gap'i yasaklamak, o gelecekteki ihtiyacı foundation seviyesinde imkânsız kılardı.
- **C (gap yalnızca embargo olarak temsil edilsin)** kavramları erken karıştırırdı: keyfi bir temporal gap ile formal ML purge/embargo (label-horizon'a bağlı, information-leakage-özel bir kavram) **aynı şey değildir** (Bölüm 17). Foundation seviyesinde yalnızca "gap legal, opsiyonel, embargo semantics'i taşımaz" denir; embargo kendi bağımlılık zincirini bekler.

**Alignment:** her iki aralık da Bölüm 6'nın grid-alignment kuralına tabidir.

## 8. KRİTİK — Evaluation Window vs. Context/Warm-up Window

Bu bölüm, bu spec'in en önemli açık-bırakılan kararıdır ve **sahte bir çözüm icat edilmez.**

**Durum (FAZ6A MS3/MS4 sonrası — LOCKED):** Bu bölümün açtığı karar artık kilitlenmiştir — exact mekanizma **Bölüm 8.3**'te LOCKED olarak tanımlanır. Bölüm 8/8.1/8.2'nin geri kalanı, MS1'in orijinal açık-problem analizini ve MS3'ün bu analizden B2'yi nasıl seçtiğini gösteren **tarihsel kayıt** olarak korunur — bu analiz olmadan B2'nin gerekçesi anlaşılamaz.

**Problem:** Bir gelecekteki policy (örn. 50-bar moving average, 100-bar momentum, rolling volatility) `OOS.start` anında **legal, geçmiş** (lookahead değil) candle'lara ihtiyaç duyabilir — bu candle'lar `OOS.start`'tan öncedir ama gelecekte değildir, dolayısıyla bunlara erişim anti-lookahead'i ihlal etmez.

**Mevcut API bunu ayıramaz** (Bölüm 9'da source'tan doğrulanmıştır): `run_backtest_from_store(requested_start=OOS.start, ...)` çağrısı, policy'ye yalnızca `OOS.start`'tan başlayan candle'ları gösterir — daha öncesi hiç görünmez. Diğer yandan, eğer `requested_start`'ı geriye (`context_start < OOS.start`) çekersek, mevcut replay loop'u context candle'larını da **ekonomik olarak** işler: ilk context candle'ından itibaren fill/cost/realized-PnL/equity-point üretilmeye başlar — çünkü `run_backtest_replay`'ın döngüsü, "yalnızca görünürlük, trade yok" diye bir ayrı mod tanımıyor; her candle hem policy'ye görünür hem de execution/accounting'e tabidir.

**Sonuç:** mevcut API, "geçmişi gör ama yalnızca `OOS.start`'tan itibaren skorla" ayrımını **temiz bir şekilde ifade edemez.**

**Bu MS1 sorunu çözmüyordu; FAZ6A MS3 pre-flight'ı, MS4 kilidi ve sonrasındaki Layer-1 implementasyonu artık çözer (Bölüm 8.3).** Mekanizma (B2) LOCKED'dır ve Layer-1 için **IMPLEMENTED + TESTED**'dır:

- **Layer-1 implementasyonu tamamlandı** — `run_backtest_replay`/`run_backtest_from_store` artık `evaluation_start: datetime | None = None` üzerinden context/evaluation ayrımını bilir (Bölüm 8.3.11, 23; Bölüm 9'daki audit bulgusu artık tarihsel/RESOLVED'dır).
- Bu, **generic/çok-pencereli (Layer-2) bir OOS runner**'ın inşasını artık warm-up mekanizması yüzünden değil, ayrı bir policy-instance-freshness ihtiyacı yüzünden bloke eder (Bölüm 8.3.6, 13).
- Bu, **temporal-window primitive**'inin (Bölüm 6/7, MS2'de implement edildi) inşasını hiçbir zaman bloke etmedi — o primitive tamamen pure/store-free'dir ve bu sorundan bağımsızdır.
- Dedicated **"OOS Context/Warm-up API Pre-flight"** mikro-adımı (FAZ6A MS3) tamamlandı; exact mekanizma bu dokümanda (Bölüm 8.3) MS4 ile kilitlendi; Layer-1 implementasyonu (canonical replay + store-runner) da tamamlandı (bkz. Bölüm 23) — geriye yalnızca Layer-2 orchestration kalır.

### 8.1 Olası Gelecek Tasarımlar (Analiz — Kilitlenmez)

| Seçenek | Açıklama | Artı | Eksi |
|---|---|---|---|
| A | OOS policy yalnızca OOS candle'larını görür | Basit, sıfır engine değişikliği, kontaminasyon riski sıfır | Lookback gerektiren stratejileri OOS başında kırar (ilk N candle context'siz kalır) |
| B | Runner `context_start < evaluation_start` kabul eder; ama fill/cost/realized-PnL/equity yalnızca `evaluation_start`'tan itibaren sayılır | Ekonomik olarak doğru, generic, herhangi bir policy'nin lookback'ini destekler | `replay.py`'a additive ama gerçek bir mimari değişiklik gerektirir (yeni `evaluation_start` sınırı) |
| C | Policy'ye ayrı, salt-okunur bir warmup candle sequence enjekte edilir | `replay.py`'ı değiştirmez | `PolicyContext`/`BacktestPolicy` signature'ını değiştirir — bugünkü `target_position(context)` sözleşmesini genişletir |
| D | Gelecekteki bir Feature/Research katmanı (ARCHITECTURE.md Katman 2/3) lookback'i önceden hesaplayıp policy'ye ham candle yerine feature-value verir | Warm-up sorununu bu katmandan tamamen kaldırır | Henüz var olmayan bir katmana bağımlı; Faz 6'nın kapsamı değil |
| E | Policy kendisi, yeterli bar birikene kadar FLAT/NO-TRADE döner | Sıfır engine/spec değişikliği, sıfır kontaminasyon riski | Her OOS penceresinin başında gerçek değerlendirme süresi "israf" edilir; policy yazarının disiplinine bağımlı |

**MS1 zamanındaki durum (tarihsel): leading direction B, NOT LOCKED.** Gerekçe: B, tek genel, policy-agnostik ve bu repo'nun mevcut additive-extension convention'ıyla (örn. `funding_events`/`funding_model`'in `run_backtest_replay`'a additive keyword-only, default'ta davranışı değiştirmeyen parametreler olarak eklenmesi — FUNDING-SPEC MS10) tutarlı görünen bir çözümdür. Ama: C, `BacktestPolicy`'nin public contract'ını genişletir (daha invaziv); D henüz var olmayan bir katmana bağımlıdır; A ve E foundation'da **hâlâ kullanılabilir** (bkz. Bölüm 11) ve sıfır engine değişikliği gerektirir. **Bu MS1 analizi B'yi implement etmiyordu** — yalnızca o zamanki en olası yönü kaydediyordu.

**Sonuç (FAZ6A MS3/MS4 — LOCKED):** FAZ6A MS3, bu beş seçeneği (A/B/C/D/E) tam olarak karşılaştırdı ve **B2**'yi (B'nin exact, context-fazında sıfır policy çağrısı yapan varyantı) seçti; **FAZ6A MS4 bu seçimi Bölüm 8.3'te LOCKED olarak kilitledi.** A/C/D/E generic çözüm olarak elenmiştir — A ve E, B2'nin özel durumları/fallback'leri olarak hâlâ mevcuttur (bkz. Bölüm 8.3). Bu tablo ve yukarıdaki gerekçe yalnızca tarihsel karşılaştırmayı korumak için burada bırakılır.

### 8.2 Warm-up için Data Quality (LOCKED — Bölüm 8.3'ün parçası)

Context/warm-up candle'ları (Bölüm 8.3'te LOCKED B2 mekanizmasıyla) desteklendiğinde, bunlar da:

```
- finalized (partial/live candle YOK)
- quality-gated (aynı candle quality gate'ten geçer, bypass YOK)
- aynı partition (exchange/market_type/symbol/timeframe)
- doğru sırada, future data YOK
```

olmalıdır — mevcut `prepare_backtest_dataset`'in candle path'i için zaten geçerli olan kural, warm-up candle'ları için de **istisnasız** uygulanır.

**Funding context sorusu:** warm-up, funding history'ye ihtiyaç duyar mı? **Hayır** (Bölüm 8.3'te LOCKED) — mevcut `BacktestPolicy.target_position(context: PolicyContext)` funding'i hiç görmez (`PolicyContext` yalnızca `as_of_time` + candle prefix taşır — bkz. `backtest/policy.py`). Feature-context verisi (candle lookback) ile ekonomik-funding-settlement verisi **ayrı kavramlardır**; bu spec bir feature sistemi icat etmez.

### 8.3 LOCKED Mechanism — B2 (FAZ6A MS3 Pre-flight + MS4 Spec-Lock)

**Durum: LOCKED (mimari/tasarım) VE Layer-1 için IMPLEMENTED + TESTED.** Bu bölüm, Bölüm 8/8.1'in açık bıraktığı kararı kilitler VE bu kararın Layer-1 (tek-pencere context-aware canonical replay + store-backed composition) implementasyonunun exact şeklini kaydeder — `run_backtest_replay`/`run_backtest_from_store` artık `evaluation_start: datetime | None = None` üzerinden bu bölümdeki ayrımı bilir, kendi regression suite'i ile test edilmiştir (Bölüm 8.3.11, 23; Bölüm 9 artık tarihsel/RESOLVED). Layer-2 (çok-pencereli orchestrator), zero-context için artık **IMPLEMENTED + TESTED**'dır (`run_rolling_backtest_from_store`, bkz. Bölüm 8.3.6, 13, 23, 28.C — 12/12); context-aware (non-zero-context) bir Layer-2 varyantı **HENÜZ implement edilmemiştir**.

**8.3.1 Context / Evaluation Aralıkları**

```
Context range:    [context_start, evaluation_start)
Evaluation range: [evaluation_start, evaluation_end)
```

Her iki aralık da Bölüm 6'nın grid-alignment kuralına tabidir; `evaluation_start` ayrıca yüklenen candle sequence'inde gerçek bir candle `open_time`'ına denk gelmelidir (contiguous/gapless dataset varsayımı altında bu, grid-alignment + range-içi-olma'dan otomatik sağlanır — ayrı bir arama gerekmez).

```
context_start == evaluation_start   LEGAL (sıfır context — Bölüm 8.1 seçenek A'nın
                                     özel durumu, ayrı bir kod yolu değildir)
context_start > evaluation_start    INVALID
evaluation_start >= evaluation_end  INVALID
```

`context_start`, mevcut `requested_start`/`candles[0].open_time` parametresiyle **aynı isimdir** — yeni bir "context_start" parametre adı gerekmez. `evaluation_end`, mevcut `requested_end` ile aynıdır. Yalnızca `evaluation_start` **yeni** bir sınırdır.

**8.3.2 Context Candle Kuralları (Koşulsuz)**

Context candle'lar yalnızca canonical historical **INFORMATION**'dır:

```
- quality-gated (aynı candle quality gate, bypass YOK)
- normal şekilde ordered/finalized (partial/live candle YOK)
- evaluation başladıktan sonra PolicyContext.candles'ta görünebilir
- policy.target_position ÇAĞRISI ALMAZLAR (koşulsuz — bkz. 8.3.5)
- SIFIR fill yaratırlar
- SIFIR transaction cost yaratırlar
- SIFIR funding cost yaratırlar
- SIFIR EquityPoint yaratırlar
- SIFIR skorlanmış PnL yaratırlar
- SIFIR ekonomik account state mutasyonu yaratırlar
```

Bu, Bölüm 11'in "context candle hiçbir pending fill yaratamaz" kuralının **koşulsuz** okunuşuyla birebir tutarlıdır (kuralda "evaluation_start'tan önce" gibi bir sınırlayıcı yoktur — kural mutlaktır). **Bu nedenle bir cancellation/iptal mekanizması tasarlanmaz:** son context candle'ın sinyali "iptal edilmez," çünkü o sinyal hiç **üretilmez** — policy context fazında hiç çağrılmaz.

**Yanlış tanım (KULLANILMAZ):** "stratejiyi warm-up sırasında çalıştır ama trade'lerini yok say." **Doğru tanım:** "policy, yalnızca context-only candle'lar için çağrılmaz."

**8.3.3 Fresh Ekonomik State**

`AccountState`, `open_time >= evaluation_start` olan ilk candle'ın loop iterasyonuna ulaşıldığı anda **fresh** olarak inşa edilir:

```
cash = config.initial_cash
position = flat (0)
realized_pnl = 0
```

Context iterasyonlarında hiçbir `AccountState` yaratılmaz/taşınmaz — context candle'ların hiçbiri ekonomik mekanizmaya (funding sweep, equity mark, execution) hiç girmediği için taşınacak bir state zaten yoktur.

**8.3.4 İlk Policy Kararı ve İlk Olası Fill**

`evaluation_start = T`, ilk evaluation candle `E0.open_time = T` olsun.

```
İlk policy çağrısı: feature_availability_time(E0) = T + candle_duration
    (1h için T + 1h, 4h için T + 4h)

E0'ın PolicyContext'i: tüm legal context candle'lar + E0
    (mevcut candles[:i+1] prefix semantics'i, DEĞİŞMEDEN)

İlk olası fill: mevcut "signal -> NEXT candle OPEN" kuralı gereği,
    E0'ın kararı E1'in (ikinci evaluation candle) OPEN'ında fill olur
    = T + candle_duration

Asla evaluation_start'ın kendisinde değil.
```

Account, `[evaluation_start, evaluation_start + candle_duration)` boyunca **flat** kalır (fill olmadığı için).

**8.3.5 Policy Semantic Precondition — Type-H / Type-I**

Context-aware/OOS evaluation'ın doğruluğu yalnızca **history-reconstructible (Type-H)** bir `BacktestPolicy` için garantilidir:

```
Type-H (history-reconstructible): target_position(context)'in ürettiği
ekonomik olarak anlamlı karar TAMAMEN şunlardan türetilebilir:
    - context.as_of_time
    - context.candles
    - policy'nin kendi frozen/immutable konfigürasyonu

Önceki target_position() çağrılarının, doğru karar için gerekli
ekonomik olarak anlamlı gizli (hidden) state biriktirmiş olmasına
İHTİYAÇ DUYMAZ.
```

```
Type-I (incremental-state): doğru kararı, önceki target_position()
çağrılarıyla biriktirilmiş mutable internal state'e bağımlıdır.
```

**Type-I bir policy, B2 tarafından otomatik olarak warm-up edilmez** — context candle'lar için hiç çağrılmadığından, ilk evaluation çağrısında internal accumulator'ı hâlâ `__init__` default'undadır. Bu, Option A'nın cold-start sorununun farklı bir biçimde geri gelmesidir.

**Bu KÜRESEL bir `BacktestPolicy` contract değişikliği DEĞİLDİR.** `BACKTEST_SPEC.md`/`backtest/policy.py`'nin mevcut, zaten shipped, Faz-4-locked contract'ı **değişmez** — Faz 4/5 policy'leri ve normal (context-aware olmayan) backtest kullanımı bundan **hiç etkilenmez.** History-reconstructibility, yalnızca **validation/context-aware-evaluation precondition'ıdır** (Bölüm 7'nin seçimi: global değil, validation-only).

**Mekanik olarak enforce edilemez:** engine, keyfi bir Python objesinin `target_position` metodunun geçmiş çağrılara bağımlı olup olmadığını runtime'da güvenilir şekilde tespit edemez (genel amaçlı purity-checking, karar verilemez bir problemdir). Bu nedenle:

```
deepcopy, introspection, hidden reset, otomatik warm-up çağrıları,
otomatik state-detection — HİÇBİRİ engine garantisi olarak
ÖNERİLMEZ/TASARLANMAZ.
```

History-reconstructibility, **açık bir caller/policy-author sorumluluğudur** — Bölüm 19'un zaten kurduğu "engine-enforceable vs. research-process disiplini" ayrımıyla aynı kategoridedir (bkz. Bölüm 19 güncellemesi).

**Type-I policy'ler global olarak yasaklanmaz** — normal (context-aware olmayan) backtest'lerde tamamen legaldir; yalnızca context-aware evaluation'ın otomatik warm-up garantisinden **yararlanamazlar.**

**8.3.6 Policy Instance Freshness — LOCKED (Factory-Based Mekanizma, FAZ6B MS1) VE Zero-Context Layer-2 İçin IMPLEMENTED + TESTED (FAZ6B MS2)**

History-reconstructibility (8.3.5) ile policy-instance-freshness **iki farklı sorundur:**

```
(A) History-reconstructibility: TEK bir context-aware evaluation'ın
    context'i, context-fazında policy çağrısı yapmadan doğru şekilde
    tüketebilmesi için gereklidir (Bölüm 8.3.5).

(B) Fresh policy instance: bağımsız evaluation'ların birbirinden
    gizli state DEVRALMAMASI için gereklidir.
```

```
Layer 1 — TEK context-aware canonical replay run'ı
    (bir caller-supplied policy instance):
    fresh instance sorumluluğu CALLER disiplinindedir. Bir policy
    objesi zaten bir run içinde birçok candle boyunca çağrılır —
    context-fazının çağrılmaması bu sorunu DEĞİŞTİRMEZ.

Layer 2 — Bağımsız pencereler üzerinde çalışan çok-pencereli
    (multi-window) validation orchestrator:
    aynı mutable policy instance'ının pencereler arası yeniden
    kullanımı GÜVENSİZDİR.
```

**Durum: LOCKED (mimari/tasarım) VE zero-context Layer-2 orchestrator için IMPLEMENTED + TESTED.** Bu bölüm, yukarıdaki (B)'nin exact mekanizmasını kilitler VE bu kararın **zero-context Layer-2** (bkz. Bölüm 13) implementasyonunun exact şeklini kaydeder — `src/crypto_quant_lab/validation/rolling.py`'deki `run_rolling_backtest_from_store`, `policy_factory: Callable[[], BacktestPolicy]` üzerinden bu bölümdeki mekanizmayı bilir, kendi regression suite'i ile test edilmiştir (FAZ6B MS2 implementasyonu `c363267`, test-hardening `c4af87c`; bkz. Bölüm 23, 28.C). **Non-zero-context (context-aware) bir Layer-2 varyantı bu implementasyonun kapsamında DEĞİLDİR** — ayrı bir spec-lock + implementasyon mikro-adımı gerektirir (bkz. bu bölümün altındaki "Implementasyon Durumu" notu).

**Karşılaştırılan alternatifler:**

```
1. Caller-discipline-only (mekanik enforcement yok) — REDDEDİLDİ (tek
   mekanizma olarak): Layer 2, tanım gereği caller'ın doğrudan görmediği
   bir orchestration loop'udur (pencereler otomatik ilerler) — Layer 1'in
   "tek run, tek caller" varsayımı burada geçerli değildir; sessiz
   cross-window leakage riski (Bölüm 19) mekanik bir kontrol olmadan
   tespit edilemez kalır.

2. Global BacktestPolicy.reset() zorunluluğu — REDDEDİLDİ: Bölüm 8.3.5
   zaten Faz-4-locked BacktestPolicy contract'ının KÜRESEL olarak
   değişmeyeceğini kilitler; yeni bir zorunlu metot eklemek bu kilidi
   ihlal eder ve mevcut/gelecekteki tüm context-aware-olmayan policy
   kullanımını (Faz 4/5) geriye dönük olarak kırar.

3. Clone/copy-based duplication (deepcopy/copy ile pencere başına bir
   kopya) — REDDEDİLDİ: Bölüm 8.3.5, deepcopy/introspection/hidden
   reset'i engine garantisi olarak zaten "ÖNERİLMEZ/TASARLANMAZ" diye
   kilitler — aynı gerekçe geçerlidir (keyfi bir Python objesinin doğru
   şekilde kopyalanabileceği genel olarak garanti edilemez).

4. Factory-based per-window construction (Callable[[], BacktestPolicy],
   orchestrator tarafından pencere başına bir kez çağrılır) — LOCKED.
   Gerekçe: mevcut repo'nun additive-extension convention'ıyla tutarlı
   (yeni bir dependency-injection noktası, mevcut hiçbir contract'ı
   değiştirmez); object identity üzerinden mekanik olarak enforce
   edilebilir (aşağıda); policy-author'a normal `__init__`'ini kullanma
   özgürlüğü bırakır (Bölüm 8.3.5'in caller-disiplini prensibiyle
   tutarlı).

5. Orchestrator'ın hazır (prebuilt) tek bir policy instance kabul etmesi
   — REDDEDİLDİ (Layer-2'nin TEK girdisi olarak): bu tam olarak yukarıdaki
   GÜVENSİZ senaryodur — aynı instance'ın pencereler arası paylaşılmasına
   yapısal olarak izin verir. Layer 1'in mevcut `run_backtest_replay`/
   `run_backtest_from_store` API'si için (tek pencere, tek caller-supplied
   instance) hâlâ doğru ve DEĞİŞMEDEN kalır (aşağıda) — yalnızca Layer-2
   orchestrator'ın tek girdisi olarak reddedilir.
```

**Locked mekanizma: factory-based per-window construction.**

**Factory şekli:**

```
Kavramsal şekil: Callable[[], BacktestPolicy]
```

Bu mikro-adım yalnızca kavramı kilitler — bir type alias, production parametre adı, veya kod eklenmez. Dokümanda bu mekanizmadan bahsederken `policy_factory` ismi kullanılır (Bölüm 18, 19 ile tutarlı) — exact production parametre/argüman adı implementasyon mikro-adımına ertelenir (Bölüm 8.3.11'in `evaluation_start` için izlediği "kavram kilitlenir, exact isim implementasyonda finalize edilir" precedent'iyle tutarlı).

**Ownership ve invocation (LOCKED invariant'lar):**

```
- Gelecekteki Layer-2 orchestrator, paylaşılan/hazır (prebuilt) mutable
  bir policy instance DEĞİL, factory-benzeri bir construction dependency
  kabul eder.
- Orchestrator, factory'i HER bağımsız evaluation penceresi için TAM
  OLARAK BİR KEZ çağırır.
- Çağrı, o pencerenin execution'ından HEMEN ÖNCE, canonical pencere
  sırasında gerçekleşir.
- Dönen policy instance, YALNIZCA o pencereye aittir.
- Orchestrator, bir instance'ı başka bir pencerede yeniden kullanmak
  için CACHE'LEMEZ.
- Orchestrator, fresh construction'ın YERİNE reset()/clone()/copy()/
  deepcopy() ÇAĞIRMAZ (Bölüm 8.3.5'in deepcopy/hidden-reset red
  gerekçesiyle tutarlı).
- Mevcut tek-pencere API'ler (`run_backtest_replay`,
  `run_backtest_from_store`) bir `BacktestPolicy` instance'ını
  DEĞİŞMEDEN kabul etmeye devam eder.
- `run_backtest_replay` ve `run_backtest_from_store`, bu kontratın
  parçası olarak bir factory ALMAZ — factory yalnızca gelecekteki
  Layer-2 orchestrator'ın girdisidir.
- Küresel `BacktestPolicy` Protocol'ü (Bölüm 8.3.5, `backtest/policy.py`)
  DEĞİŞMEDEN kalır.
```

**Mekanik enforcement (LOCKED — gelecekteki implementasyon zorunluluğu):**

```
Gelecekteki Layer-2 implementasyonu, bir factory birden fazla pencere
için AYNI objeyi döndürdüğünde bunu MEKANİK OLARAK REDDETMELİDİR.
```

- Reuse tespiti **object identity** (`is`/`id()`) üzerinden yapılır, **equality** (`==`) üzerinden DEĞİL — iki farklı instance'ın eşit karşılaştırılması (örn. aynı config'e sahip iki `@dataclass` policy) legal ve beklenen bir durumdur; yasak olan yalnızca AYNI OBJENİN yeniden kullanılmasıdır.
- Dönen instance'lar, orchestration süresince **strongly retained** tutulmalı (veya eşdeğer bir identity-safe mekanizma kullanılmalı) — CPython'da serbest bırakılan bir objenin `id()` değeri başka bir objeye yeniden atanabildiğinden (`id()` reuse), yalnızca zayıf/geçici referanslarla yapılan bir `id()` karşılaştırması reuse tespitini yanlış-negatif üretebilir.
- Reuse edilmiş bir instance, **etkilenen pencere execute edilmeden ÖNCE** fail eder.
- Exact gelecekteki exception type/mesajı bu dokümanda kilitlenmez — implementasyon mikro-adımının kendi regression suite'i bunu deterministik olarak tanımlar/test eder (mevcut proje TypeError/ValueError konvansiyonuyla tutarlı); bu docs-only mikro-adım repository-wide yeni bir exception hiyerarşisi icat etmez.

**Factory output validation (LOCKED):**

```
- Her factory sonucu, yapısal olarak çağrılabilir bir target_position
  SAĞLAMALIDIR (BacktestPolicy Protocol, Bölüm 8.3.5).
- Geçersiz bir sonuç, etkilenen pencere execute edilmeden ÖNCE fail eder.
- Bir factory exception'ı sessizce YUTULMAZ veya başarılı/kısmi bir
  validation sonucuna dönüştürülmez.
- Gelecekteki implementasyon, deterministik fail-fast davranışı
  TANIMLAMALIDIR.
```

**Açıkça iddia EDİLMEZ:** yapısal output validation (çağrılabilir `target_position` varlığı), bir policy'nin semantik doğruluğunu, Type-H (history-reconstructible) niteliğini, veya Type-I state-management doğruluğunu **kanıtlamaz** — bu yalnızca bir shape/duck-type kontrolüdür, Bölüm 8.3.5'in zaten kurduğu "mekanik olarak enforce edilemez" sınırıyla birebir tutarlıdır.

**Failure ve partial-execution sınırı (LOCKED):**

```
- Factory construction pencere-başına ve lazy'dir — TÜM pencereler için
  TÜM policy'lerin herhangi bir backtest'ten ÖNCE eagerly inşa edilmesi
  DEĞİLDİR.
- Pencere N için construction veya validation fail ederse, pencere N ve
  sonrasındaki pencereler EXECUTE EDİLMEZ.
- Daha ÖNCEKİ pencereler zaten execute edilmiş OLABİLİR.
- Rollback/transactional bir orchestration garantisi TANITILMAZ.
- Bu mikro-adım, zaten locked bir spec maddesi tarafından zorunlu
  kılınmadıkça, kısmi sonuçların persistence'ını TASARLAMAZ.
```

**Type-H / Type-I sınırı (LOCKED — Bölüm 8.3.5'ten ayrı ama ilişkili):**

```
- Fresh construction, bağımsız pencereler arasında mutable state
  leakage'ı ÖNLER.
- Freshness, bir policy'nin Type-H (history-reconstructible) olduğunu
  KANITLAMAZ.
- Freshness, Type-I internal state'i OTOMATİK OLARAK ISITMAZ.
- Type-H, gerektiği yerde açık bir semantic caller precondition olarak
  KALIR (Bölüm 8.3.5).
- Type-I otomatik warm-up DESTEKLENMEZ ve burada TANITILMAZ (Bölüm
  8.3.5'in "HİÇBİRİ engine garantisi olarak ÖNERİLMEZ/TASARLANMAZ"
  kilidiyle birebir tutarlı).
- Hiçbir context candle, yalnızca bir Type-I policy'yi "ısıtmak" için
  skorlanamaz veya evaluated account state'i mutate etmek için
  kullanılamaz (Bölüm 8.3.2, 8.3.3'ün koşulsuz kurallarıyla birebir
  tutarlı) — freshness mekanizması bu kuralları hiçbir şekilde gevşetmez.
```

**Determinism ve compatibility (LOCKED):**

```
- Sabit bir canonical pencere sırası için factory çağrı sayısı ve sırası
  DETERMİNİSTİKTİR.
- Her pencerenin execution'ı sırasında TAM OLARAK bir policy instance
  kullanılır.
- Layer-1 evaluation_start davranışı (Bölüm 8.3.11) DEĞİŞMEDEN kalır.
- BacktestResult, CostModel, FundingModel, replay semantics, store
  semantics, ve temporal-window primitive'leri (Bölüm 6/7,
  validation/windows.py) bu contract-lock tarafından DEĞİŞTİRİLMEZ.
- Tek-pencere caller'lar (mevcut run_backtest_replay/
  run_backtest_from_store kullanıcıları) geriye dönük uyumlu kalır —
  hiçbir mevcut çağrı sitesi bu mikro-adımdan etkilenmez.
```

**Implementasyon Durumu — Zero-Context Layer-2 İçin IMPLEMENTED + TESTED (FAZ6B MS2, bkz. Bölüm 23, 28.C):**

Yukarıdaki mekanizma `src/crypto_quant_lab/validation/rolling.py`'de implement edilmiştir (commit `c363267`; regression-hardening commit `c4af87c`), kendi regression suite'i `tests/test_validation_rolling_backtest.py`'de (28 test, tümü PASS). Public production şekli:

```
WindowResult(window: TemporalWindow, result: BacktestResult)   # frozen, slots

run_rolling_backtest_from_store(
    store, windows: tuple[TemporalWindow, ...], *,
    policy_factory: Callable[[], BacktestPolicy],
    exchange, market_type, symbol, timeframe, as_of_time, config, cost_model,
    funding_required=False, funding_store=None, funding_model=None,
) -> tuple[WindowResult, ...]
```

Yukarıdaki her LOCKED invariant, bu implementasyon için kanıtlanmıştır:

```
- factory pencere-başına tam olarak bir kez, lazy, sıralı çağrılır
  (test_exactly_one_factory_call_per_window_in_order)
- her sonuç, çağrılabilir target_position için yapısal olarak kontrol
  edilir; geçersizse etkilenen pencere I/O'sundan ÖNCE TypeError
  (test_invalid_factory_output_is_rejected_before_affected_window_runs)
- kabul edilen instance'lar orchestration boyunca strongly retained
  tutulur — weakref-tabanlı regression bunu doğrudan kanıtlar
  (test_prior_accepted_policies_remain_strongly_retained_throughout_orchestration)
- reuse tespiti yalnızca object identity (`is`) üzerinden yapılır,
  equality/hashing DEĞİL; aynı obje reuse edilirse etkilenen pencere
  I/O'sundan ÖNCE ValueError
  (test_same_object_factory_output_is_rejected_before_affected_window_runs)
- distinct ama equality-eşit instance'lar kabul edilir
  (test_distinct_but_equality_equal_policy_instances_are_accepted)
- factory exception'ları wrap/swallow edilmeden, AYNI obje olarak
  propagate eder — object-identity ile kanıtlanmıştır
  (test_factory_exception_propagates_as_original_object)
- fail-fast: pencere N fail ederse N ve sonrası execute edilmez,
  önceki pencereler zaten execute edilmiş olabilir, rollback/partial-
  result YOK
  (test_earlier_windows_execute_and_no_subsequent_window_executes_on_failure)
- her başarılı pencere, tek bir run_backtest_from_store çağrısına
  delege eder — ikinci/forked bir replay engine yoktur
  (test_rolling_output_matches_direct_per_window_composition)
- mevcut tek-pencere API'ler (run_backtest_replay, run_backtest_from_store)
  ve küresel BacktestPolicy Protocol'ü DEĞİŞMEDEN kalır (git diff boş;
  tam regression suite 1386/1386 PASS)
```

**Kapsam sınırı (önemli):** bu implementasyon **zero-context**'tir — her pencere `evaluation_start = window.start` ile çalışır, yani context yoktur (bkz. Bölüm 8.3.1, 8.3.11, 13). Bu implementasyonun kanıtladığı şey, **`run_rolling_backtest_from_store` için** mekanik policy-freshness enforcement'ının doğru çalıştığıdır — repository-wide, arbitrary gelecekteki caller'lar veya gelecekteki context-aware Layer-2 varyantları için otomatik/global bir garanti DEĞİLDİR. Context-aware (non-zero-context, `context_start < evaluation_start`) Layer-2 pencereleri **implement edilmemiştir**; yeni bir passive context-window modeli bu mikro-adımda tanıtılmamıştır; böyle bir uzantı ayrı bir spec-lock + implementasyon mikro-adımı gerektirir.

**8.3.7 Funding Range ve Zamanlama**

```
Economic funding range: [evaluation_start, evaluation_end)
                         (context_start'tan DEĞİL)
```

Context'in ekonomik pozisyonu olmadığından context için funding coverage **gerekli değildir** — candle'lar context için yüklendi diye funding sessizce sorgulanmaz.

**Kesin ifade (yanlış anlaşılmayı önlemek için):** `event_time == evaluation_start` olan bir funding event, ekonomik aralığa **dahildir**, ama **evaluation_start'ın kendisinde ayrı bir replay tick YOKTUR** — sweep'ler yalnızca bir candle'ın `feature_availability_time`'ında olur. Böyle bir event, **ilk evaluation candle'ın kendi normal funding sweep'inde** (`feature_availability_time(E0) = evaluation_start + candle_duration`) tüketilir — mevcut mekanikle birebir aynı. Fresh `AccountState` o sweep anına kadar flat kaldığından, bu event'in maliyeti `LinearFundingModel`'in **zaten var olan flat-position sıfır-formül davranışıyla** sıfırdır — yeni bir özel-durum kodu YAZILMAZ.

```
"funding tam olarak evaluation_start'ta işlenir" YAZILMAZ — bu mevcut
replay mekaniğini yanlış tarif eder.
```

Aynı ilke, `evaluation_start + candle_duration`'daki (ilk olası fill anındaki) bir funding event için de geçerlidir: mevcut sıra — funding → mark → policy → fill — DEĞİŞMEDEN korunur; o funding, entering flat position'a karşı, ilk fill'den ÖNCE settle olur. Özel durum yoktur.

**8.3.8 Candle / Data Quality Aralığı**

```
Canonical candle dataset: [context_start, evaluation_end)
```

Dataset katmanı (`prepare_backtest_dataset`) yalnızca şundan sorumlu kalır:

```
- canonical candle retrieval/input
- range integrity, finalization, partition correctness, ordering
- timeframe cadence / data quality (grid-alignment, gap-free)
```

Dataset katmanı **ekonomik semantics kazanmaz** — hangi candle'ların ekonomik olduğuna, `AccountState`'in ne zaman başladığına, hangi candle'ların policy'yi tetikleyebileceğine karar VERMEZ. Bunlar replay/evaluation semantics'idir (8.3.1–8.3.6). `prepare_backtest_dataset`'e context/evaluation-farkında bir API değişikliği **bu MS4'te kilitlenmez** — yalnızca gerçek bir implementasyon incelemesi zorunlu bir ihtiyaç bulursa değerlendirilir.

Eksik/hizasız bir context candle → **FAIL** (aynı canonical quality gate, sessizce kısaltılmış warm-up YOK).

**8.3.9 IS/OOS Secrecy**

Context candle'lar tarihsel olarak IS aralığının içinden, IS/OOS research gap'inin içinden, veya IS'ten bile önceden gelebilir — bu **OOS secrecy'yi ihlal etmez**, çünkü context candle'lar tanım gereği (`open_time < evaluation_start`) her zaman evaluation_start'tan kesin olarak öncedir; hiçbir OOS-dönemi bilgisi yapısal olarak içeremezler. Ham, zaten kamuya açık geçmiş fiyat verisini indicator lookback olarak kullanmak, candidate **seçim sürecinin** OOS sonuçlarına/metriklerine erişmesinden (asıl leakage) kategorik olarak farklıdır (Bölüm 19/20). Pending bir IS ekonomik state/aksiyonu OOS'a **asla** geçemez (Bölüm 11) — bu context desteğiyle DEĞİŞMEZ.

**8.3.10 BacktestResult ve Equity Curve**

`BacktestResult`'ın şekli **DEĞİŞMEZ** (Bölüm 4, 21). `evaluation_start`'ta yapay/fabricated bir `EquityPoint` **eklenmez** — mevcut equity-mark semantics'i (candle availability başına bir örnek) korunur; ilk gerçek/sayılan `EquityPoint`, ilk evaluation candle'ın kendi availability anında (`evaluation_start + candle_duration`) doğal olarak ortaya çıkar. Bir baseline equity noktası ihtiyacı varsa, bu **gelecekteki bir metrics-contract konusudur** (Bölüm 15/16/22 Faz 6B) — bu MS4'ün kapsamı değildir.

Nihai `BacktestResult`'ın tüm ekonomik alanları (`final_cash`, `final_equity`, `total_realized_pnl`, `total_unrealized_pnl`, `total_cost`, `total_pnl`, `fill_count`, `trade_count`, `equity_curve`) yalnızca evaluation-fazı ekonomisinden türetilir — context, yalnızca policy'nin gördüğü INFORMATION'ı etkiler, ekonomik muhasebeye asla doğrudan katkıda bulunmaz.

**8.3.11 Canonical Replay Composition (IMPLEMENTED — Exact Signature Aşağıdaki Kavramsal API ile Birebir Eşleşir)**

```
Canonical run_backtest_replay TEK replay engine olarak kalır.
Context-aware evaluation, validation-specific bir replay loop
YARATAMAZ (Bölüm 4, 21) — additive, canonical replay'i GENİŞLETİR.
```

**IMPLEMENTED** (`src/crypto_quant_lab/backtest/replay.py`, `src/crypto_quant_lab/backtest/store_runner.py`) — exact şekil, aşağıdaki kavramsal API ile birebir örtüşür:

```
evaluation_start: datetime | None = None
```

`evaluation_start` `None` olduğunda (default), legacy replay semantics'i **DEĞİŞMEDEN** kalır — mevcut `run_backtest_replay`/`run_backtest_from_store` çağrıcıları, context/evaluation mekanizmasını hiç kullanmadıkları sürece **davranış değişikliği görmez** (regression testleriyle kanıtlanmıştır: `tests/test_backtest_replay_context_evaluation.py`, `tests/test_backtest_store_runner_context_evaluation.py`). Hiçbir mevcut çağrıcı sessizce context semantics'i almaz. Bu, `funding_events=()`/`funding_model=None` (FUNDING-SPEC MS10) ve `funding_required=False` (FUNDING-SPEC MS11) additive-parametre precedent'ıyla birebir tutarlıdır.

Bu MS4, exact positional/keyword parametre şeklini literal olarak kilitlememişti — gerçekleşen implementasyon, mevcut fonksiyon signature'larına (her iki fonksiyonda da keyword-only, additive, `funding_model`'den sonra) doğal şekilde oturan, buradaki kavramsal API ile birebir aynı şekli kullandı.

**Store-runner yönü — IMPLEMENTED:** `requested_start`, yüklenen context/evaluation dataset'inin başlangıcı (context_start) rolünü oynamaya devam eder; `requested_end` evaluation_end/yüklenen aralığın sonu olarak kalır; ayrı, açık bir `evaluation_start` parametresi information-history başlangıcını economic-start'tan ayırır — tek bir "start" parametresi iki anlamı üstlenmez. Store-runner: `[requested_start, requested_end)` üzerinden candle yükler/quality-gate'ler (`dataset.py` DEĞİŞMEDİ), `evaluation_start`'ı canonical replay'e geçirir, ve ekonomik funding'i yalnızca `[evaluation_start, gerçek prepared run end)` üzerinden sorgular/gate'ler. Ayrıca, raw `requested_end`'in gerçek/prepared `effective_end`'den daha geç olabileceği durumu ele almak için, candle I/O'dan **SONRA** ama funding I/O'dan **ÖNCE** ikinci bir doğrulama adımı (Stage B) uygular — bu, MS4'te öngörülmemiş ama implementasyon sırasında gerekli bulunan, minimal bir ek kontroldür (bkz. Bölüm 8.3.15).

**8.3.12 TemporalWindow / TemporalSplit İlişkisi**

`TemporalWindow` (MS2), context/evaluation aralıklarını caller-tarafı bir convenience olarak temsil edebilir (örn. iki `TemporalWindow` örneği) — **bu MS4'te MS2 modellerine hiçbir değişiklik yapılmaz.** Engine'in kendisi yalnızca skaler bir `evaluation_start: datetime` parametresine ihtiyaç duyar; yeni bir passive model **zorunlu değildir.**

`TemporalSplit` (IS/OOS research-selection boundary, Bölüm 7), context/evaluation ayrımı için **yeniden kullanılmaz/repurpose edilmez** — bunlar semantik olarak farklı kavramlardır: IS/OOS bir research-selection sınırıdır (iki bağımsız, simetrik pencere; overlap yasak, gap legal-ama-embargo-değil); context/evaluation ise TEK bir evaluation penceresine tabi, asimetrik bir information-support ilişkisidir (context, evaluation'ın bağımsız bir "ikinci" penceresi değil, salt onun bilgi girdisidir). Aynı sınıfı iki farklı kavram için kullanmak bu ayrımı karıştırırdı.

**8.3.13 Context Boundary — Explicit, Auto-Inference YOK**

```
Caller/research layer, açık bir context_start sınırı seçer.
```

Policy internals'tan (50 bar, 100 bar, rolling window periyodu, indicator period) otomatik lookback **inference edilmez/introspect edilmez.** Bu, validation'ı deterministik ve strategy-agnostic tutar (Bölüm 25/50-51'in genel prensibiyle tutarlı). `context_start`, `IS.start`/`IS.end`/`OOS.start - sabit gap`/embargo boundary'sine **otomatik bağlanmaz** — `TemporalSplit`'ten türetilmez; yalnız gereklilik, context'in evaluation_start'a göre historical olması ve quality kurallarından geçmesidir (Bölüm 8.3.1, 8.3.8).

**8.3.14 Future Data Güvenliği**

Her evaluation çağrısındaki `PolicyContext`, yalnızca o çağrının `as_of_time`'ında canonical olarak available olan candle'ları içerebilir — mevcut prefix semantics'i (`candles[:i+1]`, Bölüm 8.3.4) bunu zaten garanti eder. Context desteği **HISTORY'yi genişletir, FUTURE'ı değil** — hiçbir evaluation çağrısına tüm evaluation tuple'ı bir kerede açılmaz.

**8.3.15 Error Semantics (Kavramsal — Exact Mesaj Kilitlenmez)**

```
- evaluation_start genuine aware datetime olmalı (TypeError/ValueError,
  mevcut proje konvansiyonu)
- evaluation_start grid-aligned olmalı (ValueError)
- evaluation_start yüklenen [context_start, evaluation_end) aralığına
  düşmeli (ValueError)
- context_start > evaluation_start → ValueError
- evaluation_start >= evaluation_end → ValueError
- eksik/geçersiz context candle → canonical candle quality gate
  üzerinden FAIL (Bölüm 8.3.8)
- eksik gerekli economic funding coverage → canonical funding quality
  gate üzerinden FAIL (Bölüm 8.3.7, 24)
- store-backed çağrılarda: raw requested_end'e göre legal görünen bir
  evaluation_start, gerçek/prepared candle tuple'ının effective run
  end'i daha erken olduğunda geçersiz olabilir (effective_end clamp'i
  nedeniyle) — bu durum candle I/O'dan SONRA, funding I/O'dan ÖNCE
  ayrıca kontrol edilir (Stage B, bkz. 8.3.11) → ValueError
```

Exact exception mesaj string'leri bu dokümanda kilitlenmez — mevcut proje TypeError (yanlış tip) / ValueError (yanlış değer) konvansiyonu kullanılır. Bu liste artık implement edilmiş/test edilmiş davranışı doğru şekilde tarif eder (bkz. Bölüm 8.3.11, 23).

## 9. Current API Limitation Audit (Bölüm 8'in Kaynak Doğrulaması) — TARİHSEL, RESOLVED

**Durum: RESOLVED (Layer-1 implementasyonuyla, bkz. Bölüm 8.3.11, 23).** Bu bölüm, B2 mekanizmasının Layer-1 implementasyonundan **ÖNCEKİ** (MS1 zamanındaki) kaynak-kod audit bulgusunu, B2'nin gerekçesini korumak için **tarihsel kayıt** olarak saklar. `run_backtest_replay`/`run_backtest_from_store` artık bu bölümdeki ayrımı `evaluation_start` parametresi üzerinden bilir (implement edildi + test edildi).

`src/crypto_quant_lab/backtest/store_runner.py` ve `replay.py`'dan (implementasyon **ÖNCESİ**, MS1 zamanında) doğrulanmıştır:

- `run_backtest_from_store(requested_start=X, requested_end=Y, ...)` → `prepare_backtest_dataset` yalnızca `[X, report.effective_end)` candle'larını query eder ve döner. `X`'ten önceki hiçbir candle asla mevcut değildir.
- `run_backtest_replay`'ın ana döngüsü (`for i, candle in enumerate(candles): ...`), **her** candle için sırasıyla funding sweep → equity mark → `PolicyContext` → policy call → (son candle değilse) execution çalıştırır. Loop'ta "yalnızca görünürlük, trade yok" diye ayrı bir mod **yoktur** — sequence'e giren her candle hem `PolicyContext.candles`'a hem de execution/equity mark mekanizmasına eşit şekilde tabidir.

**Sonuç (tarihsel audit finding, B2 Layer-1 implementasyonuyla RESOLVED):**

```
Mevcut (implementasyon ÖNCESİ) API, pre-evaluation lookback history'yi
WITHOUT contamination sağlayamıyordu.
```

Bu, Bölüm 8.3'te LOCKED olan B2 mekanizması gibi bir additive extension'a ihtiyaç duyuyordu — bu extension artık implement edilmiştir (bkz. Bölüm 8.3.11, 23) ve bu audit bulgusu artık geçerli değildir. **Sahte destek icat edilmemişti; gerçek destek şimdi implement edilmiştir.**

## 10. State Carryover — Üç Ayrı Kavram (LOCKED)

Bu üç kavram **aynı şey değildir** ve karıştırılmaz:

```
1. Historical context carry-in     — policy'nin GÖRDÜĞÜ geçmiş candle'lar (Bölüm 8)
2. Economic account-state carry-in — cash/position/realized_pnl IS'ten OOS'a taşınır mı
3. Parameter/candidate carry-in    — IS'te seçilen dondurulmuş candidate/parametre OOS'a taşınır mı
```

**(1) Historical context:** gereklidir (Bölüm 8); exact mekanizması **Bölüm 8.3'te LOCKED**'dır ve Layer-1 için **implement edilmiştir** (bkz. `replay.py`/`store_runner.py`, Bölüm 8.3.11, 23).

**(2) Economic account-state:** Bölüm 11'de **fresh (A)** olarak kilitlenir — foundation için.

**(3) Parameter/candidate carry-in:** candidate abstraction var olduğunda **zorunlu** olacaktır (Bölüm 18) — dondurulmuş bir candidate, IS'ten OOS'a mutlaka taşınmalıdır (aksi halde "OOS'u değerlendirmek" anlamsız olur). Bu foundation'da henüz yok, çünkü candidate abstraction henüz yok.

"Fresh state" kısaltması, **yalnızca (2)'yi** ifade eder — (1)'i (legal historical context) sessizce silmez; (1)'in mekanizması artık Bölüm 8.3'te LOCKED'dır.

## 11. OOS Accounting Contract (LOCKED — Principle, Mekanizmadan Bağımsız)

```
OOS ekonomik performans attribution'ı TAM OLARAK evaluation_start'ta başlar.

Başlangıç ekonomik state'i:
    cash = config.initial_cash
    position = flat (0)
    realized_pnl = 0

IS'teki hiçbir fill, OOS PnL'ine katkıda bulunmaz.
IS'te üretilen hiçbir sinyal, OOS içinde bir fill YARATAMAZ.
```

Bu, **Bölüm 8.3'te LOCKED olan B2 mekanizması de dahil, hangi warm-up mekanizması seçilirse seçilsin geçerli kalan bir prensiptir** — exact API tasarımından bağımsız olarak kilitlenir. Bölüm 8.3'te LOCKED olan B2 mekanizması Layer-1 için **implement edilmiştir**; bu **AYNI** prensip o implementasyonda explicit olarak regression testleriyle **yeniden kanıtlanmıştır** (`tests/test_backtest_replay_context_evaluation.py`, `tests/test_backtest_store_runner_context_evaluation.py`) — context desteği eklemek bu accounting kontratını **gevşetmemiştir.**

**Final IS signal / context-candle rule (LOCKED, mekanizmadan bağımsız):** IS sırasında üretilen ekonomik bir aksiyon/sinyal, hiçbir bağımsız OOS evaluation'ında pending bir fill olarak **ortaya çıkamaz.** Bölüm 8.3'te LOCKED olan B2 mekanizması Layer-1 için implement edilmiştir; pre-OOS geçmiş candle'lar (context candle'lar) artık salt-okunur information context olarak görünürdür; bu context candle'ları:

```
- evaluation_start'tan ÖNCE hiçbir skorlanmış PnL üretemez
- hiçbir carried pozisyon yaratamaz
- hiçbir pending fill yaratamaz
- OOS başlangıç cash/account state'ini mutate edemez
```

Bir warm-up candle **yalnızca information context'tir, asla bir execution kaynağı değildir** — bu ayrım Bölüm 8.3'ün LOCKED B2 mekanizmasının temel invariant'ıdır; mekanizmadan bağımsız bir prensip olarak burada da ayrıca kilitli kalır.

## 12. `as_of_time` Contract (LOCKED — Yanlış Anlaşılmayı Önlemek İçin)

`as_of_time`, **veri finalization sınırıdır** (`DATA_QUALITY_SPEC.md`: hangi candle'ların "kapanmış/finalized" sayıldığını belirler) — **araştırma-gizlilik (research-secrecy) mekanizması DEĞİLDİR.**

```
as_of_time KULLANILAMAZ olarak:
    IS/OOS "gizliliğini" sağlayan bir mekanizma
```

Settled tarihsel veri için bugün `OOS`'u `as_of_time > OOS.end` ile çalıştırmak **veri açısından legal**dir (gerçekleşmiş geçmiş veri zaten kesinleşmiştir) — ama bu, bir candidate'in **seçim sürecinin** OOS sonucunu görmediğini garanti **etmez.** Selection leakage (Bölüm 20), `as_of_time`'ın değil, **research-process disiplininin** (henüz code ile enforce edilemeyen) sorumluluğundadır — bu ayrım Bölüm 19'da tekrar netleştirilir.

## 13. First Foundation Mode (LOCKED — Hedef; Generic/Çok-Pencereli Runner Layer-2 Policy-Freshness'a Gated)

FAZ6A'nın hedeflediği ilk validation modu:

```
FIXED-POLICY TEMPORAL EVALUATION

Bir zaten inşa edilmiş / dondurulmuş BacktestPolicy,
birden fazla temporal pencere üzerinde BAĞIMSIZ olarak değerlendirilir.

YOK: fitting, optimizer, candidate selection.
```

Bu, henüz **tam walk-forward optimization değildir** (Bölüm 14) — bu ayrım kilitlidir.

**Tek-pencereli (Layer 1) context-aware evaluation artık implement edilmiş + test edilmiştir** (Bölüm 8.3.11, 23). Mimari Bölüm 8.3'te LOCKED'dır (B2) VE `run_backtest_from_store`/`run_backtest_replay` kodu artık `evaluation_start` üzerinden context/evaluation ayrımını bilir — Bölüm 8/9'un audit bulgusu artık tarihsel/RESOLVED'dır: history-reconstructible (Type-H) bir `BacktestPolicy` (özellikle lookback/rolling-feature kullanan bir policy), `run_backtest_from_store(requested_start=context_start, evaluation_start=OOS.start, ...)` ile doğrudan, doğru şekilde değerlendirilebilir (bkz. Bölüm 8.3.5 için Type-H niteliğinin caller/policy-author sorumluluğu kaldığı). **Çok-pencereli (Layer 2) bir rolling OOS runner artık zero-context için implement edilmiş + test edilmiştir** (`run_rolling_backtest_from_store`, bkz. Bölüm 8.3.6, 23, 28.C) — **ama GENERIC/context-aware (non-zero-context) bir varyant bugün implement edilmiş değildir.** Bu nedenle:

```
- Fixed-policy temporal evaluation FAZ6A'nın bir HEDEFİDİR (Bölüm 22).
- Onun generic runner kontratının MİMARİSİ LOCKED'dır (Bölüm 8.3, B2) VE
  Layer-1 İMPLEMENTASYONU TAMAMLANMIŞTIR. Tek-pencereli (Layer 1)
  context-aware canonical replay + store-backed composition
  IMPLEMENTED + TESTED'dır (bkz. Bölüm 23); çok-pencereli (Layer 2)
  rolling OOS orchestrator, policy-instance-freshness mekanizmasına
  (Bölüm 8.3.6, Bölüm 19) ihtiyaç duyuyordu — bu mekanizma Bölüm 8.3.6'da
  (factory-based) LOCKED'dır VE artık **zero-context Layer-2 için
  İMPLEMENT EDİLMİŞ + TEST EDİLMİŞTİR** (`run_rolling_backtest_from_store`,
  bkz. Bölüm 23, 28.C — 12/12). Context-aware (non-zero-context) bir
  Layer-2 varyantı HENÜZ İMPLEMENT EDİLMEMİŞTİR.
- MS2 (temporal-window primitives) bu karara bağımlı DEĞİLDİR — tamamen
  pure/store-free'dir ve bağımsız olarak inşa edilebilir (implement
  edildi, bkz. Bölüm 23).
```

Context/lookback kullanmayan trivial bir policy için, bugünkü `run_backtest_from_store`'un pencere-başına bağımsız çağrılması **zaten doğru sonucu üretir** (Bölüm 11) — bunu artık **tek-pencereli (Layer 1) bir runner contract'ı** olarak kilitlemek mümkündür, çünkü warm-up implementasyonu tamamlanmıştır. Çok-pencereli (Layer 2) rolling orchestrator, zero-context için artık **implement edilmiş ve test edilmiştir** (`run_rolling_backtest_from_store`) — her pencere `requested_start=window.start, requested_end=window.end, evaluation_start=window.start` ile çalışır, yani `context_start < evaluation_start` DEĞİLDİR. Context-aware (non-zero-context) bir Layer-2 runner, ayrı, henüz tasarlanmamış bir per-window context-boundary uzantısına bağımlı kalır; o uzantı spec-lock edilip implement edilmeden context-aware bir çok-pencereli runner'a commit edilmez.

## 14. Walk-Forward Terminolojisi (LOCKED — Precision)

**İki farklı kavram** kesin olarak ayrılır:

```
(A) Rolling / sequential fixed-policy OOS evaluation:
    aynı, sabit policy, ardışık pencerelerde bağımsız çalıştırılır.
    Foundation'ın kapsamındadır (Bölüm 13).

(B) True walk-forward optimization:
    IS üzerinde fit/select → candidate dondur → OOS'ta değerlendir →
    ilerlet → tekrarla.
    Candidate/trial abstraction'a bağımlıdır (Bölüm 18) — foundation'ın
    kapsamında DEĞİLDİR.
```

(A), **"walk-forward optimization" olarak adlandırılmaz** — yalnızca "rolling fixed-policy temporal evaluation" veya benzeri dürüst bir isimle anılır. Bu repo (A)'yı (B)'den önce inşa edebilir; ama ikisi asla karıştırılmaz.

## 15. Metrics Foundation — Staged Bağımlılık (LOCKED) — Stage-1 LOCKED VE IMPLEMENTED + TESTED (FAZ6B MS4 + MS5)

`BacktestResult` **değişmeden** kalır (Bölüm 4). Metrikler `equity_curve`'den **dışarıda** türetilir.

**Aşama 1 (foundation): total return + max drawdown — exact formül, API, validation ve edge-case davranışı Bölüm 15.1–15.8'de LOCKED'dır (FAZ6B MS4) VE artık IMPLEMENTED + TESTED'dır (FAZ6B MS5).** `equity_curve`'den doğrudan, ek runtime bağımlılık gerektirmeden hesaplanır. `src/crypto_quant_lab/validation/metrics.py`'de implement edilmiştir (commit `a265e44`) — `Stage1Metrics` (frozen, slots) + `compute_stage1_metrics(result: BacktestResult) -> Stage1Metrics` — kendi regression suite'i `tests/test_validation_metrics.py`'de (82 test, tümü PASS). İlgili regression suite'ler (`tests/test_backtest_models.py`, `tests/test_backtest_results.py`, `tests/test_validation_rolling_backtest.py` — 105 test) DEĞİŞMEDEN yeşil kalır; tam suite 1468/1468 PASS. Post-commit implementasyon audit'i — PASS (bkz. Bölüm 23, 28.D — 18/18).

**Aşama 2 (LATER IN FAZ 6, kendi dedicated contract mikro-adımı):** return-series / Sharpe foundation — Bölüm 16'daki açık sorular **önce** çözülmelidir; formül burada gelişigüzel kilitlenmez.

**Aşama 3 (LATER IN FAZ 6):** Deflated Sharpe, PBO, multiple-testing corrections, parameter stability — Bölüm 17.

**Faz 6'nın "foundation" tamamlanma tanımı, Sharpe'ı kalıcı olarak göz ardı edip yalnızca total-return/max-drawdown ile tanımlanmaz** — Aşama 2/3, Bölüm 22'deki alt-faz yapısında **explicit olarak** yer alır, yalnızca implementasyon sırası ertelenir.

**15.1 Aşama 1 Kapsamı (LOCKED — Yalnızca Bu İkisi)**

Aşama 1, **kesinlikle ve yalnızca** şunlardan oluşur:

```
1. Total return
2. Maximum drawdown
```

Bu kontrattan **açıkça hariç tutulur** (hiçbiri burada formül-kilitlenmez, hiçbiri bu mikro-adımda tasarlanmaz):

```
- Periodic return series (Bölüm 16 — açık sorular, çözülmedi)
- Mean/volatility
- Sharpe (Bölüm 15 Aşama 2, 17.3)
- Sortino
- Calmar
- CAGR/annualization
- Win rate
- Profit factor
- Exposure
- Turnover
- Benchmark-relative metrikler
- Cross-window aggregation
- Candidate/trial aggregation (Bölüm 18)
- Optimizer/grid-search (Bölüm 27)
- Multiple-testing corrections (Bölüm 17.6, 20)
- İleri seviye Faz 6 metrics/kontrolleri (Bölüm 17 — Deflated Sharpe, PBO, purging/embargo, CPCV, parameter stability)
```

**15.2 Public API (LOCKED — Kavram ve İsimler; Implementasyon Değil)**

```
Modül:  src/crypto_quant_lab/validation/metrics.py

@dataclass(frozen=True, slots=True)
class Stage1Metrics:
    total_return: Decimal
    max_drawdown: Decimal

def compute_stage1_metrics(result: BacktestResult) -> Stage1Metrics:
    ...
```

```
- Public import path: crypto_quant_lab.validation.metrics
- validation/__init__.py DEĞİŞMEDEN kalır (mevcut zero-re-export
  convention'ıyla tutarlı — windows.py, rolling.py ile aynı desen).
- BacktestResult DEĞİŞMEDEN kalır (Bölüm 4, 21).
- WindowResult DEĞİŞMEDEN kalır (Bölüm 28.C) — hiçbir result modeline
  metrics field'ı EKLENMEZ.
- Aynı compute_stage1_metrics fonksiyonu hem doğrudan bir BacktestResult
  üzerinde, hem de bağımsız bir WindowResult.result üzerinde çalışır —
  hiçbir per-window metrics wrapper veya cross-window aggregate
  TANITILMAZ.
```

Bu mikro-adım yalnızca API'yi kilitler — `metrics.py` modülü veya içindeki hiçbir sembol bu mikro-adımda YARATILMAZ.

**15.3 `Stage1Metrics` Değer Invariant'ları (LOCKED)**

Doğrudan `Stage1Metrics` construction'ı şunları validate eder:

```
- total_return bir Decimal olmalıdır; değilse TypeError.
- max_drawdown bir Decimal olmalıdır; değilse TypeError.
- Her iki değer de finite olmalıdır; değilse ValueError.
- max_drawdown >= Decimal("0") olmalıdır; negatifse ValueError.
- total_return'ün yapay bir alt veya üst sınırı YOKTUR.
- max_drawdown'ın yapay bir üst sınırı YOKTUR (bkz. 15.6).
```

Nesne frozen, slotted, value-equal ve normal frozen-dataclass davranışıyla hashable'dır. Yüzde string'i veya float field TANITILMAZ — yalnızca `Decimal`.

**15.4 `compute_stage1_metrics` Input Validation ve Fail-Fast Sırası (LOCKED)**

`compute_stage1_metrics`, `result` argümanını mevcut repo'nun concrete-type `isinstance` convention'ı ile kabul eder — `isinstance(result, BacktestResult)`, subclass'ları REDDETMEZ (repo'da zaten `isinstance` her yerde bu şekilde kullanılır; bu, structural/duck-type bir kabul DEĞİLDİR, tam tersini iddia etmek yanlıştır).

Deterministik validation, tam olarak bu sırada:

```
1. result bir BacktestResult olmalıdır; değilse TypeError.
2. initial_cash finite olmalıdır; değilse ValueError.
3. initial_cash > 0 olmalıdır; değilse ValueError.
4. final_equity finite olmalıdır; değilse ValueError.
5. equity_curve boş OLMAMALIDIR; boşsa ValueError.
6. Her curve elemanı bir EquityPoint olmalıdır; geçersiz eleman,
   index'i içeren bir TypeError fırlatır.
7. Her equity_curve[i].equity finite olmalıdır; geçersiz değer,
   index'i içeren bir ValueError fırlatır.
8. Equity-point timestamp'leri strictly ascending olmalıdır;
   değilse ValueError.
9. equity_curve[-1].equity == final_equity olmalıdır; değilse
   ValueError.

Yalnızca TÜM validasyonlar geçtikten SONRA metrikler hesaplanır.
```

**Gerekçe:**

```
- Maximum drawdown path-dependent'tir ve en az bir equity gözlemi
  olmadan dürüst şekilde ifade edilemez — bu yüzden boş curve
  REDDEDİLİR (Bölüm 15.6 ile tutarlı).
- Boş bir curve için, final_equity'den materyal olarak farklı bir
  drawdown "0" döndürmek yanıltıcı olurdu.
- Curve sırası drawdown'ı etkiler — bu yüzden doğrudan inşa edilmiş,
  sırasız/tutarsız BacktestResult'lar sessizce kabul edilmez.
- Total return ve drawdown, birbiriyle tutarsız terminal değerlerden
  hesaplanmamalıdır (equity_curve[-1] != final_equity).
- Canonical replay (Bölüm 8.3, 21) bu koşulların TÜMÜNÜ zaten sağlar
  (build_backtest_result'ın kendi invariant'ları üzerinden) — bu
  nedenle bu liste replay'e bir değişiklik DEĞİLDİR, yalnızca public
  metrics-boundary'sinde bir savunma katmanıdır (defense-in-depth).
```

Bu liste, Stage-1'in kullanmadığı hiçbir `BacktestResult` field'ının (örn. `fill_count`, `trade_count`, `total_cost`) revalidation'ını içermez — mevcut repo kontratı bunu zaten gerektirmiyorsa, bu mikro-adım onu icat etmez.

**15.5 Total-Return Kontratı (LOCKED)**

Exact formül ve operation sırası:

```
total_return = final_equity / initial_cash - Decimal("1")
```

```
- Çıktı bir Decimal fraction'dır (örn. Decimal("0.05") == +%5).
- Pozitif = kâr. Negatif = zarar. Sıfır = başabaş.
- Bu bir percentage-point sayısı DEĞİLDİR ve bir absolute PnL
  DEĞİLDİR.
- final_equity ve initial_cash'ten hesaplanır — first-to-last
  equity-curve return'den DEĞİL.
- Bu nedenle canonical final_equity'de zaten yansıyan transaction
  cost, funding, realized PnL, unrealized PnL, ve final
  mark-to-market etkilerini otomatik olarak içerir.
- Hesaplama sonrası kasıtlı bir quantization/rounding adımı
  UYGULANMAZ (Bölüm 15.7'deki context'in doğal precision'ı dışında).
- Negatif final_equity legal'dir, bu yüzden total_return
  Decimal("-1")'den küçük olabilir.
- initial_cash <= 0, bölmeden ÖNCE reddedilir (Bölüm 15.4, madde 3).
- total_pnl / initial_cash, canonical sonuçlar için cebirsel olarak
  eşdeğer OLABİLİR (build_backtest_result'ın total_pnl == final_equity
  - initial_cash invariant'ı nedeniyle) — ama bu, canonical
  implementasyon formülü DEĞİLDİR; yukarıdaki exact operation sırası
  kilitlidir.
```

**Sessizce şuna geçilmez:**

```
(final_equity - initial_cash) / initial_cash
```

çünkü finite-precision Decimal operation sırası farklı bir son basamak üretebilir. Kilitlenen exact operation sırası (önce bölme, sonra çıkarma) SABİT kalır.

**15.6 Maximum-Drawdown Kontratı (LOCKED)**

Maximum drawdown, **non-negative bir relative magnitude**'dur — signed negatif bir sayı DEĞİLDİR, absolute bir currency tutarı DEĞİLDİR.

Exact algoritma:

```python
peak = initial_cash
max_drawdown = Decimal("0")

for point in equity_curve:
    if point.equity > peak:
        peak = point.equity
    else:
        drawdown = (peak - point.equity) / peak
        if drawdown > max_drawdown:
            max_drawdown = drawdown

return max_drawdown
```

```
- initial_cash, curve-öncesi bir implicit baseline'dır ve ilk peak'i
  seed eder.
- Bu, curve'ün kendisinde bir initial-cash noktası olmadığı için
  aksi halde gizli kalacak bir ilk-noktadaki (immediate) kaybı
  yakalar.
- Baseline'dan sonra yalnızca canonical equity değerleri walk edilir.
- Input tuple sırası, yalnızca strict timestamp-order validation'dan
  (Bölüm 15.4, madde 8) SONRA kullanılır.
- Bir drawdown'ın sayılması için recovery GEREKMEZ.
- Flat veya monotonically rising equity → 0.
- initial_cash'e eşit veya üzerindeki tek bir nokta → 0.
- initial_cash'in ALTINDAKİ tek bir nokta → kendi immediate relative
  drawdown'ı.
- Birden fazla historical peak-to-trough decline'ın EN BÜYÜĞÜ
  döndürülür.
- Maximum drawdown her zaman >= 0'dır.
- Maximum drawdown 1'de CAP'LENMEZ.
- Equity, pozitif bir peak'ten SONRA negatife dönerse, drawdown 1'i
  AŞABİLİR.
- Pozitif bir peak'ten sıfıra tam bir decline → 1.
- Stage-1'e hiçbir absolute-currency drawdown DAHİL EDİLMEZ.
- Hiçbir drawdown duration, recovery time, veya peak/trough
  timestamp'i DAHİL EDİLMEZ.
```

`initial_cash > 0` validate edildiğinden (Bölüm 15.4, madde 3) ve peak, ondan seed edilen bir running maximum olduğundan (yalnızca artabilir), sıfır veya negatif bir peak'e bölme **hiçbir zaman** oluşamaz.

**15.7 Decimal-Context Determinism (LOCKED)**

Metrik çıktıları, caller'ın mutable global Decimal context'ine bağımlı BIRAKILMAZ.

```
Private Stage-1 computation context:

Context(
    prec=28,
    rounding=ROUND_HALF_EVEN,
    traps=[],
)

Tüm Stage-1 bölme aritmetiği şunun içinde çalışır:

localcontext(private_stage1_context)
```

```
- Precision: 28 significant decimal digit.
- Rounding: ROUND_HALF_EVEN.
- Context, metrics modülü tarafından explicit olarak inşa edilir —
  caller'ın ambient state'inden KOPYALANMAZ.
- Bir caller'ın getcontext().prec veya rounding mode'unu değiştirmesi,
  Stage-1 çıktısını DEĞİŞTİRMEMELİDİR.
- Hiçbir float conversion oluşmaz.
- Hiçbir NumPy/pandas kullanılmaz (Bölüm 27).
- Hiçbir ek .quantize() adımı uygulanmaz.
- Non-terminating bölme, yalnızca kilitlenen 28-digit computation
  context'ine göre rounded olur.
- Hesaplama sonrası çıktılar finite OLMALIDIR; non-finite bir
  hesaplanmış çıktı, deterministik olarak ValueError ile reddedilir.
- Decimal aritmetik hataları (fault), sessizce başarılı bir
  non-finite metrik ÜRETMEMELİDİR.
```

Exact `Context(...)` constructor'ı, ambient state'ten tam bağımsız olmak için ek explicit exponent/clamp field'larına ihtiyaç duyarsa, bunlar Python'un standart deterministik `Context` default'ları kullanılarak kaydedilir — Stage-1'de caller-configurable bir precision İCAT EDİLMEZ.

**15.8 Purity ve Compatibility (LOCKED)**

`compute_stage1_metrics`:

```
- Aynı geçerli input için deterministiktir.
- Input'u mutate ETMEZ.
- Wallclock time KULLANMAZ.
- Randomness KULLANMAZ.
- I/O yapmaz.
- Hiçbir store'a query atmaz.
- Replay'i çağırmaz.
- Pencereleri aggregate etmez.
- BacktestResult veya WindowResult'ı DEĞİŞTİRMEZ.
- Rolling orchestration'a bir bağımlılık EKLEMEZ.
- Non-zero-context Layer-2 implement edilmeden ÖNCE çalışabilir.
- Yalnızca standard-library Python ve Decimal kullanır.
```

**Implementasyon Durumu — IMPLEMENTED + TESTED (FAZ6B MS5, bkz. Bölüm 23, 28.D):**

Yukarıdaki mekanizma `src/crypto_quant_lab/validation/metrics.py`'de implement edilmiştir (commit `a265e44`), kendi regression suite'i `tests/test_validation_metrics.py`'de (82 test, tümü PASS). Public production şekli:

```
@dataclass(frozen=True, slots=True)
class Stage1Metrics:
    total_return: Decimal
    max_drawdown: Decimal

def compute_stage1_metrics(result: BacktestResult) -> Stage1Metrics: ...
```

Yukarıdaki her LOCKED invariant, bu implementasyon için kanıtlanmıştır (post-commit implementasyon audit'i — PASS):

```
- Stage1Metrics: her iki alan da yalnızca Decimal, finite; max_drawdown
  negatif olamaz (ValueError); hiçbir alan cap'lenmez (test_stage1_metrics_*)
- compute_stage1_metrics, Bölüm 15.4'teki 9-adımlık deterministik
  validation sırasını tam olarak uygular — hesaplama, TÜM adımlar
  geçtikten SONRA başlar (test_rejects_*, test_order_* — 21 test)
- total_return, `result.final_equity / result.initial_cash - Decimal(1)`
  exact operation sırasıyla, private context içinde hesaplanır;
  precision-28 altında forbidden rewrite'tan farklı son basamak
  ürettiği somut bir Decimal çiftiyle kanıtlanmıştır
  (test_total_return_exact_locked_operation_order); costs/funding/
  unrealized mark-to-market'in final_equity'de zaten yansıdığı gerçek
  engine sonuçlarıyla kanıtlanmıştır (test_total_return_costs_already_reflected,
  _funding_already_reflected, _unrealized_mark_to_market_already_reflected)
- max_drawdown, initial_cash'ten seed edilen running-peak algoritmasıyla
  hesaplanır; negatif equity sonrası 1'i aşabildiği ve hiçbir upper cap
  olmadığı doğrudan kanıtlanmıştır (test_max_drawdown_negative_equity_after_positive_peak_exceeds_one,
  _no_artificial_cap) — Ruff'ın `if`/assignment'ı `max_drawdown = max(max_drawdown, drawdown)`
  olarak sadeleştirmesi, post-commit audit'te bağımsız olarak semantik
  eşdeğerlik için doğrulanmıştır
- Private Decimal context (prec=28, ROUND_HALF_EVEN, Emin/Emax/capitals/
  clamp Python'un stdlib default'ları, traps=[]) her çağrıda taze inşa
  edilir; ambient precision/rounding değişikliklerinin çıktıyı
  ETKİLEMEDİĞİ davranışsal olarak kanıtlanmıştır (5 Decimal-context
  determinism testi); hesaplanmış non-finite bir çıktı (overflow)
  total_return ve max_drawdown için BAĞIMSIZ olarak deterministik
  ValueError ile reddedilir (2 test)
- Pure/deterministic/no-mutation, WindowResult.result'un bağımsız
  kullanımı, ve hiçbir cross-window aggregation'ın olmadığı doğrudan
  kanıtlanmıştır (9 purity/compatibility testi)
```

**Kapsam sınırı (önemli):** bu implementasyon **yalnızca Stage-1**'dir (total return + max drawdown). Stage-2 (return-series/Sharpe/Sortino/Calmar/CAGR) ve Stage-3 (Deflated Sharpe, PBO, multiple-testing, parameter stability) implement edilmemiştir ve bu modülde hiçbir iz bırakmaz — `metrics.py` bu metriklere hiçbir referans içermez. `BacktestResult`, `WindowResult`, replay, store-runner, ve rolling orchestrator DEĞİŞMEDEN kalır (`git diff 76002ab..a265e44` bunların hiçbirinde boştur). Non-zero-context Layer-2 hâlâ implement edilmemiştir; bu implementasyon ona bağımlı DEĞİLDİR.

## 16. Return Series Semantics — Açık Sorular (Kilitlenmez)

Sharpe-ailesi metrikler bir return series gerektirir. `EquityPoint`, her candle availability'sinde bir örnek sağlar — ama şu sorular **bu MS1'de yeterli kanıt olmadığı için kilitlenmez:**

```
- simple vs. log return
- periyodiklik (1h/4h candle-by-candle mi, yoksa resample mi)
- risk-free rate varsayımı
- 1h/4h için annualization faktörü
- sıfır/negatif equity handling
```

Bu sorular, Sharpe implementasyonundan **önce** dedicated bir metrics-contract mikro-adımında çözülür (Bölüm 22, Faz 6B).

## 17. İleri Seviye Validation Tekniklerinin Bağımlılık Haritası (LOCKED — Hiçbiri Sessizce Taşınmaz)

`BACKTEST_SPEC.md` Bölüm 26/27/34'ün Faz 6'ya atadığı her madde burada **Faz 6 kapsamında** kalır; yalnızca dependency-position (NOW / LATER IN FAZ 6) belirlenir.

### 17.1 Purging / Embargo — LATER IN FAZ 6

Classical purged K-fold / embargo, bir **information/label/outcome horizon** tanımına ihtiyaç duyar (bir gözlemin "etkisinin" ne kadar sürdüğü). Mevcut `BacktestPolicy` abstraction'ının **hiçbir explicit label/outcome horizon kavramı yoktur** (`target_position(context) -> PositionTarget`, salt candle-prefix tabanlıdır).

**Bu nedenle:** purging/embargo implementasyonu, ilgili observation/outcome-horizon kontratı var olana kadar **Faz 6 İÇİNDE deferred**'dir — başka bir faza taşınmaz. Olası gelecek çözümler (kilitlenmez): generic bir "information interval," bir trade outcome horizon, veya (Faz 14 ML modelleri geldiğinde) bir label horizon. Hangisinin seçileceği bu dokümanda **prematüre kilitlenmez.**

### 17.2 CPCV — LATER IN FAZ 6

Prerequisites: fold model + observation/outcome-horizon contract (17.1) + purge/embargo semantics (17.1) + repeated candidate evaluation (18) + deterministic performance matrix (17.4). Hiçbiri bugün yok. CPCV, bu prerequisite'ler karşılanana kadar implement edilmez.

### 17.3 Sharpe-Ailesi Metrikler — Aşama 2 (Bölüm 15/16)

Prerequisites: return-series contract (Bölüm 16) çözülmeli. Basit total-return/max-drawdown'dan **sonra**, ama foundation'ın (Bölüm 13) parçası değil.

### 17.4 Deflated Sharpe — LATER IN FAZ 6

Prerequisites: tanımlı Sharpe istatistiği (17.3) + candidate/trial history (18) + (efektif) trial sayısı + gerekli dağılımsal girdiler. Standalone bir formül olarak, deneysel/trial framework'ünden **kopuk** implement edilmez.

### 17.5 PBO — LATER IN FAZ 6

Prerequisites: birden fazla candidate/trial (18) + birden fazla partition + deterministic performance matrix + explicit selection rule. Mevcut repo'da candidate-search result matrix'i **yok** — bu nedenle PBO ilk primitive olarak **anlamlı şekilde implement edilemez** (foundation pre-flight'in kendi bulgusuyla tutarlı).

### 17.6 Multiple-Testing Corrections — LATER IN FAZ 6

Prerequisites: trial-count tracking (18) — candidate/trial abstraction'a bağımlı.

### 17.7 Parameter Stability — LATER IN FAZ 6

Prerequisites: parameterized candidate abstraction + komşu parametre konfigürasyonları + stabil bir evaluation metriği. Mevcut `BacktestPolicy`'ler parametrik değildir (`BACKTEST_SPEC.md` Bölüm 12: Faz 4 policy'leri kasıtlı olarak trivial/deterministic). Parameter optimizer burada **icat edilmez.**

## 18. Candidate / Trial Abstraction — Neden Şimdi Değil (LOCKED)

Bu MS1'de **inşa edilmez.** Ama neden Faz 6'nın ilerideki bölümlerinin buna ihtiyaç duyduğu kaydedilir:

```
- parameter search
- candidate identity
- frozen candidate (bkz. Bölüm 10.3)
- selection metric
- trial count
- multiple-testing corrections (17.6)
- Deflated Sharpe (17.4)
- PBO (17.5)
```

**Zorunlu prensip (LOCKED, ne zaman implement edilirse edilsin geçerli):** candidate selection **yalnızca IS**'i kullanabilir. Bir OOS sonucu, **o sonucu üreten aynı candidate'in seçimine** asla geri besleme yapamaz (Bölüm 12, Bölüm 20).

**İlgili ama ayrı bir konu (Bölüm 8.3.6, 19):** bir candidate/trial'ın frozen policy instance'ının bağımsız evaluation pencereleri arasında güvenle yeniden kullanılabilmesi için, ileride bir `policy_factory`-benzeri mekanizma bu abstraction'ın bir parçası olabilir — bu MS4'te tasarlanmaz/implement edilmez, yalnızca gelecekteki bir bağımlılık olarak kaydedilir.

## 19. Leakage / Anti-Overfitting — Engine vs. Process (LOCKED)

**Engine-enforceable (bu spec'in kilitlediği kod-seviyeli korumalar):**

```
- IS/OOS overlap → ValueError (Bölüm 7)
- her pencere kendi bağımsız quality/funding gate'inden geçer (Bölüm 4, 24)
- OOS fresh economic state (Bölüm 11)
- as_of_time'ın mevcut anti-lookahead semantics'i her pencerede korunur
- aynı mutable policy instance'ının bağımsız evaluation pencereleri
  arasında yeniden kullanılması, run_rolling_backtest_from_store için
  artık ENGINE-ENFORCEABLE'dır — object-identity tabanlı mekanik
  reddetme (Bölüm 8.3.6, 23, 28.C — factory-based, IMPLEMENTED + TESTED)
```

**Research-process riskleri (henüz code ile enforce edilemez):**

```
- bir araştırmacının/LLM'in candidate'i dondurmadan ÖNCE OOS metriklerini görmesi
  ("peeking") — yalnızca disiplinle önlenir, bu spec'in kapsamı dışında
- aynı OOS penceresinin art arda birçok candidate seçim iterasyonunda
  tekrar kullanılması (OOS'un fiilen ikinci bir IS'e dönüşmesi) —
  yalnızca trial-count tracking (Bölüm 18) var olduğunda tespit edilebilir
- context-aware evaluation'ın history-reconstructible (Type-H) olmayan bir
  BacktestPolicy ile kullanılması (Bölüm 8.3.5) — mekanik olarak tespit
  edilemez, yalnızca policy-author disiplinine bağlıdır
- aynı mutable policy instance'ının bağımsız pencereler arasında yeniden
  kullanılması: Layer-1 tek-pencere run'da hâlâ caller disiplinine
  bağlıdır (bu Layer-1 API'sine mekanik bir kontrol eklenmedi); ve
  run_rolling_backtest_from_store DIŞINDA, gelecekte yazılacak herhangi
  bir başka orchestrator/caller için de mekanik enforcement otomatik
  DEĞİLDİR — yukarıdaki engine-enforceable madde yalnızca
  run_rolling_backtest_from_store'un kendisi için geçerlidir
```

## 20. Multiple Testing — Kayıt Prensibi (LOCKED, İmplementasyon Yok)

Gelecekteki candidate/trial katmanı, en azından şunu **kaydedebilmelidir** (implement edilmez, yalnızca prensip):

```
- kaç candidate/trial değerlendirildi
- hangi metrik bir candidate'i seçti
- hangi data partition kullanıldı
- hangi OOS evaluation, dondurulmuş seçime aittir
```

## 21. Backward Compatibility (LOCKED)

```
BacktestResult:          unchanged
CostModel/FundingModel:  unchanged
```

**Compose-not-duplicate prensibi (LOCKED):**

```
- validation, canonical backtest semantics'i (replay/accounting/execution)
  COMPOSE eder — asla yeniden implement etmez
- validation ikinci bir replay/accounting/execution engine OLUŞTURAMAZ
  (validation-specific replay FORBIDDEN)
- mevcut economic semantics (fill timing, cost/funding accounting,
  PnL/equity formülleri) korunur
- context/evaluation-boundary desteği Bölüm 8.3'te LOCKED'dır VE
  IMPLEMENT EDİLMİŞTİR: ADDITIVE, keyword-only, default'ta davranışı
  değiştirmeyen bir `evaluation_start` parametresi (`run_backtest_replay`,
  `run_backtest_from_store`) — kendi regression suite'i ile test
  edilmiştir (`tests/test_backtest_replay_context_evaluation.py`,
  `tests/test_backtest_store_runner_context_evaluation.py`)
- exact API mechanism (Bölüm 8.1'deki B2 seçeneği) Bölüm 8.3'te
  LOCKED'dır; exact parametre ismi/signature implementasyonda finalize
  edildi: `evaluation_start: datetime | None = None`, her iki
  fonksiyonda da keyword-only additive parametre olarak (bkz. Bölüm
  8.3.11) — bu MS4 positional/keyword shape'i literal olarak
  kilitlememişti, ama gerçekleşen implementasyon kavramsal API ile
  birebir örtüşmektedir
```

**Bu MS1/MS4, `run_backtest_from_store`/`run_backtest_replay`'in public signature'larının sonsuza kadar literal olarak aynı kalacağını GARANTİ ETMEZ** — yalnızca, herhangi bir gelecekteki değişikliğin additive/geriye-uyumlu olacağını ve kendi regression suite'inden geçeceğini kilitler. `evaluation_start` eklemesi bu garantiyi doğrulamıştır: additive, geriye-uyumlu (legacy-equivalence regression testleriyle kanıtlanmıştır) ve kendi regression suite'inden geçmiştir. Herhangi bir gelecekteki extension syntax'ı bu dokümanda tasarlanmaz.

**`run_rolling_backtest_from_store` (FAZ6B MS2, `src/crypto_quant_lab/validation/rolling.py`) aynı compose-not-duplicate prensibine tabidir ve onu doğrulamıştır:** `run_backtest_replay`/`run_backtest_from_store` public signature'larına HİÇBİR değişiklik yapmadan, tamamen yeni/ayrı bir modülde eklenmiştir; her pencere için tek bir `run_backtest_from_store` çağrısına delege eder, ikinci bir replay/accounting/execution engine yaratmaz; tam regression suite (mevcut 1358 + yeni 28 = 1386 test) DEĞİŞMEDEN yeşil kalır.

## 22. Faz 6 Alt-Faz Yapısı (LOCKED — "Foundation" ≠ "Faz 6 Complete")

```
FAZ 6A — Temporal Validation Foundation
    temporal window primitive, IS/OOS split, fixed-policy rolling OOS
    evaluation, basic return/drawdown metrics.

FAZ 6B — Context/Warm-up + Metrics + Experiment Foundation
    OOS context/warm-up API implementasyonu (exact mekanizma — B2 —
    Bölüm 8.3'te LOCKED; Layer-1 [tek-pencere context-aware canonical
    replay + store-backed composition] artık İMPLEMENT EDİLMİŞ +
    TEST EDİLMİŞTİR, bkz. Bölüm 23, 28.B). Policy-instance-freshness
    mekanizması (Bölüm 8.3.6) LOCKED'dır VE zero-context Layer-2
    çok-pencereli orchestrator [run_rolling_backtest_from_store] artık
    İMPLEMENT EDİLMİŞ + TEST EDİLMİŞTİR (bkz. Bölüm 23, 28.C — 12/12).
    Stage-1 metrics (total return + max drawdown) exact kontratı
    Bölüm 15.1–15.8'de LOCKED'dır (FAZ6B MS4) VE artık production
    implementasyonu [`Stage1Metrics`, `compute_stage1_metrics`]
    İMPLEMENT EDİLMİŞ + TEST EDİLMİŞTİR (FAZ6B MS5, bkz. Bölüm 23,
    28.D — 18/18). Bu alt-fazda kalan iş: context-aware (non-zero-context)
    Layer-2 varyantı, return-series/Sharpe contract (Aşama 2),
    Aşama 3 (Deflated Sharpe, PBO, multiple-testing, parameter stability),
    candidate/trial abstraction — hepsi HENÜZ PENDING.

FAZ 6C — Advanced Overfitting Controls
    purging/embargo (horizon contract'a bağımlı), CPCV, Deflated Sharpe,
    PBO, multiple-testing corrections, parameter stability.

FAZ 6D — Faz 6 Final Acceptance
    tüm binding BACKTEST_SPEC Bölüm 26 maddelerinin ya implement edildiğinin
    ya da (yalnızca approved bir BACKTEST_SPEC revizyonuyla) yeniden
    kapsamlandığının audit'i.
```

**LOCKED:** 6A'nın tamamlanması **Faz 6'nın tamamlanması anlamına gelmez.** `BACKTEST_SPEC.md` Bölüm 26'daki her madde ya implement edilir ya da yalnızca dokümante edilmiş bir spec revizyonu ile yeniden kapsamlandırılır — sessizce "foundation yeterli" denip kapatılmaz.

## 23. Faz 6 Mikro-Adım Sırası — Yalnızca Yakın Vade (Bağlayıcı)

```
FAZ6A MS1:
  VALIDATION_SPEC.md (bu doküman)

FAZ6A MS2:
  Immutable temporal window / IS-OOS split primitives (pure, store-free)
  — Bölüm 6/7'nin kod karşılığı.

FAZ6A MS3:
  OOS CONTEXT / WARM-UP API PRE-FLIGHT (READ-ONLY) — TAMAMLANDI
  — exact mekanizma (B2) bu pre-flight'ta seçildi. Generic OOS
  runner'dan ÖNCE geldi.

FAZ6A MS4:
  OOS CONTEXT / EVALUATION CONTRACT — SPEC LOCK — TAMAMLANDI
  — MS3'ün seçtiği B2 mekanizmasını, Type-H/Type-I policy semantics'ini
  ve policy-freshness sınırını Bölüm 8.3'te LOCKED olarak kaydetti.
  Docs-only; production kod değişikliği içermedi.

FAZ6A Layer-1 implementasyonu (henüz resmi MS numarası atanmamış) —
TAMAMLANDI:
  - canonical run_backtest_replay evaluation_start desteği — TAMAMLANDI
    (src/crypto_quant_lab/backtest/replay.py,
    tests/test_backtest_replay_context_evaluation.py — 22 test)
  - store-backed run_backtest_from_store evaluation_start desteği —
    TAMAMLANDI (src/crypto_quant_lab/backtest/store_runner.py,
    tests/test_backtest_store_runner_context_evaluation.py — 21 test)
  - 28.B Layer-1 acceptance/status reconciliation (bu doküman
    güncellemesi) — TAMAMLANDI

FAZ6B MS1:
  POLICY INSTANCE FRESHNESS CONTRACT — SPEC LOCK — TAMAMLANDI
  — factory-based (`policy_factory`, Callable[[], BacktestPolicy])
  mekanizmayı; ownership/invocation invariant'larını; object-identity
  tabanlı mekanik reuse-detection kuralını; factory-output validation
  semantics'ini; failure/partial-execution sınırını; ve Type-H/Type-I
  sınırını Bölüm 8.3.6'da LOCKED olarak kaydetti. Docs-only; production
  kod, `policy_factory` implementasyonu, veya yeni test içermedi.

FAZ6B MS2:
  ROLLING FIXED-POLICY LAYER-2 ORCHESTRATOR (ZERO-CONTEXT) — TAMAMLANDI
  — Bölüm 8.3.6'da LOCKED olan factory-based policy-instance-freshness
  mekanizmasını, zero-context (`evaluation_start = window.start`) bir
  çok-pencereli orchestrator olarak implement etti (commit `c363267`):
  - `WindowResult` (frozen, slots) + `run_rolling_backtest_from_store`
    — TAMAMLANDI (src/crypto_quant_lab/validation/rolling.py,
    tests/test_validation_rolling_backtest.py — 26 test)
  - post-commit implementasyon audit'i — PASS
  Ardından test-hardening (commit `c4af87c`) — TAMAMLANDI: strong-retention
  kanıtı weakref-tabanlı hale getirildi, exception-propagation object-identity
  ile kanıtlandı, query-count coupling kaldırıldı, WindowResult immutability
  ve TemporalSplit-reddi testleri eklendi (tests/test_validation_rolling_backtest.py
  — 28 test). 28.B Layer-1 acceptance/status reconciliation gibi, 28.C
  Layer-2 acceptance/status reconciliation (bu doküman güncellemesi) —
  TAMAMLANDI.

FAZ6B MS3:
  STAGE-1 METRICS FOUNDATION PRE-FLIGHT (READ-ONLY) — TAMAMLANDI
  — total return/max drawdown için mevcut kod/spec kanıtını okudu;
  total-return formülünün kısmen LOCKED, max-drawdown'ın formül/edge-case
  seviyesinde HİÇ LOCKED OLMADIĞINI tespit etti; production implementasyonu
  bu belirsizlik çözülmeden önermedi. Generic OOS runner'dan önce MS3'ün
  (Bölüm 8.3) izlediği aynı "önce pre-flight, sonra spec-lock" precedent'i.

FAZ6B MS4:
  STAGE-1 METRICS FOUNDATION CONTRACT-LOCK — TAMAMLANDI
  — total return ve max drawdown için exact formülü, `Stage1Metrics`/
  `compute_stage1_metrics` public API'sini, input validation/fail-fast
  sırasını, ve private Decimal-context determinism kontratını Bölüm
  15.1–15.8'de LOCKED olarak kaydetti. Docs-only; production kod,
  `metrics.py` implementasyonu, veya yeni test içermedi.

FAZ6B MS5:
  STAGE-1 METRICS FOUNDATION IMPLEMENTATION — TAMAMLANDI
  — Bölüm 15.1–15.8'de LOCKED olan Stage-1 kontratını implement etti
  (commit `a265e44`):
  - `Stage1Metrics` (frozen, slots) + `compute_stage1_metrics` —
    TAMAMLANDI (src/crypto_quant_lab/validation/metrics.py,
    tests/test_validation_metrics.py — 82 test)
  - ilgili regression suite'ler (test_backtest_models.py,
    test_backtest_results.py, test_validation_rolling_backtest.py —
    105 test) DEĞİŞMEDEN yeşil kaldı; tam suite 1468/1468 PASS
  - post-commit implementasyon audit'i — PASS
  - 28.D Stage-1 acceptance/status reconciliation (bu doküman
    güncellemesi) — TAMAMLANDI

Sonraki (henüz başlanmadı):
  Context-aware (non-zero-context, `context_start < evaluation_start`)
  bir Layer-2 varyantı — ayrı bir per-window context-boundary tasarımına
  (henüz tanımlanmamış) ihtiyaç duyar; return-series/Sharpe contract
  (Aşama 2, Bölüm 16); Aşama 3 (Deflated Sharpe, PBO, multiple-testing,
  parameter stability, Bölüm 17); candidate/trial abstraction (Bölüm 18).
  Bu mikro-adımlardan önce context-aware bir çok-pencereli runner veya
  Aşama 2/3 metrics'e commit edilmez.
```

**MS3 scope (TAMAMLANDI — pre-flight'in kendisi, Bölüm 8.3'te kilitlendi):**

MS3, Bölüm 8.1'deki 5 yaklaşımı (A/B/C/D/E) tam olarak karşılaştırdı ve exact mekanizmayı seçti — seçim ve gerekçe **Bölüm 8.3'te LOCKED**'dır:

```
A) OOS-only history (context yok) — REJECTED (generic çözüm olarak);
   B2'nin context_start == evaluation_start özel durumu olarak hâlâ
   mevcuttur (Bölüm 8.3.1)
B) context_start < evaluation_start benzeri, ayrı bir context/evaluation
   boundary — LOCKED (exact varyant: B2, Bölüm 8.3)
C) policy'ye ayrı, salt-okunur bir warm-up candle sequence enjekte edilmesi
   — REJECTED
D) feature-layer / precomputed historical context yönü — REJECTED
   (bu faz için; ileride mümkün)
E) policy'nin, yeterli OOS history birikene kadar sinyal üretmemesi —
   REJECTED (generic çözüm olarak); fallback olarak hâlâ mevcuttur
```

MS3'ün seçtiği B2 mekanizması, Bölüm 11'de zaten kilitlenmiş şu invariant'ları korur (tam kontrat için bkz. Bölüm 8.3):

```
- historical context yalnızca information olabilir, asla bir execution
  kaynağı değildir
- evaluation_start'tan ÖNCE hiçbir skorlanmış PnL üretilemez
- hiçbir carried pozisyon yaratılamaz
- hiçbir pending fill yaratılamaz (context candle'lar hiçbir zaman
  policy'yi tetiklemez — koşulsuz, Bölüm 8.3.2)
- OOS başlangıç cash/account state'i mutate edilemez
- canonical replay/accounting/execution semantics'i reuse edilir
  (Bölüm 4, 21) — validation-specific replay YASAK
- candle/funding data quality gate bypass edilemez (Bölüm 24)
```

B2'nin Layer-1 implementasyonu artık **tamamlanmıştır** (yukarıda). Zero-context Layer-2 [`run_rolling_backtest_from_store`] implementasyonu da artık **tamamlanmıştır** (bkz. yukarıdaki FAZ6B MS2 kaydı, Bölüm 28.C). Bunun ötesi (context-aware/non-zero-context Layer-2 varyantı, walk-forward window advance, metrics foundation implementasyonu, 6B/6C'nin kalan mikro-adımları) burada detaylandırılmaz — context-aware bir Layer-2 varyantı, kendi ayrı per-window context-boundary tasarımı spec-lock edilip implement edilmeden **commit edilmez.**

## 24. Data / Ekonomik Bütünlük (LOCKED)

Her değerlendirilen pencere:

```
- candle quality gate'ten geçer (bypass YOK)
- funding_required=True ise funding quality gate'ten geçer (bypass YOK)
- transaction cost'ları korur (CostModel değişmeden)
- funding'i korur (FundingModel/funding_required değişmeden)
- sabit/global bir schedule varsaymaz
- sessiz repair yapmaz (missing data, partial coverage → açık hata)
```

Store-query tasarımı: her pencere **kendi bağımsız** `run_backtest_from_store` çağrısı olarak değerlendirilir (tek dev bir dataset çekip manuel dilimleme **yapılmaz**) — bu, quality/funding gate'lerin her pencerede **tam olarak** yeniden kanıtlanmasını garanti eder ve bu repo'nun "asla körü körüne güvenme, her sınırda yeniden doğrula" prensibiyle (MS11'in double-read deseni) tutarlıdır. Çoklu SQLite sorgusu maliyeti, correctness-first felsefesiyle bilinçli olarak kabul edilir; caching bu foundation'da **tanıtılmaz.**

## 25. Determinism (LOCKED)

```
Explicit girdi olmalı: store'lar, exchange, market_type, symbol, timeframe,
pencere sınırları, as_of_time, BacktestConfig, policy/candidate, CostModel,
funding mode/store/model, (ileride) selection/metric rule.

YOK: wallclock, seed'siz/kontrolsüz randomness.
```

Aynı girdiler → aynı pencere sonuçları — mevcut `run_backtest_from_store`'un determinism garantisinin (FUNDING-SPEC MS12'de real-SQLite reopen-determinism ile kanıtlanmış) doğal bir uzantısıdır.

## 26. Yeni Bağımlılık (LOCKED)

`pyproject.toml`'da hâlihazırda **hiçbir runtime dependency yok** (yalnızca `pytest`/`ruff` dev dependency). Foundation (Bölüm 22 FAZ6A) **hiçbir yeni dependency gerektirmez** — pure dataclass'lar + mevcut store-runner composition yeterlidir. İleri seviye istatistik (Bölüm 17) için hangi kütüphanelerin **ileride** faydalı olabileceği bu dokümanda **spekülatif olarak dahi listelenmez** — ihtiyaç kanıtlandığında, kendi dedicated mikro-adımında değerlendirilir.

## 27. Explicit Out-of-Scope (MUST NOT — Foundation ve Yakın Vade)

```
- generic ML framework / sklearn
- yalnızca bir-iki metrik için NumPy/pandas
- optimizer / grid-search / Bayesian optimization framework
- paralel / distributed backtest execution
- GPU training
- portfolio / multi-asset validation
- Monte Carlo simulation
- live/paper-trading promotion logic
- external LLM decision-making
```

## 28. Acceptance Criteria — Dört Ayrı Grup (LOCKED)

Foundation acceptance, runner-independent (pure/store-free) kontratlar ile Layer-1 context-aware runner acceptance kontratları (28.B, artık runtime/test exercised) **karıştırılmaz.** 28.B'nin karşılanması, Layer-2 çok-pencereli orchestrator'ın hazır olduğu anlamına **gelmez** (Bölüm 8.3.6, 13) — Layer-2'nin kendi gelecekteki policy-freshness implementasyon acceptance checklist'i, henüz runtime/test exercised OLMAYAN ayrı bir liste olarak 28.C'de kaydedilir. Stage-1 metrics'in (total return + max drawdown) implementasyon acceptance checklist'i de, artık implementation/test exercised olan ayrı bir liste olarak 28.D'de kaydedilir (bkz. Bölüm 15, 23 — 18/18). Önceki sürümün tek listedeki "15 madde" sayısı korunmaya çalışılmaz — spec wording'ine göre yeniden türetilmiştir (bkz. 28.A/28.B/28.C/28.D altındaki sayılar).

### 28.A — LOCKED FOUNDATION ACCEPTANCE (Runner-Bağımsız)

Bu MS1 ile lock edilebilen, MS2 gibi pure primitive'lerin temelini oluşturan kontratlar:

1. Temporal window genuine aware datetime kullanır — naive/pseudo-naive → `ValueError` (Bölüm 6).
2. Range half-open `[start, end)`'dir (Bölüm 6).
3. `start < end` zorunludur (Bölüm 6).
4. Pencere boundary'leri, kullanılan timeframe'in candle grid'ine aligned olmalıdır; hizasız boundary → açık `ValueError` (Bölüm 6).
5. IS/OOS overlap **yasaktır** → açık `ValueError` (Bölüm 7).
6. Overlap silent clip/trim ile repair edilmez (Bölüm 7).
7. IS/OOS arasında gap legal ve optional'dır — ne zorunlu, ne yasaktır (Bölüm 7).
8. Arbitrary bir temporal gap, formal embargo semantics'i ile eşitlenmez (Bölüm 7, 17.1).
9. Sessiz clip/normalize/sort/repair hiçbir zaman yapılmaz (Bölüm 6, 7, 24).
10. Fresh ECONOMIC OOS state prensibi: her bağımsız OOS evaluation `initial_cash`, flat pozisyon, `realized_pnl=0` ile başlar (Bölüm 10, 11).
11. IS'teki hiçbir ekonomik aksiyon/pending fill, bağımsız bir OOS evaluation'ına taşınamaz (Bölüm 11).
12. Historical information context (policy'nin gördüğü geçmiş candle'lar) ile economic account-state carry-in (cash/position/realized_pnl) **ayrı kavramlardır** ve karıştırılmaz (Bölüm 10).
13. Pre-OOS historical (information) context, prensip olarak legal olabilir (Bölüm 8, 8.1).
14. Exact warm-up/context API mekanizması **Bölüm 8.3'te LOCKED**'dır (FAZ6A MS3 pre-flight'ı + MS4 spec-lock'u ile) ve Layer-1 için **implement edilmiş + test edilmiştir** (bkz. Bölüm 23, 28.B — 15/15 runtime/test exercised) — ama bu implementasyon durumu, 28.A'nın MS1 zamanında lock edilen foundation kapsamını **genişletmez**; bu criterion yalnızca "mekanizma MS1'de LOCKED DEĞİLDİ, sonradan LOCKED edildi (ve implement edildi)" tarihsel gerçeğini kaydeder (bkz. Bölüm 8.1, 8.3, 23, 28.B).
15. `BacktestResult`, validation tarafından değişmeden (unchanged) canonical economic output olarak kalır (Bölüm 4, 21).
16. Validation, canonical backtest semantics'ini (replay/accounting/execution/cost/funding) compose eder — yeniden implement etmez (Bölüm 4, 21).
17. Validation-specific bir replay/accounting/execution engine **yasaktır** (Bölüm 4, 21).
18. Tüm girdi (store, partition, pencereler, `as_of_time`, config, policy, cost/funding model) explicit'tir (Bölüm 25).
19. Wallclock veya kontrolsüz/seed'siz randomness kullanılmaz (Bölüm 25).
20. Foundation scope tek-symbol'dür (Bölüm 5).
21. Foundation scope yalnızca `1h`/`4h` timeframe'i kapsar (Bölüm 5).
22. Foundation, hiçbir yeni runtime dependency gerektirmez (Bölüm 26).

**Locked foundation acceptance count: 22.**

### 28.B — LAYER-1 CONTEXT-AWARE ACCEPTANCE (15/15 RUNTIME/TEST EXERCISED)

Bu kriterlerin hepsi artık **Layer-1** (tek-pencere context-aware canonical replay + store-backed composition) için **RUNTIME/TEST EXERCISED**'dır — implementasyon (`src/crypto_quant_lab/backtest/replay.py`, `store_runner.py`) ve kendi regression suite'i (`tests/test_backtest_replay_context_evaluation.py` — 22 test, `tests/test_backtest_store_runner_context_evaluation.py` — 21 test) tamamlanmıştır.

**Bu, generic/context-aware (non-zero-context) bir Layer-2 OOS runner'ın hazır olduğu anlamına GELMEZ** — zero-context Layer-2 (`run_rolling_backtest_from_store`) artık implement edilmiş + test edilmiştir (Bölüm 8.3.6, 28.C — 12/12), ama context-aware bir varyant HENÜZ implement edilmemiştir. Ayrıca criterion 14'ün history-reconstructible (Type-H) niteliği, hâlâ mekanik olarak enforce edilemeyen bir semantic/caller precondition'dır (Bölüm 8.3.5) — bu, testlerin "kanıtladığı" bir şey değildir, yalnızca testlerin VARSAYDIĞI (Type-H policy fixture'ları kullanan) bir disiplin sınırıdır. **Foundation locked acceptance count'una (28.A) hâlâ dahil edilmezler** — bu ayrı bir sayımdır.

1. Legal pre-OOS context, candle quality gate'ten geçmiş (quality-gated) olmalıdır (Bölüm 8.2, 24).
2. Context, evaluation edilen pencere ile aynı exact partition'a (exchange/market_type/symbol/timeframe) ait olmalıdır (Bölüm 8.2).
3. Context, canonical ordering/finalization contract'ını korumalıdır — partial/live candle yasak (Bölüm 8.2, 9).
4. Context hiçbir future data içeremez (Bölüm 8.2, 9, 12).
5. Context, evaluation_start'tan önce hiçbir skorlanmış PnL üretemez (Bölüm 11, 8.3.4).
6. Context hiçbir carried pozisyon yaratamaz (Bölüm 11).
7. Context hiçbir pending fill yaratamaz (Bölüm 11).
8. Context, OOS başlangıç cash/account state'ini mutate edemez (Bölüm 11).
9. Ekonomik accounting, tam olarak Bölüm 8.3'te LOCKED olan evaluation_start boundary'sinde başlamalıdır (Bölüm 11, 13, 8.3.3).
10. Candle quality gate, context desteğiyle birlikte canonical kalmalıdır — bypass yasak (Bölüm 24).
11. `funding_required=True` olan bir ekonomik evaluation, funding quality gate'ini korumalıdır — bypass yasak (Bölüm 24).
12. Transaction cost semantics'i (`CostModel`) korunmalıdır (Bölüm 21, 24).
13. Funding chronology/cost semantics'i (`FundingModel`) korunmalıdır (Bölüm 21, 24).
14. **History-reconstructible (Type-H)** lookback kullanan bir `BacktestPolicy`, pencere-boundary distortion'ı olmadan (ilk N candle'ı context'siz kırmadan) değerlendirilebilmelidir (Bölüm 8.1, 8.3.5, 13). **Design: RESOLVED** (Bölüm 8.3.5) Type-H policy'ler için; **incremental-state (Type-I)** policy'ler bu mekanizma tarafından otomatik warm-up edilmez — bu, `BacktestPolicy`'nin global contract'ının değil, yalnızca context-aware evaluation'ın bir precondition'ıdır. **Implementation/Testing: TAMAMLANDI** (Layer-1, bkz. Bölüm 23) — Type-H policy fixture'ları kullanan regression testleriyle kanıtlanmıştır (örn. `test_type_h_policy_can_use_context_history_for_first_decision`); Type-H niteliğinin **kendisi** mekanik olarak enforce edilmez, hâlâ caller/policy-author sorumluluğudur.
15. Context desteği, canonical replay'i fork etmemeli / ikinci bir engine yaratmamalıdır (Bölüm 4, 21).

**Layer-1 runtime/test exercised acceptance count: 15 / 15.**

**Durum:** Bölüm 8.3'teki B2 kilidi Layer-1 için **implement edilmiş ve test edilmiştir** (bkz. Bölüm 23) — bu 15 kriterin hepsi artık **runtime/test exercised**'dır. Bu, Layer-2 (çok-pencereli orchestrator) veya Faz 6A'nın tamamının tamamlandığı anlamına **GELMEZ** — yalnızca context-aware Layer-1 acceptance contract'ının karşılandığı anlamına gelir.

**İleri seviye Faz 6 kategorileri (28.A/28.B/28.C/28.D'nin hiçbirine dahil DEĞİL, ayrı ve pending):** purging/embargo (17.1), CPCV (17.2), Sharpe-ailesi/return-series (16, 17.3), Deflated Sharpe (17.4), PBO (17.5), multiple-testing corrections (17.6), parameter stability (17.7), candidate/trial abstraction (18).

### 28.C — ZERO-CONTEXT LAYER-2 POLICY-FRESHNESS ACCEPTANCE (12/12 RUNTIME/TEST EXERCISED)

Bu liste, Bölüm 8.3.6'da LOCKED olan factory-based policy-instance-freshness mekanizmasının, **zero-context Layer-2 orchestrator** (`run_rolling_backtest_from_store`, `src/crypto_quant_lab/validation/rolling.py`, commit `c363267`; test-hardening `c4af87c`) tarafından karşılandığını kaydeder. **Bu 12 kriterin hepsi artık runtime/test exercised'dır** — `tests/test_validation_rolling_backtest.py`'de 28 test (tümü PASS); davranışsal kriterler doğrudan regression testleriyle, "değişmedi"/"coupled değil" türü kriterler ise static/scope kanıtı (`git diff` boş) + tam regression suite uyumluluğuyla (1386/1386 PASS) kanıtlanır. Bu kanıt **yalnızca zero-context slice içindir** — context-aware (non-zero-context) bir Layer-2 varyantı için otomatik/global bir garanti değildir (bkz. Bölüm 8.3.6, 13).

1. Sabit bir canonical pencere sırası için, execute edilen her pencere başına tam olarak bir factory çağrısı yapılır. **PASS** — `run_rolling_backtest_from_store`'daki `for index, window in enumerate(windows): policy = policy_factory()` döngüsü; `test_exactly_one_factory_call_per_window_in_order`.
2. Factory çağrı sırası deterministiktir ve canonical pencere sırasıyla eşleşir. **PASS** — aynı döngü, `enumerate(windows)` input sırasını korur; `test_exactly_one_factory_call_per_window_in_order`, `test_multiple_windows_preserve_exact_input_order`.
3. Her pencere, önceki/sonraki hiçbir pencereyle paylaşılmayan, distinct bir policy instance kullanır. **PASS** — reuse-detection döngüsü + `seen_policies: list[BacktestPolicy]`; `test_same_object_factory_output_is_rejected_before_affected_window_runs` (negatif), `test_stateful_fixture_shows_zero_cross_window_state_carryover` (pozitif), `test_prior_accepted_policies_remain_strongly_retained_throughout_orchestration` (weakref-tabanlı strong-retention kanıtı).
4. Bir factory iki pencere için aynı objeyi (object identity) döndürürse, bu durum etkilenen pencere execute edilmeden ÖNCE reddedilir. **PASS** — `if policy is previous_policy: raise ValueError(...)`, `run_backtest_from_store` çağrısından ÖNCE; `test_same_object_factory_output_is_rejected_before_affected_window_runs` (boundary-set üzerinden etkilenen pencerenin sıfır I/O yaptığı kanıtlanır).
5. Yapısal olarak geçersiz bir factory sonucu (çağrılabilir `target_position` sağlamayan), etkilenen pencere execute edilmeden ÖNCE reddedilir. **PASS** — `_require_valid_policy_result`, `run_backtest_from_store` çağrısından ÖNCE; `test_invalid_factory_output_is_rejected_before_affected_window_runs`.
6. Factory exception'ları sessizce yutulmaz veya başarılı/kısmi bir sonuca dönüştürülmez. **PASS** — `policy_factory()` çağrısının etrafında hiçbir try/except yoktur (kod incelemesiyle doğrulanır); `test_factory_exception_propagates_as_original_object` bunu `excinfo.value is expected_exception` ile — yalnızca type/mesaj değil, exact object identity ile — kanıtlar.
7. Stateful (Type-I) bir fixture policy, bağımsız pencereler arasında SIFIR state carryover gösterir (regression testiyle kanıtlanır). **PASS** — `test_stateful_fixture_shows_zero_cross_window_state_carryover`. **Not:** bu yalnızca freshness'i kanıtlar — Type-I otomatik warm-up'ın DESTEKLENDİĞİ anlamına gelmez (bkz. madde 10, Bölüm 8.3.5, 8.3.6).
8. Mevcut tek-pencere API'ler (`run_backtest_replay`, `run_backtest_from_store`) DEĞİŞMEDEN kalır — bu kriterler onları etkilemez. **PASS** — `git diff b626f5c..c363267 -- src/crypto_quant_lab/backtest/` boştur; `tests/test_backtest_replay_context_evaluation.py` (22 test) ve `tests/test_backtest_store_runner_context_evaluation.py` (21 test) DEĞİŞMEDEN yeşil kalır (regression suite'in parçası).
9. Küresel `BacktestPolicy` Protocol'ü (Bölüm 8.3.5) DEĞİŞMEDEN kalır. **PASS** — `git diff b626f5c..c363267 -- src/crypto_quant_lab/backtest/policy.py` boştur; `tests/test_backtest_policy.py` DEĞİŞMEDEN yeşil kalır (tam regression suite'in parçası).
10. Bu mekanizma, Type-I otomatik warm-up SAĞLADIĞINI veya Type-H niteliğini mekanik olarak ENFORCE ETTİĞİNİ iddia etmez (Bölüm 8.3.5, 8.3.6). **PASS** — `rolling.py`'de warm-up kodu YOKTUR; `_require_valid_policy_result`'ın docstring'i "bu asla semantik doğruluk, Type-H, veya Type-I geçerliliğini kanıtladığını iddia etmez" diye açıkça kaydeder. Bu, absence-of-claim bir kriterdir — runtime testle değil, kod/docstring incelemesiyle kanıtlanır.
11. Bu mekanizmanın implementasyonu, metrics, candidate/trial aggregation, optimizer, veya herhangi bir advanced-validation tekniğiyle (Bölüm 17) COUPLE edilmez. **PASS** — `rolling.py` metrics/candidate/optimizer/Bölüm-17 kavramlarına hiçbir import veya referans içermez (kod incelemesiyle doğrulanır, absence-of-coupling kriteri).
12. İkinci/forked bir replay engine yaratılmaz — canonical `run_backtest_replay` compose edilmeye devam eder (Bölüm 4, 21). **PASS** — `rolling.py` yalnızca `run_backtest_from_store`'u import eder (`run_backtest_replay` doğrudan hiç import/çağrılmaz); her pencere TEK bir `run_backtest_from_store` çağrısına delege eder; `test_rolling_output_matches_direct_per_window_composition` bağımsız direct-call kompozisyonuyla byte-identical `BacktestResult` eşitliğini kanıtlar.

**Zero-context Layer-2 policy-freshness acceptance count: 12 / 12 runtime/test exercised.** Bu sayım, 28.A'nın (22) veya 28.B'nin (15/15) hiçbirine dahil değildir — ayrı bir sayımdır. **Bu, context-aware (non-zero-context) bir Layer-2 varyantının, metrics foundation'ının, veya Faz 6B/6C'nin tamamının tamamlandığı anlamına GELMEZ** — yalnızca zero-context rolling fixed-policy orchestrator'ın kendi acceptance contract'ının karşılandığı anlamına gelir.

### 28.D — STAGE-1 METRICS FOUNDATION ACCEPTANCE (18/18 IMPLEMENTATION/TEST EXERCISED)

Bu liste, Bölüm 15.1–15.8'de LOCKED olan Stage-1 metrics (total return + max drawdown) exact kontratının, `src/crypto_quant_lab/validation/metrics.py` (commit `a265e44`) tarafından karşılandığını kaydeder. **Bu 18 kriterin hepsi artık implementation/test exercised'dır** — `tests/test_validation_metrics.py`'de 82 test (tümü PASS), ilgili regression suite'ler (`test_backtest_models.py`, `test_backtest_results.py`, `test_validation_rolling_backtest.py` — 105 test) DEĞİŞMEDEN yeşil, tam suite 1468/1468 PASS, post-commit implementasyon audit'i PASS. Davranışsal kriterler doğrudan regression testleriyle, "değişmedi"/"coupled değil" türü kriterler ise static/scope kanıtı (`git diff 76002ab..a265e44` boş) + tam regression suite uyumluluğuyla kanıtlanır — bu ikisi ayrı ayrı etiketlenir, biri diğeri yerine geçmez. Test-kanıtı grupları: value-object invariants (18 test), validation/fail-fast (21 test), total return (13 test), maximum drawdown (14 test), Decimal-context determinism (5 test), purity/compatibility (9 test), canonical integration (bu gruplardan 6 test, gerçek `run_backtest_from_store`/`run_rolling_backtest_from_store` kullanır), non-finite computed-output (2 test) — toplam 82; bu 82 test, aşağıdaki 18 kriterle birebir eşlenmez, kriterler bu test gruplarının toplamından kanıt alır.

1. `Stage1Metrics` ve `compute_stage1_metrics`, kilitli modül yolunda mevcuttur. **PASS** — `src/crypto_quant_lab/validation/metrics.py`; tüm 82 test bu sembolleri import edip kullanır.
2. `Stage1Metrics` frozen/slotted'dır ve kendi field invariant'larını enforce eder (Bölüm 15.3). **PASS** — `@dataclass(frozen=True, slots=True)`, `__post_init__`; `test_stage1_metrics_is_frozen`, `_is_slotted`, ve 8 invariant testi (non-Decimal/non-finite/negatif/sınır-kabul).
3. Yanlış `result` tipi deterministik olarak fail eder (`TypeError`). **PASS** — `_require_backtest_result`; `test_rejects_non_backtest_result_input`.
4. Non-finite veya non-positive `initial_cash`, hesaplamadan ÖNCE fail eder (`ValueError`). **PASS** — Bölüm 15.4 adım 2-3; `test_rejects_nan/infinite/zero/negative_initial_cash` + sıra-kanıtlayan testler.
5. Non-finite `final_equity` veya curve equity'si deterministik olarak fail eder (`ValueError`). **PASS** — adım 4, 7; `test_rejects_nan/infinite_final_equity`, `_curve_equity_with_index`.
6. Boş equity curve reddedilir (`ValueError`). **PASS** — `_require_valid_equity_curve` adım 5; `test_rejects_empty_equity_curve`.
7. Geçersiz curve elemanları, kendi index'leriyle birlikte reddedilir (`TypeError`). **PASS** — adım 6; `test_rejects_invalid_curve_member_at_index_0/later_index`.
8. Curve timestamp'leri strictly ascending olmalıdır; değilse fail eder (`ValueError`). **PASS** — adım 8; `test_rejects_duplicate/descending_timestamps`.
9. Son curve equity'si `final_equity`'e eşit olmalıdır; değilse fail eder (`ValueError`). **PASS** — adım 9; `test_rejects_terminal_equity_not_matching_final_equity`.
10. Total return, exact kilitli formülü ve Decimal-fraction convention'ını kullanır (Bölüm 15.5). **PASS** — `final_equity / initial_cash - Decimal(1)`; 13 total-return testi, özellikle `test_total_return_exact_locked_operation_order` (precision-28'de forbidden rewrite'tan somut olarak farklı son basamak).
11. Total return, canonical final-equity ekonomik etkilerini yeniden hesaplamadan içerir. **PASS** — gerçek engine sonuçlarıyla: `test_total_return_costs_already_reflected`, `_funding_already_reflected`, `_unrealized_mark_to_market_already_reflected`.
12. Maximum drawdown, peak'i `initial_cash`'ten seed eder (Bölüm 15.6). **PASS** — `peak = result.initial_cash`; `test_max_drawdown_immediate_first_point_loss_from_initial_cash_peak`.
13. Flat/rising, immediate loss, multiple drawdowns, full loss, recovery, ve final-trough case'leri, kilitlendiği gibi davranır. **PASS** — 14 maximum-drawdown testi.
14. Negatif equity, 1'i aşan bir drawdown üretebilir; yapay bir üst sınır yoktur. **PASS** — `test_max_drawdown_negative_equity_after_positive_peak_exceeds_one`, `_no_artificial_cap`.
15. Çıktılar, ambient Decimal precision/rounding'den bağımsızdır ve kilitli private context'i kullanır (Bölüm 15.7). **PASS** — `_stage1_decimal_context()` + `localcontext`; 5 Decimal-context determinism testi, davranışsal olarak (ambient context mutate edilip çıktı kilitli 28-digit değerle karşılaştırılarak) kanıtlanmıştır — yalnızca private constant incelemesiyle değil.
16. Hesaplama pure, deterministik, yalnızca-Decimal'dır ve input'u mutate etmez (Bölüm 15.8). **PASS** — `test_input_result_unchanged_after_computation`, `_deterministic_repeated_computation`, `_returned_values_are_decimal_never_float`.
17. Doğrudan `BacktestResult` kullanımı VE bağımsız `WindowResult.result` kullanımı, hiçbir aggregation olmadan desteklenir. **PASS** — `test_window_result_evaluated_independently_via_rolling`, `_no_cross_window_aggregation_occurs`.
18. Mevcut result modelleri, replay/store/rolling API'leri, ve ileri-seviye metrics kontratları DEĞİŞMEDEN/coupled-olmadan kalır. **PASS** — static kanıt: `git diff 76002ab..a265e44 -- src/crypto_quant_lab/backtest/ src/crypto_quant_lab/validation/rolling.py src/crypto_quant_lab/validation/windows.py` boştur; regression kanıtı: ilgili 105 test + tam suite 1468 DEĞİŞMEDEN yeşil; ayrıca `test_backtest_result_has_no_metrics_fields`, `_window_result_has_no_metrics_fields`, `_metrics_module_does_not_import_rolling`.

**Stage-1 metrics foundation acceptance count: 18 / 18 implementation/test exercised.** Bu sayım, 28.A'nın (22), 28.B'nin (15/15), veya 28.C'nin (12/12) hiçbirine dahil değildir — ayrı bir sayımdır. **Bu, Aşama 2 (return-series/Sharpe), Aşama 3 (Deflated Sharpe/PBO/multiple-testing/parameter stability), context-aware Layer-2, veya Faz 6B/6C'nin tamamının tamamlandığı anlamına GELMEZ** — yalnızca Stage-1 (total return + max drawdown) foundation'ının kendi acceptance contract'ının karşılandığı anlamına gelir.

## 29. Faz 6 Sonrası (Bilgi Amaçlı — Bu Dokümanda Tasarlanmaz)

ROADMAP.md'deki bir sonraki faz **Faz 7 — İlk Funding/Basis araştırması**dır. Faz 7'nin güvenilir olabilmesi için, en azından Bölüm 22'deki FAZ6A (temporal split + rolling fixed-policy OOS evaluation + basic return/drawdown metrikleri) tamamlanmış olmalıdır — bu, Faz 7'nin IS'te seçilen bir funding/basis sinyalini gerçekten görülmemiş bir OOS penceresinde kontrol edebilmesi için minimum güven sınırıdır. Faz 6'nın daha ileri maddeleri (CPCV/PBO/DSR), Faz 7'nin **başlaması** için zorunlu değildir, ama FAZ6A'nın kendisi zorunludur. Bu doküman Faz 7'nin strateji tasarımını **yapmaz.**
