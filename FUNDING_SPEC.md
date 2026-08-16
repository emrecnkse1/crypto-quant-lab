# FUNDING_SPEC

Bu doküman, Faz 5B — Funding katmanının **economic / accounting / timing** kontratını kilitler. Bu bir tasarım dokümanıdır; kod, bağımlılık veya somut implementasyon içermez.

**Bu dokümanın kapsamadığı şey:** tam funding data/storage implementasyon kontratı. Canonical storage schema, SQLite layout, parser, pagination, ingestion ve schedule/coverage quality kontratı ayrı bir gelecek dokümanda — `FUNDING_DATA_SPEC.md` — kilitlenecektir (bkz. Bölüm 17).

## 1. Amaç

Faz 5A, transaction-cost (commission, spread, slippage, composite) katmanını fill-tetiklemeli, monetary-cost bir `CostModel` mimarisi üzerinden kilitledi (COST_MODEL_SPEC.md). Bu doküman, Faz 5B'nin **funding** yarısını bağlayıcı hale getirir: funding, fill'e değil **zaman içinde taşınan bir pozisyona** bağlı, event-tabanlı bir ekonomik etkidir ve mevcut `CostModel` Protocol'ü ile yapısal olarak temsil edilemez (bkz. Bölüm 3).

Bu doküman şunları kilitler:

- Funding'in transaction cost'tan yapısal ayrımı
- Signed economic sign convention (formül + örnekler)
- Reference/settlement price prensibi
- Settled-historical-only semantics ve anti-lookahead prensibi
- Accounting entegrasyon prensibi (`cash`, `total_cost`, `BacktestResult` uyumluluğu)
- Timing/replay mimari yönü (tie-ordering dahil, açıkça deferred olarak işaretlenmiş)
- Backward-compatibility sınırları
- Binance USDⓈ-M source-adapter research bulguları (yalnızca adapter'ı bilgilendirmek için, exchange-neutral core'a bake edilmeden)

## 2. Binding Foundation — Diğer Spec'lerle İlişki

Bu doküman şu mevcut kontratları **binding foundation** olarak referans alır ve **hiçbirini değiştirmez**:

- `CostModel` Protocol ve Cost Model Boundary (BACKTEST_SPEC.md Bölüm 21, 22; COST_MODEL_SPEC.md Bölüm 4)
- `CompositeCostModel` (COST_MODEL_SPEC.md Bölüm 16-19)
- Next-open execution timing (BACKTEST_SPEC.md Bölüm 10)
- Decimal-only accounting (BACKTEST_SPEC.md Bölüm 17, 29)
- Determinism garantisi (BACKTEST_SPEC.md Bölüm 3, 28)
- Anti-lookahead invariant (BACKTEST_SPEC.md Bölüm 8; DATA_QUALITY_SPEC.md Bölüm 10)
- `market_type` opak partition-key contract (HISTORICAL_DATA_SPEC.md; `HistoricalCandle.market_type: str`)
- COST_MODEL_SPEC.md Bölüm 26, 27: funding'in neden mevcut `CostModel` için yetersiz olduğunun ilk dokümantasyonu

**Çelişki politikası:** Bu dokümanın herhangi bir maddesi yukarıdaki spec'lerle çelişiyor görünüyorsa, önceki spec'ler **sessizce değiştirilmez** — çelişki açıkça raporlanır ve çözülene kadar bu dokümanın ilgili maddesi askıda kalır.

## 3. Funding vs Transaction Cost (MUST — KRİTİK)

Commission/spread/slippage: **FILL-tetiklemeli.** Her biri, `CostModel.calculate_cost(*, quantity, execution_price) -> Decimal` üzerinden, yalnızca fiziksel bir fill gerçekleştiğinde bir kez hesaplanır (COST_MODEL_SPEC.md Bölüm 4).

