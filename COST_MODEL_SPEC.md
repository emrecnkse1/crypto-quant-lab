# COST_MODEL_SPEC

Bu doküman, Faz 5A — Transaction Cost katmanının kodlanmasından önce gereksinimleri ve doğruluk kurallarını kesinleştirir. Bu bir tasarım dokümanıdır; kod, bağımlılık veya somut implementasyon içermez.

## 1. Amaç

Faz 4, cost-aware bir mimari kurdu (her fill bir `CostModel` üzerinden maliyet hesabından geçer) ama tek zorunlu implementasyon her zaman sıfır döndüren `ZeroCostModel` idi. Bu doküman, Faz 5'in **transaction-cost yarısını** (commission, spread, slippage, composite) bağlayıcı hale getirir: gerçekçi, sıfır-olmayan, proportional friction modelleri.

**Bu dokümanın kapsamadığı şey:** funding. Funding, fill-tetiklemeli değil zaman/pozisyon-tetiklemelidir ve kendi domain/data/storage/timing tasarımını gerektirir. Funding, Faz 5B'de ayrı bir spec çalışmasıyla ele alınacaktır (bkz. Bölüm 18).

## 2. Diğer Spec'lerle İlişki

`COST_MODEL_SPEC.md`, `BACKTEST_SPEC.md`'nin **yerine geçmez** ve onu **değiştirmez**. Faz 4, tamamlanmış ve acceptance-audited (35/35 PASS) bir fazdır; bu doküman onun üzerine **katman ekler**, onu yeniden tanımlamaz.

Bu doküman özellikle şu Faz 4 kontratlarını **binding foundation** olarak referans alır ve **hiçbirini değiştirmez**:

- `CostModel` Protocol ve Cost Model Boundary (BACKTEST_SPEC.md Bölüm 21, 22)
- Next-open execution timing (Bölüm 10)
- Decimal-only accounting (Bölüm 17, 29)
- Cost / realized-PnL ayrımı ve tutarlılık invariant'ı (Bölüm 17)
- Determinism garantisi (Bölüm 3, 28)
- Last-candle rule ve no-op semantics (Bölüm 11, 16)

**Çelişki politikası:** Bu dokümanın herhangi bir maddesi BACKTEST_SPEC.md ile çelişiyor görünüyorsa, BACKTEST_SPEC.md **sessizce değiştirilmez** — çelişki açıkça raporlanır ve çözülene kadar bu dokümanın ilgili maddesi askıda kalır.

**Not:** BACKTEST_SPEC.md Bölüm 22'deki bilinen cross-reference typo'su ("bkz. Bölüm 26" → olması gereken "bkz. Bölüm 27") bu dokümanla **ilgisizdir** ve düzeltilmemiştir.

## 3. Scope — Faz 5A / Faz 5B Ayrımı (MUST)

Faz 5, iki bağımsız kapanabilir alt-faza ayrılır:

- **Faz 5A — Transaction Cost** (bu doküman): commission, spread, slippage, composite. Mevcut Faz 4 pipeline'ı üzerinden **hiçbir public API değişikliği olmadan** teslim edilebilir.
- **Faz 5B — Funding**: kendi domain/data/storage/timing/sign-convention tasarımını gerektirir; bu doküman kapsamı **dışındadır** (bkz. Bölüm 18, 19).

Faz 5A, Faz 5B'yi beklemeden kendi acceptance audit'iyle kapanabilir (bkz. Bölüm 20, 21).

## 4. Existing CostModel Protocol (MUST NOT DEĞİŞTİRİLMEZ)

Faz 4'ün `CostModel` Protocol'ü **aynen** korunur:

```
CostModel:
    calculate_cost(*, quantity: Decimal, execution_price: Decimal) -> Decimal
```

- `quantity`: execution katmanının zaten sağladığı **unsigned (absolute) physical fill quantity** (`abs(quantity_delta)`). Cost model bunu kendi içinde yeniden `abs()`'lamaz veya işaretini değiştirmez — bu sorumluluk execution katmanınındır ve Faz 5A'da **değişmez**.
- `execution_price`: Faz 4'ün next-open simulated fill fiyatı. Cost model bu fiyatı **değiştirmez**, yalnızca girdide okur.
- **No side.** Buy/sell yönü parametre olarak geçirilmez.
- **No timestamp.**
- **No position state.**

Faz 5A'nın hiçbir modeli bu Protocol'ü genişletmez veya yeni bir parametre eklemez. Side-aware davranış icat edilmez.

