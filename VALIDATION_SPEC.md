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

**Problem:** Bir gelecekteki policy (örn. 50-bar moving average, 100-bar momentum, rolling volatility) `OOS.start` anında **legal, geçmiş** (lookahead değil) candle'lara ihtiyaç duyabilir — bu candle'lar `OOS.start`'tan öncedir ama gelecekte değildir, dolayısıyla bunlara erişim anti-lookahead'i ihlal etmez.

**Mevcut API bunu ayıramaz** (Bölüm 9'da source'tan doğrulanmıştır): `run_backtest_from_store(requested_start=OOS.start, ...)` çağrısı, policy'ye yalnızca `OOS.start`'tan başlayan candle'ları gösterir — daha öncesi hiç görünmez. Diğer yandan, eğer `requested_start`'ı geriye (`context_start < OOS.start`) çekersek, mevcut replay loop'u context candle'larını da **ekonomik olarak** işler: ilk context candle'ından itibaren fill/cost/realized-PnL/equity-point üretilmeye başlar — çünkü `run_backtest_replay`'ın döngüsü, "yalnızca görünürlük, trade yok" diye bir ayrı mod tanımıyor; her candle hem policy'ye görünür hem de execution/accounting'e tabidir.

**Sonuç:** mevcut API, "geçmişi gör ama yalnızca `OOS.start`'tan itibaren skorla" ayrımını **temiz bir şekilde ifade edemez.**

**Bu spec bu sorunu ŞİMDİ çözmez.** Bunun yerine:

- Bu, **generic bir OOS runner**'ın inşasını blokeler (Bölüm 9).
- Bu, **temporal-window primitive**'inin (Bölüm 6/7) inşasını **bloke etmez** — o primitive tamamen pure/store-free'dir ve bu sorundan bağımsızdır.
- Dedicated bir **"OOS Context/Warm-up API Pre-flight"** mikro-adımı, generic OOS runner'dan **önce** gelmelidir (Bölüm 23, FAZ6A MS3).

### 8.1 Olası Gelecek Tasarımlar (Analiz — Kilitlenmez)

| Seçenek | Açıklama | Artı | Eksi |
|---|---|---|---|
| A | OOS policy yalnızca OOS candle'larını görür | Basit, sıfır engine değişikliği, kontaminasyon riski sıfır | Lookback gerektiren stratejileri OOS başında kırar (ilk N candle context'siz kalır) |
| B | Runner `context_start < evaluation_start` kabul eder; ama fill/cost/realized-PnL/equity yalnızca `evaluation_start`'tan itibaren sayılır | Ekonomik olarak doğru, generic, herhangi bir policy'nin lookback'ini destekler | `replay.py`'a additive ama gerçek bir mimari değişiklik gerektirir (yeni `evaluation_start` sınırı) |
| C | Policy'ye ayrı, salt-okunur bir warmup candle sequence enjekte edilir | `replay.py`'ı değiştirmez | `PolicyContext`/`BacktestPolicy` signature'ını değiştirir — bugünkü `target_position(context)` sözleşmesini genişletir |
| D | Gelecekteki bir Feature/Research katmanı (ARCHITECTURE.md Katman 2/3) lookback'i önceden hesaplayıp policy'ye ham candle yerine feature-value verir | Warm-up sorununu bu katmandan tamamen kaldırır | Henüz var olmayan bir katmana bağımlı; Faz 6'nın kapsamı değil |
| E | Policy kendisi, yeterli bar birikene kadar FLAT/NO-TRADE döner | Sıfır engine/spec değişikliği, sıfır kontaminasyon riski | Her OOS penceresinin başında gerçek değerlendirme süresi "israf" edilir; policy yazarının disiplinine bağımlı |

