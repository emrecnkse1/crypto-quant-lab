# BACKTEST_SPEC

Bu doküman, Faz 4 — Backtest Engine katmanının kodlanmasından önce gereksinimleri ve doğruluk kurallarını kesinleştirir. Bu bir tasarım dokümanıdır; kod, bağımlılık veya somut implementasyon içermez.

## 1. Amaç

Backtest engine, projenin **tüm strateji/araştırma çalışmasının maliyetler dahil geçmiş veri üzerinde deterministik olarak doğrulandığı** katmandır (ARCHITECTURE.md, Katman 4: "Stratejilerin geçmiş veri üzerinde maliyetler dahil test edildiği ve doğrulandığı katman").

Bu spec, backtest doğruluğu için kritik olan şu kararları bağlayıcı hale getirir:

- Execution modeli (deterministic, bar-by-bar, event-driven)
- Veri kaynağı ve veri kalite ön-koşulu
- Anti-lookahead zaman kontratı
- Signal/execution timing
- Minimal pluggable policy/position/fill/accounting modeli
- Maliyet, risk, execution ve validation katmanlarıyla sınır

**Bu dokümanın YAPMADIĞI şeyler (bkz. Bölüm 34):** somut strateji/indicator implementasyonu, gerçekçi maliyet modeli, Risk Engine, walk-forward/anti-overfitting validasyonu, canlı execution.

## 2. Diğer Spec'lerle İlişki

`BACKTEST_SPEC.md`, `HISTORICAL_DATA_SPEC.md` ve `DATA_QUALITY_SPEC.md`'nin **yerine geçmez** ve onları **değiştirmez**. Backtest engine:

- Historical storage'a yalnızca `HISTORICAL_DATA_SPEC.md`'de tanımlı backend-neutral `HistoricalCandleStore` abstraction'ı üzerinden erişir.
- Veri kalitesi ön-koşulunu yalnızca `DATA_QUALITY_SPEC.md`'de tanımlı `build_data_quality_report_from_store(...)`/`DataQualityReport` üzerinden değerlendirir.
- Anti-lookahead zaman kontratını yalnızca `DATA_QUALITY_SPEC.md` Bölüm 10'da tanımlı `feature_availability_time()`/`is_candle_available_for_features()` üzerinden uygular.

Bu üç primitive/contract **bu dokümanda yeniden implement edilmez** — yalnızca yeniden kullanılır.

**Çelişki politikası:** Bu dokümanın herhangi bir maddesi `ARCHITECTURE.md`, `HISTORICAL_DATA_SPEC.md` veya `DATA_QUALITY_SPEC.md` ile çelişiyor görünüyorsa, önceki spec'ler **sessizce değiştirilmez** — çelişki açıkça raporlanır ve çözülene kadar spec'in ilgili maddesi askıda kalır.