Funding: **ZAMAN + TAŞINAN POZİSYON tetiklemeli.** Bir funding settlement event'i, o anda **tutulan** (held) pozisyona uygulanır — hiçbir fill gerekmez.

**Zorunlu kural:**

- Funding, `CompositeCostModel` içine **konulmaz.** `CompositeCostModel`'in `components: tuple[CostModel, ...]` alanı yalnızca fill-tetiklemeli `CostModel` implementasyonları içindir; funding'in signature'ı (signed position + reference price + rate, quantity/execution_price değil) yapısal olarak uyumsuzdur.
- Funding **yalnızca trade gerçekleştiğinde** hesaplanamaz. Policy hiçbir yeni sinyal üretmese bile, tutulan pozisyon üzerinden funding tahakkuku **devam eder** (bkz. Bölüm 10).
- Mevcut `CostModel` ve `CompositeCostModel` **değişmeden** kalır.

## 4. Signed Economic Convention (LOCKED)

### 4.1 Signed Position (Değişmez, BACKTEST_SPEC.md Bölüm 14)

```
LONG:   position_quantity > 0
FLAT:   position_quantity == 0
SHORT:  position_quantity < 0
```

Faz 5B, yeni bir position representation **icat etmez.**

### 4.2 Funding Cost Sign Convention

Kanonik accounting convention:

```
positive funding_cost  =  cash outflow / account pays
negative funding_cost  =  cash inflow  / account receives
```

Bu, mevcut `fill_cost` convention'ıyla (BACKTEST_SPEC.md Bölüm 22: `cash_after_execution -= fill_cost`; COST_MODEL_SPEC.md Bölüm 18'in negatif generic cost = rebate precedent'i) **birebir tutarlıdır.**

### 4.3 Formula (LOCKED — Linear USDⓈ-Margined Perpetual)

```
funding_cost =
signed_position_quantity
* reference_price
* funding_rate
```

**NO leading minus.** Standard perpetual-swap market mekaniği (Binance/Bybit/OKX/dYdX gibi büyük perpetual borsalarının tamamında ortak, exchange-specific bir detay değil): pozitif funding rate'te **long, short'a öder**; negatif funding rate'te **short, long'a öder.**

**Doğrulanmış örnekler:**

```
LONG +2, price 100, rate +0.001
  → 2 * 100 * 0.001 = +0.20
  → pozitif = outflow = LONG PAYS

SHORT -2, price 100, rate +0.001
  → -2 * 100 * 0.001 = -0.20
  → negatif = inflow = SHORT RECEIVES

LONG +2, price 100, rate -0.001
  → 2 * 100 * (-0.001) = -0.20
  → negatif = inflow = LONG RECEIVES

SHORT -2, price 100, rate -0.001
  → -2 * 100 * (-0.001) = +0.20
  → pozitif = outflow = SHORT PAYS
```

Her dört örnek de bağımsız olarak doğrulanmıştır ve standard perpetual funding convention'ı ile birebir tutarlıdır. **Leading-minus'lu bir varyant** (`funding_cost = -1 * signed_position_quantity * reference_price * funding_rate`) yukarıdaki dört işareti de tersine çevirir — örn. pozitif rate'te LONG'un ödeme yerine tahsilat alması gibi standard convention'a aykırı bir sonuç üretir. Bu bir sign-inversion bug'ıdır ve **kullanılmayacaktır.**

**Rate legality:**

```
zero rate:      legal
negative rate:  legal
positive rate:  legal
```

### 4.4 Terminoloji

Signed accounting miktarı için `funding_cost` ismi kullanılır. `fill_cost` ile **karıştırılmaz** — ikisi farklı tetikleme mekanizmasına sahiptir (Bölüm 3), ama aynı sign convention'ı (positive=pays/negative=receives) paylaşırlar; bu nedenle isimlendirme ailesi (`_cost`) bilinçli olarak tutarlı tutulur. `FundingModel`, `CostModel`'den **yapısal olarak ayrı** kalır (Bölüm 6).