## 5. Transaction Cost Philosophy (MUST)

Commission, spread ve slippage, Faz 5A'da **execution price adjustment olarak değil, MONETARY COST olarak** modellenir.

```
Faz 4 next-open execution price:  DEĞİŞMEZ
CostModel output:                 cash'ten ayrı fill_cost olarak düşer
```

Bu tasarım, BACKTEST_SPEC.md Bölüm 17'deki tutarlılık invariant'ını korur:

```
total_pnl = realized_pnl + unrealized_pnl - total_cost
```

Spread/slippage, `average_entry_price`'ı kirletip realized/unrealized PnL bucket'larına gizlenmez — her zaman ayrı, adlandırılmış `total_cost` üzerinden görünür kalır.

## 6. Approximation Disclosure (MUST — DÜRÜSTLÜK)

Canonical historical store yalnızca OHLCV taşır — executable bid/ask quote veya order-book verisi **içermez**.

Bu nedenle Faz 5A'daki proportional spread ve slippage modelleri **research approximation'dır**, gerçek piyasa mikroyapısının simülasyonu değildir. Bu modeller şu iddialarda **bulunmaz**:

- Gerçek order-book simülasyonu
- Market impact
- Depth-aware fiyatlama
- Latency

Bu dürüstlük, sahte sofistike bir izlenim yaratmaktan kaçınmak için **açıkça** belirtilir.

## 7. Commission Model (LOCKED)

```
ProportionalCommissionModel:
    rate: Decimal

    calculate_cost(*, quantity, execution_price) -> Decimal:
        return quantity * execution_price * rate
```

Faz 4'ün execution modeli yalnızca market-at-next-open/full-fill olduğundan (Bölüm 15), her simüle edilen order yapısal olarak bir taker order'dır. Ancak isim buna göre daraltılmaz — `Taker` kelimesi class adına **bake edilmez**, çünkü maker/limit path şu an mevcut değildir ve gelecekte eklenirse `ProportionalCommissionModel` adı halen doğru kalmalıdır.

**İsim: `ProportionalCommissionModel`.**

## 8. Commission Rate Validation (MUST)

- `rate` genuine `Decimal` olmalı. `float`/`int`/`bool` → `TypeError`.
- `rate < 0` → `ValueError`.
- `rate == 0` → legal.
- Hiçbir default `rate` yoktur — caller açıkça sağlamak zorundadır.

## 9. Spread Model (LOCKED)

```
ProportionalSpreadCostModel:
    half_spread_rate: Decimal

    calculate_cost(*, quantity, execution_price) -> Decimal:
        return quantity * execution_price * half_spread_rate
```

Field adı kasıtlı olarak `half_spread_rate`'dir, belirsiz `spread_rate` **değil** — tek bir market fill, referans/mid fiyattan yaklaşık olarak **bir yarım-spread** öder (bid-ask genişliğinin tamamını değil). Bu isimlendirme, caller'ın ne büyüklükte bir değer sağlaması gerektiğini API yüzeyinde belirsizlik bırakmadan anlatır.

**İsim: `ProportionalSpreadCostModel`.**

## 10. Spread Validation (MUST)

- `half_spread_rate` genuine `Decimal` olmalı. `float`/`int`/`bool` → `TypeError`.
- `half_spread_rate < 0` → `ValueError`.
- `half_spread_rate == 0` → legal.
- Default yok.

## 11. Slippage Model (LOCKED)

```
ProportionalSlippageCostModel:
    rate: Decimal

    calculate_cost(*, quantity, execution_price) -> Decimal:
        return quantity * execution_price * rate
```

Bu, sabit-proportional slippage yaklaşımıdır — ciddi bir ilk model için yeterlidir. Size-dependent, depth-dependent, volatility-dependent ve market-impact-tabanlı slippage modelleri **deferred**'dır (bkz. Bölüm 19).

**İsim: `ProportionalSlippageCostModel`.**

## 12. Slippage Validation (MUST)

- `rate` genuine `Decimal` olmalı. `float`/`int`/`bool` → `TypeError`.
- `rate < 0` → `ValueError`.
- `rate == 0` → legal.
- Default yok.

## 13. Quantity / Price Input Semantics (MUST NOT DEĞİŞTİRİLMEZ)

Faz 5A modelleri, Bölüm 4'teki mevcut `CostModel` sınırına **aynen** uyar:

- `quantity` semantics değişmez: execution katmanının `abs(quantity_delta)` ile sağladığı unsigned physical fill quantity.
- `execution_price` semantics değişmez: Decimal simulated execution price.
- Side-aware davranış icat edilmez.
- Cost model, `quantity`'yi kendi içinde sessizce `abs()`'lamaz — bu zaten execution katmanının sorumluluğudur.
- float/int coercion yapılmaz.

## 14. Reversal Semantics (MUST)

```
LONG +q → SHORT -q
```

BACKTEST_SPEC.md Bölüm 16 gereği bu **tek bir physical fill**'dir, `2q` büyüklüğünde. Buna göre:

- Commission, spread, slippage — her biri `quantity = 2q` üzerinden **tam olarak bir kez** hesaplanır.
- Accounting tarafındaki close+open PnL attribution ayrımı (Bölüm 19), **iki ayrı transaction-cost event'i anlamına gelmez.** Bu yalnızca bir muhasebe temsilidir, fiziksel iki order değildir.

## 15. No-Op / Last-Signal Semantics (MUST — UNCHANGED)

- Aynı target'ın tekrarı (no-op) → `ExecutionResult` üretilmez → cost model **çağrılmaz.**
- Dataset'in son candle'ında üretilen signal, sonraki candle olmadığı için fill üretmez → cost model **çağrılmaz.**

Bu davranış Faz 4'ten değişmeden korunur; hiçbir Faz 5A modeli bu kuralı etkilemez.

## 16. Composite Model (LOCKED)

```
CompositeCostModel:
    components: tuple[CostModel, ...]

    calculate_cost(*, quantity, execution_price) -> Decimal:
        total = Decimal(0)
        for component in components:
            total += component.calculate_cost(quantity=quantity, execution_price=execution_price)
        return total
```

- `components` genuine `tuple` olmalı — `list`/`set`/başka unordered koleksiyon **değil.**
- İterasyon **kesin tuple sırasıyla**, sol-dan-sağa gerçekleşir — Decimal toplamanın matematiksel olarak associative/commutative olması, bu dokümanın iterasyon sırasını rastgele bırakmasına **gerekçe olarak kullanılmaz.** BACKTEST_SPEC.md Bölüm 28'in "hidden/unordered collection semantics yasak" prensibiyle tutarlı olarak, sıra her zaman **explicit ve deterministic** kalır.
- Sonuç genuine `Decimal` toplamıdır.

## 17. Composite Empty Behavior (MUST)

Boş `components` tuple'ı **legal**dir ve `Decimal(0)` döndürür — ekonomik olarak sıfır transaction cost anlamına gelir.

Ancak normal konfigürasyonda **açık sıfır-friction baseline'ı** olarak tercih edilen hâlâ `ZeroCostModel`'dir — `CompositeCostModel(components=())` bir "sıfır maliyet ifade etme yolu" olarak **teşvik edilmez**, yalnızca legal bir edge-case olarak tanımlanır.

## 18. Composite Output Sign (MUST)

`CompositeCostModel`, sonucun `>= 0` olmasını **zorlamaz.**

Sebep: genel `CostModel` Protocol'ü zaten negatif cost/rebate'e izin verir (BACKTEST_SPEC.md'nin `apply_fill`/negative `fill_cost` precedent'i ile tutarlı). Bölüm 8/10/12'deki isimli spesifik friction modelleri (`ProportionalCommissionModel` vb.) kendi `rate`'lerini negatif reddeder — ama `CompositeCostModel` genel bir **composition infrastructure**'dır, bileşenlerinin anlamını varsaymaz. Gelecekte bir rebate-şekilli component eklenirse, composite'in genel toplamı legal olarak negatif olabilir.

## 19. Component Output Type (MUST)

Bir component `calculate_cost` çağrısından genuine `Decimal` olmayan bir değer döndürürse, bu **açık bir `TypeError`** ile sonuçlanmalıdır — sessiz float mixing veya `Decimal(float(...))` coercion **icat edilmez.**

Exact validation noktası (composite'in kendisi mi, yoksa her component'in kendi sözleşmesine mi güvenilir) bir implementasyon mikro-adımı kararıdır; ama kontrat şudur: **component output'ları genuine Decimal olmak zorundadır**, aksi halde davranış sessiz değil, açık bir hata olmalıdır.

## 20. Decimal-Only Rule (MUST)