**Current leading direction: B — NOT LOCKED.** Gerekçe: B, tek genel, policy-agnostik ve bu repo'nun mevcut additive-extension convention'ıyla (örn. `funding_events`/`funding_model`'in `run_backtest_replay`'a additive keyword-only, default'ta davranışı değiştirmeyen parametreler olarak eklenmesi — FUNDING-SPEC MS10) tutarlı görünen bir çözümdür. Ama: C, `BacktestPolicy`'nin public contract'ını genişletir (daha invaziv, henüz elenmiş değil); D henüz var olmayan bir katmana bağımlıdır (henüz elenmiş değil); A ve E foundation'da **hâlâ kullanılabilir** (bkz. Bölüm 11) ve sıfır engine değişikliği gerektirir (henüz elenmiş değil). **Bu doküman B'yi implement etmez, B'yi diğer dört seçeneğin (A/C/D/E) önüne kesin olarak da koymaz** — yalnızca bugünkü en olası yönü kaydeder. Beş seçeneğin (A/B/C/D/E) exact karşılaştırması ve nihai seçim FAZ6A MS3'ün pre-flight konusudur (Bölüm 23) — MS3'ten önce hiçbir seçenek kilitlenmiş sayılmaz.

### 8.2 Warm-up için Data Quality (Analiz — Kilitlenmez)

Eğer gelecekte context/warm-up candle'ları desteklenirse, bunlar da:

```
- finalized (partial/live candle YOK)
- quality-gated (aynı candle quality gate'ten geçer, bypass YOK)
- aynı partition (exchange/market_type/symbol/timeframe)
- doğru sırada, future data YOK
```

olmalıdır — mevcut `prepare_backtest_dataset`'in candle path'i için zaten geçerli olan kural, warm-up candle'ları için de **istisnasız** uygulanır.

**Funding context sorusu:** warm-up, funding history'ye ihtiyaç duyar mı? Muhtemelen **hayır**, çünkü mevcut `BacktestPolicy.target_position(context: PolicyContext)` funding'i hiç görmez (`PolicyContext` yalnızca `as_of_time` + candle prefix taşır — bkz. `backtest/policy.py`). Feature-context verisi (candle lookback) ile ekonomik-funding-settlement verisi **ayrı kavramlardır**; bu spec bir feature sistemi icat etmez.

## 9. Current API Limitation Audit (Bölüm 8'in Kaynak Doğrulaması)

`src/crypto_quant_lab/backtest/store_runner.py` ve `replay.py`'dan doğrulanmıştır:

- `run_backtest_from_store(requested_start=X, requested_end=Y, ...)` → `prepare_backtest_dataset` yalnızca `[X, report.effective_end)` candle'larını query eder ve döner. `X`'ten önceki hiçbir candle asla mevcut değildir.
- `run_backtest_replay`'ın ana döngüsü (`for i, candle in enumerate(candles): ...`), **her** candle için sırasıyla funding sweep → equity mark → `PolicyContext` → policy call → (son candle değilse) execution çalıştırır. Loop'ta "yalnızca görünürlük, trade yok" diye ayrı bir mod **yoktur** — sequence'e giren her candle hem `PolicyContext.candles`'a hem de execution/equity mark mekanizmasına eşit şekilde tabidir.

**Sonuç (LOCKED — audit finding, icat edilmemiştir):**

```
Mevcut API, pre-evaluation lookback history'yi WITHOUT contamination
sağlayamaz.
```

Bu, Bölüm 8.1'deki B tasarımı gibi bir gelecek engine-additive extension'a ihtiyaç duyar. **Sahte destek icat edilmez.**

## 10. State Carryover — Üç Ayrı Kavram (LOCKED)

Bu üç kavram **aynı şey değildir** ve karıştırılmaz:

```
1. Historical context carry-in     — policy'nin GÖRDÜĞÜ geçmiş candle'lar (Bölüm 8)
2. Economic account-state carry-in — cash/position/realized_pnl IS'ten OOS'a taşınır mı
3. Parameter/candidate carry-in    — IS'te seçilen dondurulmuş candidate/parametre OOS'a taşınır mı
```

**(1) Historical context:** muhtemelen gerekli (Bölüm 8), ama bugün temiz bir mekanizması yok — deferred.

**(2) Economic account-state:** Bölüm 11'de **fresh (A)** olarak kilitlenir — foundation için.

**(3) Parameter/candidate carry-in:** candidate abstraction var olduğunda **zorunlu** olacaktır (Bölüm 18) — dondurulmuş bir candidate, IS'ten OOS'a mutlaka taşınmalıdır (aksi halde "OOS'u değerlendirmek" anlamsız olur). Bu foundation'da henüz yok, çünkü candidate abstraction henüz yok.

"Fresh state" kısaltması, **yalnızca (2)'yi** ifade eder — (1)'i (legal historical context) sessizce silmez; (1) ayrı, henüz çözülmemiş bir sorundur (Bölüm 8).

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

Bu, **FAZ6A MS3'te hangi warm-up mekanizması seçilirse seçilsin geçerli kalan bir prensiptir** — exact API tasarımından bağımsız olarak kilitlenir. Bugün, historical context (Bölüm 8) desteklenmediği için, bu prensip triviyal şekilde sağlanır: her pencere, IS candle'larını hiç görmeyen, tamamen bağımsız bir `run_backtest_from_store` çağrısıdır (Bölüm 8.1 seçenek A, mevcut fiili davranış — ama nihai runner mekanizması değil, bkz. Bölüm 13). MS3, B (veya başka bir seçenek) ile historical context desteği eklediğinde, bu **AYNI** prensip explicit olarak yeniden kanıtlanmalıdır — context desteği eklemek bu accounting kontratını **asla** gevşetemez.

**Final IS signal / context-candle rule (LOCKED, mekanizmadan bağımsız):** IS sırasında üretilen ekonomik bir aksiyon/sinyal, hiçbir bağımsız OOS evaluation'ında pending bir fill olarak **ortaya çıkamaz.** Eğer gelecekte (MS3 sonrası) pre-OOS geçmiş candle'lar salt-okunur information context olarak görünür hale gelirse, bu context candle'ları:

```
- evaluation_start'tan ÖNCE hiçbir skorlanmış PnL üretemez
- hiçbir carried pozisyon yaratamaz
- hiçbir pending fill yaratamaz
- OOS başlangıç cash/account state'ini mutate edemez
```

Bir warm-up candle **yalnızca information context'tir, asla bir execution kaynağı değildir** — bu ayrım, exact warm-up API tasarımı MS3'te kilitlenmeden **önce**, bağımsız bir prensip olarak burada kilitlenir.

## 12. `as_of_time` Contract (LOCKED — Yanlış Anlaşılmayı Önlemek İçin)

`as_of_time`, **veri finalization sınırıdır** (`DATA_QUALITY_SPEC.md`: hangi candle'ların "kapanmış/finalized" sayıldığını belirler) — **araştırma-gizlilik (research-secrecy) mekanizması DEĞİLDİR.**

```
as_of_time KULLANILAMAZ olarak:
    IS/OOS "gizliliğini" sağlayan bir mekanizma
```

Settled tarihsel veri için bugün `OOS`'u `as_of_time > OOS.end` ile çalıştırmak **veri açısından legal**dir (gerçekleşmiş geçmiş veri zaten kesinleşmiştir) — ama bu, bir candidate'in **seçim sürecinin** OOS sonucunu görmediğini garanti **etmez.** Selection leakage (Bölüm 20), `as_of_time`'ın değil, **research-process disiplininin** (henüz code ile enforce edilemeyen) sorumluluğundadır — bu ayrım Bölüm 19'da tekrar netleştirilir.

## 13. First Foundation Mode (LOCKED — Hedef; Generic Runner MS3'e Gated)

FAZ6A'nın hedeflediği ilk validation modu:

```
FIXED-POLICY TEMPORAL EVALUATION

Bir zaten inşa edilmiş / dondurulmuş BacktestPolicy,
birden fazla temporal pencere üzerinde BAĞIMSIZ olarak değerlendirilir.

YOK: fitting, optimizer, candidate selection.
```

Bu, henüz **tam walk-forward optimization değildir** (Bölüm 14) — bu ayrım kilitlidir.

**Ama bu modun GENERIC, executable bir runner'ı bugün mimari olarak güvenli değildir.** Herhangi bir `BacktestPolicy`'nin (özellikle lookback/rolling-feature kullanan bir policy'nin) `run_backtest_from_store(requested_start=OOS.start, ...)` ile doğrudan, doğru şekilde değerlendirilebileceği **iddia edilmez** — Bölüm 8/9'un audit bulgusu gereği, mevcut API pre-evaluation historical context'i contamination olmadan sağlayamaz. Bu nedenle:

```
- Fixed-policy temporal evaluation FAZ6A'nın bir HEDEFİDİR (Bölüm 22).
- Onun generic runner kontratı, FAZ6A MS3'ün warm-up/context kararına
  KADAR gated'dir (bkz. Bölüm 23).
- MS2 (temporal-window primitives) bu karara bağımlı DEĞİLDİR — tamamen
  pure/store-free'dir ve bağımsız olarak inşa edilebilir.
```

Context/lookback kullanmayan trivial bir policy için, bugünkü `run_backtest_from_store`'un pencere-başına bağımsız çağrılması **zaten doğru sonucu üretir** (Bölüm 11) — ama bunu şimdiden **genel bir runner contract'ı** olarak kilitlemek, henüz çözülmemiş warm-up sorununu (Bölüm 8) sessizce görmezden gelmek olurdu. Generic runner'ın exact mekanizması MS3'te kilitlenir (Bölüm 21, Bölüm 23).

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

## 15. Metrics Foundation — Staged Bağımlılık (LOCKED)

`BacktestResult` **değişmeden** kalır (Bölüm 4). Metrikler `equity_curve`'den **dışarıda** türetilir.

**Aşama 1 (foundation, NOW):** total return (`final_equity/initial_cash - 1`), max drawdown — `equity_curve`'den doğrudan, ek bağımlılık gerektirmeden hesaplanabilir.

**Aşama 2 (LATER IN FAZ 6, kendi dedicated contract mikro-adımı):** return-series / Sharpe foundation — Bölüm 16'daki açık sorular **önce** çözülmelidir; formül burada gelişigüzel kilitlenmez.

**Aşama 3 (LATER IN FAZ 6):** Deflated Sharpe, PBO, multiple-testing corrections, parameter stability — Bölüm 17.

**Faz 6'nın "foundation" tamamlanma tanımı, Sharpe'ı kalıcı olarak göz ardı edip yalnızca total-return/max-drawdown ile tanımlanmaz** — Aşama 2/3, Bölüm 22'deki alt-faz yapısında **explicit olarak** yer alır, yalnızca implementasyon sırası ertelenir.

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

## 19. Leakage / Anti-Overfitting — Engine vs. Process (LOCKED)

**Engine-enforceable (bu spec'in kilitlediği kod-seviyeli korumalar):**

```
- IS/OOS overlap → ValueError (Bölüm 7)
- her pencere kendi bağımsız quality/funding gate'inden geçer (Bölüm 4, 24)
- OOS fresh economic state (Bölüm 11)
- as_of_time'ın mevcut anti-lookahead semantics'i her pencerede korunur
```

**Research-process riskleri (henüz code ile enforce edilemez):**

```
- bir araştırmacının/LLM'in candidate'i dondurmadan ÖNCE OOS metriklerini görmesi
  ("peeking") — yalnızca disiplinle önlenir, bu spec'in kapsamı dışında
- aynı OOS penceresinin art arda birçok candidate seçim iterasyonunda
  tekrar kullanılması (OOS'un fiilen ikinci bir IS'e dönüşmesi) —
  yalnızca trial-count tracking (Bölüm 18) var olduğunda tespit edilebilir
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
- gelecekte bir context/evaluation-boundary desteği gerekirse, bu yalnızca
  ADDITIVE, ayrı bir dokümanda specify edilen ve kendi regression suite'i
  ile test edilen bir extension olabilir
- exact API mechanism (Bölüm 8.1'deki hangi seçenek) FAZ6A MS3'e deferred'dir
```

**Bu MS1, `run_backtest_from_store`/`run_backtest_replay`'in public signature'larının sonsuza kadar literal olarak aynı kalacağını GARANTİ ETMEZ** — yalnızca, herhangi bir gelecekteki değişikliğin additive/geriye-uyumlu olacağını ve ayrı bir review'dan (MS3) geçeceğini kilitler. Exact extension bu dokümanda tasarlanmaz.

## 22. Faz 6 Alt-Faz Yapısı (LOCKED — "Foundation" ≠ "Faz 6 Complete")

```
FAZ 6A — Temporal Validation Foundation
    temporal window primitive, IS/OOS split, fixed-policy rolling OOS
    evaluation, basic return/drawdown metrics.

FAZ 6B — Context/Warm-up + Metrics + Experiment Foundation
    OOS context/warm-up API implementasyonu (FAZ6A MS3 pre-flight'ında
    Bölüm 8.1'in A/B/C/D/E seçenekleri arasından seçilip kilitlenecek
    exact mekanizma — bugün B yalnızca leading direction'dır, LOCKED
    DEĞİLDİR), return-series/Sharpe contract, candidate/trial abstraction.

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
  OOS CONTEXT / WARM-UP API PRE-FLIGHT (READ-ONLY)
  — exact mekanizma bu dokümanda ÖNCEDEN varsayılmaz; MS3'te seçilip
  kilitlenir. Generic OOS runner'dan ÖNCE gelir.

FAZ6A MS4+:
  MS3'ün bulgularına bağlı — bu dokümanda önceden kilitlenmez.
```

**MS3 scope (LOCKED — pre-flight'in kendisi, exact mekanizması DEĞİL):**

MS3, Bölüm 8.1'deki exact mekanizmayı bu dokümanda önceden varsaymaz — özellikle `evaluation_start` gibi bir parametrenin kesin olarak ekleneceği burada iddia edilmez. MS3 en az şu 5 yaklaşımı karşılaştırmalıdır (Bölüm 8.1):

```
A) OOS-only history (context yok)
B) context_start < evaluation_start benzeri, ayrı bir context/evaluation
   boundary (bugünkü CURRENT LEADING DIRECTION — NOT LOCKED)
C) policy'ye ayrı, salt-okunur bir warm-up candle sequence enjekte edilmesi
D) feature-layer / precomputed historical context yönü
E) policy'nin, yeterli OOS history birikene kadar sinyal üretmemesi
```

MS3, bu 5 yaklaşımdan tam olarak birini seçip exact mekanizmayı **LOCK eder** — seçim bu MS1'de değil, MS3'te yapılır. B bugün yalnızca leading direction'dır; MS3'ün A/C/D/E'yi eleyip B'yi seçmesi **garanti değildir.**

MS3, seçtiği mekanizmadan bağımsız olarak, Bölüm 11'de zaten kilitlenmiş şu invariant'ları korumak **zorundadır:**

```
- historical context yalnızca information olabilir, asla bir execution
  kaynağı değildir
- evaluation_start'tan (veya seçilen mekanizmanın eşdeğer sınırından)
  ÖNCE hiçbir skorlanmış PnL üretilemez
- hiçbir carried pozisyon yaratılamaz
- hiçbir pending fill yaratılamaz
- OOS başlangıç cash/account state'i mutate edilemez
- canonical replay/accounting/execution semantics'i reuse edilir
  (Bölüm 4, 21) — validation-specific replay YASAK
- candle/funding data quality gate bypass edilemez (Bölüm 24)
```

Bu dizinin ötesi (fixed-policy OOS runner implementasyonu, walk-forward window advance, metrics foundation implementasyonu, 6B/6C mikro-adımları) **MS3'ün sonucuna bağımlı olduğu için burada detaylandırılmaz** — MS3 tamamlanmadan bir generic OOS runner'a **commit edilmez.**

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

## 28. Acceptance Criteria — İki Ayrı Grup (LOCKED)

Foundation acceptance, runner-independent (pure/store-free) kontratlar ile generic OOS runner'a bağımlı, MS3'e gated kontratlar **karıştırılmaz.** Önceki sürümün tek listedeki "15 madde" sayısı korunmaya çalışılmaz — spec wording'ine göre yeniden türetilmiştir (bkz. 28.A/28.B altındaki sayılar).

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
14. Exact warm-up/context API mekanizması bu MS1'de **LOCKED DEĞİLDİR**; FAZ6A MS3'e deferred'dir (Bölüm 8.1, 23).
15. `BacktestResult`, validation tarafından değişmeden (unchanged) canonical economic output olarak kalır (Bölüm 4, 21).
16. Validation, canonical backtest semantics'ini (replay/accounting/execution/cost/funding) compose eder — yeniden implement etmez (Bölüm 4, 21).
17. Validation-specific bir replay/accounting/execution engine **yasaktır** (Bölüm 4, 21).
18. Tüm girdi (store, partition, pencereler, `as_of_time`, config, policy, cost/funding model) explicit'tir (Bölüm 25).
19. Wallclock veya kontrolsüz/seed'siz randomness kullanılmaz (Bölüm 25).
20. Foundation scope tek-symbol'dür (Bölüm 5).
21. Foundation scope yalnızca `1h`/`4h` timeframe'i kapsar (Bölüm 5).
22. Foundation, hiçbir yeni runtime dependency gerektirmez (Bölüm 26).

**Locked foundation acceptance count: 22.**

### 28.B — PENDING MS3-GATED RUNNER ACCEPTANCE

Bu kriterler henüz **SATISFIED DEĞİLDİR.** Generic OOS runner implement edilmeden önce, FAZ6A MS3 tarafından exact mekanizmaya (Bölüm 8.1 A/B/C/D/E) bağlanmalıdır. **Foundation locked acceptance count'una (28.A) dahil edilmezler.**

1. Legal pre-OOS context, candle quality gate'ten geçmiş (quality-gated) olmalıdır (Bölüm 8.2, 24).
2. Context, evaluation edilen pencere ile aynı exact partition'a (exchange/market_type/symbol/timeframe) ait olmalıdır (Bölüm 8.2).
3. Context, canonical ordering/finalization contract'ını korumalıdır — partial/live candle yasak (Bölüm 8.2, 9).
4. Context hiçbir future data içeremez (Bölüm 8.2, 9, 12).
5. Context, evaluation_start'tan (veya MS3'ün seçtiği eşdeğer sınırdan) önce hiçbir skorlanmış PnL üretemez (Bölüm 11).
6. Context hiçbir carried pozisyon yaratamaz (Bölüm 11).
7. Context hiçbir pending fill yaratamaz (Bölüm 11).
8. Context, OOS başlangıç cash/account state'ini mutate edemez (Bölüm 11).
9. Ekonomik accounting, tam olarak MS3'te kilitlenecek evaluation boundary'de başlamalıdır (Bölüm 11, 13).
10. Candle quality gate, context desteğiyle birlikte canonical kalmalıdır — bypass yasak (Bölüm 24).
11. `funding_required=True` olan bir ekonomik evaluation, funding quality gate'ini korumalıdır — bypass yasak (Bölüm 24).
12. Transaction cost semantics'i (`CostModel`) korunmalıdır (Bölüm 21, 24).
13. Funding chronology/cost semantics'i (`FundingModel`) korunmalıdır (Bölüm 21, 24).
14. Generic lookback kullanan bir `BacktestPolicy`, pencere-boundary distortion'ı olmadan (ilk N candle'ı context'siz kırmadan) değerlendirilebilmelidir (Bölüm 8.1, 13).
15. Context desteği, canonical replay'i fork etmemeli / ikinci bir engine yaratmamalıdır (Bölüm 4, 21).

**Pending MS3-gated runner acceptance count: 15.**

**İleri seviye Faz 6 kategorileri (28.A/28.B'nin hiçbirine dahil DEĞİL, ayrı ve pending):** purging/embargo (17.1), CPCV (17.2), Sharpe-ailesi/return-series (16, 17.3), Deflated Sharpe (17.4), PBO (17.5), multiple-testing corrections (17.6), parameter stability (17.7), candidate/trial abstraction (18).

## 29. Faz 6 Sonrası (Bilgi Amaçlı — Bu Dokümanda Tasarlanmaz)

ROADMAP.md'deki bir sonraki faz **Faz 7 — İlk Funding/Basis araştırması**dır. Faz 7'nin güvenilir olabilmesi için, en azından Bölüm 22'deki FAZ6A (temporal split + rolling fixed-policy OOS evaluation + basic return/drawdown metrikleri) tamamlanmış olmalıdır — bu, Faz 7'nin IS'te seçilen bir funding/basis sinyalini gerçekten görülmemiş bir OOS penceresinde kontrol edebilmesi için minimum güven sınırıdır. Faz 6'nın daha ileri maddeleri (CPCV/PBO/DSR), Faz 7'nin **başlaması** için zorunlu değildir, ama FAZ6A'nın kendisi zorunludur. Bu doküman Faz 7'nin strateji tasarımını **yapmaz.**