## 5. Reference / Settlement Price Prensibi (LOCKED)

Funding, ait olduğu tarihsel funding event'in **gerçek settlement/reference fiyatını** kullanmalıdır.

**Kesinlikle YASAK — sessiz ikame:**

- Normal OHLCV candle CLOSE
- Normal OHLCV candle OPEN
- Next-open execution price

Bu üç fiyat kaynağının hiçbiri funding settlement fiyatının yerine **sessizce** geçirilemez.

### 5.1 Binance USDⓈ-M Source-Adapter Research Finding — markPrice

**SOURCE-ADAPTER RESEARCH FACT (exchange-neutral bir yasa değil):**

Binance USDⓈ-M `GET /fapi/v1/fundingRate` endpoint'i şu anda döner:

```
symbol
fundingRate
fundingTime
markPrice
rateType
```

Resmi dokümantasyon `markPrice`'ı "mark price associated with the particular funding fee charge" olarak tanımlar.

**Adapter yönü:** Binance historical funding kaydının döndürdüğü `markPrice`, funding `reference_price`'ı olarak kullanılacaktır. Bu, Binance için **ayrı bir mark-price ingestion pipeline'ı gerektirmez** — funding-history kaydının kendisi bu tam `markPrice`'ı zaten sağlar.

**Core `FundingModel` exchange-neutral kalır** — bu bulgu yalnızca Binance adapter'ının reference_price'ı nereden alacağını bilgilendirir; core calculator/domain hiçbir Binance-specific alan/isim bilmez.

## 6. Rate / Reference Validation Prensipleri

### 6.1 Funding Rate Validation

```
genuine Decimal    : required
finite             : required
positive           : legal
zero               : legal
negative           : legal
```

Reddedilir:

```
float / int / bool           → TypeError
Decimal("NaN")                → ValueError
Decimal("Infinity")           → ValueError
Decimal("-Infinity")          → ValueError
```

Hiçbir hidden default `funding_rate` yoktur.

### 6.2 Reference Price Validation Prensibi

Kanonik funding `reference_price`:

```
genuine Decimal   : required
finite            : required
> 0                : required
```

Bu, generic `CostModel` boundary'sinin permissive `execution_price` semantics'inden (COST_MODEL_SPEC.md Bölüm 13: zero/negative legal) **kasıtlı olarak farklıdır** — çünkü `reference_price`, gerçek tarihsel market settlement verisidir, sentetik/permissive bir hesaplama girdisi değil. Bu, mevcut `Candle` fiyat validasyon prensipleriyle (pozitif finite fiyat beklentisi) daha tutarlıdır.

**Exact event dataclass validasyonu** (`__post_init__` sırası, tam hata mesajları) `FUNDING_DATA_SPEC.md`'ye veya implementasyon mikro-adımına **ertelenir.**

## 7. Settled Historical Event Semantics (MUST — KRİTİK)

Backtest için kullanılan funding, **SETTLED (tahakkuk etmiş) bir tarihsel event'i** temsil etmelidir.

**Kesinlikle YASAK:**

- Predicted next funding rate
- Estimated upcoming rate
- Current live "next funding" değeri

bunların hiçbiri settled historical fact olarak kullanılamaz.

`as_of_time`'ın ötesindeki hiçbir event, replay'i etkileyemez (bkz. Bölüm 11).

### 7.1 Binance rateType Discovery — ÖNEMLİ

**SOURCE-ADAPTER RESEARCH FACT:**

Aynı Binance endpoint şu anda `rateType` alanı da döner:

```
Regular
Special
```

burada `Special`, stock dividend'lerden kaynaklanan ek bir funding rate'i temsil eder.

**Sonuç:** gelecekteki `FUNDING_DATA_SPEC.md`, aynı `symbol`/`event_time` için **maksimum bir funding satırı** varsayımını **körü körüne kilitleyemez** — exact multi-rate/event identity semantics kilitlenene kadar bu varsayım açık kalmalıdır. `rateType` **sessizce discard edilmez** — kaydedilecek bilgi olarak işaretlenir.