- Tüm finansal/rate aritmetiği yalnızca `Decimal`.
- Float conversion **yok.**
- Implicit numeric coercion **yok.**
- Modeller içinde cent'e yuvarlama **yok.**
- Sonuç, konfigüre edilmiş Decimal context/aritmetiğin ürettiği tam precision'da döner.

## 21. Rate Unit Convention (LOCKED)

Kanonik API birimi: **Decimal fraction.**

| bps | Decimal fraction |
|---|---|
| 1 bp | `Decimal("0.0001")` |
| 5 bp | `Decimal("0.0005")` |
| 10 bp | `Decimal("0.001")` |
| 25 bp | `Decimal("0.0025")` |
| 100 bp / 1% | `Decimal("0.01")` |

Faz 5A'da ayrı bir `BasisPoints` value object **tanıtılmaz** — plain `Decimal` fraction, bu repo'daki her diğer rate/quantity alanıyla (`position_quantity: Decimal` vb.) tutarlıdır ve en küçük doğru API'dir.

## 22. Worked Example (İllüstratif — Bağlayıcı Değil)

```
quantity          = Decimal("2")
execution_price   = Decimal("100")
commission rate   = Decimal("0.001")
half_spread_rate  = Decimal("0.0005")
slippage rate     = Decimal("0.001")

commission = 2 * 100 * 0.001  = Decimal("0.200")
spread     = 2 * 100 * 0.0005 = Decimal("0.1000")
slippage   = 2 * 100 * 0.001  = Decimal("0.200")

economic total = Decimal("0.5000")
```

Bağlayıcı olan Decimal **numeric equality**'dir — belirli bir exponent normalizasyonu şart koşulmaz.

## 23. No Magic Defaults (MUST)

- Hiçbir gerçekçi rate'in default değeri **yoktur.**
- Caller her model/rate'i **açıkça** sağlamak zorundadır.
- Sıfır friction her zaman **açık** olmalıdır: `ZeroCostModel` veya kasıtlı olarak sıfır konfigüre edilmiş bir model.
- Faz 5A'da exchange-specific hard-coded rate tabloları **yazılmaz.**

## 24. Result Contract Impact (MUST NOT DEĞİŞTİRİLMEZ)

`BacktestResult`, Faz 5A'da **tamamen değişmeden** kalır.

Eklenmez:

- `commission_cost`
- `spread_cost`
- `slippage_cost`

Mevcut `total_cost` alanı, agregat transaction cost'u taşımaya **devam eder.**

## 25. Cost Breakdown — Yalnızca Yön (Deferred)

Research-grade component attribution ("strateji spread'e mi commission'a mı daha çok kaybediyor?") değerli bir ihtiyaçtır, ama exact API bu mikro-adımda **kilitlenmez.**

Gelecekteki bir Faz 5A follow-up için yön:

- Generic, **label-keyed** component attribution tercih edilir (sabit `commission`/`spread`/`slippage`/`funding` alan seti değil).
- `BacktestResult` **casually genişletilmez.**
- Yalnızca breakdown için `Fill`/`Trade` entity'si **gerekmez** — running-sum accumulation yeterlidir.

Exact breakdown API'si kendi dedicated pre-flight'ını gerektirir. Bu doküman veya onu takip eden transaction-cost model implementasyonları, bir breakdown API'sini **prematüre icat etmez.**

## 26. Funding — Explicitly Deferred (MUST)

Funding, genel Faz 5'in bir parçasıdır ama **bu transaction-cost kontratının kapsamı dışındadır.**

Mevcut `CostModel` Protocol'ünün funding için neden yetersiz olduğu (yalnızca dokümantasyon amaçlı, formül/sign-convention **olmadan**):

- Funding, bir fill'e değil, **zaman içinde taşınan bir pozisyona** bağlıdır (event-tabanlı).
- **Signed** pozisyon bilgisine ihtiyaç duyar (yön, ödeyen/alan tarafı belirler) — mevcut Protocol yalnızca unsigned `quantity` taşır.
- Bir **event timestamp/historical funding rate** girdisine ihtiyaç duyar — mevcut Protocol'de zaman kavramı yoktur.
- Bir **reference/notional price**'a ihtiyaç duyar.

Bu doküman **kasıtlı olarak** exact bir funding formülü veya sign convention'ı **tanımlamaz.** Funding sign/timing/formül kararları, Faz 5B'de bağımsız bir pre-flight/spec çalışmasıyla denetlenecektir.

## 27. Funding Data Gap (Forward-Looking Not — MUST NOT DESIGN)