**Not (ARCHITECTURE.md ile ilişki, Bölüm 21'de detaylandırılmıştır):** ARCHITECTURE.md, Backtest Layer'ı "maliyetler dahil test edildiği" katman olarak tanımlar; ROADMAP.md ise gerçekçi maliyet modellemesini ayrı bir Faz 5'e ("Realistic cost model") ayırır. Bu spec, bu iki ifadeyi şu şekilde uzlaştırır: Faz 4 engine **cost-aware bir mimariye** sahiptir (her fill maliyet hesabından geçer), ama Faz 4'te zorunlu tek `CostModel` implementasyonu maliyeti her zaman sıfır sayan `ZeroCostModel`'dir. Gerçekçilik Faz 5'in işidir.

## 3. Core Execution Model (MUST)

Faz 4 backtest core'u:

- **DETERMINISTIC**
- **BAR-BY-BAR**
- **EVENT-DRIVEN**

olacaktır. **Vectorized execution engine YOK.**

Vectorized hesaplamalar ileride Research/Feature tarafında (feature türetme, keşifsel analiz) kullanılabilir, ama execution/accounting semantiğinin **source-of-truth'u** deterministic bar-by-bar simulation'dır — vectorized bir hesaplama yolu bu semantiğin yerine geçemez veya onunla çelişemez.

**Determinism garantisi (MUST):** Aynı `dataset` + aynı `as_of_time` + aynı `config` + aynı policy davranışı + aynı cost model ile çalıştırılan iki backtest run'ı **value-for-value özdeş** sonuç üretmelidir (fills, trades, cash, positions, PnL, equity curve — bkz. Bölüm 28).

**Yasak (core engine içinde):**
- Hidden wall-clock (`datetime.now()`, `time.time()` vb.) — bkz. Bölüm 29.
- Randomness (`random`, `numpy.random` vb. tohum kontrolsüz veya kontrollü hiçbir rastgelelik kaynağı).
- Sıralaması belirsiz (unordered) koleksiyon semantiğine dayanan iterasyon (bkz. Bölüm 28).

## 4. MVP Universe (MUST)

Faz 4 MVP: **single-symbol per backtest run.**

Desteklenen timeframe'lar, mevcut `candle_duration()` contract'ıyla (DATA_QUALITY_SPEC.md Bölüm 3) birebir aynıdır:

- `1h`
- `4h`

Yeni bir timeframe duration primitive'i **yazılmaz** — mevcut `market_data.timeframes.candle_duration()` yeniden kullanılır.

Multi-symbol / portfolio-level cross-asset simulation bu Faz'ın **zorunlu kapsamı değildir.** Ancak domain modeller (config, result, policy context) gelecekteki multi-symbol genişlemeyi **yapay olarak imkânsız kılacak** şekilde sıkı/single-symbol-hardcoded tasarlanmamalıdır — yalnızca MVP scope'u single-symbol olarak sınırlanır, mimari buna izin verecek şekilde nötr kalır.

## 5. Data Source (MUST)

Backtest historical data'sı, mevcut canonical historical storage contract'ından (HISTORICAL_DATA_SPEC.md) gelir:

- Veri, `HistoricalCandleStore` (`storage/base.py`) üzerinden **salt-okunur** (`query()`) alınır.
- Backtest **asla** `write_batch()` çağırmaz. Backtest bir yazma/ingestion operasyonu değildir.

**Yasak:**
- Gerçek HTTP isteği
- Exchange API çağrısı
- Live feed / WebSocket
- API key kullanımı

Backtest **tamamen offline historical simulation'dır.**

## 6. Data Quality Gate (MUST)

Store-backed bir backtest dataset'i hazırlanırken, ilgili `[requested_start, requested_end)` aralığı ve `as_of_time` için mevcut:

```
build_data_quality_report_from_store(
    store,
    exchange=..., market_type=..., symbol=..., timeframe=...,
    requested_start=..., requested_end=..., as_of_time=...,
)
```

ile bir `DataQualityReport` üretilir.

**Zorunlu kural:**

```
report.overall_status != "PASS"
=> backtest BAŞLAMAZ
=> açık bir failure (ValueError veya eşdeğer domain exception) üretilir
```

Missing/unaligned/empty-invalid dataset üzerinde **silent backtest çalıştırma YASAK.**

Backtest engine, quality gate'in tespit ettiği gap'leri **repair etmez**:

**Kesinlikle YASAK:**
- Forward-fill
- Interpolation
- Synthetic candle üretimi
- Silent timestamp repair
- Silent sorting/deduplication

Bu kural DATA_QUALITY_SPEC.md Bölüm 12'deki "NORMALIZED katmanda synthetic candle üretimi yasak" invariant'ının backtest sınırındaki doğal uzantısıdır.

## 7. Finalized / Partial Data (MUST)

Backtest **yalnızca** Faz 3 canonical/finalized historical data contract'ını kullanır (DATA_QUALITY_SPEC.md Bölüm 9).

- Partial/live (henüz kapanmamış) candle backtest datasına **hiçbir koşulda dahil edilmez.** Canonical `HistoricalCandleStore`'un kendisi zaten yalnızca finalized veri içerir (HISTORICAL_DATA_SPEC.md Bölüm 9) — backtest bu invariant'ı bozacak bir "current candle" kavramı **icat etmez.**
- `as_of_time` semantiği her zaman **explicit** olmalıdır — backtest core'u hiçbir yerde hidden wall-clock okumaz (bkz. Bölüm 3, Bölüm 29).

## 8. Anti-Lookahead Invariant (MUST — KRİTİK)

DATA_QUALITY_SPEC.md Bölüm 10'daki feature availability invariant'ı, backtest için **bağlayıcıdır** ve yeniden implement edilmeden doğrudan kullanılır:

```
availability_time(candle) = candle.open_time + candle_duration(candle.timeframe)
```

**OPEN dahil** candle'ın hiçbir OHLCV alanı (`open`, `high`, `low`, `close`, `volume`), `availability_time(candle)` anından **önce** policy/feature/decision tarafından kullanılabilir kabul edilmez.

**Örnek (bağlayıcı referans senaryo, DATA_QUALITY_SPEC.md Bölüm 10/19 ile birebir tutarlı):**

```
1h candle, open_time = 10:00
availability_time = 11:00

as_of = 10:59:59.999999  -> unavailable
as_of = 11:00:00.000000  -> available (sınır dahil)
```

Bu kontrat, `data_quality.feature_availability.feature_availability_time()` / `is_candle_available_for_features()` üzerinden doğrudan kullanılır — backtest engine kendi paralel bir availability hesaplama mantığı **yazmaz.**

## 9. Signal Timing (MUST)

Simulation ilerlerken bir candle, kendi `availability_time`'ına ulaştığında policy tarafından **görülebilir hale gelir.**

- Policy, yalnızca o ana kadar available olmuş candle geçmişine erişebilir (available prefix).
- **Future candle erişimi YOK** — policy'ye hiçbir koşulda henüz availability boundary'sine ulaşmamış bir candle geçirilmez.
- **Current partial candle YOK** — Bölüm 7 gereği zaten canonical store'da böyle bir kavram yok; policy context'inde de yaratılmaz.

## 10. Execution Timing (MUST)

Faz 4 MVP execution kuralı:

```
signal generated after a candle becomes available
    ↓
order/fill occurs at the OPEN of the NEXT candle
```

**Örnek:**

```
10:00–11:00 candle (1h)
availability_time = 11:00

11:00'da bu candle'a dayalı signal üretilebilir.
Bu signal'in execution price'ı: 11:00'da başlayan
SONRAKİ candle'ın OPEN fiyatıdır.
```

**Kesinlikle YASAK:**
- Signal'i tetikleyen candle'ın kendi `open`/`high`/`low`/`close` fiyatlarından geriye dönük (retroactive) fill.
- Same-bar close sinyali + same-bar historical price execution.

Bu, **structural anti-lookahead rule'dur** — Bölüm 8'deki availability invariant'ı ile birlikte, hiçbir simulated karar kendi enactment anından önceki bir fiyata erişemez.

## 11. Last-Candle Rule (MUST)

Dataset'in son available candle'ında policy bir signal üretirse, ama bu signal için gerekli "sonraki candle" dataset'te **yoksa**:

- **Fill oluşturulmaz.**
- **Future price invent edilmez.**
- **Trade oluşturulmaz.**
- Pending instruction, dataset sonunda **sessizce** historical bir fiyata (örn. son candle'ın close'u) çevrilmez.

Backtest result, gerekirse bunu unfilled/final-state olarak açıkça temsil edebilir (örn. `pending_signal` alanı), ancak Faz 4 minimal implementasyonu bunun için **ayrı bir domain model zorunlu kılmaz** — scope bu amaçla genişletilmez.

## 12. Minimal Policy Contract

Faz 4 **somut bir trading strateji implement etmez.** Faz 11+ Strategy & Model layer'ı bu spec ile **prematurely freeze edilmez** (bkz. Bölüm 24).

Faz 4 engine'i test edebilmek için minimal, pluggable bir policy abstraction'ı tanımlanır:

```
PositionState: LONG | FLAT | SHORT   (küçük explicit enum/domain type)

Policy:
    available historical context -> PositionState
```

Policy'nin tek görevi: her simulation adımında, o ana kadar available olmuş candle geçmişine bakarak istenen (`desired`) `PositionState`'i üretmektir.

**Bu Faz'da YOK:**
- Indicator implementasyonu (RSI, MA, vb.)
- Trend/momentum stratejisi
- Mean reversion stratejisi
- Funding/basis stratejisi
- ML model

Testlerde yalnızca trivial deterministic policy'ler (örn. "her zaman LONG", "N. barda SHORT'a geç") kullanılır.

## 13. Position Size (LOCKED MVP)

Backtest config'i explicit, sabit bir miktar taşır:

```
position_quantity: Decimal   (> 0)
```

Policy yalnızca `LONG` / `FLAT` / `SHORT` seçer; **miktar seçmez.** Engine, seçilen `PositionState`'i signed target quantity'ye çevirir:

```
LONG  -> +position_quantity
FLAT  ->  0
SHORT -> -position_quantity
```

Dynamic sizing / volatility-based sizing / Kelly criterion / portfolio optimization bu Faz'ın kapsamı **değildir.** Risk-based sizing sonraki (Risk Engine, Faz 8) katmanlara bırakılır.

## 14. Position Model (MUST)

MVP: single symbol, **tek net signed position.**

```
quantity > 0  -> long
quantity == 0 -> flat
quantity < 0  -> short
```

- Hedge-mode (simultaneous long+short aynı sembolde) **YOK.**
- Multiple independent lots (ayrı entry fiyatlarıyla ayrı ayrı takip edilen pozisyon parçaları) **zorunlu değil** — engine aggregate net position üzerinden accounting yapar (bkz. Bölüm 17, Bölüm 19).

## 15. Order / Fill Model (LOCKED MVP)

MVP yalnızca destekler:

- **market-at-next-open**
- **full fill**

**Kesinlikle YOK:**
- Limit order
- Stop order / stop-limit
- Partial fill
- Order book / queue simulation
- Latency modeli
- Rejection modeli
- Exchange-specific order kuralları

## 16. Position Transitions (MUST — Exact Table)

Policy'nin target `PositionState`'i değiştiğinde, engine gerekli quantity delta'yı **tek bir fill'de** (Bölüm 10'daki next-open fiyatında) gerçekleştirir. `position_quantity = q` için:

| Transition | Fill |
|---|---|
| FLAT → LONG | buy `q` |
| LONG → FLAT | sell `q` |
| FLAT → SHORT | sell `q` |
| SHORT → FLAT | buy `q` |
| LONG → SHORT | sell `2q` |
| SHORT → LONG | buy `2q` |
| target değişmiyor (no-op) | **fill YOK** |

Bu tablo, deterministic target-position semantics'in **tam ve bağlayıcı** tanımıdır — implementasyon başka bir quantity-delta hesaplama yolu icat etmez.

## 17. Accounting Model (MUST — Formulas)

Faz 4 minimum accounting state'i:

```
initial_cash          : Decimal
cash                   : Decimal   (başlangıç = initial_cash)
position_quantity      : Decimal   (signed, başlangıç = 0)
entry_price            : Decimal | None   (açık pozisyonun giriş fiyatı; flat iken None)
fills                  : list[Fill]        (append-only fill log)
transitions            : list[Transition]  (append-only trade/position-transition log)
realized_pnl           : Decimal   (kümülatif, başlangıç = 0)
equity_curve           : list[(datetime, Decimal)]  (candle-by-candle equity)
```

Tüm parasal alanlar **`Decimal`** olmalıdır. **Float ile para/accounting source-of-truth oluşturmak YASAK.**

**Fill sırasında cash hareketi:**

```
buy quantity q at price p:
    cash -= q * p

sell quantity q at price p:
    cash += q * p
```

**Equity (her candle'da, yalnızca available mark price ile — bkz. Bölüm 18):**

```
equity = cash + position_quantity * mark_price
```

**Tutarlılık invariant'ı (executable test'e dönüştürülebilir):**

```
equity - initial_cash == realized_pnl + unrealized_pnl - total_cost
```

(`total_cost`, Bölüm 22'deki kümülatif fill cost'udur.)

Signed-position representation'ı dışında eşdeğer bir muhasebe temsili kullanılırsa, formül **tek ve tutarlı biçimde** tanımlanmalı, iki farklı temsil aynı spec içinde karışık kullanılmamalıdır.

## 18. Mark Price (MUST)

Equity curve candle-by-candle hesaplanırken **yalnızca available candle fiyatı** kullanılır.

**MVP mark price:** available candle'ın **CLOSE**'u.

Bu close, ancak ilgili candle `availability_time`'ına ulaştığında (Bölüm 8) kullanılabilir. **Partial candle close kullanımı YOK.**

## 19. Realized / Unrealized PnL (MUST)

**Unrealized PnL (açık pozisyon için, tek formül — hem long hem short için geçerli):**

```
unrealized_pnl = (mark_price - entry_price) * position_quantity
```

(`position_quantity` short için negatif olduğundan, bu tek formül her iki yön için de doğru işareti üretir.)

**Realized PnL (pozisyon kapatıldığında/azaltıldığında):**

```
LONG kapanışı:  realized_pnl += (exit_price - entry_price) * closed_quantity
SHORT kapanışı: realized_pnl += (entry_price - exit_price) * closed_quantity
```

(`closed_quantity` her zaman pozitiftir — kapatılan miktarın mutlak değeri.)

Faz 4 policy modeli (Bölüm 13) sabit `position_quantity` hedefine dayandığından, **pyramiding (aynı yönde artan pozisyon) beklenmez** — bir pozisyon açıldığında tek bir `entry_price` vardır ve pozisyon tamamen kapanana kadar değişmez.

**Reversal semantiği (LONG → SHORT veya SHORT → LONG, Bölüm 16'daki `2q` fill):**

Reversal, **tek bir quantity-delta fill** (aynı next-open fiyatında) olarak gerçekleşse de, muhasebe açısından iki ayrı adım olarak ele alınır:

```
1) Mevcut pozisyon TAMAMEN kapatılır (closed_quantity = q, exit_price = next_open):
   realized_pnl güncellenir (yukarıdaki formüllerle).
2) Karşıt yönde YENİ bir pozisyon açılır:
   entry_price = next_open, position_quantity = ∓q (yeni yön).
```

Trade log (`transitions`), bu iki adımı (close + open) **açıkça ayrı işlemler olarak** kaydedebilir; fiziksel fill tablosu (`fills`) Bölüm 16'daki tek `2q` fill'i yansıtır. İki log'un semantiği spec'te açıkça ayrılmıştır — birbirinin yerine geçmez.

## 20. Capital / Margin Boundary (MUST)

Faz 4 accounting simulator'ı **uygulamaz:**

- Margin requirements
- Borrow fees
- Liquidation
- Maintenance margin
- Max leverage
- Position limits

**Locked behavior:** Faz 4 engine, accounting doğruluğu için bir fill'i sermaye/risk yetersizliği nedeniyle **reddetmez.** Bu nedenle `cash` **geçici veya kalıcı olarak negatif olabilir.**

Bu davranış, borrowing/leverage modelinin **gerçekçi olduğu anlamına gelmez** — yalnızca Faz 4'ün accounting'i basitleştirmesinin bir sonucudur. Capital/risk constraints, sonraki Risk Engine (Faz 8) ve Execution fazlarının konusudur.

## 21. Cost Model Boundary (MUST — KRİTİK)

ARCHITECTURE.md, Backtest Layer'ı "maliyetler dahil test edildiği" katman olarak tanımlarken, ROADMAP.md gerçekçi maliyet modellemesini ayrı bir Faz 5'e ("Realistic cost model") ayırır. Bu sınır şöyle kilitlenir:

- Faz 4 engine, **cost-aware bir mimariye** sahip olur: minimal bir `CostModel` abstraction/protocol tanımlanır ve her fill bu abstraction üzerinden bir maliyet hesabından geçer.
- Faz 4'te **yalnızca** bir `CostModel` implementasyonu zorunludur: **`ZeroCostModel`.**

```
CostModel:
    calculate_cost(quantity: Decimal, price: Decimal, side: BuySell) -> Decimal

ZeroCostModel.calculate_cost(...) -> Decimal("0")   (her zaman)
```

Accounting, cost alanını ve cost-deduction hook'unu (Bölüm 22) **her zaman** kullanır — `ZeroCostModel` ile bile. Böylece engine, hangi `CostModel`'in enjekte edildiğinden **bağımsız** kalır.

**Faz 5'in işi:** commission, spread, slippage, funding ve diğer gerçekçi maliyetler. **Faz 4'te bunların basitleştirilmiş sahte versiyonları (örn. sabit bps komisyon) yazılmaz** — yalnızca `ZeroCostModel`.

## 22. Cost Accounting Contract (MUST)

Her fill'in maliyeti, buy/sell yönünden **bağımsız olarak** cash'ten düşülür:

```
cash_after_execution -= fill_cost
```

`ZeroCostModel` ile `fill_cost == Decimal("0")` olduğundan, bu formül Faz 4'te pratikte cash'i değiştirmez — ama hook her zaman çağrılır, böylece Faz 5'in gerçek `CostModel` implementasyonları hiçbir engine değişikliği gerektirmeden takılabilir.

Backtest result, `total_cost` alanını (tüm fill cost'larının kümülatif toplamı) taşıyabilir (bkz. Bölüm 26).

## 23. Risk Engine Boundary (MUST)

Faz 8 Risk Engine henüz **mevcut değildir.**

Faz 4 backtest core'u:

- `RiskEngine` **import etmez.**
- Placeholder `RiskEngine` **yazmaz.**
- Fake/stub risk gate **icat etmez.**

Policy'nin ürettiği target `PositionState`'ler, Faz 4 simulation core'una **doğrudan** gider (Bölüm 12, Bölüm 13). Faz 8 geldiğinde, risk-aware simulation entegrasyonu **ayrı bir contract ile** eklenir — bu spec, mimariyi bunu gelecekte imkânsız kılacak şekilde sıkı bağlamaz (örn. policy→target dönüşümü, gelecekte bir risk-gate adımının araya girebileceği net bir sınırda durur).

## 24. Decision / Live Execution Boundary (MUST)

Backtest **canlı exchange execution'ı değildir.**

- API key **YOK.**
- Network **YOK.**
- Exchange order **YOK.**
- Production execution adapter'ları kullanılmaz.

Faz 4'ün `Fill` domain modeli, gelecekteki live exchange fill modeliyle **prematurely birleştirilmez** — ikisi ayrı, bağımsız evrilebilen kavramlardır.

## 25. Strategy Layer Boundary (MUST)

Faz 4'ün policy abstraction'ı (Bölüm 12), gelecekteki Strategy & Model layer'ının (ARCHITECTURE.md Katman 5, ROADMAP.md Faz 11+) **nihai public contract'ı olarak garanti edilmez.**

Bu spec açıkça şunu tanımlar: **backtest policy, engine-side pluggable bir test/decision abstraction'ıdır** — Faz 11+ geldiğinde gerçek strateji katmanı, gerekirse bir adapter ile bu abstraction'a bağlanabilir; bu abstraction'ın kendisi Faz 11+'ın nihai tasarımını **önceden dondurmaz.**

## 26. Validation Boundary (MUST)

Faz 4 **yapmaz:**

- Walk-forward
- Train/test split
- Out-of-sample framework
- Purged CV / embargo / CPCV
- Deflated Sharpe
- PBO (Probability of Backtest Overfitting)
- Multiple-testing correction
- Parameter search

Bunların tamamı Faz 6 — Validation / Anti-overfitting'in kapsamındadır. **Faz 4 yalnızca deterministic, single-run historical simulator'dır.**

## 27. Performance Metrics — MVP

Faz 4 backtest result'ı, en az şu alanları taşıyabilir:

```
initial_cash
final_cash
final_equity
total_realized_pnl
total_unrealized_pnl
total_pnl
total_cost
fill_count
trade_count
equity_curve
```

**Faz 4'te zorunlu değil** (ve ROADMAP'teki sonraki validation/analytics fazlarıyla (Faz 6) çakıştığı için kasıtlı olarak ertelenir):

- Sharpe / Sortino / Calmar oranları
- Max drawdown analytics
- İstatistiksel anlamlılık testleri

Minimal correctness metrikleri (yukarıdaki liste) Faz 4 için **yeterlidir.**

## 28. Result Determinism (MUST)

Aynı:

```
dataset
as_of_time
config
policy davranışı
cost model
```

her zaman **aynı**:

```
fills
transitions
cash
positions
realized/unrealized PnL
equity curve
result
```

üretmelidir.

**Iteration ordering explicit olmalıdır.** Hidden/unordered collection semantiğine (örn. `set` iterasyon sırası, hash-tabanlı sıralama) dayanan hiçbir kritik yol **yasaktır** — DATA_QUALITY_SPEC.md'nin sample-sıralama disiplinine (Bölüm 13.1) paralel bir prensip.

## 29. Datetime / Numeric Rules (MUST)

Mevcut repository contract'ları **yeniden kullanılır**, yeni bir timestamp/numeric primitive yazılmaz:

**Datetime:**
- True timezone-aware.
- UTC instant semantics (non-UTC-aware equivalent instant'lar doğru kabul edilir, `datetime_to_epoch_us` üzerinden normalize edilir).
- Gerekli yerlerde integer epoch conversion (`datetime_to_epoch_us`/`epoch_us_to_datetime`) — float/`.timestamp()`/`round()` yasak.
- Naive/pseudo-naive → `ValueError`.

**Money / price / quantity / PnL:**
- `Decimal`.
- Float-tabanlı finansal muhasebe **YASAK.**

## 30. Input Order (MUST)

Backtest engine'e verilen candle sequence'i:

- **Strictly ascending**
- **Unique** (open_time bazında)
- **Canonical timeframe'e (Bölüm 4) tutarlı**

olmalıdır.

**Kesinlikle YASAK:**
- Silent sort
- Silent dedupe
- Silent repair

İhlal → **açık failure** (`ValueError` veya eşdeğer domain exception).

Quality gate (Bölüm 6), store-backed path'te bu invariant'ları büyük ölçüde zaten korur (PASS olan bir `DataQualityReport`, tanım gereği eksiksiz ve hizalı bir dataset anlamına gelir). Ancak core engine, mevcut projenin diğer tüm katmanlarında (`finalization.py`, `pagination.py`) kurulan **"upstream'e körü körüne güvenme"** prensibiyle tutarlı olarak, bu invariant'ları **kendi sınırında da açıkça tanımlar ve doğrular** — quality gate'in varlığına bel bağlayarak core engine'in kendi validasyonunu atlamaz.

## 31. Symbol / Timeframe Consistency (MUST)

Tek bir backtest run'ı:

- Tek `exchange`
- Tek `market_type`
- Tek `symbol`
- Tek `timeframe`

dataset'i üzerinde çalışır.

Mixed symbol/timeframe input → **açık failure.**

Desteklenen timeframe (Bölüm 4): yalnızca `1h`, `4h`.

## 32. Empty Data (MUST)

Empty usable dataset (hiç candle yok veya quality gate sonrası kullanılabilir hiçbir finalized veri kalmıyor), backtest **SUCCESS sayılmaz.**

**Açık `ValueError`/domain validation failure** üretilir. Synthetic "no-trade result" **üretilmez** — bu, Bölüm 33'teki (geçerli, veri-dolu ama policy'nin hep FLAT kaldığı) "NO TRADE" durumundan **kavramsal olarak farklıdır.**

## 33. No Trade (MUST — First-Class Valid Outcome)

Policy, run boyunca **tamamen FLAT** dönebilir. Bu **geçerli bir backtest sonucudur** (Bölüm 32'deki empty-data hatasıyla karıştırılmaz — burada veri mevcuttur, yalnızca hiç pozisyon açılmamıştır).

Beklenen sonuç:

```
fill_count == 0
trade_count == 0
final position == flat (quantity == 0)
total_realized_pnl == 0
total_unrealized_pnl == 0
total_cost == 0
final_equity == initial_cash
```

NO TRADE, engine tarafından **first-class geçerli bir outcome olarak** ele alınır — özel bir hata veya uyarı değildir.

## 34. Kapsam Dışı (Explicit Out-of-Scope / Deferred)

Şunların hiçbiri Faz 4'ün kapsamında değildir:

- Gerçekçi commission / spread / slippage / funding maliyetleri (→ Faz 5)
- Latency modeli (→ Faz 5 veya sonrası)
- Partial fill / order book simulation / limit / stop orders (→ ileride, tanımsız faz)
- Margin / liquidation / leverage kuralları (→ ileride, tanımsız faz)
- Portfolio optimization / multi-asset portfolio simulation (→ ileride, tanımsız faz)
- Dynamic/volatility-based position sizing (→ Risk Engine, Faz 8 veya sonrası)
- Risk Engine (→ Faz 8)
- Paper trading (→ Faz 9)
- Canlı trading (→ Faz 18)
- Strateji kütüphanesi / indicator implementasyonları (→ Faz 10-13)
- ML modelleri (→ Faz 14)
- Walk-forward / out-of-sample / cross-validation (→ Faz 6)
- Anti-overfitting analytics (Deflated Sharpe, PBO, vb.) (→ Faz 6)

## 35. Implementasyon Mikro-Adım Sırası (Bağlayıcı Bağımlılık Sırası)

1. `BACKTEST_SPEC.md` contract (bu doküman)
2. Core enum/config/result primitives (pure)
3. Cost model abstraction + `ZeroCostModel` (pure)
4. Dataset preparation + quality gate entegrasyonu (I/O, mevcut `build_data_quality_report_from_store` üzerinden)
5. Policy/context abstraction (pure)
6. Position/accounting primitives (pure)
7. Next-open fill transition engine (pure)
8. Equity/PnL result assembly (pure)
9. Deterministic replay + anti-lookahead testleri (pure/offline)
10. Real `HistoricalCandleStore` entegrasyonu (I/O)
11. Uçtan uca backtest regresyon testleri (I/O, integration)
12. Faz 4 acceptance audit/checkpoint

Her mikro-adım küçük, bağımsız test edilebilir ve ayrı audit/commit yapılabilir olmalıdır (Faz 2/3'te kurulan çalışma deseniyle tutarlı).

## 36. Faz 4 Kabul Kriterleri (Acceptance Criteria)

1. Deterministic replay: aynı input/config/policy/cost model → bit-for-bit/value-for-value aynı sonuç.
2. Quality gate `FAIL` (veya `PASS` olmayan) bir dataset → backtest reddedilir.
3. Empty dataset → reddedilir.
4. Mixed symbol/timeframe input → reddedilir.
5. Unsorted (non-ascending) input → reddedilir.
6. Duplicate timestamp input → reddedilir.
7. `1h`/`4h` desteklenir; başka timeframe reddedilir.
8. Feature availability boundary (`open_time + duration`) her koşulda korunur.
9. Signal, hiçbir koşulda henüz available olmamış (future) bir candle'ı kullanamaz.
10. N. candle'dan üretilen signal, yalnızca N+1. candle'ın OPEN'ında fill olur.
11. Dataset'in son candle'ında üretilen signal, fabricated bir fill yaratmaz (Bölüm 11).
12. FLAT → LONG transition doğru fill üretir.
13. LONG → FLAT transition doğru fill üretir.
14. FLAT → SHORT transition doğru fill üretir.
15. SHORT → FLAT transition doğru fill üretir.
16. LONG → SHORT reversal doğru (`2q`) fill + doğru realized PnL üretir.
17. SHORT → LONG reversal doğru (`2q`) fill + doğru realized PnL üretir.
18. Aynı target'ın tekrarı (no-op) → ekstra fill üretmez.
19. Full-fill semantics (Bölüm 15) korunur — partial fill yok.
20. Decimal accounting: tüm parasal hesaplamalar `Decimal`, float sızıntısı yok.
21. Cash hareketi (Bölüm 17) exact.
22. Realized PnL — long kapanışı exact (Bölüm 19 formülü).
23. Realized PnL — short kapanışı exact (Bölüm 19 formülü).
24. Unrealized PnL exact (Bölüm 19 formülü).
25. Equity formülü (`cash + position_quantity * mark_price`) exact.
26. Equity curve deterministic ve candle-by-candle doğru.
27. `ZeroCostModel` ile `total_cost == Decimal("0")`.
28. Cost hook her fill'de çağrılır ve cash/result'a doğru şekilde uygulanır (Bölüm 22).
29. NO TRADE run (Bölüm 33) geçerli bir sonuç olarak doğru şekilde temsil edilir.
30. Non-UTC-aware equivalent instant semantics doğru çalışır.
31. Naive/pseudo-naive datetime, public API'nin datetime kabul ettiği her yerde reddedilir.
32. Tüm yeni testler tamamen offline (`tmp_path` tabanlı gerçek store veya pure fixture, gerçek network çağrısı yok).
33. Core engine içinde hidden wall-clock yok.
34. Mevcut 433 test regresyonsuz PASS kalır.
35. `ruff check` / `ruff format --check` temiz kalır.

## 37. Faz 4 Invariants — Checklist

- [ ] Core execution: deterministic, bar-by-bar, event-driven; vectorized engine yok
- [ ] MVP: single-symbol, yalnızca `1h`/`4h`
- [ ] Veri kaynağı: yalnızca `HistoricalCandleStore.query()`, salt-okunur, gerçek network yok
- [ ] Quality gate: `overall_status != "PASS"` → backtest başlamaz, silent repair yok
- [ ] Yalnızca finalized/canonical veri; partial candle yok
- [ ] Anti-lookahead: `availability_time = open_time + duration`, OPEN dahil tüm OHLCV, sınır dahil (`>=`)
- [ ] Signal timing: yalnızca available prefix, future candle yok, current partial candle yok
- [ ] Execution timing: signal → next-candle OPEN fill; same-bar retroactive fill yok
- [ ] Last-candle rule: fabricated fill yok, future price invent edilmez
- [ ] Policy: minimal `LONG`/`FLAT`/`SHORT` pluggable abstraction; somut strateji yok
- [ ] Position size: sabit `position_quantity`; dynamic/Kelly/portfolio sizing yok
- [ ] Position model: tek net signed position; hedge-mode yok
- [ ] Order/fill: yalnızca market-at-next-open + full fill
- [ ] Position transitions: Bölüm 16 tablosu birebir uygulanır
- [ ] Accounting: `Decimal`, cash/position/fill/PnL/equity formülleri Bölüm 17 ile birebir
- [ ] Mark price: yalnızca available candle close
- [ ] Realized/unrealized PnL: Bölüm 19 formülleri, reversal close+open olarak ele alınır
- [ ] Capital boundary: margin/liquidation/leverage yok; cash negatif olabilir
- [ ] Cost boundary: cost-aware mimari, yalnızca `ZeroCostModel` zorunlu, gerçekçi maliyet Faz 5'te
- [ ] Cost accounting: her fill cash'ten cost düşer, yön bağımsız
- [ ] Risk Engine: import/placeholder/fake gate yok
- [ ] Live execution: API key/network/exchange order yok
- [ ] Strategy layer: policy, Faz 11+'ın nihai contract'ı olarak garanti edilmez
- [ ] Validation: walk-forward/CV/anti-overfitting analytics yok
- [ ] Performance metrics: yalnızca Bölüm 27'deki minimal set zorunlu
- [ ] Determinism: aynı input → aynı output, ordering explicit
- [ ] Datetime/numeric: mevcut repo contract'ları yeniden kullanılır, float finansal muhasebe yok
- [ ] Input order: strictly ascending + unique + timeframe-tutarlı, silent repair yok
- [ ] Symbol/timeframe: tek run içinde tek exchange/market_type/symbol/timeframe
- [ ] Empty data: açık hata, sessiz no-op/synthetic sonuç yok
- [ ] NO TRADE: first-class geçerli sonuç