**Exact canonical representation/key bu dokümanda kilitlenmez** — yalnızca şu prensip kilitlenir: event identity, ekonomik olarak ayrı settlement charge'larını (örn. Regular vs Special aynı anda) **korumalıdır**, birbirini sessizce ezmemelidir. Exact key/schema kararı `FUNDING_DATA_SPEC.md`'ye ertelenir (bkz. Bölüm 17).

## 8. Accounting (MUST — Formüller)

Funding **yalnızca cash/cost accounting'i** etkiler.

```
new_cash = old_cash - funding_cost
```

`funding_cost` negatifse, cash **artar** (credit/rebate).

**Funding kesinlikle DEĞİŞTİRMEZ:**

```
position_quantity
average_entry_price
realized_pnl
fill_count
trade_count
```

### 8.1 Accounting Primitive Yönü (Implementasyon Deferred)

Gelecekte, signed bir direct-cash-cost application primitive'i gereklidir. Aday isimlendirme:

```
apply_cash_cost(state, *, cost: Decimal) -> AccountState
```

veya funding-specific bir eşdeğeri. **Exact isim bu dokümanda kilitlenmez** — gerekli değilse kilitlenmemelidir.

**Binding behavior:**

```
cash -= cost
```

`position_quantity`, `average_entry_price`, `realized_pnl` korunur (unchanged). Exact implementasyon (`AccountState` üzerinde yeni bir fonksiyon mu, mevcut bir primitive'in genişletilmesi mi) implementasyon mikro-adımına ertelenir.

### 8.2 Position-at-Event Semantics

Funding, settlement `event_time`'ında **fiilen tutulan** pozisyona uygulanır.

```
FLAT konum:        funding_cost = Decimal(0)   (formülün doğal sonucu — özel bir dal gerekmez)
LONG/SHORT konum:   signed position formula (Bölüm 4.3)
```

Funding **hiçbir fill gerektirmez.** Policy yeni sinyal üretmese bile, tutulan pozisyon üzerinden funding **devam eder** — funding, sonraki trading aktivitesinden **bağımsızdır.**

### 8.3 position_opened_at — Gerekli DEĞİL

`AccountState`'e `position_opened_at` **eklenmez.** Funding, holding-duration'a göre time-weighted bir faiz değil, event_time'daki pozisyona karşı **discrete bir settlement**'tır. Event'ler kronolojik olarak işlendiğinde mevcut position state yeterlidir — yeni state alanı gerekmez.

## 9. total_cost / BacktestResult Compatibility (LOCKED)

Mevcut `BacktestResult` **değişmeden** kalır.

`total_cost`, transaction cost'lara ek olarak funding cost'ları da içerecektir:

```
total_cost = transaction costs + funding costs
```

Funding terimi **negatif olabilir.**

Mevcut invariant korunur:

```
total_pnl = realized_pnl + unrealized_pnl - total_cost
```

**Faz 5B correctness için yeni bir `BacktestResult` alanı gerekmez.**

### 9.1 Cost Breakdown — Yine Deferred

Exact production `CostBreakdown` API'si **deferred** kalır (COST_MODEL_SPEC.md Bölüm 25 ile tutarlı). Faz 5B correctness'i `BacktestResult`'ı genişletmeyi **gerektirmez.** Research observability ileride transaction cost'u funding cost'tan ayrı olarak expose edebilir, ama bu API burada tasarlanmaz.

## 10. Timing / Multiple Events (MUST)

Replay mimarisi:

```
zero
one
multiple
```

funding event'ini herhangi iki replay boundary'si arasında işleyebilmelidir. **Maksimum bir funding event per candle** varsayımı asla yapılamaz — funding interval'i venue/instrument-specific'tir ve seçilen candle timeframe'inden kısa olabilir (bkz. Bölüm 16).

## 11. Anti-Lookahead / Event Availability (MUST — KRİTİK)

Bir funding settlement event'i, accounting'i yalnızca kendi explicit `event_time`'ında etkiler.

```
event_time > as_of_time  →  replay bu event'i UYGULAYAMAZ
```

**Hiçbir wallclock okuması yok.** Deterministic replay içinde `datetime.now()` **yasak.** Tüm timing explicit olarak inject edilir — bu, mevcut candle anti-lookahead invariant'ının (BACKTEST_SPEC.md Bölüm 8) funding'e doğal uzantısıdır.

## 12. Tie Timestamp — LOCKED (FUNDING-SPEC MS9/MS10)

Eğer:

```
funding event_time == bir sinyal-tetiklemeli fill'in execution timestamp'i
```

ise, exact timestamp T'de şu sıra bağlayıcıdır:

```
1. funding settlement(s)
2. equity mark
3. policy evaluation
4. fill
```

Funding settlement(s), **PRE-EXISTING / PRE-FILL** `AccountState.position_quantity`'ye karşı settle olur — bu instant'taki hiçbir fill henüz gerçekleşmemiş kabul edilir. Gerekçe: scheduled settlement, az önce biten interval'ı kapatır; tam olarak aynı boundary'de gerçekleşen bir trade, kavramsal olarak bir SONRAKİ interval'a aittir.

Aynı T'de birden fazla funding event'i paylaşılırsa:

```
- her biri ayrı ayrı işlenir (asla pre-summed değil)
- deterministic (event_time, rate_type) sırasıyla
- tümü aynı pre-fill pozisyonu gözlemler
- funding cash etkisi position_quantity'yi asla değiştirmez
```

Bu, gerçek Binance millisecond-level order-book/settlement sıralaması olduğu iddiasında **değildir** — bu deterministic bir backtest convention'ıdır. Implementasyon: `backtest/replay.py` (`_apply_due_funding_events`, FUNDING-SPEC MS10) ve `backtest/store_runner.py` (FUNDING-SPEC MS11); regression: `tests/test_backtest_funding_replay.py`, `tests/test_backtest_funding_store_runner.py`, `tests/test_backtest_funding_e2e_regression.py` (FUNDING-SPEC MS12 canonical E2E golden + determinism).

## 13. Equity Curve (LOCKED — FUNDING-SPEC MS9/MS10)

Funding'in neden olduğu cash hareketi, sonraki equity accounting'de **görünür olmalıdır.** Funding, replay'den sonra **invisible bir post-processing adımı olarak uygulanamaz** — bu, equity curve'ün her sonraki noktasını ve final result'ı bozar (Bölüm 14).

Exact locked davranış (Bölüm 12'nin tie-order kontratının doğal sonucu):

```
- T'de due olan funding, o T'deki equity mark'tan ÖNCE uygulanır
- marklar arasında (between-bar) due olan funding, cash'i kronolojik olarak değiştirir
  ve bir sonraki normal candle-tetiklemeli EquityPoint'te görünür
- funding hiçbir zaman kendi başına yeni bir EquityPoint yaratmaz
- equity mark her zaman candle.close kullanır
- funding reference_price bir equity mark price DEĞİLDİR
```

Implementasyon: `backtest/results.py` (`build_equity_point`, değişmedi) + `backtest/replay.py`'ın funding sweep'i mark'tan önce çalıştıran loop sırası. Regression: `tests/test_backtest_funding_replay.py`, `tests/test_backtest_funding_e2e_regression.py`.

## 14. Replay Mimari Yönü (MUST)

**Reddedilir:** backtest'ten sonra funding'in post-processing olarak uygulanması. Funding cash'i değiştirdiği için equity path'ini ve final result'ı etkiler — retroaktif bir post-processing bu path'i doğru şekilde yeniden üretemez.

**Tercih edilen gelecek mimari:**

```
- candle/replay boundary'leri
- funding settlement event'leri
```

arasında scoped, chronological bir merge. Tüm funding event'leri, o anki pozisyona karşı kronolojik sırayla işlenir. Şu an tamamen generic, arbitrary-event bir framework'e **ihtiyaç yoktur** — yalnızca iki event sınıfının (candle/fill timeline vs funding timeline) uzlaştırılması gerekir.

**Exact implementasyon bu dokümanda kilitlenmez** — dedicated replay-integration mikro-adımına ertelenir.

## 15. Replay / Store Runner API Compatibility (LOCKED — FUNDING-SPEC MS10/MS11)

Mevcut Faz 4/Faz 5A replay çağrıları **geçerli kalmıştır** — aşağıdaki extension'lar pure additive'tir, hiçbir mevcut çağrı imzasını replace etmez.

**Implemented (FUNDING-SPEC MS10):** `backtest/replay.py`'ın `run_backtest_replay` fonksiyonu artık şu additive, keyword-only, defaulted parametreleri taşır:

```
funding_events: Sequence[HistoricalFundingEvent] = ()
funding_model: FundingModel | None = None
```

`funding_events=()` ile davranış, funding'den önceki haliyle byte/value-identical'dır.

**Implemented (FUNDING-SPEC MS11):** `backtest/store_runner.py`'ın `run_backtest_from_store` fonksiyonu artık şu additive, keyword-only, defaulted parametreleri taşır:

```
funding_required: bool = False
funding_store: HistoricalFundingStore | None = None
funding_model: FundingModel | None = None
```

Funding-enabled instrument'lar için funding'in sessiz yokluğu, bu market-aware boundary'de `funding_required` explicit caller declaration'ı ile **reddedilir** (bkz. Bölüm 16 — tam kontrat orada).

**Store runner yönü:** `store_runner`, ayrı bir `HistoricalFundingStore` ve bir `FundingModel`'e additive erişim kazanmıştır. Mevcut `HistoricalCandleStore` **yalnızca OHLCV** olarak kalır — funding satırları/kolonları candle storage'a **eklenmemiştir.**

## 16. Market-Type Prensibi (MUST)

Funding, perpetual/funding-enabled enstrümanlara uygulanır.

```
Spot:            funding YOK.
Dated futures:   perpetual funding OTOMATİK VARSAYILMAZ.
```

**Yasak kural:**

```
market_type != "spot" => funding
```

bu kural yanlıştır — dated futures perpetual-style periyodik funding kullanmaz.

Mevcut `market_type: str` opak storage partition contract'ı, Faz 5B'de **global olarak yeniden yazılmaz** (gerekli olmadıkça). Dar, funding-aware bir sınıflandırma, ileride yalnızca integration boundary'sinde (örn. `store_runner`) tanıtılabilir.

### 16.1 `funding_required` — Explicit Integration-Boundary Kontratı (LOCKED — FUNDING-SPEC MS11)

`store_runner`'daki dar, funding-aware sınıflandırma, `run_backtest_from_store`'un `funding_required: bool = False` parametresi olarak implement edilmiştir — **explicit bir caller declaration'ıdır**, asla şunlardan inference edilmez:

```
market_type
exchange
funding_store'un supply edilip edilmediği
funding_model'in supply edilip edilmediği
```

Kontrat:

```
funding_required=False  → funding_store VE funding_model her ikisi de ABSENT olmalı
funding_required=True   → funding_store VE funding_model her ikisi de MANDATORY
```

Bu iki durumun dışındaki her kombinasyon (kısmi/çelişkili config) herhangi bir store I/O'dan ÖNCE reddedilir. `market_type` bu kararda **hiçbir rol oynamaz** — Bölüm 16'nın opak partition-key prensibi bu boundary'de de korunur.

## 17. Missing Funding Data (MUST)

Funding-enabled bir backtest için, eksik gerekli funding verisi **sessizce sıfır funding anlamına gelemez.**

**Hidden zero fallback YOK.**

Funding-data quality failure, run'ı **reddetmelidir.** Exact schedule/coverage validasyonu `FUNDING_DATA_SPEC.md`'ye ertelenir.

### 17.1 Zero Event vs Zero Rate — Ayrım

```
gerçek tarihsel funding event, funding_rate = Decimal(0)
```

ile

```
hiçbir funding event/data sağlanmamış
```

**semantik olarak eşdeğer DEĞİLDİR.** İlki, sıfır oranda gerçekleşmiş gerçek bir settlement'tır (genuine historical fact). İkincisi, funding verisinin/modelin hiç mevcut olmadığı anlamına gelir (spot için doğru; funding-enabled bir enstrüman için Bölüm 17'nin reddettiği bir durum). Bu iki durum implementasyonda **asla karıştırılmaz.**

## 18. Range Prensibi (LOCKED — Prensip Seviyesinde)

Kanonik funding storage/query yönü, repository'nin mevcut internal half-open convention'ını izler:

```
[start_time, end_time)
```

`end_time`'daki bir event **hariç tutulur.**

### 18.1 Binance Source-Adapter Research Finding — Inclusive Range

**SOURCE-ADAPTER RESEARCH FACT:**

Binance REST funding history API'si şu anda `startTime`/`endTime`'ı **INCLUSIVE** olarak dokümante eder.

**Sonuç:** adapter/parser, API'nin inclusive response semantics'ini kanonik internal half-open contract'a **normalize etmelidir.** Repository-wide half-open semantics **değiştirilmez** — normalizasyon sorumluluğu adapter/parser katmanına aittir, core contract'a değil.

## 19. Binance Source-Adapter Research Facts (Kayıt Amaçlı — Kodlanmaz)

Aşağıdaki bulgular **SOURCE-ADAPTER RESEARCH FACT** olarak kaydedilir — exchange-neutral bir yasa değildir ve core `FundingModel`/domain'e bake edilmez. Şimdi **kodlanmaz**; yalnızca gelecekteki Binance adapter/`FUNDING_DATA_SPEC.md` çalışmasını bilgilendirir.

Mevcut resmi Binance USDⓈ-M historical funding endpoint'i:

```
GET /fapi/v1/fundingRate
```

Response şu anda içerir:

```
symbol
fundingRate
fundingTime
markPrice
rateType
```

Query facts:

```
startTime inclusive
endTime inclusive
limit max 1000
ascending response
range limit'i aşarsa pagination startTime + limit davranışıyla ilerler
ne startTime ne endTime sağlanmazsa en güncel kayıtlar döner
```

### 19.1 Funding Interval — Global Sabit Varsayım YOK

**Global 8 saatlik bir schedule kilitlenmez.** Mevcut Binance ekosistemi funding-interval ayarlamalarını destekler.

`GET /fapi/v1/fundingInfo`, funding parametresi ayarlanmış semboller için `fundingIntervalHours` döner. Ancak bu endpoint'in, keyfi geçmiş aralıklar boyunca **tam bir historical schedule kaynağı** olduğu henüz kanıtlanmamıştır.

**Sonuç:** exact historical schedule/coverage validasyonu, `FUNDING_DATA_SPEC.md` / source-research mikro-adımı için **AÇIK** kalır.

## 20. FUNDING_DATA_SPEC'e Ertelenen Sorular (Bu Dokümanda Kilitlenmez)

Aşağıdakiler açıkça `FUNDING_DATA_SPEC.md`'ye ertelenir; bu doküman bunları **prematüre kilitlemez:**

- Exact `HistoricalFundingEvent`/`FundingEvent` alanları
- `rateType`'ın treatment/storage'ı
- Canonical identity/key (Bölüm 7.1)
- Aynı `event_time`'da birden fazla rate type olasılığı
- SQLite schema
- Duplicate/idempotency/conflict kuralları
- Parser
- Pagination cursor
- Historical schedule coverage
- Funding quality report
- Ingestion finalization
- Source revision semantics (geçmiş bir funding rate'in revize edilip edilemeyeceği)

## 21. Explicit Out-of-Scope / Fake Sophistication (MUST NOT)

Aşağıdakilerin hiçbiri Faz 5B'de yapılamaz:

```
- global sabit 8h schedule varsaymak
- source markPrice sağlıyorken candle close/open'ı settlement mark price olarak kullanmak
- funding'i candle'lar arasında ortalamak
- funding'i candle-başına ücretlendirmek
- funding'i yalnızca fill olduğunda ücretlendirmek
- eksik funding = sıfır varsaymak
- negatif funding rate'leri reddetmek
- predicted next funding'i settled history gibi kullanmak
- funding'i CompositeCostModel içine koymak
- event ordering'i gizlemek/belirsiz bırakmak
- hidden funding default'ları tanıtmak
```

## 22. Backward Compatibility (LOCKED)

```
CostModel:              unchanged
CompositeCostModel:     unchanged
AccountState:           prefer unchanged (bkz. Bölüm 8.3 — position_opened_at gerekmez)
BacktestResult:         unchanged
HistoricalCandleStore:  unchanged (OHLCV-only kalır)
```

**Implemented additive extension'lar** (Bölüm 15'te tam kontrat):

```
run_backtest_replay        (FUNDING-SPEC MS10)
run_backtest_from_store    (FUNDING-SPEC MS11)
```

## 23. Future Acceptance Categories (Numaralandırma YOK — Kategori Seviyesinde)

`FUNDING_DATA_SPEC.md` ve replay-timing kontratı (Bölüm 12) artık kilitlenmiştir (FUNDING-SPEC MS9-MS12). Aşağıdaki kategoriler bilinçli olarak **category-level** kalır — final numaralandırma bu dokümanın acceptance style'ı için gerekli değildir; exact numbered acceptance criteria `FUNDING_DATA_SPEC.md` Bölüm 46'da (35 kriter) yaşar:

```
- funding sign exact
- positive/negative/zero rates
- genuine finite Decimal (rate ve reference_price)
- exact settled mark/reference price
- settled-only historical data
- no future/predicted leakage
- separate funding storage
- data conflict/idempotency
- deterministic range queries
- missing-data quality rejection
- zero/one/multiple events
- flat event zero cost
- long/short payment direction
- cash-only accounting
- realized PnL unchanged
- fill/trade counts unchanged
- total_cost integration
- total_pnl reconciliation
- explicit tie ordering
- deterministic replay
- real SQLite E2E
- spot unaffected
- perpetual missing funding rejected
- offline
- regression-free
- Ruff clean
```

## 24. Next Faz 5B Microsteps (Bağlayıcı Sıra — Bu Doküman Sonrası)

```
Microstep 3:
  FUNDING_DATA_SPEC.md — authoritative data/storage/source contract
  (production data class'ları yazılmadan ÖNCE)

Microstep 4:
  Canonical FundingEvent / HistoricalFundingEvent model

Microstep 5:
  Funding store abstraction + SQLite schema

Microstep 6:
  Funding parser/client/pagination/ingestion

Microstep 7:
  Funding data-quality contract/gate

Microstep 8:
  Funding calculator (FundingModel) + accounting cash-flow primitive

Microstep 9:
  Replay timing/event-order integration pre-flight
  (Bölüm 12'deki tie-ordering kararının dedicated kilitlenmesi)

Microstep 10:
  Replay integration

Microstep 11:
  Store-runner perpetual integration

Microstep 12:
  Funding E2E golden + determinism

Microstep 13:
  Faz 5B acceptance audit / Faz 5 complete
```

Microstep 3, production data class'ları yazılmadan **önce** gelir — reference-price/schema kararlarının çoğu (Bölüm 7.1, 19.1) authoritative source-data araştırmasına bağımlıdır.
