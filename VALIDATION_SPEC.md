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

**Durum: LOCKED (mimari/tasarım) VE Layer-1 için IMPLEMENTED + TESTED.** Bu bölüm, Bölüm 8/8.1'in açık bıraktığı kararı kilitler VE bu kararın Layer-1 (tek-pencere context-aware canonical replay + store-backed composition) implementasyonunun exact şeklini kaydeder — `run_backtest_replay`/`run_backtest_from_store` artık `evaluation_start: datetime | None = None` üzerinden bu bölümdeki ayrımı bilir, kendi regression suite'i ile test edilmiştir (Bölüm 8.3.11, 23; Bölüm 9 artık tarihsel/RESOLVED). Layer-2 (çok-pencereli orchestrator), zero-context için artık **IMPLEMENTED + TESTED**'dır (`run_rolling_backtest_from_store`, bkz. Bölüm 8.3.6, 13, 23, 28.C — 12/12); context-aware (non-zero-context) bir Layer-2 varyantı da artık, Bölüm 8.3.16'da LOCKED olan exact kontratıyla, **IMPLEMENTED + TESTED**'dır (`ContextAwareWindow`, `run_context_aware_rolling_backtest_from_store`, bkz. Bölüm 23, 28.F — 22/22).

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

**Durum: LOCKED (mimari/tasarım) VE zero-context Layer-2 orchestrator için IMPLEMENTED + TESTED.** Bu bölüm, yukarıdaki (B)'nin exact mekanizmasını kilitler VE bu kararın **zero-context Layer-2** (bkz. Bölüm 13) implementasyonunun exact şeklini kaydeder — `src/crypto_quant_lab/validation/rolling.py`'deki `run_rolling_backtest_from_store`, `policy_factory: Callable[[], BacktestPolicy]` üzerinden bu bölümdeki mekanizmayı bilir, kendi regression suite'i ile test edilmiştir (FAZ6B MS2 implementasyonu `c363267`, test-hardening `c4af87c`; bkz. Bölüm 23, 28.C). **Non-zero-context (context-aware) bir Layer-2 varyantı bu spesifik implementasyonun (zero-context) kapsamında DEĞİLDİR** — freshness mekanizmasının non-zero-context reuse'u artık Bölüm 8.3.16'da LOCKED'dır VE `run_context_aware_rolling_backtest_from_store` olarak İMPLEMENT EDİLMİŞ + TEST EDİLMİŞTİR (bkz. bu bölümün altındaki "Implementasyon Durumu" notu, Bölüm 8.3.16, 28.F — 22/22).

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

**Kapsam sınırı (önemli):** bu implementasyon **zero-context**'tir — her pencere `evaluation_start = window.start` ile çalışır, yani context yoktur (bkz. Bölüm 8.3.1, 8.3.11, 13). Bu implementasyonun kanıtladığı şey, **`run_rolling_backtest_from_store` için** mekanik policy-freshness enforcement'ının doğru çalıştığıdır — repository-wide, arbitrary gelecekteki caller'lar veya gelecekteki context-aware Layer-2 varyantları için otomatik/global bir garanti DEĞİLDİR. Context-aware (non-zero-context, `context_start < evaluation_start`) Layer-2 pencereleri, bu spesifik (zero-context) implementasyonun bir parçası DEĞİLDİR; bunlar için ayrı bir passive context-window modeli (`ContextAwareWindow`) ve onu kullanan `run_context_aware_rolling_backtest_from_store` orchestrator'ı, Bölüm 8.3.16'da LOCKED'dır VE artık İMPLEMENT EDİLMİŞ + TEST EDİLMİŞTİR (bkz. 28.F — 22/22).

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

**8.3.16 Non-Zero-Context Layer-2 — Exact Contract (LOCKED VE artık IMPLEMENTED + TESTED)**

**Durum: LOCKED (mimari/tasarım) VE artık IMPLEMENTED + TESTED.** Implementasyon `src/crypto_quant_lab/validation/rolling.py`'de tamamlanmıştır — `ContextAwareWindow` + `run_context_aware_rolling_backtest_from_store`, mevcut `run_rolling_backtest_from_store` ile paylaşılan bir private `_execute_windows` helper'ı üzerinden. Kendi regression suite'i `tests/test_validation_rolling_backtest.py`'de (94 test, tümü PASS: 28 mevcut zero-context test DEĞİŞMEDEN + 66 yeni non-zero-context test). İlgili regression suite'ler (`test_backtest_replay_context_evaluation.py`, `test_backtest_store_runner_context_evaluation.py`, `test_validation_metrics.py` — 250 test) DEĞİŞMEDEN yeşil kalır; tam suite 1659/1659 PASS. Post-commit implementasyon audit'i — PASS (bkz. Bölüm 23, 28.F — 22/22). Bu bölüm, mevcut zero-context Layer-2 (`run_rolling_backtest_from_store`) ve mevcut context-aware Layer-1'in (`evaluation_start`, Bölüm 8.3.1–8.3.15) kaynak kodundan doğrudan doğrulanmış bir source-preflight'e dayanır — eski dokümantasyondan değil.

**Source-preflight bulguları (kaynak koddan doğrulanmıştır):**

```
- Mevcut execution flow: window generation (TemporalWindow, windows.py)
  -> rolling Layer-2 orchestration (run_rolling_backtest_from_store,
  rolling.py) -> per-window policy_factory() çağrısı + object-identity
  freshness kontrolü -> store-backed Layer-1 execution
  (run_backtest_from_store, store_runner.py) -> replay/context warm-up
  (run_backtest_replay, evaluation_start, replay.py) -> evaluation-only
  ekonomik aktivite -> BacktestResult -> WindowResult -> opsiyonel
  Stage-1/Stage-2 metrik hesaplama (metrics.py, WindowResult.result
  üzerinden bağımsız).
- run_rolling_backtest_from_store (rolling.py): windows: tuple[TemporalWindow, ...]
  kabul eder; her pencere için requested_start=window.start,
  requested_end=window.end, evaluation_start=window.start ile ÇAĞRILIR
  — yani zero-context, `context_start == evaluation_start` özel
  durumunun (Bölüm 8.3.1) doğrudan bir örneğidir. Duplicate/overlapping
  pencereler REDDEDİLMEZ/sort edilmez/clip edilmez (kaynak: rolling.py
  docstring'i ve test_duplicate_equal_windows_are_not_deduplicated,
  test_overlapping_windows_are_not_sorted_clipped_merged_or_rejected).
  policy_factory pencere-başına TAM OLARAK bir kez, I/O'dan ÖNCE
  çağrılır; object-identity (never equality) reuse-detection; strongly
  retained instance list; fail-fast, rollback yok, partial result yok.
- run_backtest_from_store (store_runner.py) zaten `evaluation_start:
  datetime | None = None` additive keyword-only parametresini bilir:
  requested_start=context_start rolünü oynar, requested_end=
  evaluation_end rolünü oynar, evaluation_start yeni ekonomik sınırdır.
  Context candle'lar (open_time < evaluation_start) run_backtest_replay
  döngüsünde `continue` ile SIFIR EquityPoint/fill/cost/funding/PnL
  üretir (Bölüm 8.3.2, 15.10 — bağımsız olarak yeniden doğrulanmıştır,
  replay.py satır ~371-394). Funding sorgusu yalnızca
  [evaluation_start, evaluation_end) aralığındadır (Bölüm 8.3.7).
  Stage A (I/O öncesi) + Stage B (candle I/O sonrası, funding I/O
  öncesi) iki aşamalı evaluation_start validation'ı zaten mevcuttur.
- Metrics compatibility (metrics.py, Bölüm 15.10): Stage-1/Stage-2
  yalnızca result.equity_curve + result.initial_cash tüketir; ikisi de
  hiçbir evaluation_start/context_start parametresi kabul etmez veya
  ima etmez; equity_curve zaten evaluation-only olduğundan
  (compute edilmiş her WindowResult.result için) ikinci bir
  boundary-filtresi GEREKMEZ. Hiçbir metrics API/model değişikliği bu
  kontrat için GEREKLİ DEĞİLDİR.
```

**Seçilen mimari:** mevcut `validation/rolling.py` modülünde, mevcut zero-context `run_rolling_backtest_from_store`'un semantics'ini DEĞİŞTİRMEDEN, additive bir ikinci public fonksiyon + yeni bir per-window context-boundary value object.

**Reddedilen alternatifler (concrete gerekçelerle):**

```
1. Mevcut run_rolling_backtest_from_store'u yeni bir opsiyonel context
   argümanıyla GENİŞLETMEK — REDDEDİLDİ: §28.C'nin 12/12 runtime/test
   exercised zero-context sözleşmesini (exact signature, davranış,
   hata mesajları) riske atar; iki farklı semantics (context'siz/
   context'li) TEK fonksiyonda validation-sırası karmaşıklığı yaratır.
   "Prefer an additive explicit API over changing established
   zero-context semantics" strong preference'ıyla tutarsız.
2. Açık yeni bir non-zero-context rolling fonksiyonu eklemek —
   SEÇİLDİ: mevcut zero-context runner'ı hiç etkilemez, isim açıkça
   ayırt edilebilir.
3. Açık bir per-window context-boundary value object'i tanıtmak —
   SEÇİLDİ: context_start'ı, iki paralel eş-uzunluklu tuple'ın (windows
   + context_starts) index-eşleşme kırılganlığı olmadan, her pencere
   için tek, açık, reproducible bir obje içinde taşır.
4. Her pencere için context_start'ı tek bir uniform timedelta/context
   span'den türetmek — REDDEDİLDİ (orchestrator'ın kendi mekanizması
   olarak): Bölüm 8.3.13'ün "Context Boundary — Explicit, Auto-
   Inference YOK" kilidini ihlal eder — context_start caller tarafından
   AÇIKÇA seçilmelidir, otomatik türetilmemelidir. (Bir caller,
   kendi kodunda context_start = window.start - span hesaplayıp
   ContextAwareWindow'a açıkça geçirebilir — bu API'nin kendisi
   böyle bir hesaplamayı YAPMAZ.)
5. Context boundary'lerini bir callback/factory ile türetmek —
   REDDEDİLDİ: "Prefer explicit, serializable/reproducible boundary
   data over a hidden callback" strong preference'ı; bir callback
   audit edilebilir/reproducible değildir.
6. Mevcut window/result modellerini (TemporalWindow, TemporalSplit,
   WindowResult, BacktestResult) DEĞİŞTİRMEK — REDDEDİLDİ: hiçbiri
   context_start'ı taşımaya ihtiyaç duymaz; yeni, küçük, additive bir
   value object (mevcut TemporalWindow'u compose ederek) bu ihtiyacı
   hiçbirini değiştirmeden karşılar (Bölüm 21 Backward Compatibility,
   8.3.12 ile tutarlı).
7. Mevcut modelleri değiştirmeden boundary'leri AYRI, paralel argüman
   olarak geçirmek (örn. windows + context_starts iki ayrı tuple) —
   REDDEDİLDİ (primary mekanizma olarak): index-eşleşme kırılganlığı;
   seçenek 3'ün tek-obje-per-pencere yaklaşımı daha güvenli.
```

**Exact Public API (LOCKED):**

```
Modül:  src/crypto_quant_lab/validation/rolling.py  (mevcut modül,
        YENİ bir modül DEĞİL — Bölüm 4/21 compose-not-duplicate
        prensibiyle tutarlı)

@dataclass(frozen=True, slots=True)
class ContextAwareWindow:
    context_start: datetime
    evaluation: TemporalWindow

def run_context_aware_rolling_backtest_from_store(
    store: HistoricalCandleStore,
    windows: tuple[ContextAwareWindow, ...],
    *,
    policy_factory: Callable[[], BacktestPolicy],
    exchange: str,
    market_type: str,
    symbol: str,
    timeframe: str,
    as_of_time: datetime,
    config: BacktestConfig,
    cost_model: CostModel,
    funding_required: bool = False,
    funding_store: HistoricalFundingStore | None = None,
    funding_model: FundingModel | None = None,
) -> tuple[WindowResult, ...]:
    ...
```

```
- İsim (`run_context_aware_rolling_backtest_from_store`), mevcut
  zero-context `run_rolling_backtest_from_store` ile KARIŞTIRILAMAZ —
  bu dokümanın kendi "context-aware" terminolojisiyle (§22 FAZ6B
  başlığı, §8.3) birebir tutarlıdır.
- Parametre listesi (windows'un tipi HARİÇ), zero-context runner'ın
  parametre listesiyle POZİSYONEL OLARAK AYNIDIR — yalnızca
  `windows: tuple[TemporalWindow, ...]` yerine
  `windows: tuple[ContextAwareWindow, ...]`.
- Dönüş tipi DEĞİŞMEDEN: tuple[WindowResult, ...] — WindowResult
  (window: TemporalWindow, result: BacktestResult) modeline HİÇBİR
  değişiklik yapılmaz.
- İki public fonksiyon, freshness/validation/execution loop'unu
  DUPLICATE ETMEMEK için bir private orchestration helper'ı paylaşır
  (Bölüm 7'nin "Shared private validation/orchestration helpers are
  allowed when they preserve the zero-context runner byte-for-byte
  behaviorally" ilkesiyle tutarlı) — exact private helper imzası
  implementasyon mikro-adımına ertelenir; LOCKED olan şey, zero-context
  runner'ın §28.C'de kanıtlanmış davranışının bu refactor'dan SONRA da
  byte-for-byte DEĞİŞMEDEN kalması gerekliliğidir (mevcut 28 test
  DEĞİŞMEDEN yeşil kalmalıdır).
- Package-root export YOK — mevcut zero-re-export convention'ıyla
  tutarlı (validation/__init__.py DEĞİŞMEZ).
```

**`ContextAwareWindow` — exact value object (LOCKED):**

```
- Modül: src/crypto_quant_lab/validation/rolling.py (windows.py DEĞİL
  — bu obje Layer-2 context-orchestration'a özgüdür, TemporalWindow/
  TemporalSplit gibi genel-amaçlı, engine-agnostik bir primitive
  DEĞİLDİR).
- Field sırası: context_start (datetime), evaluation (TemporalWindow).
- Frozen, slotted (mevcut TemporalWindow/TemporalSplit/WindowResult
  convention'ıyla birebir aynı).
- __post_init__ validation sırası:
  1. evaluation bir TemporalWindow olmalıdır; değilse TypeError
     (TemporalSplit'in TemporalWindow'u değiştirmediği gibi, bu obje
     de TemporalWindow'u DEĞİŞTİRMEZ — onu compose eder).
  2. context_start, genuine aware datetime olmalıdır (mevcut
     datetime_to_epoch_us reuse edilir — TemporalWindow'un kendi
     start/end validation'ıyla AYNI mekanizma); naive/pseudo-naive
     → ValueError.
  3. context_start <= evaluation.start olmalıdır; context_start >
     evaluation.start → ValueError (Bölüm 8.3.1'in "context_start >
     evaluation_start INVALID" kuralının birebir aynısı).
     context_start == evaluation.start LEGAL'dir (Bölüm 8.3.1'in
     "sıfır context" özel durumu, ayrı bir kod yolu değildir — bu
     obje ZERO-context bir pencereyi de temsil edebilir, ama
     dedicated zero-context runner o durum için hâlâ daha basit/
     canonical API'dir).
- Grid-alignment KONTROL EDİLMEZ burada — TemporalWindow'un kendisi
  timeframe taşımaz (Bölüm 6: "Grid alignment is enforced by
  TemporalSplit... TemporalWindow timeframe-agnostic'tir"); grid-
  alignment kontrolü Layer-1'in kendi mevcut evaluation_start
  validation'ına (store_runner.py, timeframe orchestrator argümanı
  üzerinden) BIRAKILIR — ikinci bir data-access/validation yolu
  YARATILMAZ.
- Value equality/hashability: frozen dataclass default'ları (Stage-1/
  Stage-2/TemporalWindow/TemporalSplit/WindowResult ile aynı desen).
- WindowResult'a eşleme: WindowResult.window = <ilgili
  ContextAwareWindow>.evaluation (TAM ContextAwareWindow DEĞİL) —
  WindowResult modeli BÖYLECE DEĞİŞMEDEN kalır (Bölüm 21). context_start
  bu nedenle WindowResult İÇİNDE saklanmaz; call sonrası auditability,
  caller'ın kendi elindeki orijinal `windows: tuple[ContextAwareWindow, ...]`
  girdisiyle — output ordering input ordering'i KORUDUĞU için —
  index bazlı correlate edilerek sağlanır (zero-context runner'ın da
  WindowResult.window == girdi TemporalWindow'u için zaten kullandığı
  AYNI precedent).
- Public'tir (validation/__init__.py'den re-export edilmez, ama modül
  içinden doğrudan import edilebilir — mevcut Stage1Metrics/
  Stage2Metrics/WindowResult precedent'iyle aynı).
```

**Boundary semantics (LOCKED — Bölüm 8.3.1/8.3.7/8.3.8'in birebir reuse'u):**

```
Context range:    [context_start, evaluation.start)
Evaluation range: [evaluation.start, evaluation.end)

context_start == evaluation.start   LEGAL (sıfır context)
context_start > evaluation.start    INVALID (ContextAwareWindow
                                     construction'da reddedilir)
evaluation.start >= evaluation.end  INVALID (TemporalWindow'un kendi
                                     __post_init__'i tarafından zaten
                                     reddedilir)

Candle store request per pencere: [context_start, evaluation.end) —
  TEK bir run_backtest_from_store çağrısı (requested_start=
  context_start, requested_end=evaluation.end, evaluation_start=
  evaluation.start); context VE evaluation candle'ları AYNI çağrıda
  yüklenir — ikinci bir store request YOK.
Funding request per pencere: yalnızca [evaluation.start,
  evaluation.end) — context için funding SORGULANMAZ/GEREKMEZ
  (Bölüm 8.3.7'nin birebir reuse'u).

İlk kullanılabilir context candle: context_start'a eşit veya ondan
  sonraki ilk candle (Layer-1'in mevcut prepare_backtest_dataset
  semantics'i, DEĞİŞMEDEN).
İlk evaluation candle: open_time == evaluation.start olan candle
  (dataset'in mevcut gapless-contiguity + grid-alignment garantisi
  sayesinde var olduğu kanıtlanmıştır — ayrı bir arama YOK, Bölüm
  8.3.1).
Son evaluation candle: evaluation.end'den önceki, mevcut effective_end
  clamp semantics'ine (Stage B, Bölüm 8.3.11) tabi son candle.

Eksik/hizasız/gapped context candle → mevcut canonical candle quality
  gate üzerinden FAIL (Bölüm 8.3.8, 24) — ikinci bir gate YOK.
Context, dataset'in mevcut kapsamının ÖNCESİNE uzanıyorsa → mevcut
  store-coverage/quality-gate FAIL semantics'i (özel bir durum
  YOKTUR — context_start, Layer-1'in gözünden sıradan bir
  requested_start'tır).

Pencereler arası (input windows tuple boyunca):
- Input window sırası KORUNUR — sıralama YOK.
- Pencereler (evaluation aralıkları) overlap edebilir, dokunabilir,
  veya duplicate olabilir — REDDEDİLMEZ/sort edilmez/clip edilmez
  (zero-context Layer-2'nin mevcut, test-kanıtlı invariant'ının
  birebir uzantısı: test_overlapping_windows_are_not_sorted_
  clipped_merged_or_rejected, test_duplicate_equal_windows_are_not_
  deduplicated).
- Bir pencerenin context aralığı, başka bir pencerenin evaluation
  veya context aralığıyla overlap edebilir — REDDEDİLMEZ (her pencere
  bağımsız bir run_backtest_from_store çağrısıdır; aralarında hiçbir
  paylaşılan ekonomik/feature state YOKTUR).
```

**Warm-up vs ekonomi (LOCKED — Bölüm 8.3.2–8.3.4'ün birebir reuse'u, ikinci bir mekanizma YOK):**

```
Context candle'lar:
- policy.target_position ÇAĞIRAMAZ (koşulsuz, Bölüm 8.3.2).
- policy-owned state'i (Type-H/Type-I, Bölüm 8.3.5) SESSİZCE ISITAMAZ
  — Type-I otomatik warm-up hâlâ DESTEKLENMEZ.
- Engine-owned market history'yi (PolicyContext.candles prefix'i)
  BESLER — bu "warm-up"ın tek kanalıdır.
- SIFIR fill, SIFIR cost, SIFIR funding, SIFIR equity point, SIFIR
  PnL, SIFIR account-state mutasyonu üretir.
final_equity ve Stage-1/Stage-2 metrikleri, YALNIZCA evaluation-fazı
  ekonomisinden türetilir.
Bu orchestrator, replay.py'de HİÇBİR yeni satır GEREKTİRMEZ — yalnızca
  context_start/evaluation.start/evaluation.end'i mevcut
  requested_start/evaluation_start/requested_end parametrelerine
  FORWARD eder.
```

**Policy freshness / per-pencere izolasyon (LOCKED — Bölüm 8.3.6'nın birebir reuse'u):**

```
- policy_factory() pencere başına TAM OLARAK bir kez, o pencerenin
  I/O'sundan HEMEN ÖNCE, input sırasında çağrılır.
- Her sonuç, çağrılabilir target_position için I/O'dan ÖNCE
  yapısal olarak kontrol edilir.
- Reuse tespiti yalnızca object identity (`is`) ile — equality
  DEĞİL; kabul edilen instance'lar orchestration boyunca strongly
  retained tutulur.
- policy_factory bir window argümanı ALMAZ (Callable[[], BacktestPolicy]
  — zero-context ile AYNI imza).
- Pencereler arası hiçbir engine-state/cash/pozisyon/mutable-collection
  reuse'u YOKTUR (her pencere Layer-1'in kendi "fresh AccountState
  per run" garantisiyle, Bölüm 8.3.3, bağımsızdır).
- Sonuç aggregation YOK — WindowResult'lar düz bir tuple'a toplanır.
- Çıktı sırası = girdi sırası.
- Pencere N'de fail (construction/validation/execution): N ve
  sonrası EXECUTE EDİLMEZ; önceki pencereler zaten execute edilmiş
  OLABİLİR; rollback YOK; kısmi (partial) bir tuple DÖNDÜRÜLMEZ —
  fonksiyon raise eder.
- Pencere/context overlap'inden BAĞIMSIZDIR (yukarıdaki boundary
  semantics).
```

**Validation / fail-fast sırası (LOCKED, exact):**

```
1. windows bir tuple olmalıdır; her eleman bir ContextAwareWindow
   olmalıdır (index-specific TypeError) — ContextAwareWindow'un
   KENDİ context_start/evaluation invariant'ları (yukarıda) ZATEN
   construction anında kanıtlanmıştır; burada TEKRAR kontrol
   EDİLMEZ (rolling.py'nin mevcut _require_windows_tuple'ının
   TemporalWindow için izlediği AYNI prensip: "Does not duplicate
   [the element's] own datetime/boundary validation, which already
   ran at each instance's construction").
2. policy_factory callable olmalıdır; değilse TypeError.
3. Pencere başına, sırayla: policy_factory() çağrılır -> yapısal
   target_position kontrolü (TypeError, index-specific) -> object-
   identity reuse kontrolü (ValueError, index-specific) -> TEK bir
   run_backtest_from_store(requested_start=window.context_start,
   requested_end=window.evaluation.end, evaluation_start=
   window.evaluation.start, ...) çağrısı. Bu çağrının KENDİ iç
   validation zinciri (funding config -> evaluation_start Stage A ->
   dataset prep/quality-gate -> evaluation_start Stage B -> funding
   quality-gate -> replay) burada TEKRARLANMAZ/DUPLICATE EDİLMEZ.

Hiçbir validation sessizce:
- pencereleri sort ETMEZ
- pencereleri dedupe ETMEZ
- context'i clamp ETMEZ
- timestamp repair ETMEZ
- geçersiz pencereleri drop ETMEZ
- fail eden bir pencereden SONRA devam ETMEZ
- fail'den SONRA partial bir successful tuple DÖNDÜRMEZ
```

**Store / funding semantics:** yukarıdaki "Boundary semantics" ve "Warm-up vs ekonomi" bölümlerinde LOCKED — ikinci bir data-access yolu YOK, mevcut Layer-1 (`run_backtest_from_store`) TEK sahip kalır.

**Output / compatibility (LOCKED):**

```
- tuple[WindowResult, ...] — girdi windows ile AYNI uzunlukta, AYNI
  sırada (index i -> windows[i]).
- WindowResult.window == windows[i].evaluation.
- WindowResult.result == o pencerenin BacktestResult'ı.
- context_start WindowResult İÇİNDE saklanmaz (yukarıda gerekçeli).
- BacktestResult, EquityPoint, WindowResult, TemporalWindow,
  TemporalSplit, ve mevcut zero-context run_rolling_backtest_from_store
  DEĞİŞMEZ.
- Cross-window aggregation, candidate/trial coupling, serialization/
  persistence, veya optimizer coupling YOK.
```

**Metrics compatibility (LOCKED — Bölüm 15.10'un birebir uzantısı):** `compute_stage1_metrics`/`compute_periodic_returns`/`compute_stage2_metrics`, bu yeni orchestrator'ın ürettiği her `WindowResult.result` üzerinde, HİÇBİR değişiklik olmadan, bağımsız olarak çalışır — `equity_curve` zaten evaluation-only'dir (context candle'lar bu orchestrator için de SIFIR EquityPoint üretir, Bölüm 8.3.2/15.10 ile aynı runtime kanıtı). İkinci bir evaluation-boundary filtresi metrics katmanında GEREKMEZ/EKLENMEZ.

**Purity / determinism (LOCKED):** girdi `windows`/`ContextAwareWindow`/`TemporalWindow` mutate edilmez (zaten frozen); `config` mutate edilmez; store data mutate edilmez; eşdeğer girdilerle tekrar çağrılar eşdeğer sonuç üretir; pencere execution'ı SEQUENTIAL'dır (paralel/distributed execution bu mikro-adımda TANITILMAZ, Bölüm 27).

**Error semantics (kavramsal, Bölüm 8.3.15 ile aynı konvansiyon):** TypeError (yanlış tip) / ValueError (yanlış değer), index-specific mesajlar (etkilenen `windows[i]` index'ini içerir). Exact mesaj string'leri burada kilitlenmez — implementasyonun kendi regression suite'i tanımlar, mevcut proje konvansiyonuyla tutarlı.

**Test kontratı — TAMAMLANDI (`tests/test_validation_rolling_backtest.py`'de 66 yeni test tarafından karşılanmıştır, bkz. §28.F):**

```
- ContextAwareWindow: valid construction, field preservation, value
  equality/hashability, frozen/slotted, yanlış tip (context_start,
  evaluation), naive datetime, context_start > evaluation.start
  reddi, context_start == evaluation.start kabulü, package-root
  export yokluğu.
- run_context_aware_rolling_backtest_from_store: yanlış top-level tip,
  boş windows (izin verilir mi -> zero-context ile aynı davranış,
  boş tuple -> boş tuple), index-0 ve later-index geçersiz eleman,
  eşzamanlı çoklu ihlal (sıra kanıtı), duplicate/overlap reddedilmez,
  policy_factory callable-değil reddi.
- Context/evaluation davranışı: context candle'lar warm-up sağlar
  (Type-H fixture), context candle'lar sıfır ekonomik etki üretir,
  ilk evaluation candle warmed state kullanır, equity_curve yalnızca
  evaluation noktaları içerir, exact interval sınırları, eksik
  coverage, gapped context, pencereler arası context overlap,
  context'in başka bir pencerenin evaluation aralığıyla overlap'i,
  farklı pencereler için farklı context_start'lar.
- İzolasyon/freshness: factory pencere başına tam bir kez, distinct
  identity, later-index construction/execution failure, earlier
  pencereden state taşınmaması, sonuçların input sırasını koruması,
  cross-window aggregation yokluğu.
- Compatibility: mevcut zero-context Layer-2 testleri DEĞİŞMEDEN
  yeşil kalır; mevcut context-aware Layer-1 testleri DEĞİŞMEDEN yeşil
  kalır; gerçek candle/funding store entegrasyonu; WindowResult.result
  Stage-1/Stage-2'yi bağımsız kabul eder; ikinci bir metrics-boundary
  filtresi yokluğu; result/window modelleri DEĞİŞMEDEN.
- Temporal safety: hiçbir future candle availability'den önce
  policy'ye görünmez; context evaluation sınırında TAM OLARAK biter;
  hiçbir evaluation noktası kaybolmaz; hiçbir context noktası
  equity_curve'e girmez; sonraki bir pencere önceki bir sonucu
  etkilemez; timezone-aware UTC davranışı.

Testler deterministik olmalıdır: wall-clock/randomness/network/float/
external-service/order-dependence/mutable-global-fixture YOK.
```

**Implementasyon dosya kapsamı — TAMAMLANDI (gerçekleşen, planlanan ile birebir eşleşti):**

```
Production: src/crypto_quant_lab/validation/rolling.py (tek dosya) —
  ContextAwareWindow, run_context_aware_rolling_backtest_from_store,
  ve paylaşılan private _execute_windows/_require_context_aware_windows_tuple
  helper'ları eklendi; mevcut WindowResult, run_rolling_backtest_from_store,
  _require_windows_tuple, _require_callable_policy_factory,
  _require_valid_policy_result DEĞİŞMEDEN kaldı (yalnızca ortak
  yürütme döngüsü _execute_windows'a extract edildi — davranış
  byte-for-byte AYNI, 28 mevcut test DEĞİŞMEDEN yeşil kanıtıyla).
Test: tests/test_validation_rolling_backtest.py (mevcut dosya
  genişletildi — yeni, ilgisiz bir test framework'ü YARATILMADI).
Documentation (combined closure): VALIDATION_SPEC.md (bu güncelleme).
Açıkça YASAK kalan/DOKUNULMAYAN: ROADMAP.md, pyproject.toml, backtest/
  altındaki her şey, windows.py, metrics.py, herhangi bir __init__.py,
  herhangi bir başka production/test/spec/status dosyası.
```

**Explicit exclusions (bu kontrat kapsamında DEĞİL, implement EDİLMEDİ):** candidate/trial abstraction, optimizer/grid search, parameter search, train/select/test orchestration, annualized Sharpe, Sortino, Calmar, CAGR, downside deviation, purging/embargo, CPCV, Deflated Sharpe, PBO, multiple-testing correction, parameter stability, cross-window metric aggregation, paralel/distributed execution, persistence/reporting/serialization, CLI/API endpoint'leri, live/paper trading. Stage-1, Stage-2, Layer-1, ve zero-context Layer-2 kontratlarının hiçbiri bu implementasyonda DEĞİŞTİRİLMEDİ (28 mevcut zero-context test + 250 ilgili regression testi DEĞİŞMEDEN yeşil).

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

**(3) Parameter/candidate carry-in:** candidate abstraction'ın kendisi artık mevcuttur (Bölüm 18 `Candidate`/`Trial`, IMPLEMENTED + TESTED, 28.G — 25/25) — dondurulmuş bir candidate, IS'ten OOS'a mutlaka taşınmalıdır (aksi halde "OOS'u değerlendirmek" anlamsız olur). Ancak bu carry-in, `windows.py`/`rolling.py` içindeki temporal window/rolling MEKANİZMASININ bir parçası DEĞİLDİR ve olmayacaktır — `Candidate`/`Trial` yalnızca CALLER'ın kendi kullandığı harici value object'lerdir (§18.9); IS'ten OOS'a hangi candidate'in taşındığının disiplini, mekanik olarak enforce edilmeyen bir research-process sorumluluğu olarak kalır (Bölüm 19).

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

**Tek-pencereli (Layer 1) context-aware evaluation artık implement edilmiş + test edilmiştir** (Bölüm 8.3.11, 23). Mimari Bölüm 8.3'te LOCKED'dır (B2) VE `run_backtest_from_store`/`run_backtest_replay` kodu artık `evaluation_start` üzerinden context/evaluation ayrımını bilir — Bölüm 8/9'un audit bulgusu artık tarihsel/RESOLVED'dır: history-reconstructible (Type-H) bir `BacktestPolicy` (özellikle lookback/rolling-feature kullanan bir policy), `run_backtest_from_store(requested_start=context_start, evaluation_start=OOS.start, ...)` ile doğrudan, doğru şekilde değerlendirilebilir (bkz. Bölüm 8.3.5 için Type-H niteliğinin caller/policy-author sorumluluğu kaldığı). **Çok-pencereli (Layer 2) bir rolling OOS runner artık hem zero-context (`run_rolling_backtest_from_store`, Bölüm 8.3.6, 23, 28.C — 12/12) HEM DE GENERIC/context-aware (non-zero-context, `run_context_aware_rolling_backtest_from_store`, Bölüm 8.3.16, 23, 28.F — 22/22) için implement edilmiş + test edilmiştir.** Bu nedenle:

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
  Layer-2 varyantının exact kontratı Bölüm 8.3.16'da LOCKED'dır
  (`ContextAwareWindow`, `run_context_aware_rolling_backtest_from_store`)
  VE artık İMPLEMENT EDİLMİŞ + TEST EDİLMİŞTİR (bkz. Bölüm 23, 28.F — 22/22).
- MS2 (temporal-window primitives) bu karara bağımlı DEĞİLDİR — tamamen
  pure/store-free'dir ve bağımsız olarak inşa edilebilir (implement
  edildi, bkz. Bölüm 23).
```

Context/lookback kullanmayan trivial bir policy için, bugünkü `run_backtest_from_store`'un pencere-başına bağımsız çağrılması **zaten doğru sonucu üretir** (Bölüm 11) — bunu artık **tek-pencereli (Layer 1) bir runner contract'ı** olarak kilitlemek mümkündür, çünkü warm-up implementasyonu tamamlanmıştır. Çok-pencereli (Layer 2) rolling orchestrator, zero-context için artık **implement edilmiş ve test edilmiştir** (`run_rolling_backtest_from_store`) — her pencere `requested_start=window.start, requested_end=window.end, evaluation_start=window.start` ile çalışır, yani `context_start < evaluation_start` DEĞİLDİR. Context-aware (non-zero-context) bir Layer-2 runner'ın per-window context-boundary uzantısı Bölüm 8.3.16'da spec-lock edilmiştir (`ContextAwareWindow`) VE artık implement edilmiş ve test edilmiştir (`run_context_aware_rolling_backtest_from_store`, bkz. Bölüm 23, 28.F — 22/22) — her pencere `requested_start=context_start, requested_end=evaluation.end, evaluation_start=evaluation.start` ile çalışır.

## 14. Walk-Forward Terminolojisi (LOCKED — Precision)

**İki farklı kavram** kesin olarak ayrılır:

```
(A) Rolling / sequential fixed-policy OOS evaluation:
    aynı, sabit policy, ardışık pencerelerde bağımsız çalıştırılır.
    Foundation'ın kapsamındadır (Bölüm 13).

(B) True walk-forward optimization:
    IS üzerinde fit/select → candidate dondur → OOS'ta değerlendir →
    ilerlet → tekrarla.
    Candidate/trial abstraction'ın value-object kısmı artık mevcuttur
    (Bölüm 18, IMPLEMENTED + TESTED) — ama fit/select/optimizer/ilerletme
    DÖNGÜSÜNÜN kendisi HENÜZ MEVCUT DEĞİLDİR (§18.9, 18.13); bu nedenle
    (B) bir bütün olarak hâlâ foundation'ın kapsamında DEĞİLDİR.
```

(A), **"walk-forward optimization" olarak adlandırılmaz** — yalnızca "rolling fixed-policy temporal evaluation" veya benzeri dürüst bir isimle anılır. Bu repo (A)'yı (B)'den önce inşa edebilir; ama ikisi asla karıştırılmaz.

## 15. Metrics Foundation — Staged Bağımlılık (LOCKED) — Stage-1 LOCKED VE IMPLEMENTED + TESTED (FAZ6B MS4 + MS5); Stage-2 Return-Series + Per-Observation Sharpe LOCKED VE IMPLEMENTED + TESTED (commit `e4cedf9`); Annualized Metrics (Sharpe/Sortino/CAGR/Calmar) LOCKED AND IMPLEMENTED + TESTED (bu combined delivery; Bölüm 15.19–15.33, 28.H)

`BacktestResult` **değişmeden** kalır (Bölüm 4). Metrikler `equity_curve`'den **dışarıda** türetilir.

**Aşama 1 (foundation): total return + max drawdown — exact formül, API, validation ve edge-case davranışı Bölüm 15.1–15.8'de LOCKED'dır (FAZ6B MS4) VE artık IMPLEMENTED + TESTED'dır (FAZ6B MS5).** `equity_curve`'den doğrudan, ek runtime bağımlılık gerektirmeden hesaplanır. `src/crypto_quant_lab/validation/metrics.py`'de implement edilmiştir (commit `a265e44`) — `Stage1Metrics` (frozen, slots) + `compute_stage1_metrics(result: BacktestResult) -> Stage1Metrics` — kendi regression suite'i `tests/test_validation_metrics.py`'de (82 test, tümü PASS). İlgili regression suite'ler (`tests/test_backtest_models.py`, `tests/test_backtest_results.py`, `tests/test_validation_rolling_backtest.py` — 105 test) DEĞİŞMEDEN yeşil kalır; tam suite 1468/1468 PASS. Post-commit implementasyon audit'i — PASS (bkz. Bölüm 23, 28.D — 18/18).

**Aşama 2 — return-series + per-observation Sharpe: exact formül, API, validation, timestamp/cadence ve Decimal-context kontratı Bölüm 15.9–15.18'de LOCKED'dır VE artık IMPLEMENTED + TESTED'dır.** Bölüm 16'nın beş açık sorusundan dördü bu kilitle çözülmüştür (return tipi, periyodiklik, risk-free konvansiyonu, sıfır/negatif equity handling) — yalnızca annualization faktörü, bu Stage-2 adımında ayrı bir gelecekteki calendar/annualization kontratına ertelenmişti (bkz. Bölüm 15.9, 16); o kontrat artık Bölüm 15.19–15.33/§28.H'de LOCKED VE IMPLEMENTED + TESTED'dır (bkz. aşağıdaki paragraf). `src/crypto_quant_lab/validation/metrics.py`'de implement edilmiştir (commit `e4cedf9`) — `Stage2Metrics` (frozen, slots) + `compute_periodic_returns(result: BacktestResult) -> tuple[Decimal, ...]` + `compute_stage2_metrics(result: BacktestResult, *, risk_free_per_period: Decimal = Decimal(0)) -> Stage2Metrics` — kendi regression kanıtı `tests/test_validation_metrics.py`'nin genişletilmiş toplamında (207 test, tümü PASS: 82 Stage-1 DEĞİŞMEDEN + 125 yeni Stage-2). Tam suite 1593/1593 PASS. Post-commit implementasyon audit'i — PASS (bkz. Bölüm 23, 28.E — 29/29). Sortino, Calmar, CAGR, ve Sharpe'ın annualized varyantı bu kilide dahil DEĞİLDİR — bunların exact calendar/annualization ve downside-deviation kontratı Bölüm 15.19–15.33'te LOCKED'dır VE artık bu combined delivery ile IMPLEMENTED + TESTED'dır (bkz. Bölüm 23, 28.H — 30/30).

**Annualized Metrics — Sharpe/Sortino/CAGR/Calmar: exact formül, API, calendar-basis, validation ve Decimal-context kontratı Bölüm 15.19–15.33'te LOCKED'dır VE artık IMPLEMENTED + TESTED'dır (bu combined delivery).** `src/crypto_quant_lab/validation/annualized_metrics.py` (YENİ modül) — dört bağımsız, bare-`Decimal` döndüren fonksiyon: `compute_annualized_sharpe_ratio`, `compute_sortino_ratio`, `compute_cagr`, `compute_calmar_ratio`. Hiçbiri yeni bir dataclass/value object TANITMAZ; `Stage1Metrics`, `compute_stage1_metrics`, `Stage2Metrics`, `compute_periodic_returns`, `compute_stage2_metrics` DEĞİŞMEDEN reuse edilir. Kendi regression suite'i `tests/test_validation_annualized_metrics.py`'de (77 test, tümü PASS). İlgili regression suite'ler (`tests/test_validation_metrics.py` — 207 test, `tests/test_validation_candidate.py`, `tests/test_validation_rolling_backtest.py`, `tests/test_validation_windows.py`) DEĞİŞMEDEN yeşil kalır; tam suite 1884/1884 PASS. Post-implementation audit'i — PASS (bkz. Bölüm 23, 28.H — 30/30).

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
- Periodic return series (Stage-1'in kapsamı dışında kalır; return-series
  + per-observation Sharpe kontratı artık Bölüm 15.9–15.18'de LOCKED'dır
  — ama bu, Stage-1'in yukarıdaki iki maddelik kapsamını genişletmez)
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

**Kapsam sınırı (önemli, `a265e44` zamanı için tarihsel):** bu implementasyon (`a265e44`) **yalnızca Stage-1**'di (total return + max drawdown) — o commit zamanında Stage-2/Stage-3 implement edilmemişti ve `metrics.py` bu metriklere hiçbir referans içermiyordu. `BacktestResult`, `WindowResult`, replay, store-runner, ve rolling orchestrator DEĞİŞMEDEN kaldı (`git diff 76002ab..a265e44` bunların hiçbirinde boştur). **Güncel durum (commit `e4cedf9`'dan itibaren):** Stage-2 (return-series + arithmetic mean + sample stdev + non-annualized per-observation Sharpe) artık AYNI `metrics.py` modülünde implement edilmiştir (bkz. Bölüm 15.9–15.18'in implementasyon durumu, 28.E — 29/29) — yalnızca Sortino/Calmar/CAGR/annualized Sharpe ve Stage-3 (Deflated Sharpe, PBO, multiple-testing, parameter stability) implement edilmemiş kalır ve modülde hiçbir iz bırakmaz. Non-zero-context Layer-2 hâlâ implement edilmemiştir; Stage-1 de Stage-2 de ona bağımlı DEĞİLDİR.

### 15.9 Aşama 2 Kapsamı (LOCKED — Yalnızca Return-Series + Per-Observation Sharpe)

Aşama 2, bu mikro-adımda **kesinlikle ve yalnızca** şunlardan oluşur:

```
1. Periyodik/simple return-series türetimi (compute_periodic_returns)
2. Arithmetic mean return
3. Sample standard deviation (n-1)
4. Non-annualized, per-observation Sharpe ratio
   (compute_stage2_metrics / Stage2Metrics)
```

Bu kontrattan **açıkça hariç tutulur** (hiçbiri burada formül-kilitlenmez, hiçbiri implement edilmez):

```
- Annualized Sharpe
- periods_per_year
- Otomatik cadence/annualization inference
- Risk-free annual-to-period dönüşümü
- Sortino
- Calmar
- CAGR
- Downside-deviation konvansiyonları
- Drawdown-tabanlı annualized rasyolar
- Win rate
- Profit factor
- Exposure
- Turnover
- Cross-window aggregation
- Candidate/trial aggregation (Bölüm 18)
- Optimizer/grid-search (Bölüm 27)
- Purging/embargo (17.1), CPCV (17.2), Deflated Sharpe (17.4), PBO (17.5),
  multiple-testing corrections (17.6), parameter stability (17.7)
```

Annualized metrikler, `sharpe_ratio` alanı tarafından **sessizce ima edilmez** — kendi ayrı, artık Bölüm 15.19–15.33'te LOCKED (ama implement edilmemiş) bir calendar/annualization kontratına ihtiyaç duyarlar (bkz. Bölüm 16, 28.H).

### 15.10 Evaluation-Domain Inheritance (LOCKED — İkinci Bir Filtre YOK)

Runtime kanıtı (`src/crypto_quant_lab/backtest/replay.py`, satır ~371–394): context candle'lar için ana döngü, `equity_points.append(...)`'a hiç ulaşmadan `continue` ile bir sonraki iterasyona geçer — bu, Bölüm 8.3.2'nin "SIFIR EquityPoint yaratırlar" kuralının runtime enforcement'ıdır. Sonuç:

```
- BacktestResult.equity_curve, HEM zero-context (Layer-2 rolling) HEM
  context-aware (Layer-1) sonuçlar için YALNIZCA evaluation-fazı
  noktalarını içerir — ikisi equity_curve seviyesinde AYIRT EDİLEMEZ.
- evaluation_start'ın kendisinde hiçbir fabricated EquityPoint yoktur
  (Bölüm 8.3.10).
- İlk gerçek evaluation noktası, ilk evaluation candle'ın kendi
  feature-availability anıdır (evaluation_start + candle_duration,
  Bölüm 8.3.4).
```

**Bu nedenle:**

```
- Stage-2, ikinci bir evaluation-boundary filtresi UYGULAMAZ.
- Stage-2, bir evaluation_start parametresi GEREKTİRMEZ.
- Stage-2, BacktestResult veya EquityPoint'e hiçbir uzantı GEREKTİRMEZ.
- Stage-2, caller-supplied, önceden dilimlenmiş bir seri GEREKTİRMEZ.
- Non-zero-context Layer-2, Stage-2 için bir prerequisite DEĞİLDİR — o
  Layer-2 varyantı var olduğunda, ürettiği equity_curve de aynı B2
  garantisiyle ZATEN evaluation-only olacaktır; Stage-2'nin hiçbir
  değişikliğe ihtiyacı olmayacaktır.
```

**Gerekçe (neden ikinci bir filtre yasak):** metrics katmanında bağımsız bir evaluation-boundary filtresi inşa etmek, aynı garantinin İKİ ayrı, potansiyel olarak birbirinden SAPABİLEN sahibini yaratırdı (biri `replay.py`'de B2, diğeri `metrics.py`'de). Bu, compose-not-duplicate prensibinin (Bölüm 4, 21) bir ihlalidir — B2 zaten bu filtrelemeyi `BacktestResult` inşa edilmeden ÖNCE, tek bir yerde yapar; Stage-2 bunu yeniden yapmaz, yalnızca zaten temiz olan `equity_curve`'ü tüketir. Bu bölüm Bölüm 8.3'ün B2 mekanizmasını **değiştirmez veya genişletmez** — yalnızca onun Stage-2 için zaten yeterli olduğunu kaydeder.

### 15.11 Public API (LOCKED — Kavram ve İsimler; Implementasyon Değil)

```
Modül:  src/crypto_quant_lab/validation/metrics.py  (Stage-1 ile AYNI modül)

def compute_periodic_returns(result: BacktestResult) -> tuple[Decimal, ...]:
    ...

@dataclass(frozen=True, slots=True)
class Stage2Metrics:
    mean_return: Decimal
    return_stdev: Decimal
    sharpe_ratio: Decimal

def compute_stage2_metrics(
    result: BacktestResult,
    *,
    risk_free_per_period: Decimal = Decimal("0"),
) -> Stage2Metrics:
    ...
```

```
- Public import path: crypto_quant_lab.validation.metrics (Stage-1 ile
  aynı modül, package-root re-export YOK — mevcut zero-re-export
  convention'ıyla tutarlı).
- BacktestResult, WindowResult, EquityPoint DEĞİŞMEDEN kalır — hiçbir
  result modeline metrics field'ı EKLENMEZ.
- compute_stage2_metrics, kendi periyodik-return tuple'ını İÇSEL
  olarak, compute_periodic_returns ile AYNI kilitli semantikle
  hesaplar.
- Periyodik return'ler Stage2Metrics İÇİNDE saklanmaz — bir caller,
  compute_periodic_returns'ü bağımsız olarak da çağırabilir.
- Doğrudan bir BacktestResult üzerinde VE bağımsız bir
  WindowResult.result üzerinde çalışır — Stage-1 ile birebir aynı
  desen (Bölüm 15.2).
- risk_free_per_period ismi KORUNUR, ama kontrat bunun bir "period"ı
  YALNIZCA ardışık iki eligible equity gözlemi arasındaki ARALIK
  olarak tanımlar — bu bir yıllık (annual) oran DEĞİLDİR.
```

Bu mikro-adım yalnızca API'yi kilitler — `metrics.py`'ye hiçbir yeni sembol bu mikro-adımda EKLENMEZ.

### 15.12 `Stage2Metrics` Değer Invariant'ları (LOCKED)

```
- mean_return, return_stdev, sharpe_ratio — ÜÇÜ DE Decimal olmalıdır;
  değilse TypeError.
- ÜÇÜ DE finite olmalıdır; değilse ValueError.
- return_stdev >= Decimal("0") olmalıdır (bir value-object
  invariant'ı olarak); negatifse ValueError.
- Ekstra field YOK.
- Cross-window aggregation, candidate/trial aggregation, veya
  annualization factor field'ı YOK.
- Custom equality/hashing/ordering YOK — frozen dataclass
  default'ları (value equality, hashability) yeterlidir.
```

Nesne frozen, slotted, ve normal frozen-dataclass davranışıyla hashable'dır — `Stage1Metrics` ile birebir aynı convention (Bölüm 15.3).

### 15.13 `compute_periodic_returns` Kontratı — Formül ve Gözlem Sayısı (LOCKED)

```
- Dönüş tipi: immutable tuple[Decimal, ...].
- Return'ler YALNIZCA result.equity_curve'den türetilir.
- Cash, fills, PnL, fees, cost, funding, veya unrealized
  mark-to-market BAĞIMSIZ OLARAK YENİDEN HESAPLANMAZ (equity_curve'de
  zaten yansır, Bölüm 15.5'in aynı prensibi).
- result.initial_cash, ilk eligible equity noktasından HEMEN ÖNCEKİ
  implicit gözlemdir (Bölüm 15.6'nın peak-seed prensibiyle aynı
  desen).
- N equity noktası için TAM OLARAK N return gözlemi üretilir — N-1
  DEĞİL.
- Tek noktalı bir curve TAM OLARAK bir return üretir.
```

**Exact formül ve operation sırası:**

```python
first_return = result.equity_curve[0].equity / result.initial_cash - Decimal("1")

# i >= 1 için:
periodic_return_i = result.equity_curve[i].equity / result.equity_curve[i - 1].equity - Decimal("1")
```

```
- Operation sırası: ÖNCE bölme, SONRA çıkarma — Stage-1'in
  total-return sırasıyla birebir aynı (Bölüm 15.5). Cebirsel bir
  rewrite'a SESSİZCE geçilmez.
- Yalnızca simple return — log return KULLANILMAZ (aşağıdaki
  gerekçe).
- Float'a hiçbir dönüşüm YAPILMAZ.
- Hesaplama sonrası quantization YOK.
- Hiçbir return cap'lenmez veya clip edilmez.
- Güncel equity sıfır legal'dir ve Decimal("-1") üretebilir.
- Güncel equity negatif legal'dir ve Decimal("-1")'den küçük bir
  return üretebilir.
- Bir sonraki return'ün paydası olacak equity <= 0 ise, bu
  INVALID'dir ve o bölme işleminden ÖNCE deterministik olarak fail
  eder (Bölüm 15.15).
- Non-pozitif bir paydadan SONRA seri sessizce KISALTILMAZ —
  hesaplama o noktada AÇIKÇA fail eder.
- Hiçbir equity değeri repair/replace/filter/normalize EDİLMEZ.
```

**Log return neden kullanılmaz (gerekçe):** Bölüm 15.6 zaten equity'nin pozitif bir peak'ten sonra negatife dönebileceğini ve bunun **legal** olduğunu kilitler. `ln(x)`, `x <= 0` için tanımsızdır — log return, Stage-1'in zaten kilitlediği tam olarak bu path'lerde sessizce kırılırdı. Simple return bu çakışmayı yaşamaz; yalnızca payda (önceki equity) için açık bir pozitiflik kontrolüne ihtiyaç duyar (Bölüm 15.15).

**Stage-1 ile kasıtlı asimetri (LOCKED, çelişki DEĞİL):**

```
- Stage-1'in total_return ve max_drawdown'u, equity sıfıra
  ulaştığında veya sıfırın altına geçtiğinde HÂLÂ hesaplanabilir
  kalır (Bölüm 15.5, 15.6).
- Sonraki bir periyodik return, non-pozitif bir ÖNCEKİ equity'yi
  ekonomik olarak anlamlı bir payda olarak KULLANAMAZ.
- Bu nedenle Stage-2, Stage-1'in HÂLÂ değerlendirebildiği bir sonucu
  deterministik olarak REDDEDEBİLİR.
- Bu KASITLIDIR ve bir çelişki DEĞİLDİR — iki aşama farklı sorular
  sorar: Stage-1 "başlangıçtan sona ne oldu," Stage-2 "her ardışık
  aralıkta ne oldu" (ve bir aralığın kendisi sıfır/negatif bir
  başlangıç noktasından tanımlı bir şekilde ölçülemez).
```

### 15.14 Timestamp / Cadence Semantics (LOCKED — Per-Observation, Annualized DEĞİL)

```
- equity_curve timestamp'leri strictly ascending olmalıdır; duplicate
  veya descending timestamp → ValueError (Stage-1'in Bölüm 15.4 madde
  8'i ile aynı kural, reuse edilir).
- Stage-2, timeframe veya annualization'ı timestamp delta'larından
  INFER ETMEZ (Bölüm 8.3.13'ün auto-inference-yok prensibiyle
  tutarlı).
- Stage-2, value-object sınırında eşit timestamp spacing GEREKTİRMEZ
  — BacktestResult/EquityPoint hiçbir timeframe/cadence field'ı
  taşımaz (kod incelemesiyle doğrulanmıştır: models.py, results.py'de
  `timeframe` YOK).
- Her ardışık eligible equity-gözlem çifti TAM OLARAK bir return
  gözlemi tanımlar.
- Sonuç olarak kilitli Sharpe, bir PER-OBSERVATION, NON-ANNUALIZED
  rasyodur — "per-period" ifadesi burada annualized veya wall-clock-
  uniform olarak YANLIŞ OKUNMAMALIDIR; netlik gerektiğinde
  "per-observation" tercih edilir.
- Doğrudan inşa edilmiş, düzensiz (irregular) timestamp'lere sahip
  bir BacktestResult, strictly ascending olduğu sürece hâlâ
  legal'dir — ama onun per-observation Sharpe'ının standart bir
  wall-clock periyodu temsil ettiği İDDİA EDİLMEZ.
- Canonical backtest path'leri (her evaluation candle işaretlenir)
  düzenli spacing üretebilir, ama bu bir result-model invariant'ı
  olarak TASARLANMAZ/ENFORCE EDİLMEZ.
- Hiçbir calendar, trading-day, candle-count, veya periods_per_year
  değeri otomatik olarak INFER EDİLMEZ.
```

### 15.15 Input Validation ve Fail-Fast Sırası (LOCKED)

`compute_periodic_returns`, deterministik validation'ı tam olarak bu sırada çalıştırır — yalnızca TÜMÜ geçtikten SONRA arithmetic başlar:

```
1. result bir BacktestResult olmalıdır; değilse TypeError.
2. result.initial_cash finite olmalıdır; değilse ValueError.
3. result.initial_cash > Decimal("0") olmalıdır; değilse ValueError.
4. result.final_equity finite olmalıdır; değilse ValueError.
5. result.equity_curve boş OLMAMALIDIR; boşsa ValueError.
6. Her curve elemanı bir EquityPoint olmalıdır; geçersiz eleman,
   index'i içeren bir TypeError fırlatır.
7. Her equity_curve[i].equity finite olmalıdır; geçersiz değer,
   index'i içeren bir ValueError fırlatır.
8. Timestamp'ler strictly ascending olmalıdır; değilse ValueError.
9. equity_curve[-1].equity == result.final_equity olmalıdır; değilse
   ValueError.
10. Bir SONRAKİ return'ün paydası olacak her equity değeri
    Decimal("0")'dan KESİNLİKLE BÜYÜK olmalıdır; değilse, o index'i
    ve payda rolünü içeren bir ValueError.
11. Yalnızca TÜM validasyonlar geçtikten SONRA return arithmetic'i
    çalışır.
```

**Payda sahipliği (netleştirme):**

```
- Return index 0, initial_cash'i payda olarak kullanır — bu zaten
  adım 2-3'te validate edilmiştir.
- Return index i >= 1, equity_curve[i-1].equity'yi payda olarak
  kullanır.
- Terminal equity (equity_curve[-1]), başka bir return'ün paydası
  OLARAK KULLANILMAYACAKSA pozitif olmak ZORUNDA DEĞİLDİR — yalnızca
  bir ÖNCEKİ return'ün paydası olan her equity pozitif olmalıdır
  (yani equity_curve[0..N-2] pozitif olmalıdır; equity_curve[N-1]
  yalnızca finite olmak zorundadır, adım 7'de zaten kontrol edilir).
- Fail mesajı, ilgili curve index'ini ve onun payda rolünü tanımlar
  (mevcut proje TypeError/ValueError + index-in-message convention'ı,
  Bölüm 15.4 ile aynı stil).
```

`compute_stage2_metrics`, yukarıdaki TAM validation zincirini gevşetmeden şu EK sırayı uygular:

```
1. Yukarıdaki result/curve/payda validation zinciri TAMAMEN uygulanır.
2. risk_free_per_period bir Decimal olmalıdır; değilse TypeError.
3. risk_free_per_period finite olmalıdır; değilse ValueError.
4. En az İKİ periyodik return gereklidir (len(returns) >= 2); değilse
   ValueError.
5. Yalnızca BUNDAN SONRA mean/variance/stdev/Sharpe arithmetic'i
   çalışır.
6. Her hesaplanmış çıktı (mean_return, return_stdev, sharpe_ratio)
   finite OLMALIDIR; değilse ValueError.
7. return_stdev, Sharpe bölmesinden ÖNCE Decimal("0")'dan KESİNLİKLE
   BÜYÜK olmalıdır; değilse deterministik ValueError.
8. Stage2Metrics döndürülür.
```

Geçersiz bir `risk_free_per_period`, yetersiz sample, veya sıfır volatilite hiçbir zaman NaN/Infinity ile "başarılı" bir sonuca DÖNÜŞMEZ — yukarıdaki her adım, arithmetic'ten ÖNCE veya arithmetic'in hemen ardından açık bir exception fırlatır.

### 15.16 Mean / Sample Standard Deviation / Sharpe Formülleri (LOCKED)

Tüm işlemler Bölüm 15.17'nin kilitli private Decimal context'i İÇİNDE çalışır.

`returns = compute_periodic_returns(result)`, `n = len(returns)`, `n >= 2` (Bölüm 15.15 madde 4) için:

**Arithmetic mean:**

```python
return_sum = sum(returns, Decimal("0"))
mean_return = return_sum / Decimal(n)
```

**Sample variance ve standard deviation:**

```python
squared_deviation_sum = Decimal("0")

for periodic_return in returns:
    deviation = periodic_return - mean_return
    squared_deviation_sum += deviation * deviation

sample_variance = squared_deviation_sum / Decimal(n - 1)
return_stdev = sample_variance.sqrt()
```

**Non-annualized, per-observation Sharpe:**

```python
sharpe_ratio = (mean_return - risk_free_per_period) / return_stdev
```

```
- Yalnızca arithmetic mean — başka bir mean konvansiyonu KULLANILMAZ.
- Yalnızca sample standard deviation (n-1 payda) — population
  variance (n payda) KULLANILMAZ.
- Log-return istatistikleri KULLANILMAZ (Bölüm 15.13).
- Bessel correction başka hiçbir yerde UYGULANMAZ.
- Ara işlemler arasında rounding YAPILMAZ.
- Final çıktılar quantize EDİLMEZ.
- Float'a hiçbir dönüşüm YAPILMAZ.
- NumPy, pandas, statistics modülü (float-conversion ile), veya
  başka bir external numerical library KULLANILMAZ (Bölüm 27).
- Hiçbir çıktı cap'lenmez.
- risk_free_per_period, exact Decimal("0")'a default olur.
- Sıfır standard deviation, Sharpe'ı TANIMSIZ kılar ve deterministik
  ValueError fırlatmalıdır (Bölüm 15.15 madde 7) — sıfır, None, NaN,
  veya Infinity DÖNDÜRÜLMEZ.
- Döndürülen Sharpe rasyosu boyutsuz (dimensionless) ve
  NON-ANNUALIZED'dır.
```

Exact operation sırası (yukarıdaki kod blokları) SABİT kalır — sonraki implementasyon testleri, yasak bir cebirsel rewrite'ı (örn. matematiksel olarak eşdeğer ama farklı finite-precision davranışı olan varyantları) bu exact sıradan davranışsal olarak ayırt edebilmelidir.

### 15.17 Decimal-Context Determinism (LOCKED)

Stage-2, Stage-1 için zaten kilitli olan AYNI explicit context shape'i kullanır (Bölüm 15.7):

```
Context(
    prec=28,
    rounding=ROUND_HALF_EVEN,
    Emin=-999999,
    Emax=999999,
    capitals=1,
    clamp=0,
    traps=[],
)
```

```
- Her hesaplama için taze (fresh) bir private context — paylaşılan
  bir modül-seviyeli sabit DEĞİL (Bölüm 15.7'nin aynı gerekçesi).
- localcontext(...) izolasyonu.
- Caller'ın ambient precision/rounding'i çıktıyı ETKİLEMEZ.
- Toplama, çıkarma, çarpma, bölme, VE sqrt DAHİL tüm Stage-2
  arithmetic'i bu private context İÇİNDE çalışır.
- Hiçbir mutable context caller'a expose EDİLMEZ.
- Hesaplanmış non-finite return, mean, variance, standard deviation,
  veya Sharpe, deterministik olarak fail eder (Bölüm 15.15 madde 6).
- İstisnai bir Decimal davranışı üzerinden hesaplanmış negatif bir
  variance, hiçbir zaman başarılı bir sonuç ÜRETMEZ — non-finite
  post-check bunu da yakalar (variance matematiksel olarak asla
  negatif olamaz, kareler toplamıdır; bu yalnızca bir savunma
  katmanıdır).
- İmplementasyon başladığında, paylaşılan bir private context
  factory (Stage-1'in _stage1_decimal_context()'i ile AYNI shape,
  aynı modül içinde) TERCİH EDİLİR — context helper'ı public olarak
  expose EDİLMEZ.
- Bu bölüm, zaten test edilmiş Stage-1 semantics'ini DEĞİŞTİRMEZ.
```

Empirik doğrulama (bu mikro-adımın preflight'inde, aynı context shape altında): `Decimal.sqrt()` bu context altında çalışır (`sqrt(4)=2`, `sqrt(2)` 28-basamak sonuç üretir); `traps=[]` altında sıfıra bölme `Infinity`/`NaN` üretir (raise ETMEZ), her ikisi de `.is_finite()` ile `False` döner — Stage-1'in "fault → non-finite → post-check ValueError" deseniyle birebir tutarlıdır.

### 15.18 Purity ve Compatibility (LOCKED)

`compute_periodic_returns` ve `compute_stage2_metrics`:

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
- Non-zero-context Layer-2 implement edilmeden ÖNCE çalışabilir
  (Bölüm 15.10 — aynı gerekçenin Stage-1'in Bölüm 15.8'de zaten
  kilitli ilkesinin Stage-2'ye evidence-backed uzantısı).
- Yalnızca standard-library Python ve Decimal kullanır.
- Doğrudan bir BacktestResult kullanımı VE bağımsız bir
  WindowResult.result kullanımı, hiçbir aggregation olmadan
  desteklenir (Stage-1 ile aynı desen, Bölüm 15.8).
- Cross-window veya candidate/trial aggregation İÇERMEZ.
- Model/replay/store/rolling modüllerine hiçbir yeni coupling
  YARATMAZ.
```

**Implementasyon Durumu — IMPLEMENTED + TESTED (commit `e4cedf9`, bkz. Bölüm 23, 28.E — 29/29):**

Yukarıdaki Bölüm 15.9–15.18 kontratının tamamı `src/crypto_quant_lab/validation/metrics.py`'de implement edilmiştir. Public production şekli:

```
compute_periodic_returns(result: BacktestResult) -> tuple[Decimal, ...]

Stage2Metrics(mean_return: Decimal, return_stdev: Decimal, sharpe_ratio: Decimal)  # frozen, slots

compute_stage2_metrics(
    result: BacktestResult, *, risk_free_per_period: Decimal = Decimal(0),
) -> Stage2Metrics
```

Yukarıdaki her LOCKED invariant, bu implementasyon için kanıtlanmıştır (post-commit implementasyon audit'i — PASS):

```
- N equity noktası TAM OLARAK N return üretir; initial_cash implicit
  ilk-return baseline'ıdır (test_periodic_returns_exact_n_points_produce_n_returns,
  _initial_cash_first_return_baseline)
- equity_curve zaten evaluation-only'dir — context candle'lar hiçbir return
  gözlemine katkıda BULUNMAZ; ikinci bir evaluation-boundary filtresi
  UYGULANMAZ; metrics.py hiçbir evaluation_start parametresi KABUL ETMEZ
  (test_context_candles_produce_no_return_observations,
  _context_aware_result_not_diluted_versus_equivalent_zero_context,
  _stage2_apis_do_not_accept_evaluation_start — gerçek run_backtest_from_store
  entegrasyonuyla, davranışsal olarak kanıtlanmıştır)
- her return'ün paydası (initial_cash veya önceki equity), o bölmeden ÖNCE
  index-specific ValueError ile deterministik olarak kontrol edilir
  (test_stage2_rejects_zero/negative_intermediate_denominator,
  _denominator_error_identifies_curve_and_return_index)
- exact division-then-subtraction operation sırası, precision-28 altında
  forbidden rewrite'tan somut olarak farklı bir Decimal çiftiyle
  kanıtlanmıştır (test_periodic_returns_exact_locked_operation_order,
  _stage2_sharpe_exact_locked_operation_order)
- arithmetic mean, sample standard deviation (n-1, population variance'tan
  davranışsal olarak ayırt edilmiştir), ve non-annualized per-observation
  Sharpe (`(mean_return - risk_free_per_period) / return_stdev`), exact
  kilitli formüllerle hesaplanır (test_stage2_known_arithmetic_mean,
  _known_exact_sample_standard_deviation, _uses_sample_not_population_standard_deviation,
  _default_risk_free_sharpe, _explicit_non_zero_risk_free)
- en az iki return gerektirir; sıfır/negatif standard deviation Sharpe
  bölmesinden ÖNCE deterministik ValueError ile reddedilir — hiçbir zaman
  None/NaN/Infinity/yapay sıfır DÖNDÜRÜLMEZ (test_stage2_minimum_two_returns_required,
  _all_zero_returns_rejected, _equal_non_zero_returns_rejected)
- Stage-1 ile AYNI private Decimal context (prec=28, ROUND_HALF_EVEN,
  Emin/Emax/capitals/clamp Python default'ları, traps=[]) her çağrıda taze
  inşa edilir; ambient precision/rounding değişikliklerinin çıktıyı
  ETKİLEMEDİĞİ davranışsal olarak kanıtlanmıştır (5 Decimal-context
  determinism testi); hesaplanmış non-finite bir ara/son değer (return_sum,
  mean_return, squared_deviation_sum, sample_variance, return_stdev,
  sharpe_ratio) BAĞIMSIZ OLARAK deterministik ValueError ile reddedilir
  (5 non-finite-output testi, extreme-magnitude fixture'larla; bir alt-yol —
  finite variance'tan non-finite stdev — matematiksel olarak ULAŞILAMAZ
  olduğu ayrı bir testle kanıtlanmıştır)
- Pure/deterministic/no-mutation, WindowResult.result'un bağımsız
  kullanımı, cross-window/candidate-trial aggregation'ın YOKLUĞU, ve
  Stage-1'in DEĞİŞMEDEN kaldığı doğrudan kanıtlanmıştır (82 orijinal
  Stage-1 testi DEĞİŞMEDEN yeşil + purity/compatibility test grubu)
```

**Kapsam sınırı (önemli):** bu implementasyon **yalnızca Aşama 2**'dir (return-series + arithmetic mean + sample stdev + non-annualized per-observation Sharpe). Annualized Sharpe, Sortino, Calmar, CAGR, downside deviation, ve Aşama 3 (Deflated Sharpe, PBO, multiple-testing, parameter stability) implement edilmemiştir ve bu modülde hiçbir iz bırakmaz. `BacktestResult`, `WindowResult`, `EquityPoint`, replay, store-runner, ve rolling orchestrator DEĞİŞMEDEN kalır (`git diff da31ec7..e4cedf9 -- src/crypto_quant_lab/backtest/ src/crypto_quant_lab/validation/rolling.py src/crypto_quant_lab/validation/windows.py` boştur). Non-zero-context Layer-2 hâlâ implement edilmemiştir; bu implementasyon ona bağımlı DEĞİLDİR.

**Annualized Metrics (LOCKED — mimari/tasarım, IMPLEMENTASYON PENDING):** Annualized Sharpe, Sortino, CAGR, ve Calmar için exact kontrat Bölüm 15.19–15.33'te LOCKED'dır (bkz. Bölüm 23, 28.H — 0/N). Bu, **"Stage-3"** (Deflated Sharpe/PBO/multiple-testing/parameter stability — Bölüm 17.4–17.7) ile **AYNI ŞEY DEĞİLDİR**; iki grup her zaman ayrı kalmıştır (bkz. bu bölümün yukarıdaki "Güncel durum" notu, satır: "yalnızca Sortino/Calmar/CAGR/annualized Sharpe **ve** Stage-3... implement edilmemiş kalır" — "ve" ayrımı zaten tarihsel olarak buradaydı). İmplementasyon, regression suite'i, ve §28.H acceptance HENÜZ BAŞLAMAMIŞTIR.

### 15.19 Annualized Metrics — Source-Preflight Bulguları

```
- market_data/timeframes.py: candle_duration(timeframe) -> timedelta,
  yalnızca "1h" (timedelta(hours=1)) ve "4h" (timedelta(hours=4))
  destekler; başka her string ValueError. Faz 3 MVP kapsamı, calendar-
  variable interval YOK (dosyanın kendi docstring'i: "1M gibi
  calendar-variable interval'lar REDDEDİLİR"). Bu modül, validation/
  paketinden bağımsız, sıfır cross-dependency taşır (yalnızca stdlib
  datetime.timedelta import eder) — validation/annualized_metrics.py
  tarafından tek yönlü, döngüsüz olarak import edilebilir.
- BacktestResult (backtest/models.py) VE EquityPoint, hiçbir
  timeframe/periods_per_year/exchange/symbol field'ı TAŞIMAZ (Bölüm
  15.14'ün zaten kaydettiği bulgu: "models.py, results.py'de
  `timeframe` YOK" — bu preflight'te tekrar doğrulanmıştır). Bu nedenle
  annualization bilgisi result'tan INFER EDİLEMEZ; explicit bir
  caller-input ZORUNLUDUR (Bölüm 15.14'ün "hiçbir calendar/periods_per_year
  değeri otomatik olarak INFER EDİLMEZ" ilkesiyle tutarlı).
- BacktestResult, evaluation'ın gerçek başlangıç timestamp'ini
  (evaluation_start/window.start) TAŞIMAZ — yalnızca equity_curve
  noktalarının (ilk candle'ın availability boundary'sinde başlayan)
  timestamp'lerini taşır. Bu nedenle initial_cash'in "gerçekleştiği an"
  için repository'de mekanik olarak geçerli bir kaynak YOKTUR (bkz.
  Bölüm 8.3 — evaluation_start yalnızca çağrı-zamanı bir argümandır,
  hiçbir result-modelinde saklanmaz). CAGR bu nedenle elapsed
  wall-clock timestamp'lerine DEĞİL, return-period count + periods-
  per-year'a dayanır (bkz. 15.25).
- Trial (validation/candidate.py) `timeframe: str` provenance field'ı
  taşır — ama Bölüm 18.9'un kilitlediği gibi Candidate/Trial'a
  annualized metrics COUPLE EDİLMEZ; bu modül `Trial`'ı hiç import
  ETMEZ. Bir caller, elindeki bir `trial.timeframe`'i bu modülün
  `timeframe` argümanı olarak GEÇEBİLİR — ama bu, çağıranın kendi
  external composition'ıdır, modülün kendisi Trial'ın var olduğundan
  HABERDAR DEĞİLDİR.
- metrics.py: `Stage1Metrics.total_return`, `Stage1Metrics.max_drawdown`,
  `Stage2Metrics.sharpe_ratio`, `compute_periodic_returns` — dördü de
  public, reuse edilebilir. `_metrics_decimal_context`,
  `_require_core_result_contract`, `_require_positive_denominators`,
  `_require_backtest_result`, `_require_valid_equity_curve` — hepsi
  module-private (`_` prefix); repo konvansiyonuna göre CROSS-MODULE
  import EDİLEMEZ (yalnızca aynı modül içi reuse). Bu nedenle yeni
  modül, bu private helper'ları TEKRARLAMAZ — bunun yerine metrics.py'nin
  PUBLIC fonksiyonlarına delege eder ve onların ZATEN uyguladığı
  validation zincirini (sonucu değişmeden propagate ederek) reuse eder.
  metrics.py'nin kendi docstring'i zaten "Annualized Sharpe, Sortino,
  Calmar, CAGR... bu modül tarafından hiçbir zaman hesaplanmaz veya
  approximate edilmez" der (Bölüm 15 üst metni) — bu, annualized
  metrics'in AYRI bir modülde yaşaması gerektiğinin doğrudan kaynak
  kanıtıdır (aksi halde zaten LOCKED olan bu üst-metin ihlal edilirdi).
- Repo genelinde "365"/"annual"/"252" grep'i: yalnızca metrics.py'nin
  kendi "non-annualized" docstring cümlelerinde eşleşme var — hiçbir
  yerde önceden LOCKED bir calendar-basis sabiti veya periods_per_year
  formülü YOKTUR (funding/calculator.py dahil — funding hesaplaması da
  hiçbir annualization konvansiyonu içermez). Bu nedenle calendar basis
  bu mikro-adımda serbestçe (ama gerekçeli) kilitlenebilir; hiçbir
  upstream-locked semantiği ÇAKIŞMAZ/YENİDEN TANIMLANMAZ.
- Empirik Decimal probe'ları (Python 3.13, `Context(prec=28,
  rounding=ROUND_HALF_EVEN, Emin=-999999, Emax=999999, capitals=1,
  clamp=0, traps=[])` — Stage-1/2 ile AYNI shape altında, salt-okunur,
  hiçbir dosya oluşturmadan/değiştirmeden çalıştırılmıştır):
```

```
  Pozitif base, fractional exponent    -> finite exact sonuç
    (örn. Decimal("1.10") ** (Decimal(1)/Decimal(3)) ->
    Decimal('1.032280115456367159213585225'))
  Sıfır base, pozitif fractional exp   -> Decimal('0') (finite, LEGAL)
  Sıfır base, negatif fractional exp   -> Decimal('Infinity') (non-finite)
  Negatif base, fractional exponent    -> Decimal('NaN') (non-finite;
    traps=[] altında EXCEPTION RAISE ETMEZ, .sqrt()'un negatif-input
    davranışıyla birebir tutarlı)
  Negatif base, TAM SAYI-değerli exponent (Decimal('2') veya
    Decimal('2.0'))                    -> finite exact sonuç (özel
    olarak ele alınır, bu kontratta KULLANILMAZ çünkü periods_per_year/n
    genel olarak tam sayı değildir)
  1 ** herhangi bir exponent           -> exact Decimal('1') (finite)
  0 ** 0                                -> Decimal('NaN') (non-finite;
    bu kontratta base=0 yalnızca exponent > 0 iken oluşabildiğinden
    (periods_per_year/n her zaman pozitiftir) bu case hiçbir zaman
    tetiklenmez)
  Decimal(8760).sqrt() / Decimal(2190).sqrt() -> finite, 28-basamak
    exact sonuç
  Overflow (örn. Decimal(100) ** Decimal(4380)) -> Emax=999999 bu
    büyüklükte hâlâ finite bir sonuç üretir (gerçek Infinity'ye ulaşmak
    için çok daha ekstrem girdi gerekir) — ama mekanizma (traps=[] ->
    non-finite değer -> post-check ValueError) Stage-1/2 ile birebir
    aynı kalır, ekstrem girdilerde devreye girer
  Ambient-context bağımsızlığı         -> `**` operatörü, açık bir
    localcontext(...) SARMALANMADIĞI sürece AMBIENT (çağıranın global)
    context'i kullanır (doğrulanmıştır: ambient prec=5 iken localcontext
    dışında farklı, düşük-precision bir sonuç üretir) — bu nedenle TÜM
    power/sqrt/division operasyonları, Stage-1/2 ile AYNI şekilde,
    explicit localcontext(...) bloğu İÇİNDE çalıştırılmalıdır
  Exact periods_per_year (integer-mikrosaniye aritmetiği ile, ASLA
    timedelta.total_seconds() [float döner] KULLANILMADAN):
    1h -> Decimal(365*86_400*1_000_000) / Decimal(3_600*1_000_000)
       == Decimal('8760') (exact, kalansız)
    4h -> Decimal(365*86_400*1_000_000) / Decimal(4*3_600*1_000_000)
       == Decimal('2190') (exact, kalansız)
```

**Sonuç:** standart-kütüphane Python 3.13 `Decimal`, bu kontratın ihtiyaç duyduğu HER operasyonu (fractional power, sqrt, ambient-independent context) zaten destekler — hiçbir implementasyon-edilemez API gerekmez, hiçbir blocker YOKTUR.

### 15.20 Kapsam ve Adlandırma (LOCKED)

**Karşılaştırılan alternatifler:**

```
1. "Stage-3" adı altında Deflated Sharpe/PBO/multiple-testing/parameter
   stability ile BİRLEŞTİRİLİR — REDDEDİLDİ: doküman genelinde "Stage-3"
   terimi ZATEN yalnızca Deflated Sharpe/PBO/multiple-testing/parameter
   stability için kullanılıyor (Bölüm 15's üst metni, 17.4-17.7, 28.E/F/G
   acceptance notları) — annualized metrics bu terimin dışında,
   AYRI bir grup olarak zaten tutarlı şekilde anılıyordu ("Sortino/
   Calmar/CAGR/annualized Sharpe **ve** Stage-3" — iki ayrı liste).
   Annualized metrics'i "Stage-3" olarak yeniden adlandırmak, bu ZATEN
   tutarlı terminolojiyi BOZARDI.
2. mevcut metrics.py'ye (Stage-1/Stage-2 ile AYNI dosya) EKLENİR —
   REDDEDİLDİ: metrics.py'nin kendi modül docstring'i ZATEN LOCKED
   olarak "Annualized Sharpe, Sortino, Calmar, CAGR... bu modül
   tarafından hiçbir zaman hesaplanmaz veya approximate edilmez" der
   (Bölüm 15 üst metni) — bu locked upstream ifadeyi ihlal etmeden
   metrics.py'ye eklenemez (Bölüm 3'ün "if the contract cannot be made
   exact without changing an already locked upstream semantic, stop"
   talimatı — bu gerçek bir blocker değil, tam tersine AYRI modül
   kararını KANITLAR).
3. **SEÇİLDİ — "Annualized Metrics," kendi ayrı, yeni modülü**
   (`src/crypto_quant_lab/validation/annualized_metrics.py`) —
   Candidate/Trial'ın (Bölüm 18) kendi ayrı `candidate.py` modülü
   alma precedent'iyle AYNI desen: tek-kavram-per-modül convention'ı
   (windows.py/rolling.py/metrics.py/candidate.py) korunur.
```

Lock edilen exact isim: **"Annualized Metrics"** — "Stage-3" DEĞİL, "Aşama 3" DEĞİL. Bu grup Bölüm 15'in ("Metrics Foundation — Staged Bağımlılık") staged yapısını genişletir (Stage-1 → Stage-2 → **Annualized Metrics** → Stage-3), ama Stage-1/Stage-2'nin kendi public objelerini/davranışını **DEĞİŞTİRMEZ, RETROAKTİF OLARAK YENİDEN ADLANDIRMAZ.**

**Exact kapsam:**

```
Kapsam İÇİNDE (bu kontrat, bu dört metrik):
  1. Annualized Sharpe ratio
  2. Sortino ratio (annualized)
  3. CAGR (Compound Annual Growth Rate)
  4. Calmar ratio

Kapsam DIŞINDA (bu mikro-adımda TASARLANMAZ, gelecekteki ayrı
kontratlara ertelenir):
  - Deflated Sharpe, PBO, multiple-testing corrections, parameter
    stability ("Stage-3," Bölüm 17.4-17.7)
  - Probabilistic Sharpe Ratio (seçilen annualized Sharpe formülü
    tarafından gerektirilmez — bkz. 15.23)
  - CPCV, purging/embargo (17.1-17.2)
  - Candidate selection/ranking, optimizer/grid/random/Bayesian search
  - Cross-window metric aggregation, trial aggregation
  - Final untouched holdout enforcement
  - Reporting, persistence, serialization, dashboards, CLI output
  - Win rate, profit factor, exposure, turnover, benchmark-relative
    metrikler (Bölüm 15.1'in ZATEN dışladığı liste, DEĞİŞMEDEN)
```

**Mevcut Stage-1/Stage-2 public object'leri DEĞİŞMEDEN kalır:** `Stage1Metrics`, `compute_stage1_metrics`, `Stage2Metrics`, `compute_periodic_returns`, `compute_stage2_metrics` — dördü de bu kontrat tarafından **hiçbir field/parametre/davranış değişikliği** almaz; yalnızca **reuse edilir** (aşağıdaki bölümler).

### 15.21 Exact Public API (LOCKED)

**Karşılaştırılan bağımlılık şekilleri (Bölüm 3'ün istediği karşılaştırma):**

```
1. BacktestResult'tan doğrudan yeniden hesaplama (return-series/
   drawdown'ı Stage-1/Stage-2'yi BYPASS ederek tekrar implement etme)
   — REDDEDİLDİ: "do not create duplicate return-series or drawdown
   algorithms when existing locked functions can safely be reused."
2. Yalnızca Stage2Metrics TÜKETİLİR (mean/stdev/sharpe hazır) —
   Sharpe için KISMEN SEÇİLDİ (annualized Sharpe, Stage2Metrics.sharpe_ratio'yu
   reuse eder) — ama Sortino için REDDEDİLDİ (aşağıdaki gerekçe, 15.24).
3. Yalnızca periodic-return tuple'ı TÜKETİLİR (compute_periodic_returns) —
   Sortino için SEÇİLDİ (aşağıdaki gerekçe, 15.24); Sharpe/CAGR/Calmar
   için gerekli DEĞİL (onlar zaten Stage-1/Stage-2'nin ÖZETLENMİŞ
   çıktısını reuse eder, ham return tuple'ını DEĞİL).
4. Dedicated bir "annualization input" value object'i (timeframe +
   calendar-basis + periods_per_year'ı SARMALAYAN yeni bir frozen
   dataclass) — REDDEDİLDİ: calendar basis TEK bir global sabit olarak
   kilitlenir (15.22) — per-call configurable bir calendar YOK, bu
   nedenle sarmalayacak ikinci bir alan yok; böyle bir value object
   "no speculative framework"/"minimal API surface" önceliklerini
   ihlal eden gereksiz bir katman olurdu (Candidate/Trial'ın kendi
   "no generic plugin/registry framework" reddettiği ilkeyle aynı).
5. Timeframe veya periods-per-year DEĞERİ — SEÇİLDİ (canonical
   `timeframe: str`, aşağıdaki 15.22'nin gerekçesiyle; ham
   `periods_per_year: Decimal` REDDEDİLDİ, 15.22).
6. (2)+(3)+(5)'in explicit kombinasyonu — **SEÇİLDİ** (nihai mimari):
   her fonksiyon, kendi ihtiyacına göre (2) VEYA (3)'ü, HER ZAMAN (5)
   ile birlikte, tüketir.
```

**Seçilen mimari: dört BAĞIMSIZ, küçük public fonksiyon — TEK bir birleşik "AnnualizedMetrics" dataclass'ı DEĞİL.**

```
Reddedilen alternatif: tek bir compute_annualized_metrics(result, *,
  timeframe, ...) -> AnnualizedMetrics(sharpe, sortino, cagr, calmar)
  bundle'ı — REDDEDİLDİ. Gerekçe: (a) Stage2Metrics'in üç alanı AYNI
  tek-geçişli aritmetik bloktan (aynı mean_return'den) türetildiği için
  bundle edilir — ama annualized Sharpe/Sortino/CAGR/Calmar BİRBİRİNDEN
  BAĞIMSIZ formüllerdir (farklı payda/numerator kaynakları), ortak bir
  tek-geçiş hesaplaması PAYLAŞMAZLAR. (b) Bundle etmek "all-or-nothing"
  bir başarısızlık modu yaratırdı: örn. total_return <= -1 CAGR/Calmar'ı
  UNDEFINED kılar, ama Sharpe/Sortino aynı result için GEÇERLİ kalabilir
  — bundled bir fonksiyon bu durumda TÜM dört metriği reddederdi,
  yalnızca ikisinin geçerli olduğu bir durumda bile. Bu, mevcut "no
  silent repair, ama gereksiz-geniş red de yok" felsefesiyle
  UYUMSUZDUR. (c) compute_periodic_returns zaten bir dataclass'a
  SARMALANMADAN çıplak `tuple[Decimal, ...]` döndürür — bu, "gerekli
  olmadıkça yeni bir value object İCAT ETME" için doğrudan bir
  repository precedent'idir; dört bağımsız skaler metrik için de AYNI
  precedent uygulanır (bare Decimal, yeni dataclass YOK).
```

Kilitlenen exact production şekli:

```python
# Modül: src/crypto_quant_lab/validation/annualized_metrics.py (YENİ modül)


def compute_annualized_sharpe_ratio(
    result: BacktestResult,
    *,
    timeframe: str,
    risk_free_per_period: Decimal = Decimal(0),
) -> Decimal: ...


def compute_sortino_ratio(
    result: BacktestResult,
    *,
    timeframe: str,
    minimum_acceptable_return_per_period: Decimal = Decimal(0),
) -> Decimal: ...


def compute_cagr(
    result: BacktestResult,
    *,
    timeframe: str,
) -> Decimal: ...


def compute_calmar_ratio(
    result: BacktestResult,
    *,
    timeframe: str,
) -> Decimal: ...
```

```
- `result` her fonksiyonda pozisyoneldir; `timeframe` ve rate/target
  parametreleri keyword-only'dir (mevcut compute_stage2_metrics'in
  `*, risk_free_per_period` convention'ıyla birebir tutarlı).
- Dört fonksiyonun HİÇBİRİ yeni bir dataclass/value object döndürmez —
  hepsi bare `Decimal` döner (yukarıdaki gerekçe).
- Hiçbir fonksiyon `None` döndürmez; undefined bir sonuç HER ZAMAN
  deterministik bir exception olarak temsil edilir (Bölüm 15.27'nin
  "rejection, never None" kilidi — aynı Stage-1/Stage-2 felsefesi).
- `validation/__init__.py` DEĞİŞMEDEN kalır — bu dört fonksiyon
  package-root'ta export EDİLMEZ (windows.py/rolling.py/metrics.py/
  candidate.py ile AYNI zero-re-export convention'ı).
- `BacktestResult`, `WindowResult`, `EquityPoint`, `Stage1Metrics`,
  `Stage2Metrics`, ve mevcut Stage-1/Stage-2 public fonksiyonlarının
  imzaları DEĞİŞMEDEN kalır.
- Aynı dört fonksiyon, hem doğrudan bir `BacktestResult` üzerinde hem
  de bağımsız bir `WindowResult.result` üzerinde çalışır — hiçbir
  per-window wrapper veya cross-window aggregate TANITILMAZ (Stage-1/
  Stage-2 ile AYNI desen).
```

### 15.22 Annualization Input, Provenance, ve Calendar Basis (LOCKED)

**Annualization bilgisinin API'ye giriş şekli — karşılaştırılan alternatifler:**

```
1. Explicit periods_per_year: Decimal — REDDEDİLDİ: caller'ın her çağrı
   sitesinde calendar-basis sabitini KENDİSİNİN yeniden hesaplaması
   gerekir (DRY ihlali); bir caller, HERHANGİ bir Decimal'i (gerçek
   hiçbir timeframe'e karşılık gelmeyen) geçirebilir — bu, "accidentally
   mixing" riskini AZALTMAZ, TERSİNE ARTIRIR (mekanik bir timeframe-
   tutarlılık ipucu bile YOKTUR).
2. **SEÇİLDİ — Explicit canonical timeframe: str** (mevcut
   `market_data.timeframes.candle_duration`'ın kabul ettiği AYNI
   string uzayı: yalnızca "1h"/"4h" bugün) — periods_per_year, BU
   fonksiyon TARAFINDAN, `candle_duration(timeframe)` reuse edilerek
   İÇSEL olarak türetilir. Gerekçe: (a) mevcut store_runner/rolling
   API'lerinin HER YERDE `timeframe: str`'i explicit kabul etme
   convention'ıyla birebir tutarlı; (b) `candle_duration`'ı REUSE eder
   (Bölüm 4/21 "compose, never duplicate"); (c) geçersiz bir timeframe
   string'i `candle_duration`'ın KENDİ ValueError'ı ile REDDEDİLİR —
   yeni bir timeframe/duration tablosu İCAT EDİLMEZ.
3. Equity-curve timestamp'lerinden inference — REDDEDİLDİ: Bölüm
   15.14'ün ZATEN LOCKED "hiçbir calendar/periods_per_year değeri
   otomatik olarak INFER EDİLMEZ" ilkesini ihlal ederdi.
4. Result/config metadata'dan inference — REDDEDİLDİ: BacktestResult/
   BacktestConfig hiçbir timeframe field'ı TAŞIMAZ (15.19 kaynak
   bulgusu) — inference edilecek bir kaynak YOKTUR.
5. Elapsed-time annualization (gerçek wall-clock süre) — REDDEDİLDİ
   CAGR için de (bkz. 15.25) — initial_cash'in "gerçekleştiği an" için
   mekanik olarak geçerli bir timestamp kaynağı YOKTUR (15.19).
6. Dedicated immutable annualization value object — REDDEDİLDİ (15.21,
   madde 4 — gereksiz katman).
```

**Lock edilen mekanizma:** her dört fonksiyon, `timeframe: str`'i keyword-only, zorunlu (default YOK) olarak kabul eder. **Hiçbir silent inference YOKTUR** — `timeframe` her çağrıda explicit olarak sağlanmalıdır.

**Periods-per-year exact türetimi (private, public API'nin parçası DEĞİL):**

```python
_YEAR_MICROSECONDS = 365 * 86_400 * 1_000_000  # exact int, calendar basis (bkz. aşağı)


def _timedelta_to_microseconds(duration: timedelta) -> int:
    # timedelta.total_seconds() KULLANILMAZ (float döner) — storage/
    # sqlite_codec.py'nin datetime_to_epoch_us'ının AYNI exact-integer
    # tekniği burada bir bare timedelta'ya uygulanır (yeni bir teknik
    # İCAT EDİLMEZ, mevcut desen REUSE edilir).
    return duration.days * 86_400_000_000 + duration.seconds * 1_000_000 + duration.microseconds


def _periods_per_year(timeframe: str) -> Decimal:
    duration_us = _timedelta_to_microseconds(candle_duration(timeframe))
    return Decimal(_YEAR_MICROSECONDS) / Decimal(duration_us)
```

```
- `candle_duration` (market_data/timeframes.py, DEĞİŞMEDEN) REUSE
  edilir — geçersiz bir timeframe onun KENDİ ValueError'ı ile
  reddedilir, mesaj DEĞİŞTİRİLMEDEN propagate edilir.
- `timeframe` str olmalıdır; değilse önce TypeError (candle_duration
  zaten yalnızca `==` string karşılaştırması yapar — non-str bir
  girdi hiçbir zaman eşleşmez ve candle_duration'ın KENDİ ValueError'ına
  düşer; bu kontrat, non-str bir girdiyi bu fonksiyonların KENDİ,
  daha erken bir TypeError'ıyla EXPLICIT olarak reddeder — Bölüm 15.27,
  adım 1).
- 1h -> periods_per_year = Decimal('8760') (exact).
- 4h -> periods_per_year = Decimal('2190') (exact).
- Bu iki değer, `.sqrt()`/exponentiation'a girmeden ÖNCE, exact tam
  sayı-değerli Decimal'lerdir — genel formül gelecekteki bir timeframe
  için kalanlı bir bölme üretebilir (bugün mümkün değil, çünkü Faz 3
  MVP yalnızca 1h/4h destekler); formül YİNE DE genel doğru kalır.
```

**Calendar basis — karşılaştırılan konvansiyonlar:**

```
1. 365.25 gün/yıl — REDDEDİLDİ: leap-year ortalaması equity/geleneksel
   finans konvansiyonudur; crypto 7/24/365 işlem görür, "leap day"
   kavramının kripto analytics'te bir etkisi YOKTUR; 365 tam sayı,
   365.25'e göre daha basit ve endüstri-yaygın kripto-annualization
   varsayılanıdır.
2. 252 trading days — REDDEDİLDİ: equity/hisse-senedi piyasası
   konvansiyonudur (trading-day/holiday takvimi varsayar); crypto'da
   "trading day" kavramı YOKTUR (piyasa hiç kapanmaz) — bu repository'nin
   crypto-market kapsamı için AÇIKÇA UYGUNSUZDUR.
3. Timeframe-bağımlı exchange calendar (borsa-özel tatil/kapanış
   takvimi) — REDDEDİLDİ: crypto borsaları 7/24 çalışır, böyle bir
   calendar KAVRAMSAL OLARAK YOKTUR; icat etmek Faz 3'ün "no calendar-
   variable interval" ilkesini (candle_duration'ın kendi docstring'i)
   ihlal ederdi.
4. **SEÇİLDİ — 365 gün/yıl, exact, sabit, configurable DEĞİL.**
```

```
- Exact sabit: 365 (gün), float DEĞİL — yalnızca yukarıdaki
  `_YEAR_MICROSECONDS = 365 * 86_400 * 1_000_000` integer sabiti
  üzerinden kullanılır.
- Birimler: gün -> mikrosaniye (integer aritmetik, hiçbir noktada
  float'a dönüşüm YOK).
- Leap year'lar sonucu ETKİLEMEZ — bu SENKRON bir yıl uzunluğu
  varsayımıdır (gerçek takvim yılını izlemez), bu bilinçli ve
  dokümante edilmiş bir basitleştirmedir, "informal approximately
  annualized" bir davranış DEĞİLDİR — sabit, tek, exact bir konvansiyondur.
- Configurable DEĞİLDİR — caller bu sabiti override EDEMEZ; bu
  mikro-adım bir "calendar strategy" parametresi İCAT ETMEZ.
- Fraction-versus-percent: tüm annualized metrikler Stage-1/Stage-2 ile
  AYNI Decimal-fraction convention'ını kullanır (örn. Decimal("0.05")
  == +%5) — hiçbir percentage-string veya float yüzde TANITILMAZ.
```

**Caller-discipline sınırları (mekanik olarak enforce EDİLMEZ, explicit olarak kaydedilir — Bölüm 19'un engine-vs-process ayrımıyla tutarlı):**

```
- Bu kontrat, bir caller'ın YANLIŞ bir timeframe geçirip GERÇEK
  equity-curve cadence'i ile UYUŞMAYAN bir `timeframe` argümanı
  sağlamasını MEKANİK OLARAK ENGELLEMEZ — BacktestResult hiçbir
  timeframe field'ı taşımadığından, bu fonksiyonların KENDİSİ bunu
  ASLA doğrulayamaz. Bu, dürüstçe kaydedilen bir absence-of-guarantee'dir
  (Trial'ın §18.7'deki market_type/symbol/timeframe homogeneity
  sınırıyla AYNI kategoriden bir trusted-caller-invariant).
- Bu kontrat, bir caller'ın bir sonucu bir timeframe ile hesaplayıp
  başka bir timeframe ile annualize etmesini ("accidentally mixing")
  MEKANİK OLARAK ENGELLEMEZ.
- Irregular/gapped equity-curve timestamp'leri annualization faktörünü
  ETKİLEMEZ — periods_per_year, GERÇEK gözlenen spacing'den DEĞİL,
  yalnızca caller'ın sağladığı `timeframe`'den türetilir (15.14'ün
  "hiçbir cadence field'ı YOK" bulgusuyla tutarlı) — bu MATEMATİKSEL
  OLARAK GEÇERLİ ama SEMANTİK OLARAK YANLIŞ bir sonuç üretebilir, bu
  kontratın kapsamı dışındaki bir caller-discipline riskidir.
- Bu bağımsızlık Candidate/Trial/rolling orchestration'dan TAMAMEN
  KOPUKTUR: annualized_metrics.py hiçbir zaman `candidate.py`/`rolling.py`/
  `windows.py`'yi import ETMEZ; bir caller'ın elinde bir `Trial` objesi
  varsa `trial.timeframe`'i bu modülün `timeframe` argümanı olarak
  GEÇEBİLİR — ama bu SALT çağıranın external composition'ıdır, modülün
  içsel bir bağımlılığı DEĞİLDİR.
```

### 15.23 Annualized Sharpe Ratio Kontratı (LOCKED)

**İlişki mevcut non-annualized Sharpe'a (Bölüm 15.16) — TEK bir Sharpe tanımı, iki competing formül YOK:**

```
annualized_sharpe = compute_stage2_metrics(result, risk_free_per_period=risk_free_per_period).sharpe_ratio
                     * _periods_per_year(timeframe).sqrt()
```

```
- `compute_stage2_metrics` (DEĞİŞMEDEN, Bölüm 15.9-15.18) TAM OLARAK
  reuse edilir — ikinci bir return-series/mean/stdev algoritması
  İCAT EDİLMEZ.
- `risk_free_per_period`, Stage-2 ile BİREBİR AYNI konvansiyonu korur:
  per-OBSERVATION bir input, ASLA bir annual rate, ASLA bir annual
  rate'ten CONVERT EDİLMEZ (Bölüm 15.11, 15.14'ün AYNI kilidi) —
  annual-to-period bir conversion formülü bu kontrat tarafından İCAT
  EDİLMEZ; caller, annual bir risk-free rate'i per-period'e kendisi
  dönüştürmekten sorumludur (bu dönüşümün NASIL yapılacağı bu
  kontratın kapsamı DIŞINDADIR — annualization faktörünün KENDİSİ,
  aşağıdaki sqrt(periods_per_year) çarpanı, zaten annualization'ın
  TEK mekanizmasıdır).
- Annualization çarpanı: `_periods_per_year(timeframe).sqrt()` — Sharpe
  istatistiğinin klasik sqrt(zaman) ölçeklenmesi (i.i.d. return
  varsayımı altında standart konvansiyon).
- Exact operation sırası: ÖNCE Stage-2'nin `sharpe_ratio`'sunu al
  (kendi TAM validation zinciriyle), SONRA `periods_per_year.sqrt()`'u
  hesapla, SONRA çarp. Cebirsel olarak eşdeğer bir rewrite (örn.
  mean/stdev'i annualize edip SONRA bölmek) KULLANILMAZ — bu, farklı
  finite-precision davranışı üretebilir ve LOCKED DEĞİLDİR.
- Sıfır/negatif/undefined return_stdev (Stage-2'nin kendi zaten-LOCKED
  reddi) bu fonksiyonun KENDİ hatası olarak yeniden sarmalanmaz —
  Stage-2'nin ORİJİNAL exception'ı (tip ve mesaj DEĞİŞMEDEN) propagate
  edilir.
- Negatif annualized Sharpe legal'dir (negatif per-observation
  Sharpe'ın annualize edilmiş hali) — özel bir davranış YOK.
- Hesaplanmış annualized_sharpe finite OLMALIDIR; değilse deterministik
  ValueError (Bölüm 15.27, adım son).
- Probabilistic Sharpe Ratio bu formül tarafından GEREKTİRİLMEZ —
  seçilen formül yalnızca bir sqrt(zaman) ölçeklemesidir, dağılımsal
  bir güven-aralığı hesaplaması İÇERMEZ; bu nedenle PSR bu mikro-adımın
  kapsamı DIŞINDA kalır (Bölüm 3'ün "unless strictly required" koşulu
  karşılanmaz).
```

### 15.24 Sortino Ratio Kontratı (LOCKED)

**Neden `compute_stage2_metrics` DEĞİL, `compute_periodic_returns` tüketilir:**

```
compute_stage2_metrics, return_stdev (TOPLAM standart sapma) sıfır/
negatifse deterministik olarak REDDEDER (Bölüm 15.15, adım 7) — ama
Sortino'nun geçerliliği TOPLAM stdev'den BAĞIMSIZDIR (downside deviation,
FARKLI bir paydadır; sabit bir dönüş serisi total_stdev'i sıfır yapabilir
AMA hedefin altındaysa downside deviation'ı SIFIR OLMAYABİLİR). Sortino'yu
compute_stage2_metrics'e COUPLE ETMEK, geçerli bir Sortino'yu YANLIŞ
BİR NEDENLE reddedebilirdi. Bu nedenle Sortino, `compute_periodic_returns`'ü
(ham return tuple'ı) tüketir — return-series'in KENDİSİ REUSE edilir
(ikinci bir seri-inşa algoritması İCAT EDİLMEZ), ama toplam mean/stdev
istatistiği Stage-2'den BAĞIMSIZ olarak, Bölüm 15.16'nın AYNI kilitli
arithmetic-mean formülüyle (tek satırlık formülün KENDİSİ reuse edilir,
`compute_periodic_returns`'ün çıktı tuple'ı üzerinde) burada AYRICA
uygulanır.
```

**Downside deviation — karşılaştırılan konvansiyonlar:**

```
1. Yalnızca downside gözlemler PAYDAYA dahil edilir (semi-deviation,
   yalnızca-downside-count) — REDDEDİLDİ: standart olmayan bir
   varyanttır; küçük-örneklem instabilitesine açıktır (SIFIR downside
   gözlem varsa payda-count 0/0 belirsizliğine yol açar); Sharpe/Stage-2
   ile PARALEL bir "n toplam gözlem" yapısını BOZAR.
2. **SEÇİLDİ — TÜM N gözlem paydaya dahil edilir**, downside-olmayan
   her gözlem KARESİ-toplamına SIFIR katkıda bulunur (orijinal
   Sortino/Frank Sortino formülasyonu, standart/kanonik konvansiyon).
   `n`, Stage-2'nin KENDİ `n`'i (return_count) ile AYNI, PARALEL yapı.
```

Exact formül ve operation sırası:

```python
returns = compute_periodic_returns(result)  # reuse, ikinci kez inşa EDİLMEZ
n = len(returns)  # Stage-2'nin n'i ile AYNI sayım
return_sum = sum(returns, Decimal(0))  # Bölüm 15.16 ile AYNI formül
mean_return = return_sum / Decimal(n)

downside_squared_sum = Decimal(0)
for periodic_return in returns:
    deviation = periodic_return - minimum_acceptable_return_per_period
    downside_deviation_term = deviation if deviation < Decimal(0) else Decimal(0)
    downside_squared_sum += downside_deviation_term * downside_deviation_term

downside_variance = downside_squared_sum / Decimal(n)  # population divisor, n-1 DEĞİL
downside_deviation = downside_variance.sqrt()

per_period_sortino = (mean_return - minimum_acceptable_return_per_period) / downside_deviation
sortino_ratio = per_period_sortino * _periods_per_year(timeframe).sqrt()
```

```
- Payda `n` kullanır (POPULATION-style), Stage-2'nin sample-stdev'inin
  `n-1`'i (Bessel correction) DEĞİL — gerekçe: downside deviation,
  ÖRNEĞİN KENDİ ortalamasından DEĞİL, SABİT bir hedeften (MAR) sapmayı
  ölçer; Bessel correction'ın telafi ettiği "ortalamayı örneğin
  kendisinden tahmin etme" durumu burada GEÇERLİ DEĞİLDİR — bu,
  akademik/pratisyen Sortino literatüründeki standart konvansiyondur.
  Bu, Stage-2'nin `n-1` konvansiyonuyla KASITLI OLARAK FARKLIDIR ve bu
  fark açıkça dokümante edilir.
- `deviation < Decimal(0)` sıkı-küçüktür (strictly-below-target) testi
  kullanılır — ama `min(Decimal(0), deviation)` formülasyonu altında
  eşitlik durumu (`deviation == 0`) zaten KARE-SIFIR katkı üretir
  (`0**2 == 0`), bu nedenle strictly-below vs less-than-or-equal ayrımı
  ARİTMETİK OLARAK ANLAMSIZDIR (her iki okuma da AYNI sonucu üretir) —
  bu belirsizlik yukarıdaki formülasyonla DOĞAL OLARAK çözülür, ayrı
  bir dallanma İCAT EDİLMEZ.
- "Downside gözlem YOK" (tüm return'ler >= MAR) özel bir dal DEĞİLDİR
  — doğal olarak `downside_squared_sum == 0` -> `downside_deviation == 0`
  durumuna DÜŞER, aşağıdaki "sıfır downside deviation" kuralıyla AYNI
  şekilde ele alınır.
- Sıfır (veya non-finite) downside_deviation, Sortino'yu TANIMSIZ kılar
  ve deterministik ValueError fırlatır — sıfır, None, NaN, veya
  Infinity DÖNDÜRÜLMEZ (Stage-2'nin sıfır-stdev reddiyle AYNI felsefe).
- Negatif numerator (mean_return < MAR) legal'dir, negatif bir Sortino
  üretir.
- `minimum_acceptable_return_per_period`, Stage-2'nin `risk_free_per_period`
  ile AYNI konvansiyonu izler: explicit, per-OBSERVATION, finite bir
  Decimal, default `Decimal(0)` — ASLA annual, ASLA convert edilir.
- En az İKİ periyodik return gereklidir (`n >= 2`) — bu, downside-
  deviation formülünün KENDİSİ için matematiksel olarak zorunlu
  DEĞİLDİR (n=1'de bile tanımlıdır), ama bu kontrat içindeki Sharpe/
  Stage-2 ile AYNI minimum-örneklem eşiğini korumak için BİLİNÇLİ
  OLARAK seçilmiştir (aynı annualized-metrics kontratı içinde iki
  farklı minimum-sample eşiği OLMASINI önlemek — Bölüm 3'ün "do not
  allow two competing... definitions" ilkesinin Sortino'ya uzantısı).
  Reddedilen alternatif: n>=1 (matematiksel olarak yeterli, ama
  tutarsız eşik nedeniyle REDDEDİLDİ).
- Annualization: `per_period_sortino * periods_per_year.sqrt()` —
  Sharpe ile AYNI mekanizma (15.23), İKİ FARKLI annualization
  konvansiyonu İCAT EDİLMEZ.
- Hesaplanmış sortino_ratio finite OLMALIDIR; değilse deterministik
  ValueError.
```

### 15.25 CAGR Kontratı (LOCKED)

**Elapsed wall-clock time vs. return-period count — karşılaştırılan alternatifler:**

```
1. Gerçek elapsed wall-clock süre (equity_curve[0].time'dan
   equity_curve[-1].time'a, veya initial_cash'in "gerçekleştiği an"dan)
   — REDDEDİLDİ: initial_cash'in gerçekleştiği an (evaluation_start/
   window.start) hiçbir result-modelinde SAKLANMAZ (15.19 kaynak
   bulgusu) — bu, "do not infer a missing initial timestamp unless the
   repository provides a mechanically valid source for it" talimatının
   DOĞRUDAN uygulandığı bir durumdur: mekanik olarak geçerli bir kaynak
   YOKTUR, bu nedenle inference edilmez. equity_curve[0].time'ı "başlangıç"
   olarak kullanmak da YANLIŞ olurdu — o nokta zaten equity_curve'ün
   İLK GÖZLEMİDİR, initial_cash'in AN'ı DEĞİL (Bölüm 15.13: initial_cash
   "implicit period 0" baseline'ıdır, equity_curve[0] "period 1"in
   sonucudur).
2. **SEÇİLDİ — return-period count (N) + periods-per-year.** Grid-
   aligned candle cadence'i (Bölüm 6/7'nin timeframe-grid-alignment
   ilkesi) sayesinde, N periyodik gözlem TAM OLARAK N * candle_duration
   elapsed zamana karşılık gelir — bu nedenle N + periods_per_year
   (zaten Sharpe/Sortino için gereken AYNI input), HİÇBİR RAW TIMESTAMP'E
   İHTİYAÇ DUYMADAN, elapsed-time annualization'ın SEMANTİK EŞDEĞERİNİ
   sağlar. Bu, initial-timestamp sorununu TAMAMEN ORTADAN KALDIRIR.
```

Exact formül ve operation sırası:

```python
total_return = compute_stage1_metrics(result).total_return  # reuse, DEĞİŞMEDEN
n = len(result.equity_curve)  # Stage-1'in ZATEN
# validate ettiği curve'den
periods_per_year = _periods_per_year(timeframe)

base = Decimal(1) + total_return
exponent = periods_per_year / Decimal(n)
cagr = base**exponent - Decimal(1)
```

```
- `total_return`, `compute_stage1_metrics(result).total_return`'DAN
  REUSE EDİLİR — `final_equity / initial_cash` İKİNCİ KEZ BAĞIMSIZ
  OLARAK HESAPLANMAZ (aksi halde precision-28 altında iki ayrı hesaplama
  teorik olarak farklı son basamak üretebilirdi; Bölüm 4/21 "compose,
  never duplicate").
- `n = len(result.equity_curve)`, `result` `compute_stage1_metrics`
  tarafından ZATEN TAM OLARAK validate edildikten SONRA okunur (lower-
  layer'ın validation'ını GÜVENEREK, yeniden validate EDİLMEDEN — Bölüm
  18.7'nin "trust the lower layer" precedent'iyle tutarlı).
- Exact operation sırası: ÖNCE `base = 1 + total_return`, SONRA
  `exponent = periods_per_year / n`, SONRA `base ** exponent`, EN SON
  `- 1`. Cebirsel olarak eşdeğer bir rewrite (örn. `(final_equity/
  initial_cash) ** exponent - 1`, total_return'ü BYPASS ederek) KULLANILMAZ.
- Minimum örneklem: `n >= 1` yeterlidir (Stage-1'in ZATEN LOCKED "boş
  olmayan curve" alt sınırı) — Sharpe/Sortino'nun `n >= 2` eşiğinden
  KASITLI OLARAK DAHA GEVŞEKTİR, çünkü CAGR'ın formülü hiçbir variance/
  stdev hesaplaması İÇERMEZ (n>=2'nin var olma nedeni SPESİFİK olarak
  sample-stdev'in n-1 paydasıdır — bu formülde YOKTUR). "Zero duration"
  (N=0) zaten Stage-1'in boş-curve reddi tarafından İMKANSIZ kılınmıştır
  — bu case CAGR'a hiçbir zaman ULAŞAMAZ.
- `total_return == 0` (final_equity == initial_cash) -> `base == 1` ->
  `1 ** herhangi bir exponent == 1` (empirik olarak doğrulanmıştır) ->
  `cagr == 0` (exact).
- `total_return == -1` (final_equity == 0) -> `base == 0`, exponent
  HER ZAMAN pozitiftir (periods_per_year > 0, n > 0) -> `0 ** pozitif
  == 0` (empirik olarak doğrulanmıştır, LEGAL) -> `cagr == -1` (exact,
  total wipeout annualize edilmiş hali de -%100'dür).
- `total_return < -1` (final_equity negatif, Stage-1'de LEGAL bir
  durum) -> `base < 0`, fractional (non-integer) bir exponent ile ->
  `Decimal.__pow__`, empirik olarak doğrulanmış şekilde `Decimal('NaN')`
  üretir (exception RAISE ETMEZ, traps=[] altında) -> CAGR bu noktada
  TANIMSIZDIR ve post-computation finiteness check'i (Bölüm 15.27)
  tarafından deterministik ValueError ile REDDEDİLİR. Bu, "total return
  below -1 remains legal for Stage-1 but makes CAGR undefined" talimatının
  BİREBİR karşılığıdır.
- Irregular equity-curve timestamp'leri CAGR'ı ETKİLEMEZ — formül HİÇBİR
  RAW TIMESTAMP OKUMAZ, yalnızca `n` (count) ve `periods_per_year`
  (timeframe-türetilmiş) kullanır.
- Overflow/underflow (ekstrem total_return/periods_per_year/n
  kombinasyonları) -> hesaplanmış cagr non-finite (Infinity) olabilir
  -> deterministik ValueError (Bölüm 15.27).
```

### 15.26 Calmar Ratio Kontratı (LOCKED)

Exact formül ve operation sırası:

```python
cagr = compute_cagr(result, timeframe=timeframe)  # reuse, TAM validation+CAGR dahil
max_drawdown = compute_stage1_metrics(result).max_drawdown  # reuse, İKİNCİ bir drawdown
# algoritması İCAT EDİLMEZ
calmar_ratio = cagr / max_drawdown
```

```
- `compute_cagr` DOĞRUDAN reuse edilir — `timeframe`/`result` validation'ı
  VE CAGR formülünün KENDİSİ burada TEKRARLANMAZ; `compute_cagr`'ın
  fırlattığı HERHANGİ bir exception (undefined CAGR dahil), tip ve
  mesaj DEĞİŞMEDEN propagate edilir — Calmar'a özel, ayrı bir "undefined
  CAGR" ele alma dalı İCAT EDİLMEZ.
- `compute_stage1_metrics(result).max_drawdown` reuse edilir —
  `result`, `compute_cagr`'ın kendi çağrısı içinde ZATEN validate
  edilmiş olsa da, `compute_stage1_metrics`'in KENDİ (idempotent, saf)
  validation'ı burada tekrar çalışır — bu, gereksiz ama ZARARSIZ bir
  redundancy'dir (Stage-2'nin kendi içinde `_require_core_result_contract`'ı
  hem `compute_periodic_returns` hem `compute_stage2_metrics`
  aracılığıyla birden fazla kez çalıştırmasıyla AYNI, ZATEN var olan
  precedent) — max_drawdown için İKİNCİ bir algoritma İCAT EDİLMEZ.
- `max_drawdown == 0` (mükemmel, hiç drawdown yaşamamış bir backtest)
  -> bölme sıfıra -> traps=[] altında `Infinity`/`NaN` (non-finite) ->
  deterministik ValueError — Calmar bu durumda TANIMSIZDIR. Reddedilen
  alternatif: `Decimal('Infinity')` döndürmek ("sonsuz iyi" temsili) —
  REDDEDİLDİ, çünkü bu Stage-1/Stage-2'nin HER YERDE tutarlı "finite-only
  output, undefined -> raise" felsefesini ihlal ederdi ve repository'de
  hiçbir yerde başka bir "infinite metric legal" precedent'i YOKTUR.
- Negatif CAGR legal'dir -> negatif Calmar (özel davranış YOK).
- `max_drawdown == 1` VE `max_drawdown > 1` (Stage-1'in ZATEN LOCKED,
  yapay üst-sınırsız drawdown kontratı) -> sıradan bölme, özel bir
  davranış GEREKMEZ.
- Hesaplanmış calmar_ratio finite OLMALIDIR; değilse deterministik
  ValueError.
```

### 15.27 Validation / Fail-Fast Sırası (LOCKED, Exact)

Dört fonksiyonun HER BİRİ için numaralı, deterministik sıra — arithmetic, yalnızca TÜM adımlar geçtikten SONRA başlar. Hiçbir adım sessizce sort/repair/normalize/clip/substitute/discard YAPMAZ.

**`compute_annualized_sharpe_ratio(result, *, timeframe, risk_free_per_period=Decimal(0))`:**

```
1. timeframe bir str olmalıdır; değilse TypeError.
2. timeframe candle_duration() tarafından desteklenmelidir; değilse
   candle_duration'ın KENDİ ValueError'ı (mesaj DEĞİŞMEDEN propagate).
3. compute_stage2_metrics(result, risk_free_per_period=risk_free_per_period)
   ÇAĞRILIR — bu TEK çağrı, KENDİ tam sırasıyla (Bölüm 15.15: result
   tipi, initial_cash finite+pozitif, final_equity finite, equity_curve
   geçerliliği, positive denominators, risk_free_per_period tip+finite,
   minimum-2-return, mean/stdev/sharpe arithmetic + finiteness) TÜMÜNÜ
   uygular; HERHANGİ bir adım başarısız olursa, o ORİJİNAL exception
   (tip+mesaj) DEĞİŞMEDEN propagate edilir.
4. Yalnızca (3) TAMAMEN başarılı olduktan SONRA: periods_per_year
   hesaplanır (adım 2'nin duration'ından), annualization_factor =
   periods_per_year.sqrt() hesaplanır, annualized_sharpe = sharpe_ratio
   * annualization_factor hesaplanır — HEPSİ private context içinde
   (Bölüm 15.28).
5. annualized_sharpe finite olmalıdır; değilse ValueError.
6. annualized_sharpe döndürülür.
```

**`compute_sortino_ratio(result, *, timeframe, minimum_acceptable_return_per_period=Decimal(0))`:**

```
1. timeframe bir str olmalıdır; değilse TypeError.
2. timeframe candle_duration() tarafından desteklenmelidir; değilse
   candle_duration'ın KENDİ ValueError'ı.
3. minimum_acceptable_return_per_period bir Decimal olmalıdır; değilse
   TypeError.
4. minimum_acceptable_return_per_period finite olmalıdır; değilse
   ValueError.
5. compute_periodic_returns(result) ÇAĞRILIR — result/curve/payda'nın
   TAM Bölüm 15.15 (adım 1-10) validation zinciri uygulanır; HERHANGİ
   bir adım başarısız olursa, o ORİJİNAL exception DEĞİŞMEDEN propagate
   edilir.
6. len(returns) >= 2 olmalıdır; değilse ValueError (Stage-2'nin AYNI
   eşiği, burada BAĞIMSIZ olarak enforce edilir çünkü
   compute_stage2_metrics ÇAĞRILMAZ).
7. Yalnızca (6) geçtikten SONRA: mean_return (Bölüm 15.16 formülü),
   downside_deviation (15.24 formülü) hesaplanır.
8. downside_deviation finite VE Decimal("0")'dan KESİNLİKLE BÜYÜK
   olmalıdır; değilse ValueError (sıfır/negatif/non-finite downside
   deviation, Sortino'yu tanımsız kılar).
9. per_period_sortino, annualization_factor, sortino_ratio hesaplanır
   (private context içinde).
10. sortino_ratio finite olmalıdır; değilse ValueError.
11. sortino_ratio döndürülür.
```

**`compute_cagr(result, *, timeframe)`:**

```
1. timeframe bir str olmalıdır; değilse TypeError.
2. timeframe candle_duration() tarafından desteklenmelidir; değilse
   candle_duration'ın KENDİ ValueError'ı.
3. compute_stage1_metrics(result) ÇAĞRILIR — result/initial_cash/
   final_equity/equity_curve'ün TAM Bölüm 15.4 validation zinciri
   uygulanır; HERHANGİ bir adım başarısız olursa, o ORİJİNAL exception
   DEĞİŞMEDEN propagate edilir.
4. n = len(result.equity_curve) okunur (adım 3'ün ZATEN validate
   ettiği curve'den; n >= 1 garanti edilir, yeniden kontrol EDİLMEZ).
5. periods_per_year, base, exponent, cagr hesaplanır (private context
   içinde, 15.25'in exact operation sırasıyla).
6. cagr finite olmalıdır; değilse ValueError (negatif base'in fractional
   power'ından doğan NaN, veya overflow'dan doğan Infinity dahil).
7. cagr döndürülür.
```

**`compute_calmar_ratio(result, *, timeframe)`:**

```
1. compute_cagr(result, timeframe=timeframe) ÇAĞRILIR — timeframe/result'ın
   TAM validation zinciri VE CAGR'ın KENDİ tüm edge-case kuralları
   (adım 1-7, yukarıda) uygulanır; HERHANGİ bir adım (undefined CAGR
   dahil) başarısız olursa, o ORİJİNAL exception DEĞİŞMEDEN propagate
   edilir.
2. compute_stage1_metrics(result) ÇAĞRILIR (redundant ama zararsız,
   yukarıda 15.26'da gerekçeli) — max_drawdown elde edilir.
3. calmar_ratio = cagr / max_drawdown hesaplanır (private context
   içinde).
4. calmar_ratio finite olmalıdır; değilse ValueError (max_drawdown ==
   0 dahil).
5. calmar_ratio döndürülür.
```

**Global not:** hiçbir adım, geçersiz bir `timeframe`/rate/target girdisini sessizce normalize/coerce/round ETMEZ; hiçbir kısmi/parçalı sonuç döndürülmez — her fonksiyon ya TAM bir Decimal döndürür ya da raise eder.

### 15.28 Decimal-Context Determinism (LOCKED)

```
Private annualized-metrics computation context (Stage-1/Stage-2 ile
AYNI shape, module-privacy convention'ı nedeniyle YENİDEN TANIMLANIR —
_metrics_decimal_context() cross-module import EDİLEMEZ, `_` prefix'i
repo-genelinde module-private anlamına gelir):

Context(
    prec=28,
    rounding=ROUND_HALF_EVEN,
    Emin=-999999,
    Emax=999999,
    capitals=1,
    clamp=0,
    traps=[],
)
```

```
- Her hesaplama için taze (fresh) bir context — paylaşılan bir modül-
  seviyeli sabit DEĞİL (Bölüm 15.7/15.17 ile AYNI gerekçe).
- localcontext(...) izolasyonu — periods_per_year türetimi DAHİL, TÜM
  Decimal aritmetiği (division, sqrt, power/exponentiation, subtraction,
  multiplication) bu private context İÇİNDE çalışır. Empirik olarak
  doğrulanmıştır: `**` operatörü, açıkça sarmalanmadığı sürece AMBIENT
  context'i kullanır — bu nedenle EVERY power/sqrt/division operasyonu
  localcontext(...) bloğu İÇİNDE olmalıdır (15.19'un empirik bulgusu).
- Caller'ın ambient precision/rounding'i çıktıyı ETKİLEMEZ.
- .sqrt() ve fractional-power (**) davranışı empirik olarak doğrulanmıştır
  (15.19): negatif base + fractional exponent -> NaN (raise ETMEZ);
  sıfır base + pozitif fractional exponent -> Decimal('0') (finite,
  legal); 1 ** herhangi -> exact 1; overflow -> Infinity (bu context'in
  Emax=999999 aralığında yalnızca ekstrem girdilerde oluşur).
- Hesaplanmış non-finite bir ara/son değer (periods_per_year, sqrt
  sonucu, base, exponent, power sonucu, final ratio) deterministik
  olarak ValueError ile reddedilir — sessizce başarılı bir non-finite
  metrik ÜRETİLMEZ.
- Float, NumPy, pandas, .quantize() KULLANILMAZ (Bölüm 27).
- Hiçbir mutable context caller'a expose EDİLMEZ.
- Bu bölüm, zaten test edilmiş Stage-1/Stage-2 semantics'ini DEĞİŞTİRMEZ.
```

Standart-kütüphane Python 3.13 `Decimal`, bu kontratın gerektirdiği HER operasyonu (fractional power dahil) zaten destekler (15.19) — implementasyon-edilemez bir API YOKTUR.

### 15.29 Purity, Compatibility, ve Import Direction (LOCKED)

```
Dört annualized-metrics fonksiyonunun HER BİRİ:
- Aynı geçerli input için deterministiktir.
- Input'u (result, equity_curve, config) MUTATE ETMEZ.
- Stage1Metrics, Stage2Metrics, BacktestResult, WindowResult, Candidate,
  veya Trial'a hiçbir field EKLEMEZ/DEĞİŞTİRMEZ.
- Metrikleri persist/serialize ETMEZ.
- Wallclock time, randomness KULLANMAZ.
- I/O yapmaz; hiçbir store'a query atmaz; replay'i çağırmaz.
- Rolling orchestration'ı (run_rolling_backtest_from_store,
  run_context_aware_rolling_backtest_from_store) İMPORT ETMEZ/ÇAĞIRMAZ.
- Cross-window aggregation yapmaz; pencereleri aggregate etmez.
- Candidate/Trial'ı İMPORT ETMEZ; candidate seçmez/sıralamaz/skorlamaz.
- IS/OOS process disiplinini mekanik olarak enforce ETMEZ (Bölüm 19'un
  engine-vs-process ayrımıyla tutarlı — bu kontrat SADECE bir metrik
  formülüdür, bir selection/orchestration kontratı DEĞİLDİR).
- Mevcut public signature'ları (Stage1Metrics, compute_stage1_metrics,
  Stage2Metrics, compute_periodic_returns, compute_stage2_metrics,
  WindowResult, TemporalWindow, TemporalSplit, ContextAwareWindow,
  Candidate, Trial, her iki rolling runner) DEĞİŞTİRMEZ.
- Doğrudan bir BacktestResult kullanımı VE bağımsız bir
  WindowResult.result kullanımı, hiçbir aggregation olmadan desteklenir
  (Stage-1/Stage-2 ile AYNI desen).
- Yalnızca standard-library Python ve Decimal kullanır.
```

**Import direction (LOCKED):**

```
crypto_quant_lab.validation.annualized_metrics  (YENİ modül)
  imports:
    crypto_quant_lab.backtest.models              (BacktestResult — yalnızca tip için)
    crypto_quant_lab.validation.metrics            (compute_stage1_metrics,
                                                      compute_periodic_returns,
                                                      compute_stage2_metrics — PUBLIC
                                                      fonksiyonlar; hiçbir `_` prefix'li
                                                      private helper İMPORT EDİLMEZ)
    crypto_quant_lab.market_data.timeframes        (candle_duration)
    decimal, dataclasses (gerekirse) (stdlib)

crypto_quant_lab.validation.metrics       <- annualized_metrics.py'yi İMPORT ETMEZ
crypto_quant_lab.validation.rolling       <- annualized_metrics.py'yi İMPORT ETMEZ
crypto_quant_lab.validation.windows       <- annualized_metrics.py'yi İMPORT ETMEZ
crypto_quant_lab.validation.candidate     <- annualized_metrics.py'yi İMPORT ETMEZ
crypto_quant_lab.market_data.timeframes   <- validation paketinden HİÇBİR ŞEY
                                              import ETMEZ (yalnızca stdlib
                                              datetime.timedelta — 15.19'da
                                              doğrulanmıştır)

Sonuç: annualized_metrics.py, metrics.py VE market_data/timeframes.py'ye
bağımlıdır (tek yönlü); hiçbiri annualized_metrics.py'den BAĞIMSIZDIR —
hiçbir döngü YOK. annualized_metrics.py hiçbir rolling/candidate/optimizer
kodu İMPORT ETMEZ.
```

### 15.30 Reddedilen Mimari Alternatifler (Özet)

```
- "Stage-3" adı altında birleştirme — REDDEDİLDİ (15.20).
- metrics.py'ye ekleme — REDDEDİLDİ, locked upstream docstring ihlali
  olurdu (15.20).
- Tek bir bundled AnnualizedMetrics dataclass'ı — REDDEDİLDİ, all-or-
  nothing failure modu + gereksiz katman (15.21).
- Explicit periods_per_year: Decimal input'u — REDDEDİLDİ, DRY ihlali +
  mixing riski AZALTMAZ (15.22).
- Equity-curve timestamp'lerinden veya result/config metadata'dan
  annualization inference — REDDEDİLDİ, Bölüm 15.14'ün ZATEN LOCKED
  no-inference ilkesini ihlal ederdi (15.22).
- Elapsed wall-clock time'a dayalı CAGR — REDDEDİLDİ, mekanik olarak
  geçerli bir initial-timestamp kaynağı YOK (15.25).
- Dedicated immutable annualization value object — REDDEDİLDİ, gereksiz
  katman, calendar basis zaten TEK bir global sabit (15.21, 15.22).
- Downside-deviation'da yalnızca-downside-count payda — REDDEDİLDİ,
  standart olmayan, küçük-örneklem instabilitesi (15.24).
- Calmar'da sıfır-drawdown için Infinity döndürme — REDDEDİLDİ, finite-
  only output felsefesini ihlal ederdi (15.26).
- 365.25 gün veya 252 trading-day calendar basis — REDDEDİLDİ, kripto-
  piyasa kapsamına uygunsuz (15.22).
- Probabilistic Sharpe Ratio'nun bu mikro-adımda tasarlanması —
  REDDEDİLDİ, seçilen annualized Sharpe formülü tarafından
  GEREKTİRİLMEZ (15.23).
```

### 15.31 Gelecekteki İmplementasyon İçin Dosya Kapsamı (Planlama Bilgisi — Şimdi Değiştirilmez)

```
Yeni production dosyası: src/crypto_quant_lab/validation/annualized_metrics.py
  (compute_annualized_sharpe_ratio, compute_sortino_ratio, compute_cagr,
  compute_calmar_ratio, + private _periods_per_year/_timedelta_to_microseconds/
  context-factory helper'ları).
Yeni test dosyası: tests/test_validation_annualized_metrics.py (mevcut
  test_validation_metrics.py/test_validation_candidate.py ile AYNI,
  tek-modül-per-test-dosyası convention'ı).
Değiştirilecek mevcut production dosyası: YOK (metrics.py, rolling.py,
  windows.py, candidate.py, models.py, market_data/timeframes.py
  DOKUNULMAZ).
Documentation (combined closure için): VALIDATION_SPEC.md.
Açıkça YASAK: ROADMAP.md, BACKTEST_SPEC.md, pyproject.toml, herhangi
  bir __init__.py, herhangi bir başka production/test/spec/status
  dosyası.
```

### 15.32 Test Kontratı (Gelecekteki Mikro-Adım İçin Minimum Matris)

```
- Value/API: her dört fonksiyonun exact signature'ı (pozisyonel result,
  keyword-only timeframe/rate/target), bare-Decimal dönüş tipi, hiçbir
  yeni dataclass/None dönüşü, package-root export YOKLUĞU.
- Timeframe/calendar: "1h"/"4h" için exact periods_per_year (8760/2190),
  desteklenmeyen timeframe reddi (candle_duration'ın ValueError'ı
  propagate), non-str timeframe reddi (TypeError), float'a hiçbir
  dönüşüm YOKLUĞU (kod incelemesi + davranışsal), 365-gün sabitinin
  configurable OLMADIĞININ kanıtı.
- Annualized Sharpe: pozitif/sıfır/negatif oran, exact risk-free
  konvansiyonu (per-period, Stage-2 ile AYNI), Stage-2'nin sıfır-stdev
  reddinin DEĞİŞMEDEN propagate edildiği, exact operation sırası
  (sharpe_ratio * sqrt(periods_per_year)), non-terminating Decimal
  aritmetiği, non-finite çıktı reddi.
- Sortino: pozitif/negatif numerator, karışık upside/downside gözlemler,
  exact MAR konvansiyonu, exact downside-denominator konvansiyonu
  (n, population-style), downside gözlem YOKLUĞU case'i, sıfır downside
  deviation reddi, boundary equality (return == MAR), n<2 reddi,
  compute_stage2_metrics'in ÇAĞRILMADIĞININ (yalnızca compute_periodic_returns'ün
  çağrıldığının) statik/davranışsal kanıtı.
- CAGR: flat/growth/decline/total-loss case'leri, exact n + periods_per_year
  duration-basis'i (elapsed timestamp KULLANILMADIĞININ kanıtı), n=1
  davranışı, final_equity==initial_cash (cagr==0 exact), final_equity==0
  (cagr==-1 exact), negatif final_equity (ValueError, negatif base
  fractional power), overflow/underflow non-finite reddi, total_return'ün
  compute_stage1_metrics'ten REUSE edildiğinin (bağımsız final/initial
  bölmesi YAPILMADIĞININ) statik/davranışsal kanıtı.
- Calmar: pozitif/negatif CAGR, sıfır max_drawdown reddi, max_drawdown==1,
  max_drawdown>1, undefined-CAGR'ın DEĞİŞMEDEN propagate edildiğinin
  kanıtı, max_drawdown'ın compute_stage1_metrics'ten REUSE edildiğinin
  (ikinci bir drawdown algoritması YOKLUĞUNUN) statik/davranışsal kanıtı.
- Decimal determinism: düşük/yüksek ambient precision, farklı ambient
  rounding mode, ambient-context mutasyonu sonrası tekrarlı çağrılar,
  kilitli 28-basamak davranışı, non-finite computed-output reddi (her
  dört fonksiyon için ayrı ayrı).
- Compatibility: Stage-1/Stage-2/rolling/windows/candidate API'lerinin
  DEĞİŞMEDEN kaldığının statik kanıtı (git diff boş), gerçek canonical
  backtest entegrasyonu (en az bir gerçek run_backtest_from_store/
  run_rolling_backtest_from_store sonucu üzerinde), bağımsız
  WindowResult.result kullanımı, cross-window aggregation YOKLUĞU,
  rolling/candidate import coupling YOKLUĞU, persistence/reporting
  davranışı YOKLUĞU.

Testler WALL CLOCK/randomness/network/external service/order dependence/
mutable global fixture/float expected value KULLANMAZ; tam production
algoritmasını bir oracle olarak yeniden implement ETMEZ.
```

### 15.33 Explicit Exclusions (Bu Kontrat Kapsamında DEĞİL, İmplement EDİLMEZ)

```
Deflated Sharpe, Probabilistic Sharpe Ratio (annualized Sharpe formülü
tarafından gerektirilmediği için), PBO, CPCV, purging/embargo, multiple-
testing corrections, parameter stability, candidate selection/ranking,
optimizer/grid/random/Bayesian search, cross-window metric aggregation,
trial aggregation, final untouched holdout enforcement, reporting/
persistence/serialization/dashboard/CLI output, non-metrics backtest
davranışı.
```

Bu maddeler **deferred boundary'ler** olarak kaydedilir — implement edilmiş özellikler DEĞİL.

**Status: LOCKED AND IMPLEMENTED + TESTED** (bu combined delivery; bkz. Bölüm 23, 28.H — 30/30).

## 16. Return Series Semantics — Bölüm 15.9–15.18'de LOCKED; Annualization Bölüm 15.19–15.33'te AYRICA LOCKED

Sharpe-ailesi metrikler bir return series gerektirir. `EquityPoint`, her candle availability'sinde bir örnek sağlar. MS1 zamanında bu bölümde açık bırakılan beş soru:

```
- simple vs. log return
- periyodiklik (1h/4h candle-by-candle mi, yoksa resample mi)
- risk-free rate varsayımı
- 1h/4h için annualization faktörü
- sıfır/negatif equity handling
```

**Durum güncellemesi (FAZ6B — Return-Series + Per-Observation Sharpe, LOCKED VE IMPLEMENTED + TESTED, commit `e4cedf9`):** dördü artık Bölüm 15.9–15.18'de LOCKED'dır VE implement edilmiştir:

```
- simple vs. log return           -> simple return LOCKED (Bölüm 15.13)
- periyodiklik                    -> candle-by-candle, per-observation,
                                      resample YOK, annualize edilmez
                                      (Bölüm 15.14)
- risk-free rate varsayımı        -> explicit risk_free_per_period,
                                      default Decimal("0") (Bölüm 15.11,
                                      15.16)
- sıfır/negatif equity handling   -> mevcut equity legal, ama non-pozitif
                                      PAYDA fail-fast (Bölüm 15.13, 15.15)
```

**Beşinci soru artık AYRICA çözülmüştür:** `1h/4h için annualization faktörü` — exact calendar basis (365 gün, sabit), exact `periods_per_year` türetimi (1h -> 8760, 4h -> 2190), ve annualized Sharpe/Sortino/CAGR/Calmar formülleri artık Bölüm 15.19–15.33'te **LOCKED**'dır VE bu combined delivery ile **IMPLEMENTED + TESTED**'dır (`src/crypto_quant_lab/validation/annualized_metrics.py`, bkz. Bölüm 23, 28.H — 30/30).

## 17. İleri Seviye Validation Tekniklerinin Bağımlılık Haritası (LOCKED — Hiçbiri Sessizce Taşınmaz)

`BACKTEST_SPEC.md` Bölüm 26/27/34'ün Faz 6'ya atadığı her madde burada **Faz 6 kapsamında** kalır; yalnızca dependency-position (NOW / LATER IN FAZ 6) belirlenir.

### 17.1 Purging / Embargo — Window-Level Embargo Foundation LOCKED VE IMPLEMENTED + TESTED (FAZ6C MS1 kontrat + combined delivery); Label/Outcome-Horizon Purging DEFERRED

**MS1 zamanındaki durum (tarihsel bulgu, hâlâ doğru):** Classical purged K-fold / embargo, bir **information/label/outcome horizon** tanımına ihtiyaç duyar (bir gözlemin "etkisinin" ne kadar sürdüğü). Mevcut `BacktestPolicy` abstraction'ının **hiçbir explicit label/outcome horizon kavramı yoktur** (`target_position(context) -> PositionTarget`, salt candle-prefix tabanlıdır) — bu bulgu FAZ6C MS1 preflight'i tarafından (aşağıda, 17.1.1) tekrar doğrulanmıştır ve hâlâ geçerlidir: **klasik, label/outcome-horizon'a bağlı purging hâlâ implement EDİLEMEZ** (bkz. 17.1.2, 17.1.13) — bu tür bir horizon kavramı icat edilmeden.

**Bu nedenle (FAZ6C MS1 çözümü — LOCKED):** label/outcome-horizon'a bağlı klasik purging **Faz 6 İÇİNDE deferred** kalır (17.1.13) — ama repository, klasik purge/embargo'nun **label-horizon gerektirmeyen yarısını** zaten destekler: bir out-of-sample (OOS) penceresiyle doğrudan overlap eden herhangi bir in-sample (IS) penceresinin reddi (Bölüm 7'nin zaten LOCKED "IS/OOS overlap edemez" invariant'ının N-pencereli genellemesi) VE OOS'tan SONRA gelen, caller tarafından explicit olarak sağlanan sabit bir sürenin (`embargo: timedelta`) IS penceresi olarak kullanılmasının reddi — hiçbir per-observation label/outcome horizon'a İHTİYAÇ DUYMADAN. Bu iki mekanizma (aşağıda 17.1.2'de "purge" ve "embargo" olarak isimlendirilir ve KESİN OLARAK AYRILIR) Bölüm 17.1.1–17.1.13'te **LOCKED**'dır VE artık **İMPLEMENT EDİLMİŞ + TEST EDİLMİŞTİR**: `src/crypto_quant_lab/validation/purging.py` (YENİ modül), kendi regression suite'i `tests/test_validation_purging.py`'de (62 test, tümü PASS); bkz. Bölüm 22, 23, §28.I — 19/19.

**17.1.1 Source-Preflight Bulguları (FAZ6C MS1)**

```
- windows.py: TemporalWindow (frozen/slots, start/end datetime, half-open
  [start, end), genuine-aware-datetime zorunlu — datetime_to_epoch_us ile
  doğrulanır, sessiz clip/normalize YOK) ve TemporalSplit (in_sample:
  TemporalWindow, out_of_sample: TemporalWindow, timeframe: str; tek
  invariant `in_sample.end <= out_of_sample.start` — overlap'i yasaklar,
  adjacency/gap'i legal bırakır) — ikisi de pure/store-free, sıfır
  cross-dependency (yalnızca data_quality/time.is_grid_aligned +
  storage/sqlite_codec.datetime_to_epoch_us import eder). Bölüm 6/7'nin
  zaten LOCKED "half-open, [start, end), sessiz clip/sort/normalize YOK"
  disiplini burada da REUSE edilir — yeni bir interval primitive'i İCAT
  EDİLMEZ.
- `TemporalSplit.__post_init__`'in overlap check'i ("in_sample.end >
  out_of_sample.start ise ValueError") YALNIZCA tek-IS/tek-OOS,
  IS-önce-OOS varsayımı altında çalışan, DIREKTİF (tek yönlü) bir
  kontroldür — bidirectional/genel bir "iki keyfi pencere overlap ediyor
  mu" predicate'i DEĞİLDİR ve bu şekilde (N-pencereli, iki yönlü)
  reuse EDİLEMEZ; bu nedenle FAZ6C MS1, kendi genel `windows_overlap`
  predicate'ini (17.1.5) yeni bir primitive olarak kilitler —
  `TemporalSplit`'in kendi check'i DEĞİŞMEDEN kalır, bu yeni predicate
  onu DEĞİŞTİRMEZ/reimplement ETMEZ.
- rolling.py: `run_rolling_backtest_from_store`/
  `run_context_aware_rolling_backtest_from_store`, bağımsız bir
  `tuple[TemporalWindow, ...]` (veya `ContextAwareWindow`) üzerinde
  çalışır; duplicate/overlapping evaluation pencereleri EXPLICIT olarak
  legal'dir, hiçbir purge/overlap-rejection uygulanmaz (rolling.py'nin
  kendi docstring'i: "compose, never duplicate" — `TemporalSplit`
  rolling tarafından hiç tüketilmez, Bölüm 8.3.12). Bu, rolling.py'nin
  KENDİSİNİN hiçbir purge/embargo kavramı TAŞIMADIĞINI doğrudan kaynaktan
  doğrular — yeni purging primitive'i rolling.py'a COUPLE EDİLMEZ (bkz.
  17.1.9).
- `BacktestPolicy.target_position(context: PolicyContext) ->
  PositionTarget` (backtest/policy.py, Faz-4-locked, DEĞİŞMEDEN):
  hiçbir label/outcome/holding-period/horizon field'ı TAŞIMAZ; yalnızca
  `context.as_of_time` + `context.candles` (salt candle-prefix) görür.
  Repo genelinde "horizon"/"label"/"outcome" grep'i: hiçbir yerde
  (funding/, backtest/, validation/) önceden LOCKED bir per-observation
  label/outcome-horizon kavramı YOKTUR — bu, 17.1'in tarihsel bulgusunun
  (yukarıda) doğrudan tekrar doğrulanmasıdır.
- `candle_duration(timeframe) -> timedelta` (market_data/timeframes.py,
  DEĞİŞMEDEN, yalnızca "1h"/"4h"), context/lookback süresi (Bölüm 8.3,
  caller-supplied `context_start`), ve evaluation süresi (`TemporalWindow`
  kendi `end - start`'ı) — ÜÇÜ DE, bir "embargo duration" ile
  REPOSITORY EVIDENCE'A GÖRE EŞDEĞER DEĞİLDİR: hiçbiri "bu OOS
  penceresinden SONRA ne kadar süre boyunca IS verisi hâlâ kontamine
  sayılır" sorusuna bir cevap TAŞIMAZ/İMA ETMEZ. Bu nedenle embargo
  duration, hiçbirinden İNFERENCE EDİLMEZ — kendi bağımsız, explicit
  `embargo: timedelta` parametresi olarak kilitlenir (17.1.4) — Annualized
  Metrics'in `timeframe: str`'i hiçbir yerden infer ETMEME kararıyla
  (Bölüm 15.22) AYNI prensip.
- `Candidate`/`Trial` (candidate.py, DEĞİŞMEDEN): `Trial.timeframe`
  provenance field'ı taşır ama (Bölüm 15.19'un annualized metrics için
  zaten kaydettiği AYNI gerekçeyle) hiçbir embargo/horizon kavramına
  COUPLE EDİLMEZ; yeni purging modülü `candidate.py`'yi hiç import ETMEZ.
- Repo genelinde "purg"/"embargo"/"horizon" grep'i (src/ ve tests/):
  hiçbir üretim kodunda veya kilitli spec bölümünde önceden LOCKED bir
  purge/embargo mekanizması veya isim çakışması YOKTUR — yalnızca bu
  bölümün (17.1) kendi "LATER IN FAZ 6" tarihsel notu ve §18.9'un
  "bu foundation purging/embargo SAĞLAMAZ" açık-sınır cümlesi mevcuttur;
  ikisi de bu MS1'in kilidiyle ÇAKIŞMAZ (§18.9 DEĞİŞMEDEN doğru kalır —
  candidate/trial foundation'ının KENDİSİ hâlâ purging/embargo
  SAĞLAMAZ; yeni modül candidate/trial'dan tamamen bağımsızdır).
```

**Sonuç:** repository, bir per-observation label/outcome horizon kavramını **desteklemez ve icat edilmesi bu mikro-adımın kapsamı DIŞINDADIR** — bu nedenle klasik (label-horizon'a bağlı) purging hâlâ implement EDİLEMEZ. Ama repository, `TemporalWindow`/`TemporalSplit` üzerinden **pencere-seviyeli** bir overlap-invariant'ı ZATEN LOCKED tutar (Bölüm 6/7) — bu, label-horizon'a İHTİYAÇ DUYMADAN, N-pencereli bir purge + explicit-duration bir embargo'yu doğru ve dürüst şekilde genellemeye YETERLİDİR. Aşağıdaki 17.1.2–17.1.13, TAM OLARAK bu genellemeyi kilitler — daha fazlasını İDDİA ETMEZ.

**17.1.2 Kapsam ve Terminoloji (LOCKED) — Purge vs. Embargo, Kesin Ayrım**

```
Kapsam İÇİNDE (bu mikro-adım, LOCKED):
  1. "Purge" — bir IS penceresinin, OOS penceresiyle DOĞRUDAN zaman
     overlap'i nedeniyle reddi (Bölüm 7'nin zaten LOCKED tek-IS/tek-OOS
     overlap yasağının, keyfi sayıda IS penceresine genellemesi —
     YENİ bir kavram İCAT EDİLMEZ, yalnızca YENİDEN KULLANILABİLİR bir
     predicate olarak isimlendirilir).
  2. "Embargo" — bir IS penceresinin, OOS penceresinin BİTİMİNDEN
     hemen SONRA gelen, caller tarafından explicit olarak sağlanan
     sabit bir `embargo: timedelta` süresiyle overlap'i nedeniyle reddi.
     Bu süre hiçbir yerden İNFERENCE EDİLMEZ (17.1.4).

Kapsam DIŞINDA (bu mikro-adımda TASARLANMAZ/İMPLEMENT EDİLMEZ, gelecekteki
ayrı bir kontrata ertelenir — bkz. 17.1.13):
  - Label/outcome-horizon'a bağlı KLASİK purging (bir training
    gözleminin KENDİ ileriye-dönük outcome/label penceresinin OOS ile
    overlap'i) — bu, per-observation bir horizon kavramı GEREKTİRİR ve
    repository'de YOKTUR (17.1.1).
  - OOS'tan ÖNCE gelen bir "pre-embargo"/lookback-tarafı buffer — bu
    zaten Bölüm 7/8.3'ün context/lookback ve IS/OOS non-overlap
    kurallarıyla kapsanır; yeni bir kavram İCAT EDİLMEZ.
  - Çoklu-fold / K-fold / CPCV orkestrasyon (Bölüm 17.2) — "fold model"
    prerequisite'i (17.2) HENÜZ MEVCUT DEĞİLDİR; bu mikro-adım yalnızca
    TEK bir OOS penceresine karşı saf bir filtering primitive'i kilitler.
  - Candidate/trial seçimi, ranking, optimizer/grid/random/Bayesian
    search, final holdout enforcement, cross-candidate aggregation.
  - Deflated Sharpe, PBO, multiple-testing corrections, parameter
    stability (Bölüm 17.4–17.7).
  - Herhangi bir store/rolling/candidate orchestration entegrasyonu —
    bu mikro-adım yalnızca pure, bağımsız bir filtering primitive'i
    kilitler (17.1.9).
```

Bu ayrım LOCKED'dır: "purge" ve "embargo" bu dokümanda **iki farklı, bağımsız reddediş nedenidir** — ikisi de aynı `purge_in_sample_windows` fonksiyonu (17.1.7) tarafından uygulanır, ama farklı zaman aralıklarına (OOS'un kendisi vs. OOS'tan sonraki explicit-duration bölge) karşı test edilirler.

**17.1.3 Exact Public API (LOCKED)**

Kilitlenen exact production şekli (implementasyon henüz yapılmaz — bu yalnızca kontrat):

```python
# Modül: src/crypto_quant_lab/validation/purging.py (YENİ modül)

from datetime import datetime, timedelta

from crypto_quant_lab.validation.windows import TemporalWindow


def windows_overlap(first: TemporalWindow, second: TemporalWindow) -> bool: ...


def embargo_boundary(out_of_sample: TemporalWindow, *, embargo: timedelta) -> datetime: ...


def purge_in_sample_windows(
    in_sample_windows: tuple[TemporalWindow, ...],
    *,
    out_of_sample: TemporalWindow,
    embargo: timedelta = timedelta(0),
) -> tuple[TemporalWindow, ...]: ...
```

```
- Üç BAĞIMSIZ, küçük public fonksiyon — Annualized Metrics'in (Bölüm
  15.21) "tek bir bundled dataclass DEĞİL, bağımsız fonksiyonlar"
  precedent'iyle AYNI desen. Reddedilen alternatif: tek bir
  `PurgeResult`/`EmbargoPlan` bundling dataclass'ı (17.1.10).
- Parametre isimleri Bölüm 7'nin ZATEN LOCKED terminolojisiyle
  (`in_sample`/`out_of_sample`) birebir tutarlıdır — "training"/
  "evaluation" gibi YENİ, rakip bir isimlendirme İCAT EDİLMEZ.
- Hiçbiri yeni bir dataclass/value object döndürmez: `windows_overlap`
  bare `bool`, `embargo_boundary` bare `datetime`, `purge_in_sample_windows`
  bare `tuple[TemporalWindow, ...]` döner (Annualized Metrics'in bare-
  Decimal precedent'iyle AYNI "gerekmedikçe yeni value object İCAT ETME"
  ilkesi).
- `validation/__init__.py` DEĞİŞMEDEN kalır — bu üç fonksiyon
  package-root'ta export EDİLMEZ (windows.py/rolling.py/metrics.py/
  candidate.py/annualized_metrics.py ile AYNI zero-re-export
  convention'ı).
- `TemporalWindow`/`TemporalSplit`/`WindowResult`/`Candidate`/`Trial`
  ve mevcut Stage-1/Stage-2/Annualized-Metrics public fonksiyonlarının
  imzaları DEĞİŞMEDEN kalır — hiçbirine yeni bir field EKLENMEZ.
```

**17.1.4 Horizon/Embargo Girdisi — Explicit, Asla Inference (LOCKED)**

```
Karşılaştırılan alternatifler:
1. Embargo süresini candle_duration(timeframe)'dan türetmek — REDDEDİLDİ:
   17.1.1'in kanıtladığı gibi candle duration ile embargo duration
   arasında hiçbir repository-evidence eşdeğerliği YOKTUR; bu, "Do not
   treat candle duration... and embargo duration as interchangeable
   unless repository evidence proves that equivalence" talimatının
   doğrudan uygulamasıdır.
2. Embargo süresini context/lookback süresinden (Bölüm 8.3) türetmek —
   REDDEDİLDİ: context, OOS'tan ÖNCEki bir pencereyi tanımlar; embargo
   OOS'tan SONRAki bir bölgeyi tanımlar — zıt yönlü kavramlardır,
   birbirinden türetilemezler.
3. Embargo süresini `Trial.timeframe` veya candidate provenance'tan
   türetmek — REDDEDİLDİ: Bölüm 15.19/18.9'un zaten kilitlediği
   "annualized metrics/purging, Candidate/Trial'a COUPLE EDİLMEZ"
   ilkesiyle ÇAKIŞIRDI.
4. **SEÇİLDİ — explicit, zorunlu olmayan (default `timedelta(0)`),
   keyword-only `embargo: timedelta` parametresi.** Caller, embargo
   süresini KENDİSİ sağlar; hiçbir silent inference YOKTUR. Default
   `timedelta(0)`, embargo bölgesini boş kümeye (hiçbir pencere
   overlap edemez) degenere eder — bu, "yalnızca purge, embargo yok"
   durumunu AYRI bir kod yolu OLMADAN temsil eder (Bölüm 8.3.1'in
   `context_start == evaluation_start` sıfır-context legal-default
   precedent'iyle AYNI desen).
```

```
- `embargo`, negatif OLAMAZ (`embargo >= timedelta(0)` zorunlu) —
  embargo kavramsal olarak yalnızca OOS'tan SONRAYA doğru genişler,
  asla geriye değil; negatif bir embargo, OOS'un KENDİSİYLE overlap
  eden bir bölge tanımlar ki bu zaten "purge"ın (17.1.2 madde 1)
  kapsamındadır — ayrı, çelişkili bir anlam taşımaması için negatif
  değer reddedilir.
- `embargo`, bir `timedelta` olmalıdır — `int`/`float`/Decimal/başka
  bir tip KABUL EDİLMEZ (candle_duration'ın kendi dönüş tipiyle,
  `_timedelta_to_microseconds`'ın girdi tipiyle AYNI stdlib primitive).
- Hiçbir "calendar basis" veya "periods_per_year" kavramı bu modüle
  SIZMAZ — Annualized Metrics'in 365-gün sabiti burada KULLANILMAZ/
  REFERANS EDİLMEZ (bu modül tamamen timeframe-agnostic'tir,
  `TemporalWindow`'ın kendi "Timeframe-agnostic by design" docstring'iyle
  tutarlı).
```

**17.1.5 `windows_overlap` — Exact Overlap Predicate (LOCKED)**

```python
def windows_overlap(first: TemporalWindow, second: TemporalWindow) -> bool:
    return first.start < second.end and second.start < first.end
```

```
- Half-open `[start, end)` overlap testi — Bölüm 6'nın ZATEN LOCKED
  half-open convention'ıyla birebir tutarlı; dokunma (`a.end ==
  b.start`) overlap SAYILMAZ (Bölüm 7'nin "adjacency legal" kuralıyla
  AYNI okuma).
- Simetriktir: `windows_overlap(a, b) == windows_overlap(b, a)` —
  formülün cebirsel yapısından (iki conjunct'ün sırası anlamı
  DEĞİŞTİRMEZ) doğrudan doğrulanabilir.
- Girdi sırasından/hangisinin "IS" hangisinin "OOS" olduğundan
  BAĞIMSIZDIR — genel, iki-taraflı bir predicate'tir; `TemporalSplit`'in
  KENDİ tek-yönlü (`in_sample.end > out_of_sample.start`) check'ini
  DEĞİŞTİRMEZ/reimplement ETMEZ (17.1.1).
- Yalnızca `<`/`>` datetime karşılaştırması — float/Decimal aritmetiği
  YOK, hiçbir arithmetic fault/non-finite durumu MÜMKÜN DEĞİLDİR
  (datetime karşılaştırması toplam sıradadır).
- Validation: `first`/`second`, her ikisi de `TemporalWindow` olmalıdır;
  değilse TypeError (17.1.8) — her ikisi de zaten kendi
  `__post_init__`'inde genuine-aware-datetime + start<end garantisi
  ALMIŞTIR (trust-the-lower-layer, Bölüm 18.7 precedent'i) — bu fonksiyon
  datetime awareness'ı YENİDEN doğrulamaz.
```

**17.1.6 `embargo_boundary` — Exact Formül (LOCKED)**

```python
def embargo_boundary(out_of_sample: TemporalWindow, *, embargo: timedelta) -> datetime:
    return out_of_sample.end + embargo
```

```
- Embargo bölgesinin ÜST sınırı (exclusive, half-open convention ile
  tutarlı): `[out_of_sample.end, embargo_boundary)`.
- `embargo == timedelta(0)` -> `embargo_boundary == out_of_sample.end`
  -> bölge boş küme (`[X, X)`) -> hiçbir pencere ile overlap EDEMEZ
  (17.1.4'ün "sıfır embargo = yalnızca purge" degenerasyonu).
- Aşırı büyük bir `embargo` (`out_of_sample.end + embargo`,
  `datetime.max`'ı aşarsa) -> Python stdlib'in KENDİ deterministik
  `OverflowError`'ı (mesaj DEĞİŞMEDEN propagate edilir) — yeni bir
  overflow-check İCAT EDİLMEZ (empirik olarak doğrulanmıştır: canlı,
  salt-okunur bir probe, `datetime.max` + `timedelta(days=1)` için
  `OverflowError: date value out of range` üretti).
- Validation: `out_of_sample` bir `TemporalWindow` olmalıdır; değilse
  TypeError. `embargo` bir `timedelta` olmalıdır; değilse TypeError.
  `embargo >= timedelta(0)` olmalıdır; değilse ValueError (17.1.8).
- Float/Decimal aritmetiği YOK — yalnızca stdlib `datetime + timedelta`.
```

**17.1.7 `purge_in_sample_windows` — Exact Algoritma ve Operation Sırası (LOCKED)**

```python
purged: list[TemporalWindow] = []
for in_sample in in_sample_windows:
    reject = windows_overlap(in_sample, out_of_sample)
    if not reject and embargo > timedelta(0):
        embargo_zone = TemporalWindow(
            start=out_of_sample.end,
            end=embargo_boundary(out_of_sample, embargo=embargo),
        )
        reject = windows_overlap(in_sample, embargo_zone)
    if not reject:
        purged.append(in_sample)
return tuple(purged)
```

```
- Exact operation sırası: ÖNCE doğrudan OOS-overlap testi (purge,
  17.1.2 madde 1), YALNIZCA reddedilmediyse VE `embargo > timedelta(0)`
  ise embargo-bölge testi (embargo, 17.1.2 madde 2). `embargo ==
  timedelta(0)` iken embargo-bölge testi HİÇ ÇALIŞTIRILMAZ — bir
  `TemporalWindow(start=X, end=X)` inşa etmeye teşebbüs etmek
  `start < end` invariant'ını (Bölüm 6) ihlal ederdi; bu nedenle boş
  embargo bölgesi bir `TemporalWindow` instance'ı olarak hiç İNŞA
  EDİLMEZ (yalnızca `embargo > timedelta(0)` iken, ki bu durumda
  `embargo_boundary > out_of_sample.end` MATEMATİKSEL OLARAK garantilidir
  ve `TemporalWindow` construction'ı GÜVENLİDİR).
- Girdi sırası KORUNUR: `purged`, `in_sample_windows`'ın orijinal
  sırasıyla, yalnızca reddedilmeyenler filtrelenerek inşa edilir —
  hiçbir sort/reindex/re-order YOKTUR (Bölüm 18.7/rolling.py'nin
  "input order preserved" precedent'iyle AYNI ilke).
- Duplicate pencereler DEDUPE EDİLMEZ: `in_sample_windows` içinde
  aynı pencere birden fazla kez geçiyorsa VE reddedilmiyorsa, HER
  KOPYASI ayrı ayrı korunur (rolling.py'nin "duplicate/overlapping
  windows are legal" precedent'iyle AYNI desen, Bölüm 8.3.6 alanı).
- Boş `in_sample_windows` (`()`) -> boş `()` döner, hiçbir hata YOK
  (legal, trivial temel durum).
- `in_sample_windows`'ın TÜMÜ reddedilirse -> boş `()` döner (legal;
  "tüm IS pencereleri purge edildi" bir hata durumu DEĞİLDİR — bu
  fonksiyon yalnızca FİLTRELER, bir minimum-kalan-pencere-sayısı
  invariant'ı ENFORCE ETMEZ; bu, gelecekteki bir CPCV/orchestration
  katmanının kendi sorumluluğu olabilir, 17.1.13).
- Bir `in_sample` penceresinin `out_of_sample`'dan KRONOLOJİK OLARAK
  SONRA gelmesi (örn. ters sıralı bir kullanım) MEKANİK OLARAK
  ENGELLENMEZ — bu fonksiyon yalnızca overlap/embargo-bölge testi
  uygular, IS'in OOS'tan önce gelmesi gerektiğine dair (TemporalSplit'in
  KENDİ, ayrı invariant'ı OLAN) bir kronoloji kısıtlaması İCAT ETMEZ
  (Reddedilen alternatif, 17.1.10).
```

**17.1.8 Validation / Fail-Fast Sırası (LOCKED, Exact)**

**`windows_overlap(first, second)`:**

```
1. `first` bir `TemporalWindow` olmalıdır; değilse TypeError.
2. `second` bir `TemporalWindow` olmalıdır; değilse TypeError.
3. `first.start < second.end and second.start < first.end` hesaplanır
   ve döndürülür.
```

**`embargo_boundary(out_of_sample, *, embargo)`:**

```
1. `out_of_sample` bir `TemporalWindow` olmalıdır; değilse TypeError.
2. `embargo` bir `timedelta` olmalıdır; değilse TypeError.
3. `embargo >= timedelta(0)` olmalıdır; değilse ValueError.
4. `out_of_sample.end + embargo` hesaplanır ve döndürülür (aşırı büyük
   `embargo` -> Python'ın kendi `OverflowError`'ı, DEĞİŞMEDEN propagate).
```

**`purge_in_sample_windows(in_sample_windows, *, out_of_sample, embargo=timedelta(0))`:**

```
1. `out_of_sample` bir `TemporalWindow` olmalıdır; değilse TypeError.
2. `embargo` bir `timedelta` olmalıdır; değilse TypeError.
3. `embargo >= timedelta(0)` olmalıdır; değilse ValueError.
4. `in_sample_windows` bir `tuple` olmalıdır; değilse TypeError.
5. `in_sample_windows`'ın HER elemanı bir `TemporalWindow` olmalıdır;
   değilse index-specific TypeError — bu GLOBAL bir geçiştir: TÜM
   elemanlar adım 5'i geçmeden hiçbir eleman için adım 6 çalışmaz
   (Candidate'in düzeltilmiş global-fail-fast sırası precedent'i,
   Bölüm 18.8/18.14 — "adım N tüm girişler için tamamlanmadan adım
   N+1 hiçbir girişi incelemez").
6. Yalnızca (1)-(5) TAMAMEN başarılı olduktan SONRA: 17.1.7'nin exact
   algoritması, `in_sample_windows` sırasıyla çalıştırılır.
7. Filtrelenmiş `tuple[TemporalWindow, ...]` döndürülür.
```

**Global not:** hiçbir adım, geçersiz bir `embargo`/`in_sample_windows`/`out_of_sample` girdisini sessizce normalize/coerce/sort/dedupe ETMEZ; hiçbir kısmi/parçalı sonuç döndürülmez.

**17.1.9 Purity, Determinism, ve Import Direction (LOCKED)**

```
Üç fonksiyonun HER BİRİ:
- Aynı geçerli input için deterministiktir.
- Input'u (TemporalWindow instance'ları, tuple) MUTATE ETMEZ (zaten
  frozen/slots/immutable).
- `TemporalWindow`, `TemporalSplit`, `BacktestResult`, `WindowResult`,
  `Candidate`, `Trial`'a hiçbir field EKLEMEZ/DEĞİŞTİRMEZ.
- Sonuçları persist/serialize ETMEZ.
- Wallclock time, randomness KULLANMAZ.
- I/O yapmaz; hiçbir store'a query atmaz; replay/backtest engine'i
  ÇAĞIRMAZ.
- Rolling orchestration'ı (`run_rolling_backtest_from_store`,
  `run_context_aware_rolling_backtest_from_store`) İMPORT ETMEZ/
  ÇAĞIRMAZ.
- `Candidate`/`Trial`'ı İMPORT ETMEZ; candidate seçmez/sıralamaz/
  skorlamaz.
- Cross-window/cross-fold aggregation yapmaz; CPCV orkestrasyonu İÇERMEZ
  (Bölüm 17.2 hâlâ ayrı, deferred).
- Yalnızca standard-library Python (`datetime`, `timedelta`) ve
  `crypto_quant_lab.validation.windows.TemporalWindow` kullanır.
```

**Import direction (LOCKED):**

```
crypto_quant_lab.validation.purging  (YENİ modül)
  imports:
    crypto_quant_lab.validation.windows   (TemporalWindow — PUBLIC tip;
                                             hiçbir `_` prefix'li private
                                             helper İMPORT EDİLMEZ)
    datetime (stdlib)

crypto_quant_lab.validation.windows     <- purging.py'yi İMPORT ETMEZ
crypto_quant_lab.validation.rolling     <- purging.py'yi İMPORT ETMEZ
crypto_quant_lab.validation.metrics     <- purging.py'yi İMPORT ETMEZ
crypto_quant_lab.validation.candidate   <- purging.py'yi İMPORT ETMEZ
crypto_quant_lab.validation.annualized_metrics <- purging.py'yi İMPORT ETMEZ

Sonuç: purging.py, yalnızca windows.py'ye bağımlıdır (tek yönlü);
hiçbiri purging.py'den BAĞIMSIZDIR — hiçbir döngü YOK. purging.py
hiçbir rolling/candidate/metrics/optimizer kodu İMPORT ETMEZ.
```

**17.1.10 Reddedilen Mimari Alternatifler (Özet)**

```
- Label/outcome-horizon'a bağlı klasik purging'in bu mikro-adımda
  tasarlanması — REDDEDİLDİ: repository'de böyle bir horizon kavramı
  YOK (17.1.1); icat etmek "do not fabricate" talimatını ihlal ederdi.
- Embargo süresini candle_duration/context-lookback/Trial.timeframe'den
  türetmek — REDDEDİLDİ, hiçbiriyle repository-evidence eşdeğerliği YOK
  (17.1.4).
- Tek bir bundled `PurgeResult`/`EmbargoPlan` dataclass'ı (purged +
  kept + reddediş nedeni) — REDDEDİLDİ: Annualized Metrics'in "bare
  dönüş tipi, gerekmedikçe yeni value object İCAT ETME" precedent'iyle
  tutarlı bir minimalizm tercih edildi; zengin bir introspection
  objesi, gelecekteki bir implementasyon adımında GEREKÇELENDİRİLİRSE
  eklenebilir, ama bu mikro-adımda SPEKÜLATİF olurdu.
- `in_sample_windows`'ın `out_of_sample`'dan kronolojik olarak ÖNCE
  gelmesini mekanik olarak enforce eden bir invariant — REDDEDİLDİ:
  `TemporalSplit` zaten TEK bir IS/OOS çifti için bunu kendi kapsamında
  enforce eder; bu YENİ, N-pencereli fonksiyon daha genel bir filtreleme
  primitive'idir ve rolling.py'nin "keyfi pencere düzenlemeleri legal"
  precedent'iyle tutarlı kalması için ekstra bir kronoloji kısıtlaması
  İCAT ETMEZ.
- OOS'tan ÖNCE bir "pre-embargo" buffer'ı kilitlemek — REDDEDİLDİ: bu
  zaten Bölüm 7/8.3'ün context/lookback ve non-overlap kurallarıyla
  kapsanır; ikinci, örtüşen bir kavram İCAT EDİLMEZ (17.1.2).
- `windows_overlap`'i `TemporalSplit.__post_init__`'in KENDİ check'ini
  reimplement etmek/onu bu yeni predicate ile DEĞİŞTİRMEK için
  kullanmak — REDDEDİLDİ: bu mikro-adım `windows.py`'yi DEĞİŞTİRMEZ
  (yalnızca `VALIDATION_SPEC.md` yetkilidir); `TemporalSplit`'in kendi
  tek-yönlü check'i DEĞİŞMEDEN kalır.
```

**17.1.11 Gelecekteki İmplementasyon İçin Dosya Kapsamı (Planlama Bilgisi — Şimdi Değiştirilmez)**

```
Yeni production dosyası: src/crypto_quant_lab/validation/purging.py
  (windows_overlap, embargo_boundary, purge_in_sample_windows).
Yeni test dosyası: tests/test_validation_purging.py (mevcut
  test_validation_windows.py/test_validation_annualized_metrics.py ile
  AYNI, tek-modül-per-test-dosyası convention'ı).
Değiştirilecek mevcut production dosyası: YOK (windows.py, rolling.py,
  metrics.py, candidate.py, annualized_metrics.py, models.py DOKUNULMAZ).
Documentation (combined closure için): VALIDATION_SPEC.md.
Açıkça YASAK: ROADMAP.md, pyproject.toml, herhangi bir __init__.py,
  herhangi bir başka production/test/spec/status dosyası.
```

**17.1.12 Test Kontratı (Gelecekteki Mikro-Adım İçin Minimum Matris)**

```
- Value/API: üç fonksiyonun exact signature'ı (pozisyonel/keyword-only
  ayrımı), bare dönüş tipleri (bool/datetime/tuple), hiçbir yeni
  dataclass/None dönüşü, package-root export YOKLUĞU.
- `windows_overlap`: touching (adjacency, overlap DEĞİL), tam overlap,
  kısmi overlap (her iki yönde), tamamen ayrık, biri diğerini tamamen
  içeriyor, simetri (`overlap(a,b) == overlap(b,a)`), yanlış tip TypeError.
- `embargo_boundary`: exact `out_of_sample.end + embargo` sonucu, sıfır
  embargo -> `out_of_sample.end`'e exact eşit, negatif embargo ValueError,
  yanlış tip TypeError (hem `out_of_sample` hem `embargo` için), aşırı
  büyük embargo -> `OverflowError` (mesaj kontrolü).
- `purge_in_sample_windows`: yalnızca-purge (embargo=0) case'leri
  (overlap eden/etmeyen IS pencereleri), embargo>0 ile ek reddediş
  (embargo bölgesine düşen IS penceresi), touching-ama-overlap-olmayan
  IS penceresi kabul, boş `in_sample_windows` -> boş sonuç, tüm
  pencereler purge edilir -> boş sonuç, duplicate IS pencereleri
  (dedupe edilmediğinin kanıtı), input sırasının korunduğunun kanıtı,
  negatif/yanlış-tip `embargo` reddi, yanlış-tip `in_sample_windows`/
  eleman reddi (index-specific, global-pass sırası kanıtı — bir sonraki
  eleman tipi geçerliyken önceki elemanın tipi geçersizse hâlâ TypeError
  fırlatıldığının kanıtı), no-mutation (input tuple/pencereler `is`
  identity ile aynı kalır), determinism (tekrarlı çağrı aynı sonuç),
  `windows.py`/`rolling.py`/`candidate.py`/`metrics.py`/
  `annualized_metrics.py` API'lerinin DEĞİŞMEDEN kaldığının statik kanıtı
  (`git diff` boş) + tam regression suite uyumluluğu, import-direction
  statik kanıtı (purging.py'nin rolling/candidate/metrics import
  ETMEDİĞİ).

Testler WALL CLOCK/randomness/network/external service/order dependence/
float expected value KULLANMAZ.
```

**17.1.13 Explicit Exclusions / Deferred Scope (Bu Kontrat Kapsamında DEĞİL, İmplement EDİLMEZ)**

```
Label/outcome-horizon'a bağlı klasik purging (per-observation forward-
looking outcome window overlap'i — bir horizon kavramı icat edilmeden
implement EDİLEMEZ), CPCV (Bölüm 17.2 — "fold model" prerequisite'i
hâlâ yok), Deflated Sharpe, PBO, multiple-testing corrections, parameter
stability (17.4–17.7), candidate selection/ranking, optimizer/grid/
random/Bayesian search, final untouched holdout enforcement, cross-
candidate/cross-window aggregation, reporting/persistence/serialization/
dashboard/CLI output, non-purging backtest davranışı.
```

Bu maddeler **deferred boundary'ler** olarak kaydedilir — implement edilmiş özellikler DEĞİL. Bu bölümün (17.1) LOCKED olması, FAZ6C'nin veya Faz 6'nın tamamlandığı anlamına GELMEZ (bkz. Bölüm 22, 22.2, 28.I).

### 17.2 CPCV — LATER IN FAZ 6

Prerequisites: fold model + observation/outcome-horizon contract (17.1) + purge/embargo semantics (17.1) + repeated candidate evaluation (18) + deterministic performance matrix (17.4). Window-level purge/embargo'nun exact kontratı Bölüm 17.1.1–17.1.13'te **LOCKED VE artık İMPLEMENT EDİLMİŞ + TEST EDİLMİŞTİR** (bkz. §28.I — 19/19) — ama bir "fold model" (birden fazla IS/OOS fold'unu bir arada üreten/orkestre eden bir mekanizma) VE label/outcome-horizon'a bağlı klasik purging (17.1.13) HÂLÂ MEVCUT DEĞİLDİR. CPCV, bu kalan prerequisite'ler karşılanana kadar implement edilmez. **Durum güncellemesi:** hizalı gözlem x deneme performans matrisi önkoşulu artık `TrialReturnMatrix` ile karşılanmıştır (§17.5.1–17.5.12, §28.L — 22/22); fold modeli ve label/outcome-horizon purging hâlâ YOKTUR.

### 17.3 Sharpe-Ailesi Metrikler — Aşama 2 (Bölüm 15/16)

**Non-annualized, per-observation Sharpe'ın kontratı Bölüm 15.9–15.18'de LOCKED'dır VE artık IMPLEMENTED + TESTED'dır** (`compute_stage2_metrics`, commit `e4cedf9`, bkz. Bölüm 23, 28.E — 29/29) — return-series prerequisite'i (Bölüm 16) bu kilitle karşılanmıştır. Annualized Sharpe'ın exact kontratı Bölüm 15.19–15.33'te **LOCKED**'dır VE artık bu combined delivery ile **IMPLEMENTED + TESTED**'dır (bkz. Bölüm 23, 28.H — 30/30) — non-annualized Stage-2 Sharpe'ı DOĞRUDAN reuse eder (annualization_factor ile çarparak), ikinci bir Sharpe tanımı İCAT ETMEZ. Sortino ve CAGR/Calmar'ın exact kontratları da AYNI şekilde artık Bölüm 15.24/15.25–15.26'da **LOCKED** VE **IMPLEMENTED + TESTED**'dır. Deflated Sharpe (17.4), PBO (17.5), multiple-testing corrections (17.6), ve parameter stability (17.7) bu implementasyondan **etkilenmez**, implement EDİLMEMİŞTİR, ve **LATER IN FAZ 6** (FAZ6C) olarak deferred kalır — hiçbiri bu delivery'de tamamlanmış olarak işaretlenmez; "Annualized Metrics" (Sharpe/Sortino/CAGR/Calmar) ile "Stage-3" (Deflated Sharpe/PBO/multiple-testing/parameter stability) İKİ AYRI GRUPTUR (bkz. Bölüm 15.20). Basit total-return/max-drawdown'dan **sonra**, ama foundation'ın (Bölüm 13) parçası değil.

### 17.4 Deflated Sharpe — Exact Contract LOCKED (§17.4.1–17.4.17) VE IMPLEMENTED + TESTED (§28.K — 27/27)

Prerequisites: tanımlı Sharpe istatistiği (17.3) + candidate/trial history (18) + (efektif) trial sayısı + gerekli dağılımsal girdiler. Bölüm 18'in candidate/trial foundation'ı artık IMPLEMENTED + TESTED'dır (28.G — 25/25), ama yalnızca TEK bir candidate'in TEK bir trial'ını value object olarak temsil eder — çoklu-trial history/registry/trial-count tracking bu foundation'ın DIŞINDADIR (§18.9, 18.13) ve henüz mevcut değildir. Standalone bir formül olarak, deneysel/trial framework'ünden **kopuk** implement edilmez.

**Durum güncellemesi (FAZ6C — DSR bağımlılık çözümü, docs-only):** "candidate/trial history" ve "trial sayısı"nın **ham** kısmı için gereken en küçük foundation — tek bir karşılaştırılabilir deneme grubunu temsil eden `TrialGroup` ve `recorded_trial_count` (`src/crypto_quant_lab/validation/trial_group.py`) — Bölüm 20.1–20.13'te **LOCKED**'dır VE artık **IMPLEMENTED + TESTED**'dır (§20.14, §28.J — 19/19). Bu güncelleme yazıldığında Deflated Sharpe'ın KENDİSİ henüz spec-lock edilmemişti ve §20.12'deki maddeler açıktı (tarihsel); bu maddeler artık §17.4.2'de karara bağlanmış ve DSR exact kontratı §17.4.1–17.4.15'te LOCKED'dır (implementasyon yok, §28.K — 0/27). Kaydedilmiş sayı, verilen gruptaki kabul edilmiş Trial kayıtlarının tam sayısıdır — efektif/bağımsız deneme sayısı, benzersiz strateji sayısı veya gerçek araştırma deneme yüküne göre koşulsuz bir alt/üst sınır DEĞİLDİR (§20.7).

**Durum güncellemesi (FAZ6C — Deflated Sharpe source-preflight + exact contract lock, docs-only):** Deflated Sharpe'ın exact kontratı aşağıdaki Bölüm 17.4.1–17.4.15'te **LOCKED**'dır — kilit zamanında (commit `65273d7`) henüz implement edilmemişti (§28.K — 0/27, tarihsel); kontrat §17.4.1a'daki kaynak/nümerik düzeltmelerle birlikte artık **IMPLEMENTED + TESTED**'dır (§17.4.17, §28.K — 27/27). §20.12'deki on açık madde bu kontratla karara bağlanmıştır (§17.4.2); ikisi bilinçli olarak deferred kalır (efektif-N estimator'ı ve çok pencereli pooling — §17.4.14). Bu kilit, CPCV/PBO/multiple-testing/parameter stability'yi başlatmaz ve FAZ6C'yi tamamlamaz.

**17.4.1 Source-Preflight Bulguları ve Kaynaklar**

```
Birincil kaynak (bu preflight'te ERİŞİLDİ):
  Bailey, D. H. ve López de Prado, M. (2014). "The Deflated Sharpe
  Ratio: Correcting for Selection Bias, Backtest Overfitting and
  Non-Normality." Journal of Portfolio Management 40(5), 94-107.
  SSRN 2460551. Yazarların PDF'i (davidhbailey.com/dhbpapers/
  deflated-sharpe.pdf, 22 sayfa) indirildi; metin akışları stdlib ile
  çıkarıldı. Denklem gövdeleri PDF'te gömülü font/görsel olduğundan
  METİN OLARAK çıkarılamadı — aşağıdaki formül biçimi, metin + yazarların
  kod listesi + makalenin sayısal sonuçlarının bağımsız olarak yeniden
  üretilmesiyle doğrulanmıştır (aşağıda).

Metin olarak doğrulanan hükümler:
  - Eq. (1) / Ek A.1 Eq. (5)-(6): N BAĞIMSIZ deneme sonrası beklenen
    maksimum Sharpe, Euler-Mascheroni sabiti (~0.5772) ve standart
    normal ters-CDF ile yaklaşıklanır; yaklaşıklık "large N" içindir.
    Yazarların Snippet 1 kodu (metin olarak okunabilir):
      maxZ = (1-emc)*norm.ppf(1-1./numTrials) + emc*norm.ppf(1-1./(numTrials*e))
      return mu + sigma*maxZ
  - Eq. (2): DSR, eşiği çoklu denemeye göre ayarlanmış bir PSR'dir;
    girdiler: seçilen stratejinin tahmini SR'si, örneklem uzunluğu T,
    getirilerin skewness'i ve kurtosis'i, denenmiş SR'lerin varyansı ve
    "N is the number of independent trials". Z standart normal CDF'dir.
  - Ek A.3: "the N used ... corresponds to the number of independent
    trials"; M bağımlı denemede M yerine N kullanılmalı; ortalama
    korelasyon interpolasyonu (Eq. 7-9) önerilir, AMA yazarlar bu
    tahminin kısa örneklemde kötü koşullu/overfit olabileceğini ve
    entropi temelli alternatifleri açıkça not eder.
  - Ek A.2 / Exhibit 3.1: N < 50 iken analitik beklenen-maksimum
    sayısal sonucu çok küçük bir farkla (varyans 1 için < 0.05) AŞIRI
    tahmin edebilir; N büyüdükçe fark sıfıra yakınsar.
  - Sayısal örnek: T=1250 günlük gözlem (metin), yıllık 250 gözlem
    ile "non-annualized" SR kullanımı (metin), sonuç "only a 90%
    chance" (metin), N=46'da DSR 0.9505 (metin), normal getirilerde
    eşik N=88 (metin).

Bağımsız sayısal doğrulama (preflight, scratchpad, float stdlib —
production DEĞİL):
  Örnek girdileri (N=100, yıllık SR=2.5 -> SR=2.5/sqrt(250), yıllık
  V=1/2 -> V=0.5/250, T=1250, skewness=-3, kurtosis=10) PDF'te görsel
  olduğundan METİNDEN OKUNAMADI; bu değerler hatırlanan örnek
  girdileridir ve aşağıdaki DÖRT metinsel sonucu birlikte yeniden
  ürettikleri için DOĞRULANMIŞ sayılır:
    formül: Z[(SR - SR0) * sqrt(T-1) / sqrt(1 - g3*SR + (g4-1)/4*SR^2)]
    N=100 -> 0.9004 ("90%"); N=46 -> 0.9505 (metin: 0.9505);
    normal (g3=0, g4=3): N=88 -> 0.95049 >= 0.95, N=89 -> 0.94984.
  Konvansiyon ayrımı: sqrt(T) varyantı N=46'da 0.9506, excess-kurtosis
  varyantı N=46'da 0.9495 verir — yalnızca sqrt(T-1) + HAM (normal=3)
  kurtosis kombinasyonu makalenin 0.9505 değerini üretir. Ayrıca
  (g4-1)/4 terimi normal dağılımda (g4=3) 1 + SR^2/2 verir; bu,
  iid-normal SR tahmincisinin bilinen asimptotik varyansıyla tutarlıdır.

ERİŞİLEMEYEN kaynaklar (erişilmiş gibi davranılmaz):
  - Bailey & López de Prado (2012a) PSR / "The Sharpe Ratio Efficient
    Frontier" ve Mertens (2002) — okunmadı. Payda biçimi yukarıdaki
    bağımsız yeniden üretimle doğrulanmıştır; bu kaynakların skewness/
    kurtosis tahmin konvansiyonunu (population vs. bias-corrected)
    belirleyip belirlemediği DOĞRULANMADI -> §17.4.2 karar 6'da bu
    nedenle açıkça bir konvansiyon SEÇİLİR ve sınırı kaydedilir.
  - Marsaglia, G. (2004). "Evaluating the Normal Distribution." Journal
    of Statistical Software 11(4) — bu turda okunmadı; §17.4.8'deki
    seri, standart normal CDF'nin bilinen Taylor açılımıdır ve doğruluğu
    kaynak iddiasına değil §17.4.12'deki bağımsız referans testlerine
    dayandırılır.

Repository kaynak bulguları (kod, DEĞİŞMEDEN):
  - metrics.compute_stage2_metrics(result, *, risk_free_per_period):
    per-observation Sharpe = (mean - rf) / sample-stdev(n-1), private
    context prec=28, TEK bir BacktestResult üzerinde; en az 2 return;
    stdev > 0 şartı.
  - metrics.compute_periodic_returns(result): N equity noktası -> N
    simple return (ilk return initial_cash'e göre), prec=28.
  - trial_group.TrialGroup: benzersiz candidate_id, homojen provenance
    ve BİREBİR aynı ordered evaluation pencere dizisi; recorded_trial_
    count = len(trials). Trial.results çok pencereli olabilir;
    duplicate/overlapping pencereler legaldir; her pencere taze sermaye
    ile başlar (Bölüm 11).
  - annualized_metrics: Sharpe_annual = Stage-2 Sharpe *
    sqrt(periods_per_year); 365-gün takvim tabanı.
  - Bölüm 15.16 / 27: float'a dönüşüm, statistics modülü (float ile),
    NumPy/pandas/SciPy YASAK; pyproject runtime dependency YOK.
  - Repo'da skewness/kurtosis, normal CDF/ters-CDF, Euler-Mascheroni
    sabiti YOK (grep: skew/kurtos/NormalDist/erf( — eşleşme yok).
```

**17.4.1a Kaynak ve Doğrulama Düzeltmeleri (implementation delivery ile eklendi — §17.4.1'in bazı ifadelerini AÇIKLIĞA KAVUŞTURUR; §17.4.1 tarihsel kayıt olarak korunur)**

```
1. Render ile doğrulama: Yazarların PDF'i (davidhbailey.com/dhbpapers/
   deflated-sharpe.pdf; 22 sayfa; PDF CreationDate 2014-08-07) bu kez
   sayfa sayfa RENDER edilerek (PyMuPDF, scratchpad; proje bağımlılığı
   DEĞİL) okundu. Eq. (1) (basılı s. 7), Eq. (2) ve SR0 tanımı (s. 8),
   sayısal örnek girdileri (s. 9) ve yerine koyma (s. 10) GÖRSEL olarak
   doğrulandı; §17.4.1'deki "denklem gövdeleri metin olarak çıkarılamadı,
   örnek girdileri hatırlanan değerlerdir" kaydı artık geçerli değildir:
   s. 9 "N = 100, V[{SR_n}] = 1/2, T=1250, γ3 = -3 and γ4 = 10";
   s. 10 SR0 = sqrt(1/(2*250)) * ((1-γ)Z^-1[1-1/100] + γZ^-1[1-e^-1/100])
   ≈ 0.1132 (non-annualized, 250 gözlem/yıl) ve DSR ≈ Z[(2.5/sqrt(250)
   - 0.1132) sqrt(1249) / sqrt(1 - (-3) 2.5/sqrt(250) + (10-1)/4
   (2.5/sqrt(250))^2)] = 0.9004 < 0.95.
2. Skewness işareti: Bu PDF sürümünde HEM s. 9 girdisi HEM s. 10
   yerine koyması γ3 = -3'tür; s. 9'da +3 GÖZLENMEDİ. Başka bir sürümde
   (örn. dergi baskısı) +3 yazıyor olabilir — bu tur DOĞRULANAMADI.
   Her durumda 0.9004 sonucu γ3 = -3 ile üretilir (+3 ile 0.9004
   ÜRETİLMEZ); örnek girdileri Eq. (2)'nin genel formülünün parçası
   DEĞİLDİR, yalnızca bir yerine koyma örneğidir.
3. Sayısal örneğin float kontrolü (standart float, yalnızca kontrol —
   yüksek hassasiyet oracle'ı DEĞİL): N=100, γ3=-3, γ4=10: 0.900396834449;
   N=46: 0.950501706876; N=88, γ3=0, γ4=3: 0.950490816676 (4 ondalıkta
   0.9505, makalenin yazdığı değer). §17.4.1'in "N=88 -> 0.95049" ifadesi
   doğrudur; kilit commit mesajındaki "0.9504/0.9505 at N=46" ifadesi
   HATALIDIR (N=46 değeri 0.9505'tir; 0.95049 N=88'e aittir) — commit
   geçmişi değiştirilmez, düzeltme burada kaydedilir.
4. Ham kurtosis ile moment tahmincisi AYRI gerekçelendirilir:
   (a) HAM kurtosis (normal = 3) — DOĞRUDAN kaynak kanıtı: s. 10
       "Normal returns (γ3 = 0, γ4 = 3)" ve Eq. (2)'deki (γ4 - 1)/4.
   (b) Ham getirilerden moment TAHMİNİNDE population payda (T) — makale
       örneği hazır γ3/γ4 değerleri kullanır; bu, paydanın T olduğunu
       KANITLAMAZ ve doğrudan kaynak kanıtı YOKTUR. Bu, projenin AÇIK
       estimator tercihidir. Gerekçesi: population momentler örneklemin
       deneysel dağılımının momentleridir; Pearson eşitsizliği
       (γ4 >= γ3^2 + 1) onlar için TAM olarak geçerlidir ve bu,
       1 - γ3 SR + (γ4-1)/4 SR^2 ikinci derece ifadesinin diskriminantını
       <= 0 yapar -> payda terimi HER SR için >= 0 (§17.4.9).
       Bias-corrected G1/G2 tahmincileri bu garantiyi VERMEZ.
5. Doğruluk kanıtı: float referansıyla 2.2e-16 uyum (önceki preflight)
   28 basamak doğruluğun kanıtı DEĞİLDİR. Doğrulama artık algoritmadan
   bağımsız arbitrary-precision bir referansla yapılır (mpmath 1.3.0,
   dps=120-130; ncdf erfc tabanlı, ters-CDF sqrt(2)*erfinv(2p-1);
   araştırma ortamında mevcut, proje bağımlılığı DEĞİL) — sonuçlar
   §17.4.8 ve §17.4.12'de.
6. Newton iterasyon sayısı: örneklenmiş N'lerde gözlenen en fazla 75
   iterasyon DENEYSEL bir bulgudur, [2, 10^30] domain'inin tamamı için
   İSPAT DEĞİLDİR (§17.4.8'de ayrıştırıldı).
7. N ve V aynı evreni temsil etmelidir: Eq. (1)'in türetimi, N
   bağımsız denemenin SR'lerinin ORTAK bir dağılımdan (aynı "strateji
   sınıfı") geldiğini ve V'nin o dağılımın varyansı olduğunu varsayar.
   Gruptan tahmin edilen V ile keyfî büyüklükte bir araştırma programı
   N'ini birleştirmek KOŞULSUZ geçerli DEĞİLDİR; yalnızca caller, grubun
   V'sinin N'in kapsadığı ilgili deneme evrenini temsil ettiğini
   varsaydığında anlamlıdır (§17.4.2 karar 2, düzeltildi).
8. Caller'ın N'i bir VARSAYIMDIR: eksik geçmişi tamamlamaz; başarısız/
   iptal edilmiş her çalıştırma OTOMATİK olarak bağımsız bir istatistiksel
   deneme SAYILMAZ (§17.4.2 karar 3, düzeltildi).
Bu düzeltmeler API'yi, algoritmayı, validation sırasını veya §28.K
kriter sayısını (27) DEĞİŞTİRMEZ; §28.K kriter 18-20'nin doğrulama
yöntemi daha güçlü bağımsız referanslarla yeniden ifade edilmiştir.
```

**17.4.2 On Açık Kararın Çözümü (LOCKED)**

```
1. N'in anlamı
   Kanıt: Eq. (2) ve Ek A.3 — N BAĞIMSIZ deneme sayısıdır; M bağımlı
   denemede M kullanmak eşiği şişirir. recorded_trial_count (§20.7)
   yalnızca kabul edilmiş kayıt sayısıdır.
   KARAR: N, caller tarafından AÇIKÇA verilen zorunlu bir keyword
   girdisidir: `independent_trial_count: int`. recorded_trial_count
   N'e varsayılan/yedek olarak ASLA kullanılmaz; ikisi arasında
   mekanik bir sınır (N <= M veya N >= M) ENFORCE EDİLMEZ — bağımlılık
   N'i M'nin altına, kaydedilmemiş denemeler üstüne taşıyabilir.
   Sınır: DSR sonucu beyan edilen N'e KOŞULLUDUR; N'in doğruluğu
   mekanik olarak doğrulanamaz (makalenin kendisi de N'i "disclose"
   edilen bir girdi olarak ele alır). Ek bağımlılık: yok.

2. N'in kapsamı
   Kanıt: V[SR_n] aynı örneklem üzerinde hesaplanmış SR'ler gerektirir;
   TrialGroup bunu tek partition için mekanik olarak garanti eder.
   KARAR: V ve seçilen denemenin istatistikleri YALNIZCA verilen TEK
   TrialGroup'tan hesaplanır. N ise araştırma programı kapsamında
   (diğer gruplar, coin'ler, kaydedilmemiş denemeler dahil) caller
   tarafından belirlenir. Gruplar-arası tekrarların ayıklanması ve
   program-düzeyi sayım bu API'nin DIŞINDADIR (registry YOK).
   Sınır (düzeltildi, §17.4.1a madde 7): Eq. (1), N denemenin SR'lerinin
   ortak bir dağılımdan geldiğini ve V'nin o dağılımın varyansı olduğunu
   varsayar. Gruptan tahmin edilen V ile keyfî büyüklükte bir program
   N'ini birleştirmek KOŞULSUZ geçerli DEĞİLDİR; caller, V'nin N'in
   kapsadığı ilgili deneme/strateji evrenini temsil ettiğini ayrıca
   gerekçelendirmelidir. API bunu doğrulayamaz.

3. Başarısız / iptal / kaydedilmemiş denemeler
   KARAR: Hiçbir yeni failure nesnesi yok. Bu denemeler V'ye KATKI
   VERMEZ (gözlenmiş bir SR yok). Bağımsız deneme sayılıp sayılmayacakları
   yalnızca caller'ın beyan ettiği N'e yansır — ve bu bir varsayımdır:
   başarısız/iptal edilmiş her çalıştırma otomatik olarak bağımsız bir
   istatistiksel deneme SAYILMAZ; N eksik geçmişi tamamlamaz
   (§17.4.1a madde 8). Sınır: metrik hesaplaması
   sırasında hata veren (örn. equity <= 0) bir deneme gruba giremez; bu
   V'yi aşağı yönlü yanlı kılabilir — mekanik tespit YOK.

4. Çok pencereli Trial, tek SR ve T
   Kanıt: DSR tek bir getiri örnekleminin SR'sini, T'sini ve momentlerini
   kullanır. Repo'da çok pencereli pooling kontratı yoktur; pencereler
   overlap/duplicate olabilir (T şişer), boşluk içerebilir ve her pencere
   sermayeyi sıfırlar; pencere-başı Sharpe'ları ortalamak makalede
   tanımlı değildir.
   KARAR: Bu kontratta her Trial TAM OLARAK BİR WindowResult içermelidir
   (len(trial.results) == 1); aksi ValueError. SR = o pencerenin Stage-2
   Sharpe'ı; T = compute_periodic_returns(...) uzunluğu. Tüm Trial'ların
   equity_curve uzunluğu eşit olmalıdır (aynı pencere -> aynı T).
   Gerekçe: DSR, seçimin yapıldığı TEK bir örneklem (tipik olarak
   tek, bitişik IS penceresi) üzerindeki seçim yanlılığını düzeltir.
   Çok pencereli pooling DEFERRED (§17.4.14).

5. Ölçek
   Kanıt: Makale örneği non-annualized SR, V ve T'yi aynı gözlem
   ölçeğinde kullanır. DSR bir olasılıktır; ölçek tutarlıysa ölçekten
   bağımsızdır.
   KARAR: Tüm hesap PER-OBSERVATION ölçektedir (Stage-2 Sharpe, aynı
   ölçekte V, gözlem sayısı T). Annualized metrikler DSR'de
   KULLANILMAZ; periods_per_year/timeframe girdisi YOKTUR.

6. Skewness / kurtosis
   KARAR: Seçilen denemenin compute_periodic_returns çıktısı üzerinden
   POPULATION moment tahmincileri (payda T): m_k = sum((r-mean)^k)/T,
   skewness = m3 / (m2 * sqrt(m2)), kurtosis = m4 / (m2 * m2) — HAM
   kurtosis (normal = 3), excess DEĞİL. Bias-corrected (G1/G2)
   tahminciler KULLANILMAZ. Gerekçe AYRIDIR (§17.4.1a madde 4): ham
   kurtosis doğrudan kaynağa dayanır (s. 10, Eq. 2); population payda
   ise doğrudan kaynağa DAYANMAYAN, projenin açık estimator tercihidir
   (Pearson eşitsizliği ile payda teriminin >= 0 kalmasını garanti
   eder). Sınır: SR'nin kendisi n-1 paydalı sample stdev kullanır
   (Stage-2, DEĞİŞMEZ). m2 > 0 değilse ValueError.

7. Normal CDF / ters-CDF
   Kanıt: Bölüm 15.16/27 float ve statistics modülünü yasaklar;
   bu kuralı değiştirmek kullanıcı kararı olurdu -> değiştirilmez.
   KARAR: Private, Decimal-only implementasyon: Φ için standart normal
   CDF'nin Taylor serisi, Φ^-1 için x0=0'dan Newton iterasyonu
   (§17.4.8). Doğruluk bağımsız referanslarla test edilir (§17.4.12).

8. Trial'lar arası Sharpe varyansı
   KARAR: Gruptaki TÜM Trial'ların (seçilen dahil) per-observation
   Stage-2 Sharpe'larının SAMPLE varyansı (payda M-1, M =
   recorded_trial_count). M >= 2 zorunlu; V > 0 zorunlu (V = 0 ise
   ValueError — beklenen-maksimum modelinin dejenere olduğu ve
   deflasyonun sessizce kaybolduğu durum). Makale varyans
   konvansiyonunu belirtmez; n-1 seçimi repo'nun Stage-2 konvansiyonu
   ile tutarlılık içindir.

9. Seçilen Trial
   KARAR: Zorunlu keyword `selected_candidate_id: str`; gruptaki bir
   Trial'ın candidate_id'si ile BİREBİR eşleşmelidir. DSR hiçbir seçim,
   sıralama veya "en yüksek SR" tespiti YAPMAZ; seçilenin maksimum
   olduğunu DOĞRULAMAZ.

10. IS / OOS / final holdout
   KARAR: API pencerelerin rolünü BİLEMEZ (role alanı yok, §18.7).
   DSR, seçimin yapıldığı örneklem üzerinde kullanılmak üzere
   tanımlanır; bunu doğrulamak caller disiplinidir. DSR sonucu final
   holdout koruması DEĞİLDİR, bir işlem/yatırım kararı DEĞİLDİR ve
   OOS sonuçlarının seçimde kullanılmasını MEŞRULAŞTIRMAZ.
   API'nin mekanik olarak doğrulayabildikleri: grup homojenliği
   (TrialGroup), tek pencere, eşit T, seçilenin grupta bulunması.
```

**17.4.3 Exact Public API (LOCKED)**

```python
# Modül: src/crypto_quant_lab/validation/deflated_sharpe.py (YENİ modül)


def compute_deflated_sharpe_ratio(
    group: TrialGroup,
    *,
    selected_candidate_id: str,
    independent_trial_count: int,
    risk_free_per_period: Decimal = Decimal(0),
) -> Decimal: ...
```

```
- Tek public sembol: compute_deflated_sharpe_ratio. Bare Decimal döner
  (Annualized Metrics precedent'i) — [0, 1] aralığında bir olasılık.
- Import edilen isimler private alias'larla alınır; modülün public
  sembol kümesi TAM OLARAK {compute_deflated_sharpe_ratio} olur.
- Package-root export YOK; validation/__init__.py DEĞİŞMEZ.
- Hiçbir mevcut modül (metrics.py, annualized_metrics.py, candidate.py,
  trial_group.py, rolling.py, windows.py, purging.py) DEĞİŞMEZ.
- Private yardımcılar (isimleri kilitli, test erişimi §17.4.12'de):
    _deflated_sharpe_from_statistics(*, sharpe_ratio, trial_sharpe_variance,
        independent_trial_count, sample_length, skewness, kurtosis) -> Decimal
    _normal_cdf(x: Decimal) -> Decimal
    _normal_quantile(p: Decimal) -> Decimal
```

**17.4.4 Veri Akışı (LOCKED)**

```
TrialGroup (mevcut, doğrulanmış)
  -> her Trial: tek WindowResult -> BacktestResult
  -> compute_stage2_metrics(result, risk_free_per_period=rf).sharpe_ratio
     (gruptaki HER Trial için, grup sırasıyla) -> SR_1..SR_M
  -> V = sample variance(SR_1..SR_M)
  -> seçilen Trial: SR_sel, returns = compute_periodic_returns(result),
     T = len(returns), skewness, kurtosis
  -> _deflated_sharpe_from_statistics(...) -> DSR
Hiçbir getiri/Sharpe/stdev algoritması yeniden implement EDİLMEZ;
Stage-2 ve periodic-returns DOĞRUDAN reuse edilir.
```

**17.4.5 Formüller ve Exact Operation Sırası (LOCKED)**

Tümü §17.4.7'deki private context içinde; `g` Euler-Mascheroni sabiti, `e = Decimal(1).exp()`.

```python
# Trial'lar arası varyans (M = recorded_trial_count(group))
mean_sr = sum(srs, Decimal(0)) / Decimal(M)
V = sum((sr - mean_sr) * (sr - mean_sr) for sr in srs) / Decimal(M - 1)

# Seçilen denemenin momentleri (T = len(returns))
mean_r = sum(returns, Decimal(0)) / Decimal(T)
m2 = sum((r - mean_r) ** 2 for r in returns) / Decimal(T)
m3 = sum((r - mean_r) ** 3 for r in returns) / Decimal(T)
m4 = sum((r - mean_r) ** 4 for r in returns) / Decimal(T)
skewness = m3 / (m2 * m2.sqrt())
kurtosis = m4 / (m2 * m2)

# Beklenen maksimum eşik (Eq. 1 / Snippet 1, mu = 0)
N = Decimal(independent_trial_count)
q1 = _normal_quantile(Decimal(1) - Decimal(1) / N)
q2 = _normal_quantile(Decimal(1) - Decimal(1) / (N * e))
sr0 = V.sqrt() * ((Decimal(1) - g) * q1 + g * q2)

# DSR (Eq. 2)
variance_term = Decimal(1) - skewness * sr + (kurtosis - Decimal(1)) / Decimal(4) * sr * sr
z = (sr - sr0) * Decimal(T - 1).sqrt() / variance_term.sqrt()
dsr = _normal_cdf(z)
```

```
- Toplamlar ve kuvvetler soldan sağa, gösterilen sırayla; ara rounding
  YOK (yalnızca context precision'ı). `** 2/3/4` tamsayı üsleridir.
- Beklenen-maksimum ifadesinde mu = 0 (makalenin sıfır-gerçek-SR null
  hipotezi); caller'a bir mu/threshold girdisi AÇILMAZ.
- Sonuç, §17.4.7'deki prec=28 context'te TEK BİR KEZ `plus()` ile
  28 anlamlı basamağa yuvarlanır.
```

**17.4.6 Validation / Fail-Fast Sırası ve Exact Mesajlar (LOCKED)**

```
1. group TrialGroup değil ->
   TypeError(f"group must be a TrialGroup, got {type(group).__name__}")
2. selected_candidate_id str değil ->
   TypeError(f"selected_candidate_id must be a str, got {type(v).__name__}")
3. independent_trial_count int değil veya bool ->
   TypeError(f"independent_trial_count must be an int, got {type(v).__name__}")
4. 2 <= independent_trial_count <= 10**30 değil ->
   ValueError(f"independent_trial_count must be between 2 and 10**30 inclusive, got {v}")
5. risk_free_per_period Decimal değil ->
   TypeError(f"risk_free_per_period must be a Decimal, got {type(v).__name__}")
   finite değil ->
   ValueError(f"risk_free_per_period must be finite, got {v}")
6. recorded_trial_count(group) < 2 ->
   ValueError(f"at least two trials are required to estimate trial Sharpe variance, got {m}")
7. selected_candidate_id grupta yok ->
   ValueError(f"selected_candidate_id {v!r} is not in the group")
8. GLOBAL geçiş, index artan: len(trial.results) != 1 ->
   ValueError(f"trials[{i}] must have exactly one window result for deflated Sharpe, got {k}")
9. GLOBAL geçiş, index 1'den artan: equity_curve uzunluğu trials[0]'dan
   farklı ->
   ValueError(f"trials[{i}] has {n_i} equity observations, trials[0] has {n_0}")
10. GLOBAL geçiş, index artan: compute_stage2_metrics(...) — alt katman
   hataları (tip/sonluluk/n>=2/stdev>0/pozitif payda) DEĞİŞMEDEN
   propagate edilir (sarmalama/yeniden yazma YOK).
11. V hesaplanır; finite değil veya V <= 0 ->
   ValueError(f"trial Sharpe ratio variance must be greater than zero, got {V}")
12. Seçilen denemenin momentleri; m2, skewness, kurtosis finite değil
   veya m2 <= 0 ->
   ValueError(f"computed {name} must be finite and valid, got {value}")
   (name: "m2" | "skewness" | "kurtosis")
13. variance_term finite değil veya <= 0 ->
   ValueError(f"deflated Sharpe variance term must be greater than zero, got {value}")
14. sr0, z finite değil ->
   ValueError(f"computed {name} must be finite, got {value}")
15. dsr = _normal_cdf(z); 28 basamağa yuvarlanır; döndürülür.
Hiçbir adım girdiyi sessizce düzeltmez, trial atlamaz, N'i veya V'yi
başka bir kaynaktan türetmez, kısmi sonuç döndürmez.
```

**17.4.7 Decimal Precision ve Context (LOCKED)**

```
- DSR hesap context'i: Bölüm 15.17 ile AYNI shape, yalnızca prec=80:
  Context(prec=80, rounding=ROUND_HALF_EVEN, Emin=-999999, Emax=999999,
          capitals=1, clamp=0, traps=[])
  — her çağrıda taze, private; localcontext(...) izolasyonu; caller'ın
  ambient context'i sonucu ETKİLEMEZ.
- Çıkış yuvarlaması: Bölüm 15.17'nin prec=28 context shape'i ile tek
  bir `plus()`.
- Sabitler (modül-seviyesi, immutable Decimal string'lerden):
  EULER_MASCHERONI = 0.57721566490153286060651209008240243104215933593992
  PI = 3.14159265358979323846264338327950288419716939937510582097494459
  (her ikisi de en az 50 doğru basamak; e = Decimal(1).exp() context
  içinde hesaplanır).
- Girdi SR'leri ve getiriler Stage-2/periodic-returns'ün kendi prec=28
  çıktılarıdır (DEĞİŞMEZ); DSR bunları yeniden hesaplamaz.
```

**17.4.8 Normal CDF ve Ters-CDF — Nümerik Yöntem, Yakınsama, Uç Değerler (LOCKED)**

```
_normal_cdf(x):
  - x >= 15  -> exact Decimal(1);  x <= -15 -> exact Decimal(0)
    (|x| >= 15'te gerçek kuyruk < 3.7e-51; mutlak hata sınırının altında).
  - Aksi halde: Φ(x) = 1/2 + φ(x) * S(x),
      φ(x) = exp(-x*x/2) / sqrt(2*PI),
      S(x) = x + x^3/3 + x^5/(3*5) + ... ; term_k = term_{k-1} * x^2 / (2k+1)
    Tüm terimler x ile aynı işaretlidir (seri içinde iptal yok).
    Durma: s + term == s (working precision'da) olduğunda; güvenlik
    sınırı 5000 terim -> aşılırsa ArithmeticError (alan içinde
    erişilemez; preflight'te |x| <= 15 için en fazla 368 terim).
  - Hedef (düzeltildi): AÇIK (-15, 15) aralığında mutlak hata <= 1e-60;
    |x| >= 15 clamp bölgesinde mutlak hata <= Φ(-15) < 3.68e-51 (önceki
    "[-15, 15] üzerinde <= 1e-60" ifadesi uç noktalarda YANLIŞTI).
    mpmath ölçümü (x adımı 0.007, açık aralık): en büyük mutlak hata
    6.6e-79. Göreli hata GARANTİ EDİLMEZ: alt kuyrukta Φ(x)'in kendisi
    küçüldükçe büyür (ölçülen: x=-10'da 9.8e-57, -12'de 6.2e-48,
    -14'te 2.0e-36, -14.9'da 1.5e-30; x <= -15'te sonuç exact 0).
    Kuyruk kaybının kaynağı 1/2 + φ(x)S(x) toplamındaki iptaldir
    (mutlak hata ~1e-79 korunur, göreli hata 1e-79/Φ(x) olur).
_normal_quantile(p):
  - Alan: 1/2 <= p < 1 (DSR yalnızca 1-1/N ve 1-1/(N e), N >= 2
    değerlerini ister); alan dışı -> ValueError (private invariant).
  - Newton: x_0 = 0; x_{k+1} = x_k + (p - Φ(x_k)) / φ(x_k).
    Φ, (0, ∞) üzerinde konkav olduğundan x_0 = 0'dan başlayan iterasyon
    köke MONOTON olarak yaklaşır (aşma yok).
  - Durma: |adım| <= 1e-45. Güvenlik sınırı 200 iterasyon -> aşılırsa
    ArithmeticError (fail-closed; yakınsamamış değer DÖNDÜRÜLMEZ).
  - KESİN (tam aritmetikte): x_0 = 0'dan monoton, sınırlı -> köke
    yakınsama. Durma ölçütünün domain içinde ULAŞILABİLİR olması: adım
    gürültüsü ~ (Φ mutlak hatası)/φ(x) <= ~1e-78 / 4e-30 < 1e-45
    (en uç kantil ~11.55).
  - DENEYSEL (ispat DEĞİL): 592 N değeri (2..200 arası seçili
    tamsayılar, 10^0.35'ten 10^30'a dekad başına 20 log noktası,
    10^30-1 ve 10^30) x {1-1/N, 1-1/(N e)} için en fazla 75 iterasyon.
    İterasyon sayısının tüm domain için 200'ün altında kaldığı
    İSPATLANMAMIŞTIR; sınır aşılırsa ArithmeticError.
  - x-hatası (mpmath erfinv referansına göre, aynı 592 x 2 ızgara):
    en büyük |x - x_ref| = 6.3e-50. p'nin kendisi 80 basamakta
    oluşturulur; 1 - p kuyruğunun göreli hatası ~1e-80/(1-p) olup x'e
    ~Δp/φ(x) (<= ~3e-51) olarak yansır.
  - N <= 10**30 sınırının gerekçesi: bu aralıkta kantil < 11.6 kalır
    (Φ'nin clamp sınırı 15'in altında) ve 1 - 1/(N e), prec=80'de 1'e
    yuvarlanmaz.
Global doğruluk hedefi: döndürülen DSR, §17.4.5 formülünün aynı Decimal
girdi istatistikleri üzerindeki tam değerinden en fazla 1e-27 mutlak
farklıdır (28 basamaklı çıkış yuvarlaması baskındır). Kanıt DÜZEYİ:
(i) test edilen durumlarda mpmath'e göre ölçülen en büyük hata 2.0e-29;
(ii) hata yayılımı |ΔDSR| <= φ(z)|Δz| + ε_Φ + ε_yuvarlama, Δz ≈
sqrt(T-1)/sqrt(variance_term) * sqrt(V) * Δq ile sınırlıdır — Δq ~1e-49
olduğundan olağan girdilerde ihmal edilebilir; variance_term -> 0 veya
astronomik T gibi KÖTÜ KOŞULLU girdiler için genel bir ispat
VERİLMEZ. Göreli doğruluk yalnızca alt kuyrukta Φ(z) >> 1e-50 iken
anlamlıdır.
```

**17.4.9 Tanımsız Durumlar ve Minimum Veri Gereksinimleri (LOCKED)**

```
Mekanik minimumlar (ENFORCE EDİLİR):
  - M = recorded_trial_count(group) >= 2 (V için)
  - N = independent_trial_count, 2 <= N <= 10**30
  - her Trial tek pencere; tüm Trial'larda eşit equity-gözlem sayısı
  - T >= 2 (Stage-2'nin kendi minimumu; sqrt(T-1) > 0)
  - her Trial'da Stage-2 return_stdev > 0
  - V > 0, m2 > 0, variance_term > 0
İstatistiksel yeterlilik eşiği (örn. "T >= 250") KİLİTLENMEZ — makale
asimptotik bir yaklaşım kullanır; kanıtlanmamış bir eşik İCAT EDİLMEZ.
Küçük T ve küçük N'de sonuç yaklaşık kalır (Ek A.2: N < 50'de beklenen
maksimum hafifçe yüksek tahmin edilir -> DSR muhafazakâr yönde).
variance_term <= 0 durumu ValueError'dır, clip EDİLMEZ. Düzeltme
(§17.4.1a madde 4): population momentler için Pearson eşitsizliği
(γ4 >= γ3^2 + 1) ikinci derece ifadeyi HER SR için >= 0 yapar; bu
nedenle gerçek bir örneklemden hesaplanan momentlerle <= 0 durumu
yalnızca dejenere iki-noktalı dağılımlarda (eşitlik sınırında)
yuvarlama yoluyla oluşabilir (sayısal aramada en küçük değer ~7.6e-8,
pozitif). Dal savunma amaçlı korunur ve private yardımcı üzerinden,
tutarsız istatistiklerle test edilir.
```

**17.4.10 Purity ve Import Direction (LOCKED)**

```
crypto_quant_lab.validation.deflated_sharpe (YENİ)
  imports: validation.metrics (compute_periodic_returns,
           compute_stage2_metrics), validation.trial_group (TrialGroup,
           recorded_trial_count), decimal (stdlib) — private alias'larla.
  Hiçbir mevcut modül deflated_sharpe.py'yi import ETMEZ; döngü YOK.
- Girdi mutasyonu YOK; wall-clock/randomness/I/O YOK; float/statistics/
  math modülü YOK; seçim/sıralama/persistence YOK.
- Aynı girdi -> aynı Decimal çıktı (ambient context'ten bağımsız).
```

**17.4.11 Dosya Kapsamı (Planlama — Şimdi Değiştirilmez)**

```
Yeni production: src/crypto_quant_lab/validation/deflated_sharpe.py
Yeni test:       tests/test_validation_deflated_sharpe.py
Dokümantasyon:   VALIDATION_SPEC.md (combined closure)
Değişmeyecek:    tüm mevcut production/test dosyaları, __init__.py,
                 ROADMAP.md, pyproject.toml, AGENTS.md, CLAUDE.md
```

**17.4.12 Doğrulama Yöntemi — Bağımsız Referanslar (LOCKED; implementation delivery ile güçlendirildi)**

```
Referans doğrulaması aynı algoritmanın ikinci bir kopyasına DAYANMAZ.
Birincil referans: mpmath 1.3.0 (arbitrary precision, dps=130; CDF erfc
tabanlı, ters-CDF sqrt(2)*erfinv(2p-1)) ile OFFLINE üretilmiş değerler,
testte Decimal string olarak saklanır (CDF 90, kantil 70, DSR 40
anlamlı basamak). mpmath proje/runtime bağımlılığı DEĞİLDİR; üretim
betiği scratchpad'dedir ve testler mpmath İMPORT ETMEZ.
1. Φ: 13 nokta (merkez, iki kuyruk, x=±14.5) mutlak hata <= 1e-75
   (ölçülen en büyük 6.6e-79; hedef 1e-60). Alt kuyruk göreli hata:
   x=-10'da <= 1e-50, x=-14.5'te <= 1e-28. Clamp: Φ(15)=Φ(15.000001)=1,
   Φ(-15)=Φ(-40)=0, Φ(±14.999999) clamp DIŞINDA.
2. Φ^-1: p in {0.975, 0.99} ve modülün N in {2, 100, 10^30} için
   kurduğu exact p değerleri (1-1/N, 1-1/(N e)) — x-hatası <= 1e-47
   (ölçülen en büyük 6.3e-50); p = 1/2 -> exact 0; alan dışı p
   ValueError.
3. DSR (istatistik düzeyi, private yardımcı): makale örneği N=100/46
   (γ3=-3, γ4=10) ve N=88/89 (γ3=0, γ4=3) mpmath değerleri, mutlak
   <= 1e-27; ayrıca makalenin basılı dört ondalıklı değerleri (0.9004,
   0.9505, 0.9505) ve N=88/89 eşiği.
4. DSR (grup düzeyi, public API): sentetik üç Trial'lık grup için, mevcut
   Stage-2 Sharpe ve periodic-returns çıktıları girdi alınarak mpmath ile
   hesaplanmış 12 referans (rf in {0, 0.0005}, seçilen in {alpha, gamma},
   N in {2, 5, 50}), mutlak <= 1e-27.
5. Tamamlayıcı (bağımsız ama düşük hassasiyetli): stdlib float
   NormalDist ile Φ ızgarası |fark| <= 1e-15; grup DSR float referansı
   |fark| <= 1e-9; moment konvansiyonu ayrımı (G1/G2 ve excess
   varyantları > 1e-6 farklı).
6. İç tutarlılık (tek başına yeterli SAYILMAZ): Φ(Φ^-1(p)) - p <= 1e-60,
   Φ(x) + Φ(-x) - 1 <= 1e-75.
7. Fail-closed: iterasyon/terim sınırı düşürülerek (monkeypatch)
   ArithmeticError mesajları doğrulanır.
Private yardımcıların (§17.4.3) doğrudan test edilmesine YALNIZCA
bağımsız-referans doğrulaması, public API ile gerçek örneklemden
ulaşılamayan variance_term <= 0 dalı (§17.4.9) ve fail-closed sınır
testleri için izin verilir; davranış testlerinin geri kalanı public API
üzerinden yapılır.
```

**17.4.13 Gerçek Entegrasyon Senaryosu (LOCKED)**

```
Gerçek SQLiteHistoricalCandleStore (tmp_path) üzerinde, fiyatı
değişen (sabit OLMAYAN) mumlarla, tek bir IS penceresi; en az üç farklı
candidate (farklı deterministik policy'ler) için
run_rolling_backtest_from_store -> Trial -> TrialGroup;
compute_deflated_sharpe_ratio(group, selected_candidate_id=...,
independent_trial_count=...) sonucu [0, 1] içinde ve bağımsız float
referansıyla (§17.4.12 madde 5) uyumludur; N arttıkça DSR artmaz
(monoton azalmayan eşik); iki pencereli bir Trial içeren grup
exact mesajla reddedilir.
```

**17.4.14 Deferred / Kapsam Dışı (Bu Kontratta DEĞİL)**

```
- Efektif-N estimator'ı (Ek A.3 ortalama-korelasyon interpolasyonu,
  entropi/kümeleme yöntemleri) — ayrı kontrat; hizalanmış getiri
  matrisi ve kötü koşulluluk kararları gerektirir.
- Çok pencereli pooling (walk-forward OOS birleştirme, overlap/boşluk/
  sermaye sıfırlama kuralları) — ayrı kontrat.
- Harvey-Liu alternatif eşiği, PSR'nin ayrı public API'si, annualized
  DSR raporlaması, güven eşiği (örn. 0.95) kararı veya "geçti/kaldı"
  etiketi — DSR yalnızca bir olasılık döndürür.
- Candidate selection, optimizer, final holdout enforcement, CPCV, PBO,
  multiple-testing correction, parameter stability, persistence,
  raporlama/CLI/UI, risk profili, günlük öneri bildirimi, paper/live.
```

**17.4.15 Durum**

```
Kilit zamanında: LOCKED — IMPLEMENTATION PENDING, §28.K — 0/27 (tarihsel).
Şimdi: LOCKED VE IMPLEMENTED + TESTED, §28.K — 27/27 (§17.4.17). Bu,
FAZ6C'nin veya Faz 6'nın tamamlandığı anlamına GELMEZ; Faz 7 önkoşulunu
(§29) DEĞİŞTİRMEZ.
```

**17.4.16 Sınırlamalar (Implementation Sonrası, Açık Kayıt)**

```
- N caller-beyanlı bir varsayımdır; efektif-N estimator'ı YOK.
- V tek grubun kayıtlı Trial'larından gelir; N ile aynı evreni temsil
  ettiği doğrulanamaz.
- Yalnızca tek pencereli Trial; çok pencereli pooling YOK (mevcut çok
  pencereli Trial/TrialGroup/rolling desteği DEĞİŞMEDEN korunur).
- Eq. (1) büyük-N yaklaşımıdır; küçük N'de hafif yüksek eşik
  (muhafazakâr) verir.
- İstatistiksel yeterlilik eşiği, güven eşiği veya "geçti/kaldı"
  etiketi YOK; DSR bir işlem/risk kararı DEĞİLDİR ve kullanıcının
  gelecekteki risk profiliyle BAĞLANTILI DEĞİLDİR.
- Newton iterasyon üst sınırının tüm domain için ispatı YOK (fail-closed).
```

**17.4.17 Implementation Evidence (IMPLEMENTED + TESTED — combined delivery ile eklendi)**

```
Production: src/crypto_quant_lab/validation/deflated_sharpe.py (YENİ)
  - compute_deflated_sharpe_ratio (tek public sembol; import'lar private
    alias'larla), private _normal_pdf/_normal_cdf/_normal_quantile/
    _deflated_sharpe_from_statistics, _dsr_context (prec=80) ve
    _output_context (prec=28), 85 basamaklı _EULER_MASCHERONI ve _PI
    (mpmath'e göre hata ~4e-86).
  - §17.4.6'nın 15 adımı ve mesajları birebir; Stage-2 ve periodic
    returns DEĞİŞMEDEN reuse; hiçbir mevcut dosya değişmedi.
Test: tests/test_validation_deflated_sharpe.py (YENİ) — 109 test, tümü
  PASS: API/imza/scope, 15 adımın mesajları ve ulaşılabilir eşzamanlı-
  ihlal sıraları (1>2>3, 3>5, 4>5, 5>6, 6>7, 7>8, 8>9, 9>10, 10>11;
  11/12/13 çiftleri gerçek örneklemle aynı anda ihlal edilemez: stdev>0
  => m2>0, Pearson), N/V/moment/ölçek/rf
  semantiği, bağımsız referanslar (§17.4.12), clamp/alan/fail-closed,
  ambient-context bağımsızlığı, no-mutation/determinism, import yönü,
  gerçek SQLite + run_rolling_backtest_from_store entegrasyonu (LONG/
  SHORT/alternating policy, 25 değişken fiyatlı mum, tek pencere; iki
  pencereli grup reddi).
İlgili regression suite'ler (trial_group, candidate, metrics,
annualized_metrics, rolling, windows, purging, backtest_models,
backtest_results — 790 test) DEĞİŞMEDEN yeşil; tam suite 2154/2154 PASS
(2045 önceki + 109 yeni).
```

### 17.5 PBO — CSCV Exact Contract LOCKED VE IMPLEMENTED + TESTED (§17.5.13–17.5.24, §28.M — 24/24); Önkoşul Trial Return Matrix (§17.5.1–17.5.12, §28.L — 22/22)

Prerequisites: birden fazla candidate/trial (18) + birden fazla partition + deterministic performance matrix + explicit selection rule. Bölüm 18 artık tek bir candidate'in tek bir trial'ını implement eder (28.G — 25/25); çoklu-candidate/trial karşılaştırma/aggregation ve deterministic performance matrix HENÜZ MEVCUT DEĞİLDİR (§18.9, 18.13) — bu nedenle PBO ilk primitive olarak **anlamlı şekilde implement edilemez** (foundation pre-flight'in kendi bulgusuyla tutarlı).

**Durum güncellemesi (FAZ6C — PBO önkoşulu: trial return matrix foundation):** PBO'nun "çoklu candidate/trial karşılaştırması" ve "deterministic performance matrix" önkoşulu, birincil kaynağın tanımladığı biçimde aşağıdaki Bölüm 17.5.1–17.5.12'de LOCKED VE IMPLEMENTED + TESTED'dır (`TrialReturnMatrix`, `build_trial_return_matrix`; §28.L — 22/22). PBO'nun KENDİSİ (CSCV bölümleme, seçim kuralı, rank/logit, PBO olasılığı) bu durum güncellemesi yazıldığında spec-lock/implement edilmemişti (tarihsel); artık §17.5.13–17.5.24'te LOCKED VE IMPLEMENTED + TESTED'dır (§28.M — 24/24).

**17.5.1 Source-Preflight ve Bağımlılık Seçimi**

```
Birincil kaynak (ERİŞİLDİ, render ile okundu):
  Bailey, D. H., Borwein, J., López de Prado, M., Zhu, Q. J. "The
  Probability of Backtest Overfitting." Journal of Computational Finance
  (2017); SSRN 2326253. Yazar PDF'i davidhbailey.com/dhbpapers/
  backtest-prob.pdf (34 sayfa, CreationDate 2015-02-27), Algorithm 2.3
  (CSCV), basılı s. 11-12:
  - "First, we form a matrix M by collecting the performance series
    from the N trials. In particular, each column n = 1, ..., N
    represents a vector of profits and losses over t = 1, ..., T
    observations ... M is therefore a real-valued matrix of order
    (T x N)." Koşullar: "(i) M is a true matrix, i.e. with the same
    number of rows for each column, where observations are synchronous
    for every row across the N trials, and (ii) the performance
    evaluation metric ... can be estimated on subsamples of each column."
  - "Second, we partition M across rows, into an even number S of
    disjoint submatrices of equal dimensions" (T/S x N).
  - Training/testing setleri alt matrisleri "in their original order"
    birleştirir; seçim n* = IS'te en iyi strateji; ω̄c = r̄/(N+1);
    λc = ln(ω̄c/(1 - ω̄c)); f(λ) Eq. (2.4).
  - Kaynak içi not: Adım (c) metni "the nth column of J (the testing
    set)" der; J tanım gereği training set'tir (adım a) — metindeki
    parantez bir yazım hatası olarak kaydedilir, bu foundation'ı
    etkilemez.

Sonuç: PBO'nun girdisi aday x değerlendirme-penceresi SKALER metrik
tablosu DEĞİL, gözlem x deneme hizalı PERFORMANS SERİSİ matrisidir —
CSCV metriği satır bloklarının BİRLEŞİMİ üzerinde yeniden hesaplar;
pencere başına özet değerlerden bu yeniden hesaplanamaz (örn. Sharpe
toplanabilir değildir). Bu nedenle önerilen "aday x bölüm performans
matrisi" yerine, pencere etiketli satırlara sahip hizalı getiri
matrisi seçildi; pencere-başı skaler tablo gerekirse bu matristen
türetilebilir.
```

**Bağımlılık tablosu (FAZ6C kalanları):**

| Bileşen | Eksik önkoşullar (kaynak koddan) | Bu teslimattan sonra |
|---|---|---|
| CPCV (17.2) | fold modeli; label/outcome-horizon purging (repo'da horizon kavramı yok, §17.1.13); hizalı performans matrisi | Matris önkoşulu karşılanır; horizon + fold modeli hâlâ ENGEL |
| PBO (17.5) | hizalı T x N performans matrisi; S bölümleme kuralı; açık seçim kuralı; alt-örneklem metriği; rank/logit | Matris karşılanır; PBO kontratı (bölümleme/seçim/metrik) sıradaki adım olabilir |
| Multiple-testing (17.6) | deneme başına test istatistiği/p-değeri tanımı ve düzeltme ailesi seçimi (yöntem kararı); DSR tek bir seçim-yanlılığı düzeltmesini zaten sağlar | Matristen bağımsız; yöntem kararı gerekir |
| Parameter stability (17.7) | Candidate.parameters opaque'tır; komşuluk/parametre uzayı tanımı ve stabilite metriği yok | Hâlâ tasarım ENGELİ |

Ek fayda: hizalı getiri matrisi, DSR'de deferred kalan efektif-N (Ek A.3 ortalama korelasyon) tahmininin de ön koşuludur — bu teslimat o tahmini YAPMAZ.

**17.5.2 Kapsam ve Tasarım Kararları (LOCKED)**

```
Kaynakta belirtilen:
  - T x N gerçek matris; satırlar N deneme boyunca eşzamanlı gözlem;
    sütun = bir deneme; satırlar orijinal sırada kullanılır.
Projenin tasarım tercihleri:
  - Hücre değeri: kaynaktaki "profits and losses" yerine, mevcut
    compute_periodic_returns'ün per-observation SIMPLE RETURN'ü
    (Bölüm 15.13, DEĞİŞMEDEN reuse). Gerekçe: Stage-2/DSR ile aynı
    ölçek; her pencere taze sermaye ile başladığı için (Bölüm 11)
    pencere başı ilk getiri o pencerenin initial_cash'ine göredir.
  - Kaynak: tek bir TrialGroup (§20) — aynı provenance, aynı ordered
    evaluation pencereleri, benzersiz candidate_id garanti.
  - Sütun yönü ve sırası: sütun n = group.trials[n]; candidate_ids
    grup sırasıyla (sıralama/ranking YOK).
  - Satır yönü ve sırası: pencereler grup sırasıyla, her pencerede
    equity_curve sırasıyla; satır anahtarı = gözlem zamanı; her satır
    kaynak pencere index'iyle etiketlenir (window_indices).
  - Pencereler kronolojik ve ayrık OLMALIDIR (window[j].start >=
    window[j-1].end); overlap/duplicate/ters sıra -> ValueError.
    Gerekçe: aksi halde aynı gözlem birden fazla satırda yer alır
    (T şişer, CSCV blokları bağımsız olmaz). Boşluklar (gap) legaldir
    ve observation_times üzerinden görünür kalır. Bu, rolling/Trial/
    TrialGroup'un overlap'i legal bırakan davranışını DEĞİŞTİRMEZ —
    yalnızca bu matrisin girdisine uygulanır.
  - Hizalama: her denemenin her penceredeki equity-gözlem zamanları
    trials[0] ile BİREBİR eşit olmalıdır; aksi ValueError. Eksik hücre
    doldurma, düşürme, interpolasyon, ortalama YOK.
  - Pencere sahipliği: trials[0]'ın her gözlem zamanı kendi penceresinin
    GÖZLEM aralığı (start, end] içinde olmalıdır (hizalama nedeniyle tüm
    denemeler için geçerli olur). Gerekçe (kaynak koddan, implementasyon
    sırasında gerçek entegrasyonla doğrulandı): backtest/results.
    build_equity_point, EquityPoint.time'ı feature_availability_time(
    candle) = open_time + candle süresi (mum KAPANIŞI) olarak atar; bu
    nedenle [start, end) penceresinde açılan mumların gözlemleri
    (start, end] aralığına düşer — son gözlem tam olarak `end`'dir.
    Bitişik pencereler ([a, b), [b, c)) yine ayrık gözlem kümeleri verir.
  - Zaman eşitliği ANLIK eşitliktir (aware datetime `==`); aynı anı
    gösteren farklı tzinfo'lu zamanlar eşzamanlı sayılır, dönüşüm
    yapılmaz; satır anahtarı olarak trials[0]'ın nesneleri kullanılır.
  - Tek pencere kısıtı YOK (DSR'nin kısıtı buraya TAŞINMAZ); N >= 1
    (TrialGroup minimumu), T >= 1.
Doğrulanmamış varsayımlar / kanıtlanmayanlar:
  - Aynı maliyet/funding modeli (Trial saklamaz, §18.7), sızıntısızlık,
    denemeler arası bağımsızlık, IS/OOS rolü veya holdout koruması —
    matrisin varlığı bunların HİÇBİRİNİ kanıtlamaz.
  - Window-level purging (§17.1) ile label/outcome-horizon purging
    ayrımı DEĞİŞMEZ; bu matris hiçbir purging yapmaz.
  - CSCV'nin "S eşit alt matris" koşulu bu foundation'da ENFORCE
    EDİLMEZ (bölümleme PBO kontratının işidir).
```

**17.5.3 Exact Public API (LOCKED)**

```python
# Modül: src/crypto_quant_lab/validation/return_matrix.py (YENİ modül)


@dataclass(frozen=True, slots=True)
class TrialReturnMatrix:
    candidate_ids: tuple[str, ...]
    observation_times: tuple[datetime, ...]
    window_indices: tuple[int, ...]
    returns: tuple[tuple[Decimal, ...], ...]


def build_trial_return_matrix(group: TrialGroup) -> TrialReturnMatrix: ...
```

```
- returns SATIR-ÖNCELİKLİDİR: returns[t][n] = t. gözlemde n. denemenin
  getirisi (kaynaktaki T x N yönü).
- Public semboller TAM OLARAK {TrialReturnMatrix,
  build_trial_return_matrix}; import'lar private alias'larla.
- Package-root export YOK; hiçbir mevcut modül değişmez.
- Frozen/slotted, default eq/hash; ek metot/ranking/metrik YOK.
```

**17.5.4 `TrialReturnMatrix.__post_init__` — Yapısal Invariant'lar ve Exact Sıra (LOCKED)**

```
Her çok-elemanlı adım ayrı bir GLOBAL geçiştir.
 1. candidate_ids tuple değil -> TypeError("candidate_ids must be a tuple, got {type}")
 2. boş -> ValueError("candidate_ids must not be empty")
 3. eleman str değil -> TypeError("candidate_ids[{i}] must be a str, got {type}")
 4. tekrar -> ValueError("candidate_ids[{i}] {id!r} duplicates candidate_ids[{j}]")
 5. observation_times tuple değil -> TypeError("observation_times must be a tuple, got {type}")
 6. boş -> ValueError("observation_times must not be empty")
 7. eleman datetime değil -> TypeError("observation_times[{i}] must be a datetime, got {type}");
    timezone-naive/pseudo-naive -> datetime_to_epoch_us'un KENDİ
    ValueError'ı DEĞİŞMEDEN propagate
 8. kesin artan değil -> ValueError("observation_times[{i}] must be after observation_times[{i-1}]")
 9. window_indices tuple değil -> TypeError("window_indices must be a tuple, got {type}");
    uzunluk farklı -> ValueError("window_indices must have {T} entries, got {m}")
10. eleman int değil veya bool -> TypeError("window_indices[{i}] must be an int, got {type}")
11. window_indices[0] != 0 -> ValueError("window_indices[0] must be 0, got {v}");
    sonraki eleman öncekine eşit veya bir fazlası değil ->
    ValueError("window_indices[{i}] must equal window_indices[{i-1}] or window_indices[{i-1}] + 1, got {v}")
12. returns tuple değil -> TypeError("returns must be a tuple, got {type}");
    satır sayısı farklı -> ValueError("returns must have {T} rows, got {m}")
13. satır tuple değil -> TypeError("returns[{t}] must be a tuple, got {type}") (global);
    sütun sayısı farklı -> ValueError("returns[{t}] must have {N} columns, got {m}") (global)
14. hücre Decimal değil -> TypeError("returns[{t}][{n}] must be a Decimal, got {type}") (global);
    finite değil -> ValueError("returns[{t}][{n}] must be finite, got {v}") (global)
Hiçbir değer sessizce sıralanmaz, düzeltilmez, kopyalanmaz veya doldurulmaz.
```

**17.5.5 `build_trial_return_matrix` — Veri Akışı ve Exact Sıra (LOCKED)**

```
1. group TrialGroup değil ->
   TypeError("group must be a TrialGroup, got {type}")
2. windows = trials[0]'ın pencere dizisi (TrialGroup tüm denemelerde
   eşitliği garanti eder). j >= 1 için windows[j].start < windows[j-1].end ->
   ValueError("evaluation windows must be chronologically ordered and non-overlapping: "
              "windows[{j}].start ({start!r}) is before windows[{j-1}].end ({end!r})")
3. trials[0] için her pencere j ve her equity noktası k:
   NOT (start < time <= end) ->
   ValueError("trials[0].results[{j}].result.equity_curve[{k}].time ({t!r}) "
              "is outside the observation range ({start!r}, {end!r}] of its evaluation window")
4. i >= 1 (artan), her j (artan): gözlem zamanları dizisi trials[0]'ınkinden
   farklı -> ValueError("trials[{i}].results[{j}] equity observation times "
                        "do not match trials[0].results[{j}]")
5. Her deneme (grup sırası), her pencere (sıra): compute_periodic_returns(
   result) — alt katman hataları DEĞİŞMEDEN propagate.
6. Satırlar: pencereler sırasıyla, her pencerenin gözlemleri sırasıyla;
   observation_times = trials[0]'ın zaman nesneleri (kopyalanmaz),
   window_indices = pencere index'i, returns[t][n] = n. denemenin o
   gözlemdeki getirisi. TrialReturnMatrix inşa edilir (17.5.4 yeniden
   doğrular).
```

**17.5.6 Metrik Anlamı ve Tanımsız Durumlar (LOCKED)**

```
- Hücre = Bölüm 15.13'ün per-observation simple return'ü, boyutsuz,
  annualize EDİLMEMİŞ; her pencerenin ilk getirisi o pencerenin
  initial_cash'ine göredir (sermaye pencere başında sıfırlanır).
- Sıfır/negatif payda, boş equity curve, sıralı olmayan zaman vb. —
  compute_periodic_returns'ün kendi deterministik hataları; matris
  kısmi sonuç DÖNDÜRMEZ.
- Sonlu olmayan hücre üretilmez (15.13 zaten fail eder); 17.5.4
  adım 14 savunma katmanıdır.
```

**17.5.7 Purity ve Import Direction (LOCKED)**

```
return_matrix.py imports: dataclasses, datetime, decimal (stdlib);
  storage.sqlite_codec.datetime_to_epoch_us; validation.metrics.
  compute_periodic_returns; validation.trial_group.TrialGroup — hepsi
  private alias'larla. Hiçbir mevcut modül return_matrix.py'yi import
  ETMEZ; döngü YOK. Girdi mutasyonu, wall-clock, randomness, I/O,
  float, seçim/ranking, persistence YOK.
```

**17.5.8 Dosya Kapsamı**

```
Yeni: src/crypto_quant_lab/validation/return_matrix.py,
      tests/test_validation_return_matrix.py. Doküman: VALIDATION_SPEC.md.
Değişmeyen: tüm mevcut production/test dosyaları, __init__.py,
ROADMAP.md, pyproject.toml, AGENTS.md, CLAUDE.md.
```

**17.5.9 Test Kontratı**

```
API/scope; 17.5.4'ün her adımı ve eşzamanlı-ihlal sıraları; builder
adım 1-5 mesajları ve sıraları; tek ve çok pencereli grup; gap'li
pencereler; overlap/duplicate/ters sıra reddi; hizalama/sahiplik reddi;
alt katman hatalarının DEĞİŞMEDEN propagasyonu; satır/sütun yönü ve
sırası; hücrelerin mevcut compute_periodic_returns çıktısıyla birebir
eşitliği; no-copy/no-mutation/determinism; import yönü; gerçek SQLite +
run_rolling_backtest_from_store ile çok pencereli entegrasyon.
Test oracle'ı modülün kendi birleştirme mantığını KOPYALAMAZ: hücreler
mevcut (değişmemiş) compute_periodic_returns'e ve elle yazılmış küçük
sabit beklentilere karşı doğrulanır.
```

**17.5.10 Explicit Exclusions**

```
PBO, CSCV bölümleme ve kombinasyonlar, seçim kuralı, rank/logit, CPCV,
fold modeli, label/outcome-horizon purging, efektif-N/korelasyon
tahmini, çok pencereli DSR pooling, aday x pencere skaler özet tablosu,
multiple-testing, parameter stability, persistence, raporlama/UI.
```

**17.5.11 Durum ve Implementation Evidence**

```
LOCKED VE IMPLEMENTED + TESTED (aynı combined delivery). §28.L — 22/22.
Production: src/crypto_quant_lab/validation/return_matrix.py (YENİ).
Test: tests/test_validation_return_matrix.py (YENİ) — 55 test, tümü
PASS. İlgili regression suite'ler (trial_group, candidate, metrics,
rolling, windows, purging, deflated_sharpe, annualized_metrics,
backtest_models, backtest_results — 899 test) DEĞİŞMEDEN yeşil; tam
suite 2209/2209 PASS (2154 önceki + 55 yeni). Implementasyon sırasında
yapılan tek kontrat düzeltmesi: ilk taslaktaki "[start, end)" sahiplik
kuralı, gerçek rolling entegrasyonunun gösterdiği mum-kapanışı zaman
damgası semantiğine göre "(start, end]" olarak düzeltildi (17.5.2).
```

**17.5.12 PBO İçin Kalan Açık Kararlar (Kilitlenmez — tarihsel; artık §17.5.15–17.5.16'da karara bağlandı)**

```
S (çift sayı) ve eşit-boyutlu blok kuralı (T'nin S'ye bölünememesi),
bloklar pencere sınırlarıyla mı yoksa eşit satır sayısıyla mı
belirlenir, alt-örneklem metriği (örn. Stage-2 Sharpe blok birleşimi
üzerinde — tanımsız stdev durumu), eşitlik (tie) durumunda seçim ve
rank kuralı, ω̄ = 0 veya 1 olamayacağının (N+1 paydası) doğrulanması,
C(S, S/2) hesap maliyeti sınırı.
```

**Durum güncellemesi (FAZ6C — PBO/CSCV exact contract + implementation):** PBO'nun kendisi, TrialReturnMatrix üzerinde CSCV ile, aşağıdaki Bölüm 17.5.13–17.5.24'te LOCKED VE IMPLEMENTED + TESTED'dır (`compute_probability_of_backtest_overfitting`, `PboResult`, `CscvCombination`; §28.M — 24/24). §17.5.12'deki açık kararlar §17.5.15'te karara bağlanmıştır. CSCV, CPCV DEĞİLDİR; CPCV hâlâ spec-lock edilmemiştir.

**17.5.13 Kaynak Bulguları (render ile okundu; §17.5.1'e ek)**

```
Bailey, Borwein, López de Prado, Zhu — yazar PDF'i (34 s., 2015-02-27):
  - s. 9 (§2.1): sıralama ARTAN yöndedir — örnek R^c = (0.5, 1.1, 0.7)
    -> r^c = (1, 3, 2); IS-optimal strateji Ω*_n = {f : f_n = N}, yani
    IS'te N. sırada olandır.
  - s. 10, Definition 2.2 (Eq. 2.2):
    PBO = Σ_n Prob[r̄_n < N/2 | r ∈ Ω*_n] Prob[r ∈ Ω*_n]  (KESİN "<").
  - s. 11-12, Algorithm 2.3: çift S, eşit boyutlu ayrık alt matrisler,
    C(S, S/2) kombinasyon (Eq. 2.3), J = c'deki alt matrislerin
    "original order"da birleşimi, J̄ = tümleyen, n* = IS'te en iyi,
    ω̄c = r̄_{n*}/(N+1), λc = ln(ω̄c/(1 - ω̄c)), f(λ) (Eq. 2.4).
  - s. 13 (§3.1): "The PBO ... may now be estimated using the CSCV method
    with φ = ∫_{-∞}^{0} f(λ)dλ" — 0 DAHİL; s. 14: backtest yardımcıysa
    "That is the case when λc > 0".
  - s. 13 (Figure 1): her training kombinasyonu testing olarak da
    kullanılır (simetri).
Kaynak içi tutarsızlıklar (kaydedilir, sessizce seçilmez):
  (a) Definition 2.2'nin kesin r̄ < N/2 koşulu ile §3.1'in λ <= 0
      (r̄ <= (N+1)/2) kestiricisi medyanda ayrışır.
  (b) s. 11: "if S = 16, we will form 12,780 combinations" —
      C(16, 8) = 12,870'tir (yazım hatası).
  (c) s. 12 adım (c) "the nth column of J (the testing set)" — J
      training set'tir (§17.5.1'de kaydedildi).
Kaynağın ELE ALMADIKLARI: IS kazananında eşitlik, OOS sıralamasında
eşitlik, T'nin S'ye bölünememesi, alt-örneklemde tanımsız metrik,
hesap maliyeti sınırı. Bunlar §17.5.15'te PROJE KONVANSİYONU olarak
kilitlenir.
```

**17.5.14 Exact Public API (LOCKED)**

```python
# Modül: src/crypto_quant_lab/validation/pbo.py (YENİ modül)


@dataclass(frozen=True, slots=True)
class CscvCombination:
    in_sample_blocks: tuple[int, ...]
    selected_candidate_ids: tuple[str, ...]
    out_of_sample_ranks: tuple[Decimal, ...]
    logits: tuple[Decimal, ...]


@dataclass(frozen=True, slots=True)
class PboResult:
    block_count: int
    combinations: tuple[CscvCombination, ...]
    probability_of_backtest_overfitting: Decimal


def compute_probability_of_backtest_overfitting(
    matrix: TrialReturnMatrix,
    *,
    block_count: int,
    risk_free_per_period: Decimal = Decimal(0),
) -> PboResult: ...
```

```
- Public semboller TAM OLARAK bu üçü; import'lar private alias'larla;
  package-root export YOK; hiçbir mevcut modül değişmez.
- Sonuç denetlenebilirdir: her kombinasyon için IS blokları, IS
  kazanan(lar)ı, OOS sırası ve logit; PBO bunlardan yeniden
  hesaplanabilir. IS/OOS Sharpe değerleri, satır matrisi veya f(λ)
  histogramı SAKLANMAZ (gereksiz büyüme).
- CscvCombination: dört alan tuple; selected boş olamaz; selected/ranks/
  logits eşit uzunlukta. PboResult: block_count int (bool değil),
  combinations CscvCombination tuple'ı, PBO finite ve [0, 1] içinde.
```

**17.5.15 Konvansiyonlar — Kaynak ve Proje Ayrımı (LOCKED)**

```
KAYNAKTAN:
  - S çift; satırlar S eşit, ayrık alt matrise bölünür; C(S, S/2)
    kombinasyonun TAMAMI kullanılır; tümleyen = OOS; alt matrisler
    orijinal sırada birleştirilir.
  - Artan sıralama; IS-optimal = en yüksek IS performansı;
    ω̄ = r̄/(N+1); λ = ln(ω̄/(1 - ω̄)).
  - PBO kestiricisi φ = ∫_{-∞}^{0} f(λ)dλ, yani λ <= 0 (medyan DAHİL)
    aşırı uyum sayılır — §3.1'in CSCV kestiricisi seçildi; Definition
    2.2'nin kesin "<" okuması SEÇİLMEDİ (tutarsızlık (a)). Medyan
    durumu sonuçta görünür kalır (rank = (N+1)/2, logit = 0).
PROJE KONVANSİYONLARI (kaynak belirtmez):
  1. Bloklar ARDIŞIK satır bloklarıdır: blok b = satırlar
     [b*T/S, (b+1)*T/S). T % S != 0 -> ValueError; satır silme, kırpma,
     padding YOK. S >= 2, çift; N >= 2 (N = 1'de sıralama anlamsız,
     her kombinasyon medyana düşer); T/2 >= 2 (sample stdev için).
  2. Kombinasyonlar itertools.combinations(range(S), S/2) sırasıyla
     (leksikografik) ve TEMBEL üretilir; yalnızca kombinasyon başı
     kayıt saklanır.
  3. Alt-örneklem metriği: Stage-2'nin per-observation Sharpe'ı
     (Bölüm 15.16) — arithmetic mean, sample stdev (n-1), (mean - rf)
     / stdev, Bölüm 15.17 context shape'i (prec=28). compute_stage2_
     metrics bir BacktestResult ister ve getirileri doğrudan KABUL
     ETMEZ; sahte BacktestResult üretilmez ve private helper import
     EDİLMEZ — aynı formül ve işlem sırası yerel olarak tanımlanır ve
     bir BacktestResult'ın kendi getirileri üzerinde compute_stage2_
     metrics ile BİREBİR (bit-bit) eşitliği test edilir. Sharpe satır
     sırasından bağımsızdır; "original order" korunur ama sonucu
     değiştirmez.
  4. Sıfır stdev (IS veya OOS, herhangi bir aday): Sharpe TANIMSIZ ->
     ValueError (aday ve kombinasyonu tanımlar); 0/sonsuz ile
     DEĞİŞTİRİLMEZ; kombinasyon paydadan ÇIKARILMAZ (tüm hesap fail
     eder).
  5. IS kazananında eşitlik: en yüksek IS Sharpe'ına (28 basamaklı
     Decimal değer eşitliği) sahip TÜM adaylar seçilir; kombinasyonun
     aşırı-uyum ağırlığı = (λ <= 0 olan seçilmişlerin sayısı) / (seçilmiş
     sayısı). Gerekçe: Definition 2.2'nin Σ_n ... Prob[r ∈ Ω*_n] yapısında
     eşitliği düzgün (uniform) rastgele kıran bir seçimin beklenen
     değerine eşittir; sütun sırasına BAĞIMSIZDIR. Seçilmişler sonuçta
     matris sütun sırasıyla raporlanır.
  6. OOS sıralamasında eşitlik: ortalama (orta) sıra — r̄ = (2*alttaki +
     eşit + 1)/2; r̄ in [1, N], ω̄ in (0, 1) kalır; sütun sırasından
     bağımsızdır.
  7. λ <= 0 kararı TAM rasyonel karşılaştırmayla verilir: 2 r̄ <= N + 1
     (ln yuvarlamasına dayanmaz). Raporlanan logit = ln(r̄/(N + 1 - r̄)),
     prec=28 private context'te.
  8. PBO = Σ_c ağırlık_c / C(S, S/2), fractions.Fraction ile TAM
     hesaplanır, sonra prec=28 context'te bir kez Decimal'e bölünür.
  9. risk_free_per_period (per-observation, Decimal, finite, varsayılan
     0) tüm adayların IS ve OOS Sharpe'ına aynı şekilde uygulanır.
```

**17.5.16 Maliyet Sınırı (LOCKED)**

```
cell_evaluations = C(S, S/2) * T * N  (her kombinasyon her adayın tüm
T satırını IS + OOS olarak bir kez dolaşır). cell_evaluations >
20,000,000 -> ValueError (değerler mesajda); rastgele örneklemeye veya
kısmi hesaba GEÇİLMEZ. Sınır, maliyetin C ile birlikte T ve N'e de
bağlı olduğunu yansıtır. Ölçülen hız (bu makine): ~0.4-0.8 µs/hücre
-> sınırda en fazla ~16 s. Örnek: S=16 (C=12,870), N=10 için T <= 155.
```

**17.5.17 Matris Girdisinin Sınırları (LOCKED)**

```
- Girdi yalnızca TrialReturnMatrix'tir; yapısal geçerliliği (eşzamanlı
  satırlar, kronolojik/ayrık pencereler, (start, end] sahipliği)
  §17.5.4-17.5.5'te zaten kanıtlanmıştır — burada yeniden
  doğrulanmaz.
- Çok pencereli matrisler KABUL edilir ve bloklar pencere sınırlarını
  AŞABİLİR. Sharpe satır sırasından bağımsız olduğundan pencere
  boşlukları ve sınırları metriği değiştirmez; her pencerenin ilk
  getirisi o pencerenin taze sermayesine göredir (Bölüm 11, 15.13).
  DOĞRULANMAMIŞ VARSAYIM: pencereler arası getirilerin aynı stratejinin
  değiştirilebilir (exchangeable) gözlemleri olduğu; matris modeli
  rejim/koşul bilgisi TAŞIMAZ ve bu varsayım mekanik olarak
  doğrulanamaz. Pencere hizalı blok zorunluluğu getirilmedi (kaynak
  gerektirmez); çağıran isterse pencere başına eşit gözlem sayısı ve
  S = pencere sayısı seçerek blokları pencerelerle hizalayabilir.
- Mevcut çok pencereli matris desteği DEĞİŞMEZ; PBO'ya özgü tüm
  koşullar yalnızca bu fonksiyonun girişinde uygulanır.
```

**17.5.18 Validation / Fail-Fast Sırası ve Exact Mesajlar (LOCKED)**

```
1. matrix TrialReturnMatrix değil -> TypeError("matrix must be a TrialReturnMatrix, got {type}")
2. block_count int değil veya bool -> TypeError("block_count must be an int, got {type}")
3. block_count < 2 veya tek -> ValueError("block_count must be an even integer >= 2, got {S}")
4. risk_free_per_period Decimal değil -> TypeError("risk_free_per_period must be a Decimal, got {type}");
   finite değil -> ValueError("risk_free_per_period must be finite, got {v}")
5. N < 2 -> ValueError("at least two candidates are required to rank out-of-sample performance, got {N}")
6. T % S != 0 -> ValueError("row count {T} is not divisible by block_count {S}; rows are never trimmed or padded")
7. T // 2 < 2 -> ValueError("each half-sample must contain at least two rows to compute a sample standard deviation, got {T//2}")
8. maliyet -> ValueError("CSCV cost of {cells} cell evaluations (C({S}, {S/2})={C} x T={T} x N={N}) exceeds the limit of 20000000; no sampling is performed")
9. Kombinasyonlar leksikografik; her birinde önce tüm adayların IS
   Sharpe'ı, sonra OOS Sharpe'ı (sütun sırasıyla); sıfır stdev ->
   ValueError("{in-sample|out-of-sample} Sharpe ratio is undefined for candidate {id!r} in combination with in-sample blocks {blocks}: zero standard deviation");
   sonlu olmayan ara değer -> ValueError.
```

**17.5.19 Purity, Determinism, Context (LOCKED)**

```
Girdi mutasyonu YOK; aynı girdi -> eşit (ve eşit-hash) sonuç; tüm
Decimal aritmetiği (Sharpe, sıra, λ karşılaştırması, logit, PBO) taze
private prec=28 context'te; ambient context sonucu değiştirmez;
wall-clock, randomness, float, I/O, persistence YOK. Import'lar:
dataclasses, decimal, fractions, itertools, math (stdlib) ve
validation.return_matrix.TrialReturnMatrix.
```

**17.5.20 Kapsam Dışı ve İddia Edilmeyenler**

```
CPCV, fold modeli, label/outcome-horizon purging, performance
degradation / probability of loss / stochastic dominance (§3.2-3.4),
f(λ) histogramı/grafik, PBO eşiği (örn. kaynaktaki 0.05) veya
"geçti/kaldı" etiketi, efektif-N, multiple-testing, parameter
stability, canlı strateji seçimi, emir, risk profili kararı. PBO bir
araştırma değerlendirmesidir; bağımsızlık, tam araştırma geçmişi,
aynı maliyet modeli veya holdout koruması kanıtı DEĞİLDİR.
```

**17.5.21 Dosya Kapsamı**

```
Yeni: src/crypto_quant_lab/validation/pbo.py, tests/test_validation_pbo.py.
Doküman: VALIDATION_SPEC.md. Değişmeyen: tüm mevcut production/test
dosyaları, __init__.py, ROADMAP.md, pyproject.toml, AGENTS.md, CLAUDE.md.
```

**17.5.22 Doğrulama Yöntemi**

```
Beklenen değerler production algoritmasından ÜRETİLMEZ: küçük, elle
izlenebilen matrislerde (her iki-satırlık yarıda eşit yayılım ->
Sharpe sırası satır toplamı sırasıyla aynı) PBO = 1 (anti-kalıcı),
PBO = 0 (kalıcı), medyan (λ = 0) ve IS-eşitliği (PBO = 1/4) elle
türetildi; logit sabitleri (ln 2, ln 3, ln(5/3)) mpmath ile bağımsız
doğrulandı. Sharpe eşdeğerliği mevcut compute_stage2_metrics'e karşı
bit-bit test edilir. Entegrasyon: gerçek SQLite + rolling -> TrialGroup
-> TrialReturnMatrix -> PBO.
```

**17.5.23 Durum ve Implementation Evidence**

```
LOCKED VE IMPLEMENTED + TESTED (aynı combined delivery). §28.M — 24/24.
Production: src/crypto_quant_lab/validation/pbo.py (YENİ).
Test: tests/test_validation_pbo.py (YENİ) — 35 test, tümü PASS.
İlgili regression suite'ler (return_matrix, trial_group, candidate,
metrics, deflated_sharpe, rolling, windows, purging,
annualized_metrics, backtest_models, backtest_results — 954 test)
DEĞİŞMEDEN yeşil; tam suite 2244/2244 PASS (2209 önceki + 35 yeni).
```

**17.5.24 Kalan Sınırlamalar**

```
Pencereler arası exchangeability varsayımı doğrulanamaz; eşitlik
konvansiyonları kaynağa değil projeye aittir; Definition 2.2'nin
kesin okumasıyla medyanda fark olabilir (sonuçta görünür); maliyet
sınırı büyük S/T/N kombinasyonlarını reddeder (örnekleme yok);
§3.2-3.4 istatistikleri ve CPCV yapılmadı.
```

### 17.6 Multiple-Testing Corrections — Holm Düzeltme Temeli LOCKED VE IMPLEMENTED + TESTED (§17.6.1–17.6.10, §28.N — 18/18); p-Değeri Üretimi, Aile Kapsamı ve Seçim Politikası AÇIK

Prerequisites: trial-count tracking (18) — candidate/trial foundation'ına bağımlı. Bölüm 18'in kendisi artık IMPLEMENTED + TESTED'dır (28.G), ama trial-count tracking (kaç candidate/trial değerlendirildiğinin kaydı, Bölüm 20) bu foundation'ın kapsamı DIŞINDADIR ve henüz mevcut değildir. **Durum güncellemesi:** tek-grup kapsamlı, ham trial-count kaydının exact kontratı Bölüm 20.1–20.13'te LOCKED'dır VE artık IMPLEMENTED + TESTED'dır (§20.14, §28.J — 19/19); multiple-testing correction'ın kendisi, gruplar-arası sayım ve efektif sayı hâlâ spec-lock edilmemiştir.

**Durum güncellemesi (FAZ6C — Holm düzeltme temeli):** Dışarıdan verilen p-değerleri için Holm çoklu-test düzeltmesi aşağıdaki Bölüm 17.6.1–17.6.10'da LOCKED VE IMPLEMENTED + TESTED'dır (`apply_holm_correction`; §28.N — 18/18). Bu YALNIZCA bir düzeltme katmanıdır: getirilerden geçerli p-değeri üretimi, test ailesinin araştırma geçmişine göre kapsamı ve sonuç seçim politikası AÇIKTIR; "multiple-testing corrections" başlığı ve FAZ6C bu teslimatla TAMAMLANMIŞ SAYILMAZ.

**17.6.1 Kaynaklar ve Kaynak Kuralları**

```
Birincil referans: Holm, S. (1979). "A Simple Sequentially Rejective
Multiple Test Procedure." Scandinavian Journal of Statistics 6, 65-70 —
orijinal makale bu turda OKUNMADI (erişilmedi). Formül ve özellikler,
yöntemin standart referans implementasyonundan doğrulandı:
  - R stats::p.adjust belgesi (stat.ethz.ch/R-manual/R-devel/library/
    stats/html/p.adjust.html, ERİŞİLDİ): Holm (1979) Bonferroni'den
    "less conservative"; ilk dört yöntem "strong control of the
    family-wise error rate" için tasarlanmıştır; Bonferroni "dominated
    by Holm's method, which is also valid under arbitrary assumptions";
    n > length(p) verilirse gözlenmeyen p-değerleri Holm için "greater
    than all the observed p" varsayılır; çıktı girişle aynı uzunlukta
    ve aynı adlarla döner.
  - R kaynak kodu src/library/stats/R/p.adjust.R (ERİŞİLDİ):
      holm = { i <- seq_len(lp); o <- order(p); ro <- order(o)
               pmin(1, cummax((n+1L - i) * p[o]))[ro] }
    ve "if (n <= 1) return(p0)".
KAYNAKTAN: artan sıralama; i. sıradaki p'nin (m + 1 - i) ile çarpımı;
kümülatif maksimum; 1 ile üst sınır; sonuçların giriş sırasına geri
eşlenmesi; m <= 1 için p değişmez (formülün m = 1 hâli ile aynı);
keyfî bağımlılık altında güçlü FWER kontrolü.
```

**17.6.2 Proje Tercihleri (kaynak belirtmez veya kasıtlı kısıtlanır)**

```
1. Aile büyüklüğü m = verilen hipotez sayısıdır. R'nin n > length(p)
   seçeneği (gözlenmeyen hipotezler) SUNULMAZ; eksik hipotez için
   varsayımsal p-değeri veya tahmini toplam sayı EKLENMEZ.
2. Karar: adjusted_p <= significance_level ise reddedilir (eşitlik
   RED). Bu, adım-azalan Holm prosedürüne (p_(i) <= α/(m - i + 1) ilk
   başarısızlığa kadar) tam eşdeğerdir: 0 < α < 1 iken adjusted_(i) <= α
   <=> her j <= i için (m + 1 - j) p_(j) <= α.
3. Aritmetik TAM'dır: (m + 1 - j) * p, p'nin tam sayı katsayısı
   üzerinden kurulur; yalnızca karşılaştırma, max ve min uygulanır.
   Hiçbir Decimal context kullanılmaz ve yuvarlama YOKTUR — karar
   sınırındaki davranış tamdır (örn. 0.0125 x 4 = 0.05 == α -> RED;
   28 basamağın ötesindeki p-değerleri de tam çarpılır). Düzeltilmiş
   değerler girişten fazla basamak taşıyabilir; -0 girişi 0 olarak
   döner.
4. Eşit p-değerleri: sıralama (p, giriş index'i) ile kararlıdır; eşit
   p'lerin düzeltilmiş değerleri kümülatif maksimum nedeniyle
   matematiksel olarak EŞİTTİR — giriş sırası hiçbir hipotezin
   sonucunu değiştirmez.
5. significance_level açıkça verilir, Decimal, sonlu ve 0 < α < 1
   (0 ve 1 dejenere olduğundan reddedilir); varsayılan YOK.
6. Kimlikler: family_id ve hypothesis_id, candidate_id ile AYNI kural
   (str, boş/yalnızca-boşluk değil, baş/son boşluk yok; case-sensitive).
   Aile içinde hypothesis_id benzersizdir. p-değeri Decimal, sonlu,
   [0, 1] içinde (int/float/bool/str REDDEDİLİR).
7. Hiçbir kayıt sessizce çıkarılmaz; geçersiz girdi hesabı durdurur.
```

**17.6.3 Exact Public API (LOCKED)**

```python
# Modül: src/crypto_quant_lab/validation/multiple_testing.py (YENİ modül)


@dataclass(frozen=True, slots=True)
class HypothesisPValue:
    hypothesis_id: str
    p_value: Decimal


@dataclass(frozen=True, slots=True)
class HolmAdjustedHypothesis:
    hypothesis_id: str
    p_value: Decimal
    adjusted_p_value: Decimal
    rejected: bool


@dataclass(frozen=True, slots=True)
class HolmCorrectionResult:
    family_id: str
    significance_level: Decimal
    hypotheses: tuple[HolmAdjustedHypothesis, ...]


def apply_holm_correction(
    family_id: str,
    hypotheses: tuple[HypothesisPValue, ...],
    *,
    significance_level: Decimal,
) -> HolmCorrectionResult: ...
```

```
- Public semboller TAM OLARAK bu dördü; package-root export YOK.
- Sonuç hipotezleri GİRİŞ SIRASINDADIR; her biri kendi hypothesis_id'si
  ve ham p_value nesnesiyle (kopyalanmadan) eşlenir; family_id ve
  significance_level sonuçta korunur. Aile büyüklüğü len(hypotheses)'tır.
- Sonuç modelleri kendi alanlarını doğrular (kimlik, [0, 1] olasılıklar,
  bool karar, tuple tipleri).
- Modül yalnızca dataclasses ve decimal import eder; TrialGroup, DSR,
  PBO veya başka bir validation modülüne BAĞLANMAZ.
```

**17.6.4 Algoritma (LOCKED)**

```
m = len(hypotheses)
order = giriş index'lerinin (p_value, index) anahtarıyla artan sırası
running_max yok
her konum k = 0 .. m-1 için (index = order[k]):
    product = (m - k) * p_value[index]            # tam
    running_max = product (ilk adım) veya max(running_max, product)
    adjusted[index] = min(running_max, 1)
rejected[index] = adjusted[index] <= significance_level
```

**17.6.5 Validation / Fail-Fast Sırası ve Exact Mesajlar (LOCKED)**

```
HypothesisPValue.__post_init__:
  hypothesis_id str değil -> TypeError("hypothesis_id must be a str, got {type}")
  boş/yalnızca-boşluk -> ValueError("hypothesis_id must not be empty or whitespace-only")
  padding -> ValueError("hypothesis_id must not have leading/trailing whitespace padding")
  p_value Decimal değil -> TypeError("p_value must be a Decimal, got {type}")
  sonlu değil veya [0, 1] dışı -> ValueError("p_value must be finite and within [0, 1], got {v}")
apply_holm_correction:
  1-2. family_id (aynı kimlik kuralı ve mesaj kalıbı, alan adı family_id)
  3. hypotheses tuple değil -> TypeError("hypotheses must be a tuple, got {type}")
  4. boş -> ValueError("hypotheses must not be empty")
  5. GLOBAL: eleman HypothesisPValue değil ->
     TypeError("hypotheses[{i}] must be a HypothesisPValue, got {type}")
  6. GLOBAL: yinelenen kimlik ->
     ValueError("hypotheses[{i}].hypothesis_id {id!r} duplicates hypotheses[{j}].hypothesis_id")
  7. significance_level Decimal değil -> TypeError("significance_level must be a Decimal, got {type}")
  8. sonlu değil veya 0 < α < 1 değil ->
     ValueError("significance_level must be finite and satisfy 0 < significance_level < 1, got {α}")
```

**17.6.6 Kapsam Sınırları ve İddia Edilmeyenler**

```
- p-değeri ÜRETİLMEZ: Sharpe, DSR (1 - DSR dahil) veya PBO p-değeri
  olarak KABUL EDİLMEZ ve hiçbir dönüşüm uygulanmaz; getirilerden
  istatistiksel olarak geçerli p-değeri üretimi ayrı ve AÇIK bir iştir.
- TrialGroup.recorded_trial_count ile hipotez sayısı OTOMATİK
  EŞİTLENMEZ: aday, pencere ve ölçülen hipotez aynı kavram değildir.
- Bir family_id yazılması, hipotezlerin önceden belirlendiğini, ailenin
  araştırma geçmişinin tamamını kapsadığını veya seçilerek raporlanmış
  sonuçların hariç tutulmadığını KANITLAMAZ; bunlar caller disiplinidir.
- "Reddedildi" yalnızca Holm'un FWER kontrolü altında istatistiksel
  anlamlılıktır; kârlılık olasılığı, strateji seçimi, emir veya risk
  profili kararı DEĞİLDİR.
- Hochberg/Hommel/BH/BY, n > m seçeneği, p-değeri üretimi, aile kapsamı
  politikası, sonuç seçim politikası: kapsam dışı.
```

**17.6.7 Dosya Kapsamı**

```
Yeni: src/crypto_quant_lab/validation/multiple_testing.py,
tests/test_validation_multiple_testing.py. Doküman: VALIDATION_SPEC.md.
Değişmeyen: tüm mevcut production/test dosyaları, __init__.py,
ROADMAP.md, pyproject.toml, AGENTS.md, CLAUDE.md.
```

**17.6.8 Doğrulama Yöntemi**

```
Beklenen düzeltilmiş p-değerleri R'nin Holm tanımından (17.6.1) ELLE
türetilmiş tam rasyonellerdir ve testte literal olarak yazılıdır
(modülden üretilmez); kararlar ayrıca adım-azalan prosedürle elle
çapraz kontrol edilir. Yeni runtime bağımlılığı YOK (R/statsmodels
kullanılmadı).
```

**17.6.9 Durum ve Implementation Evidence**

```
LOCKED VE IMPLEMENTED + TESTED (aynı combined delivery). §28.N — 18/18.
Production: src/crypto_quant_lab/validation/multiple_testing.py (YENİ).
Test: tests/test_validation_multiple_testing.py (YENİ) — 41 test, tümü
PASS. İlgili regression suite'ler (pbo, deflated_sharpe, trial_group,
return_matrix, candidate, metrics — 653 test) DEĞİŞMEDEN yeşil; tam
suite 2285/2285 PASS (2244 önceki + 41 yeni).
```

**17.6.10 Multiple-Testing Başlığında Kalan Somut Bağımlılıklar**

```
1. Geçerli p-değeri üretimi: hangi test istatistiği (örn. Sharpe için
   bir t/PSR tabanlı test), hangi null hipotez, tek/çift yön, bağımlı
   gözlemler (otokorelasyon) ve çok pencereli birleştirme kararı.
2. Aile kapsamı politikası: bir ailenin hangi aday/pencere/ölçüm
   hipotezlerinden oluştuğu ve kaydedilmemiş/başarısız denemelerin
   nasıl ele alınacağı (n > m seçeneği bilinçli olarak kapalı).
3. Sonuç seçimi/raporlama politikası: hangi düzeltilmiş sonuçların
   hangi karara girdi olacağı (şu an hiçbirine girmez).
```

### 17.7 Parameter Stability — LATER IN FAZ 6

Prerequisites: parameterized candidate abstraction + komşu parametre konfigürasyonları + stabil bir evaluation metriği. Parameterized candidate abstraction'ın kendisi artık mevcuttur (Bölüm 18 `Candidate`, IMPLEMENTED + TESTED) — ama "komşu parametre konfigürasyonları" üretimi (bir parametre-uzayı arama/iterasyon mekanizması) ve stabilite metriği HENÜZ MEVCUT DEĞİLDİR; mevcut `BacktestPolicy`'ler de parametrik değildir (`BACKTEST_SPEC.md` Bölüm 12: Faz 4 policy'leri kasıtlı olarak trivial/deterministic). Parameter optimizer burada **icat edilmez.**

## 18. Candidate / Trial Abstraction — Exact Contract (LOCKED — IMPLEMENTED + TESTED)

**Durum: LOCKED VE artık İMPLEMENT EDİLMİŞ + TEST EDİLMİŞTİR** (`Candidate`, `Trial`, `ParameterValue` — `src/crypto_quant_lab/validation/candidate.py`; regression suite'i `tests/test_validation_candidate.py` — 148 test, tümü PASS; bkz. Bölüm 18.14, 23, 28.G — 25/25). Bu bölüm, mevcut `BacktestPolicy`/`PolicyContext` (Faz 4), `WindowResult`/zero-context+non-zero-context Layer-2 runner'ları (Bölüm 8.3.6, 8.3.16), ve Stage-1/Stage-2 metrics'in (Bölüm 15) kaynak kodundan doğrudan doğrulanmış bir source-preflight'e dayanır.

**Zorunlu prensip (LOCKED, ne zaman implement edilirse edilsin geçerli, DEĞİŞMEDEN korunur):** candidate selection **yalnızca IS**'i kullanabilir. Bir OOS sonucu, **o sonucu üreten aynı candidate'in seçimine** asla geri besleme yapamaz (Bölüm 12, Bölüm 20). Bu prensip, aşağıdaki §18.7'nin de açıkça kaydettiği gibi, **research-process disiplinidir — bu bölümün kilitlediği value object'ler bunu mekanik olarak enforce ETMEZ** (Bölüm 19 ile aynı engine-vs-process ayrımı).

### 18.1 Source-Preflight Bulguları

```
- BacktestPolicy (backtest/policy.py): structural Protocol,
  target_position(context: PolicyContext) -> PositionTarget. Hiçbir
  __init__ şekli dayatılmaz — policy inşası zaten policy_factory:
  Callable[[], BacktestPolicy] convention'ıyla (rolling.py, HER İKİ
  runner'da da) caller'a bırakılmıştır.
- WindowResult (window: TemporalWindow, result: BacktestResult) —
  "no candidate identity" (Bölüm 28.C kaynak docstring'i) zaten
  açıkça kilitli; bu bölüm bunu DEĞİŞTİRMEZ.
- run_rolling_backtest_from_store / run_context_aware_rolling_backtest_from_store
  (Bölüm 8.3.6, 8.3.16) zaten policy-instance-freshness'i (factory
  per-window, object-identity reuse detection, strong retention)
  kanıtlanmış şekilde enforce eder — bu bölüm bu mekanizmayı
  TEKRARLAMAZ, yalnızca REUSE eder.
- BacktestResult (backtest/models.py) exchange/market_type/symbol/
  timeframe/as_of_time/cost_model/funding_model TAŞIMAZ — yalnızca
  initial_cash/final_cash/final_equity/... ve equity_curve. Bu nedenle
  bu provenance alanları WindowResult/BacktestResult'tan KURTARILAMAZ
  (recoverable DEĞİLDİR) — ayrı olarak saklanmaları gerekir (bkz.
  §18.6).
- compute_stage1_metrics / compute_periodic_returns / compute_stage2_metrics
  (metrics.py) yalnızca result.equity_curve + result.initial_cash
  tüketir; hiçbiri candidate/trial kavramından haberdar DEĞİLDİR ve
  olmak ZORUNDA DEĞİLDİR — WindowResult.result üzerinde bağımsız
  çalışmaya devam ederler (Bölüm 15.8, 15.18).
- pyproject.toml: hiçbir runtime dependency yok (yalnızca pytest/ruff
  dev). Candidate/trial STANDART KÜTÜPHANE DIŞINDA hiçbir şey
  gerektirmez.
- Repository-wide grep: "candidate"/"trial"/"optimizer"/"selection"/
  "score"/"rank"/"grid"/"search" — production kodunda GERÇEK bir
  candidate/trial/optimizer implementasyonu YOKTUR; tüm eşleşmeler
  "grid-aligned" (timeframe grid'i) veya WindowResult'ın kendi
  "no candidate identity" docstring'i gibi ilgisiz/negatif atıflardır.
  Bu, tamamen greenfield bir tasarım alanıdır.
```

### 18.2 Gerekli Data Flow (Kaynak Koddan Doğrulanmıştır)

```
explicit candidate tanımı (Candidate — bu bölüm)
  -> policy builder: Callable[[Candidate], Callable[[], BacktestPolicy]]
     (caller-yazılı, YENİ bir public sembol DEĞİL — bkz. §18.6)
  -> policy_factory: Callable[[], BacktestPolicy] (mevcut convention)
  -> pencere başına taze policy (mevcut rolling runner'ların kendi
     freshness mekanizması, DEĞİŞMEDEN)
  -> zero-context VEYA context-aware rolling evaluation (mevcut,
     DEĞİŞMEDEN runner'lar)
  -> tuple[WindowResult, ...] (mevcut, DEĞİŞMEDEN)
  -> Trial (bu bölüm) — candidate + o tuple + provenance
  -> bağımsız Stage-1/Stage-2 metrics (mevcut, DEĞİŞMEDEN, trial.results[i].result üzerinden)
  -> [GELECEK, bu kontratın DIŞINDA] candidate karşılaştırma/seçim/optimizer/search
```

Açıkça ayrıştırılan sorumluluklar:

```
- Candidate tanımı           : bu bölüm (§18.3-18.5) — pure data
- Runtime policy instance     : mevcut BacktestPolicy (Faz 4), DEĞİŞMEDEN
- Policy factory/builder      : caller convention (§18.6), YENİ sembol DEĞİL
- Tek pencere execution       : mevcut run_backtest_from_store, DEĞİŞMEDEN
- Multi-window trial evidence : bu bölüm (§18.4) — Trial
- Pencere-başı metrics        : mevcut metrics.py, DEĞİŞMEDEN, bağımsız
- Gelecekteki aggregation     : bu kontratın DIŞINDA (§18.9)
- Gelecekteki optimizer/search: bu kontratın DIŞINDA (§18.9)
- Final untouched test        : bu kontratın DIŞINDA (§18.9)
```

Candidate/trial, bu sorumlulukları TEK bir opaque objede BİRLEŞTİRMEZ.

### 18.3 Seçilen Mimari ve Reddedilen Alternatifler

**Candidate temsili:** Candidate, **explicit identifier + immutable parameter metadata** taşıyan frozen/slotted bir value object'tir (Bölüm 5.1 seçenek 2+6+8+10) — ne bir policy factory/callable (seçenek 3), ne bir concrete policy instance (seçenek 4), ne bir Protocol (seçenek 5).

```
Reddedilenler:
1. Yalnızca stable identifier (parametre metadata'sı YOK) — REDDEDİLDİ:
   "Explicit provenance" ve "reproducible candidate identity" öncelikleri
   ihlal edilirdi; hangi parametrelerin değerlendirildiği auditable
   olmazdı.
3. Candidate bir policy factory/callable saklar — REDDEDİLDİ: "No
   callable equality/hash ambiguity" önceliğini doğrudan ihlal eder —
   fonksiyon objelerinin equality/hash semantics'i güvenilir/reproducible
   DEĞİLDİR.
4. Candidate concrete bir policy instance saklar — REDDEDİLDİ: "Do not
   store a live policy instance in an immutable candidate" strong
   preference'ı; bir policy instance genellikle mutable internal state
   taşıyabilir (Type-I, Bölüm 8.3.5) — immutable bir value object'in
   İÇİNE mutable state sızdırmak candidate'i frozen olmaktan çıkarır.
5. Candidate bir Protocol'dür — REDDEDİLDİ: candidate PURE DATA'dır,
   pluggable BEHAVIOR değildir (BacktestPolicy'nin Protocol olma
   gerekçesinden FARKLI bir kategori) — repo convention'ı (TemporalWindow,
   WindowResult, Stage1Metrics, Stage2Metrics, ContextAwareWindow) zaten
   pure-data value object'leri hep concrete frozen/slotted dataclass
   olarak modeller, hiçbirini Protocol yapmaz.
7. Parametreler mutable mapping (dict) — REDDEDİLDİ: "Prefer immutable
   canonical parameter data over a mutable mapping" strong preference'ı;
   dict hem mutable hem hashable değildir.
9. Candidate identity parametrelerin otomatik hash'inden türetilir —
   REDDEDİLDİ: auto-derived bir hash, insan-okunabilir/auditable
   DEĞİLDİR ve sessiz bir çakışma riskini gizleyebilir — Bölüm 8.3.13'ün
   "Explicit, Auto-Inference YOK" prensibiyle aynı gerekçeyle reddedilir.
```

**Parametre domain'i:** deliberately NARROW bir scalar domain (Bölüm 5.2) — `bool`, `int` (non-bool), finite `Decimal`, `str`, `None`, ve bunların recursive `tuple`'ları. `Any`/arbitrary object/JSON-like recursive value/list/dict **REDDEDİLDİ** — repo'nun mevcut Decimal-exclusive, no-NumPy/pandas prensibiyle (Bölüm 27) ve "immutable canonical parameter data" strong preference'ıyla tutarlı. Float **REDDEDİLDİ** — repo'da hiçbir güvenli float->Decimal canonicalization kontratı yoktur.

**Trial temsili:** bir candidate × ordered `tuple[WindowResult, ...]` (Bölüm 5.3 seçenek 2+5+8+10+12-kısmi), **yalnızca fully-successful evaluation için** inşa edilebilir (seçenek 8) — raw sonuçlar saklanır, metrikler HARİCEN türetilir (seçenek 5), policy factory SAKLANMAZ (seçenek 6 reddedildi — aynı callable-hash gerekçesiyle), ve **explicit bir selection/test role alanı bu kontrata DAHİL EDİLMEZ** (seçenek 10 — bkz. §18.7'nin gerekçesi).

```
Reddedilenler:
1. Bir candidate x TEK bir WindowResult — REDDEDİLDİ: mevcut rolling
   runner'ların KENDİ çıktısı zaten tuple[WindowResult, ...]'tur;
   trial'ı tek-sonuca sınırlamak bu şekli yapay olarak parçalar.
3. Trial yalnızca metrikleri saklar — REDDEDİLDİ: "Keep raw evaluation
   evidence available; do not preserve only a derived score" strong
   preference'ı.
6. Trial policy factory'yi saklar — REDDEDİLDİ (yukarıda gerekçeli).
7-8. Trial execution errors/partial-failure saklar / yalnızca
   fully-successful evaluation için var olur — KISMEN SEÇİLDİ: 8
   LOCKED'dır (yalnızca fully-successful), 7 REDDEDİLDİ — failure bir
   Trial objesi olarak DEĞİL, raise ile temsil edilir (mevcut rolling
   runner'ların KENDİ "no partial result, raise on failure" prensibiyle
   birebir tutarlı).
9. Trial explicit bir evaluation-purpose/role (selection/test) taşır —
   REDDEDİLDİ bu foundation'da (bkz. §18.7 — bu, YANLIŞ bir güvenlik
   iddiası yaratırdı).
11 (vs 12). Trial ayrıca `as_of_time`/exchange/market_type/symbol/
   timeframe/config saklar (yalnızca window boundary'leri
   WindowResult'tan zaten kurtarılabilir olduğu için KISMİ olarak 12) —
   bkz. §18.6: bu alanlar BacktestResult'tan KURTARILAMAZ, bu yüzden
   AYRICA saklanmaları LOCKED'dır.
13. Ayrı TrialSpec + TrialResult — REDDEDİLDİ: bu foundation'da
   pre-execution bir "spec" objesine ihtiyaç yoktur (persistence/queue
   kapsam dışı, Bölüm 27); TEK compact bir post-execution value
   object (14) yeterlidir.
```

**Execution composition:** Candidate/Trial **yalnızca value object'lerdir** — bu kontrat (ve bir sonraki implementasyon) **hiçbir evaluator fonksiyonu içermez** (Bölüm 5.4 seçenek 1). Caller, mevcut `run_rolling_backtest_from_store` veya `run_context_aware_rolling_backtest_from_store`'u DOĞRUDAN, KENDİSİ çağırır; bu bölüm yalnızca sonucu (candidate + tuple[WindowResult,...] + provenance) bir `Trial`'a NASIL paketleyeceğini kilitler.

```
Reddedilenler:
2, 3, 4, 5. Bir candidate-evaluation fonksiyonu (runner-agnostic veya
   zero-context/context-aware için ayrı ayrı) — REDDEDİLDİ (bu adımda):
   "Minimal API surface" ve "No speculative framework" öncelikleri;
   böyle bir fonksiyon HER İKİ mevcut runner'ı doğru şekilde
   dispatch etmek, freshness'i yeniden kanıtlamak, ve kendi geniş
   regression suite'ine ihtiyaç duyardı — bu, "foundation" kapsamının
   ÖTESİNE geçer. "Do not silently dispatch based on window type"
   instruction'ı ile seçenek 4 zaten AÇIKÇA yasaktır.
6. Hidden global candidate/factory registry — REDDEDİLDİ: "No hidden
   global registry" önceliği.
8-10. Candidate builder pencere başına çağrılır / candidate kendisi
   construction behavior'ı sahiplenir — REDDEDİLDİ: mevcut
   policy_factory zaten pencere başına ÇAĞRILIR (Bölüm 8.3.6) — bunu
   Candidate seviyesinde TEKRARLAMAK gereksiz bir katman eklerdi.
```

**Modül yerleşimi:** **yeni, dedicated bir modül** — `src/crypto_quant_lab/validation/candidate.py` (Bölüm 5.5) — mevcut `windows.py`/`rolling.py`/`metrics.py` ile AYNI tek-kavram-per-modül convention'ı. `rolling.py`/`metrics.py`/`windows.py`'a EKLENMEZ (candidate identity'yi replay/rolling/metrics internals'ına couple ederdi); yeni bir "optimizer" modülü de AÇILMAZ (bu kontratın kapsamı DIŞINDA, Bölüm 5.5, 18.9).

### 18.4 Terminoloji — Kesin Tanımlar

```
Candidate: pure, immutable bir TANIM — bir explicit identifier +
  immutable parameter metadata kombinasyonu.

Candidate DEĞİLDİR:
  - bir policy instance (Bölüm 8.3.5'in Type-H/Type-I ayrımı hâlâ
    geçerlidir — Candidate hiçbir mutable policy state taşımaz)
  - bir trial
  - bir score
  - seçilmiş bir kazanan
  - (explicitly ve güvenli tasarlanmadıkça) bir trained model state
  - bir optimizer talimatı

Trial: bir candidate'in, ordered bir pencere koleksiyonu üzerindeki,
  TEK bir başarılı, immutable evidence bundle'ı.

Trial DEĞİLDİR:
  - bir ranking
  - bir aggregate score
  - bir selection kararı
  - final bir holdout iddiası
  - (explicitly seçilmedikçe) bir failure log'u — failure bir Trial
    objesi DEĞİL, bir raise'dir (bkz. §18.8)
  - mutable bir workspace
```

### 18.5 Exact Public API (LOCKED)

```
Modül:  src/crypto_quant_lab/validation/candidate.py  (YENİ modül)

ParameterValue = bool | int | Decimal | str | None | tuple["ParameterValue", ...]

@dataclass(frozen=True, slots=True)
class Candidate:
    candidate_id: str
    parameters: tuple[tuple[str, ParameterValue], ...]

@dataclass(frozen=True, slots=True)
class Trial:
    candidate: Candidate
    results: tuple[WindowResult, ...]
    exchange: str
    market_type: str
    symbol: str
    timeframe: str
    as_of_time: datetime
    config: BacktestConfig
```

```
- Field sırası yukarıdaki gibi TAM OLARAK kilitlidir.
- Her iki tip de frozen, slotted — mevcut convention.
- Equality/hash: frozen-dataclass default'ları, TÜM field'lar üzerinden
  (custom __eq__/__hash__/__order__ YOK).
- Package-root export YOK — validation/__init__.py DEĞİŞMEZ (mevcut
  zero-re-export convention'ıyla tutarlı).
- Bu kontratın bir parçası olarak HİÇBİR evaluator fonksiyonu
  KİLİTLENMEZ (bkz. §18.3 execution composition).
- Rolling runner'lara (her ikisine de) İLİŞKİ: yalnızca dolaylı — bir
  caller, mevcut runner'lardan birini KENDİSİ çağırır ve dönen
  tuple[WindowResult, ...]'ı bir Trial'a paketler; candidate.py bu
  runner'ları İMPORT ETMEZ dışında WindowResult tipini kullanmak için
  (bkz. §18.10 import direction).
- metrics.py'ye İLİŞKİ: YOK — candidate.py hiçbir metrics sembolü
  import ETMEZ; Stage-1/Stage-2 metrikleri trial.results[i].result
  üzerinde, caller tarafından, bağımsız olarak çağrılır (bkz. §18.6).
```

### 18.6 Candidate Identity ve Parametre Domain'i (LOCKED)

**`candidate_id`:**

```
- Tip: str; değilse TypeError.
- Boş string → ValueError.
- Yalnızca whitespace → ValueError.
- Baştaki/sondaki whitespace padding → ValueError (sessizce strip
  EDİLMEZ — "reject, don't repair" prensibi, bu dokümanın her yerinde
  zaten kilitli).
- Case-sensitive; hiçbir case-folding YAPILMAZ.
- Unicode: Python str semantics'i aynen kullanılır; ek bir normalization
  (NFC/NFKC vb.) UYGULANMAZ.
- Maksimum/minimum uzunluk kısıtlaması YOKTUR (kanıtlanmamış bir
  kısıtlama İCAT EDİLMEZ).
- Global benzersizlik ENFORCE EDİLMEZ — bu foundation'da candidate'leri
  toplayan bir collection/registry objesi YOKTUR (Bölüm 5.5, "no hidden
  global registry"); tek bir Candidate objesi başka candidate'lerin
  varlığından HABERDAR DEĞİLDİR. Birden fazla candidate arası
  benzersizlik, ileride bir orchestration/registry kontratının işidir.
- candidate_id equality/hash'e DAHİLDİR (iki candidate, aynı
  parametrelere sahip olsa bile farklı ID'lerle EŞİT SAYILMAZ).
```

**Parametre anahtarları (`parameters[i][0]`):**

```
- Tip: str; değilse TypeError, index-specific.
- Boş/yalnızca-whitespace/padded → ValueError, index-specific (candidate_id
  ile AYNI kural).
- Case-sensitive; normalization YOK.
- Dotted/nested isimler (örn. "risk.stop_loss_pct") LEGAL'dir — engine
  bunları salt opaque string olarak görür, özel bir parsing/semantics
  İCAT EDİLMEZ.
- Duplicate key → ValueError, duplicate index'i tanımlar.
- Key sırası CANONICAL'dır (aşağıya bkz.) — semantic olarak ANLAMLI
  DEĞİLDİR (yalnızca reproducibility/equality için sabit bir sıra).
```

**Parametre değerleri (`parameters[i][1]`) — exact permitted types, validation order:**

```
1. bool  -> LEGAL (int kontrolünden ÖNCE kontrol edilir — bool, int'in
            alt sınıfıdır; mevcut _require_non_negative_int
            (backtest/models.py) ile AYNI sıra/gerekçe).
2. int (bool DEĞİL) -> LEGAL, sınırsız (yapay bir aralık kısıtlaması
            İCAT EDİLMEZ).
3. Decimal -> yalnızca finite ise LEGAL; NaN/+Infinity/-Infinity ->
            ValueError (Stage-1/Stage-2'nin is_finite() convention'ıyla
            birebir aynı).
4. str    -> LEGAL (candidate_id/key ile AYNI whitespace/case kuralları
            BURADA UYGULANMAZ — bir parametre DEĞERİ olarak bir string,
            bir İSİM değildir; içerik kısıtlaması YOKTUR).
5. None   -> LEGAL (açık bir "değer yok" skaleri; belirsiz DEĞİLDİR,
            immutable/hashable'dır).
6. tuple  -> LEGAL yalnızca HER elemanı recursive olarak bu AYNI 6
            kuralı sağlıyorsa (nested tuple'lar desteklenir).
7. float  -> REDDEDİLDİ (TypeError) — repo'nun Decimal-exclusive,
            no-float prensibiyle (Bölüm 27) tutarlı; hiçbir sessiz
            float->Decimal coercion YAPILMAZ.
8. list/dict/set (mutable container) -> REDDEDİLDİ (TypeError).
9. Herhangi bir başka custom/arbitrary object -> REDDEDİLDİ (TypeError).
```

**Canonical representation (LOCKED — seçenek: reddet, sessizce sıralama):**

```
- parameters, KESİNLİKLE ascending lexicographic key sırasında
  (Python'un default str '<' karşılaştırması, codepoint-tabanlı;
  locale-aware bir karşılaştırma KULLANILMAZ) OLMALIDIR.
- Sıra ihlali → ValueError, ihlalin index'ini tanımlar — sessizce
  SIRALANMAZ.
- Bu, bu dokümanın HER YERİNDE zaten kilitli "reject invalid/
  non-canonical input, don't silently repair" prensibinin (Bölüm 6, 7,
  15.4, 15.15, 8.3.16 vb.) candidate/trial'a doğrudan uzantısıdır.
```

### 18.7 Trial Evidence, Provenance, ve Evaluation-Role (LOCKED)

**`results` (`tuple[WindowResult, ...]`):**

```
- Boş tuple → ValueError (bir trial en az bir WindowResult İÇERMELİDİR
  — sıfır sonuçlu bir "trial," hiçbir şeyin kanıtı değildir).
- Her eleman bir WindowResult olmalıdır; değilse TypeError,
  index-specific.
- Input sırası KORUNUR — sıralama YOK.
- Duplicate/overlapping WindowResult.window değerleri LEGAL'dir,
  REDDEDİLMEZ/dedupe edilmez — mevcut rolling runner'ların KENDİ
  kilitli "no overlap rejection" prensibinin (Bölüm 8.3.16 madde 16)
  birebir uzantısıdır.
- Mutasyon/kopyalama: results tuple'ı KOPYALANMAZ (tuple zaten
  immutable'dır); Trial kendisi hiçbir elemanı DEĞİŞTİRMEZ.
```

**Provenance (`exchange`, `market_type`, `symbol`, `timeframe`, `as_of_time`, `config`):**

```
- BacktestResult HİÇBİR exchange/market_type/symbol/timeframe/
  as_of_time/cost_model/funding_model alanı TAŞIMAZ (§18.1 kaynak
  bulgusu) — bu nedenle bu alanlar WindowResult/BacktestResult'tan
  KURTARILAMAZ ve Trial'da AYRICA, explicit olarak saklanmaları
  LOCKED'dır (reproducibility/equality/comparison için gerekli asgari
  provenance).
- Pencere boundary'leri (evaluation window) AYRICA saklanmaz — zaten
  her results[i].window üzerinden kurtarılabilir (Bölüm 8.3.16'nın
  "context_start yalnızca caller'ın kendi orijinal input'u üzerinden
  auditable kalır" precedent'iyle aynı ilke: yalnızca gerçekten
  kurtarılamayan veri ayrıca saklanır).
- cost_model, funding_model, funding_store, ve candle store'un
  KENDİSİ Trial'da SAKLANMAZ — bunlar (a) çoğu zaman arbitrary/
  Protocol-tipli objelerdir ve equality/hash semantics'leri güvenilir
  DEĞİLDİR ("no callable equality/hash ambiguity" ile aynı gerekçe,
  cost/funding model'lere genişletilmiş), (b) canlı I/O kaynaklarıdır,
  immutable bir value object'e ait DEĞİLDİR. Hangi exact cost/funding
  modelinin kullanıldığının tam reproducibility'si, bu foundation'ın
  AÇIKÇA DIŞINDA bırakılır — gelecekteki bir experiment-tracking/
  persistence katmanının (Bölüm 27, kapsam dışı) sorumluluğudur.
- Trial.candidate bir Candidate olmalıdır; değilse TypeError.
- exchange/market_type/symbol/timeframe: candidate_id ile AYNI
  non-empty/non-whitespace/no-padding kuralı.
- as_of_time: genuine aware datetime (mevcut datetime_to_epoch_us
  reuse edilir).
- config: bir BacktestConfig olmalıdır; değilse TypeError. config'in
  KENDİ iç invariant'ları (initial_cash/position_quantity) TEKRAR
  DOĞRULANMAZ — zaten kendi __post_init__'inde kanıtlanmıştır (mevcut
  "lower-layer invariant'lara güven" prensibi).
- Mekanik olarak kontrol edilebilen TEK cross-result consistency
  invariant'ı: her results[i].result.initial_cash == config.initial_cash
  olmalıdır; değilse index-specific ValueError. exchange/market_type/
  symbol/timeframe homojenliği (results'un GERÇEKTEN aynı partition'dan
  geldiği) BacktestResult'ın kendisi bu bilgiyi taşımadığı için
  MEKANİK OLARAK DOĞRULANAMAZ — bu, Bölüm 8.3.5'in Type-H mekanik
  olarak enforce edilemezliğiyle AYNI kategoride, açıkça kaydedilen bir
  trust boundary'dir.
- Trial/candidate consistency (bu results'un GERÇEKTEN bu candidate'in
  policy'siyle üretildiği) de MEKANİK OLARAK DOĞRULANAMAZ — WindowResult
  hiçbir candidate/policy referansı TAŞIMAZ (kasıtlı olarak, Bölüm
  28.C). Bu, açık bir caller-disiplini sorumluluğudur.
```

**Evaluation-role/provenance kararı (LOCKED — KRİTİK):**

```
Bu foundation, Trial'a explicit bir selection/test role alanı
EKLEMEZ.
```

**Gerekçe:** Bölüm 19 zaten "peeking" (bir araştırmacının/LLM'in candidate'i dondurmadan önce OOS metriklerini görmesi) riskini **research-process disiplini** olarak kilitlemiştir — **engine-enforceable DEĞİL**. Bir `role: Literal["selection", "test"]` alanı eklemek, MEKANİK OLARAK HİÇBİR ŞEYİ enforce ETMEZ (hiçbir caller, kendi kendine bildirdiği bu alanı doğru doldurmaya ZORLANAMAZ) — bu, gerçekte var olmayan bir engine-seviyeli koruma **YANLIŞ İZLENİMİNİ** yaratırdı; tam olarak bu dokümanın her yerinde kaçınılan "false safety claim" hatasıdır. Bölüm 20'nin ("Multiple Testing — Kayıt Prensibi") kendisi de bunu yalnızca gelecekteki bir "kayıt" (implementasyon değil, prensip) olarak çerçeveler.

**Bu nedenle:** selection/test role, bu foundation'ın **tamamen DIŞINDadır** ve gelecekteki bir orchestration kontratına ERTELENİR. Bu Candidate/Trial abstraction'ı, **hiçbir şekilde train/select/test ayrımını sağlamaz, final holdout'u korumaz, veya "peeking"i engellemez** — §18.9'un açıkça kaydettiği gibi.

### 18.8 Validation / Fail-Fast Sırası (LOCKED, exact)

**`Candidate.__post_init__`:**

```
1. candidate_id str olmalı; değilse TypeError.
2. candidate_id boş/whitespace-only/padded olmamalı; değilse ValueError.
3. parameters bir tuple olmalı; değilse TypeError.
4. Her eleman TAM OLARAK 2 uzunluklu bir tuple olmalı; değilse
   TypeError, index-specific.
5. Her key (element[0]) str olmalı; değilse TypeError, index-specific.
6. Her key boş/whitespace-only/padded olmamalı; değilse ValueError,
   index-specific.
7. Duplicate key YOK; değilse ValueError, duplicate index'i tanımlar.
8. Key'ler strictly ascending lexicographic sırada; değilse ValueError,
   ihlal index'i.
9. Her value (element[1]) §18.6'nın kilitli 6 legal tipinden biri
   olmalı (bool/int/Decimal-finite/str/None/recursive-tuple); değilse
   TypeError (yanlış tip: float, list, dict, custom object) veya
   ValueError (Decimal non-finite) — index-specific (nested tuple'lar
   için üst-seviye parametre index'i raporlanır).

Yalnızca TÜM adımlar geçtikten SONRA candidate "geçerli" sayılır.
```

**`Trial.__post_init__`:**

```
1. candidate bir Candidate olmalı; değilse TypeError.
2. results bir tuple olmalı; değilse TypeError.
3. results boş OLMAMALI; değilse ValueError.
4. Her eleman bir WindowResult olmalı; değilse TypeError, index-specific.
5. exchange/market_type/symbol/timeframe str, non-empty/non-whitespace/
   unpadded olmalı; değilse TypeError/ValueError.
6. as_of_time genuine aware datetime olmalı; değilse TypeError/ValueError.
7. config bir BacktestConfig olmalı; değilse TypeError (config'in KENDİ
   iç invariant'ları TEKRAR doğrulanmaz).
8. Her results[i].result.initial_cash == config.initial_cash olmalı;
   değilse ValueError, index-specific.

Yalnızca TÜM adımlar geçtikten SONRA trial "geçerli" sayılır.
```

```
Hiçbir validation sessizce:
- candidate_id/key'i strip/case-fold ETMEZ
- parametreleri SIRALAMAZ
- duplicate key/window'ı DEDUPE ETMEZ
- float'ı Decimal'e COERCE ETMEZ
- geçersiz bir değeri bir default ile DEĞİŞTİRMEZ
- fail'den SONRA partial evidence DÖNDÜRMEZ
- lower-layer bir hatayı yutup yanıltıcı bir "başarılı" trial'a
  DÖNÜŞTÜRMEZ
```

### 18.9 Leakage / Selection Safety — Açık Sınırlar (LOCKED, KRİTİK)

**Bu candidate/trial foundation'ı, TEK BAŞINA, AŞAĞIDAKİLERİN HİÇBİRİNİ SAĞLAMAZ:**

```
- train/select/test data split'i
- en iyi candidate'in seçilmesi
- final bir holdout'un korunması
- multiple-testing correction'ı
- bir caller'ın çok fazla candidate değerlendirmesinin engellenmesi
- purging/embargo
- CPCV
- Deflated Sharpe
- PBO
- parameter stability
- metriklerin annualize edilmesi
- cross-window performance aggregation'ı
```

Bu foundation **yalnızca bir candidate'in TANIMINI ve TEK bir başarılı çok-pencereli evaluation'ının HAM kanıtını** value object olarak sabitler. §18.7'nin kilitlediği gibi, hiçbir evaluator fonksiyonu bu kontratın parçası DEĞİLDİR; bu nedenle §11'deki "If an evaluator is selected" gereksinimleri (tek candidate/call, rank/compare yapmama, vb.) bu mikro-adımda **N/A**'dır — bunlar, bir evaluator fonksiyonu GELECEKTE seçilirse o kontratın kendi sorumluluğu olacaktır.

**Yalnızca value object'ler seçildiği için:**

```
- Caller'lar bunları MEVCUT runner'larla şöyle compose eder: (1) bir
  Candidate inşa et; (2) candidate'ten bir policy_factory üret (caller
  kendi kodu, YENİ bir sembol DEĞİL); (3) mevcut
  run_rolling_backtest_from_store veya
  run_context_aware_rolling_backtest_from_store'u DOĞRUDAN çağır; (4)
  dönen tuple[WindowResult, ...]'ı, candidate + provenance ile birlikte
  bir Trial'a paketle; (5) Stage-1/Stage-2 metriklerini
  trial.results[i].result üzerinde BAĞIMSIZ olarak hesapla.
- Multi-candidate selection'a güvenle geçmeden ÖNCE, gelecekteki bir
  kontrat şunları AÇIKÇA eklemelidir: (a) bir selection/test role veya
  eşdeğer bir IS/OOS ayrım mekanizması, (b) trial-count tracking
  (Bölüm 20), (c) bir explicit selection-metric/rule, (d) multiple-
  testing correction'ının nasıl uygulanacağı (Bölüm 17.6), (e) mevcutsa
  bir final-holdout execution kontratı.
```

### 18.10 Purity, Determinism, ve Import Direction (LOCKED)

```
- Candidate/Trial construction girdi objelerini MUTATE ETMEZ.
- Candidate parametreleri immutable'dır (tuple-of-tuples, yalnızca
  hashable skaler/nested-tuple değerler).
- Trial evidence immutable'dır (results tuple'ı olduğu gibi saklanır,
  kopyalanmaz, değiştirilmez).
- Eşit girdilerden tekrar construction, eşit değerler ÜRETİR (frozen
  dataclass default equality).
- Hash davranışı, kilitli değer domain'i içinde STABIL'dir — hem
  Candidate hem Trial, mevcut WindowResult/BacktestResult/
  BacktestConfig'in ZATEN hashable olduğu ampirik olarak doğrulanarak
  (bu preflight'te), frozen-dataclass default'ları üzerinden hashable'dır.
- Wall-clock/randomness bağımlılığı YOK.
- Hidden global registry YOK.
- Validation için ambient Decimal-context bağımlılığı YOK (yalnızca
  is_finite() kontrolü — Stage-1/Stage-2'nin AKSİNE, burada hiçbir
  arithmetic YOK, dolayısıyla hiçbir private Decimal context'e de
  ihtiyaç YOK).
- Hiçbir policy instance beklenmedik şekilde saklanmaz/reuse edilmez.
- BacktestResult, WindowResult, TemporalWindow, TemporalSplit,
  ContextAwareWindow, her iki rolling runner, ve metrics.py'nin tamamı
  DEĞİŞMEDEN kalır.
- Package export'ları DEĞİŞMEDEN kalır (validation/__init__.py
  dokunulmaz).
- Hiçbir import cycle YOK.
```

**Import direction (LOCKED):**

```
crypto_quant_lab.validation.candidate  (YENİ modül)
  imports:
    crypto_quant_lab.backtest.models        (BacktestConfig)
    crypto_quant_lab.validation.rolling      (WindowResult — yalnızca value-model tipi için)
    crypto_quant_lab.storage.sqlite_codec    (datetime_to_epoch_us)
    decimal, dataclasses, datetime  (stdlib)

crypto_quant_lab.validation.rolling      <- candidate.py'yi İMPORT ETMEZ
crypto_quant_lab.validation.metrics      <- candidate.py'yi İMPORT ETMEZ
crypto_quant_lab.validation.windows      <- candidate.py'yi İMPORT ETMEZ

Sonuç: candidate.py, rolling.py'ye bağımlıdır (tek yönlü); rolling.py,
metrics.py, ve windows.py candidate.py'den TAMAMEN BAĞIMSIZDIR — hiçbir
döngü YOK. candidate.py hiçbir optimizer/search kodu İMPORT ETMEZ (böyle
bir kod bu kontrat kapsamında zaten YOKTUR).

**"Yalnızca tip için" ifadesinin netleştirilmesi (implementasyon closure'ı ile eklendi):** `Trial.__post_init__`'in `results` elemanlarını `isinstance(result, WindowResult)` ile mekanik olarak doğrulayabilmesi için `WindowResult`'ın runtime'da import edilmesi ZORUNLUDUR ve LEGALDİR — bu, `candidate.py`'nin `rolling.py`'nin bir value-model SEMBOLÜNE bağımlı olduğu anlamına gelir, `rolling.py`'nin RUNNER FONKSİYONLARINI (`run_rolling_backtest_from_store`, `run_context_aware_rolling_backtest_from_store`) veya execution davranışını import/invoke ettiği anlamına GELMEZ. "Tip için" ifadesi bu ayrımı ifade eder: `candidate.py` yalnızca `WindowResult` SINIFINI (isinstance kontrolü ve field-okuma için) kullanır — hiçbir rolling orchestration mantığı candidate.py'ye sızmaz (implementasyonun `git diff` ve statik import-satırı kanıtıyla doğrulanmıştır, bkz. 28.G kriter 24).
```

### 18.11 Gelecekteki İmplementasyon İçin Dosya Kapsamı (planlama bilgisi — şimdi değiştirilmez)

```
Yeni production dosyası: src/crypto_quant_lab/validation/candidate.py
  (Candidate, ParameterValue type alias, Trial).
Yeni test dosyası: tests/test_validation_candidate.py (mevcut
  test_validation_windows.py/test_validation_rolling_backtest.py/
  test_validation_metrics.py ile AYNI, tek-modül-per-test-dosyası
  convention'ı).
Değiştirilecek mevcut production dosyası: YOK (rolling.py, metrics.py,
  windows.py, models.py, policy.py DOKUNULMAZ).
Documentation (combined closure için): VALIDATION_SPEC.md.
Açıkça YASAK: ROADMAP.md, pyproject.toml, backtest/ altındaki her şey,
  windows.py, rolling.py, metrics.py, herhangi bir __init__.py, herhangi
  bir başka production/test/spec/status dosyası.
```

### 18.12 Test Kontratı (implementasyon mikro-adımı için gerekli minimum matris)

```
- Candidate value semantics: valid construction (parametreli VE boş
  parametre seti ile), field preservation, equality, hashability,
  frozen/slotted, geçersiz candidate_id (tip/boş/whitespace/padding),
  geçersiz parameters koleksiyon tipi, geçersiz parametre elemanı
  (index 0 VE later index), geçersiz key (tip/boş/whitespace/padding),
  duplicate key, canonical-order ihlali, HER izin verilen value tipi
  (bool, int, Decimal-finite, str, None, nested tuple), bool/int
  ayrımının sırası, NaN/+Infinity/-Infinity Decimal reddi, float
  reddi, mutable-container reddi (list/dict/set), custom-object reddi,
  sessiz coercion/normalization YOKLUĞU, eşzamanlı çoklu ihlalin exact
  sırayı kanıtlaması.
- Trial semantics: valid construction, candidate preservation, result
  preservation, equality/hash, frozen/slotted, boş-result reddi,
  non-tuple result koleksiyonu reddi, index-0 ve later-index geçersiz
  eleman, eşzamanlı çoklu ihlal sırası, input sırasının korunması,
  duplicate/overlapping window kabulü, result mutasyonu YOKLUĞU,
  initial_cash/config tutarsızlığı reddi, WindowResult/BacktestResult'a
  hiçbir metrics field'ı eklenmediği, implicit aggregation/ranking/score
  YOKLUĞU, failure'ın hiçbir zaman başarılı bir trial gibi
  görünemeyeceği (raise, trial DEĞİL).
- Provenance: her provenance field'ının tip/finiteness/timezone
  validation'ı, equality davranışı, mismatched provenance (initial_cash
  tutarsızlığı) davranışı, objenin KENDİSİNİN final-holdout koruması
  SAĞLAMADIĞININ açık kanıtı (yalnızca dokümantasyon/absence-of-claim
  değil, davranışsal olarak: role alanı YOK, selection engelleme kodu
  YOK).
- Static/non-coupling: package-root export YOK, result-model değişikliği
  YOK, rolling/metrics signature değişikliği YOK, optimizer/search
  import'u YOK, circular import YOK, float kabulü YOK, mutable mapping
  saklanmıyor, candidate/trial equality/hash'ine hiçbir callable
  dahil edilmiyor.

Testler WALL CLOCK/randomness/network/external service/order
dependence/mutable global fixture/float expected value KULLANMAZ; tam
production algoritmasını bir oracle olarak yeniden implement ETMEZ.
```

### 18.13 Explicit Exclusions (bu kontrat kapsamında DEĞİL, implement EDİLMEZ)

```
candidate ranking, candidate scoring aggregation, optimizer/grid/
random/Bayesian search, hyperparameter search, train/select/test
orchestration, final untouched holdout execution, purging/embargo,
CPCV, Deflated Sharpe, PBO, multiple-testing correction, parameter
stability, annualized Sharpe/Sortino/Calmar/CAGR, cross-window
aggregate metrics, portfolio construction, parallel/distributed
trials, persistence/database schemas, reporting/UI/API/CLI, paper/live
trading.
```

Bu maddeler **deferred boundary'ler** olarak kaydedilir — implement edilmiş özellikler DEĞİL.

### 18.14 Implementation Evidence (IMPLEMENTED + TESTED — combined delivery ile eklendi)

```
Production: src/crypto_quant_lab/validation/candidate.py (YENİ dosya)
  - ParameterValue = bool | int | Decimal | str | None | tuple["ParameterValue", ...]
  - Candidate (frozen, slots) — candidate_id: str, parameters: tuple[tuple[str, ParameterValue], ...]
  - Trial (frozen, slots) — candidate, results, exchange, market_type, symbol,
    timeframe, as_of_time, config (kilitli field sırasıyla, §18.5 ile birebir)
  - imports: yalnızca BacktestConfig (backtest/models), WindowResult
    (validation/rolling, yalnızca value-model tipi — §18.10), datetime_to_epoch_us
    (storage/sqlite_codec), ve stdlib (dataclasses, datetime, decimal) — §18.10'da
    LOCKED olan import listesiyle birebir, fazlası YOK.
Test: tests/test_validation_candidate.py (YENİ dosya) — 148 test, tümü PASS
  (132 orijinal combined-delivery testi + 16 yeni, fail-fast-order
  correction'ıyla eklenen global-pass-across-stages kanıtı — bkz. aşağıdaki
  düzeltme notu).
  - Candidate value semantics (valid construction, field order, equality/hash,
    frozen/slotted, deterministic construction, candidate_id validation,
    parameters yapısal validation, duplicate/canonical-order, parameter-value
    domain'inin TAMAMI dahil NaN/Infinity/float/mutable-container/custom-object
    reddi, nested-value top-level-index raporlaması, eşzamanlı çoklu ihlal
    sırası (adım 4-9'un HER BİRİNİN her diğer adımdan daha erken/daha geç
    index'te olsa bile kendinden SONRAKİ her adımı ezdiğini kanıtlayan 16
    dedicated test — bkz. düzeltme notu), purity/no-partial-construction).
  - Trial semantics (valid construction, field order, equality/hash,
    frozen/slotted, candidate/results validation, provenance validation
    (4 field x tip+boş+whitespace+padding), as_of_time (tip/naive/pseudo-naive/
    valid), config tip kontrolü, initial_cash tutarlılığı (index 0 VE later
    index), eşzamanlı çoklu ihlal sırası — adım 1'den 8'e kadar her adımın
    kendinden sonraki adımları ezdiğini kanıtlayan dedicated testler,
    duplicate/overlapping window kabulü, mutasyon/kopyalama YOKLUĞU, role/score/
    holdout field'ının absence-of-field kanıtı).
  - Static/non-coupling (locked modül yolunda sembol erişilebilirliği,
    package-root export YOKLUĞU, evaluator/optimizer sembolü YOKLUĞU, candidate.py
    kaynağında metrics/policy/rolling-runner import'u YOKLUĞU, rolling.py/
    metrics.py/windows.py kaynağında candidate import satırı YOKLUĞU, callable
    field YOKLUĞU).
  - Gerçek rolling-path entegrasyonu: `run_rolling_backtest_from_store`'dan
    üretilen gerçek bir `tuple[WindowResult, ...]`, bir Trial'a paketlenir;
    `compute_stage1_metrics` ve `compute_periodic_returns`, `trial.results[i].result`
    üzerinden BAĞIMSIZ olarak (Trial/Candidate'e hiçbir coupling olmadan)
    çağrılır. Ayrı bir sentetik equity-curve testi, `compute_stage2_metrics`'in
    de (sıfır-olmayan varyansla, Sharpe dahil) aynı şekilde bağımsız çalıştığını
    kanıtlar.
İlgili regression suite'ler (tests/test_validation_windows.py,
tests/test_validation_rolling_backtest.py, tests/test_validation_metrics.py,
tests/test_backtest_models.py, tests/test_backtest_results.py — 404 test)
DEĞİŞMEDEN yeşil kaldı; tam suite 1807/1807 PASS (1791 önceki + 16 yeni
fail-fast-order correction testi).
Post-implementation audit'i — Bölüm 18'in tüm invariant'ları ve Bölüm 28.G'nin
25 kriterinin tamamı için concrete davranışsal/static kanıt doğrulandı — PASS.
Değiştirilen mevcut production/test dosyası: YOK (rolling.py, metrics.py,
windows.py, models.py, policy.py, ve tüm mevcut testler DEĞİŞMEDEN — statik
`git diff` kanıtı + tam regression suite uyumluluğu).
```

**Candidate global fail-fast sıra düzeltmesi (corrective delivery ile eklendi — ÖNCEKİ "per-index" yorumunu DÜZELTİR):** §18.8'deki `Candidate.__post_init__` adım 4-9, HER BİRİ TÜM `parameters` girişleri üzerinden **ayrı, global bir geçiştir** — adım N'in KENDİSİ her girişte tamamlanmadan adım N+1 HİÇBİR girişi incelemez. Combined-delivery'nin ilk implementasyonu/dokümantasyonu YANLIŞLIKLA "her index için tek bir ileri-yönlü geçişte o index'e özgü tüm yapısal/key kontrollerinin sırayla uygulandığını" (adım 4-8'in per-index birleştirildiğini) belirtmişti — bu, eşzamanlı ihlallerde YANLIŞ sonuç üretebiliyordu (örn. index 0'da bir adım-5 [key tipi] ihlali VE index 1'de bir adım-4 [entry şekli] ihlali varsa, LOCKED global sıra adım-4'ün index 1'deki ihlalinin KAZANMASINI gerektirir — çünkü TÜM adım-4 kontrolleri TÜM adım-5 kontrollerinden ÖNCE tamamlanmalıdır — ama eski per-index implementasyonu yanlışlıkla index 0'daki adım-5 ihlalini raise ediyordu). Bu, `src/crypto_quant_lab/validation/candidate.py`'de düzeltildi: `Candidate.__post_init__` artık adım 4 (entry şekli), 5 (key tipi), 6 (key içeriği), 7 (duplicate key), 8 (canonical order), 9 (value domain) için ALTI AYRI, TAM `parameters` üzerinden geçen döngü kullanır — her döngü yalnızca kendi tekil kontrolünü uygular ve yalnızca bir ÖNCEKİ adımın döngüsü TÜM index'ler için hatasız tamamlandıktan SONRA başlar. 16 yeni dedicated test (`test_candidate_stage4_shape_wins_over_earlier_stage5_key_type` ve benzerleri, `tests/test_validation_candidate.py`), adım 4'ün 5-9'un HER BİRİNDEN, adım 5'in 6-9'un HER BİRİNDEN, adım 6'nın 7-9'un HER BİRİNDEN, adım 7'nin 8-9'un HER İKİSİNDEN, ve adım 8'in 9'dan — index sırasından BAĞIMSIZ olarak — HER ZAMAN önce geldiğini kanıtlar (davranışsal, exact exception type/message assertion ile, yalnızca public `Candidate` API üzerinden). `Trial.__post_init__` bu düzeltmeden ETKİLENMEMİŞTİR — Trial zaten §18.7'de listelenen 8 adımı tek bir sıralı, tek-alan-bazlı geçişte uygular (Candidate'in `parameters` gibi çoklu-eleman bir koleksiyonu yoktur, bu nedenle per-index/global ayrımı Trial için baştan beri geçerli değildi); Trial public API/davranışı ve mevcut Trial testleri DEĞİŞMEDEN kalır.

**Status: LOCKED AND IMPLEMENTED + TESTED** (bkz. Bölüm 23, 28.G — 25/25).

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

## 20. Multiple Testing — Kayıt Prensibi (LOCKED) — Trial-Group / Recorded-Trial-Count Foundation LOCKED VE IMPLEMENTED + TESTED (Bölüm 20.1–20.14, 28.J)

Bölüm 18'in candidate/trial foundation'ı (`Candidate`, `Trial`) artık IMPLEMENTED + TESTED'dır (28.G), ama trial-count tracking/registry SAĞLAMAZ (§18.9, 18.13). Gelecekteki, bu foundation'ın ÜZERİNE inşa edilecek bir orchestration/tracking katmanı, en azından şunu **kaydedebilmelidir** (implement edilmez, yalnızca prensip):

```
- kaç candidate/trial değerlendirildi
- hangi metrik bir candidate'i seçti
- hangi data partition kullanıldı
- hangi OOS evaluation, dondurulmuş seçime aittir
```

**Durum güncellemesi (FAZ6C — Deflated Sharpe bağımlılık çözümü, docs-only):** Yukarıdaki dört kayıt maddesinden YALNIZCA ilki ("kaç candidate/trial değerlendirildi") ve üçüncüsünün mekanik olarak doğrulanabilen kısmı ("hangi data partition kullanıldı" — yalnızca `Trial`'ın taşıdığı provenance + evaluation pencereleri düzeyinde), aşağıdaki Bölüm 20.1–20.13'te, **tek bir karşılaştırılabilir deneme grubu** kapsamında, exact bir foundation kontratı olarak **LOCKED**'dır — kilit zamanında (commit `1b666fd`) henüz implement edilmemişti (§28.J — 0/19); artık **IMPLEMENTED + TESTED**'dır (`src/crypto_quant_lab/validation/trial_group.py`, `tests/test_validation_trial_group.py`; bkz. §20.14, §28.J — 19/19). "Hangi metrik bir candidate'i seçti" ve "hangi OOS evaluation dondurulmuş seçime aittir" maddeleri (selection rule, selection/test role, final holdout) bu kontratın **DIŞINDA** kalır ve hâlâ yalnızca prensiptir — implement EDİLMEMİŞTİR.

### 20.1 Source-Preflight Bulguları (FAZ6C — DSR Bağımlılık Çözümü)

```
- §17.4 (Deflated Sharpe — LATER IN FAZ 6), exact prerequisite metni:
  "tanımlı Sharpe istatistiği (17.3) + candidate/trial history (18) +
  (efektif) trial sayısı + gerekli dağılımsal girdiler" — ve: "çoklu-
  trial history/registry/trial-count tracking bu foundation'ın
  DIŞINDADIR (§18.9, 18.13) ve henüz mevcut değildir. Standalone bir
  formül olarak, deneysel/trial framework'ünden **kopuk** implement
  edilmez." Ön-rapor hükmü (çoklu trial geçmişi + efektif trial sayısı
  gerekli; framework'ten kopuk formül yasak) bu metinle BİREBİR
  doğrulanmıştır.
- §17.6 (Multiple-Testing Corrections): "Prerequisites: trial-count
  tracking (18)" — trial-count tracking "henüz mevcut değildir".
- §17.5 (PBO): birden fazla candidate/trial + deterministic performance
  matrix gerektirir; bunlar HENÜZ MEVCUT DEĞİLDİR.
- §18.6: candidate_id için "Global benzersizlik ENFORCE EDİLMEZ ...
  Birden fazla candidate arası benzersizlik, ileride bir orchestration/
  registry kontratının işidir."
- §18.3 (Trial reddedilen seçenek 7-8) + §18.4: Trial YALNIZCA fully-
  successful evaluation için var olur; failure bir Trial DEĞİL, bir
  raise'dir. Başarısız/iptal edilmiş/sonuçsuz bir deneme mevcut modelde
  TEMSİL EDİLEMEZ.
- §18.7: Trial hiçbir selection/test role alanı taşımaz (self-declared
  bir alanın "false safety claim" yaratacağı gerekçesiyle);
  cost_model/funding_model/funding_store/candle store Trial'da
  SAKLANMAZ; Trial/candidate consistency ve results'un gerçekten aynı
  partition'dan geldiği MEKANİK OLARAK DOĞRULANAMAZ (trust boundary).
- §18.9: "Multi-candidate selection'a güvenle geçmeden ÖNCE, gelecekteki
  bir kontrat şunları AÇIKÇA eklemelidir: ... (b) trial-count tracking
  (Bölüm 20) ..." — VE bu foundation "cross-window performance
  aggregation" SAĞLAMAZ.
- candidate.py (kaynak, DEĞİŞMEDEN): `Candidate(candidate_id,
  parameters)`, `Trial(candidate, results, exchange, market_type, symbol,
  timeframe, as_of_time, config)` — ikisi de frozen/slotted; eşitlik/hash
  frozen-dataclass default'u (§18.10'da hashability ampirik olarak
  doğrulanmış; tests/test_validation_candidate.py hash eşitlik testleri
  içerir). Kimlik doğrulama helper'ları (`_require_str_type`,
  `_require_canonical_content`, `_require_canonical_identifier`) `_`
  prefix'li PRIVATE'tır.
- metrics.py / annualized_metrics.py (kaynak, DEĞİŞMEDEN): Sharpe, TEK
  bir BacktestResult (tek pencere) üzerinden tanımlıdır —
  compute_stage2_metrics (per-observation, arithmetic mean, sample stdev
  n-1) ve compute_annualized_sharpe_ratio (Stage-2 Sharpe x
  sqrt(periods_per_year)). Bir Trial'ın ÇOK pencereli results'u için tek
  bir "trial Sharpe"ı tanımlayan hiçbir cross-window aggregation YOKTUR.
  Skewness/kurtosis (üçüncü/dördüncü moment) fonksiyonu YOKTUR.
- Bölüm 27: NumPy/pandas ve "statistics modülü (float-conversion ile)"
  (Bölüm 15.16) kullanımı yasaktır; pyproject.toml hiçbir runtime
  dependency taşımaz. Normal dağılım CDF/ters-CDF'i için Decimal-exclusive
  bir kaynak repository'de YOKTUR.
- Repository-wide grep (src/ ve tests/): "registry", "experiment",
  "trial_count", "n_trials", "deflat", "skew", "kurtos", "NormalDist",
  "erf(" — HİÇBİR eşleşme yok. Trial koleksiyonu/sayımı tamamen
  greenfield'dır; duplicate risk taşıyan mevcut bir modül YOKTUR.
```

**Sonuç:** Deflated Sharpe'ın §17.4'teki bağımlılıklarından **"candidate/trial history" ve "trial sayısı"nın ham (raw) kısmı**, mevcut `Trial` değer nesnesi DEĞİŞTİRİLMEDEN, onun ÜZERİNE inşa edilen tek bir immutable koleksiyon value object'i + tek bir sayım fonksiyonuyla dürüstçe kilitlenebilir. **Efektif trial sayısı, trial-başı Sharpe'ın çok-pencereli tanımı, yüksek momentler, normal CDF kaynağı ve sayımın kapsamı (tek grup mu / tüm araştırma programı mı)** mevcut kaynaklarla ÇÖZÜLEMEZ — bunlar açık bağımlılık olarak kaydedilir (§20.12) ve bu kontratta TASARLANMAZ.

### 20.2 Yedi Kavramın Kesin Ayrımı (LOCKED)

```
1. Tekil Trial değer nesnesi
   -> MEVCUT (Bölüm 18, candidate.py, IMPLEMENTED + TESTED). DEĞİŞMEZ.
2. Araştırmada denenen aday/denemelerin kayıt altına alınması (tüm
   denemeler: başarılı + başarısız + iptal + kaydedilmemiş)
   -> Bu kontrat SAĞLAMAZ. Yalnızca BAŞARILI ve caller tarafından
      AÇIKÇA verilen Trial'lar kaydedilebilir (Trial'ın kendi sınırı,
      §18.3/18.4). Başarısız/iptal denemelerin kaydı DEFERRED (§20.12).
3. Tamamlanan sonuçların karşılaştırılabilir koleksiyonu
   -> BU KONTRAT: TrialGroup (§20.4) — aynı provenance + aynı ordered
      evaluation pencereleri üzerinde değerlendirilmiş, candidate_id'leri
      benzersiz, en az bir Trial içeren immutable koleksiyon.
4. Ham deneme sayısı
   -> BU KONTRAT: recorded_trial_count(group) (§20.4, §20.7) — YALNIZCA
      o TrialGroup'a kaydedilmiş Trial sayısı. "Tüm denemelerin sayısı"
      DEĞİLDİR.
5. Bağımlılığı/korelasyonu hesaba katan efektif deneme sayısı
   -> Bu kontrat SAĞLAMAZ ve TASARLAMAZ. Hiçbir estimator (clustering,
      korelasyon matrisi, eigenvalue vb.) seçilmez. Ham sayı efektif
      sayı olarak KULLANILAMAZ/ADLANDIRILAMAZ (§20.7).
6. Kalıcı kayıt deposu (persistence/DB/serialization)
   -> Bu kontrat SAĞLAMAZ. Repository kanıtı bir kalıcı store
      gerekliliği GÖSTERMEZ (§18.13, Bölüm 27); in-memory immutable
      value object yeterlidir.
7. Optimizer ve candidate selection
   -> Bu kontrat SAĞLAMAZ (§18.9, 18.13, Bölüm 27). TrialGroup hiçbir
      ranking/score/winner/selection alanı veya fonksiyonu taşımaz.
```

Bu yedi kavram **tek bir "registry" adı altında BİRLEŞTİRİLMEZ**. Bu kontrat YALNIZCA (3) ve (4)'ü kilitler; "registry" terimi bu kontratın hiçbir public sembolünde KULLANILMAZ (§18.3'ün "hidden global registry" reddiyle karışmaması için).

### 20.3 Seçilen En Küçük Foundation ve Reddedilen Alternatifler (LOCKED)

**SEÇİLDİ:** yeni, dedicated `src/crypto_quant_lab/validation/trial_group.py` modülünde (a) frozen/slotted `TrialGroup(group_id, trials)` value object'i ve (b) tek bir bare-`int` dönen `recorded_trial_count(group)` fonksiyonu. `Candidate`/`Trial` DEĞİŞMEZ; hiçbir mevcut modül değişmez.

```
Reddedilenler:
a. Yeni obje YOK, yalnızca `len(tuple[Trial, ...])` — REDDEDİLDİ:
   karşılaştırılabilirlik ve duplicate-kimlik kontrolü olmadan ham
   tuple, farklı partition'lardan Trial'ları sessizce karıştırır; sayım
   semantiği isimsiz kalır ve gelecekteki bir DSR implementasyonu
   örtük bir "n_trials" icat etmeye zorlanır (§17.4'ün "framework'ten
   kopuk formül" yasağına en yakın risk).
b. Mutable registry/ledger (add()/append(), global/module-level
   registry) — REDDEDİLDİ: §18.3 "hidden global registry" reddi; repo'nun
   frozen/slotted value-object convention'ı.
c. Kalıcı store (SQLite tablo/şema, JSON serialization) — REDDEDİLDİ:
   repository kanıtı gerektirmiyor; §18.13 persistence/database schema'yı
   açıkça dışlar.
d. Başarısız/iptal denemeleri temsil eden yeni bir obje/alan
   (TrialAttempt, status, failure_reason) — REDDEDİLDİ bu foundation'da:
   §18.3 seçenek 7'nin (failure = raise) doğrudan tersine çevrilmesi
   olurdu ve yeni bir failure taksonomisi icat ederdi. DSR'nin N'ine
   başarısız denemelerin dahil edilip edilmeyeceği bir kullanıcı/
   kontrat kararıdır (§20.12).
e. Self-declared tamlık bayrağı veya caller-beyanlı
   `declared_total_attempts: int` — REDDEDİLDİ: §18.7'nin role alanı
   gerekçesiyle AYNI: hiçbir şeyi mekanik olarak enforce etmez, gerçekte
   var olmayan bir tamlık garantisi izlenimi yaratır.
f. Efektif trial-count estimator — BU TURDA TASARLANMAZ (açık bağımlılık).
g. Heterojen grup (farklı symbol/timeframe/as_of_time/config/pencere) —
   REDDEDİLDİ: farklı örneklemler üzerindeki Sharpe tahminleri aynı
   deneme ailesinin karşılaştırılabilir gözlemleri değildir; gruplar-
   arası sayım birleştirme açık bağımlılıktır (§20.12).
h. Duplicate candidate'in sessizce dedupe edilmesi — REDDEDİLDİ: repo
   geneli "reject, don't repair" prensibi (§18.8).
i. candidate_id'ye göre canonical sıralama zorunluluğu — REDDEDİLDİ:
   Trial.results / rolling / purging "input order preserved" precedent'i;
   sıra hiçbir ranking anlamı taşımaz.
j. Candidate/Trial'a yeni alan (trial_id, experiment_id, role, status) —
   REDDEDİLDİ: Bölüm 18 LOCKED + IMPLEMENTED'dır; bu kontrat onu
   değiştirmez.
k. candidate.py içine eklemek — REDDEDİLDİ: tek-kavram-per-modül
   convention'ı; candidate.py'nin kilitli implementasyonu DEĞİŞMEZ.
l. Grubun paylaşılan provenance/pencerelerini ayrı alan olarak saklamak —
   REDDEDİLDİ: trials[0]'dan kurtarılabilir; §18.7'nin "yalnızca
   kurtarılamayan veri ayrıca saklanır" ilkesi.
```

### 20.4 Exact Public API (LOCKED)

```python
# Modül: src/crypto_quant_lab/validation/trial_group.py (YENİ modül)

from dataclasses import dataclass

from crypto_quant_lab.validation.candidate import Trial


@dataclass(frozen=True, slots=True)
class TrialGroup:
    group_id: str
    trials: tuple[Trial, ...]


def recorded_trial_count(group: TrialGroup) -> int: ...
```

```
- Field sırası (group_id, trials) TAM OLARAK kilitlidir.
- Frozen, slotted; equality/hash frozen-dataclass default'ları, TÜM
  field'lar üzerinden (custom __eq__/__hash__/__order__ YOK). Equality
  trials SIRASINA duyarlıdır (aynı Trial'lar farklı sırada -> EŞİT
  DEĞİL); bu yalnızca value-semantics'tir, bir ranking anlamı TAŞIMAZ.
- recorded_trial_count: tek pozisyonel parametre; bare `int` döner
  (bool DEĞİL, yeni value object DEĞİL).
- Modülün public sembolleri YALNIZCA TrialGroup ve
  recorded_trial_count'tur. effective_trial_count, deflated_sharpe,
  select/rank/score/best/winner, add/append/register, save/load
  sembolleri TANIMLANMAZ.
- Package-root export YOK — validation/__init__.py DEĞİŞMEZ.
- Candidate, Trial, ParameterValue, WindowResult, TemporalWindow,
  BacktestConfig, BacktestResult ve tüm mevcut fonksiyon imzaları
  DEĞİŞMEZ; hiçbirine alan EKLENMEZ.
```

### 20.5 Kimlik, Tekrar Çalıştırma ve Duplicate Davranışı (LOCKED)

**`group_id`:** `candidate_id` ile AYNI kural (§18.6): `str` olmalı (değilse TypeError); boş/yalnızca-whitespace (ValueError); baştaki/sondaki whitespace padding (ValueError, sessizce strip EDİLMEZ); case-sensitive; Unicode normalization YOK; uzunluk sınırı YOK. `group_id`, gruplar-arası benzersizliği ENFORCE ETMEZ (global registry YOK) — iki farklı TrialGroup aynı `group_id`'yi taşıyabilir; bu, caller disiplinidir.

**Grup-içi deneme kimliği:** bir TrialGroup içinde bir denemenin kimliği, `trial.candidate.candidate_id`'dir.

```
- Aynı candidate_id'ye sahip ikinci bir Trial -> ValueError (§20.8
  stage 6), ilk görüldüğü index'i ve duplicate index'i tanımlar.
- Bu kural, HER İKİ durumu da AYNI şekilde reddeder:
  (i)  deterministik bir TEKRAR ÇALIŞTIRMA (aynı Candidate, aynı
       provenance, aynı pencereler -> Bölüm 25 determinism'i gereği
       eşit Trial): yeni bilgi TAŞIMAZ; iki kez saymak ham sayıyı
       şişirir.
  (ii) ÇAKIŞAN bir kayıt (aynı candidate_id, farklı parameters veya
       farklı results — örn. Trial'da saklanmayan farklı bir cost/
       funding model veya farklı veri): aynı kimlik altında iki farklı
       kanıt, auditability'yi bozar.
- Sessiz dedupe, "ilkini tut"/"sonuncuyu tut" politikası YOKTUR —
  hangisinin gruba ait olduğuna caller karar verir.
- AYNI parameters'a sahip ama FARKLI candidate_id'li iki Trial LEGAL'dir
  ve REDDEDİLMEZ — §18.6'ya göre candidate_id kimliğin parçasıdır ve
  parameters, candidate -> policy eşlemesinin tamamını kanıtlamaz
  (policy builder caller kodudur, §18.2). Bunun sonucu (aynı
  konfigürasyonun farklı ID'lerle iki kez kaydedilip ham sayının
  şişmesi) mekanik olarak tespit EDİLMEZ; açık bir trust boundary'dir.
- Aynı Trial'ın İKİ FARKLI TrialGroup'ta bulunması tespit EDİLMEZ (her
  grup diğerlerinden habersizdir); gruplar-arası sayım birleştirme
  (açık bağımlılık, §20.12) bunu çözmek zorundadır.
```

### 20.6 Karşılaştırılabilir Deney Grubu Sınırları (LOCKED)

Bir TrialGroup'un TÜM Trial'ları, `trials[0]` referans alınarak, aşağıdaki mekanik olarak doğrulanabilir alanlarda EŞİT olmalıdır (Python `==`, frozen-dataclass/stdlib equality):

```
1. exchange
2. market_type
3. symbol
4. timeframe
5. as_of_time      (aware datetime `==` -> ANLIK/instant eşitliği; aynı
                    anı gösteren farklı tzinfo'lu değerler EŞİT sayılır —
                    Trial'ın kendi dataclass eşitliğiyle tutarlı; hiçbir
                    UTC dönüşümü/normalizasyonu YAPILMAZ)
6. config          (BacktestConfig `==`; Decimal değer eşitliği)
7. ordered evaluation pencere dizisi:
   tuple(r.window for r in trial.results) — uzunluk, SIRA ve her
   TemporalWindow EŞİT olmalı (duplicate/overlapping pencereler Trial'da
   legal olduğundan, dizinin kendisi karşılaştırılır; set/sorted
   karşılaştırması YAPILMAZ).
```

```
Mekanik olarak DOĞRULANAMAYAN (trust boundary, açıkça kaydedilir):
- cost_model / funding_model / funding_required / funding_store / candle
  store'un aynı olduğu (Trial bunları saklamaz, §18.7)
- context-aware evaluation'da context_start'ların aynı olduğu (hiçbir
  result modelinde saklanmaz, §8.3.16)
- candidate -> policy builder eşlemesinin ve kod sürümünün aynı olduğu
- pencerelerin IS mi OOS mu olduğu (role alanı YOK, §18.7)
Bu nedenle "karşılaştırılabilir grup" = "yalnızca mekanik olarak
kontrol edilebilen provenance + pencere eşitliği". Farklı maliyet/
funding varsayımlarıyla üretilmiş Trial'ları aynı gruba koymamak caller
disiplinidir.
```

Gelecekteki araştırma boyutlarına etkisi (bilgi amaçlı, yeni alan YETKİSİ DEĞİLDİR): farklı coin/veri evreni, timeframe, as_of_time veya config -> **farklı TrialGroup'lar**; aynı partition üzerindeki farklı strateji parametresi / feature-signal kombinasyonu -> aynı grupta **farklı candidate_id'ler**; piyasa rejimi alt-dönemleri -> farklı pencere dizileri -> farklı gruplar; farklı maliyet varsayımı -> Trial'da temsil edilmez (yukarıdaki trust boundary) — eksiklik olarak kaydedilir. Tüm bu gruplar arasındaki toplam deneme yükü bu kontratla ÖLÇÜLMEZ (§20.7, §20.12).

### 20.7 Recorded Trial Count Semantiği — Ne Sayar, Neyi Kanıtlamaz (LOCKED)

```
recorded_trial_count(group) == len(group.trials)
```

```
SAYAR:
- Yalnızca bu TrialGroup'a caller tarafından AÇIKÇA verilmiş ve kabul
  edilmiş, başarılı, candidate_id'si benzersiz Trial KAYITLARINI —
  sayı, verilen gruptaki kabul edilmiş Trial kayıtlarının TAM
  sayısıdır. Ağırlıklandırma, dedupe, korelasyon düzeltmesi YOK.

KANITLAMAZ (her biri açıkça):
- Araştırmada denenen TÜM denemelerin sayısını VEYA benzersiz strateji
  sayısını. Başarısız (raise edilmiş), iptal edilmiş, sonuçsuz veya
  caller'ın kaydetmediği denemeler bu sayıda YOKTUR (sayıyı gerçek
  deneme yükünün ALTINDA bırakabilir); aynı konfigürasyon farklı
  candidate_id'lerle birden fazla kez kaydedilmiş olabilir (§20.5 —
  sayıyı benzersiz denemelerin ÜSTÜNE çıkarabilir). Bu iki etki aynı
  anda mümkün olduğundan sayı, gerçek araştırma deneme sayısına göre
  KOŞULSUZ bir alt sınır VEYA üst sınır garantisi VERMEZ ve tamlık
  kanıtı DEĞİLDİR. Eksik geçmiş "tam" SAYILMAZ; tamlık iddiası taşıyan
  hiçbir alan/fonksiyon yoktur.
- Diğer TrialGroup'lardaki (farklı coin/timeframe/as_of_time/config/
  pencere) denemeleri.
- Denemelerin bağımsızlığını. recorded_trial_count, EFEKTİF/BAĞIMSIZ
  deneme sayısı DEĞİLDİR ve gelecekteki hiçbir kontratta, ayrı ve
  açıkça kilitlenmiş bir karar olmadan, efektif sayı yerine
  KULLANILAMAZ. Korelasyonlu Trial'lar (örn. komşu parametreler) ham
  sayıda tam ağırlıkla yer alır.
- Yalnızca kazanan denemelerin kaydedilmediğini: caller yalnızca iyi
  sonuçları gruba koyarsa sayı düşük kalır ve gelecekteki bir DSR
  düzeltmesi fazla iyimser olur. Bu mekanik olarak tespit EDİLEMEZ —
  Bölüm 19 research-process riskidir.
- Trial'ların IS üzerinde seçildiğini, OOS'un seçim/tuning girdisi
  olarak kullanılmadığını, veya bir final holdout'un korunduğunu. Bu
  foundation hiçbir selection/test role'ü, hiçbir final holdout
  korumasını SAĞLAMAZ ve OOS/holdout sonuçlarının seçim girdisi olarak
  kullanılmasını hiçbir şekilde MEŞRULAŞTIRMAZ (§18 zorunlu prensibi,
  §18.7, §18.9, Bölüm 19 DEĞİŞMEDEN geçerlidir).
```

**Sayım-garantisi açıklama düzeltmesi (implementation combined delivery ile eklendi — kontratın kilitlendiği `1b666fd` commit'indeki ifadeyi AÇIKLIĞA KAVUŞTURUR):** Kilit sürümündeki §20.7 metni, sayıyı gerçek deneme yükünün koşulsuz bir "ALT SINIRI (lower bound)" olarak tanımlıyordu. Bu garanti mevcut API'den çıkarılamaz: §20.5, aynı `parameters`'ın farklı `candidate_id`'lerle kaydedilmesini KABUL EDER ve bunun ham sayıyı şişirebileceğini zaten kaydeder — dolayısıyla eksik kayıtlar sayıyı düşürürken mükerrer temsil onu yükseltebilir ve hiçbir yöndeki sınır koşulsuz değildir. Yukarıdaki metin buna göre düzeltildi. Bu yalnızca bir **açıklama düzeltmesidir** — API, algoritma veya davranış DEĞİŞMEZ: aynı `candidate_id` hâlâ reddedilir; aynı `parameters` + farklı `candidate_id` hâlâ kabul edilir; sayı hâlâ `len(group.trials)`'dır; hiçbir yeni alan, dedupe veya sayım algoritması eklenmez; §28.J kriter sayısı 19 kalır. §23'teki kilit kaydının "(alt sınır, tamlık kanıtı değil)" ifadesi tarihsel kayıt olarak korunur ve bu not ile açıklığa kavuşturulmuş sayılır.

### 20.8 Validation / Fail-Fast Sırası ve Exact Mesajlar (LOCKED)

**`TrialGroup.__post_init__`** — her adım yalnızca önceki adım TÜM girişler için hatasız tamamlandıktan SONRA çalışır; çok-elemanlı adımlar `trials` üzerinden AYRI, GLOBAL geçişlerdir (Candidate'in düzeltilmiş global fail-fast precedent'i, §18.8/18.14):

```
1. group_id str olmalı; değilse
   TypeError(f"group_id must be a str, got {type(group_id).__name__}")
2. group_id içeriği:
   boş/yalnızca-whitespace -> ValueError("group_id must not be empty or whitespace-only")
   padding                 -> ValueError("group_id must not have leading/trailing whitespace padding")
3. trials tuple olmalı; değilse
   TypeError(f"trials must be a tuple, got {type(trials).__name__}")
4. trials boş olmamalı; değilse
   ValueError("trials must not be empty")
5. GLOBAL geçiş, index artan: her eleman Trial olmalı; değilse
   TypeError(f"trials[{index}] must be a Trial, got {type(trial).__name__}")
6. GLOBAL geçiş, index artan: candidate_id benzersizliği; ilk tekrarda
   ValueError(f"trials[{index}].candidate.candidate_id {candidate_id!r} "
              f"duplicates trials[{first_index}].candidate.candidate_id")
   (first_index = aynı candidate_id'nin ilk görüldüğü index)
7-12. Provenance homojenliği — alan başına AYRI GLOBAL geçiş, şu SABİT
   sırayla: 7 exchange, 8 market_type, 9 symbol, 10 timeframe,
   11 as_of_time, 12 config. Her geçiş index 1'den artan sırayla
   trials[index].<alan> != trials[0].<alan> ilk uyuşmazlıkta:
   ValueError(f"trials[{index}].{field_name} ({value!r}) does not match "
              f"trials[0].{field_name} ({reference!r})")
13. GLOBAL geçiş, index 1'den artan: evaluation pencere dizisi
   tuple(r.window for r in trials[index].results) !=
   tuple(r.window for r in trials[0].results) ise
   ValueError(f"trials[{index}] evaluation windows do not match "
              f"trials[0] evaluation windows")
```

Tek-elemanlı bir `trials` (len == 1) adım 6-13'ü trivial olarak geçer ve LEGAL'dir (`recorded_trial_count == 1`).

**`recorded_trial_count(group)`:**

```
1. group bir TrialGroup olmalı; değilse
   TypeError(f"group must be a TrialGroup, got {type(group).__name__}")
2. len(group.trials) döndürülür (int).
```

```
Hiçbir adım sessizce: group_id'yi strip/case-fold ETMEZ; trials'ı
SIRALAMAZ, DEDUPE ETMEZ, FİLTRELEMEZ; uyumsuz bir Trial'ı atlayıp
kısmi bir grup DÖNDÜRMEZ; Trial/Candidate'in kendi iç invariant'larını
TEKRAR doğrulamaz (lower-layer trust, §18.7).
Private kimlik kuralı (adım 1-2) trial_group.py içinde, candidate.py
ile AYNI semantik ve AYNI mesaj kalıbıyla YEREL olarak yeniden
tanımlanır — candidate.py'nin `_` prefix'li helper'ları cross-module
İMPORT EDİLMEZ (annualized_metrics.py'nin private Decimal context'i
yeniden tanımlama precedent'i, §15.28/15.29).
```

### 20.9 Purity, Immutability, UTC/Decimal ve Import Direction (LOCKED)

```
- Construction girdileri MUTATE ETMEZ; trials tuple'ı ve Trial
  elemanları KOPYALANMAZ (`is` kimliği korunur).
- Eşit girdilerden tekrar construction eşit ve eşit-hash değer üretir.
- Wall-clock, randomness, I/O, store query, backtest/replay çağrısı YOK.
- Metrik HESAPLAMAZ (Stage-1/Stage-2/annualized fonksiyonlarını
  çağırmaz/import etmez); Sharpe/score/rank üretmez.
- Decimal aritmetiği YOK, float YOK, private Decimal context GEREKMEZ
  (yalnızca equality karşılaştırması ve len()).
- UTC: yeni bir zaman kuralı YOK; as_of_time ve pencere sınırları zaten
  Trial/TemporalWindow tarafından genuine-aware olarak doğrulanmıştır;
  karşılaştırma instant-eşitliğidir (§20.6), dönüşüm YAPILMAZ.
- Hidden global registry / module-level mutable state YOK.
```

**Import direction (LOCKED):**

```
crypto_quant_lab.validation.trial_group  (YENİ modül)
  imports:
    crypto_quant_lab.validation.candidate  (Trial — PUBLIC tip)
    dataclasses (stdlib)

candidate.py, rolling.py, windows.py, metrics.py, annualized_metrics.py,
purging.py <- trial_group.py'yi İMPORT ETMEZ.

Sonuç: trial_group.py -> candidate.py (tek yönlü); döngü YOK.
trial_group.py rolling runner'larını, metrics'i, purging'i, optimizer/
search kodunu İMPORT ETMEZ.
```

### 20.10 Gelecekteki İmplementasyon İçin Dosya Kapsamı (Planlama Bilgisi — Şimdi Değiştirilmez)

```
Yeni production dosyası: src/crypto_quant_lab/validation/trial_group.py
  (TrialGroup, recorded_trial_count).
Yeni test dosyası: tests/test_validation_trial_group.py (tek-modül-per-
  test-dosyası convention'ı).
Değiştirilecek mevcut production/test dosyası: YOK (candidate.py,
  rolling.py, windows.py, metrics.py, annualized_metrics.py, purging.py,
  backtest/models.py, validation/__init__.py DOKUNULMAZ).
Documentation (combined closure için): VALIDATION_SPEC.md.
Açıkça YASAK: ROADMAP.md, pyproject.toml, AGENTS.md, CLAUDE.md, herhangi
  bir __init__.py, herhangi bir başka production/test/spec dosyası.
```

### 20.11 Test Kontratı (Gelecekteki Mikro-Adım İçin Minimum Davranışsal Matris)

```
- API: modül yolunda iki sembol; public sembol kümesi TAM OLARAK
  {TrialGroup, recorded_trial_count}; TrialGroup frozen/slotted, field
  sırası (group_id, trials); package-root export YOK.
- group_id: yanlış tip TypeError; boş/whitespace/padding ValueError
  (exact mesaj); case-sensitive, normalizasyon YOK.
- trials: non-tuple TypeError; boş ValueError; yanlış-tipli eleman
  index 0 VE sonraki index (exact mesaj); global geçiş kanıtı (sonraki
  index'te tip hatası, önceki index'lerde duplicate/provenance hatası
  varken bile adım 5 kazanır).
- Duplicate: aynı candidate_id (eşit Trial tekrarı VE farklı parameters/
  results ile çakışan kayıt) ValueError, iki index'i de içeren exact
  mesaj; dedupe YOK; aynı parameters + farklı candidate_id KABUL.
- Provenance: 6 alanın her biri için index 1 ve sonraki index
  uyuşmazlığı exact mesajla; alan-başı global geçiş sırası kanıtı
  (ör. index 2'de exchange uyuşmazlığı, index 1'deki symbol
  uyuşmazlığından ÖNCE raise edilir); farklı tzinfo'lu aynı-an
  as_of_time KABUL.
- Pencere dizisi: farklı uzunluk, farklı sıra, farklı pencere ->
  ValueError; birebir aynı dizi (duplicate/overlapping pencereler dahil)
  KABUL.
- Stage sırası: her adımın kendinden sonraki adımları ezdiğini gösteren
  eşzamanlı-ihlal testleri (1->2->...->13).
- Tek-Trial grup legal, recorded_trial_count == 1; N Trial -> N;
  dönüş tipi tam olarak int (bool değil).
- recorded_trial_count: TrialGroup olmayan girdi (tuple, list, Trial,
  None) TypeError exact mesaj.
- Value semantics: equality/hash, sıra-duyarlı equality, deterministik
  tekrar construction, trials tuple'ı ve elemanlarının `is` kimliğiyle
  korunması (no copy/mutation).
- Absence: TrialGroup'ta role/score/rank/winner/selected/effective/
  status/failed/complete/holdout alanı YOK; modülde effective count/
  DSR/selection/persistence sembolü YOK.
- Static/import: trial_group.py yalnızca candidate.Trial + stdlib
  import eder; hiçbir validation modülü trial_group.py'yi import etmez;
  candidate.py private helper import'u YOK; float/Decimal aritmetiği YOK.
- Entegrasyon: gerçek run_rolling_backtest_from_store çıktısından AYNI
  pencerelerle üretilmiş iki farklı-candidate Trial bir TrialGroup
  oluşturur (count 2); farklı pencere dizili üçüncü bir Trial reddedilir.
Testler wall clock/randomness/network/external service/float expected
value KULLANMAZ; production algoritmasını oracle olarak yeniden
implement ETMEZ.
```

### 20.12 Deflated Sharpe İçin Bağımlılık Durumu — Çözülen / Açık Kalan (LOCKED Kayıt)

**Durum güncellemesi (DSR exact contract lock):** Aşağıdaki on madde, bu bölüm yazıldığı andaki durumu kaydeder (tarihsel olarak korunur). Maddelerin tamamı artık §17.4.2'de karara bağlanmıştır: 1 N = caller-beyanlı `independent_trial_count` (recorded_trial_count ile ikame YOK); 2 V/istatistikler tek TrialGroup'tan, N program kapsamında caller'dan; 3 başarısız/kaydedilmemiş denemeler V'ye girmez, yalnızca beyan edilen N'e yansır; 4 her Trial tek pencere, SR = Stage-2 Sharpe, T = getiri sayısı; 5 per-observation ölçek; 6 population moment skewness + HAM kurtosis; 7 Decimal-only Taylor serisi Φ + Newton Φ^-1; 8 Stage-2 Sharpe'ların sample varyansı (M-1), V > 0; 9 zorunlu `selected_candidate_id`, seçim YOK; 10 rol doğrulanamaz, holdout koruması YOK. Deferred kalanlar: efektif-N estimator'ı ve çok pencereli pooling (§17.4.14). Karar 2, 3 ve 6'nın gerekçeleri implementation delivery'de §17.4.1a ile düzeltilmiştir; DSR artık IMPLEMENTED + TESTED'dır (§17.4.17, §28.K — 27/27).

```
Bu kontratla ÇÖZÜLEN (kontrat düzeyinde; implementasyon HENÜZ YOK):
- Tek bir karşılaştırılabilir partition için "candidate/trial history"
  temsili (TrialGroup).
- O grup için isimlendirilmiş, ham, kaydedilmiş deneme sayısı
  (recorded_trial_count) — efektif sayıdan açıkça ayrılmış.

HÂLÂ AÇIK (DSR kontratı kilitlenmeden önce ayrı karar/kontrat gerekir;
bu kontrat hiçbirini seçmez):
1. N semantiği: DSR'de ham sayı mı, efektif sayı mı kullanılacağı; efektif
   ise hangi estimator (kullanıcı kararı).
2. N kapsamı: yalnızca tek TrialGroup mu, yoksa aynı araştırma
   programındaki birden fazla grup (coin/timeframe/as_of_time/config)
   mu; gruplar-arası birleştirme ve aynı Trial'ın çift sayılmasının
   önlenmesi.
3. Başarısız/iptal/kaydedilmemiş denemelerin N'e dahil edilip
   edilmeyeceği ve nasıl temsil edileceği (Trial bunları temsil edemez).
4. Çok pencereli bir Trial için TEK "trial Sharpe"ının tanımı (cross-
   window aggregation, §18.9'da hâlâ dışarıda) ve buna karşılık gelen
   örneklem uzunluğu T.
5. Per-observation vs. annualized Sharpe ölçeğinin DSR'de hangisi
   olacağı (15.9–15.33 ikisini de sağlar).
6. Seçilen denemenin getiri serisinin skewness/kurtosis'i — repository'de
   yok; Decimal kontratı gerekir.
7. Normal dağılım CDF / ters-CDF kaynağı — Bölüm 27 ve 15.16 float
   dönüşümlü statistics modülünü yasaklar; Decimal-exclusive bir
   yaklaşım veya açık bir float-sınırı kararı gerekir.
8. Trial'lar arası Sharpe varyansı (en az 2 Trial) — hangi ölçekte ve
   hangi varyans konvansiyonuyla.
9. "Seçilmiş" denemenin nasıl belirtildiği — selection rule bu kontratın
   ve DSR'nin DIŞINDADIR; DSR seçilmiş denemeyi explicit girdi olarak
   almalıdır, seçimi kendisi yapmamalıdır.
10. IS/OOS/final-holdout ayrımı — hâlâ research-process disiplini
   (Bölüm 19); DSR bunu mekanik olarak sağlamaz.
```

### 20.13 Explicit Exclusions (Bu Kontrat Kapsamında DEĞİL, İmplement EDİLMEZ)

```
Deflated Sharpe formülü, efektif trial-count estimator, PBO, CPCV,
multiple-testing correction, parameter stability, candidate selection/
ranking/scoring, optimizer/grid/random/Bayesian search, selection/test
role, final holdout protection/enforcement, cross-window veya cross-
group aggregation, başarısız/iptal deneme kaydı, persistence/database/
serialization, global registry, online learning, reporting/CLI/UI,
paper/live trading, Candidate/Trial değişikliği.
```

Bu maddeler **deferred boundary'ler** olarak kaydedilir — implement edilmiş özellikler DEĞİL. Bu kontratın LOCKED olması, Deflated Sharpe'ın spec-lock edildiği, FAZ6C'nin veya Faz 6'nın tamamlandığı anlamına GELMEZ (bkz. Bölüm 22, 22.2, 28.J).

### 20.14 Implementation Evidence (IMPLEMENTED + TESTED — combined delivery ile eklendi)

```
Production: src/crypto_quant_lab/validation/trial_group.py (YENİ dosya)
  - TrialGroup (frozen, slots) — group_id: str, trials: tuple[Trial, ...]
    (kilitli field sırası, §20.4 ile birebir; default dataclass eq/hash,
    order=False).
  - recorded_trial_count(group: TrialGroup) -> int — yalnızca tip kontrolü
    + len(group.trials).
  - __post_init__: §20.8'in 13 adımı birebir — adım 1-2 group_id (yerel
    olarak yeniden tanımlanmış kimlik kuralı, candidate.py private
    helper'ları İMPORT EDİLMEDEN), 3 tuple, 4 non-empty, 5 eleman tipi
    (global geçiş), 6 candidate_id benzersizliği (global geçiş, ilk-görülen
    index raporlanır), 7-12 exchange/market_type/symbol/timeframe/
    as_of_time/config için alan-başı AYRI global geçişler (trials[0]'a
    karşı), 13 ordered evaluation pencere dizisi (global geçiş). Kilitli
    exception türleri ve mesajları birebir.
  - imports: `from dataclasses import dataclass as _dataclass` ve
    `from crypto_quant_lab.validation.candidate import Trial as _Trial` —
    YALNIZCA. Private alias'lar, modülün public sembol kümesinin TAM
    OLARAK {TrialGroup, recorded_trial_count} kalması içindir (§20.4);
    çözümlenmiş annotation'lar kilitli API ile aynıdır
    (typing.get_type_hints: group_id -> str, trials -> tuple[Trial, ...];
    recorded_trial_count: group -> TrialGroup, return -> int). §20.4'teki
    örnek import satırları alias'sız yazılmıştı; bu, import edilen
    sembolün veya yönün değişmesi DEĞİL, yalnızca yerel isim gizlemedir
    (import direction §20.9 ile birebir: trial_group.py -> candidate.py).
Test: tests/test_validation_trial_group.py (YENİ dosya) — 99 test
  (60 test fonksiyonu, parametrize ile 99 collected), tümü PASS.
  - API/shape: modül yolu, public sembol kümesi, field sırası, çözümlenmiş
    annotation'lar, signature, frozen/slotted, default eq/hash, package-
    root export yokluğu, Candidate/Trial/WindowResult field'larının
    değişmediği.
  - group_id: tip/boş/whitespace/padding exact mesajları, case-sensitivity
    ve Unicode normalizasyon yokluğu.
  - trials: non-tuple, boş, index 0 ve sonraki index yanlış eleman.
  - Duplicate: eşit tekrar çalıştırma, aynı obje iki kez, farklı
    parameters ile çakışan kayıt, ilk-görülen index; aynı parameters +
    farklı candidate_id KABUL ve count == 2 (iki bağımsız strateji kanıtı
    olarak SUNULMAZ — yalnızca kayıt sayısı).
  - Provenance: 6 alanın her biri için index 1 ve sonraki index uyuşmazlığı;
    ardışık her alan çifti için "önceki alanın geçişi, daha sonraki index'te
    olsa bile kazanır" (tek-döngülü per-trial implementasyonu ayırt eden
    testler); aynı-an farklı tzinfo'lu as_of_time ve değer-eşit config
    KABUL.
  - Pencere dizisi: kısa/uzun/yeniden sıralı/farklı değer reddi, sonraki
    index, birebir aynı duplicate+overlapping dizi KABUL, aynı-an farklı
    tzinfo'lu pencere KABUL.
  - Global stage sırası: 1>3, 2>3, 3>4, 5>6 (sonraki index), 6>7 (sonraki
    index), 6>13, 7>8>...>12 (ardışık çiftler), 12>13 (sonraki index).
    Adım 4'ün adım 5'e üstünlüğü yapısal olarak zorunludur (boş tuple
    yanlış-tipli eleman içeremez) — ayrı eşzamanlı-ihlal testi mümkün
    değildir; adım 1'in adım 2'ye üstünlüğü de aynı şekilde yapısaldır.
  - Değer semantiği: tek/çoklu grup ve tam int count, girdi sırasının
    korunması, `is` kimliği/no-copy/no-mutation, değer-eşitliği, sıra-
    duyarlı eşitlik, group_id eşitsizliği, hashability (set/dict),
    tekrarlı construction/call determinism, aynı Trial'ların iki ayrı
    grupta gruplar-arası tespit OLMADAN bulunabilmesi.
  - recorded_trial_count yanlış girdi (None/tuple/list/str/int/Trial)
    exact mesaj.
  - Absence/static: yasak alan (role/score/rank/winner/selected/effective/
    status/failed/complete/holdout/...) ve yasak sembol (effective count/
    DSR/selection/persistence/registry/optimizer) yokluğu; import
    satırlarının tam listesi; hiçbir validation modülünün trial_group'u
    import etmediği; Decimal/float/clock/randomness import'u yokluğu.
  - Entegrasyon: gerçek SQLite store (tmp_path) + run_rolling_backtest_
    from_store ile iki pencere üzerinde FLAT ve LONG policy'lerinden
    üretilmiş iki Trial bir TrialGroup oluşturur (count == 2); tek
    pencereli üçüncü bir Trial exact mesajla reddedilir.
İlgili regression suite'ler (test_validation_candidate.py,
test_validation_rolling_backtest.py, test_validation_windows.py,
test_validation_metrics.py, test_validation_annualized_metrics.py,
test_validation_purging.py, test_backtest_models.py,
test_backtest_results.py — 691 test) DEĞİŞMEDEN yeşil; tam suite
2045/2045 PASS (1946 önceki + 99 yeni).
Değiştirilen mevcut production/test dosyası: YOK.
```

**Status: LOCKED AND IMPLEMENTED + TESTED** (bkz. Bölüm 23, 28.J — 19/19). Bu, Deflated Sharpe'ın spec-lock veya implement edildiği ya da §20.12'deki açık bağımlılıkların çözüldüğü anlamına GELMEZ.

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

**Capability Ownership Model (LOCKED — FAZ6 Phase-Status Reconciliation mikro-adımıyla eklendi):** Bir deliverable'ın hangi alt-faza ait olduğu, bu bölümdeki (§22) authoritative capability listesi tarafından belirlenir — **historical microstep/commit label prefix'i tarafından değil.** `FAZ6B MS2` (zero-context rolling orchestrator) ve `FAZ6B MS3`–`MS5` (Stage-1 metrics) label'ları, bu iş **FAZ6B-numaralı workstream'de** yürütüldüğü için bu şekilde adlandırılmıştır (gerekçe: §22.1) — ama capability'nin kendisi (fixed-policy rolling OOS evaluation, basic return/drawdown metrics), aşağıdaki FAZ6A tanımına göre **FAZ6A-owned**'dır. Historical microstep/commit label'lar **yeniden yazılmaz veya fabrikasyon edilmez** (§22.1); yalnızca capability-ownership'in doğru okunması gereken kaynak bu bölümdür (§22), §23'teki microstep log değil.

```
FAZ 6A — Temporal Validation Foundation
    temporal window primitive, IS/OOS split, fixed-policy rolling OOS
    evaluation, basic return/drawdown metrics.

    Durum: FAZ6A — COMPLETE (bkz. §22.2 Phase-Status Table; kanıt ve
    tam kapsam sınırı için bkz. §22.3).

FAZ 6B — Context-Aware Extensions + Return-Series / Experiment Foundation
    Tamamlanan bileşenler:
      - B2 Layer-1 context/evaluation mimarisi (Bölüm 8.3, LOCKED) VE
        `evaluation_start` — İMPLEMENT EDİLMİŞ + TEST EDİLMİŞTİR
        (bkz. Bölüm 23, 28.B — 15/15).
      - Context-aware replay/store-backed tek-pencere execution
        (`run_backtest_replay`/`run_backtest_from_store`).
      - Type-H semantic caller precondition (Bölüm 8.3.5) — LOCKED.
      - Factory-based policy-instance-freshness kontratı (Bölüm 8.3.6)
        — LOCKED.
      - Mevcut zero-context rolling orchestrator
        (`run_rolling_backtest_from_store`) için runtime freshness
        enforcement — İMPLEMENT EDİLMİŞ + TEST EDİLMİŞTİR (bkz. Bölüm
        23, 28.C — 12/12). (Zero-context rolling orchestrator'ın
        kendisi FAZ6A-owned bir capability'dir — Bölüm 8.3.6, 13; bu
        madde yalnızca freshness-mekanizmasının FAZ6B'de LOCKED/
        implement edildiğini kaydeder.)
      - Stage-1 metrics (Bölüm 15.1–15.8), FAZ6A'nın zaten tamamlanmış
        bir bağımlılığı olarak FAZ6B'ye açık.
      - Return-series semantics preflight (read-only, bkz. Bölüm 23) —
        equity_curve'ün zaten evaluation-only olduğunu runtime kanıtıyla
        doğruladı.
      - Return-series + per-observation Sharpe kontratı (Bölüm
        15.9–15.18) — LOCKED VE artık İMPLEMENT EDİLMİŞ + TEST
        EDİLMİŞTİR: `compute_periodic_returns`, `Stage2Metrics`,
        `compute_stage2_metrics` (commit `e4cedf9`; bkz. Bölüm 23,
        28.E — 29/29).
      - Non-zero-context Layer-2 source-preflight + exact kontrat
        (Bölüm 8.3.16) — LOCKED VE artık İMPLEMENT EDİLMİŞ + TEST
        EDİLMİŞTİR: `ContextAwareWindow`,
        `run_context_aware_rolling_backtest_from_store`
        (`src/crypto_quant_lab/validation/rolling.py`; kendi
        regression suite'i 94 test — 28 mevcut zero-context DEĞİŞMEDEN
        + 66 yeni non-zero-context; bkz. Bölüm 23, 28.F — 22/22).
      - Candidate/trial foundation (Bölüm 18) — LOCKED VE artık
        İMPLEMENT EDİLMİŞ + TEST EDİLMİŞTİR: `Candidate`, `Trial`,
        `ParameterValue` (`src/crypto_quant_lab/validation/candidate.py`;
        kendi regression suite'i 148 test, tümü PASS; bkz. Bölüm 18.14,
        23, 28.G — 25/25). Exact public API, kimlik/parametre domain'i,
        provenance, validation/fail-fast sırası, leakage/selection
        sınırları (§18.9), purity/import-direction — tümü kilitlendiği
        gibi implement edildi; hiçbir mevcut production dosyası
        (rolling.py/metrics.py/windows.py/models.py/policy.py)
        değişmedi.

      - Annualized Metrics (Sharpe/Sortino/CAGR/Calmar) source-preflight
        + exact kontrat (Bölüm 15.19–15.33) — LOCKED VE artık İMPLEMENT
        EDİLMİŞ + TEST EDİLMİŞTİR (bu combined delivery):
        `src/crypto_quant_lab/validation/annualized_metrics.py` (YENİ
        modül), dört bağımsız, bare-`Decimal` döndüren fonksiyon
        (`compute_annualized_sharpe_ratio`, `compute_sortino_ratio`,
        `compute_cagr`, `compute_calmar_ratio`), 365-gün calendar basis,
        exact `periods_per_year` türetimi, exact formüller/operation
        sıraları, validation/fail-fast sırası, purity/import-direction —
        tümü kilitlendiği gibi implement edildi; hiçbir mevcut production
        dosyası (metrics.py/rolling.py/windows.py/candidate.py/models.py/
        market_data/timeframes.py) değişmedi. Kendi regression suite'i
        `tests/test_validation_annualized_metrics.py`'de (77 test, tümü
        PASS); bkz. Bölüm 23, 28.H — 30/30.

    Kalan zorunlu bileşenler: **yok** — locked FAZ6B scope içindeki tüm
    maddeler (Layer-1 context/evaluation, policy-instance-freshness,
    return-series + per-observation Sharpe, non-zero-context Layer-2,
    candidate/trial foundation, Annualized Metrics) artık İMPLEMENT
    EDİLMİŞ + TEST EDİLMİŞTİR.

    Durum: FAZ6B — COMPLETE (bkz. §22.2). Bu, candidate/trial
    foundation'ın kendisinin hiçbir candidate selection/ranking,
    optimizer/search, final holdout protection, veya multiple-testing
    correction SAĞLADIĞI anlamına GELMEZ (§18.9) — bunlar FAZ6C/FAZ6D'nin
    ayrı, henüz tamamlanmamış kapsamıdır; FAZ6B'nin tamamlanması **Faz
    6'nın tamamlanması anlamına gelmez** (bkz. Bölüm 22 üst metni,
    FAZ6C/FAZ6D durumu aşağıda).

FAZ 6C — Advanced Overfitting Controls
    purging/embargo (Bölüm 17.1), CPCV (17.2), Deflated Sharpe (17.4),
    PBO (17.5), multiple-testing corrections (17.6), parameter stability
    (17.7). Bu liste FAZ6C-owned'dır ve yalnızca bu maddeleri kapsar —
    Sharpe-ailesi foundation'ın kendisi (17.3) ve candidate/trial
    abstraction (18) FAZ6B-owned'dır (yukarıda); FAZ6C yalnızca onların
    ÜZERİNE inşa edilen ileri seviye kontrolleri kapsar.

    Tamamlanan bileşenler:
      - Window-level purging/embargo exact kontratı (Bölüm 17.1.1–
        17.1.13) — LOCKED VE artık İMPLEMENT EDİLMİŞ + TEST EDİLMİŞTİR:
        yeni `src/crypto_quant_lab/validation/purging.py` modülü, üç
        bağımsız fonksiyon (`windows_overlap`, `embargo_boundary`,
        `purge_in_sample_windows`), Bölüm 7'nin zaten LOCKED IS/OOS
        non-overlap invariant'ının N-pencereli genellemesi ("purge") +
        explicit, caller-supplied `embargo: timedelta` (asla inference
        edilmeyen bir post-OOS buffer, "embargo") — exact
        formüller/operation sırası, exact validation/fail-fast sırası,
        purity/import-direction, tümü kilitlendiği gibi implement
        edildi; hiçbir mevcut production dosyası (windows.py/rolling.py/
        metrics.py/candidate.py/annualized_metrics.py/models.py)
        değişmedi. Label/outcome-horizon'a bağlı KLASİK purging hâlâ
        deferred'dir (17.1.13) — repository'de böyle bir horizon
        kavramı yok. Kendi regression suite'i
        `tests/test_validation_purging.py`'de (62 test, tümü PASS);
        bkz. Bölüm 23, 28.I — 19/19.

      - Karşılaştırılabilir deneme grubu + kaydedilmiş Trial sayısı
        exact kontratı (Bölüm 20.1–20.13) — LOCKED VE artık İMPLEMENT
        EDİLMİŞ + TEST EDİLMİŞTİR: yeni
        `src/crypto_quant_lab/validation/trial_group.py` modülü
        (`TrialGroup`, `recorded_trial_count`). Deflated Sharpe (17.4) ve
        multiple-testing corrections'ın (17.6) "trial history /
        trial-count" önkoşulunun YALNIZCA tek-grup, ham kayıt-sayımı
        kısmını karşılar; FAZ6C listesine yeni bir madde EKLEMEZ,
        17.4/17.6'nın alt-foundation'ıdır (purging/embargo
        foundation'ının 17.1'e ait olması gibi). Hiçbir mevcut production
        dosyası değişmedi. Kendi regression suite'i
        `tests/test_validation_trial_group.py`'de (99 test, tümü PASS);
        bkz. Bölüm 20.14, 23, 28.J — 19/19. (Kontrat kilit zamanında —
        commit `1b666fd` — bu madde "kilitlenmiş, henüz implement
        edilmemiş" olarak kaydedilmişti, §28.J — 0/19.)

      - Deflated Sharpe exact kontratı (Bölüm 17.4.1–17.4.17) — LOCKED
        VE artık İMPLEMENT EDİLMİŞ + TEST EDİLMİŞTİR:
        `compute_deflated_sharpe_ratio`
        (`src/crypto_quant_lab/validation/deflated_sharpe.py`, YENİ
        modül); kendi regression suite'i
        `tests/test_validation_deflated_sharpe.py`'de (109 test, tümü
        PASS); mpmath tabanlı bağımsız nümerik doğrulama (§17.4.12);
        bkz. Bölüm 17.4.17, 23, 28.K — 27/27. Efektif-N estimator'ı ve
        çok pencereli pooling deferred (§17.4.14). (Kilit zamanında —
        commit `65273d7` — "kilitlenmiş, henüz implement edilmemiş",
        §28.K — 0/27.)

      - PBO önkoşulu olan hizalı trial return matrix foundation
        (Bölüm 17.5.1–17.5.12) — LOCKED VE İMPLEMENT EDİLMİŞ + TEST
        EDİLMİŞTİR: `TrialReturnMatrix`, `build_trial_return_matrix`
        (`src/crypto_quant_lab/validation/return_matrix.py`, YENİ);
        kendi regression suite'i `tests/test_validation_return_matrix.py`
        (55 test, tümü PASS); bkz. Bölüm 23, 28.L — 22/22. PBO'nun
        kendisi DEĞİLDİR.

      - PBO / CSCV (Bölüm 17.5.13–17.5.24) — LOCKED VE İMPLEMENT
        EDİLMİŞ + TEST EDİLMİŞTİR: `compute_probability_of_backtest_
        overfitting`, `PboResult`, `CscvCombination`
        (`src/crypto_quant_lab/validation/pbo.py`, YENİ); kendi
        regression suite'i `tests/test_validation_pbo.py` (35 test,
        tümü PASS); bkz. Bölüm 23, 28.M — 24/24. CSCV, CPCV DEĞİLDİR.

      - Multiple-testing için Holm düzeltme temeli (Bölüm 17.6.1–
        17.6.10) — LOCKED VE İMPLEMENT EDİLMİŞ + TEST EDİLMİŞTİR:
        `apply_holm_correction` (`src/crypto_quant_lab/validation/
        multiple_testing.py`, YENİ); kendi regression suite'i
        `tests/test_validation_multiple_testing.py` (41 test, tümü
        PASS); bkz. Bölüm 23, 28.N — 18/18. Yalnızca düzeltme
        katmanıdır; multiple-testing başlığı TAMAMLANMADI.

    Kalan zorunlu bileşenler (HENÜZ PENDING):
      - CPCV (17.2) ve parameter stability (17.7) — spec-lock
        edilmemiştir; multiple-testing (17.6) için geçerli p-değeri
        üretimi, aile kapsamı ve sonuç seçim politikası (§17.6.10)
        AÇIKTIR.

    Durum: FAZ6C — NOT COMPLETE. Purging/embargo foundation'ının
    implement/test edilmiş olması, CPCV/Deflated Sharpe/PBO/multiple-
    testing corrections/parameter stability'nin hiçbirinin
    tamamlandığı veya FAZ6C'nin tamamlandığı anlamına GELMEZ.

FAZ 6D — Faz 6 Final Acceptance
    tüm binding BACKTEST_SPEC Bölüm 26 maddelerinin ya implement edildiğinin
    ya da (yalnızca approved bir BACKTEST_SPEC revizyonuyla) yeniden
    kapsamlandığının audit'i.

    Durum: FAZ6D — NOT STARTED.
```

**LOCKED:** 6A'nın tamamlanması **Faz 6'nın tamamlanması anlamına gelmez.** `BACKTEST_SPEC.md` Bölüm 26'daki her madde ya implement edilir ya da yalnızca dokümante edilmiş bir spec revizyonu ile yeniden kapsamlandırılır — sessizce "foundation yeterli" denip kapatılmaz.

### 22.1 Historical Microstep Label vs. Capability Ownership (LOCKED)

`§23`'teki microstep log, `FAZ6B MS2` (zero-context rolling orchestrator, commit `c363267`/`c4af87c`) ve `FAZ6B MS3`–`MS5` (Stage-1 metrics pre-flight/lock/implementation, commit `76002ab`/`a265e44`/`05eaa2b`) label'larını kullanır — bu label'lar **tarihsel olarak doğrudur ve yeniden yazılmaz, yeniden numaralandırılmaz, veya fabrikasyon edilmiş yeni bir label ile değiştirilmez.** Bu iş, aşağıdaki gerekçelerle FAZ6B-numaralı workstream'de yürütüldü:

```
- Policy-instance-freshness (Bölüm 8.3.6), zero-context rolling
  orchestrator'ın inşası için cross-cutting bir prerequisite'ti ve
  FAZ6B MS1'de spec-lock edilmişti (bkz. §22'nin FAZ6B "Tamamlanan
  bileşenler" listesi) — orchestrator'ın implementasyonu (MS2)
  doğal olarak aynı numaralı workstream'in devamı olarak yürütüldü.
- Context/evaluation mimarisi (B2, Bölüm 8.3) ve metrics staging
  (Bölüm 15) aynı dönemde, birbiriyle ilişkili olarak geliştiriliyordu
  — mikro-adım numaralandırması bu geliştirme sırasını, nihai capability
  ownership'i değil, yansıtır.
```

**Prosedürel label, §22'de kilitlenen nihai capability-ownership'i EZMEZ (override etmez).** Bir okuyucu "FAZ6B MS2/MS3/MS4/MS5 nerede tamamlandı" sorusuna `§23`'ten cevap bulur; "hangi capability hangi alt-faza AİTTİR" sorusuna ise yalnızca `§22`'den cevap bulur — bu iki soru farklıdır ve bu doküman onları artık karıştırmaz.

### 22.2 Faz 6 Phase-Status Table (LOCKED — Kompakt Özet)

| Phase | Status | Completed scope | Remaining scope |
|---|---|---|---|
| FAZ6A | COMPLETE | temporal window/IS-OOS primitives (§28.A — 22/22), zero-context rolling OOS evaluation (§28.C — 12/12), Stage-1 metrics (§28.D — 18/18) | locked FAZ6A scope içinde yok |
| FAZ6B | COMPLETE | Layer-1 context/evaluation mimarisi (§28.B — 15/15), policy-instance-freshness foundation (§8.3.6), return-series + per-observation Sharpe (§15.9–15.18, §28.E — 29/29, LOCKED VE IMPLEMENTED + TESTED), non-zero-context Layer-2 (§8.3.16, §28.F — 22/22, LOCKED VE IMPLEMENTED + TESTED), candidate/trial foundation (§18, §28.G — 25/25, LOCKED VE IMPLEMENTED + TESTED), Annualized Metrics (§15.19–15.33, §28.H — 30/30, LOCKED VE IMPLEMENTED + TESTED) | locked FAZ6B scope içinde yok |
| FAZ6C | NOT COMPLETE | purging/embargo exact kontrat + implementasyonu (§17.1.1–17.1.13, §28.I — 19/19, LOCKED VE IMPLEMENTED + TESTED); trial-group + kaydedilmiş Trial sayısı exact kontratı + implementasyonu (§20.1–20.14, §28.J — 19/19, LOCKED VE IMPLEMENTED + TESTED); Deflated Sharpe exact kontratı + implementasyonu (§17.4.1–17.4.17, §28.K — 27/27, LOCKED VE IMPLEMENTED + TESTED); PBO önkoşulu trial return matrix (§17.5.1–17.5.12, §28.L — 22/22, LOCKED VE IMPLEMENTED + TESTED); PBO/CSCV (§17.5.13–17.5.24, §28.M — 24/24, LOCKED VE IMPLEMENTED + TESTED); Holm düzeltme temeli (§17.6.1–17.6.10, §28.N — 18/18, LOCKED VE IMPLEMENTED + TESTED) | CPCV, multiple-testing p-değeri üretimi/aile kapsamı/seçim politikası, multiple-testing corrections, parameter stability |
| FAZ6D | NOT STARTED | yok | Faz 6 final acceptance audit'i |

Bu tablo, §28.A/B/C/D'nin bağımsız acceptance sayımlarını **birleşik bir yüzdeye veya tek bir sayıya dönüştürmez** — her grup kendi bağımsız kanıtını korur; bu tablo yalnızca hangi grubun hangi alt-fazın kanıtı olduğunu özetler.

### 22.3 FAZ6A Completion — Kapsam ve Kanıt (LOCKED)

**FAZ6A: COMPLETE.** Bu, yukarıdaki FAZ6A tanımının (temporal window primitive, IS/OOS split, fixed-policy rolling OOS evaluation, basic return/drawdown metrics) dört maddesinin **tamamının** implement edilmiş, test edilmiş ve dokümante edilmiş olduğu anlamına gelir:

```
1. TemporalWindow primitive — implemented/tested/documented
   (src/crypto_quant_lab/validation/windows.py, Bölüm 6, §28.A).
2. TemporalSplit IS/OOS primitive — implemented/tested/documented
   (src/crypto_quant_lab/validation/windows.py, Bölüm 7, §28.A).
3. Zero-context rolling fixed-policy OOS evaluation — implemented/
   tested/documented: WindowResult, run_rolling_backtest_from_store,
   zero-context Layer-2 için policy freshness (§8.3.6, §13); §28.C —
   12/12 PASS.
4. Basic Stage-1 metrics — implemented/tested/documented:
   Stage1Metrics, compute_stage1_metrics, total return, maximum
   drawdown (§15.1–15.8); §28.D — 18/18 PASS.
```

Bu tamamlanma **capability-based**'dir — §28.A'nın 22 maddesi, §28.C'nin 12 maddesi ve §28.D'nin 18 maddesi ayrı, bağımsız sayımlar olarak **DEĞİŞMEDEN** kalır; bu bölüm onları yeni birleşik bir sayıya katlamaz.

**FAZ6A completion açıkça ŞUNLARI İDDİA ETMEZ:**

```
- Context-aware / non-zero-context rolling'in tamamlandığını
  (bu FAZ6B-owned'dır, §22).
- Stage-2 (return-series/Sharpe) veya Stage-3 (Deflated Sharpe/PBO/
  multiple-testing/parameter stability) metriklerinin tamamlandığını.
- FAZ6B, FAZ6C, veya genel Faz 6'nın tamamlandığını.
```

**Zero-context'in yeterliliği (gerekçe):** Bölüm 13'ün kendi ifadesiyle, context/lookback kullanmayan bir policy için mevcut per-window `run_backtest_from_store` çağrısı **zaten doğru sonucu üretir** — bu nedenle zero-context rolling, FAZ6A'nın "fixed-policy rolling OOS evaluation" maddesini baseline seviyede karşılar. Context/warm-up (Layer-1 VE non-zero-context Layer-2) FAZ6B'nin kendi başlığında ("Context-Aware Extensions...") açıkça sahiplenilen bir **uzantıdır** — FAZ6A'nın dört maddesinden hiçbiri context-awareness'i zorunlu kılmaz.

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

FAZ6 PHASE-GATE AND NEXT-DEPENDENCY AUDIT (READ-ONLY) — TAMAMLANDI:
  — Bu doküman ve §23'ün kendisi (o zamanki hâli), ROADMAP.md, ve
  production/test surface'ları okunarak bağımsız bir phase-gate audit'i
  yapıldı. Bulgu: FAZ6A capability gate PASS, FAZ6B capability gate
  FAIL; §22'nin FAZ6A bulleti ile FAZ6B başlığı/bulleti arasında, ve
  §23'ün FAZ6B-prefixed microstep label'ları ile §22'nin FAZ6A capability
  tanımı arasında bir ownership-wording ambiguity tespit edildi. Docs-only,
  read-only; hiçbir dosya değiştirilmedi.

FAZ6 PHASE-STATUS RECONCILIATION — FAZ6A/FAZ6B OWNERSHIP AND COMPLETION —
TAMAMLANDI:
  — Yukarıdaki audit'in bulduğu ownership-wording ambiguity'yi çözdü:
  §22'yi bir "Capability Ownership Model" ile genişletti (§22, 22.1,
  22.2, 22.3); FAZ6A'yı capability-based olarak explicit COMPLETE ilan
  etti; FAZ6B'nin başlığını/bulletini yeniden yazarak Stage-1 metrics'in
  artık ambiguous şekilde FAZ6B'ye ait görünmesini giderdi ve FAZ6B'yi
  explicit NOT COMPLETE ilan etti (kalan: non-zero-context Layer-2,
  return-series/Sharpe, candidate/trial abstraction); Aşama 3'ü
  (Deflated Sharpe/PBO/multiple-testing/parameter stability) yalnızca
  FAZ6C-owned olarak netleştirdi (önceden hem FAZ6B'nin "kalan iş"
  listesinde hem FAZ6C bulletinde görünüyordu); kompakt bir phase-status
  tablosu ekledi (§22.2). Historical `FAZ6B MS2`–`MS5` label'ları
  DEĞİŞTİRİLMEDİ/yeniden yazılmadı — yalnızca §22.1'de neden bu
  label'ların kullanıldığı kaydedildi. Docs-only; production kod,
  test, veya acceptance sayımı (§28.A/B/C/D) değişmedi.

FAZ6B — RETURN-SERIES + PER-OBSERVATION SHARPE SEMANTICS PREFLIGHT
(READ-ONLY) — TAMAMLANDI:
  — `equity_curve`'ün zaten yalnızca evaluation-fazı noktalarını
  içerdiğini (context candle'ların SIFIR EquityPoint ürettiği,
  `replay.py`'deki `continue`/`append` sırasıyla runtime'da doğrulanmış)
  tespit etti; bu nedenle Stage-2'nin ayrı bir evaluation-boundary
  parametresine veya `BacktestResult` uzantısına ihtiyaç DUYMADIĞI
  sonucuna vardı. Dönüş formülü, gözlem sayısı (N), payda-sıfır/negatif
  davranışı, mean/sample-stdev/Sharpe konvansiyonu için evidence-backed
  öneriler üretti; annualization'ı ayrı, henüz kilitlenmemiş bir konu
  olarak bıraktı. Docs-only bulgu; hiçbir dosya değiştirilmedi — bu
  preflight'in kendisi commit edilmedi, bulguları doğrudan aşağıdaki
  spec-lock'a girdi oldu.

FAZ6B — RETURN-SERIES + PER-OBSERVATION SHARPE SEMANTICS SPEC-LOCK —
TAMAMLANDI:
  — Yukarıdaki preflight'in bulgularını Bölüm 15.9–15.18'de LOCKED
  olarak kaydetti: evaluation-domain inheritance kuralı (ikinci bir
  filtre YOK, Bölüm 15.10), `compute_periodic_returns`/`Stage2Metrics`/
  `compute_stage2_metrics` public API'si (Bölüm 15.11–15.12), N equity
  noktası → N return ve initial_cash-seeded ilk return (Bölüm 15.13),
  per-observation/non-annualized timestamp semantics (Bölüm 15.14),
  validation/fail-fast sırası (Bölüm 15.15), arithmetic mean + sample
  stdev (n-1) + non-annualized Sharpe formülleri (Bölüm 15.16), Stage-1
  ile paylaşılan private Decimal-context (Bölüm 15.17), ve purity/
  explicit-deferred-metrics sınırı (Bölüm 15.18). Bölüm 16, 17.3, 22
  bu kilitle tutarlı hale getirildi; annualized Sharpe/Sortino/Calmar/
  CAGR açıkça deferred kaldı. Yalnızca `VALIDATION_SPEC.md` değişti;
  hiçbir production kod, `metrics.py` değişikliği, veya yeni test
  içermedi — Stage-2 implementasyonu ve regression suite'i HENÜZ
  BAŞLAMADI. §28.E acceptance grubu 0/29 olarak eklendi.

FAZ6B — STAGE-2 RETURN-SERIES + PER-OBSERVATION SHARPE IMPLEMENTATION —
TAMAMLANDI:
  — Bölüm 15.9–15.18'de LOCKED olan Stage-2 kontratını implement etti
  (commit `e4cedf9`):
  - `compute_periodic_returns`, `Stage2Metrics` (frozen, slots),
    `compute_stage2_metrics` — TAMAMLANDI
    (src/crypto_quant_lab/validation/metrics.py). Stage-1'in
    steps-1-9 validation'ı ve private Decimal-context factory'si,
    davranış DEĞİŞMEDEN, `_require_core_result_contract`/
    `_metrics_decimal_context` olarak Stage-1 ile paylaşılan private
    helper'lara refactor edildi.
  - Genişletilmiş `tests/test_validation_metrics.py` — 207 test
    (82 Stage-1 DEĞİŞMEDEN + 125 yeni Stage-2), tümü PASS.
  - İlgili regression suite'ler (test_backtest_models.py,
    test_backtest_results.py, test_validation_rolling_backtest.py,
    test_backtest_replay_context_evaluation.py,
    test_backtest_store_runner_context_evaluation.py — 148 test)
    DEĞİŞMEDEN yeşil kaldı; tam suite 1593/1593 PASS.
  - post-commit implementasyon audit'i — PASS
  - 28.E Stage-2 acceptance/status reconciliation (bu doküman
    güncellemesi) — TAMAMLANDI

FAZ6B — STAGE-2 POST-IMPLEMENTATION AUDIT + DOCUMENTATION/ACCEPTANCE
CLOSURE — TAMAMLANDI:
  — Commit `e4cedf9`'u Bölüm 15.9–15.18'deki kilitli kontrata karşı
  bağımsız olarak audit etti (API şekli, validation/fail-fast sırası,
  payda kontrolü, exact formül/operation sırası, evaluation-domain
  inheritance, mean/stdev/Sharpe, Decimal-context, purity/compatibility,
  Stage-1'in DEĞİŞMEDEN kaldığı) — PASS, hiçbir material çelişki/
  regression/eksik kanıt bulunmadı. Hedefli suite (207/207) yeniden
  çalıştırıldı. §28.E'yi 0/29'dan 29/29'a, her kriter için concrete
  implementasyon/test kanıtıyla güncelledi; Bölüm 15, 16, 17.3, 22, 23,
  28 giriş paragrafını Stage-2'nin artık implement + test edildiğini
  yansıtacak şekilde günceledi. Docs-only; production kod veya test
  değişikliği içermedi.

FAZ6B — NON-ZERO-CONTEXT LAYER-2 SOURCE PREFLIGHT + EXACT CONTRACT
LOCK — TAMAMLANDI:
  — Mevcut zero-context Layer-2 (`run_rolling_backtest_from_store`) ve
  context-aware Layer-1'in (`evaluation_start`, Bölüm 8.3.1–8.3.15)
  kaynak kodunu doğrudan doğrulayan bir source-preflight yaptı; 7
  mimari alternatifi karşılaştırdı ve seçti/reddetti; exact kontratı
  Bölüm 8.3.16'da LOCKED olarak kaydetti: `ContextAwareWindow`
  (frozen, slots, `src/crypto_quant_lab/validation/rolling.py`) +
  `run_context_aware_rolling_backtest_from_store` (aynı modül, mevcut
  zero-context runner'ı DEĞİŞTİRMEYEN additive bir ikinci public
  fonksiyon); boundary/warm-up/freshness/validation/store-funding/
  output/metrics-compatibility/purity semantiklerinin tamamını, mevcut
  Layer-1/Layer-2 mekanizmalarının birebir reuse'u olarak (hiçbir yeni
  ekonomi/warm-up mekanizması icat etmeden) kilitledi. Implementasyon
  için gerekli test kontratını ve exact dosya kapsamını kaydetti. §28.F
  acceptance grubunu, kontrattan türetilen 22 kriterle, 0/22 olarak
  ekledi. Docs-only; production kod, `rolling.py` değişikliği, veya
  yeni test içermedi.

FAZ6B — NON-ZERO-CONTEXT LAYER-2 IMPLEMENTATION + TESTS + AUDIT +
DOCUMENTATION/ACCEPTANCE CLOSURE — TAMAMLANDI:
  — Bölüm 8.3.16'da LOCKED olan kontratı tek bir combined delivery'de
  implement etti:
  - `ContextAwareWindow` (frozen, slots) + `run_context_aware_rolling_backtest_from_store`
    — TAMAMLANDI (`src/crypto_quant_lab/validation/rolling.py`); mevcut
    `run_rolling_backtest_from_store` ile paylaşılan private
    `_execute_windows` yürütme helper'ına refactor edildi — zero-context
    runner'ın davranışı byte-for-byte DEĞİŞMEDEN (28 mevcut test
    DEĞİŞMEDEN yeşil kanıtıyla).
  - `tests/test_validation_rolling_backtest.py` genişletildi — 94 test
    (28 mevcut zero-context DEĞİŞMEDEN + 66 yeni non-zero-context),
    tümü PASS.
  - İlgili regression suite'ler (`test_backtest_replay_context_evaluation.py`,
    `test_backtest_store_runner_context_evaluation.py`,
    `test_validation_metrics.py` — 250 test) DEĞİŞMEDEN yeşil kaldı;
    tam suite 1659/1659 PASS.
  - post-commit implementasyon audit'i — Bölüm 28.F'nin 22 kriterinin
    tamamı için concrete davranışsal/static kanıt doğrulandı — PASS.
  - 28.F non-zero-context Layer-2 acceptance/status reconciliation (bu
    doküman güncellemesi) — TAMAMLANDI: 0/22 -> 22/22.

FAZ6B — CANDIDATE/TRIAL SOURCE PREFLIGHT + EXACT CONTRACT LOCK —
TAMAMLANDI:
  — `BacktestPolicy`/`PolicyContext` Protocol'ünü, `WindowResult`'ın
  "hiçbir candidate identity taşımadığını", her iki rolling runner'ın
  factory-based policy-freshness reuse-detection'ını, `BacktestResult`'ın
  eksik provenance alanlarını (exchange/market_type/symbol/timeframe/
  as_of_time/cost_model/funding_model — kurtarılamaz), `metrics.py`'nin
  rolling/candidate concern'lerinden tam bağımsızlığını, ve
  `pyproject.toml`'un sıfır runtime dependency'sini doğrudan kaynak
  koddan doğrulayan bir source-preflight yaptı; candidate/trial için
  5 mimari karar alanını (candidate temsili, parametre domain'i, trial
  temsili, execution composition, modül yerleşimi) toplam 40+ alternatif
  karşısında karşılaştırdı ve seçti/reddetti; exact kontratı Bölüm 18'de
  LOCKED olarak kaydetti: `Candidate` (frozen/slots, `candidate_id: str`
  + `parameters: tuple[tuple[str, ParameterValue], ...]`) ve `Trial`
  (frozen/slots, `candidate` + `results: tuple[WindowResult, ...]` +
  provenance alanları), yeni `src/crypto_quant_lab/validation/candidate.py`
  modülünde, NO evaluator function, NO selection/test role field (Bölüm
  19'un engine-vs-process ayrımıyla tutarlı, §18.7). Candidate kimliği/
  parametre domain'i/canonical-order kuralı (§18.6), trial evidence/
  provenance/validation-fail-fast sırası (§18.7–18.8), leakage/selection
  açık sınırları (§18.9 — bu kontrat train/select/test split, best-
  candidate selection, holdout protection, multiple-testing correction,
  purging/embargo/CPCV/Deflated-Sharpe/PBO/parameter-stability/
  annualization/cross-window aggregation SAĞLAMAZ), purity/determinism/
  import-direction (§18.10, acyclic — candidate.py `rolling.py`'den
  yalnızca `WindowResult` tipini import eder, tersi YOK), implementasyon
  dosya kapsamı (§18.11), ve test kontratı (§18.12) kilitlendi.
  Hashability empirik olarak (canlı sandbox check ile) doğrulandı.
  §28.G acceptance grubu, kontrattan türetilen 25 kriterle, 0/25 olarak
  eklendi; §28 giriş paragrafı altıdan yediye güncellendi. Docs-only;
  production kod, `candidate.py` implementasyonu, veya yeni test
  içermedi — candidate/trial implementasyonu ve regression suite'i
  HENÜZ BAŞLAMADI.

FAZ6B — CANDIDATE/TRIAL FOUNDATION IMPLEMENTATION + TESTS + AUDIT +
DOCUMENTATION/ACCEPTANCE CLOSURE — TAMAMLANDI:
  — Bölüm 18'de LOCKED olan candidate/trial exact kontratını tek bir
  combined delivery'de implement etti (non-zero-context Layer-2
  implementasyonunun izlediği AYNI precedent):
  - `ParameterValue`, `Candidate` (frozen/slots), `Trial` (frozen/slots)
    — TAMAMLANDI (`src/crypto_quant_lab/validation/candidate.py`, YENİ
    modül); yalnızca `BacktestConfig` (backtest/models), `WindowResult`
    (validation/rolling, yalnızca value-model tipi için), ve
    `datetime_to_epoch_us` (storage/sqlite_codec) import eder — Bölüm
    18.10'da LOCKED olan import listesiyle birebir.
  - `tests/test_validation_candidate.py` (YENİ dosya) — 132 test, tümü
    PASS: Candidate value semantics (candidate_id/parametre yapısı/
    duplicate/canonical-order/tüm value-domain tipleri/NaN-Infinity-
    float-mutable-container-custom-object reddi/eşzamanlı çoklu ihlal
    sırası), Trial semantics (candidate/results/provenance/as_of_time/
    config/initial_cash tutarlılığı/eşzamanlı çoklu ihlal sırası/
    duplicate-overlapping window kabulü/mutasyon-kopyalama YOKLUĞU/
    role-score-holdout field'ının absence-of-field kanıtı), static/non-
    coupling (locked modül yolu, package-root export YOKLUĞU, evaluator/
    optimizer sembolü YOKLUĞU, candidate.py'nin metrics/policy/rolling-
    runner import'u YOKLUĞU, rolling.py/metrics.py/windows.py'nin
    candidate import'u YOKLUĞU), ve gerçek rolling-path entegrasyonu
    (`run_rolling_backtest_from_store`'dan üretilen gerçek bir
    `tuple[WindowResult, ...]`'ın bir Trial'a paketlenip Stage-1/Stage-2
    metriklerinin `trial.results[i].result` üzerinden bağımsız olarak
    hesaplanabildiğinin kanıtı).
  - İlgili regression suite'ler (`test_validation_windows.py`,
    `test_validation_rolling_backtest.py`, `test_validation_metrics.py`,
    `test_backtest_models.py`, `test_backtest_results.py` — 404 test)
    DEĞİŞMEDEN yeşil kaldı; tam suite 1791/1791 PASS (1659 mevcut + 132
    yeni).
  - post-implementation audit'i — Bölüm 18'in tüm invariant'ları ve
    Bölüm 28.G'nin 25 kriterinin tamamı için concrete davranışsal/static
    kanıt doğrulandı — PASS.
  - Değiştirilen mevcut production/test dosyası: YOK (rolling.py,
    metrics.py, windows.py, models.py, policy.py, ve tüm mevcut testler
    DEĞİŞMEDEN — statik `git diff` kanıtı + tam regression suite
    uyumluluğu).
  - 28.G candidate/trial foundation acceptance/status reconciliation (bu
    doküman güncellemesi) — TAMAMLANDI: 0/25 -> 25/25.
  - Candidate selection/ranking, optimizer/grid/random/Bayesian search,
    final holdout protection, multiple-testing correction, ve Stage-3
    (annualized metrics, Deflated Sharpe, PBO, parameter stability) bu
    delivery'de BAŞLATILMADI — bunlar ayrı, henüz spec-lock edilmemiş
    gelecekteki adımlardır (§18.9, §18.13).

FAZ6B — CANDIDATE GLOBAL FAIL-FAST ORDER CORRECTION — TAMAMLANDI:
  — Post-delivery review, yukarıdaki combined delivery'nin implementasyon/
  dokümantasyonunda tek bir exact contract deviation tespit etti: §18.8'in
  kilitlediği `Candidate.__post_init__` adım 4-9 sırası GLOBAL geçişlerdir
  (adım N TÜM `parameters` girişleri için tamamlanmadan adım N+1 HİÇBİR
  girişi incelemez) — ama implementasyon bunun yerine adım 4-8'i HER index
  için TEK bir birleşik geçişte uyguluyordu (index 0 tam geçmeden index 1'e
  geçilmiyordu), ve implementasyon-closure dokümantasyonu bu YANLIŞ "per-
  index" yorumunu LOCKED contract'ın kendisiymiş gibi kaydetmişti. Somut
  çapraz-index hatası: `parameters=((123, "v"), ("bad","shape","toolong"))`
  girdisinde, LOCKED global sıra adım-4 (entry şekli) ihlalinin index 1'de
  KAZANMASINI gerektirir (adım-4 TÜM girişler için ÖNCE tamamlanmalıdır) —
  ama eski implementasyon yanlışlıkla index 0'daki adım-5 (key tipi)
  ihlalini raise ediyordu.
  - Düzeltme: `Candidate.__post_init__`, adım 4 (entry şekli)/5 (key
    tipi)/6 (key içeriği)/7 (duplicate key)/8 (canonical order)/9 (value
    domain) için ALTI AYRI, TAM `parameters` üzerinden geçen döngü
    kullanacak şekilde yeniden yazıldı — TAMAMLANDI
    (`src/crypto_quant_lab/validation/candidate.py`). Yeni private
    helper'lar (`_require_str_type`, `_require_canonical_content`)
    eklendi; `_require_canonical_identifier` (candidate_id VE Trial
    provenance field'ları için tek-alan tip+içerik kontrolü) DEĞİŞMEDEN
    davranışsal olarak korundu (bu iki yeni helper'ın üzerine yeniden
    inşa edildi). Public API, dataclass field'ları, hata kategorileri,
    index-specific raporlama, ve Trial davranışı TAMAMEN DEĞİŞMEDEN.
  - `tests/test_validation_candidate.py`'e 16 yeni dedicated davranışsal
    test eklendi — TAMAMLANDI: adım 4'ün 5-9'un HER BİRİNDEN, adım 5'in
    6-9'un HER BİRİNDEN, adım 6'nın 7-9'un HER BİRİNDEN, adım 7'nin 8-9'un
    HER İKİSİNDEN, ve adım 8'in 9'dan — index sırasından BAĞIMSIZ olarak —
    HER ZAMAN önce geldiğini, exact exception type/message assertion ile
    ve yalnızca public `Candidate` API üzerinden kanıtlar (132 -> 148
    test, tümü PASS).
  - İlgili regression suite'ler (test_validation_windows.py,
    test_validation_rolling_backtest.py, test_validation_metrics.py,
    test_backtest_models.py, test_backtest_results.py — 404 test)
    DEĞİŞMEDEN yeşil kaldı; tam suite 1791/1791 -> 1807/1807 PASS (16
    yeni test).
  - post-correction audit'i — düzeltilmiş implementasyonun global-pass
    sırasını doğru uyguladığı, Trial'ın etkilenmediği, ve mevcut 132
    testin tamamının hâlâ yeşil kaldığı doğrulandı — PASS.
  - 28.G'nin 25 kriteri, düzeltilmiş implementasyon/testler karşısında
    yeniden doğrulandı ve 25/25'te DEĞİŞMEDEN kaldı (kriter 6/7/8'in
    kanıt listesine yeni global-pass testleri eklendi) — TAMAMLANDI.
  - Bölüm 18.14'e "Candidate global fail-fast sıra düzeltmesi" notu
    eklendi; önceki YANLIŞ "per-index" yorum paragrafı KALDIRILDI/
    DÜZELTİLDİ. Değiştirilen dosyalar: yalnızca
    `src/crypto_quant_lab/validation/candidate.py`,
    `tests/test_validation_candidate.py`, `VALIDATION_SPEC.md` — hiçbir
    başka production/test dosyası dokunulmadı.

FAZ6B — ANNUALIZED METRICS SOURCE-PREFLIGHT + EXACT CONTRACT LOCK —
TAMAMLANDI:
  — market_data/timeframes.py (`candle_duration` — yalnızca "1h"/"4h",
  sıfır cross-dependency), BacktestResult/EquityPoint'in hiçbir
  timeframe/periods_per_year field'ı taşımadığı (Bölüm 15.14'ün
  bulgusunun tekrar doğrulanması), initial_cash'in gerçekleştiği anın
  (evaluation_start) hiçbir result-modelinde saklanmadığı, Trial'ın
  `timeframe` provenance field'ı taşımasına rağmen annualized metrics'e
  COUPLE EDİLMEMESİ gerektiği (§18.9), metrics.py'nin PUBLIC fonksiyonları
  (compute_stage1_metrics/compute_periodic_returns/compute_stage2_metrics)
  ile PRIVATE helper'larının (`_` prefix, cross-module import EDİLEMEZ),
  ve repo genelinde önceden LOCKED bir calendar-basis/annualization
  konvansiyonunun YOKLUĞUNU doğrudan kaynak koddan doğrulayan bir
  source-preflight yaptı. Python 3.13 stdlib `Decimal`'in fractional
  power/sqrt davranışını (pozitif base: finite exact; sıfır base +
  pozitif üs: finite sıfır; negatif base + fractional üs: NaN, raise
  ETMEZ; 1**herhangi: exact 1; ambient-context bağımlılığı yalnızca
  localcontext dışında) canlı, salt-okunur probe'larla ampirik olarak
  doğruladı — hiçbir implementasyon-edilemez API bulunmadı.
  Mimari kararları karşılaştırdı ve seçti/reddetti: adlandırma
  ("Annualized Metrics," "Stage-3" DEĞİL — bu terim zaten Deflated
  Sharpe/PBO/multiple-testing/parameter-stability için ayrılmıştı),
  modül yerleşimi (yeni `annualized_metrics.py` — metrics.py'nin KENDİ
  LOCKED docstring'i annualized metrikleri açıkça dışladığından),
  dört bağımsız bare-Decimal-dönen fonksiyon (TEK bir bundled dataclass
  DEĞİL — all-or-nothing failure modu riski), annualization-input şekli
  (explicit `timeframe: str`, `candle_duration` reuse edilerek —
  explicit `periods_per_year` veya inference DEĞİL), calendar basis
  (365 gün exact, sabit — 365.25/252-trading-day REDDEDİLDİ), CAGR'ın
  duration kaynağı (return-period count + periods_per_year — elapsed
  wall-clock timestamp DEĞİL, çünkü mekanik olarak geçerli bir initial-
  timestamp kaynağı yok), Sortino'nun downside-deviation kaynağı
  (compute_periodic_returns, compute_stage2_metrics DEĞİL — Sortino'nun
  geçerliliği Stage-2'nin total-stdev>0 şartından bağımsız olmalı) ve
  downside-deviation konvansiyonu (population-divisor, TÜM N gözlem
  paydada — yalnızca-downside-count REDDEDİLDİ).
  Exact kontratı Bölüm 15.19–15.33'te LOCKED olarak kaydetti:
  `compute_annualized_sharpe_ratio`, `compute_sortino_ratio`,
  `compute_cagr`, `compute_calmar_ratio` (hepsi
  `src/crypto_quant_lab/validation/annualized_metrics.py`, YENİ modül) —
  exact formüller/operation sıraları, dört fonksiyon için ayrı numaralı
  validation/fail-fast sıraları, private Decimal context (Stage-1/2 ile
  AYNI shape), purity/import-direction (acyclic — annualized_metrics.py
  yalnızca metrics.py'nin PUBLIC fonksiyonlarını ve market_data/
  timeframes.py'yi import eder; hiçbiri annualized_metrics.py'yi import
  ETMEZ). §28.H acceptance grubunu, kontrattan türetilen kriterlerle
  0/N olarak ekledi; §28 giriş paragrafını yediden sekize güncelledi.
  Docs-only; production kod, `annualized_metrics.py` implementasyonu,
  veya yeni test içermedi — Annualized Metrics implementasyonu ve
  regression suite'i HENÜZ BAŞLAMADI.

FAZ6B — ANNUALIZED METRICS IMPLEMENTATION + REGRESSION SUITE +
POST-IMPLEMENTATION AUDIT + DOCUMENTATION/ACCEPTANCE CLOSURE —
TAMAMLANDI (tek bir combined delivery olarak, Candidate/Trial ve
non-zero-context Layer-2 implementasyonlarının izlediği AYNI
precedent):
  Bölüm 15.19–15.33'te LOCKED olan exact kontratı, yeniden tasarlamadan,
  birebir implement etti: `src/crypto_quant_lab/validation/annualized_metrics.py`
  (YENİ modül) — `compute_annualized_sharpe_ratio`, `compute_sortino_ratio`,
  `compute_cagr`, `compute_calmar_ratio`; hepsi kilitli exact signature'larla
  (result pozisyonel; timeframe/rate/target keyword-only), bare `Decimal`
  döner, hiçbir yeni dataclass/value object tanıtılmadı. `Stage1Metrics`,
  `compute_stage1_metrics`, `Stage2Metrics`, `compute_periodic_returns`,
  `compute_stage2_metrics` DEĞİŞMEDEN reuse edildi — ikinci bir return-
  series/stdev/drawdown algoritması İCAT EDİLMEDİ. `candle_duration`
  (market_data/timeframes.py, DEĞİŞMEDEN) reuse edilerek exact
  `periods_per_year` (1h -> 8760, 4h -> 2190) integer-mikrosaniye
  aritmetiğiyle türetildi; 365-gün calendar basis sabit ve configurable
  DEĞİL. Dört fonksiyonun her biri kendi taze, private, module-privacy
  nedeniyle yeniden tanımlanmış Decimal context'i (Stage-1/2 ile AYNI
  shape) kullanır; her power/sqrt/division `localcontext(...)` içinde
  çalışır. Kilitli numaralı validation/fail-fast sıraları birebir
  uygulandı (timeframe str+candle_duration önce, sonra ilgili
  Stage-1/Stage-2/periodic-returns delegasyonu, sonra rate/target
  kontrolleri, sonra arithmetic, sonra finiteness check). Sortino,
  `compute_periodic_returns`'ü tüketir (`compute_stage2_metrics`'i DEĞİL)
  — bu, Stage-2'nin total-stdev>0 şartı ihlal edildiğinde bile (sabit/
  flat periyodik return serisi) Sortino'nun geçerli kalabildiği canlı bir
  testle davranışsal olarak kanıtlandı. `validation/__init__.py`,
  `metrics.py`, `rolling.py`, `windows.py`, `candidate.py`, `models.py`,
  `market_data/timeframes.py` DEĞİŞMEDEN kaldı (static `git diff` kanıtı).
  Kendi regression suite'i `tests/test_validation_annualized_metrics.py`'de
  (77 test, tümü PASS) — API/signature, timeframe/calendar validation
  sırası, dört formülün exact operation sırası, tüm edge-case'ler (sıfır/
  negatif/non-finite Sharpe, downside-gözlem-yokluğu, boundary-equality,
  total-wipeout, negatif final_equity, sıfır max_drawdown), ambient-
  context bağımsızlığı, no-mutation/determinism, ve gerçek canonical
  backtest + bağımsız `WindowResult.result` entegrasyonu dahil. Post-
  implementation audit — kontratın 30 kriterinin HER BİRİ implementasyon/
  test karşısında tek tek doğrulandı — PASS. Tam suite: 1807 (önceki
  baseline) + 77 (yeni) = 1884/1884 PASS. Ruff/format/`git diff --check`
  — hepsi temiz. Değiştirilen dosyalar: yalnızca
  `src/crypto_quant_lab/validation/annualized_metrics.py` (YENİ),
  `tests/test_validation_annualized_metrics.py` (YENİ), `VALIDATION_SPEC.md`
  — hiçbir başka production/test dosyası dokunulmadı. §28.H, 0/30'dan
  30/30'a kapatıldı; §22.2 tablosunda FAZ6B artık COMPLETE olarak
  işaretlendi (locked FAZ6B scope içinde kalan madde yok) — bu, Faz 6'nın
  tamamlandığı anlamına GELMEZ (FAZ6C/FAZ6D hâlâ NOT COMPLETE/NOT
  STARTED). Candidate selection/ranking, optimizer/grid/random/Bayesian
  search, final holdout enforcement, ve Stage-3 (Deflated Sharpe, PBO,
  multiple-testing corrections, parameter stability) bu delivery'de de
  BAŞLATILMADI — bunlar ayrı, henüz spec-lock edilmemiş gelecekteki
  adımlardır (FAZ6C/FAZ6D).

FAZ6C — HORIZON + PURGING/EMBARGO SOURCE PREFLIGHT + EXACT CONTRACT LOCK
— TAMAMLANDI (MS1):
  — `BacktestPolicy`'nin hiçbir explicit label/outcome-horizon kavramı
  taşımadığını (17.1 tarihsel bulgusunun tekrar doğrulanması),
  `TemporalWindow`/`TemporalSplit`'in (windows.py, DEĞİŞMEDEN) zaten
  half-open/non-overlap invariant'ları taşıdığını ama `TemporalSplit`'in
  kendi overlap check'inin tek-yönlü (yalnızca tek-IS/tek-OOS) olduğunu,
  rolling.py'nin duplicate/overlapping pencereleri legal bıraktığını ve
  hiçbir purge/embargo kavramı taşımadığını, candle_duration/context-
  lookback/Trial.timeframe'in hiçbirinin bir "embargo duration" ile
  repository-evidence eşdeğerliği olmadığını doğrudan kaynak koddan
  doğrulayan bir source-preflight yaptı. Horizon bağımlılığını dürüstçe
  ikiye ayırdı: (a) label/outcome-horizon'a bağlı KLASİK purging —
  repository'de hiçbir horizon kavramı olmadığından HÂLÂ implement
  EDİLEMEZ, deferred kalır (17.1.13); (b) pencere-seviyeli purge (Bölüm
  7'nin ZATEN LOCKED IS/OOS non-overlap invariant'ının N-pencereli
  genellemesi) + explicit, caller-supplied bir `embargo: timedelta`
  buffer'ı — hiçbir label-horizon'a İHTİYAÇ DUYMADAN dürüstçe
  kilitlenebilir. Yalnızca (b)'yi, Bölüm 17.1.1–17.1.13'te LOCKED olarak
  kaydetti: yeni `src/crypto_quant_lab/validation/purging.py` modülü,
  üç bağımsız fonksiyon (`windows_overlap`, `embargo_boundary`,
  `purge_in_sample_windows`) — exact formüller/operation sırası, exact
  validation/fail-fast sırası (global-pass eleman tipi kontrolü dahil,
  Candidate'in düzeltilmiş global-fail-fast precedent'iyle tutarlı),
  purity/import-direction (acyclic — purging.py yalnızca windows.py'nin
  `TemporalWindow` tipini import eder; hiçbir mevcut modül purging.py'yi
  import etmez). §28.I acceptance grubunu, kontrattan türetilen 19
  kriterle 0/19 olarak ekledi; §28 giriş paragrafını sekizden dokuza
  güncelledi. §15'in Stage-2 durumu paragrafındaki eski "annualization
  faktörü... ertelenmiştir" ifadesi, artık implement edilmiş olduğunu
  netleştirmek için düzeltildi; §28.H'nin "git diff yalnızca iki yeni
  dosya gösterir" ifadesi, VALIDATION_SPEC.md'nin de değiştiğini
  açıkça belirtmek üzere düzeltildi. Docs-only; production kod,
  `purging.py` implementasyonu, veya yeni test içermedi — purging/
  embargo implementasyonu ve regression suite'i HENÜZ BAŞLAMADI.

FAZ6C — PURGING/EMBARGO FOUNDATION COMBINED DELIVERY — TAMAMLANDI:
  Bölüm 17.1.1–17.1.13'te LOCKED olan window-level purging/embargo exact
  kontratını, tek bir combined delivery olarak (Annualized Metrics/
  Candidate-Trial/non-zero-context Layer-2 implementasyonlarının izlediği
  AYNI precedent) implement etti: yeni `src/crypto_quant_lab/validation/
  purging.py` modülü, üç bağımsız fonksiyon (`windows_overlap`,
  `embargo_boundary`, `purge_in_sample_windows`) — exact formüller/
  operation sırası, exact validation/fail-fast sırası (global-pass eleman
  tipi kontrolü dahil), purity/import-direction (acyclic — purging.py
  yalnızca windows.py'nin `TemporalWindow` tipini + stdlib'i import eder;
  hiçbir mevcut modül purging.py'yi import etmez) — kontrattan hiçbir
  sapma olmadan implement edildi. Kendi regression suite'i
  `tests/test_validation_purging.py`'de (62 test, tümü PASS); ilgili
  regression suite'ler (`test_validation_windows.py`,
  `test_validation_rolling_backtest.py`, `test_validation_candidate.py`,
  `test_validation_metrics.py`, `test_validation_annualized_metrics.py`
  — birlikte 552 test) DEĞİŞMEDEN yeşil; tam suite 1946/1946 PASS (1884
  önceki + 62 yeni). Post-implementation audit: §28.I'in 19 kriterinin
  HER BİRİ somut test/kod-incelemesi kanıtına eşlendi, hiçbiri yalnızca
  niyet beyanıyla PASS işaretlenmedi. §28.I, 0/19'dan 19/19'a kapatıldı;
  §28 giriş paragrafı, §17.1 başlığı, §22 FAZ6C bölümü, ve §22.2 tablosu
  buna göre güncellendi. Değiştirilen dosyalar: yalnızca
  `src/crypto_quant_lab/validation/purging.py` (YENİ),
  `tests/test_validation_purging.py` (YENİ), `VALIDATION_SPEC.md` —
  hiçbir başka production/test dosyası dokunulmadı. Bu, FAZ6C'nin
  tamamlandığı anlamına GELMEZ: CPCV, Deflated Sharpe, PBO,
  multiple-testing corrections, ve parameter stability hiçbiri bu
  delivery'de BAŞLATILMADI — bunlar ayrı, henüz spec-lock edilmemiş
  gelecekteki adımlardır.

FAZ6C — DEFLATED SHARPE BAĞIMLILIK ÇÖZÜMÜ + TRIAL-GROUP / RECORDED
TRIAL COUNT SOURCE PREFLIGHT + EXACT CONTRACT LOCK — TAMAMLANDI
(docs-only):
  Kullanıcı, FAZ6C'nin kalan maddelerinden Deflated Sharpe'ı (17.4)
  sıradaki hedef olarak seçti. Source-preflight, §17.4'ün exact
  prerequisite metnini ("candidate/trial history (18) + (efektif) trial
  sayısı + gerekli dağılımsal girdiler"; "trial framework'ünden kopuk
  implement edilmez") doğruladı ve DSR'nin KENDİSİNİN henüz
  kilitlenemeyeceğini tespit etti: çoklu-trial koleksiyonu ve trial
  sayımı repository'de yoktur (grep: registry/experiment/trial_count/
  n_trials/deflat — eşleşme yok). Yedi kavramı (tekil Trial, tüm
  denemelerin kaydı, karşılaştırılabilir sonuç koleksiyonu, ham sayı,
  efektif sayı, kalıcı store, optimizer/selection) ayrıştırdı ve
  YALNIZCA en küçük gerekli foundation'ı — tek bir karşılaştırılabilir
  deneme grubu (`TrialGroup(group_id, trials)`) ve onun ham kaydedilmiş
  deneme sayısı (`recorded_trial_count`) — Bölüm 20.1–20.13'te LOCKED
  olarak kaydetti: yeni `src/crypto_quant_lab/validation/trial_group.py`
  modülü (henüz yok), candidate_id-tabanlı grup-içi kimlik (tekrar
  çalıştırma ve çakışan kayıt ikisi de reddedilir, dedupe YOK),
  provenance + ordered evaluation-pencere homojenliği, exact 13 adımlı
  global fail-fast sırası ve mesajlar, purity/import-direction
  (trial_group.py -> candidate.py, tek yönlü). Ham sayının efektif/
  bağımsız sayı olmadığını, başarısız/iptal/kaydedilmemiş denemeleri ve
  diğer grupları kapsamadığını (alt sınır, tamlık kanıtı değil) ve hiçbir
  final holdout koruması sağlamadığını açıkça kilitledi. DSR için açık
  kalan 10 bağımlılığı §20.12'de kaydetti. §28.J acceptance grubunu,
  kontrattan türetilen 19 kriterle 0/19 olarak ekledi; §28 giriş
  paragrafını dokuzdan ona güncelledi; §17.4, §17.6, §20, §22 ve §22.2
  durum metinlerini buna göre güncelledi. Docs-only; production kod,
  `trial_group.py` implementasyonu veya yeni test içermedi.
  Candidate/Trial DEĞİŞMEDİ. Deflated Sharpe, efektif trial-count
  estimator, PBO, CPCV, multiple-testing correction, candidate selection,
  optimizer, persistence ve final holdout enforcement BAŞLATILMADI.
  FAZ6C ve Faz 6 NOT COMPLETE kalır.

FAZ6C — TRIAL-GROUP / RECORDED TRIAL COUNT FOUNDATION COMBINED
DELIVERY — TAMAMLANDI:
  Bölüm 20.1–20.13'te LOCKED olan kontratı, yeniden tasarlamadan, tek
  bir combined delivery olarak implement etti: yeni
  `src/crypto_quant_lab/validation/trial_group.py` modülü — `TrialGroup`
  (frozen/slotted, `group_id`, `trials`) ve `recorded_trial_count`
  (tip kontrolü + `len`). §20.8'in 13 adımlı global fail-fast sırası
  birebir uygulandı (eleman tipi, candidate_id benzersizliği, altı
  provenance alanının her biri ve pencere dizisi AYRI global geçişler;
  kilitli exception türleri/mesajları). Import yönü: yalnızca
  `candidate.Trial` + stdlib `dataclasses` (private alias'larla, public
  sembol kümesi TAM OLARAK {TrialGroup, recorded_trial_count}; §20.14).
  Kendi regression suite'i `tests/test_validation_trial_group.py`'de
  (99 test, tümü PASS) — gerçek SQLite + run_rolling_backtest_from_store
  entegrasyonu dahil. İlgili regression suite'ler (691 test) DEĞİŞMEDEN
  yeşil; tam suite 2045/2045 PASS (1946 önceki + 99 yeni). Ruff/format/
  `git diff --check` temiz. Post-implementation audit: §28.J'nin 19
  kriterinin HER BİRİ somut test veya static/scope kanıtına eşlendi;
  §28.J 0/19'dan 19/19'a kapatıldı. Tek yetkili kontrat açıklama
  düzeltmesi (§20.7 sonundaki not): kilit sürümündeki "sayı, gerçek
  deneme yükünün ALT SINIRIDIR" ifadesi, §20.5'in aynı parameters +
  farklı candidate_id kabulüyle çeliştiği için düzeltildi — sayı,
  verilen gruptaki kabul edilmiş Trial kayıtlarının TAM sayısıdır ve
  gerçek araştırma deneme sayısına göre koşulsuz alt/üst sınır garantisi
  VERMEZ; API/algoritma/davranış DEĞİŞMEDİ, kilit kaydındaki tarihsel
  ifade korunarak açıklığa kavuşturuldu. §17.4, §17.6, §20, §22, §22.2
  ve §28 girişi buna göre güncellendi. Değiştirilen dosyalar: yalnızca
  `trial_group.py` (YENİ), `test_validation_trial_group.py` (YENİ),
  `VALIDATION_SPEC.md`. Deflated Sharpe hâlâ spec-lock EDİLMEMİŞTİR;
  §20.12'deki açık bağımlılıkların hiçbiri çözülmedi. Efektif trial-count,
  PBO, CPCV, multiple-testing correction, parameter stability, candidate
  selection, optimizer, persistence ve final holdout enforcement
  BAŞLATILMADI. FAZ6C ve Faz 6 NOT COMPLETE kalır.

FAZ6C — DEFLATED SHARPE SOURCE PREFLIGHT + §20.12 DECISIONS + EXACT
CONTRACT LOCK — TAMAMLANDI (docs-only):
  Birincil kaynağı (Bailey & López de Prado 2014, JPM 40(5); yazarların
  PDF'i) okudu: Eq. (1)/(2), Ek A.1-A.3 ve yazarların kod listesi metin
  olarak doğrulandı; denklem gövdeleri görsel olduğundan formül biçimi,
  makalenin metinsel sonuçlarının (N=100'de ~%90, N=46'da 0.9505,
  normal getirilerde N=88 eşiği) bağımsız float yeniden üretimiyle
  doğrulandı — bu yeniden üretim yalnızca sqrt(T-1) + ham kurtosis
  konvansiyonunda tutar. PSR (2012a), Mertens (2002) ve Marsaglia
  (2004) bu turda OKUNMADI (§17.4.1). §20.12'nin on maddesini §17.4.2'de
  karara bağladı (N caller-beyanlı bağımsız deneme sayısı; V ve seçilen
  istatistikler tek TrialGroup'tan; tek-pencereli Trial; per-observation
  ölçek; population moment skewness + ham kurtosis; Decimal-only Taylor
  Φ + Newton Φ^-1; M-1 paydalı V > 0; zorunlu selected_candidate_id;
  rol/holdout doğrulanamaz). Exact kontratı §17.4.3–17.4.15'te kilitledi:
  `compute_deflated_sharpe_ratio` (`src/crypto_quant_lab/validation/
  deflated_sharpe.py`, YENİ, henüz yok), veri akışı, formüller ve
  operation sırası, 15 adımlı validation sırası ve mesajlar, prec=80
  private context + 28 basamak çıkış, Φ clamp/yakınsama/iterasyon
  sınırları, minimum veri, bağımsız-referans doğrulama yöntemi, gerçek
  entegrasyon senaryosu. Nümerik iddialar scratchpad'de bir prototiple
  (production DEĞİL) sınandı: Φ, [-15, 15]'te float referansıyla
  2.2e-16 içinde; Newton N <= 1e30 için <= 75 iterasyon. §28.K'yi 0/27
  olarak ekledi; §17.4, §20.12, §22, §22.2, §28 girişi ve §29 durum
  cümlesini güncelledi (§29'un Faz 7 önkoşulu DEĞİŞMEDİ). Deferred:
  efektif-N estimator'ı ve çok pencereli pooling (§17.4.14). Docs-only;
  production kod ve test YOK. FAZ6C ve Faz 6 NOT COMPLETE kalır.

FAZ6C — DEFLATED SHARPE SOURCE/NUMERIC VERIFICATION + IMPLEMENTATION +
REGRESSION SUITE + DOCUMENTATION/ACCEPTANCE CLOSURE — TAMAMLANDI
(tek combined delivery):
  Kodlamadan önce kontratı doğruladı: yazarların PDF'i render edilerek
  Eq. (1)/(2) ve sayısal örnek görsel olarak okundu; bu sürümde s. 9 ve
  s. 10'un ikisi de γ3 = -3 gösterir (+3 GÖZLENMEDİ, başka sürüm
  doğrulanamadı). §17.4.1a'da kaydedilen düzeltmeler: kaynak atfı,
  ham kurtosis (kaynak kanıtlı) ile population moment paydası (projenin
  açık tercihi; Pearson eşitsizliği gerekçesi) ayrımı, kilit commit
  mesajındaki 0.9504 hatası, float 2.2e-16 uyumunun 28 basamak kanıtı
  olmadığı, 75 iterasyonun deneysel olduğu, N ile V'nin aynı evreni
  temsil etmesi gerektiği ve N'in bir varsayım olduğu. §17.4.8'de CDF
  doğruluk hedefi düzeltildi (açık aralık <= 1e-60; clamp bölgesi
  <= 3.68e-51). Bağımsız doğrulama mpmath 1.3.0 (dps 120-130) ile:
  CDF açık aralık en büyük mutlak hata 6.6e-79; kantil x-hatası
  (592 N x 2 olasılık) en büyük 6.3e-50; en fazla 75 Newton iterasyonu
  (deneysel); DSR zinciri test edilen durumlarda en büyük 2.0e-29;
  ambient context bağımsız. Implementasyon:
  `src/crypto_quant_lab/validation/deflated_sharpe.py` (YENİ) — kilitli
  API/15 adım/mesajlar birebir; API ve algoritma kilitli kontrattan
  SAPMADI. Test: `tests/test_validation_deflated_sharpe.py` (YENİ,
  109 test, tümü PASS; mpmath-üretimli Decimal referanslar testte
  saklıdır, mpmath import EDİLMEZ). İlgili regression suite'ler
  (790 test) DEĞİŞMEDEN yeşil; tam suite 2154/2154 PASS (2045 + 109).
  Ruff check/format ve `git diff --check` temiz — önceki kilit
  commit'inin §17.4.3 python kod bloğunda ruff'ın markdown
  biçimlendirmesinin istediği tek boş satır eksikti; elle eklendi.
  §28.K 0/27'den 27/27'ye kapatıldı (kriter 18-20'nin doğrulama yöntemi
  mpmath referanslarıyla güçlendirildi, kriter sayısı DEĞİŞMEDİ).
  Değiştirilen dosyalar: yalnızca `deflated_sharpe.py` (YENİ),
  `test_validation_deflated_sharpe.py` (YENİ), `VALIDATION_SPEC.md`.
  Efektif-N estimator'ı ve çok pencereli pooling deferred. FAZ6C ve
  Faz 6 NOT COMPLETE kalır.

FAZ6C — PBO PREREQUISITE: TRIAL RETURN MATRIX SOURCE PREFLIGHT +
CONTRACT + IMPLEMENTATION + REGRESSION SUITE + CLOSURE — TAMAMLANDI
(tek combined delivery):
  Bağımlılık analizi (§17.5.1 tablosu): CPCV fold modeli ve label/
  outcome-horizon purging nedeniyle, parameter stability parametre
  uzayı tanımı nedeniyle ENGELLİ; multiple-testing bir yöntem kararı
  gerektirir; PBO'nun eksik ilk önkoşulu hizalı performans matrisidir.
  Birincil kaynak (Bailey, Borwein, López de Prado, Zhu — Algorithm 2.3
  CSCV, render ile okundu) matrisi gözlem x deneme (T x N), eşzamanlı
  satırlı bir performans SERİSİ matrisi olarak tanımlar; bu nedenle
  aday x pencere skaler tablosu yerine pencere etiketli hizalı getiri
  matrisi seçildi. Kontrat §17.5.1–17.5.12'de kilitlendi ve
  `src/crypto_quant_lab/validation/return_matrix.py` (YENİ) ile
  implement edildi: `TrialReturnMatrix` (satır-öncelikli returns[t][n],
  window_indices blokları, 14 adımlı yapısal doğrulama) ve
  `build_trial_return_matrix(group)` (kronolojik/ayrık pencere, (start,
  end] gözlem sahipliği, birebir eşzamanlılık, mevcut
  compute_periodic_returns reuse; doldurma/düşürme/ortalama YOK).
  Gerçek rolling entegrasyonu, ilk taslaktaki [start, end) sahiplik
  kuralının yanlış olduğunu gösterdi (equity noktaları mum kapanışında,
  feature_availability_time ile damgalanır) — kural (start, end] olarak
  düzeltildi. Test: `tests/test_validation_return_matrix.py` (YENİ, 55
  test, tümü PASS); ilgili regression suite'ler (899 test) DEĞİŞMEDEN
  yeşil; tam suite 2209/2209 PASS (2154 + 55). Ruff/format/`git diff
  --check` temiz. §28.L 22/22. PBO, CSCV, CPCV, efektif-N ve
  multiple-testing BAŞLATILMADI. FAZ6C ve Faz 6 NOT COMPLETE kalır.

FAZ6C — PBO / CSCV SOURCE VERIFICATION + CONTRACT + IMPLEMENTATION +
REGRESSION SUITE + CLOSURE — TAMAMLANDI (tek combined delivery):
  Birincil kaynağın s. 9-14'ü render edilerek okundu: artan sıralama
  (s. 9 örneği), Definition 2.2 (kesin r̄ < N/2), Algorithm 2.3 (CSCV)
  ve §3.1'in φ = ∫_{-∞}^{0} f(λ)dλ kestiricisi. Kaynak içi
  tutarsızlıklar kaydedildi (medyanda Definition 2.2 ile §3.1 farkı;
  "12,780" yerine C(16,8) = 12,870). §17.5.12'deki açık kararlar
  §17.5.15-17.5.16'da kilitlendi: ardışık eşit bloklar, T % S = 0
  (kırpma/padding yok), N >= 2, T/2 >= 2, leksikografik ve tembel
  kombinasyonlar, Stage-2 ile bit-bit aynı yerel Sharpe (sahte
  BacktestResult veya private import yok), sıfır stdev -> hata, IS
  eşitliğinde ağırlık bölüşümü (uniform tie-break beklentisi), OOS
  eşitliğinde orta sıra, λ <= 0 (medyan dahil) tam rasyonel kararla,
  PBO tam kesirle, maliyet sınırı C(S,S/2)*T*N <= 20,000,000 (örnekleme
  yok). Implementasyon: `src/crypto_quant_lab/validation/pbo.py` (YENİ).
  Test: `tests/test_validation_pbo.py` (YENİ, 35 test, tümü PASS; elle
  izlenebilir PBO = 1 / 0 / medyan / 1/4 örnekleri, logit sabitleri
  mpmath ile doğrulandı, gerçek rolling entegrasyonu). İlgili regression
  suite'ler (954 test) DEĞİŞMEDEN yeşil; tam suite 2244/2244 PASS
  (2209 + 35). Ruff/format/`git diff --check` temiz. §28.M 24/24.
  CPCV, performance degradation/probability of loss/stochastic
  dominance, multiple-testing ve parameter stability BAŞLATILMADI.
  FAZ6C ve Faz 6 NOT COMPLETE kalır.

FAZ6C — HOLM MULTIPLE-TESTING CORRECTION FOUNDATION: SOURCE
VERIFICATION + CONTRACT + IMPLEMENTATION + REGRESSION SUITE + CLOSURE —
TAMAMLANDI (tek combined delivery):
  Holm formülü R stats::p.adjust belgesi ve p.adjust.R kaynak kodundan
  doğrulandı (orijinal Holm 1979 makalesi okunmadı). Kontrat
  §17.6.1–17.6.10'da kilitlendi: aile büyüklüğü = verilen hipotez
  sayısı (n > m yok), red kuralı adjusted <= α (adım-azalan prosedüre
  eşdeğer), context kullanmayan TAM aritmetik, eşitliklerde ve giriş
  sırasında değişmezlik, giriş sırasında çıktı, 0 < α < 1, candidate_id
  ile aynı kimlik kuralı. Implementasyon:
  `src/crypto_quant_lab/validation/multiple_testing.py` (YENİ). Test:
  `tests/test_validation_multiple_testing.py` (YENİ, 41 test, tümü PASS;
  beklenen değerler elle türetildi). İlgili regression suite'ler (653
  test) DEĞİŞMEDEN yeşil; tam suite 2285/2285 PASS (2244 + 41).
  Ruff/format/`git diff --check` temiz. §28.N 18/18. p-değeri üretimi,
  aile kapsamı ve sonuç seçim politikası AÇIK; multiple-testing başlığı,
  FAZ6C ve Faz 6 NOT COMPLETE kalır.

Sonraki (henüz başlanmadı):
  FAZ6C'nin kalanları: CPCV (17.2 — fold modeli ve label/outcome-
  horizon purging önkoşulları hâlâ YOK), multiple-testing için geçerli
  p-değeri üretimi + aile kapsamı + sonuç seçim politikası (17.6.10),
  parameter stability (17.7 — Candidate.parameters için parametre
  uzayı/komşuluk tanımı YOK). Deferred: efektif-N estimator'ı (DSR Ek
  A.3), çok pencereli DSR pooling, PBO'nun §3.2-3.4 yan istatistikleri.
  Candidate selection/ranking, optimizer ve final holdout enforcement
  BAŞLATILMAZ. Ardından FAZ6D — Faz 6 Final Acceptance audit'i. Faz 6'nın
  tamamlanması için FAZ6C/FAZ6D'nin ikisi de gereklidir (bkz. Bölüm 22).
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

## 28. Acceptance Criteria — On Dört Ayrı Grup (LOCKED)

Foundation acceptance, runner-independent (pure/store-free) kontratlar ile Layer-1 context-aware runner acceptance kontratları (28.B, artık runtime/test exercised) **karıştırılmaz.** 28.B'nin karşılanması, Layer-2 çok-pencereli orchestrator'ın hazır olduğu anlamına **gelmez** (Bölüm 8.3.6, 13) — zero-context Layer-2'nin kendi implementasyon acceptance checklist'i, artık runtime/test exercised olan ayrı bir liste olarak 28.C'de kaydedilir (12/12). Stage-1 metrics'in (total return + max drawdown) implementasyon acceptance checklist'i de, artık implementation/test exercised olan ayrı bir liste olarak 28.D'de kaydedilir (bkz. Bölüm 15, 23 — 18/18). Stage-2'nin (return-series + per-observation Sharpe) implementasyon acceptance checklist'i de, artık implementation/test exercised olan ayrı bir liste olarak 28.E'de kaydedilir (bkz. Bölüm 15.9–15.18, 23 — 29/29). Non-zero-context Layer-2'nin implementasyon acceptance checklist'i de, artık implementation/test exercised olan ayrı bir liste olarak 28.F'de kaydedilir (bkz. Bölüm 8.3.16, 23 — 22/22). Candidate/trial foundation'ının implementasyon/test acceptance checklist'i de, artık implementation/test exercised olan ayrı bir liste olarak 28.G'de kaydedilir (bkz. Bölüm 18, 23 — 25/25). Annualized Metrics'in (Sharpe/Sortino/CAGR/Calmar) implementasyon/test acceptance checklist'i de, artık implementation/test exercised olan ayrı bir liste olarak 28.H'de kaydedilir (bkz. Bölüm 15.19–15.33, 23 — 30/30). Window-level purging/embargo'nun (Bölüm 17.1.1–17.1.13) implementasyon/test acceptance checklist'i de, artık implementation/test exercised olan ayrı bir liste olarak 28.I'de kaydedilir (bkz. Bölüm 17.1, 23 — 19/19). Trial-group / recorded-trial-count foundation'ının (Bölüm 20.1–20.13) implementasyon/test acceptance checklist'i de, artık implementation/test exercised olan ayrı bir liste olarak 28.J'de kaydedilir (bkz. Bölüm 20, 23 — 19/19). Deflated Sharpe'ın (Bölüm 17.4.1–17.4.17) implementasyon/test acceptance checklist'i de, artık implementation/test exercised olan ayrı bir liste olarak 28.K'de kaydedilir (bkz. Bölüm 17.4, 23 — 27/27). Trial return matrix foundation'ının (Bölüm 17.5.1–17.5.12) implementasyon/test acceptance checklist'i de, implementation/test exercised olan ayrı bir liste olarak 28.L'de kaydedilir (bkz. Bölüm 17.5, 23 — 22/22). PBO/CSCV'nin (Bölüm 17.5.13–17.5.24) implementasyon/test acceptance checklist'i de, implementation/test exercised olan ayrı bir liste olarak 28.M'de kaydedilir (bkz. Bölüm 17.5, 23 — 24/24). Holm düzeltme temelinin (Bölüm 17.6.1–17.6.10) implementasyon/test acceptance checklist'i de, implementation/test exercised olan ayrı bir liste olarak 28.N'de kaydedilir (bkz. Bölüm 17.6, 23 — 18/18). Önceki sürümün tek listedeki "15 madde" sayısı korunmaya çalışılmaz — spec wording'ine göre yeniden türetilmiştir (bkz. 28.A/28.B/28.C/28.D/28.E/28.F/28.G/28.H/28.I/28.J/28.K/28.L/28.M/28.N altındaki sayılar). §28.A/B/C/D/E/F/G/H/I/J/K/L/M/N'nin sayımları birbirine **katlanmaz** — her biri kendi bağımsız, ayrı kanıtını korur.

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

**Bu 28.B'nin karşılanması, kendisi bir Layer-2 OOS runner olduğu anlamına GELMEZ** — Layer-2 kendi ayrı acceptance grubudur: zero-context Layer-2 (`run_rolling_backtest_from_store`, Bölüm 8.3.6, 28.C — 12/12) ve non-zero-context (context-aware) Layer-2 (`run_context_aware_rolling_backtest_from_store`, Bölüm 8.3.16, 28.F — 22/22) artık ikisi de implement edilmiş + test edilmiştir, ama bu 28.B'nin karşılanmasının otomatik bir sonucu DEĞİLDİR — her biri kendi ayrı acceptance kanıtına sahiptir. Ayrıca criterion 14'ün history-reconstructible (Type-H) niteliği, hâlâ mekanik olarak enforce edilemeyen bir semantic/caller precondition'dır (Bölüm 8.3.5) — bu, testlerin "kanıtladığı" bir şey değildir, yalnızca testlerin VARSAYDIĞI (Type-H policy fixture'ları kullanan) bir disiplin sınırıdır. **Foundation locked acceptance count'una (28.A) hâlâ dahil edilmezler** — bu ayrı bir sayımdır.

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

**İleri seviye Faz 6 kategorileri (28.A/28.B/28.C/28.D'nin hiçbirine dahil DEĞİL, ayrı sayımlar):** CPCV (17.2), Deflated Sharpe (17.4), PBO (17.5), multiple-testing corrections (17.6), parameter stability (17.7) — hâlâ pending. Sharpe-ailesi/return-series (16, 17.3), candidate/trial abstraction (18), ve window-level purging/embargo (17.1) de bu dört grubun hiçbirine dahil değildir, ama kendi ayrı gruplarında (sırasıyla 28.E — 29/29, 28.G — 25/25, 28.I — 19/19) artık IMPLEMENTED + TESTED'dır — bu not yazıldığı tarihte (yalnızca 28.A–D mevcutken) üçü de henüz pending idi.

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

### 28.E — STAGE-2 RETURN-SERIES + PER-OBSERVATION SHARPE ACCEPTANCE (29/29 IMPLEMENTATION/TEST EXERCISED)

Bu liste, Bölüm 15.9–15.18'de LOCKED olan Stage-2 (return-series + per-observation Sharpe) exact kontratının, `src/crypto_quant_lab/validation/metrics.py` (commit `e4cedf9`) tarafından karşılandığını kaydeder. **Bu 29 kriterin hepsi artık implementation/test exercised'dır** — genişletilmiş `tests/test_validation_metrics.py`'de 207 test (82 Stage-1 DEĞİŞMEDEN + 125 yeni Stage-2, tümü PASS), ilgili regression suite'ler (test_backtest_models.py, test_backtest_results.py, test_validation_rolling_backtest.py, test_backtest_replay_context_evaluation.py, test_backtest_store_runner_context_evaluation.py — 148 test) DEĞİŞMEDEN yeşil, tam suite 1593/1593 PASS, post-commit implementasyon audit'i PASS. Davranışsal kriterler doğrudan regression testleriyle, "değişmedi"/"coupled değil"/"yok" türü kriterler ise static/scope kanıtı (`git diff da31ec7..e4cedf9`, imza/kaynak incelemesi) + tam regression suite uyumluluğuyla kanıtlanır — bu ikisi ayrı ayrı etiketlenir, biri diğeri yerine geçmez; bir testin yalnızca DAVRANIŞ kanıtlayabildiği yerde bir absence-property için tek başına kanıt olduğu iddia edilmez.

1. `compute_periodic_returns`, `Stage2Metrics`, `compute_stage2_metrics`, kilitli modül yolunda mevcuttur. **PASS** — `src/crypto_quant_lab/validation/metrics.py`; test dosyasının import bloğu bu üç sembolü doğrudan `crypto_quant_lab.validation.metrics`'ten import eder, 125 yeni testin tümü bunları kullanır.
2. `Stage2Metrics` frozen/slotted'dır (Bölüm 15.12). **PASS** — `@dataclass(frozen=True, slots=True)`; `test_stage2_metrics_is_frozen`, `_is_slotted`.
3. `mean_return`, `return_stdev`, `sharpe_ratio` — üçü de Decimal ve finite olmalıdır; değilse TypeError/ValueError. **PASS** — `__post_init__`; `test_stage2_metrics_rejects_non_decimal_mean_return`, `_rejects_non_finite_mean_return` (NaN/+Inf/-Inf), `_rejects_non_decimal_return_stdev`, `_rejects_non_finite_return_stdev`, `_rejects_non_decimal_sharpe_ratio`, `_rejects_non_finite_sharpe_ratio`.
4. `return_stdev >= Decimal("0")` bir value-object invariant'ı olarak enforce edilir; negatifse ValueError. **PASS** — `test_stage2_metrics_rejects_negative_return_stdev`, `_zero_return_stdev_is_accepted` (sınır-kabul), `_metrics_with_zero_stdev_remains_directly_constructible`.
5. Yanlış `result` tipi deterministik olarak TypeError ile fail eder. **PASS** — `_require_backtest_result`; `test_stage2_rejects_non_backtest_result_input` (her iki public fonksiyon için parametrized).
6. Non-finite veya non-positive `initial_cash`, hesaplamadan ÖNCE ValueError ile fail eder. **PASS** — `_require_core_result_contract`; `test_stage2_rejects_nan_initial_cash`, `_rejects_zero_initial_cash`, `_rejects_negative_initial_cash` (her iki fonksiyon için parametrized).
7. `equity_curve` boş olamaz, her eleman bir `EquityPoint` olmalı, her equity finite olmalıdır; değilse index-specific TypeError/ValueError. **PASS** — `_require_valid_equity_curve` (Stage-1 ile paylaşılan); `test_stage2_rejects_empty_equity_curve`, `_rejects_invalid_curve_member_at_index_0/later_index`, `_rejects_non_finite_curve_equity_with_index`.
8. Timestamp'ler strictly ascending olmalıdır; değilse ValueError. **PASS** — `test_stage2_rejects_duplicate_timestamps`, `_rejects_descending_timestamps_without_silently_sorting`.
9. Terminal curve equity, `result.final_equity`'e eşit olmalıdır; değilse ValueError. **PASS** — `test_stage2_rejects_terminal_equity_not_matching_final_equity`.
10. `equity_curve`, hem zero-context hem context-aware Layer-1 sonuçları için evaluation-only'dir — context candle'lar hiçbir return gözlemine katkıda bulunmaz. **PASS (davranışsal, gerçek engine entegrasyonu)** — `test_context_candles_produce_no_return_observations` (gerçek `run_backtest_from_store(evaluation_start=...)` çağrısı, context/evaluation candle sayımı doğrulanır), `test_context_aware_result_not_diluted_versus_equivalent_zero_context` (context-aware sonuç, eşdeğer zero-context sonuçla byte-identical return-series üretir).
11. Stage-2, ikinci/bağımsız bir evaluation-boundary filtresi uygulamaz. **PASS (static + davranışsal)** — kod incelemesi: `metrics.py` hiçbir `evaluation_start`-farkında filtreleme mantığı içermez, yalnızca `equity_curve`'ü olduğu gibi tüketir; `test_context_candles_produce_no_return_observations` bunu davranışsal olarak doğrular (madde 10 ile aynı kanıt, farklı iddia).
12. Stage-2, bir `evaluation_start` metrics parametresi veya `BacktestResult`/`EquityPoint` uzantısı gerektirmez. **PASS** — `test_stage2_apis_do_not_accept_evaluation_start` (imza incelemesi, her iki public fonksiyon), `test_metrics_module_has_no_second_evaluation_boundary_filter` (modüldeki her fonksiyonun imzası incelenir); static kanıt: `git diff da31ec7..e4cedf9 -- src/crypto_quant_lab/backtest/models.py` boştur (`BacktestResult`/`EquityPoint` değişmedi).
13. N equity noktası tam olarak N return gözlemi üretir — N-1 değil. **PASS** — `test_periodic_returns_exact_n_points_produce_n_returns`.
14. İlk return, `equity_curve[0].equity / initial_cash - Decimal("1")` ile, `initial_cash`'i implicit baseline olarak kullanır. **PASS** — `test_periodic_returns_initial_cash_first_return_baseline`.
15. Return formülü, exact `division-then-subtraction` operation sırasını kullanır; cebirsel bir rewrite'a sessizce geçilmez. **PASS** — `test_periodic_returns_exact_locked_operation_order` (precision-28'de forbidden rewrite'tan somut olarak farklı son basamak, Stage-1'in kendi kanıt deseniyle aynı).
16. Float dönüşümü, log-return, quantization, cap/clip, veya sessiz repair/replace/filter/normalize içermez. **PASS (davranışsal + static)** — `test_periodic_returns_are_decimal_never_float`, `_no_post_computation_quantization`; kod incelemesi: `_compute_periodic_returns_unchecked`'te `float()`, `math.log`, `min`/`max`-cap, veya sort/filter çağrısı yoktur.
17. Güncel equity sıfır veya negatif legal kalır ve `Decimal("-1")` veya daha küçük bir return üretebilir. **PASS** — `test_periodic_returns_terminal_equity_zero_is_minus_one`, `_terminal_negative_equity_below_minus_one`, `test_stage2_terminal_equity_need_not_be_positive`.
18. Bir sonraki return'ün paydası olacak equity <= 0 ise, bu, o bölme işleminden ÖNCE index-specific ValueError ile deterministik olarak fail eder; seri sessizce kısaltılmaz. **PASS** — `_require_positive_denominators`; `test_stage2_rejects_zero_intermediate_denominator`, `_rejects_negative_intermediate_denominator`, `test_stage2_denominator_error_identifies_curve_and_return_index`, `test_stage2_no_partial_tuple_on_denominator_failure`.
19. Cash, fills, PnL, fees, cost, funding, ve unrealized mark-to-market etkileri, bağımsız olarak yeniden hesaplanmadan `equity_curve`'den zaten yansıyan haliyle kullanılır. **PASS (davranışsal, gerçek engine entegrasyonu)** — `test_periodic_returns_costs_already_reflected`, `_funding_already_reflected`, `_unrealized_mark_to_market_already_reflected` (gerçek `run_backtest_from_store` sonuçları, periyodik return'lerin kümülatif çarpımı Stage-1'in `total_return`'üyle cross-check edilir).
20. `compute_stage2_metrics`, sample-istatistikler için en az iki periyodik return gerektirir; değilse ValueError. **PASS** — `test_stage2_minimum_two_returns_required`, `_one_return_accepted_by_periodic_returns_but_not_stage2` (aynı input'un `compute_periodic_returns` için legal, `compute_stage2_metrics` için invalid olduğu doğrudan gösterilir).
21. `mean_return`, yalnızca arithmetic mean formülüyle hesaplanır. **PASS** — `test_stage2_known_arithmetic_mean` (elle hesaplanmış beklenen değerle exact eşleşme).
22. `return_stdev`, yalnızca sample standard deviation (`n - 1` payda) formülüyle hesaplanır — population variance kullanılmaz. **PASS** — `test_stage2_known_exact_sample_standard_deviation`, `_uses_sample_not_population_standard_deviation` (population ve sample stdev'in FARKLI değerler ürettiği, ve implementasyonun yalnızca sample'ı döndürdüğü doğrudan gösterilir).
23. Sıfır standard deviation, Sharpe'ı tanımsız kılar ve deterministik ValueError fırlatır — sıfır, None, NaN, veya Infinity döndürülmez. **PASS** — `test_stage2_all_zero_returns_rejected`, `_equal_non_zero_returns_rejected`.
24. `risk_free_per_period`, explicit, finite bir Decimal parametredir (default `Decimal(0)`); Decimal olmayan veya non-finite bir değer TypeError/ValueError ile fail eder. **PASS** — keyword-only, `= Decimal(0)` default; `test_stage2_rejects_non_decimal_risk_free`, `_rejects_non_finite_risk_free`, `test_stage2_default_risk_free_sharpe` (default davranışı doğrular).
25. `sharpe_ratio`, `(mean_return - risk_free_per_period) / return_stdev` formülüyle, non-annualized ve boyutsuz bir per-observation rasyo olarak hesaplanır — annualization yapılmaz. **PASS** — `test_stage2_explicit_non_zero_risk_free`, `_sharpe_is_dimensionless_non_annualized` (imza incelemesi: `periods_per_year` yok), `_no_annualization_multiplier_applied`, `_sharpe_exact_locked_operation_order` (subtraction-then-division, precision-28 forbidden-rewrite kanıtı).
26. Tüm Stage-2 arithmetic'i, Stage-1 ile aynı private Decimal context'i içinde çalışır; caller'ın ambient precision/rounding'i çıktıyı etkilemez. **PASS** — `_metrics_decimal_context()` (Stage-1 ile paylaşılan factory) + `localcontext`; 5 Decimal-context determinism testi (`test_stage2_output_independent_of_low/high_ambient_precision`, `_output_independent_of_ambient_rounding_mode`, `_output_deterministic_across_repeated_calls_after_ambient_changes`, `_ambient_global_context_untouched_by_computation`), davranışsal olarak (ambient context mutate edilip çıktı kilitli 28-digit değerle karşılaştırılarak) kanıtlanmıştır; `test_stage2_matches_locked_precision_28_round_half_even`.
27. Hesaplanmış non-finite `mean_return`, `return_stdev`, veya `sharpe_ratio`, deterministik olarak ValueError ile reddedilir. **PASS** — `test_non_finite_computed_periodic_return_is_rejected`, `_non_finite_return_sum_or_mean_is_rejected`, `_non_finite_squared_deviation_sum_is_rejected`, `_non_finite_sharpe_ratio_is_rejected` (extreme-magnitude public-input fixture'larla); `return_stdev`'in finite `sample_variance`'tan non-finite olması matematiksel olarak ULAŞILAMAZ olduğu ayrı, odaklı bir testle (`test_non_finite_standard_deviation_is_mathematically_unreachable_given_finite_variance`) kanıtlanmış ve dokümante edilmiştir — `sqrt()` finite non-negative bir girdi için her zaman finite döner, ve bu yolu non-finite kılacak her public-input fixture zaten `squared_deviation_sum`/`sample_variance` kontrolünde daha ÖNCE yakalanır.
28. Hesaplama pure, deterministik, input-mutate-etmeyen'dir; doğrudan bir `BacktestResult` kullanımı VE bağımsız bir `WindowResult.result` kullanımı, hiçbir aggregation olmadan desteklenir. **PASS** — `test_stage2_input_result_unchanged_after_computation`, `_deterministic_repeated_computation`, `test_stage2_direct_backtest_result_supported`, `_independent_window_result_supported` (gerçek `run_rolling_backtest_from_store` entegrasyonu).
29. Cross-window aggregation, candidate/trial aggregation, model/replay/store/rolling modüllerine yeni coupling, ve annualized Sharpe/Sortino/Calmar/CAGR/diğer sonraki-aşama metrikleri içermez/ima etmez. **PASS (davranışsal + static)** — `test_stage2_no_cross_window_aggregation_occurs`, `_no_candidate_trial_aggregation_symbols_exist` (absence-of-symbol kanıtı, kod incelemesiyle), `test_metrics_module_still_does_not_import_rolling`, `test_backtest_result_has_no_stage2_fields`, `_window_result_has_no_stage2_fields`; static kanıt: `git diff da31ec7..e4cedf9 -- src/crypto_quant_lab/backtest/ src/crypto_quant_lab/validation/rolling.py src/crypto_quant_lab/validation/windows.py src/crypto_quant_lab/validation/__init__.py` boştur; `metrics.py` kaynağında `sharpe`/`sortino`/`calmar`/`cagr`/`periods_per_year`/`annualiz` dizgilerinden yalnızca `sharpe_ratio`/`compute_stage2_metrics` ve docstring'deki açık exclusion cümleleri geçer — Sortino/Calmar/CAGR/annualization için hiçbir sembol veya formül yoktur.

**Stage-2 return-series + per-observation Sharpe acceptance count: 29 / 29 implementation/test exercised.** Bu sayım, 28.A'nın (22), 28.B'nin (15/15), 28.C'nin (12/12), veya 28.D'nin (18/18) hiçbirine dahil değildir/katlanmaz — ayrı bir sayımdır. **29/29 olması ŞUNLARI TAMAMLAMAZ:** annualized Sharpe; Sortino/Calmar/CAGR; Stage-3 kontrolleri (Deflated Sharpe, PBO, multiple-testing corrections, parameter stability); non-zero-context Layer-2; candidate/trial abstraction; FAZ6B; Faz 6'nın tamamı — yalnızca Stage-2'nin (return-series + per-observation Sharpe) kendi implementasyon/test acceptance contract'ının karşılandığı anlamına gelir.

### 28.F — NON-ZERO-CONTEXT LAYER-2 ACCEPTANCE (22/22 IMPLEMENTATION/TEST EXERCISED)

Bu liste, Bölüm 8.3.16'da LOCKED olan non-zero-context Layer-2 (`ContextAwareWindow`, `run_context_aware_rolling_backtest_from_store`) exact kontratının, `src/crypto_quant_lab/validation/rolling.py` tarafından karşılandığını kaydeder. **Bu 22 kriterin hepsi artık implementation/test exercised'dır** — genişletilmiş `tests/test_validation_rolling_backtest.py`'de 94 test (28 mevcut zero-context DEĞİŞMEDEN + 66 yeni non-zero-context, tümü PASS), ilgili regression suite'ler (`test_backtest_replay_context_evaluation.py`, `test_backtest_store_runner_context_evaluation.py`, `test_validation_metrics.py` — 250 test) DEĞİŞMEDEN yeşil, tam suite 1659/1659 PASS, post-commit implementasyon audit'i PASS. Davranışsal kriterler doğrudan regression testleriyle, "değişmedi"/"coupled değil"/"yok" türü kriterler ise static/scope kanıtı (kod incelemesi, mevcut testlerin DEĞİŞMEDEN yeşil kalması) + tam regression suite uyumluluğuyla kanıtlanır — bu ikisi ayrı ayrı etiketlenir, biri diğeri yerine geçmez.

1. `ContextAwareWindow` (`context_start: datetime`, `evaluation: TemporalWindow`), kilitli modül yolunda frozen/slotted olarak mevcuttur. **PASS** — `src/crypto_quant_lab/validation/rolling.py`, `@dataclass(frozen=True, slots=True)`; `test_context_aware_window_is_frozen`, `_is_slotted`.
2. `context_start`, genuine aware datetime olmalıdır; naive/pseudo-naive/non-datetime → TypeError/ValueError. **PASS** — `datetime_to_epoch_us(self.context_start)` reuse edilir; `test_context_aware_window_rejects_non_datetime_context_start`, `_rejects_naive_context_start`, `_rejects_pseudo_naive_context_start`.
3. `evaluation`, bir `TemporalWindow` olmalıdır; değilse TypeError. **PASS** — `test_context_aware_window_rejects_wrong_evaluation_type`, `_rejects_temporal_split_as_evaluation` (TemporalSplit'in de reddedildiği ayrıca kanıtlanır).
4. `context_start <= evaluation.start` invariant'ı enforce edilir; `>` → ValueError; `==` (sıfır context) legal kalır. **PASS** — `test_context_aware_window_rejects_context_start_after_evaluation_start`, `_accepts_zero_context_equality_boundary`, `_accepts_valid_non_zero_context`.
5. `run_context_aware_rolling_backtest_from_store`, kilitli modül yolunda, kilitli signature ile mevcuttur; mevcut `run_rolling_backtest_from_store` DEĞİŞMEDEN kalır. **PASS** — fonksiyon tanımlıdır; `test_zero_context_runner_signature_unchanged` (parametre listesi imza kontrolü) + mevcut 28 zero-context testin tümü DEĞİŞMEDEN yeşil (davranışsal byte-for-byte kanıt).
6. `windows` bir `tuple[ContextAwareWindow, ...]` olmalıdır; yanlış top-level tip veya yanlış-tipli eleman (index-specific) → TypeError, herhangi bir I/O'dan önce. **PASS** — `_require_context_aware_windows_tuple`; `test_context_aware_non_tuple_windows_is_rejected_before_activity`, `_invalid_member_at_index_0_is_rejected_before_activity`, `_invalid_member_at_later_index_is_detected_upfront`, `_plain_temporal_window_element_is_rejected`, `_invalid_window_causes_zero_factory_calls` (`_PoisonCandleStore` ile I/O yokluğu kanıtlanır).
7. `policy_factory` callable olmalıdır; değilse TypeError, herhangi bir I/O'dan önce. **PASS** — `test_context_aware_non_callable_policy_factory_is_rejected_before_store_io` (`_PoisonCandleStore`).
8. `policy_factory`, pencere başına tam olarak bir kez, o pencerenin I/O'sundan hemen önce, input sırasında çağrılır. **PASS** — `test_context_aware_exactly_one_factory_call_per_window_in_order`.
9. Her factory sonucu, çağrılabilir `target_position` için I/O'dan önce yapısal olarak kontrol edilir; geçersizse index-specific TypeError. **PASS** — `test_context_aware_invalid_factory_output_is_rejected_before_affected_window_runs` (`_CountingCandleStore` ile etkilenen pencerede sıfır query kanıtlanır).
10. Reuse tespiti yalnızca object identity (`is`) ile yapılır — equality değil; reuse edilmiş bir instance, etkilenen pencere I/O'sundan önce index-specific ValueError ile reddedilir; distinct-ama-equality-eşit instance'lar kabul edilir. **PASS** — `test_context_aware_same_object_factory_output_is_rejected_before_affected_window_runs`, `test_context_aware_distinct_but_equality_equal_policy_instances_are_accepted`.
11. Kabul edilen policy instance'ları, orchestration boyunca strongly retained tutulur. **PASS** — `test_context_aware_prior_accepted_policies_remain_strongly_retained` (weakref + zorunlu `gc.collect()` ile kanıtlanır, zero-context'in kendi kanıtlanmış deseniyle aynı).
12. Her pencere, tek bir `run_backtest_from_store` çağrısına (`requested_start=context_start, requested_end=evaluation.end, evaluation_start=evaluation.start`) delege eder — ikinci/forked bir replay engine yaratılmaz. **PASS** — `test_context_aware_candle_request_spans_context_start_to_evaluation_end`, `_no_second_data_access_path` (boundary-set kanıtı); static kanıt: `rolling.py` yalnızca `run_backtest_from_store`'u import eder, `run_backtest_replay` doğrudan hiç import/çağrılmaz (kod incelemesi).
13. Context candle'lar sıfır policy çağrısı, sıfır fill, sıfır cost, sıfır funding etkisi, sıfır equity point, sıfır PnL, sıfır account-state mutasyonu üretir — mevcut, zaten test edilmiş Layer-1 B2 mekanizmasının reuse'u yoluyla, ikinci bir warm-up mekanizması icat edilmeden. **PASS** — `test_context_aware_context_candles_never_call_policy`, `_context_produces_no_economic_activity`, `_equity_curve_contains_only_evaluation_observations`; static kanıt: `rolling.py` yalnızca `requested_start`/`requested_end`/`evaluation_start`'ı forward eder, hiçbir yeni economics kodu içermez.
14. Ekonomik muhasebe, her pencere için bağımsız olarak, tam olarak `evaluation.start`'ta taze başlar. **PASS** — `test_context_aware_context_produces_no_economic_activity` (`final_cash == initial_cash`), `test_context_aware_zero_context_special_case_matches_zero_context_runner` (zaten kanıtlanmış zero-context fresh-state davranışıyla cross-check).
15. `equity_curve`/`final_equity`/Stage-1/Stage-2 çıktıları, yalnızca evaluation-fazı ekonomisini yansıtır. **PASS** — `test_context_aware_equity_curve_contains_only_evaluation_observations`, `test_context_aware_window_result_works_independently_with_periodic_returns` (dönüş sayısının evaluation candle sayısına eşit olduğu, context candle'lardan dilüsyon olmadığı doğrudan kanıtlanır).
16. Pencereler, tam olarak input sırasında execute edilir; sort/dedupe/clamp/normalize/overlap-reddi yapılmaz — hem pencere (evaluation) hem de context aralıkları için. **PASS** — `test_context_aware_output_order_equals_input_order`, `_duplicate_evaluation_windows_execute_independently`, `_overlapping_evaluation_windows_are_accepted`, `_touching_evaluation_windows_are_accepted`, `_overlapping_context_intervals_are_accepted`, `_context_overlapping_another_windows_evaluation_is_accepted`.
17. Pencere N'de fail (construction/validation/execution): N ve sonrası execute edilmez; önceki pencereler zaten execute edilmiş olabilir; rollback yapılmaz; partial bir tuple döndürülmez — fonksiyon raise eder. **PASS** — `test_context_aware_earlier_windows_execute_and_no_subsequent_window_executes_on_failure`, `_store_execution_failure_at_later_index_stops_orchestration`, `_no_partial_result_returned_on_failure`.
18. Funding, gerekli olduğunda, yalnızca `[evaluation.start, evaluation.end)` üzerinden sorgulanır/gate edilir — context aralığı için asla — mevcut Layer-1 funding kontratının değişmeden reuse'u yoluyla. **PASS** — `test_context_aware_no_funding_coverage_required_for_context_only_time` (context aralığı kapsanmadan da PASS olduğu doğrudan kanıtlanır), `_funding_required_flows_through_canonical_composition`.
19. Çıktı `tuple[WindowResult, ...]` olur, girdi ile aynı uzunlukta/sırada; `WindowResult.window == windows[i].evaluation`; `WindowResult.result` o pencerenin `BacktestResult`'ıdır. **PASS** — `test_context_aware_window_result_window_equals_evaluation_window`, `_output_order_equals_input_order`.
20. `BacktestResult`, `EquityPoint`, `WindowResult`, `TemporalWindow`, `TemporalSplit`, ve mevcut zero-context `run_rolling_backtest_from_store` değişmeden/byte-for-byte-compatible kalır. **PASS** — `test_window_result_gains_no_fields_for_context_aware_support`, `test_temporal_window_and_split_unchanged_by_context_aware_addition`; static kanıt: bu implementasyon `models.py`/`windows.py`'a hiç dokunmaz (yalnızca `rolling.py` ve test dosyası değişti); mevcut 28 zero-context testin tümü DEĞİŞMEDEN yeşil.
21. Cross-window aggregation, candidate/trial coupling, veya ek bir metrics-boundary filtresi tanıtılmaz; `WindowResult.result`, Stage-1 VE Stage-2 metriklerini bağımsız olarak, hiçbir değişiklik olmadan kabul eder. **PASS** — `test_no_cross_window_metric_aggregation_for_context_aware_results`, `test_rolling_module_introduces_no_candidate_trial_or_optimizer_coupling` (absence-of-symbol kanıtı), `test_context_aware_window_result_works_independently_with_stage1_metrics`, `_with_periodic_returns`, `_with_stage2_metrics`, `test_context_aware_metrics_receive_evaluation_only_curve_without_second_filter`.
22. Hesaplama/orchestration pure, deterministik, input-mutate-etmeyendir; store data mutate edilmez; eşdeğer girdilerle tekrar çağrılar eşdeğer sonuç üretir; execution sequential kalır (paralel/distributed execution tanıtılmaz). **PASS** — `test_context_aware_config_is_not_mutated`, `_repeated_equivalent_call_produces_equivalent_result`; static kanıt: `_execute_windows` düz bir `for` döngüsüdür, `rolling.py`'de hiçbir threading/multiprocessing/async import'u yoktur.

**Non-zero-context Layer-2 acceptance count: 22 / 22 implementation/test exercised.** Bu sayım, 28.A'nın (22 — farklı bir grup, aynı sayı tesadüfen), 28.B'nin (15/15), 28.C'nin (12/12), 28.D'nin (18/18), veya 28.E'nin (29/29) hiçbirine dahil değildir/katlanmaz — ayrı bir sayımdır. **22/22 olması ŞUNLARI TAMAMLAMAZ:** candidate/trial abstraction; optimizer/search orchestration; annualized Sharpe/Sortino/Calmar/CAGR; Stage-3 kontrolleri (Deflated Sharpe, PBO, multiple-testing corrections, parameter stability); FAZ6B; Faz 6'nın tamamı — yalnızca non-zero-context Layer-2'nin kendi implementasyon/test acceptance contract'ının karşılandığı anlamına gelir.

### 28.G — CANDIDATE/TRIAL FOUNDATION ACCEPTANCE (25/25 IMPLEMENTATION/TEST EXERCISED)

Bu liste, Bölüm 18'de LOCKED olan candidate/trial exact kontratının, `src/crypto_quant_lab/validation/candidate.py` tarafından karşılandığını kaydeder. **Bu 25 kriterin hepsi artık implementation/test exercised'dır** — `tests/test_validation_candidate.py`'de 148 test (tümü PASS; 132 orijinal combined-delivery testi + candidate global fail-fast order correction'ıyla eklenen 16 yeni test, bkz. Bölüm 18.14), ilgili regression suite'ler (`test_validation_windows.py`, `test_validation_rolling_backtest.py`, `test_validation_metrics.py`, `test_backtest_models.py`, `test_backtest_results.py` — 404 test) DEĞİŞMEDEN yeşil, tam suite 1807/1807 PASS (1791 önceki + 16 yeni), post-implementation audit'i PASS. Davranışsal kriterler doğrudan regression testleriyle, "değişmedi"/"coupled değil"/"yok" türü kriterler ise static/scope kanıtı (kod incelemesi, `git diff` boş, mevcut testlerin DEĞİŞMEDEN yeşil kalması) + tam regression suite uyumluluğuyla kanıtlanır — bu ikisi ayrı ayrı etiketlenir, biri diğeri yerine geçmez.

1. `Candidate` (`candidate_id: str`, `parameters: tuple[tuple[str, ParameterValue], ...]`), kilitli modül yolunda (`src/crypto_quant_lab/validation/candidate.py`) frozen/slotted olarak, kilitli field sırasıyla mevcut olmalıdır (Bölüm 18.5). **PASS** — `@dataclass(frozen=True, slots=True) class Candidate`; `test_candidate_field_order_is_locked`, `test_candidate_is_frozen`, `test_candidate_is_slotted`.
2. `candidate_id`, `str` olmalıdır; yanlış tip → TypeError (Bölüm 18.6). **PASS** — `_require_canonical_identifier`; `test_candidate_id_wrong_type_is_rejected`.
3. `candidate_id`, boş veya yalnızca whitespace/padded olamaz; ihlal → ValueError (Bölüm 18.6). **PASS** — `test_candidate_id_empty_whitespace_or_padded_is_rejected` (6 varyant, parametrized).
4. `candidate_id`, case-sensitive'dir ve hiçbir normalizasyona tabi tutulmaz (Bölüm 18.6). **PASS** — `test_candidate_id_is_case_sensitive_and_not_normalized`.
5. `parameters`, `tuple[tuple[str, ParameterValue], ...]` olmalıdır; yanlış top-level tip veya yanlış-tipli eleman (index-specific) → TypeError (Bölüm 18.5, 18.6). **PASS** — `test_candidate_non_tuple_parameters_is_rejected`, `test_candidate_invalid_parameter_entry_at_index_0_is_rejected`, `_at_later_index_is_rejected`, `test_candidate_parameter_entry_wrong_length_is_rejected`.
6. Parametre key'leri, boş/yalnızca-whitespace/padded olamaz ve tekrar edemez (duplicate key) — ihlal → index-specific ValueError (Bölüm 18.6). Adım 5 (key tipi)/6 (key içeriği)/7 (duplicate), her biri TÜM girişler üzerinden AYRI, global bir geçiştir — adım N her girişte tamamlanmadan adım N+1 hiçbir girişi incelemez (Bölüm 18.8, corrected). **PASS** — `test_candidate_parameter_key_wrong_type_is_rejected`, `_empty_whitespace_or_padded_is_rejected` (4 varyant), `test_candidate_duplicate_key_is_rejected_at_duplicate_index`; global-pass sırası: `test_candidate_stage5_key_type_wins_over_earlier_stage6_key_content`, `_wins_over_earlier_stage7_duplicate`, `_wins_over_earlier_stage8_order`, `_wins_over_earlier_stage9_value`, `test_candidate_stage6_key_content_wins_over_earlier_stage7_duplicate`, `_wins_over_earlier_stage8_order`, `_wins_over_earlier_stage9_value`, `test_candidate_stage7_duplicate_wins_over_earlier_stage8_order`, `_wins_over_earlier_stage9_value`.
7. Parametre key'leri, strictly ascending lexicographic sırada verilmelidir; sırasız girdi sessizce sıralanmaz, index-specific ValueError ile reddedilir (Bölüm 18.6). Adım 8 (order), TÜM girişler üzerinden AYRI, global bir geçiştir — yalnızca adım 7 (duplicate) TÜM girişler için hatasız tamamlandıktan SONRA başlar (Bölüm 18.8, corrected). **PASS** — `test_candidate_noncanonical_order_is_rejected_at_violation_index`, `test_candidate_parameters_are_not_silently_sorted_or_case_folded`; global-pass sırası: `test_candidate_stage8_order_wins_over_earlier_stage9_value`.
8. Parametre değerleri, kilitli 9 adımlık sırayla doğrulanır: bool int'ten ÖNCE, int (non-bool), Decimal (yalnızca finite), str, None, aynı tipte recursive tuple, float REDDEDİLİR, mutable container'lar REDDEDİLİR, custom object'ler REDDEDİLİR (Bölüm 18.6). Adım 4 (entry şekli) de dahil, adım 4-9'un HER BİRİ TÜM girişler üzerinden ayrı bir global geçiştir; adım 9 (value domain), yalnızca adım 3-8 TÜM girişler için hatasız tamamlandıktan SONRA başlar ve TÜM girişler için çalıştırılır (Bölüm 18.8, corrected — bkz. Bölüm 18.14'teki düzeltme notu). **PASS** — `test_candidate_accepts_all_legal_parameter_value_types` (14 varyant), `test_candidate_bool_value_is_accepted_under_its_own_branch_before_int`, `test_candidate_rejects_float_parameter_value` (3 varyant), `_rejects_non_finite_decimal_parameter_value` (NaN/+Inf/-Inf), `_rejects_mutable_container_parameter_value` (list/dict/set), `_rejects_arbitrary_object_parameter_value`, `_rejects_nested_float/_nested_mutable_container_parameter_value`; global-pass sırası: `test_candidate_stage4_shape_wins_over_earlier_stage5_key_type`, `_wins_over_earlier_stage6_key_content`, `_wins_over_earlier_stage7_duplicate`, `_wins_over_earlier_stage8_order`, `_wins_over_earlier_stage9_value`, `test_candidate_stage9_value_domain_only_reached_after_stages_4_to_8_clear_globally`.
9. `Candidate` eşitliği/hash'i, `candidate_id` ve `parameters`'ın tamamını kapsar (default dataclass equality); custom `__eq__`/`__hash__` yoktur (Bölüm 18.5, 18.10). **PASS** — `test_candidate_equality_is_value_based`, `test_candidate_inequality_for_different_ids_same_parameters`; kod incelemesi: `candidate.py`'de hiçbir `__eq__`/`__hash__` tanımı yoktur.
10. `Candidate`, genuinely hashable'dır (empirik olarak, örn. bir `set`/`dict` key'i olarak kullanılarak kanıtlanır) (Bölüm 18.10). **PASS** — `test_candidate_is_hashable_as_set_member_and_dict_key`.
11. Eşit girdilerle yapılan `Candidate` construction'ı, eşit (ve eşit-hash) instance'lar üretir; construction deterministiktir (Bölüm 18.10). **PASS** — `test_candidate_deterministic_repeated_construction`.
12. `Trial` (`candidate`, `results`, `exchange`, `market_type`, `symbol`, `timeframe`, `as_of_time`, `config`), kilitli modül yolunda frozen/slotted olarak, kilitli field sırasıyla mevcut olmalıdır (Bölüm 18.5). **PASS** — `@dataclass(frozen=True, slots=True) class Trial`; `test_trial_field_order_is_locked`, `test_trial_is_frozen`, `test_trial_is_slotted`.
13. `results`, boş olamayan bir `tuple[WindowResult, ...]` olmalıdır; boş tuple → ValueError (Bölüm 18.7). **PASS** — `test_trial_results_non_tuple_is_rejected`, `test_trial_results_empty_is_rejected`.
14. `results`'ın her elemanı `WindowResult` tipinde olmalıdır; yanlış-tipli eleman → index-specific TypeError (Bölüm 18.7). **PASS** — `test_trial_results_invalid_member_at_index_0_is_rejected`, `_at_later_index_is_rejected`.
15. `results` sırası, girdi sırası olarak tam olarak korunur — sort/dedupe/filter yapılmaz (Bölüm 18.7, 18.8). **PASS** — `test_trial_results_preserve_input_order`.
16. `results` arasında duplicate/overlapping pencereler legal'dir — reddedilmez (Bölüm 18.7, mevcut rolling runner precedent'iyle tutarlı). **PASS** — `test_trial_duplicate_windows_are_accepted`, `test_trial_overlapping_windows_are_accepted`.
17. `exchange`/`market_type`/`symbol`/`timeframe`, boş olmayan `str` olmalıdır; ihlal → TypeError/ValueError (Bölüm 18.7). **PASS** — `test_trial_provenance_field_wrong_type_is_rejected` (4 field, parametrized), `_empty_whitespace_or_padded_is_rejected` (4 field x 4 varyant, parametrized).
18. `as_of_time`, genuine aware `datetime` olmalıdır; naive/pseudo-naive/non-datetime → TypeError/ValueError (Bölüm 18.7, 12). **PASS** — `test_trial_as_of_time_wrong_type_is_rejected`, `_naive_is_rejected`, `_pseudo_naive_is_rejected`, `_valid_aware_datetime_is_accepted`.
19. `config`, bir `BacktestConfig` instance'ı olmalıdır; yanlış tip → TypeError (Bölüm 18.5, 18.7). **PASS** — `test_trial_config_wrong_type_is_rejected`.
20. Her `i` için `results[i].result.initial_cash == config.initial_cash` mekanik olarak enforce edilir; uyuşmazlık → index-specific ValueError (Bölüm 18.7 — tek mekanik cross-result consistency kriteri). **PASS** — `test_trial_initial_cash_mismatch_at_index_0_is_rejected`, `_at_later_index_is_rejected`, `test_trial_construction_succeeds_when_every_initial_cash_matches`.
21. `Trial` construction'ı, `results` veya `candidate`'ı kopyalamaz veya mutate etmez (Bölüm 18.7, 18.10). **PASS** — `test_trial_does_not_copy_or_mutate_results_tuple`, `_does_not_copy_or_mutate_candidate_or_config` (`is` identity kanıtı).
22. `Trial`, hiçbir selection/test `role` alanı içermez — bu, absence-of-field kanıtıyla (field introspection) doğrulanır (Bölüm 18.7 — KRİTİK, engine-vs-process ayrımı). **PASS** — `test_trial_has_no_selection_role_score_or_holdout_field`, `test_no_callable_field_participates_in_candidate_or_trial_dataclass`.
23. `candidate.py` modülü, hiçbir evaluator fonksiyonu içermez — yalnızca value object'ler (`Candidate`, `Trial`, `ParameterValue`) tanımlıdır; bu, absence-of-symbol kanıtıyla doğrulanır (Bölüm 18.3, 18.5, 18.9). **PASS** — `test_candidate_module_defines_no_evaluator_or_optimizer_symbol`, `test_candidate_module_public_symbols_include_only_the_locked_value_objects`.
24. `candidate.py`, yalnızca `rolling.py`'den `WindowResult` (value-model tipi için) ve `backtest/models.py`'den `BacktestConfig` import eder; `rolling.py`/`metrics.py`/`windows.py`, `candidate.py`'den HİÇBİR ŞEY import etmez — import cycle yoktur (Bölüm 18.10, static kanıt). **PASS** — `test_symbols_available_at_locked_module_path`, `test_candidate_module_imports_no_metric_optimizer_policy_or_rolling_runner_function`, `test_rolling_module_does_not_import_candidate_module`, `_metrics_module_does_not_import_candidate_module`, `_windows_module_does_not_import_candidate_module`; kod incelemesi: `candidate.py`'nin import bloğu Bölüm 18.10'daki kilitli listeyle birebir eşleşir.
25. Mevcut `BacktestResult`/`WindowResult`/`TemporalWindow`/`TemporalSplit`/her iki rolling runner/`metrics.py` API'leri ve paket export'ları, `candidate.py`'nin eklenmesiyle değişmeden/coupled-olmadan kalır (Bölüm 18.5, 18.10, 21 — static `git diff` kanıtı + tam regression suite uyumluluğu). **PASS** — `test_candidate_trial_not_exported_at_package_root`, `test_existing_window_result_and_temporal_window_still_construct_as_before`; static kanıt: bu delivery `rolling.py`/`metrics.py`/`windows.py`/`models.py`/`policy.py`/`validation/__init__.py` dosyalarının hiçbirine dokunmaz (yalnızca `candidate.py`, `test_validation_candidate.py`, `VALIDATION_SPEC.md` değişti); ilgili 404 regression testi + tam suite 1791 DEĞİŞMEDEN yeşil.

**Candidate/trial foundation acceptance count: 25 / 25 implementation/test exercised.** Bu, aşağıdakilerin HİÇBİRİNİN var olduğu anlamına GELMEZ: candidate selection/ranking; optimizer/grid/random/Bayesian search; final holdout protection; multiple-testing correction; annualized Sharpe/Sortino/Calmar/CAGR; Stage-3 kontrolleri (Deflated Sharpe, PBO, parameter stability); FAZ6B'nin tamamlanması; Faz 6'nın tamamlanması — yalnızca candidate/trial foundation'ının kendi implementasyon/test acceptance contract'ının karşılandığı anlamına gelir.

### 28.H — ANNUALIZED METRICS ACCEPTANCE (30/30 IMPLEMENTATION/TEST EXERCISED)

Bu liste, Bölüm 15.19–15.33'te LOCKED olan Annualized Metrics (Sharpe/Sortino/CAGR/Calmar) exact kontratının, `src/crypto_quant_lab/validation/annualized_metrics.py` tarafından karşılandığını kaydeder. **Bu 30 kriterin hepsi artık implementation/test exercised'dır** — `tests/test_validation_annualized_metrics.py`'de 77 test (tümü PASS), ilgili regression suite'ler (`test_validation_metrics.py` — 207 test, `test_validation_candidate.py`, `test_validation_rolling_backtest.py`, `test_validation_windows.py`) DEĞİŞMEDEN yeşil, tam suite 1884/1884 PASS (1807 önceki + 77 yeni), post-implementation audit'i PASS. Davranışsal kriterler doğrudan regression testleriyle, "değişmedi"/"coupled değil"/"yok" türü kriterler ise static/scope kanıtı (kod incelemesi; o delivery'nin `git diff --name-status` çıktısı yalnızca iki YENİ production/test dosyası — `annualized_metrics.py`, `test_validation_annualized_metrics.py` — artı `VALIDATION_SPEC.md`'nin kendi dokümantasyon güncellemesini gösterir; hiçbir MEVCUT production/test dosyası değişmez; mevcut testlerin DEĞİŞMEDEN yeşil kalması) + tam regression suite uyumluluğuyla kanıtlanır — bu ikisi ayrı ayrı etiketlenir, biri diğeri yerine geçmez.

1. Dört fonksiyon (`compute_annualized_sharpe_ratio`, `compute_sortino_ratio`, `compute_cagr`, `compute_calmar_ratio`), kilitli modül yolunda (`src/crypto_quant_lab/validation/annualized_metrics.py`), kilitli exact signature'larla (result pozisyonel; timeframe/rate/target keyword-only) mevcut olmalıdır; hepsi bare `Decimal` döner — hiçbir yeni dataclass/value object tanıtılmaz (Bölüm 15.21). **PASS** — `test_all_four_functions_exist_and_are_callable`, `test_sharpe_exact_signature`, `test_sortino_exact_signature`, `test_cagr_exact_signature`, `test_calmar_exact_signature`, `test_all_four_functions_return_bare_decimal_never_a_dataclass`, `test_no_function_ever_returns_none`.
2. `validation/__init__.py` DEĞİŞMEDEN kalır — bu dört fonksiyon package-root'ta export EDİLMEZ; `BacktestResult`/`WindowResult`/`Candidate`/`Trial`'a hiçbir yeni field EKLENMEZ (Bölüm 15.21, 15.29). **PASS** — `test_validation_package_root_unchanged`, `test_backtest_result_has_no_annualized_fields`, `test_window_result_has_no_annualized_fields`, `test_candidate_and_trial_have_no_annualized_fields`; kod incelemesi: `validation/__init__.py` tek satırlık docstring'i DEĞİŞMEDEN kaldı.
3. `metrics.py`/`rolling.py`/`windows.py`/`candidate.py`/`models.py` DEĞİŞMEDEN kalır — static `git diff` kanıtı + tam regression suite uyumluluğu (Bölüm 15.29). **PASS** — `git diff --stat`/`git status --short`, bu delivery'de yalnızca `src/crypto_quant_lab/validation/annualized_metrics.py` (YENİ) ve `tests/test_validation_annualized_metrics.py` (YENİ) + `VALIDATION_SPEC.md` değişti; `test_existing_stage1_stage2_public_api_still_present_and_callable`; ilgili 331+ regression testi (`test_validation_metrics.py`, `test_validation_candidate.py`, `test_validation_rolling_backtest.py`, `test_validation_windows.py`) DEĞİŞMEDEN yeşil.
4. Dört fonksiyonun HER BİRİNDE `timeframe`, her diğer yeni-parametre/result-delegasyon kontrolünden ÖNCE `str` olarak validate edilir; değilse TypeError (Bölüm 15.27). **PASS** — `test_timeframe_must_be_str_type_error` (4 fonksiyon, parametrized), `test_timeframe_type_checked_before_risk_free_type_for_sharpe`, `test_timeframe_type_checked_before_target_type_for_sortino`.
5. `timeframe`, reuse edilen `candle_duration()` ile desteklenmelidir; desteklenmeyen bir string, `candle_duration`'ın KENDİ ValueError'ı ile (mesaj DEĞİŞMEDEN propagate edilerek) reddedilir — yeni bir timeframe/duration tablosu İCAT EDİLMEZ (Bölüm 15.22, 15.27). **PASS** — `test_unsupported_timeframe_propagates_candle_duration_error_unchanged` (4 fonksiyon, parametrized, mesaj eşitliği doğrudan `candle_duration`'ın kendi exception'ıyla karşılaştırılarak), `test_unsupported_timeframe_checked_before_sortino_target_validation`.
6. `periods_per_year`, exact integer-mikrosaniye aritmetiğiyle (ASLA `timedelta.total_seconds()`, ASLA float) hesaplanır; "1h" için exact `Decimal('8760')`, "4h" için exact `Decimal('2190')` (Bölüm 15.22). **PASS** — `test_periods_per_year_exact_for_1h_and_4h_via_annualized_sharpe` (davranışsal: `compute_stage2_metrics`'in çıktısı `Decimal('8760').sqrt()`/`Decimal('2190').sqrt()` ile çarpılarak üretim çıktısıyla exact karşılaştırılır); kod incelemesi: `_timedelta_to_microseconds` yalnızca `.days`/`.seconds`/`.microseconds` kullanır, `.total_seconds()` YOK.
7. Calendar basis exact 365 gündür, configurable DEĞİLDİR; leap year'lar sabiti ETKİLEMEZ (Bölüm 15.22). **PASS** — `test_calendar_basis_is_365_days_no_leap_year_effect` (2024-02-29 leap-year sınırını kapsayan bir curve ile ordinary bir curve'ün AYNI CAGR'ı ürettiği), `test_calendar_basis_not_configurable` (hiçbir fonksiyon signature'ında `calendar`/`periods_per_year`/`year_days` parametresi yok).
8. `compute_annualized_sharpe_ratio`, `compute_stage2_metrics`'e TAM olarak delege eder (result/risk_free_per_period validation + non-annualized `sharpe_ratio`) — ikinci bir return-series/stdev algoritması İCAT EDİLMEZ; Stage-2'nin HERHANGİ bir exception'ı tip/mesaj DEĞİŞMEDEN propagate edilir (Bölüm 15.23, 15.27). **PASS** — `test_sharpe_propagates_stage2_zero_stdev_error_unchanged`, `test_sharpe_propagates_stage2_insufficient_returns_error_unchanged`, `test_sharpe_does_not_reinvent_return_series_or_stdev_algorithm` (kaynak incelemesi: `compute_stage2_metrics(` çağrılır, `equity_curve` hiç referans edilmez).
9. Exact formül/operation sırası: `sharpe_ratio * periods_per_year.sqrt()`, yalnızca madde 8 TAMAMEN başarılı olduktan SONRA hesaplanır (Bölüm 15.23). **PASS** — `test_sharpe_exact_operation_order_sharpe_ratio_times_sqrt` (non-terminating risk-free rate ile exact Decimal eşitliği).
10. `risk_free_per_period`, Stage-2 ile BİREBİR AYNI konvansiyonu korur: per-period, ASLA annual, ASLA convert edilir (Bölüm 15.23). **PASS** — `test_sharpe_risk_free_convention_is_per_period_matches_stage2`.
11. Negatif/sıfır annualized Sharpe legal'dir; hesaplanmış non-finite bir annualized Sharpe deterministik olarak reddedilir (Bölüm 15.23, 15.27). **PASS** — `test_sharpe_negative_is_legal`, `test_sharpe_zero_is_legal_when_mean_equals_risk_free`, `test_sharpe_non_finite_output_rejected`.
12. `compute_sortino_ratio`, `compute_periodic_returns`'ü tüketir (`compute_stage2_metrics`'i DEĞİL) — Sortino'nun geçerliliğinin Stage-2'nin total-stdev>0 şartından BAĞIMSIZ olduğu davranışsal olarak kanıtlanır (Bölüm 15.24). **PASS** — `test_sortino_succeeds_when_stage2_would_reject_zero_total_stdev` (sabit periyodik return serisi: `compute_stage2_metrics` "return_stdev must be greater than zero" ile reddederken `compute_sortino_ratio` başarıyla exact bir sonuç döner), `test_sortino_does_not_call_stage2_metrics` (kaynak incelemesi).
13. `minimum_acceptable_return_per_period`, kendi adımlarında tip/finiteness validate edilir; default `Decimal(0)` (Bölüm 15.24, 15.27). **PASS** — `test_sortino_default_minimum_acceptable_return_is_zero`, `test_sortino_rejects_non_decimal_target_type`, `test_sortino_rejects_non_finite_target` (NaN/+Infinity/-Infinity, parametrized).
14. En az İKİ periyodik return gereklidir (`n >= 2`) — downside-deviation formülünün kendisi n=1'de matematiksel olarak tanımlı olsa da, Sharpe/Stage-2 ile AYNI minimum-örneklem eşiği için BİLİNÇLİ OLARAK korunur (Bölüm 15.24). **PASS** — `test_sortino_requires_at_least_two_returns`.
15. Downside deviation exact formülü: population divisor (`n`), her gözlem için `min(0, r-MAR)^2`, TÜM `n` gözlem paydaya dahildir — yalnızca-downside-count alternatifi REDDEDİLMİŞTİR (Bölüm 15.24). **PASS** — `test_sortino_downside_denominator_uses_population_n_not_sample_n_minus_1` (population-`n` ve sample-`n-1` sonuçlarının FARKLI olduğu, üretim çıktısının yalnızca population-`n` ile eşleştiği), `test_sortino_mixed_upside_and_downside_observations`.
16. Sıfır (veya non-finite) downside deviation — "downside gözlem YOK" durumu DAHİL — deterministik ValueError ile reddedilir; hiçbir zaman None/sıfır/NaN/Infinity döndürülmez (Bölüm 15.24). **PASS** — `test_sortino_no_downside_observations_is_rejected`, `test_sortino_boundary_equality_at_target_is_rejected`.
17. Negatif Sortino numerator (mean_return < MAR) legal'dir; annualization `periods_per_year.sqrt()` ile AYNI mekanizmayı kullanır (Sharpe ile); hesaplanmış non-finite bir Sortino deterministik olarak reddedilir (Bölüm 15.24, 15.27). **PASS** — `test_sortino_negative_numerator_is_legal`, `test_sortino_annualization_same_mechanism_as_sharpe` (1h/4h oranının `sqrt(8760)/sqrt(2190)` oranına exact eşitliği), `test_sortino_non_finite_output_rejected`.
18. `compute_cagr`, `compute_stage1_metrics(result).total_return`'ü REUSE eder — `final_equity/initial_cash` ikinci kez bağımsız olarak HESAPLANMAZ (Bölüm 15.25). **PASS** — `test_cagr_reuses_stage1_total_return_never_recomputes_independently` (kaynak incelemesi: `compute_stage1_metrics(` çağrılır, `result.final_equity`/`result.initial_cash` doğrudan attribute-access olarak hiç geçmez).
19. Exact formül/operation sırası: `(Decimal(1) + total_return) ** (periods_per_year / Decimal(n)) - Decimal(1)`; `n = len(result.equity_curve)`, yalnızca Stage-1'in kendi validation'ı BAŞARILI olduktan SONRA, yeniden validate EDİLMEDEN okunur (Bölüm 15.25). **PASS** — `test_cagr_exact_formula_and_operation_order`, `test_cagr_n_equals_len_of_equity_curve`.
20. `n >= 1` yeterlidir (Stage-1'in zaten LOCKED boş-olmayan-curve alt sınırı) — Sharpe/Sortino'nun `n >= 2` eşiğinden KASITLI OLARAK DAHA GEVŞEKTİR, dokümante edilmiş gerekçeyle (Bölüm 15.25). **PASS** — `test_cagr_succeeds_with_single_equity_point_where_sharpe_sortino_reject` (tek noktalı curve'de CAGR başarılı, Sharpe/Sortino "at least two periodic returns" ile reddeder).
21. `total_return == 0` -> `cagr == 0` (exact, `base == 1`); `total_return == -1` -> `cagr == -1` (exact, `base == 0`, pozitif exponent) — her ikisi de empirik olarak doğrulanmış `Decimal.__pow__` davranışıyla kanıtlanır (Bölüm 15.19, 15.25). **PASS** — `test_cagr_flat_equity_is_exact_zero`, `test_cagr_total_wipeout_is_exact_negative_one`.
22. `total_return < -1` (negatif final_equity, Stage-1'de legal) -> `base < 0`, fractional exponent -> NaN -> CAGR deterministik olarak ValueError ile reddedilir (Bölüm 15.25). **PASS** — `test_cagr_negative_final_equity_below_negative_one_total_return_rejected` (7 noktalı curve, `periods_per_year / n` kasıtlı olarak fractional bırakılarak integer-exponent özel durumundan kaçınılır).
23. Elapsed wall-clock equity-curve timestamp'leri CAGR tarafından HİÇ okunmaz; overflow/underflow'dan doğan hesaplanmış non-finite bir CAGR deterministik olarak reddedilir (Bölüm 15.25, 15.27). **PASS** — `test_cagr_ignores_equity_curve_timestamps` (aynı `total_return`/`n`, radikal farklı gap'lerle AYNI CAGR), `test_cagr_overflow_is_rejected`.
24. `compute_calmar_ratio`, `compute_cagr`'ı (DEĞİŞMEDEN) VE `compute_stage1_metrics(result).max_drawdown`'ı (DEĞİŞMEDEN) reuse eder — ikinci bir CAGR veya drawdown algoritması İCAT EDİLMEZ; `compute_cagr`'ın HERHANGİ bir başarısızlığı (undefined CAGR dahil) tip/mesaj DEĞİŞMEDEN propagate edilir (Bölüm 15.26, 15.27). **PASS** — `test_calmar_reuses_cagr_and_stage1_max_drawdown` (kaynak incelemesi), `test_calmar_propagates_undefined_cagr_error_unchanged`.
25. Exact formül: `cagr / max_drawdown` — tek operasyon (Bölüm 15.26). **PASS** — `test_calmar_exact_formula`.
26. Sıfır `max_drawdown` deterministik ValueError ile reddedilir (hiçbir zaman `Decimal('Infinity')` döndürülmez); negatif CAGR legal'dir (negatif Calmar); `max_drawdown == 1` ve `max_drawdown > 1` her ikisi de sıradan bölme olarak ele alınır (Bölüm 15.26). **PASS** — `test_calmar_zero_drawdown_is_rejected_never_infinity`, `test_calmar_negative_cagr_is_legal`, `test_calmar_max_drawdown_equal_to_one`, `test_calmar_max_drawdown_greater_than_one`.
27. Dört fonksiyonun HER BİRİ, kendi taze, private, module-privacy nedeniyle YENİDEN TANIMLANMIŞ bir Decimal context (Stage-1/2 ile AYNI shape: prec=28, ROUND_HALF_EVEN, Emin=-999999, Emax=999999, capitals=1, clamp=0, traps=[]) kullanır (Bölüm 15.28). **PASS** — kod incelemesi: `_annualized_metrics_decimal_context()` her çağrıda yeni bir `Context(...)` instance'ı döner (paylaşılan modül-seviyeli sabit DEĞİL), dört fonksiyonun her biri onu `localcontext(...)` ile kendi hesaplama bloklarında çağırır; madde 28'in ambient-independence testleri bu davranışı davranışsal olarak da doğrular.
28. Her power/sqrt/division operasyonu bu private `localcontext(...)` bloğu İÇİNDE çalışır; caller'ın ambient precision/rounding'inin çıktıyı ETKİLEMEDİĞİ davranışsal olarak kanıtlanır (Bölüm 15.28). **PASS** — `test_sharpe_independent_of_low_ambient_precision` (ambient prec=3), `test_sortino_independent_of_high_ambient_precision` (ambient prec=200), `test_cagr_independent_of_ambient_rounding_mode` (ambient `ROUND_DOWN`, prec=5), `test_calmar_deterministic_after_ambient_context_mutation`.
29. Dört fonksiyon da pure/deterministik/input-mutate-etmeyen'dir; wall-clock/randomness/I/O/persistence KULLANMAZ; rolling orchestration/`Candidate`/`Trial`/optimizer İMPORT ETMEZ; cross-window aggregation YAPMAZ (Bölüm 15.29). **PASS** — `test_annualized_metrics_module_does_not_import_rolling_windows_candidate`, `test_annualized_metrics_module_only_imports_public_metrics_symbols`, `test_functions_do_not_mutate_input_result`, `test_deterministic_repeated_calls`, `test_no_cross_window_aggregation_between_independent_results`, `test_no_float_conversion_anywhere_in_module_source` (float/numpy/pandas/`.quantize(` YOKLUĞU).
30. Doğrudan bir `BacktestResult` kullanımı VE bağımsız bir `WindowResult.result` kullanımı, hiçbir aggregation olmadan desteklenir — Stage-1/Stage-2 ile AYNI desen; en az bir gerçek `run_backtest_from_store`/`run_rolling_backtest_from_store` entegrasyonuyla kanıtlanır (Bölüm 15.29). **PASS** — `test_all_four_functions_work_on_a_real_canonical_backtest_result` (`run_backtest_from_store` ile), `test_all_four_functions_work_on_independent_window_result` (`run_rolling_backtest_from_store` + bağımsız `WindowResult.result` ile).

**Annualized Metrics acceptance count: 30 / 30 implementation/test exercised.** Bu, aşağıdakilerin HERHANGİ BİRİNİN var olduğu anlamına GELMEZ: candidate selection/ranking; optimizer/grid/random/Bayesian search; final holdout protection; multiple-testing correction; Stage-3 kontrolleri (Deflated Sharpe, PBO, parameter stability); Faz 6'nın tamamlanması. Bu grup, yalnızca Annualized Metrics'in kendi implementasyon/test acceptance contract'ının karşılandığı anlamına gelir — FAZ6B artık COMPLETE'dir (bkz. Bölüm 22, 22.2), ama bu Faz 6'nın (FAZ6C/FAZ6D dahil) tamamlanması anlamına GELMEZ.

### 28.I — PURGING/EMBARGO ACCEPTANCE (19/19 IMPLEMENTATION/TEST EXERCISED)

Bu liste, Bölüm 17.1.1–17.1.13'te LOCKED olan window-level purging/embargo exact kontratının, `src/crypto_quant_lab/validation/purging.py` tarafından karşılandığını kaydeder. **Bu 19 kriterin hepsi artık implementation/test exercised'dır** — `tests/test_validation_purging.py`'de 62 test (tümü PASS), ilgili regression suite'ler (`test_validation_windows.py`, `test_validation_rolling_backtest.py`, `test_validation_candidate.py`, `test_validation_metrics.py`, `test_validation_annualized_metrics.py` — birlikte 552 test) DEĞİŞMEDEN yeşil, tam suite 1946/1946 PASS (1884 önceki + 62 yeni), post-implementation audit'i PASS. Davranışsal kriterler doğrudan regression testleriyle, "değişmedi"/"coupled değil"/"yok" türü kriterler ise static/scope kanıtı (kaynak incelemesi; bu delivery'nin `git diff --name-status` çıktısı yalnızca iki YENİ production/test dosyası — `purging.py`, `test_validation_purging.py` — artı `VALIDATION_SPEC.md`'nin kendi dokümantasyon güncellemesini gösterir; hiçbir MEVCUT production/test dosyası değişmez; mevcut testlerin DEĞİŞMEDEN yeşil kalması) + tam regression suite uyumluluğuyla kanıtlanır — bu ikisi ayrı ayrı etiketlenir, biri diğeri yerine geçmez.

1. Üç fonksiyon (`windows_overlap`, `embargo_boundary`, `purge_in_sample_windows`), kilitli modül yolunda (`src/crypto_quant_lab/validation/purging.py`), kilitli exact signature'larla (pozisyonel/keyword-only ayrımı Bölüm 17.1.3'teki gibi) mevcut olmalıdır; bare `bool`/`datetime`/`tuple[TemporalWindow, ...]` döner — hiçbir yeni dataclass/value object tanıtılmaz (Bölüm 17.1.3). **PASS** — `test_three_functions_exist_at_locked_module_path`, `test_windows_overlap_exact_signature`, `test_embargo_boundary_exact_signature`, `test_purge_in_sample_windows_exact_signature`, `test_windows_overlap_returns_bare_bool`, `test_embargo_boundary_returns_bare_datetime`, `test_purge_in_sample_windows_returns_bare_tuple`.
2. `validation/__init__.py` DEĞİŞMEDEN kalır — bu üç fonksiyon package-root'ta export EDİLMEZ; `TemporalWindow`/`TemporalSplit`/`WindowResult`/`Candidate`/`Trial`'a hiçbir yeni field EKLENMEZ (Bölüm 17.1.3, 17.1.9). **PASS** — `test_validation_package_does_not_export_purging_functions`, `test_temporal_window_gains_no_purge_embargo_fields`, `test_temporal_split_gains_no_purge_embargo_fields`, `test_window_result_gains_no_purge_embargo_fields`, `test_candidate_and_trial_gain_no_purge_embargo_fields`; kod incelemesi: `validation/__init__.py` tek satırlık docstring'i DEĞİŞMEDEN kaldı.
3. `windows.py`/`rolling.py`/`metrics.py`/`candidate.py`/`annualized_metrics.py`/`models.py` DEĞİŞMEDEN kalır — static `git diff` kanıtı + tam regression suite uyumluluğu (Bölüm 17.1.9). **PASS** — `git diff --stat`/`git status --short`, bu delivery'de yalnızca `src/crypto_quant_lab/validation/purging.py` (YENİ) ve `tests/test_validation_purging.py` (YENİ) + `VALIDATION_SPEC.md` değişti; `test_existing_public_api_across_validation_modules_still_present`; ilgili 552 regression testi (`test_validation_windows.py`, `test_validation_rolling_backtest.py`, `test_validation_candidate.py`, `test_validation_metrics.py`, `test_validation_annualized_metrics.py`) DEĞİŞMEDEN yeşil.
4. `windows_overlap`: `first`/`second`'ın HER İKİSİ de `TemporalWindow` olmalıdır; değilse TypeError (Bölüm 17.1.5, 17.1.8). **PASS** — `test_windows_overlap_rejects_non_window_first`, `test_windows_overlap_rejects_non_window_second`.
5. `windows_overlap`: exact half-open formül `first.start < second.end and second.start < first.end`; touching pencereler (adjacency) overlap SAYILMAZ; predicate simetriktir (`windows_overlap(a, b) == windows_overlap(b, a)`) (Bölüm 17.1.5). **PASS** — `test_windows_overlap_touching_is_not_overlap`, `test_windows_overlap_full_overlap_identical_windows`, `test_windows_overlap_partial_overlap_both_directions`, `test_windows_overlap_disjoint`, `test_windows_overlap_full_containment_both_directions`, `test_windows_overlap_symmetric_across_several_pairs`.
6. `embargo_boundary`: `out_of_sample` `TemporalWindow`, `embargo` `timedelta` olmalıdır, VE `embargo >= timedelta(0)` olmalıdır — sırasıyla TypeError/TypeError/ValueError (Bölüm 17.1.6, 17.1.8). **PASS** — `test_embargo_boundary_rejects_non_window_out_of_sample`, `test_embargo_boundary_rejects_non_timedelta_embargo`, `test_embargo_boundary_rejects_negative_embargo`.
7. `embargo_boundary`: exact formül `out_of_sample.end + embargo`; sıfır embargo -> exact `out_of_sample.end`'e eşit; aşırı büyük embargo -> Python'ın KENDİ deterministik `OverflowError`'ı (mesaj DEĞİŞMEDEN propagate) (Bölüm 17.1.6). **PASS** — `test_embargo_boundary_exact_formula`, `test_embargo_boundary_zero_embargo_equals_out_of_sample_end`, `test_embargo_boundary_overflow_propagates_pythons_own_error` (empirik olarak `datetime.max` sınırında, mesaj `"date value out of range"` doğrudan eşleştirilerek).
8. `purge_in_sample_windows`'un kilitli numaralı validation/fail-fast sırası (out_of_sample tipi -> embargo tipi -> embargo non-negatifliği -> in_sample_windows tipi -> her elemanın tipi, GLOBAL bir geçiş olarak) birebir uygulanır (Bölüm 17.1.8). **PASS** — `test_purge_rejects_non_window_out_of_sample`, `test_purge_rejects_non_timedelta_embargo`, `test_purge_rejects_negative_embargo`, `test_purge_rejects_non_tuple_in_sample_windows`, `test_purge_rejects_invalid_element_type_at_index_0`, `test_purge_rejects_invalid_element_type_at_later_index`, `test_purge_fail_fast_order_out_of_sample_type_before_embargo_type`, `test_purge_fail_fast_order_embargo_type_before_embargo_value`, `test_purge_fail_fast_order_embargo_value_before_in_sample_windows_type`, `test_purge_fail_fast_order_in_sample_windows_type_before_element_type`, `test_purge_global_element_pass_reports_first_invalid_index_regardless_of_later_validity`.
9. Purge testi (doğrudan OOS-overlap) HER ZAMAN embargo testinden ÖNCE çalışır; embargo testi YALNIZCA purge testi reddetmediyse VE `embargo > timedelta(0)` ise çalışır; boş embargo bölgesi için hiçbir `TemporalWindow` instance'ı İNŞA EDİLMEZ (Bölüm 17.1.7). **PASS** — `test_direct_overlap_is_rejected_without_computing_embargo_zone` (embargo `timedelta(days=999_999_999)` iken bile `OverflowError` TETİKLENMEZ, çünkü embargo bölgesi hiç hesaplanmaz), `test_zero_embargo_never_constructs_a_temporal_window_for_the_embargo_zone` (touching pencere, embargo=0 iken kabul edilir — aksi halde `TemporalWindow(start=X, end=X)` inşası `ValueError` fırlatırdı), `test_embargo_zone_rejection_only_applies_when_not_already_purged_and_embargo_positive`.
10. Girdi sırası (`in_sample_windows`) çıktıda KORUNUR — sort/reindex YOK; duplicate IS pencereleri DEDUPE EDİLMEZ (Bölüm 17.1.7). **PASS** — `test_output_preserves_original_input_order` (kronolojik olarak sıralı OLMAYAN bir girdi sırasıyla), `test_duplicate_in_sample_windows_are_not_deduplicated`.
11. Boş `in_sample_windows` -> boş `()` döner; TÜM pencereler purge edilirse de boş `()` döner — ikisi de legal, hata DEĞİLDİR (Bölüm 17.1.7). **PASS** — `test_empty_in_sample_windows_returns_empty_tuple`, `test_all_windows_purged_returns_empty_tuple`.
12. `in_sample_windows`'ın `out_of_sample`'dan kronolojik olarak önce gelmesi gerektiğine dair hiçbir mekanik invariant ENFORCE EDİLMEZ (Bölüm 17.1.7, 17.1.10). **PASS** — `test_in_sample_after_out_of_sample_is_not_mechanically_rejected`.
13. `purge_in_sample_windows`'ta `embargo` default değeri exact `timedelta(0)`'dır ve yalnızca-purge davranışına degenere eder (Bölüm 17.1.4). **PASS** — `test_purge_in_sample_windows_exact_signature` (default `== timedelta(0)`), `test_default_embargo_degenerates_to_purge_only_behavior`.
14. Purge ve embargo, AYNI fonksiyon tarafından uygulanan ama FARKLI zaman bölgelerine karşı test edilen, bağımsız iki reddediş nedenidir — tek, birleşik bir tanım İCAT EDİLMEZ (Bölüm 17.1.2). **PASS** — `test_purge_and_embargo_are_distinct_independent_rejection_reasons` (aynı üç-pencereli girdi, embargo>0 ve embargo=0 karşılaştırmasıyla iki farklı reddediş nedeni ayrı ayrı gözlemlenir).
15. Label/outcome-horizon'a bağlı klasik purging bu kontratın kapsamı DIŞINDADIR ve implement EDİLMEZ — repository'de böyle bir horizon kavramı YOKTUR (Bölüm 17.1.1, 17.1.13). **PASS** — `test_purging_module_defines_no_horizon_label_outcome_concept` (modülde `horizon`/`label`/`outcome` isimli hiçbir public symbol veya fonksiyon parametresi YOK); implementasyonun kendisi `purging.py` içinde böyle bir kavram tanımlamaz.
16. Üç fonksiyon da pure/deterministik/input-mutate-etmeyen'dir; wall-clock/randomness/I/O KULLANMAZ; rolling/candidate/metrics/annualized_metrics orchestration'ı İMPORT ETMEZ (Bölüm 17.1.9). **PASS** — `test_purge_does_not_mutate_input_tuple_or_window_identities`, `test_purge_is_deterministic_across_repeated_calls`, `test_purging_module_import_lines_reference_only_windows_and_stdlib`, `test_purging_module_uses_no_wallclock_or_randomness`, `test_purging_functions_do_not_reference_rolling_candidate_metrics_orchestration`.
17. Import direction acyclic'tir: `purging.py` yalnızca `windows.py`'nin `TemporalWindow` tipini VE stdlib'i import eder; hiçbir mevcut modül `purging.py`'yi import ETMEZ (Bölüm 17.1.9). **PASS** — `test_purging_module_import_lines_reference_only_windows_and_stdlib`, `test_windows_module_does_not_import_purging_module`, `test_rolling_module_does_not_import_purging_module`, `test_metrics_module_does_not_import_purging_module`, `test_candidate_module_does_not_import_purging_module`, `test_annualized_metrics_module_does_not_import_purging_module`.
18. Hiçbir float/Decimal aritmetiği kullanılmaz — yalnızca stdlib `datetime`/`timedelta` karşılaştırma ve toplama (Bölüm 17.1.4, 17.1.5, 17.1.6). **PASS** — `test_purging_module_uses_no_float_or_decimal_arithmetic` (kaynak incelemesi: `float(`/`Decimal` YOKLUĞU).
19. `purge_in_sample_windows`, doğrudan inşa edilmiş `TemporalWindow` instance'ları İLE bir `TemporalSplit`'in kendi `in_sample`/`out_of_sample` field'larından türetilen pencereler ÜZERİNDE aynı şekilde çalışır — `TemporalSplit`'e hiçbir coupling/reimplementasyon YAPILMAZ (Bölüm 17.1.1, 17.1.9). **PASS** — `test_purging_module_does_not_import_temporal_split`, `test_purge_works_identically_on_temporal_split_derived_windows`.

**Purging/embargo acceptance count: 19 / 19 implementation/test exercised.** Bu, aşağıdakilerin HERHANGİ BİRİNİN var olduğu anlamına GELMEZ: label/outcome-horizon'a bağlı klasik purging; CPCV; Deflated Sharpe; PBO; multiple-testing correction; parameter stability; candidate selection/ranking; optimizer/grid/random/Bayesian search; final holdout protection; FAZ6C'nin tamamlanması; Faz 6'nın tamamlanması. Bu grup, yalnızca Bölüm 17.1.1–17.1.13'te LOCKED olan window-level purging/embargo foundation kontratının kendisinin implement edilmiş + test edilmiş olduğu anlamına gelir — FAZ6C hâlâ NOT COMPLETE'dir (bkz. Bölüm 22, 22.2), çünkü CPCV/Deflated Sharpe/PBO/multiple-testing corrections/parameter stability hiçbiri henüz spec-lock edilmemiştir.

### 28.J — TRIAL-GROUP / RECORDED TRIAL COUNT ACCEPTANCE (19/19 IMPLEMENTATION/TEST EXERCISED)

Bu liste, Bölüm 20.1–20.13'te LOCKED olan trial-group / recorded-trial-count exact kontratının, `src/crypto_quant_lab/validation/trial_group.py` tarafından karşılandığını kaydeder. **Bu 19 kriterin hepsi artık implementation/test exercised'dır** — `tests/test_validation_trial_group.py`'de 99 test (tümü PASS; 60 test fonksiyonu), ilgili regression suite'ler (`test_validation_candidate.py`, `test_validation_rolling_backtest.py`, `test_validation_windows.py`, `test_validation_metrics.py`, `test_validation_annualized_metrics.py`, `test_validation_purging.py`, `test_backtest_models.py`, `test_backtest_results.py` — 691 test) DEĞİŞMEDEN yeşil; tam suite 2045/2045 PASS (1946 önceki + 99 yeni). Grup, kontrat kilidiyle (commit `1b666fd`) 0/19 olarak eklenmişti; bu combined delivery ile 19/19'a kapatıldı. Test sayısı (99) ile acceptance kriter sayısı (19) ayrı sayımlardır.

1. `TrialGroup` (`group_id: str`, `trials: tuple[Trial, ...]`) ve `recorded_trial_count(group: TrialGroup) -> int`, kilitli modül yolunda (`src/crypto_quant_lab/validation/trial_group.py`) mevcuttur; `TrialGroup` frozen/slotted ve field sırası kilitlidir; modülün public sembolleri TAM OLARAK bu ikisidir (Bölüm 20.4). **PASS** — `@dataclass(frozen=True, slots=True) class TrialGroup` (private `_dataclass` alias ile); `test_symbols_available_at_locked_module_path`, `test_module_public_symbols_are_exactly_the_locked_api`, `test_trial_group_field_order_is_locked`, `test_trial_group_resolved_annotations_match_locked_api`, `test_recorded_trial_count_signature_is_locked`, `test_trial_group_is_frozen`, `test_trial_group_is_slotted`.
2. `validation/__init__.py` DEĞİŞMEZ ve iki sembol package-root'ta export EDİLMEZ; `Candidate`/`Trial`/`WindowResult`/`TemporalWindow`/`BacktestConfig`'e hiçbir alan eklenmez; `candidate.py`/`rolling.py`/`windows.py`/`metrics.py`/`annualized_metrics.py`/`purging.py`/`backtest/models.py` DEĞİŞMEZ (Bölüm 20.4, 20.10 — static `git diff` + tam regression suite). **PASS** — `test_trial_group_not_exported_at_package_root`, `test_candidate_and_trial_fields_are_unchanged`; static kanıt: bu delivery'nin `git diff --stat`/`git status`'u yalnızca `trial_group.py` (YENİ), `test_validation_trial_group.py` (YENİ) ve `VALIDATION_SPEC.md`'yi gösterir; 691 ilgili + 2045 tam suite testi DEĞİŞMEDEN yeşil.
3. `group_id` `str` değilse exact mesajlı TypeError (Bölüm 20.8 adım 1). **PASS** — `test_group_id_wrong_type_is_rejected` (4 varyant), `test_stage1_group_id_type_wins_over_trials_type`.
4. `group_id` boş/yalnızca-whitespace veya padded ise exact mesajlı ValueError; case-sensitive, strip/normalizasyon YOK (Bölüm 20.5, 20.8 adım 2). **PASS** — `test_group_id_empty_or_whitespace_only_is_rejected` (3 varyant), `test_group_id_padding_is_rejected_not_stripped` (4 varyant), `test_group_id_is_case_sensitive_and_not_normalized` (case + NFC/NFD), `test_group_id_with_internal_whitespace_is_accepted`.
5. `trials` tuple değilse TypeError, boşsa ValueError — exact mesajlarla (Bölüm 20.8 adım 3-4). **PASS** — `test_trials_non_tuple_is_rejected` (5 varyant), `test_trials_empty_tuple_is_rejected`.
6. `trials`'ın her elemanı `Trial` olmalıdır; index-specific exact mesajlı TypeError, global geçiş olarak (Bölüm 20.8 adım 5). **PASS** — `test_trials_invalid_element_at_index_0_is_rejected`, `test_trials_invalid_element_at_later_index_is_rejected`, `test_stage5_element_type_at_later_index_wins_over_earlier_duplicate` (global geçiş kanıtı).
7. Aynı `candidate_id`'li ikinci Trial — eşit tekrar çalıştırma VE çakışan kayıt — iki index'i tanımlayan exact mesajlı ValueError ile reddedilir; dedupe YOK (Bölüm 20.5, 20.8 adım 6). **PASS** — `test_equal_rerun_trial_is_rejected_as_duplicate`, `test_same_trial_object_twice_is_rejected_as_duplicate`, `test_conflicting_duplicate_with_different_parameters_is_rejected`, `test_duplicate_message_reports_first_seen_index`, `test_stage6_duplicate_wins_over_window_mismatch`, `test_candidate_id_uniqueness_is_case_sensitive`.
8. Aynı `parameters`'a sahip ama farklı `candidate_id`'li Trial'lar KABUL edilir (Bölüm 20.5). **PASS** — `test_same_parameters_different_candidate_id_is_accepted_and_counted_as_two_records` (count == 2 yalnızca kayıt sayısıdır; iki bağımsız strateji kanıtı olarak sunulmaz, §20.7).
9. `exchange`/`market_type`/`symbol`/`timeframe`/`as_of_time`/`config`, `trials[0]`'a karşı alan-başı ayrı global geçişlerle, kilitli sırayla ve exact mesajla doğrulanır; aynı anı gösteren farklı tzinfo'lu `as_of_time` KABUL edilir (Bölüm 20.6, 20.8 adım 7-12). **PASS** — `test_provenance_mismatch_at_index_1_is_rejected` (6 alan), `test_provenance_mismatch_at_later_index_is_rejected` (6 alan), `test_earlier_field_pass_wins_even_at_a_later_index` (5 ardışık alan çifti), `test_first_field_pass_wins_over_last_field_pass`, `test_same_instant_as_of_time_with_different_tzinfo_is_accepted`, `test_equal_valued_config_instances_are_accepted`.
10. Ordered evaluation pencere dizisi (`tuple(r.window for r in trial.results)`) `trials[0]` ile uzunluk/sıra/değer olarak eşit olmalıdır; aksi exact mesajlı ValueError; duplicate/overlapping pencereli birebir aynı dizi KABUL (Bölüm 20.6, 20.8 adım 13). **PASS** — `test_window_sequence_mismatch_is_rejected` (kısa/uzun/yeniden sıralı/farklı değer), `test_window_sequence_mismatch_at_later_index_is_rejected`, `test_identical_duplicate_and_overlapping_window_sequences_are_accepted`, `test_same_instant_windows_with_different_tzinfo_are_accepted`.
11. 13 adımın global fail-fast sırası, eşzamanlı-ihlal testleriyle birebir kanıtlanır (Bölüm 20.8). **PASS** — `test_stage1_group_id_type_wins_over_trials_type`, `test_stage2_group_id_content_wins_over_trials_type`, `test_stage3_trials_type_wins_over_emptiness`, `test_stage5_element_type_at_later_index_wins_over_earlier_duplicate`, `test_stage6_duplicate_at_later_index_wins_over_earlier_provenance_mismatch`, `test_stage6_duplicate_wins_over_window_mismatch`, `test_earlier_field_pass_wins_even_at_a_later_index` (7>8>9>10>11>12), `test_stage12_config_at_later_index_wins_over_earlier_window_mismatch`; 1>2 ve 4>5 yapısal olarak zorunludur (aynı anda ihlal edilemez, §20.14).
12. Girdi sırası korunur (sort/dedupe/filter YOK); equality sıraya duyarlıdır; tek-Trial grup legal'dir (Bölüm 20.4, 20.8). **PASS** — `test_input_order_is_preserved_not_sorted`, `test_equality_is_order_sensitive`, `test_single_trial_group_is_legal_and_counts_one`.
13. Equality/hash frozen-dataclass default'udur; grup hashable'dır; eşit girdilerden tekrar construction eşit ve eşit-hash değer üretir (Bölüm 20.4, 20.9). **PASS** — `test_trial_group_uses_default_dataclass_equality_and_hash`, `test_equality_is_value_based`, `test_different_group_id_is_not_equal`, `test_trial_group_is_hashable_as_set_member_and_dict_key`, `test_repeated_construction_and_count_are_deterministic`.
14. Construction `trials` tuple'ını ve Trial elemanlarını kopyalamaz/mutate etmez (`is` kimliği) (Bölüm 20.9). **PASS** — `test_construction_does_not_copy_or_mutate_trials` (`is` kimliği + hash snapshot), `test_same_instant_as_of_time_with_different_tzinfo_is_accepted` (değer normalize edilmeden `is` ile korunur).
15. `recorded_trial_count`, `TrialGroup` olmayan girdide exact mesajlı TypeError verir ve tam olarak `len(group.trials)` değerini `int` olarak döndürür (bool değil; ağırlıklandırma/dedupe/korelasyon düzeltmesi YOK) (Bölüm 20.7, 20.8). **PASS** — `test_recorded_trial_count_rejects_non_trial_group` (5 varyant), `test_recorded_trial_count_rejects_trial_and_trial_tuple`, `test_single_trial_group_is_legal_and_counts_one`, `test_multi_trial_group_counts_exact_int` (`type(count) is int`), `test_same_trials_can_form_separate_groups_without_cross_group_detection`.
16. `TrialGroup` hiçbir role/score/rank/winner/selected/effective/status/failed/complete/holdout alanı taşımaz; modülde effective count/DSR/selection/persistence/registry sembolü YOKTUR (absence kanıtı) (Bölüm 20.2, 20.3, 20.4, 20.7). **PASS** — `test_trial_group_has_no_role_score_status_or_effective_field`, `test_module_defines_no_effective_dsr_selection_persistence_or_registry_symbol`, `test_module_public_symbols_are_exactly_the_locked_api`.
17. `trial_group.py` yalnızca `candidate.Trial` ve stdlib `dataclasses` import eder; `candidate.py`'nin private helper'larını import ETMEZ; hiçbir validation modülü `trial_group.py`'yi import ETMEZ (Bölüm 20.8, 20.9). **PASS** — `test_trial_group_module_imports_only_candidate_trial_and_stdlib_dataclass` (import satırlarının TAM listesi), `test_no_existing_validation_module_imports_trial_group` (7 modül).
18. Wall-clock/randomness/I/O/metrik hesaplama/Decimal aritmetiği/float kullanımı YOKTUR (Bölüm 20.9). **PASS** — `test_trial_group_module_uses_no_decimal_float_clock_or_randomness_import`; kod incelemesi: `trial_group.py` yalnızca `isinstance`, `==`/`!=`, `getattr`, `len` ve tuple inşası kullanır — metrik fonksiyonu, I/O veya aritmetik yoktur.
19. Gerçek `run_rolling_backtest_from_store` çıktısından aynı pencerelerle üretilmiş iki farklı-candidate Trial bir `TrialGroup` oluşturur (`recorded_trial_count == 2`); farklı pencere dizili bir Trial reddedilir (Bölüm 20.11). **PASS** — `test_real_rolling_trials_form_group_and_mismatched_windows_are_rejected` (gerçek SQLite store `tmp_path`'te, FLAT ve LONG policy'leri, iki pencere; count == 2; tek pencereli üçüncü Trial exact mesajla reddedilir).

**Trial-group / recorded-trial-count acceptance count: 19 / 19 implementation/test exercised.** (Kilit zamanındaki tarihsel sayım: 0 / 19, commit `1b666fd`.) Bu grubun 19/19 olması, aşağıdakilerin HERHANGİ BİRİNİN var olduğu anlamına GELMEZ: Deflated Sharpe; efektif/bağımsız trial sayısı; gruplar-arası veya tüm-araştırma-programı deneme sayımı; başarısız/iptal deneme kaydı; PBO; CPCV; multiple-testing correction; parameter stability; candidate selection/ranking; optimizer/search; final holdout protection; persistence; FAZ6C'nin veya Faz 6'nın tamamlanması.

### 28.K — DEFLATED SHARPE ACCEPTANCE (27/27 IMPLEMENTATION/TEST EXERCISED)

Bu liste, Bölüm 17.4.1–17.4.17'de LOCKED olan Deflated Sharpe exact kontratının `src/crypto_quant_lab/validation/deflated_sharpe.py` tarafından karşılandığını kaydeder. **Bu 27 kriterin hepsi artık implementation/test exercised'dır** — `tests/test_validation_deflated_sharpe.py`'de 109 test (tümü PASS), ilgili regression suite'ler (790 test) DEĞİŞMEDEN yeşil; tam suite 2154/2154 PASS (2045 önceki + 109 yeni). Grup kontrat kilidiyle (commit `65273d7`) 0/27 olarak eklenmişti. Kriter 18-20'nin metni, doğrulama yöntemi mpmath referanslarıyla güçlendirildiği için (§17.4.1a, §17.4.12) yeniden ifade edildi; kriter sayısı 27 kaldı. Test sayısı (109) ile kriter sayısı (27) ayrı sayımlardır.

1. `compute_deflated_sharpe_ratio(group, *, selected_candidate_id, independent_trial_count, risk_free_per_period=Decimal(0)) -> Decimal` kilitli modül yolunda, kilitli imzayla mevcuttur; modülün public sembol kümesi TAM OLARAK `{compute_deflated_sharpe_ratio}`'dir (§17.4.3). **PASS** — `test_public_symbol_set_is_exactly_the_locked_api`, `test_signature_is_locked`.
2. Package-root export YOK; hiçbir mevcut production/test dosyası ve `validation/__init__.py` değişmez (§17.4.3, 17.4.11 — static `git diff` + tam regression suite). **PASS** — `test_not_exported_at_package_root`; static kanıt: `git status`/`git diff --stat` yalnızca iki yeni dosyayı ve `VALIDATION_SPEC.md`'yi gösterir; 790 ilgili + 2154 tam suite testi DEĞİŞMEDEN yeşil.
3. Adım 1-5 tip/aralık doğrulamaları exact tür ve mesajlarla, kilitli sırayla uygulanır; `bool` N reddedilir; N=2 ve N=10**30 kabul, N=1 ve N=10**30+1 reddedilir (§17.4.6). **PASS** — `test_step1_group_type`, `test_step2_selected_candidate_id_type`, `test_step3_independent_trial_count_type` (5 varyant, `bool` dahil), `test_step4_independent_trial_count_range` (1, 0, -5, 10**30+1), `test_step4_independent_trial_count_bounds_are_accepted` (2, 10**30), `test_step5_risk_free_type_and_finiteness`.
4. Tek-Trial grup exact mesajlı ValueError ile reddedilir (adım 6). **PASS** — `test_step6_single_trial_group_is_rejected_before_selected_lookup`.
5. Grupta olmayan `selected_candidate_id` exact mesajlı ValueError ile reddedilir; DSR hiçbir seçim yapmaz — seçilen denemenin en yüksek SR'ye sahip OLMADIĞI durumda da hesaplanır (§17.4.2 karar 9, adım 7). **PASS** — `test_step7_selected_not_in_group_is_rejected_before_window_check`, `test_selected_trial_need_not_have_the_highest_sharpe`.
6. Birden fazla pencereli herhangi bir Trial, global geçişle exact mesajlı ValueError ile reddedilir (adım 8). **PASS** — `test_step8_multi_window_trial_is_rejected`, `test_step8_multi_window_wins_over_step9_unequal_lengths`, entegrasyon testindeki iki pencereli grup reddi.
7. Eşit olmayan equity-gözlem sayısı exact mesajlı ValueError ile reddedilir (adım 9). **PASS** — `test_step9_unequal_equity_observation_counts_are_rejected_before_stage2`.
8. Stage-2 alt katman hataları (örn. sıfır stdev, tek gözlem) DEĞİŞMEDEN propagate edilir (adım 10). **PASS** — `test_step10_stage2_errors_propagate_unchanged` (sıfır stdev), `test_step10_single_observation_stage2_error_propagates_unchanged` — beklenen mesaj `compute_stage2_metrics`'in kendi hatasından alınır.
9. V = 0 (tüm Sharpe'lar eşit) exact mesajlı ValueError ile reddedilir (adım 11). **PASS** — `test_step11_zero_trial_sharpe_variance_is_rejected` (mesajdaki değer deterministik `0E-56`).
10. variance_term <= 0 durumu exact mesajlı ValueError ile reddedilir, clip EDİLMEZ (adım 13, §17.4.9). **PASS** — `test_step13_nonpositive_variance_term_is_rejected_not_clipped` (private yardımcı, tutarsız istatistik — Pearson nedeniyle gerçek örneklemden ulaşılamaz, §17.4.9), `test_real_sample_variance_term_stays_positive_near_two_point_extreme`.
11. Eşzamanlı ihlallerde kilitli sıra (adım 1→15) davranışsal testlerle kanıtlanır (§17.4.6). **PASS** — Adım testleri eşzamanlı ihlallerle kurulmuştur: 1>2>3, 3>5, 4>5, 5>6, 6>7, 7>8, 8>9, 9>10, 10>11 (`test_step10_wins_over_step11_zero_variance` dahil); 11/12/13 gerçek örneklemle aynı anda ihlal edilemez (stdev>0 ⇒ m2>0; Pearson) — §17.4.17.
12. `recorded_trial_count` hiçbir koşulda N yerine kullanılmaz: aynı grup farklı N ile farklı DSR verir; N > M ve N < M ikisi de kabul edilir (§17.4.2 karar 1). **PASS** — `test_recorded_trial_count_is_never_used_as_n` (M=3; N=2, 3, 100 üç farklı, kesin azalan sonuç).
13. V, seçilen dahil tüm Trial'ların Stage-2 Sharpe'larının sample varyansıdır (payda M-1) — bağımsız float referansıyla doğrulanır (§17.4.2 karar 8, §17.4.5). **PASS** — `test_group_result_matches_independent_high_precision_reference` (12 mpmath referansı, M-1 paydalı V ile, ≤ 1e-27), `test_group_result_matches_independent_float_reference` (`statistics.variance`, 9 durum, ≤ 1e-9).
14. Skewness/kurtosis population moment tahmincileridir ve kurtosis HAMDIR (normal=3); simetrik/sabit-olmayan bilinen bir örneklemde beklenen değerler bağımsız hesapla doğrulanır (§17.4.2 karar 6). **PASS** — `test_moment_convention_is_population_moments_with_raw_kurtosis` (population+ham ≤ 1e-9; G1/G2 ve excess varyantları > 1e-6 farklı).
15. Hesap per-observation ölçektedir; annualized metrik veya timeframe girdisi kullanılmaz (§17.4.2 karar 5). **PASS** — `test_scale_is_per_observation_and_independent_of_timestamp_spacing` (saatlik ve günlük aralık aynı sonuç); imzada timeframe/periods_per_year yok.
16. `risk_free_per_period`, gruptaki TÜM Trial'ların Stage-2 Sharpe'ına aynı şekilde uygulanır (§17.4.4). **PASS** — `test_risk_free_rate_is_applied_to_every_trial_sharpe`, rf=0.0005 mpmath referansları (6 durum).
17. Makalenin sayısal örneği `_deflated_sharpe_from_statistics` ile yeniden üretilir: N=100 → 0.9004, N=46 → 0.9505 (4 ondalık); normal getirilerde N=88 → ≥ 0.95, N=89 → < 0.95 (§17.4.12 madde 1). **PASS** — `test_paper_example_matches_high_precision_reference` (4 durum, ≤ 1e-27), `test_paper_example_published_four_decimal_values`, `test_paper_example_normal_returns_threshold_is_n_88`.
18. `_normal_cdf`, mpmath (dps=130) referanslarına göre 13 noktada (merkez, iki kuyruk, ±14.5) mutlak hata ≤ 1e-75; alt kuyruk göreli hata x=-10'da ≤ 1e-50, x=-14.5'te ≤ 1e-28; tamamlayıcı float `NormalDist` ızgarasıyla |fark| ≤ 1e-15; Φ(1) 90 basamaklı referansla eşleşir; |x| ≥ 15 clamp exact 0/1 (§17.4.8, 17.4.12). **PASS** — `test_normal_cdf_matches_independent_reference` (13), `test_normal_cdf_lower_tail_relative_error` (2), `test_normal_cdf_agrees_with_float_reference_on_grid`, `test_normal_cdf_clamp_boundaries`.
19. `_normal_quantile`, mpmath `erfinv` referanslarına göre p ∈ {0.975, 0.99} ve modülün N ∈ {2, 100, 10^30} için kurduğu exact p değerlerinde x-hatası ≤ 1e-47; p = 1/2 → exact 0; alan dışı p exact mesajlı ValueError (§17.4.8, 17.4.12). **PASS** — `test_normal_quantile_matches_independent_reference` (6), `test_normal_quantile_half_is_exact_zero_and_domain_is_enforced`.
20. İç tutarlılık (tamamlayıcı, tek başına kanıt değil): Φ(Φ^-1(p)) - p ≤ 1e-60 ve Φ(-x) + Φ(x) = 1 (≤ 1e-75); N ∈ {2, 10, 100, 1e6, 1e12, 1e30} için Newton 200 iterasyon sınırına ulaşmadan yakınsar (deneysel, ispat değil); iterasyon ve seri-terim sınırı aşımı exact mesajlı ArithmeticError ile fail-closed (§17.4.8). **PASS** — `test_normal_quantile_roundtrip_consistency_is_supplementary_only`, `test_normal_cdf_symmetry_identity`, `test_newton_converges_within_limit_for_sampled_n` (6), `test_newton_non_convergence_fails_closed`, `test_cdf_series_non_convergence_fails_closed`.
21. Euler-Mascheroni ve π sabitleri en az 50 doğru basamak taşır; e context içinde hesaplanır (§17.4.7). **PASS** — `test_constants_match_published_leading_digits` (50 basamak; ayrıca offline mpmath karşılaştırması: hata ~4e-86).
22. Sonuç [0, 1] içindedir, 28 anlamlı basamağa tek kez yuvarlanır; caller'ın ambient Decimal context'i (prec/rounding/traps) sonucu değiştirmez (§17.4.7). **PASS** — `test_result_is_probability_rounded_to_28_significant_digits`, `test_ambient_decimal_context_does_not_change_result` (prec=3, ROUND_DOWN, Inexact/Rounded/DivisionByZero trap'leri).
23. Aynı grup ve parametrelerle N arttıkça DSR artmaz (monoton azalmayan eşik) (§17.4.13). **PASS** — `test_dsr_does_not_increase_as_n_grows` (8 N değeri), entegrasyon testinde N=3/10/100.
24. Girdi mutasyonu YOK; tekrarlı çağrı aynı sonucu verir; float/statistics/math/random/time import'u production modülünde YOKTUR (§17.4.10). **PASS** — `test_no_mutation_and_deterministic_repeated_calls`, `test_import_direction_is_locked` (float(/math/statistics/random/time yokluğu).
25. Import direction: `deflated_sharpe.py` yalnızca `metrics` (iki public fonksiyon), `trial_group` ve `decimal` import eder; hiçbir mevcut modül onu import etmez (§17.4.10). **PASS** — `test_import_direction_is_locked` (import modül kümesi tam olarak {decimal, validation.metrics, validation.trial_group}; metrics private helper'ı yok), `test_no_existing_module_imports_deflated_sharpe` (8 modül).
26. Grup düzeyi sonuç, sentetik bir TrialGroup için bağımsız float referansıyla |fark| ≤ 1e-9 uyumludur (§17.4.12 madde 5). **PASS** — `test_group_result_matches_independent_float_reference` (≤ 1e-9); daha güçlü olarak mpmath referansları ≤ 1e-27.
27. Gerçek SQLite + `run_rolling_backtest_from_store` entegrasyon senaryosu (§17.4.13) geçer: ≥ 3 candidate, tek pencere, [0, 1] sonuç, float referansıyla uyum, iki pencereli Trial reddi. **PASS** — `test_real_sqlite_rolling_integration` (gerçek `SQLiteHistoricalCandleStore` tmp_path'te, 25 değişken fiyatlı mum, LONG/SHORT/alternating, tek 24 saatlik pencere; [0, 1] sonuç, float referansıyla ≤ 1e-9, N arttıkça DSR artmıyor; iki pencereli grup exact mesajla reddedilir).

**Deflated Sharpe acceptance count: 27 / 27 implementation/test exercised.** (Kilit zamanındaki tarihsel sayım: 0 / 27, commit `65273d7`.) Bu grubun 27/27 olması, aşağıdakilerin HERHANGİ BİRİNİN var olduğu anlamına GELMEZ: efektif-N estimator'ı; çok pencereli pooling; candidate selection; final holdout protection; PBO; CPCV; multiple-testing correction; parameter stability; bir yatırım/işlem kararı; FAZ6C'nin veya Faz 6'nın tamamlanması.

### 28.L — TRIAL RETURN MATRIX ACCEPTANCE (22/22 IMPLEMENTATION/TEST EXERCISED)

Bu liste, Bölüm 17.5.1–17.5.12'de LOCKED olan trial return matrix foundation'ının `src/crypto_quant_lab/validation/return_matrix.py` tarafından karşılandığını kaydeder. Kontrat ve implementasyon AYNI combined delivery'de yapıldı; **22 kriterin hepsi implementation/test exercised'dır** — `tests/test_validation_return_matrix.py`'de 55 test (tümü PASS); ilgili regression suite'ler (899 test) DEĞİŞMEDEN yeşil; tam suite 2209/2209 PASS (2154 önceki + 55 yeni). Test sayısı (55) ile kriter sayısı (22) ayrı sayımlardır.

1. `TrialReturnMatrix` (frozen/slotted, alan sırası candidate_ids, observation_times, window_indices, returns) ve `build_trial_return_matrix(group)` kilitli modül yolunda; public semboller tam olarak bu ikisi; çözümlenmiş tip ipuçları kilitli API ile aynı (§17.5.3). **PASS** — `test_public_symbols_are_exactly_the_locked_api`, `test_value_object_shape_is_locked`, `test_builder_signature_is_locked`.
2. Package-root export YOK; hiçbir mevcut production/test dosyası değişmez (§17.5.3, 17.5.8). **PASS** — `test_not_exported_at_package_root`; static: `git status`/`git diff --stat` yalnızca iki yeni dosya + `VALIDATION_SPEC.md`; 899 ilgili ve 2209 tam suite testi yeşil.
3. `candidate_ids` adımları 1-4 exact mesajlarla (§17.5.4). **PASS** — `test_structural_invariants_have_exact_messages` (4 varyant).
4. `observation_times` adımları 5-8 exact mesajlarla; naive zaman için codec hatası DEĞİŞMEDEN propagate (§17.5.4). **PASS** — `test_structural_invariants_have_exact_messages` (4 varyant), `test_naive_observation_time_propagates_codec_error_unchanged`.
5. `window_indices` adımları 9-11 (tuple, uzunluk, int/bool, 0'dan başlayan bitişik bloklar) exact mesajlarla (§17.5.4). **PASS** — `test_structural_invariants_have_exact_messages` (6 varyant).
6. `returns` adımları 12-14 (tuple, satır sayısı, satır tipi, sütun sayısı, hücre tipi, sonluluk) exact mesajlarla (§17.5.4). **PASS** — `test_structural_invariants_have_exact_messages` (2 varyant), `test_row_and_cell_checks_are_separate_global_passes` (4 durum).
7. Yapısal doğrulamada alan sırası ve global-geçiş sırası eşzamanlı ihlallerle kanıtlanır (§17.5.4). **PASS** — `test_field_order_of_validation_is_enforced`, `test_row_and_cell_checks_are_separate_global_passes`.
8. Builder adım 1: TrialGroup olmayan girdi exact TypeError (§17.5.5). **PASS** — `test_builder_rejects_non_group`.
9. Builder adım 2: overlap, duplicate ve ters sıralı pencereler exact mesajla reddedilir; bitişik pencereler kabul (§17.5.2, 17.5.5). **PASS** — `test_builder_rejects_overlapping_duplicate_or_reversed_windows` (3 varyant), `test_builder_accepts_adjacent_windows`.
10. Builder adım 3: gözlem zamanı pencerenin (start, end] gözlem aralığı dışındaysa exact mesajla reddedilir (§17.5.2, 17.5.5). **PASS** — `test_builder_rejects_observation_outside_its_window`; gerçek entegrasyon son gözlemin tam `end` olduğunu doğrular.
11. Builder adım 4: eşzamanlı olmayan veya eksik gözlemler doldurulmaz/düşürülmez, exact mesajla reddedilir; aynı anı gösteren farklı tzinfo kabul (§17.5.2, 17.5.5). **PASS** — `test_builder_rejects_misaligned_observation_times`, `test_builder_rejects_missing_observation_instead_of_filling`, `test_same_instant_times_with_different_tzinfo_are_synchronous`.
12. Builder adım 5: `compute_periodic_returns` hataları DEĞİŞMEDEN propagate (§17.5.5, 17.5.6). **PASS** — `test_lower_layer_return_errors_propagate_unchanged` (sıfır payda), `test_empty_equity_curve_error_propagates_unchanged` — beklenen mesaj mevcut fonksiyonun kendi hatasından alınır.
13. Builder adım sırası 2 > 3 > 4 > 5 eşzamanlı ihlallerle kanıtlanır (§17.5.5). **PASS** — `test_builder_validation_order` (3 durum).
14. Yön: satırlar gözlem, sütunlar deneme; returns[t][n]; T = tüm pencerelerin gözlem toplamı (hiçbir satır eklenmez/düşürülmez) (§17.5.3). **PASS** — `test_orientation_rows_are_observations_columns_are_trials`.
15. Satır sırası pencere sırası + gözlem sırasıdır; window_indices bitişik pencere blokları; boşluk zaman damgalarında görünür (§17.5.2). **PASS** — `test_orientation_rows_are_observations_columns_are_trials`, `test_builder_accepts_adjacent_windows`, entegrasyon testindeki boşluk kontrolü.
16. Hücreler pencere başı sermaye sıfırlamasıyla per-observation simple return'dür; elle hesaplanmış beklentilerle eşleşir (§17.5.6). **PASS** — `test_cells_are_per_window_simple_returns_with_capital_reset`.
17. Hücreler her pencere için mevcut, değişmemiş `compute_periodic_returns` çıktısıyla birebir eşittir (§17.5.5). **PASS** — `test_cells_equal_existing_periodic_returns_for_every_window`, entegrasyon testi.
18. Sütun sırası grup sırasıdır; ranking/sıralama yapılmaz (§17.5.2). **PASS** — `test_column_order_follows_group_order_without_ranking`.
19. Tek pencere kısıtı yoktur; tek denemeli/tek pencereli ve çok pencereli gruplar desteklenir; satır anahtarları trials[0] nesneleridir (§17.5.2). **PASS** — `test_single_trial_and_single_window_groups_are_supported`, `test_observation_times_reuse_reference_trial_objects`, entegrasyon (3 pencere).
20. Girdi mutasyonu yok; tekrarlı çağrı eşit ve eşit-hash sonuç verir (§17.5.7). **PASS** — `test_no_mutation_and_determinism`.
21. Import yönü ve kapsam: yalnızca stdlib + sqlite_codec + metrics.compute_periodic_returns + trial_group; hiçbir mevcut modül return_matrix'i import etmez; PBO/CSCV/seçim/purging/doldurma/korelasyon sembolü yok (§17.5.7, 17.5.10). **PASS** — `test_import_direction_is_locked`, `test_no_existing_module_imports_return_matrix` (9 modül), `test_module_defines_no_pbo_selection_or_purging_symbol`.
22. Gerçek SQLite + `run_rolling_backtest_from_store` ile üç pencereli (boşluklu), üç policy'li (LONG/SHORT/alternating) grup matrise dönüşür; satır zamanları ve hücreler kaynakla birebir eşleşir (§17.5.9). **PASS** — `test_real_sqlite_rolling_multi_window_integration`.

**Trial return matrix acceptance count: 22 / 22 implementation/test exercised.** Bu, aşağıdakilerin HERHANGİ BİRİNİN var olduğu anlamına GELMEZ: PBO; CSCV bölümleme/kombinasyonlar; seçim kuralı; rank/logit; CPCV; fold modeli; label/outcome-horizon purging; efektif-N/korelasyon tahmini; çok pencereli DSR pooling; aynı maliyet modelinin, sızıntısızlığın, bağımsızlığın veya holdout korumasının kanıtı; FAZ6C'nin veya Faz 6'nın tamamlanması.

### 28.M — PBO / CSCV ACCEPTANCE (24/24 IMPLEMENTATION/TEST EXERCISED)

Bu liste, Bölüm 17.5.13–17.5.24'te LOCKED olan PBO/CSCV kontratının `src/crypto_quant_lab/validation/pbo.py` tarafından karşılandığını kaydeder. Kontrat ve implementasyon AYNI combined delivery'de yapıldı; **24 kriterin hepsi implementation/test exercised'dır** — `tests/test_validation_pbo.py`'de 35 test (tümü PASS); ilgili regression suite'ler (954 test) DEĞİŞMEDEN yeşil; tam suite 2244/2244 PASS (2209 önceki + 35 yeni). Test sayısı (35) ile kriter sayısı (24) ayrı sayımlardır.

1. Public semboller tam olarak `CscvCombination`, `PboResult`, `compute_probability_of_backtest_overfitting`; imza (matrix pozisyonel, block_count keyword-only, risk_free_per_period=Decimal(0)), alan sıraları, frozen/slotted (§17.5.14). **PASS** — `test_public_symbols_are_exactly_the_locked_api`, `test_signature_and_result_shapes_are_locked`.
2. Package-root export YOK; import yönü yalnızca stdlib + `return_matrix`; hiçbir mevcut modül pbo'yu import etmez; mevcut dosyalar değişmez (§17.5.14, 17.5.19, 17.5.21). **PASS** — `test_not_exported_at_package_root`, `test_import_direction_and_forbidden_scope`, `test_no_existing_module_imports_pbo` (6 modül); static `git diff --stat`: yalnızca iki yeni dosya + `VALIDATION_SPEC.md`.
3. Adım 1-4 (matrix tipi, block_count tipi/çift >= 2, rf tipi/sonluluk) exact mesajlarla (§17.5.18). **PASS** — `test_invalid_inputs_have_exact_messages` (8 durum).
4. N < 2 exact mesajla reddedilir (§17.5.15.1, 17.5.18 adım 5). **PASS** — `test_single_candidate_is_rejected`.
5. T % S != 0 reddedilir; satır kırpma/padding yok (§17.5.15.1, adım 6). **PASS** — `test_rows_are_never_trimmed_or_padded`.
6. T/2 < 2 reddedilir (§17.5.15.1, adım 7). **PASS** — `test_half_sample_needs_two_rows`.
7. Maliyet C(S,S/2)*T*N > 20,000,000 örnekleme yapılmadan reddedilir; sınır kapsayıcıdır ve T ile N'i içerir (§17.5.16, adım 8). **PASS** — `test_cost_limit_rejects_without_sampling` (C(24,12) x 24 x 2; sabit sütunlar değerlendirilmeden), `test_cost_limit_boundary_counts_rows_and_candidates` (16 geçer, 15 reddeder).
8. Validation sırası 1 > 2 > 3 > 4 > 5 > 6 ve maliyet > tanımsız Sharpe eşzamanlı ihlallerle kanıtlanır (§17.5.18). **PASS** — `test_validation_order`, `test_cost_limit_rejects_without_sampling`.
9. C(S, S/2) kombinasyonun tamamı leksikografik sırada üretilir; her kombinasyonun tümleyeni de mevcuttur (§17.5.15). **PASS** — `test_all_combinations_in_lexicographic_order_with_complements`, `test_single_row_blocks_with_s_equal_t` (6 kombinasyon).
10. Bloklar orijinal sırada ardışık satır bloklarıdır (§17.5.15.1). **PASS** — `test_blocks_are_contiguous_in_original_row_order` (ardışık bölme PBO=1, araya serpiştirilmiş bölme farklı).
11. Alt-örneklem Sharpe'ı Stage-2 ile bit-bit aynıdır (rf dahil) (§17.5.15.3). **PASS** — `test_subsample_sharpe_equals_stage2_sharpe_exactly` (rf ∈ {0, 0.0003}; mevcut `compute_stage2_metrics` referans).
12. Sıfır stdev (IS ve OOS) Sharpe'ı tanımsız kılar; 0/sonsuz ile değiştirilmez, exact ValueError (§17.5.15.4). **PASS** — `test_zero_in_sample_stdev_is_an_error_not_zero_or_infinity`, `test_zero_out_of_sample_stdev_is_an_error`, `test_subsample_sharpe_returns_none_for_zero_stdev`.
13. IS kazananı en yüksek IS Sharpe'ıdır; eşitlikte tüm eşit adaylar sütun sırasıyla seçilir (§17.5.15.5). **PASS** — `test_anti_persistent_selection_has_pbo_one`, `test_in_sample_tie_splits_the_combination_weight_across_tied_winners`.
14. OOS sırası artan ortalama sıradır (eşitlikte orta sıra) (§17.5.15.6). **PASS** — `test_in_sample_tie_splits_the_combination_weight_across_tied_winners` (2.5), `test_persistent_selection_has_pbo_zero`, `test_anti_persistent_selection_has_pbo_one`.
15. λ <= 0 (tam medyan dahil) aşırı uyum sayılır ve karar tam rasyonel karşılaştırmayla verilir; medyanda logit tam 0'dır (§17.5.15, 17.5.15.7). **PASS** — `test_exact_oos_median_counts_as_overfit` (Definition 2.2'nin kesin okuması 0.5 verirdi; seçilen kestirici 1).
16. Logit = ln(r̄/(N+1-r̄)), bağımsız sabitlerle doğrulanır (§17.5.15.7). **PASS** — ln 2, -ln 2, -ln 3, ln(5/3) karşılaştırmaları (mpmath ile doğrulanmış sabitler, tolerans 1e-26).
17. PBO = Σ ağırlık / C(S,S/2) tam kesirle; bilinen sonuçlar 1, 0, 1 (medyan), 1/4 (eşitlik) (§17.5.15.8). **PASS** — `test_anti_persistent_selection_has_pbo_one`, `test_persistent_selection_has_pbo_zero`, `test_exact_oos_median_counts_as_overfit`, `test_in_sample_tie_splits_the_combination_weight_across_tied_winners`.
18. Sütun sırası PBO'yu ve eşitlik ağırlığını değiştirmez (§17.5.15.5-6). **PASS** — `test_column_order_does_not_change_pbo_or_tie_handling`, entegrasyondaki ters sıralı grup.
19. risk_free_per_period tüm adaylara uygulanır ve IS kazananını değiştirebilir (§17.5.15.9). **PASS** — `test_risk_free_rate_can_change_the_in_sample_winner`.
20. Sonuç modelleri kendi alanlarını doğrular (§17.5.14). **PASS** — `test_result_model_validates_its_own_fields`.
21. Determinizm ve girdi değişmezliği (§17.5.19). **PASS** — `test_determinism_and_input_immutability`, entegrasyonda tekrarlı çağrı eşitliği.
22. Ambient Decimal context sonucu değiştirmez (§17.5.19). **PASS** — `test_ambient_decimal_context_does_not_change_result` (prec=2, ROUND_DOWN, Inexact/Rounded/DivisionByZero trap'leri).
23. Kombinasyonlar tembel üretilir; satır matrisi veya Sharpe tabloları sonuçta saklanmaz (§17.5.15.2, 17.5.14). **PASS** — Kod incelemesi: `for in_sample_blocks in _combinations(...)` (liste yok); `PboResult`/`CscvCombination` alanları yalnızca kombinasyon başı kayıt taşır (`test_signature_and_result_shapes_are_locked`).
24. Gerçek SQLite + `run_rolling_backtest_from_store` (iki boşluklu pencere, LONG/SHORT/alternating) -> TrialGroup -> TrialReturnMatrix (T=12) -> PBO (S=4, 6 kombinasyon) çalışır; aday sırası sonucu değiştirmez (§17.5.17, 17.5.22). **PASS** — `test_real_rolling_trial_group_matrix_pbo_integration`.

**PBO / CSCV acceptance count: 24 / 24 implementation/test exercised.** Bu, aşağıdakilerin HERHANGİ BİRİNİN var olduğu anlamına GELMEZ: CPCV; fold modeli; label/outcome-horizon purging; performance degradation / probability of loss / stochastic dominance; PBO eşiği veya geçti/kaldı kararı; canlı strateji seçimi, emir veya risk kararı; bağımsızlık, tam araştırma geçmişi, aynı maliyet modeli veya holdout koruması kanıtı; multiple-testing; parameter stability; FAZ6C'nin veya Faz 6'nın tamamlanması.

### 28.N — HOLM MULTIPLE-TESTING CORRECTION FOUNDATION ACCEPTANCE (18/18 IMPLEMENTATION/TEST EXERCISED)

Bu liste, Bölüm 17.6.1–17.6.10'da LOCKED olan Holm düzeltme temelinin `src/crypto_quant_lab/validation/multiple_testing.py` tarafından karşılandığını kaydeder. Kontrat ve implementasyon AYNI combined delivery'de yapıldı; **18 kriterin hepsi implementation/test exercised'dır** — `tests/test_validation_multiple_testing.py`'de 41 test (tümü PASS); ilgili regression suite'ler (653 test) DEĞİŞMEDEN yeşil; tam suite 2285/2285 PASS (2244 önceki + 41 yeni). Test sayısı (41) ile kriter sayısı (18) ayrı sayımlardır.

1. Public semboller tam olarak `HypothesisPValue`, `HolmAdjustedHypothesis`, `HolmCorrectionResult`, `apply_holm_correction`; imza (family_id, hypotheses pozisyonel; significance_level keyword-only, varsayılansız); alan sıraları; frozen/slotted (§17.6.3). **PASS** — `test_public_symbols_are_exactly_the_locked_api`, `test_signature_and_model_shapes_are_locked`.
2. Package-root export YOK; modül yalnızca dataclasses/decimal import eder; TrialGroup/recorded_trial_count/DSR/PBO'ya bağlanmaz ve onlar tarafından import edilmez; mevcut dosyalar değişmez (§17.6.3, 17.6.6, 17.6.7). **PASS** — `test_not_exported_at_package_root`, `test_module_is_standalone_and_links_to_no_trial_group_dsr_or_pbo`; static `git diff --stat`: yalnızca iki yeni dosya + `VALIDATION_SPEC.md`.
3. hypothesis_id kimlik kuralı exact mesajlarla (§17.6.2.6, 17.6.5). **PASS** — `test_invalid_hypothesis_ids` (4 varyant).
4. p_value: yalnızca Decimal; NaN/sNaN/Infinity ve [0, 1] dışı exact mesajlarla reddedilir (§17.6.2.6, 17.6.5). **PASS** — `test_invalid_p_values` (9 varyant: float, int, bool, str, NaN, sNaN, Infinity, -0.001, 1.0001).
5. family_id kimlik kuralı exact mesajlarla (§17.6.5 adım 1-2). **PASS** — `test_invalid_family_inputs_have_exact_messages`.
6. hypotheses tuple, boş değil, elemanlar HypothesisPValue (global geçiş) (§17.6.5 adım 3-5). **PASS** — `test_invalid_family_inputs_have_exact_messages`, `test_validation_order`.
7. Yinelenen hypothesis_id iki index'i tanımlayan exact mesajla reddedilir; sessizce çıkarılmaz (§17.6.5 adım 6). **PASS** — `test_invalid_family_inputs_have_exact_messages`, `test_validation_order`.
8. significance_level Decimal ve sonlu, 0 < α < 1 (0, 1, negatif, >1, NaN, Infinity reddedilir) (§17.6.2.5, adım 7-8). **PASS** — `test_significance_level_must_be_strictly_between_zero_and_one` (6 varyant), `test_invalid_family_inputs_have_exact_messages` (float).
9. Validation sırası 1-2 > 3-4 > 5 > 6 > 7-8 eşzamanlı ihlallerle kanıtlanır (§17.6.5). **PASS** — `test_validation_order`.
10. Bilinen düzeltilmiş p-değerleri ve kararlar elle türetilmiş referanslarla eşleşir (§17.6.4, 17.6.8). **PASS** — `test_known_adjusted_p_values_and_decisions` (0.03/0.06/0.06/0.02).
11. İlk reddedilmeyen hipotezden sonra, kendi p'si α'nın altında olsa bile hiçbir hipotez reddedilmez (§17.6.2.2). **PASS** — `test_no_rejection_after_the_first_non_rejected_hypothesis` (0.045 < α ama düzeltilmiş 0.08).
12. Düzeltilmiş değerler 1 ile sınırlıdır ve ham p sırasına göre monotondur (§17.6.1). **PASS** — `test_adjusted_p_values_are_capped_at_one`, `test_adjusted_p_values_are_monotone_in_the_raw_p_values`.
13. Kararlar adım-azalan Holm prosedürüyle birebir aynıdır (§17.6.2.2). **PASS** — `test_decisions_match_the_step_down_procedure` (m = 5, elle α/(m-i+1) karşılaştırması).
14. Eşit p-değerleri eşit düzeltilmiş değer alır; giriş sırasının tüm permütasyonları hipotez başı sonucu değiştirmez (§17.6.2.4). **PASS** — `test_tied_p_values_receive_equal_adjusted_values`, `test_input_order_does_not_change_any_hypothesis_result` (5! = 120 permütasyon).
15. Sonuç giriş sırasındadır; kimlikler, ham p nesneleri, family_id ve significance_level korunur (§17.6.3). **PASS** — `test_output_preserves_input_order_ids_raw_p_values_and_family`.
16. Tek hipotez düzeltilmez; p = 0 ve p = 1; α'ya tam eşitlik RED, hemen üstü değil (§17.6.1, 17.6.2.2-3). **PASS** — `test_single_hypothesis_is_unadjusted`, `test_p_values_zero_and_one`, `test_equality_with_the_significance_level_is_a_rejection` (0.0125 x 4 = 0.05 red; 0.0500004 değil).
17. Aritmetik tamdır (28 basamak ötesi), ambient Decimal context sonucu değiştirmez, -0 sıfıra normalize edilir (§17.6.2.3). **PASS** — `test_arithmetic_is_exact_beyond_28_digits`, `test_ambient_decimal_context_does_not_change_results` (prec=2, ROUND_DOWN, Inexact/Rounded/InvalidOperation trap'leri), `test_negative_zero_p_value_is_normalized_to_zero`.
18. Determinizm, girdi değişmezliği ve sonuç modellerinin kendi alan doğrulaması (§17.6.3). **PASS** — `test_inputs_are_not_mutated_and_results_are_deterministic`, `test_result_models_validate_their_fields`.

**Holm correction foundation acceptance count: 18 / 18 implementation/test exercised.** Bu, aşağıdakilerin HERHANGİ BİRİNİN var olduğu anlamına GELMEZ: getirilerden geçerli p-değeri üretimi; Sharpe/DSR/PBO'nun p-değerine dönüştürülmesi; test ailesinin araştırma geçmişine göre eksiksiz kapsamı; sonuç seçim/raporlama politikası; Hochberg/Hommel/BH/BY; multiple-testing başlığının, FAZ6C'nin veya Faz 6'nın tamamlanması.

## 29. Faz 6 Sonrası (Bilgi Amaçlı — Bu Dokümanda Tasarlanmaz)

ROADMAP.md'deki bir sonraki faz **Faz 7 — İlk Funding/Basis araştırması**dır. Faz 7'nin güvenilir olabilmesi için, en azından Bölüm 22'deki FAZ6A (temporal split + rolling fixed-policy OOS evaluation + basic return/drawdown metrikleri) tamamlanmış olmalıdır — bu, Faz 7'nin IS'te seçilen bir funding/basis sinyalini gerçekten görülmemiş bir OOS penceresinde kontrol edebilmesi için minimum güven sınırıdır. Faz 6'nın daha ileri maddeleri (CPCV/PBO/DSR), Faz 7'nin **başlaması** için zorunlu değildir, ama FAZ6A'nın kendisi zorunludur. Bu doküman Faz 7'nin strateji tasarımını **yapmaz.**

**Durum güncellemesi (FAZ6 Phase-Status Reconciliation ile):**

```
- Yukarıdaki minimum FAZ6A ön koşulu artık KARŞILANMIŞTIR (§22.3 —
  FAZ6A: COMPLETE).
- Faz 7 HENÜZ BAŞLAMAMIŞTIR — bu doküman Faz 7'yi başlatmaz, tasarlamaz,
  veya scope etmez.
- FAZ6A'nın (ve artık FAZ6B'nin) karşılanmış olması, kalan FAZ6C işinin
  atlandığı veya tamamlandığı anlamına GELMEZ — proje, Faz 7'ye
  başlamadan ÖNCE Faz 6'nın geri kalanını (FAZ6B artık COMPLETE'dir:
  non-zero-context Layer-2, return-series/Sharpe, candidate/trial
  foundation, VE Annualized Metrics [Sharpe/Sortino/Calmar/CAGR]
  hepsi IMPLEMENTED + TESTED'dır, bkz. §22.2, 28.H — 30/30; FAZ6C:
  advanced overfitting controls — purging/embargo foundation'ı artık
  LOCKED VE IMPLEMENTED + TESTED'dır (§28.I — 19/19); trial-group
  foundation'ı LOCKED VE IMPLEMENTED + TESTED'dır (§28.J — 19/19);
  Deflated Sharpe exact kontratı LOCKED VE IMPLEMENTED + TESTED (§28.K —
  27/27); PBO önkoşulu trial return matrix LOCKED VE IMPLEMENTED +
  TESTED (§28.L — 22/22); PBO/CSCV LOCKED VE IMPLEMENTED + TESTED
  (§28.M — 24/24); Holm düzeltme temeli LOCKED VE IMPLEMENTED +
  TESTED (§28.N — 18/18; p-değeri üretimi/aile kapsamı/seçim
  politikası açık); CPCV/parameter-stability HÂLÂ
  spec-lock edilmemiştir) tamamlamaya devam EDEBİLİR (bu doküman bir
  sıralama zorunluluğu icat etmez).
- CPCV/PBO/DSR'nin Faz 7'nin başlaması için zorunlu olmadığına dair
  yukarıdaki cümle DEĞİŞMEMİŞTİR/genişletilmemiştir — bu bölüm yalnızca
  FAZ6A'nın artık karşılanmış olduğunu kaydeder, başka hiçbir izin
  genişletmez.
```