Mevcut repository yalnızca canonical OHLCV storage'a (`HistoricalCandleStore`) sahiptir. Historical funding-rate domain/storage/schema **şu anda mevcut değildir.**

Bu nedenle funding, kendi domain type'ını, storage abstraction'ını ve timing tasarımını gerektirir — bu doküman bu şemayı **tasarlamaz.** Bu, Faz 5B'nin kendi mikro-adımlarının işidir (bkz. Bölüm 28).

## 28. Faz 5A API Compatibility (MUST)

| API | Durum |
|---|---|
| `CostModel` Protocol | **unchanged** |
| `ZeroCostModel` | **unchanged** |
| `execute_target_on_next_candle` | **unchanged** |
| `run_backtest_replay` | **unchanged** |
| `run_backtest_from_store` | **unchanged** |
| `BacktestResult` | **unchanged** |

Commission, spread, slippage ve composite modellerinin tamamı, mevcut Faz 4 pipeline'ından **hiçbir public API kırılması olmadan** geçmelidir — yalnızca yeni `CostModel` implementasyonları eklenir, mevcut hiçbir imza değişmez.

## 29. Kapsam Dışı (Deferred — Faz 5A)

Aşağıdakilerin hiçbiri Faz 5A'nın kapsamında değildir:

- Gerçek bid/ask historical quote replay
- Order-book simülasyonu
- Market impact curve'leri
- Size/depth-dependent slippage
- Volatility-dependent slippage
- Maker order'lar
- Limit order'lar
- Partial fill
- Latency modeli
- Exchange-specific VIP tier'lar
- Fee-token indirimleri
- Dynamic fee schedule
- Borrow interest
- Liquidation
- Margin
- Vergi
- Multi-asset netting

## 30. Faz 5A Implementasyon Mikro-Adım Sırası (Bağlayıcı Bağımlılık Sırası)

1. `COST_MODEL_SPEC.md` contract (bu doküman)
2. `ProportionalCommissionModel` (pure)
3. `ProportionalSpreadCostModel` (pure)
4. `ProportionalSlippageCostModel` (pure)
5. `CompositeCostModel` (pure)
6. Transaction-cost replay/store-backed integration regresyonu (I/O, integration)
7. Faz 5A acceptance audit/checkpoint

Cost-breakdown API'si, gerekirse Microstep 5 ile 6 arasında kendi dedicated pre-flight'ını alabilir, ama **fırsatçı biçimde icat edilmez.**

Faz 5B (bu dokümanın kapsamı dışında, yalnızca referans için):

8. Funding domain/model contract
9. Historical funding data representation/storage
10. Funding event integration into replay
11. Funding E2E regresyon
12. Faz 5 (tam, 5A+5B) acceptance audit

## 31. Faz 5A Kabul Kriterleri (Acceptance Criteria)

1. Mevcut `CostModel` Protocol'ü değişmeden kalır.
2. Mevcut `ZeroCostModel` davranışı değişmeden kalır.
3. Commission exact Decimal hesaplama üretir.
4. Spread exact Decimal hesaplama üretir.
5. Slippage exact Decimal hesaplama üretir.
6. Composite, exact deterministic sol-dan-sağa Decimal toplamı üretir.
7. Spesifik model rate'leri yalnızca genuine Decimal kabul eder — float/int/bool reddedilir.
8. Negatif commission/spread/slippage rate'leri reddedilir.
9. Sıfır rate'ler legal'dir.
10. Hiçbir gerçekçi rate için hidden/default değer yoktur.
11. Reversal, her component'i fiziksel `2q` quantity üzerinden tam olarak bir kez ücretlendirir.
12. No-op hiçbir cost üretmez.
13. Son candle'da discard edilen signal hiçbir cost üretmez.
14. Cost'lar realized PnL'den ayrı kalır.
15. `total_pnl` reconciliation invariant'ı exact korunur.
16. Composite boş tuple'a izin verir → `Decimal(0)`.
17. Composite, negatif generic component output/sum'ı yasaklamaz.
18. Mevcut Faz 4 execution/replay/store API'leri değişmeden kalır.
19. Store-backed gerçekçi transaction-cost regresyonu PASS olur.
20. Deterministic tekrar çalıştırma exact eşitlik üretir.
21. Tüm testler tamamen offline çalışır.
22. Mevcut 715 test regresyonsuz PASS kalır.
23. `ruff check` / `ruff format --check` temiz kalır.
24. Funding, Faz 5A içinde yanlışlıkla implement/approximate edilmez.
